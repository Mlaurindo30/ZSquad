"""Leitura normalizada e segura do pacote SDD (T2) — backend-engineer.

O pacote SDD vive em ``work/<project_id>/<work_id>/sdd/`` com
``package.json`` mais os documentos de entrada (spec, clarifications,
plan, tasks) e a constituição do projeto.

Este módulo é puro: sem rede, sem subprocess, sem mutação de ``sys.path``.
O diretório ``integrations/spec-kit`` contém hífen e não é importável pelo
nome; o pacote ``adapter`` deve ser carregado via ``importlib`` com
``spec_from_file_location(..., submodule_search_locations=[...])``.

Códigos de erro estáveis (contrato da seção 6 do plano):

- ``SDD_MISSING_INPUT``   — input obrigatório ausente no estágio ou
  documento não encontrado/ilegível em disco.
- ``SDD_OPEN_QUESTION``   — questão blocking aberta (ou ``resolved`` sem
  resposta/fonte, ou ``accepted_assumption`` sem justificativa/owner/
  condição de revisão, que não disfarça bloqueante aberta).
- ``SDD_STALE_GATE``      — reservado a T4 (hashes aprovados vs. atuais).
- ``SDD_COVERAGE_GAP``    — requisito de código sem tarefa e teste
  correspondentes.
- ``SDD_DEPENDENCY_CYCLE`` — ``depends_on`` inexistente ou em ciclo.
- ``SDD_POLICY_INVALID``  — reservado a documentos de política (T4);
  este módulo o aplica apenas em :func:`validation.validate_policy`.
- ``SDD_MALFORMED``       — código dedicado a estrutura malformada do
  pacote: JSON Schema violado, IDs duplicados, ``points`` fora de
  {1, 2, 3, 5, 8}, path traversal, estágio desconhecido, tipo errado.

``load_package`` levanta :class:`SDPPackageError` (subclasse de
``ValueError``) apenas para falhas de *carregamento* (package.json
ausente/ilegível, YAML inseguro). Problemas estruturais detectáveis sem
quebrar a leitura (path traversal, arquivo de input ausente) são
registrados em ``documents`` e reportados por ``validate_package``.

YAML é lido exclusivamente com ``yaml.safe_load`` (SafeLoader): tags
``!!python/object`` e afins nunca são instanciadas — o carregamento
falha fechado com :class:`SDPPackageError`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

__all__ = ["SDPPackageError", "KNOWN_STAGES", "load_package", "resolve_within", "sha256_bytes", "sha256_file"]


class SDPPackageError(ValueError):
    """Falha de carregamento do pacote SDD (arquivo ausente, JSON inválido, YAML inseguro/inválido)."""


KNOWN_STAGES = ("planning", "tasking", "implementation", "readiness")

_INPUT_KEYS = ("spec", "clarifications", "plan", "tasks")
_YAML_INPUT_KEYS = ("clarifications", "tasks")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_within(work_dir: Path, rel: str) -> Path | None:
    """Resolve ``rel`` contra ``work_dir``; devolve ``None`` se escapar da raiz.

    Rejeita caminhos absolutos fora de ``work_dir`` e qualquer traversal
    (``../``). Aceita espaços e acentos (paths são Unicode nativos no
    Python 3 no Windows).
    """
    if not isinstance(rel, str) or not rel:
        return None
    try:
        candidate = Path(rel)
        if candidate.drive and not candidate.root:
            # Caminho com unidade relativa (ex.: 'C:sdd/spec.md') é ambíguo
            # (resolve contra o cwd da unidade): rejeitado explicitamente.
            return None
        if not candidate.is_absolute():
            candidate = work_dir / candidate
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return None
    try:
        resolved.relative_to(work_dir)
    except ValueError:
        return None
    return resolved


def load_package(work_dir: Path) -> dict:
    """Carrega e normaliza o pacote SDD de ``work_dir`` (layout ``sdd/``).

    Retorna o conteúdo de ``package.json`` acrescido de:

    - ``work_dir``: caminho absoluto resolvido do work item;
    - ``documents``: mapa ``{constitution, spec, clarifications, plan,
      tasks}`` onde cada entrada tem ``exists``, ``sha256`` (hash real do
      arquivo), ``path``, ``parsed`` (YAML) ou ``content`` (texto) e
      ``safe_path`` (``None`` quando o caminho escapa da raiz).

    Levanta :class:`SDPPackageError` se ``package.json`` estiver ausente,
    corrompido, ou se algum YAML de entrada for inválido/inseguro.
    """
    work_dir = Path(work_dir).resolve()
    package_file = work_dir / "sdd" / "package.json"
    if not package_file.is_file():
        raise SDPPackageError(f"package.json não encontrado em {work_dir / 'sdd'}")
    try:
        raw = package_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise SDPPackageError(f"package.json ilegível em {package_file}: {exc}") from exc
    try:
        package = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SDPPackageError(f"package.json inválido (JSON): {exc}") from exc
    if not isinstance(package, dict):
        raise SDPPackageError("package.json deve conter um objeto JSON")

    documents: dict[str, dict] = {}
    documents["constitution"] = _read_document(
        work_dir, package.get("constitution_path"), parse=None, label="constitution"
    )
    inputs = package.get("inputs")
    for key in _INPUT_KEYS:
        entry = inputs.get(key) if isinstance(inputs, dict) else None
        rel = entry.get("path") if isinstance(entry, dict) else None
        parse = "yaml" if key in _YAML_INPUT_KEYS else "text"
        documents[key] = _read_document(work_dir, rel, parse=parse, label=key)

    package["work_dir"] = str(work_dir)
    package["documents"] = documents
    return package


def _read_document(work_dir: Path, rel, parse: str | None, label: str) -> dict:
    """Lê um documento do pacote sem nunca seguir caminho fora de ``work_dir``."""
    result: dict = {
        "exists": False,
        "sha256": None,
        "path": rel if isinstance(rel, str) else None,
        "safe_path": None,
        "parsed": None,
        "content": None,
    }
    if rel is None:
        return result
    safe = resolve_within(work_dir, rel)
    if safe is None:
        # Path traversal: não lê; validate_package reporta SDD_MALFORMED.
        return result
    result["safe_path"] = str(safe)
    if not safe.is_file():
        return result
    try:
        data = safe.read_bytes()
    except OSError as exc:
        raise SDPPackageError(f"documento '{label}' ilegível em {safe}: {exc}") from exc
    result["exists"] = True
    result["sha256"] = sha256_bytes(data)
    if parse == "yaml":
        result["parsed"] = _safe_yaml_load(data, label)
    else:
        result["content"] = data.decode("utf-8")
    return result


def _safe_yaml_load(data: bytes, label: str):
    """Carrega YAML com SafeLoader; falha fechado ante tags não seguras."""
    try:
        return yaml.safe_load(data.decode("utf-8"))
    except yaml.YAMLError as exc:
        raise SDPPackageError(f"YAML inseguro ou inválido em '{label}': {exc}") from exc
