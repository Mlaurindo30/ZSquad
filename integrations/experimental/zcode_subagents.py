"""
O que é: adaptador determinístico das personas canônicas para Subagents Markdown do ZCode.
Responsabilidade: validar cobertura exata, renderizar, migrar e gerenciar somente perfis com proveniência Agents Squad.
Pra que serve: instalar as 36 personas sem duplicar identidade canônica na configuração específica do host.
Comportamento em falha: bloqueia caminhos, links ou conflitos inseguros e restaura bytes originais após falha de escrita.
Conexões: config/agent-registry.yaml, config/zcode-agents.yaml e agents/*/{PROMPT.md,skills/manifest.yaml}.
Dependências & Imports: pathlib, hashlib, json, os, shutil, tempfile, dataclasses e PyYAML.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

MANAGED_BY = "agents-squad-zcode-adapter"
PROFILE_SUFFIX = ".md"
CONFIG_SCHEMA_VERSION = 2
RENDER_CONTRACT_VERSION = 2
MAX_FRONTMATTER_BYTES = 64 * 1024
BACKUP_MANIFEST = "manifest.json"
DEFAULT_CONFIG_NAME = "zcode-agents.yaml"
LEGACY_CONFIG_NAME = "zcode-pilot-agents.yaml"
VALID_COLORS = {"red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink"}


class ZCodeProfileError(RuntimeError):
    """Indica configuração inválida, caminho inseguro ou conflito não gerenciado."""


@dataclass(frozen=True)
class ZCodeProfile:
    """Representa uma persona canônica validada e suas opções específicas do ZCode."""

    agent_id: str
    title: str
    purpose: str
    prompt_path: str
    manifest_path: str
    native_skill_path: str
    openai_path: str | None
    color: str
    tools: tuple[str, ...]
    max_turns: int
    activation_fingerprint: str = ""
    activation_probe: str = ""


PilotProfile = ZCodeProfile


def _read_yaml(path: Path) -> dict[str, Any]:
    """Lê YAML como mapping e converte falhas de E/S ou sintaxe em erro de domínio."""
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ZCodeProfileError(f"invalid YAML at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ZCodeProfileError(f"YAML must contain a mapping: {path}")
    return value


def _safe_filename(agent_id: str) -> str:
    """Converte somente identificadores canônicos seguros em nomes Markdown."""
    if not agent_id or agent_id in {".", ".."} or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in agent_id):
        raise ZCodeProfileError(f"unsafe profile id: {agent_id}")
    return f"{agent_id}{PROFILE_SUFFIX}"


def _relative_runtime_path(root: Path, raw_path: str) -> str:
    """Valida caminho relativo existente, contido no runtime e sem componentes enlazados."""
    if not isinstance(raw_path, str) or not raw_path:
        raise ZCodeProfileError(f"unsafe runtime path: {raw_path}")
    normalized = raw_path.replace("\\", "/")
    if Path(raw_path).is_absolute() or (len(normalized) >= 2 and normalized[1] == ":"):
        raise ZCodeProfileError(f"unsafe runtime path: {raw_path}")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or pure.anchor:
        raise ZCodeProfileError(f"unsafe runtime path: {raw_path}")
    if any(part == ".." for part in pure.parts):
        raise ZCodeProfileError(f"path escapes runtime: {raw_path}")
    if any(part in {"", "."} for part in pure.parts):
        raise ZCodeProfileError(f"unsafe runtime path: {raw_path}")
    root = root.resolve()
    candidate = root.joinpath(*pure.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ZCodeProfileError(f"missing canonical path: {raw_path}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ZCodeProfileError(f"path escapes runtime: {raw_path}") from exc
    current = root
    for part in pure.parts:
        current = current / part
        if _is_linked_path(current):
            raise ZCodeProfileError(f"canonical path contains link: {raw_path}")
    return pure.as_posix()


def _registry_agents(root: Path) -> list[dict[str, Any]]:
    """Carrega a lista canônica preservando a ordem declarada no registry."""
    registry = _read_yaml(root / "config/agent-registry.yaml")
    agents = registry.get("agents")
    if not isinstance(agents, list) or len(agents) != 41 or not all(isinstance(agent, dict) for agent in agents):
        raise ZCodeProfileError("canonical registry must contain exactly 41 agents")
    required = {"id", "path", "title", "purpose", "manifest"}
    ids: list[str] = []
    for agent in agents:
        if not required <= set(agent) or not all(isinstance(agent[key], str) and agent[key] for key in required):
            raise ZCodeProfileError("invalid canonical persona record")
        _safe_filename(agent["id"])
        ids.append(agent["id"])
    if len(ids) != len(set(ids)):
        raise ZCodeProfileError("duplicate canonical persona id")
    return agents


def _host_mappings(config: dict[str, Any], canonical_ids: list[str]) -> tuple[dict[str, tuple[str, ...]], dict[str, dict[str, Any]]]:
    """Valida schema host-only e cobertura bijetiva sobre o registry."""
    if config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ZCodeProfileError("unsupported ZCode config schema")
    if config.get("managed_by") != MANAGED_BY:
        raise ZCodeProfileError("invalid ZCode configuration")
    allowed_top = {"schema_version", "version", "managed_by", "toolsets", "profiles"}
    if set(config) - allowed_top:
        raise ZCodeProfileError("invalid ZCode configuration fields")
    toolsets_value = config.get("toolsets")
    mappings_value = config.get("profiles")
    if not isinstance(toolsets_value, dict) or not isinstance(mappings_value, list):
        raise ZCodeProfileError("invalid ZCode configuration")
    toolsets: dict[str, tuple[str, ...]] = {}
    for name, value in toolsets_value.items():
        if not isinstance(name, str) or not name or not isinstance(value, list) or not value or not all(isinstance(tool, str) and tool for tool in value):
            raise ZCodeProfileError(f"invalid toolset: {name}")
        toolsets[name] = tuple(value)
    mappings: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for item in mappings_value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ZCodeProfileError("each profile requires an id")
        agent_id = item["id"]
        if agent_id in seen:
            raise ZCodeProfileError(f"duplicate profile id: {agent_id}")
        seen.add(agent_id)
        if set(item) - {"id", "toolset", "tools", "color", "max_turns", "maxTurns"}:
            raise ZCodeProfileError(f"canonical identity fields forbidden in host mapping: {agent_id}")
        mappings[agent_id] = item
    canonical_set = set(canonical_ids)
    unknown = sorted(seen - canonical_set)
    missing = sorted(canonical_set - seen)
    if unknown:
        raise ZCodeProfileError(f"unknown profile mappings: {', '.join(unknown)}")
    if missing:
        raise ZCodeProfileError(f"missing profile mappings: {', '.join(missing)}")
    configured_ids = [item["id"] for item in mappings_value]
    if configured_ids != canonical_ids:
        raise ZCodeProfileError("profile mappings must preserve canonical registry order")
    return toolsets, mappings


def _profile_source_paths(profile: ZCodeProfile) -> tuple[str, ...]:
    paths = [profile.prompt_path, profile.manifest_path, profile.native_skill_path]
    if profile.openai_path is not None:
        paths.append(profile.openai_path)
    return tuple(paths)


def activation_fingerprint(profile: ZCodeProfile, root: Path) -> str:
    """Calcula impressão da identidade, fontes, opções host e contrato de renderização."""
    sources = []
    for path in _profile_source_paths(profile):
        data = (root / path).read_bytes()
        sources.append({"path": path, "sha256": hashlib.sha256(data).hexdigest()})
    payload = {
        "contract": RENDER_CONTRACT_VERSION,
        "identity": {"id": profile.agent_id, "title": profile.title, "purpose": profile.purpose},
        "sources": sources,
        "host": {"color": profile.color, "tools": list(profile.tools), "max_turns": profile.max_turns},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def activation_probe(profile: ZCodeProfile) -> str:
    """Retorna resposta exata e observável esperada para o probe do perfil renderizado."""
    fingerprint = profile.activation_fingerprint
    if not fingerprint:
        raise ZCodeProfileError(f"profile has no activation fingerprint: {profile.agent_id}")
    return f"ZCODE_ACTIVATION_OK {profile.agent_id} {fingerprint}"


def load_profiles(root: Path, config_path: Path | None = None) -> tuple[ZCodeProfile, ...]:
    """Compõe exatamente as 36 identidades do registry com opções exclusivas do host."""
    root = root.resolve()
    config_path = config_path or root / "config" / DEFAULT_CONFIG_NAME
    canonical_agents = _registry_agents(root)
    config = _read_yaml(config_path)
    toolsets, mappings = _host_mappings(config, [agent["id"] for agent in canonical_agents])
    profiles: list[ZCodeProfile] = []
    for canonical in canonical_agents:
        agent_id = canonical["id"]
        item = mappings[agent_id]
        color = item.get("color")
        max_turns = item.get("max_turns", item.get("maxTurns"))
        direct_tools = item.get("tools")
        toolset_name = item.get("toolset")
        if direct_tools is not None:
            tools = tuple(direct_tools) if isinstance(direct_tools, list) else ()
        elif isinstance(toolset_name, str) and toolset_name in toolsets:
            tools = toolsets[toolset_name]
        else:
            if isinstance(toolset_name, str):
                raise ZCodeProfileError(f"unknown toolset for {agent_id}: {toolset_name}")
            tools = ()
        if color not in VALID_COLORS or not tools or not all(isinstance(tool, str) and tool for tool in tools):
            raise ZCodeProfileError(f"invalid ZCode options for {agent_id}")
        if not isinstance(max_turns, int) or isinstance(max_turns, bool) or max_turns < 1:
            raise ZCodeProfileError(f"invalid max_turns for {agent_id}")
        prompt = _relative_runtime_path(root, f"{canonical['path']}/PROMPT.md")
        manifest = _relative_runtime_path(root, canonical["manifest"])
        manifest_value = _read_yaml(root / manifest)
        if manifest_value.get("agent") != agent_id:
            raise ZCodeProfileError(f"manifest identity mismatch for {agent_id}")
        native = manifest_value.get("native")
        if not isinstance(native, list) or not native:
            raise ZCodeProfileError(f"missing native skill for {agent_id}")
        native_entries = [entry for entry in native if isinstance(entry, dict) and isinstance(entry.get("path"), str)]
        native_prefix = f"{canonical['path']}/skills/native/"
        native_matches = [entry["path"] for entry in native_entries if entry["path"].startswith(native_prefix)]
        if len(native_matches) != 1:
            raise ZCodeProfileError(f"missing native skill for {agent_id}")
        native_path = native_matches[0]
        native_skill = _relative_runtime_path(root, f"{native_path}/SKILL.md")
        openai_candidate = root / native_path / "agents/openai.yaml"
        openai_directory = openai_candidate.parent
        if openai_directory.is_dir() and not openai_candidate.is_file():
            raise ZCodeProfileError(f"missing native interface metadata for {agent_id}")
        openai_path = _relative_runtime_path(root, f"{native_path}/agents/openai.yaml") if openai_candidate.is_file() else None
        profile = ZCodeProfile(
            agent_id=agent_id,
            title=canonical["title"],
            purpose=canonical["purpose"],
            prompt_path=prompt,
            manifest_path=manifest,
            native_skill_path=native_skill,
            openai_path=openai_path,
            color=color,
            tools=tools,
            max_turns=max_turns,
        )
        fingerprint = activation_fingerprint(profile, root)
        profile = replace(profile, activation_fingerprint=fingerprint)
        profiles.append(replace(profile, activation_probe=activation_probe(profile)))
    return tuple(profiles)


def load_pilot_profiles(root: Path, config_path: Path | None = None) -> tuple[ZCodeProfile, ...]:
    """Alias de compatibilidade; o adaptador atual sempre exige cobertura das 36 personas."""
    return load_profiles(root, config_path)


def _yaml_scalar(value: str) -> str:
    return yaml.safe_dump(value, allow_unicode=True, default_flow_style=True).removesuffix("\n...\n").strip()


def render_profile(profile: ZCodeProfile, root: Path) -> str:
    """Renderiza frontmatter nativo e ponte de ativação canônica verificável."""
    calculated = activation_fingerprint(profile, root)
    if calculated != profile.activation_fingerprint:
        raise ZCodeProfileError(f"stale activation fingerprint for {profile.agent_id}")
    tools = "[" + ", ".join(_yaml_scalar(tool) for tool in profile.tools) + "]"
    description = f"{profile.title}. {profile.purpose}"
    runtime = root.resolve().as_posix()
    openai_instruction = f"\n4. `{profile.openai_path}` — native interface metadata when present." if profile.openai_path else ""
    governance_step = 5 if profile.openai_path else 4
    return f"""---
