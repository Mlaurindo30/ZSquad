"""
O que é: resolvedor do contexto entre um projeto consumidor e o runtime compartilhado.
Responsabilidade: validar o marcador mínimo .agents_squad e produzir caminhos centrais namespaced;
resolver a política SDD por projeto (T4 do plano de integração Spec Kit) e o registro
durável de ativação SDD (CORR-2, auditoria pós-entrega P1#4).
Pra que serve: permitir que vários providers e projetos usem um único runtime sem copiá-lo.
Comportamento em falha: rejeita marcador ausente, inválido ou runtime incompatível;
política SDD ilegível/inválida em projeto ativado falha fechada (nunca desativa SDD);
política AUSENTE em projeto com registro de ativação durável também falha fechada —
apagar o arquivo de política não rebaixa silenciosamente para legado.
Conexões: bootstrap_project_squad.py, agent_squad.py, prompts dos providers e
integrations/spec-kit/adapter/policy.py (authorize/is_sdd_required).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Localização proposta da política SDD por projeto (plano seção 6/T4):
# junto do marcador de projeto, versionada no repositório consumidor.
SDD_POLICY_REL = Path(".agents_squad") / "config" / "sdd-policy.yaml"

# CORR-2 (P1#4): registro durável de ativação SDD. Gravado na ativação formal
# (`sdd activate`) e na adoção (`sdd init` em projeto com sdd.required: true).
# Enquanto existir, a ausência do arquivo de política é FALHA FECHADA
# (SDD_POLICY_INVALID) — nunca um rebaixamento silencioso para legado.
SDD_ACTIVATION_REL = Path(".agents_squad") / "config" / "sdd-activation.yaml"
SDD_PILOT_SCOPE_REL = Path(".agents_squad") / "config" / "sdd-pilot-scope.yaml"
SDD_PILOT_ACTIVATION_REL = Path(".agents_squad") / "config" / "sdd-pilot-activation.yaml"
SDD_PILOT_JOURNAL_REL = Path(".agents_squad") / "config" / "sdd-pilot-journal"


class ProjectContextError(ValueError):
    """Indica que o vínculo projeto-runtime não pode ser validado."""


@dataclass(frozen=True)
class SDDRequirementResolution:
    """Resolução efetiva por work item, sem alterar a política global."""

    source: str
    state: str
    policy: dict | None
    errors: tuple[str, ...] = field(default=())
    policy_path: Path | None = None
    scope_path: Path | None = None
    activation_path: Path | None = None


@dataclass(frozen=True)
class ProjectContext:
    """Contexto validado de um projeto consumidor do runtime central."""

    runtime_root: Path
    project_root: Path
    project_id: str

    @property
    def work_dir(self) -> Path:
        return self.runtime_root / "work" / self.project_id

    @property
    def db_path(self) -> Path:
        return self.runtime_root / "banco" / "squad.db"


def validate_project_id(project_id: str) -> str:
    """Valida o identificador usado como namespace central."""
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ProjectContextError(f"project_id inválido: {project_id!r}")
    return project_id


def find_project_root(start: Path) -> Path | None:
    """Procura o marcador de projeto no diretório inicial e ancestrais."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".agents_squad" / "config" / "project.yaml").is_file():
            return candidate
    return None