name: {profile.agent_id}
description: {_yaml_scalar(description)}
color: {profile.color}
tools: {tools}
maxTurns: {profile.max_turns}
memory: project
injectAgentsMd: true
x-managed-by: {MANAGED_BY}
x-render-contract: {RENDER_CONTRACT_VERSION}
x-activation-fingerprint: {profile.activation_fingerprint}
x-activation-probe: {_yaml_scalar(profile.activation_probe)}
---

You are the Agents Squad `{profile.agent_id}` persona. Assume this identity before acting.

Canonical runtime: `{runtime}`.
Before work, read and internalize:
1. `{profile.prompt_path}` — complete persona definition.
2. `{profile.manifest_path}` — skill, handoff, and memory manifest.
3. `{profile.native_skill_path}` — native method.{openai_instruction}
{governance_step}. `AGENTS.md`, `config/workflow.yaml`, and the active `work/<WORK-ID>/status.yaml` and cited artifacts.

Use the canonical prompt in full; this condensed profile is only the native ZCode activation layer. Load only skills required by the task. Follow governed artifacts, handoffs, memory paths, WIP limits, gates, evidence, and role boundaries. Never approve your own work where segregation of duties applies. Do not invent a work item, approval, tool, credential, or result. Mode-aware contract: when the active mode is Full and no work item is supplied, report that requirement instead of guessing one. In Consult or Light, proceed without a work item.

## Activation probe contract
When the user message is exactly `ZCODE_ACTIVATION_PROBE`, do not perform work or claim host execution. Respond with exactly `{profile.activation_probe}` and nothing else.
This probe verifies rendered contract data, not host execution.
"""


def expected_profiles(root: Path, config_path: Path | None = None) -> dict[str, str]:
    """Retorna nome e conteúdo esperado em ordem canônica."""
    return {_safe_filename(profile.agent_id): render_profile(profile, root) for profile in load_profiles(root, config_path)}


def expected_activation_evidence(root: Path, config_path: Path | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Expõe fingerprints e respostas de probe esperadas sem alegar execução pelo host."""
    profiles = load_profiles(root, config_path)
    return (
        {profile.agent_id: profile.activation_fingerprint for profile in profiles},
        {profile.agent_id: profile.activation_probe for profile in profiles},
    )


def _frontmatter(content: bytes) -> dict[str, Any] | None:
    """Interpreta apenas frontmatter UTF-8 delimitado e limitado no início do arquivo."""
    bounded = content[: MAX_FRONTMATTER_BYTES + 8].replace(b"\r\n", b"\n")
    if not bounded.startswith(b"---\n"):
        return None
    delimiter = bounded.find(b"\n---\n", 4)
    if delimiter < 0 and bounded.endswith(b"\n---\n"):
        delimiter = len(bounded) - 5
    if delimiter < 0 and bounded.endswith(b"\n---"):
        delimiter = len(bounded) - 4
    if delimiter < 0 or delimiter > MAX_FRONTMATTER_BYTES:
        return None
    try:
        value = yaml.safe_load(bounded[4:delimiter].decode("utf-8"))
    except (UnicodeError, yaml.YAMLError):
        return None
    return value if isinstance(value, dict) else None