def load_project_context(project_root: Path) -> ProjectContext:
    """Carrega e valida o marcador mínimo criado pelo bootstrap."""
    project_root = project_root.resolve()
    marker = project_root / ".agents_squad" / "config" / "project.yaml"
    try:
        payload: Any = yaml.safe_load(marker.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProjectContextError(f"marcador de projeto inválido: {marker}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectContextError(f"marcador de projeto inválido: {marker}")

    project_id = validate_project_id(str(payload.get("project_id") or payload.get("project_name") or ""))
    runtime_value = payload.get("runtime")
    recorded_root = payload.get("project_root")
    if not isinstance(runtime_value, str) or not runtime_value:
        raise ProjectContextError("runtime ausente no marcador de projeto")
    if recorded_root and Path(str(recorded_root)).resolve() != project_root:
        raise ProjectContextError("project_root do marcador não corresponde ao projeto atual")

    runtime_root = Path(runtime_value).resolve()
    required = (runtime_root / "scripts", runtime_root / "agents", runtime_root / "contracts")
    if not runtime_root.is_dir() or not all(path.is_dir() for path in required):
        raise ProjectContextError(f"runtime compartilhado inválido: {runtime_root}")
    return ProjectContext(runtime_root, project_root, project_id)


def resolve_project_context(start: Path, explicit_project_root: Path | None = None) -> ProjectContext:
    """Resolve contexto a partir de uma raiz explícita ou do diretório atual."""
    root = explicit_project_root.resolve() if explicit_project_root else find_project_root(start)
    if root is None:
        raise ProjectContextError("marcador .agents_squad/config/project.yaml não encontrado")
    return load_project_context(root)


# ---------------------------------------------------------------------------
# Política SDD por projeto (T4 — plano seção 6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SDDPolicyResolution:
    """Resultado da resolução da política SDD de um projeto.

    Atributos:
        state: ``"active"`` quando o projeto ativou SDD (arquivo de política
            presente em ``.agents_squad/config/sdd-policy.yaml`` OU registro
            durável de ativação presente em
            ``.agents_squad/config/sdd-activation.yaml`` — CORR-2/P1#4);
            ``"legacy"`` quando não há política E não há registro de
            ativação (projeto ainda não migrado — compatível, porém SEM
            garantia/cobertura SDD).
        policy: conteúdo carregado da política, ou ``None`` quando ausente
            ou inválida.
        policy_path: caminho esperado/da política.
        errors: problemas de leitura/estrutura; não vazio em estado ativo
            inválido (o chamador deve falhar fechado).
    """

    state: str
    policy: dict | None
    policy_path: Path
    errors: tuple[str, ...] = field(default=())


def resolve_sdd_policy(project_root: Path) -> SDDPolicyResolution:
    """Resolve a política SDD do projeto em ``<project_root>/.agents_squad/config/sdd-policy.yaml``.

    Semântica (plano seção 6; ativação durável CORR-2/P1#4):

    - Arquivo presente => projeto ativado: política ilegível (OSError/YAML
      inválido) ou sem objeto no nível raiz registra ``errors`` e devolve
      ``policy=None`` — o consumidor deve falhar fechado
      (``SDD_POLICY_INVALID``), nunca desativar SDD silenciosamente.
      Desativação legítima é ``sdd.required: false`` numa política revisada,
      não exclusão do arquivo.
    - Arquivo AUSENTE com registro de ativação durável
      (``.agents_squad/config/sdd-activation.yaml``, CORR-2/P1#4) => o
      projeto permanece ATIVO em falha fechada: ``state="active"``,
      ``policy=None`` e ``errors`` com ``SDD_POLICY_INVALID`` ("projeto
      ativado com política ausente — restaurar ou desativar formalmente").
      Apagar a política NÃO rebaixa silenciosamente para legado.
    - Arquivo ausente SEM registro de ativação => projeto legado sem
      ativação: estado ``"legacy"``, sem erros, compatível — mas não
      constitui cobertura SDD.

    A leitura não usa lock: a política é configuração versionada; a
    validação executável (``validate_policy``) roda no consumidor sobre o
    dict retornado.
    """
    policy_path = project_root / SDD_POLICY_REL
    if not policy_path.is_file():
        activation_path = project_root / SDD_ACTIVATION_REL
        if activation_path.is_file():
            record = read_sdd_activation(project_root)
            detail = (
                "SDD_POLICY_INVALID: projeto ativado com política ausente — o registro de "
                f"ativação durável {activation_path} existe; restaurar {policy_path} ou "
                "desativar formalmente (sdd deactivate)"
            )
            if isinstance(record, dict) and record.get("activated_at"):
                detail += f" (ativado em {record['activated_at']})"
            return SDDPolicyResolution("active", None, policy_path, (detail,))
        return SDDPolicyResolution("legacy", None, policy_path, ())
    try:
        payload: Any = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return SDDPolicyResolution(
            "active", None, policy_path, (f"política SDD ilegível em {policy_path}: {exc}",)
        )
    if not isinstance(payload, dict):
        return SDDPolicyResolution(
            "active",
            None,
            policy_path,
            (f"política SDD inválida (raiz deve ser um objeto): {policy_path}",),
        )
    return SDDPolicyResolution("active", payload, policy_path, ())


# ---------------------------------------------------------------------------
# Registro durável de ativação SDD (CORR-2, P1#4)
# ---------------------------------------------------------------------------


def read_sdd_activation(project_root: Path) -> dict | None:
    """Lê o registro durável de ativação SDD; ``None`` quando ausente/ilegível.

    Registro ilegível NÃO é tratado como ausência: em combinação com política
    ausente o ``resolve_sdd_policy`` continua falhando fechado (o arquivo
    existir já basta para manter o estado ativo).
    """
    path = project_root / SDD_ACTIVATION_REL
    if not path.is_file():
        return None
    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return payload if isinstance(payload, dict) else None


def build_sdd_activation(project_root: Path, policy_path: Path) -> dict:
    """Constrói o registro de ativação a partir da política corrente.

    Captura o instante UTC da ativação, o sha256 do CONTEÚDO do arquivo de
    política e a ``policy_version`` declarada. Falha (``ProjectContextError``)
    quando a política é ilegível ou não declara ``policy_version`` inteiro
    >= 1 — ativação sobre política inválida é recusada.
    """
    try:
        policy_bytes = policy_path.read_bytes()
    except OSError as exc:
        raise ProjectContextError(f"política SDD ilegível em {policy_path}: {exc}") from exc
    try:
        payload: Any = yaml.safe_load(policy_bytes.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ProjectContextError(f"política SDD inválida: {policy_path}: {exc}") from exc
    policy_version = payload.get("policy_version") if isinstance(payload, dict) else None
    if isinstance(policy_version, bool) or not isinstance(policy_version, int) or policy_version < 1:
        raise ProjectContextError(
            f"política SDD sem policy_version inteiro >= 1: {policy_path}"
        )
    activated_at = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    return {
        "schema_version": 1,
        "activated_at": activated_at,
        "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "policy_version": policy_version,
    }


def write_sdd_activation(project_root: Path, record: dict) -> Path:
    """Grava o registro durável de ativação (validação de schema é do chamador)."""
    path = project_root / SDD_ACTIVATION_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    return path


def archive_sdd_activation(project_root: Path) -> Path | None:
    """Arquiva o registro de ativação (desativação formal); ``None`` se ausente.

    O registro não é apagado: é renomeado para
    ``sdd-activation.archived-<timestamp>.yaml`` na mesma pasta, preservando
    a trilha auditável da desativação.
    """
    path = project_root / SDD_ACTIVATION_REL
    if not path.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = path.with_name(f"sdd-activation.archived-{stamp}.yaml")
    path.replace(archive)
    return archive


def sdd_required(project_root: Path) -> bool:
    """Indica se o projeto exige SDD, com falha fechada.

    ``True`` somente quando o projeto está ativado (política presente) e a
    política carregada declara ``sdd.required: true``. Estado ativo com
    política ilegível/inválida também retorna ``True`` (falha fechada: o
    consumidor vai tentar autorizar, encontrar ``SDD_POLICY_INVALID`` e
    bloquear) — uma política quebrada nunca desativa o requisito.
    Projeto legado sem ativação retorna ``False`` (compatibilidade, sem
    garantia SDD).
    """
    resolution = resolve_sdd_policy(project_root)
    if resolution.state != "active":
        return False
    if resolution.errors or resolution.policy is None:
        return True
    sdd = resolution.policy.get("sdd")
    return isinstance(sdd, dict) and sdd.get("required") is True


# ---------------------------------------------------------------------------
# Piloto SDD por work item (T0)
# ---------------------------------------------------------------------------


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _valid_work_items(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not value:
        return None
    if any(not isinstance(item, str) or not PROJECT_ID_RE.fullmatch(item) for item in value):
        return None
    if len(set(value)) != len(value):
        return None
    return tuple(value)


def _read_yaml_object(path: Path) -> dict[str, Any] | None:
    try:
        value: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return value if isinstance(value, dict) else None


def _pilot_scope(path: Path) -> tuple[dict[str, Any] | None, bytes | None]:
    try:
        raw = path.read_bytes()
        value: Any = yaml.safe_load(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None, None
    if not isinstance(value, dict) or set(value) != {"schema_version", "scope_mode", "policy", "work_items"}:
        return None, raw
    policy = value.get("policy")
    if (
        value.get("schema_version") != 1
        or value.get("scope_mode") != "allowlist"
        or not isinstance(policy, dict)
        or set(policy) - {"policy_version", "sdd", "legacy_mode"}
        or not isinstance(policy.get("policy_version"), int)
        or isinstance(policy.get("policy_version"), bool)
        or policy.get("policy_version", 0) < 1
        or not isinstance(policy.get("sdd"), dict)
        or set(policy["sdd"]) != {"required"}
        or policy["sdd"].get("required") is not True
        or _valid_work_items(value.get("work_items")) is None
    ):
        return None, raw
    return value, raw


def _read_pilot_journal(journal: Path) -> tuple[tuple[str, ...], dict[str, Any] | None, bool]:
    """Return the safely recoverable snapshot, terminal event and validity.

    A snapshot is extracted only from canonically hashed activation events.  It
    therefore never derives enforcement targets from the mutable scope or
    projection files.  An invalid HEAD still permits fail-closed treatment of
    the last verified snapshot, while new IDs remain legacy.
    """
    events_dir = journal / "events"
    head_path = journal / "HEAD.json"
    if not events_dir.is_dir() or not head_path.is_file():
        return (), None, False
    try:
        names = sorted(events_dir.glob("*.json"))
        head_raw = head_path.read_bytes()
        head: Any = json.loads(head_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return (), None, False
    genesis = hashlib.sha256(_canonical_json_bytes({"schema_version": 1, "kind": "sdd-pilot-genesis"})).hexdigest()
    previous = genesis
    verified: dict[str, Any] | None = None
    snapshot: tuple[str, ...] = ()
    valid = True
    for index, path in enumerate(names, start=1):
        try:
            raw = path.read_bytes()
            event: Any = json.loads(raw.decode("utf-8"))
            if not isinstance(event, dict) or raw != _canonical_json_bytes(event):
                raise ValueError("noncanonical event")
            supplied_hash = event.pop("event_sha256", None)
            event_hash = hashlib.sha256(_canonical_json_bytes(event)).hexdigest()
            event_id = event.get("event_id")
            if (
                supplied_hash != event_hash
                or event.get("schema_version") != 1
                or event.get("event") != "activated"
                or event.get("sequence") != index
                or event.get("prev_event_sha256") != previous
                or event.get("activation_id") != event_id
                or path.name != f"{index:08d}-{event_id}.json"
                or _valid_work_items(event.get("work_items")) is None
                or not isinstance(event.get("scope_sha256"), str)
                or not isinstance(event.get("scope_policy_version"), int)
            ):
                raise ValueError("invalid event")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            valid = False
            break
        event["event_sha256"] = supplied_hash
        previous = event_hash
        verified = event
        snapshot = _valid_work_items(event["work_items"]) or ()
    if not snapshot or verified is None:
        return (), None, False
    if not isinstance(head, dict) or head_raw != _canonical_json_bytes(head):
        valid = False
    elif (
        head.get("schema_version") != 1
        or head.get("genesis_sha256") != genesis
        or head.get("sequence") != len(names)
        or head.get("event_id") != verified.get("event_id")
        or head.get("event_sha256") != previous
    ):
        valid = False
    return snapshot, verified, valid


def resolve_sdd_requirement(project_root: Path, work_id: str) -> SDDRequirementResolution:
    """Resolve global policy first, then the immutable T0 pilot snapshot."""
    if not isinstance(work_id, str) or not PROJECT_ID_RE.fullmatch(work_id):
        return SDDRequirementResolution("legacy", "legacy", None)
    global_resolution = resolve_sdd_policy(project_root)
    if global_resolution.state == "active":
        if global_resolution.errors or global_resolution.policy is None:
            return SDDRequirementResolution("global", "global-invalid", None, global_resolution.errors, global_resolution.policy_path)
        required = global_resolution.policy.get("sdd", {}).get("required") is True
        return SDDRequirementResolution("global", "global-required" if required else "global-optional", global_resolution.policy, (), global_resolution.policy_path)

    scope_path = project_root / SDD_PILOT_SCOPE_REL
    activation_path = project_root / SDD_PILOT_ACTIVATION_REL
    journal_path = project_root / SDD_PILOT_JOURNAL_REL
    snapshot, event, journal_valid = _read_pilot_journal(journal_path)
    captured = work_id in snapshot
    if event is None:
        return SDDRequirementResolution("legacy", "legacy", None, (), None, scope_path, activation_path)
    if not journal_valid:
        return _pilot_invalid_or_legacy(work_id, captured, "SDD_PILOT_JOURNAL_INVALID", scope_path, activation_path)

    scope, scope_bytes = _pilot_scope(scope_path)
    activation = _read_yaml_object(activation_path)
    event_items = _valid_work_items(event.get("work_items"))
    expected_hash = event.get("scope_sha256")
    projection_matches = (
        isinstance(activation, dict)
        and activation.get("schema_version") == 1
        and activation.get("activation_id") == event.get("activation_id")
        and activation.get("scope_sha256") == expected_hash
        and activation.get("scope_policy_version") == event.get("scope_policy_version")
        and _valid_work_items(activation.get("work_items")) == event_items
    )
    scope_matches = (
        scope is not None
        and scope_bytes is not None
        and hashlib.sha256(scope_bytes).hexdigest() == expected_hash
        and _valid_work_items(scope.get("work_items")) == event_items
        and scope.get("policy", {}).get("policy_version") == event.get("scope_policy_version")
    )
    if not projection_matches or not scope_matches:
        return _pilot_invalid_or_legacy(work_id, captured, "SDD_PILOT_SCOPE_INVALID", scope_path, activation_path)
    if captured:
        return SDDRequirementResolution("pilot", "pilot-required", scope["policy"], (), None, scope_path, activation_path)
    return SDDRequirementResolution("legacy", "legacy", None, (), None, scope_path, activation_path)


def _pilot_invalid_or_legacy(work_id: str, captured: bool, code: str, scope_path: Path, activation_path: Path) -> SDDRequirementResolution:
    if captured:
        return SDDRequirementResolution("pilot", "pilot-invalid", None, (code,), None, scope_path, activation_path)
    return SDDRequirementResolution("legacy", "legacy", None, (), None, scope_path, activation_path)