def _is_managed(content: bytes | str) -> bool:
    if isinstance(content, str):
        content = content.encode("utf-8")
    value = _frontmatter(content)
    return value is not None and value.get("x-managed-by") == MANAGED_BY


def _is_linked_path(path: Path) -> bool:
    """Detecta symlinks e junctions sem resolver o caminho lexical."""
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _assert_real_directory(path: Path, *, allow_missing: bool = False) -> None:
    """Bloqueia diretórios e ancestrais existentes que sejam symlinks ou junctions."""
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.exists() or _is_linked_path(current):
            if _is_linked_path(current):
                raise ZCodeProfileError(f"linked path rejected: {current}")
    if not allow_missing and not path.is_dir():
        raise ZCodeProfileError(f"not a directory: {path}")


def _profile_files(destination: Path) -> list[Path]:
    if not destination.exists():
        return []
    _assert_real_directory(destination)
    result: list[Path] = []
    try:
        entries = sorted(destination.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ZCodeProfileError(f"cannot inspect destination: {exc}") from exc
    for entry in entries:
        if entry.suffix != PROFILE_SUFFIX:
            continue
        if entry.is_symlink():
            raise ZCodeProfileError(f"symlink profile rejected: {entry.name}")
        if not entry.is_file():
            raise ZCodeProfileError(f"non-file profile rejected: {entry.name}")
        result.append(entry)
    return result


def _write_atomic(path: Path, content: bytes | str) -> None:
    """Persiste bytes por temporário no mesmo diretório, fsync e replace."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    if path.is_symlink():
        raise ZCodeProfileError(f"symlink profile rejected: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _preflight(destination: Path, expected: dict[str, str]) -> tuple[dict[Path, bytes | None], list[str], list[str], list[str]]:
    """Lê todos os alvos antes de qualquer escrita e classifica mudança, igualdade e stale."""
    try:
        originals: dict[Path, bytes | None] = {}
        changed: list[str] = []
        unchanged: list[str] = []
        if destination.exists():
            _assert_real_directory(destination)
        else:
            _assert_real_directory(destination, allow_missing=True)
        existing = {path.name: path for path in _profile_files(destination)}
        stale = sorted(name for name, path in existing.items() if name not in expected and _is_managed(path.read_bytes()))
        for name, rendered in expected.items():
            target = destination / name
            current = target.read_bytes() if target.exists() else None
            if target.is_symlink():
                raise ZCodeProfileError(f"symlink profile rejected: {name}")
            if current is not None and not _is_managed(current):
                raise ZCodeProfileError(f"unmanaged profile conflict: {name}")
            originals[target] = current
            if current == rendered.encode("utf-8"):
                unchanged.append(name)
            else:
                changed.append(name)
        return originals, changed, unchanged, stale
    except ZCodeProfileError:
        raise
    except OSError as exc:
        raise ZCodeProfileError(f"cannot inspect profile destination {destination}: {exc}") from exc


def _restore_originals(originals: dict[Path, bytes | None]) -> list[str]:
    """Tenta restaurar todos os alvos, acumulando falhas sem interromper rollback."""
    errors: list[str] = []
    for target, original in originals.items():
        try:
            if target.is_symlink():
                raise OSError("rollback target became symlink")
            if original is None:
                target.unlink(missing_ok=True)
            else:
                _write_atomic(target, original)
        except Exception as exc:
            errors.append(f"{target.name}: {exc}")
    return errors


def _default_backup_root(destination: Path) -> Path:
    return destination.parent / f"{destination.name}-backups"


def _confined_backup_root(destination: Path, backup_root: Path) -> Path:
    """Exige backup fora do destino e bloqueia links em componentes existentes."""
    destination_abs = destination.absolute()
    backup_abs = backup_root.absolute()
    try:
        backup_abs.relative_to(destination_abs)
    except ValueError:
        pass
    else:
        raise ZCodeProfileError("backup root must be outside managed destination")
    _assert_real_directory(backup_abs, allow_missing=True)
    return backup_abs.resolve(strict=False)


def create_migration_backup(destination: Path, backup_root: Path | None = None) -> Path | None:
    """Copia bytes gerenciados para backup externo collision-safe com hashes verificáveis."""
    if not destination.exists():
        return None
    files = [path for path in _profile_files(destination) if _is_managed(path.read_bytes())]
    if not files:
        return None
    root = _confined_backup_root(destination, backup_root or _default_backup_root(destination))
    digest_input = b"".join(path.name.encode("utf-8") + b"\0" + path.read_bytes() for path in files)
    base_name = f"migration-{hashlib.sha256(digest_input).hexdigest()[:16]}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        _assert_real_directory(root)
        backup = root / base_name
        suffix = 0
        while backup.exists():
            suffix += 1
            backup = root / f"{base_name}-{suffix}"
        files_dir = backup / "files"
        files_dir.mkdir(parents=True)
    except ZCodeProfileError:
        raise
    except OSError as exc:
        raise ZCodeProfileError(f"migration backup failed: {exc}") from exc
    manifest_files: dict[str, dict[str, Any]] = {}
    try:
        for source in files:
            data = source.read_bytes()
            target = files_dir / source.name
            _write_atomic(target, data)
            manifest_files[source.name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        manifest = {"schema_version": 1, "managed_by": MANAGED_BY, "source": str(destination.absolute()), "files": manifest_files}
        _write_atomic(backup / BACKUP_MANIFEST, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    except ZCodeProfileError:
        shutil.rmtree(backup, ignore_errors=True)
        raise
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        shutil.rmtree(backup, ignore_errors=True)
        raise ZCodeProfileError(f"migration backup failed: {exc}") from exc
    return backup.resolve()


def restore_migration_backup(destination: Path, backup: Path) -> None:
    """Valida manifesto e restaura o conjunto integral de Markdown gerenciado no backup."""
    try:
        backup = backup.resolve(strict=True)
        manifest = json.loads((backup / BACKUP_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ZCodeProfileError(f"invalid migration backup: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ZCodeProfileError("invalid migration backup manifest")
    files = manifest.get("files")
    if manifest.get("managed_by") != MANAGED_BY or not isinstance(files, dict):
        raise ZCodeProfileError("invalid migration backup manifest")
    originals: dict[str, bytes] = {}
    for name, metadata in files.items():
        if _safe_filename(name.removesuffix(PROFILE_SUFFIX)) != name or not isinstance(metadata, dict):
            raise ZCodeProfileError("invalid migration backup filename")
        data = (backup / "files" / name).read_bytes()
        if metadata.get("size") != len(data) or metadata.get("sha256") != hashlib.sha256(data).hexdigest():
            raise ZCodeProfileError(f"migration backup hash mismatch: {name}")
        originals[name] = data
    destination.mkdir(parents=True, exist_ok=True)
    for path in _profile_files(destination):
        if _is_managed(path.read_bytes()) and path.name not in originals:
            path.unlink()
    for name, data in originals.items():
        _write_atomic(destination / name, data)


def sync_profiles(
    root: Path,
    destination: Path,
    *,
    config_path: Path | None = None,
    check: bool = False,
    dry_run: bool = False,
    migrate: bool = False,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    """Instala/verifica perfis com preflight global, rollback byte-exato e backup de migração."""
    root = root.resolve()
    destination = destination.absolute()
    expected = expected_profiles(root, config_path)
    fingerprints, probes = expected_activation_evidence(root, config_path)
    originals, changed, unchanged, stale = _preflight(destination, expected)
    if check and (changed or stale):
        details = []
        if changed:
            details.append(f"profiles out of sync: {', '.join(changed)}")
        if stale:
            details.append(f"stale managed profiles: {', '.join(stale)}")
        raise ZCodeProfileError("; ".join(details))
    backup: Path | None = None
    should_backup = migrate and bool(changed) and destination.exists() and any(
        original is not None for original in originals.values()
    )
    if not check and not dry_run and should_backup:
        backup = create_migration_backup(destination, backup_root)
    if not check and not dry_run and changed:
        try:
            if destination.exists():
                _assert_real_directory(destination)
            else:
                _assert_real_directory(destination, allow_missing=True)
                destination.mkdir(parents=True, exist_ok=True)
            for name in changed:
                _write_atomic(destination / name, expected[name])
        except (OSError, ZCodeProfileError) as exc:
            rollback_errors = _restore_originals(originals)
            if backup is not None:
                try:
                    restore_migration_backup(destination, backup)
                except Exception as restore_exc:
                    rollback_errors.append(f"backup restore: {restore_exc}")
            detail = f"profile sync failed: {exc}"
            if rollback_errors:
                detail += f"; rollback failed: {'; '.join(rollback_errors)}"
            raise ZCodeProfileError(detail) from exc
    return {
        "changed": changed,
        "unchanged": unchanged,
        "stale": stale,
        "backup": str(backup) if backup else None,
        "fingerprints": fingerprints,
        "probes": probes,
        "host_execution_verified": False,
    }


def remove_managed_profiles(destination: Path, *, dry_run: bool = False) -> list[str]:
    """Remove somente Markdown com proveniência válida, sem seguir links e com preflight global."""
    destination = destination.absolute()
    if not destination.exists():
        return []
    managed = [path for path in _profile_files(destination) if _is_managed(path.read_bytes())]
    removed = [path.name for path in managed]
    if dry_run:
        return removed
    originals = {path: path.read_bytes() for path in managed}
    try:
        for target in managed:
            if target.is_symlink():
                raise ZCodeProfileError(f"symlink profile rejected: {target.name}")
            target.unlink()
    except (OSError, ZCodeProfileError) as exc:
        rollback_errors = _restore_originals(originals)
        detail = f"managed profile removal failed: {exc}"
        if rollback_errors:
            detail += f"; rollback failed: {'; '.join(rollback_errors)}"
        raise ZCodeProfileError(detail) from exc
    return removed
