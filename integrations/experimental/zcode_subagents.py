"""
O que é: adaptador determinístico das personas canônicas para Subagents Markdown do ZCode.
Responsabilidade: validar configuração, renderizar perfis e gerenciar somente arquivos com proveniência Agents Squad.
Pra que serve: instalar o piloto ZCode sem duplicar manualmente identidade, skills ou caminhos do runtime.
Comportamento em falha: rejeita persona, caminho ou arquivo não gerenciado e não deixa escrita parcial.
Conexões: config/agent-registry.yaml, config/zcode-pilot-agents.yaml e agents/*/PROMPT.md.
Dependências & Imports: pathlib, hashlib, os, tempfile e PyYAML.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MANAGED_BY = "agents-squad-zcode-adapter"
PROFILE_SUFFIX = ".md"
VALID_COLORS = {"red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink"}


class ZCodeProfileError(RuntimeError):
    """Indica configuração inválida ou conflito com arquivo não gerenciado."""


@dataclass(frozen=True)
class PilotProfile:
    """Representa uma persona validada e suas opções específicas do ZCode."""

    agent_id: str
    title: str
    purpose: str
    prompt_path: str
    manifest_path: str
    native_skill_path: str
    color: str
    tools: tuple[str, ...]
    max_turns: int


def _read_yaml(path: Path) -> dict[str, Any]:
    """Lê um mapeamento YAML e falha para conteúdo ausente ou malformado."""
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ZCodeProfileError(f"invalid YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ZCodeProfileError(f"YAML must contain a mapping: {path}")
    return value


def _relative_runtime_path(root: Path, raw: str) -> str:
    """Valida que uma referência existe e permanece confinada ao runtime."""
    candidate = (root / raw).resolve()
    resolved_root = root.resolve()
    if resolved_root not in candidate.parents:
        raise ZCodeProfileError(f"path escapes runtime: {raw}")
    if not candidate.exists():
        raise ZCodeProfileError(f"missing canonical path: {raw}")
    return candidate.relative_to(resolved_root).as_posix()


def load_pilot_profiles(root: Path, config_path: Path | None = None) -> tuple[PilotProfile, ...]:
    """Resolve as cinco configurações do piloto contra o registro canônico."""
    config = _read_yaml(config_path or root / "config/zcode-pilot-agents.yaml")
    registry = _read_yaml(root / "config/agent-registry.yaml")
    entries = config.get("profiles")
    agents = registry.get("agents")
    if config.get("managed_by") != MANAGED_BY or not isinstance(entries, list) or not isinstance(agents, list):
        raise ZCodeProfileError("invalid ZCode pilot configuration")
    by_id = {item.get("id"): item for item in agents if isinstance(item, dict)}
    profiles: list[PilotProfile] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ZCodeProfileError("each profile requires an id")
        agent_id = item["id"]
        if agent_id in seen:
            raise ZCodeProfileError(f"duplicate profile id: {agent_id}")
        seen.add(agent_id)
        canonical = by_id.get(agent_id)
        if canonical is None:
            raise ZCodeProfileError(f"unknown canonical persona: {agent_id}")
        color = item.get("color")
        tools = item.get("tools")
        max_turns = item.get("max_turns")
        if color not in VALID_COLORS or not isinstance(tools, list) or not tools or not all(isinstance(tool, str) and tool for tool in tools):
            raise ZCodeProfileError(f"invalid ZCode options for {agent_id}")
        if not isinstance(max_turns, int) or max_turns < 1:
            raise ZCodeProfileError(f"invalid max_turns for {agent_id}")
        prompt = _relative_runtime_path(root, f"{canonical['path']}/PROMPT.md")
        manifest = _relative_runtime_path(root, canonical["manifest"])
        manifest_value = _read_yaml(root / manifest)
        native = manifest_value.get("native")
        if not isinstance(native, list) or not native or not isinstance(native[0], dict) or not isinstance(native[0].get("path"), str):
            raise ZCodeProfileError(f"missing native skill for {agent_id}")
        native_skill = _relative_runtime_path(root, f"{native[0]['path']}/SKILL.md")
        profiles.append(PilotProfile(
            agent_id=agent_id,
            title=str(canonical["title"]),
            purpose=str(canonical["purpose"]),
            prompt_path=prompt,
            manifest_path=manifest,
            native_skill_path=native_skill,
            color=color,
            tools=tuple(tools),
            max_turns=max_turns,
        ))
    return tuple(profiles)


def _yaml_scalar(value: str) -> str:
    """Serializa um escalar curto para frontmatter YAML estável e sem marcador documental."""
    return yaml.safe_dump(value, allow_unicode=True, default_flow_style=True).removesuffix("\n...\n").strip()


def render_profile(profile: PilotProfile, root: Path) -> str:
    """Renderiza um arquivo de Subagent compatível com o parser nativo do ZCode."""
    source_paths = (profile.prompt_path, profile.manifest_path, profile.native_skill_path)
    digest = hashlib.sha256("\n".join((root / path).read_text(encoding="utf-8") for path in source_paths).encode("utf-8")).hexdigest()
    tools = "[" + ", ".join(_yaml_scalar(tool) for tool in profile.tools) + "]"
    description = f"{profile.title}. {profile.purpose}"
    runtime = root.resolve().as_posix()
    return f"""---
name: {profile.agent_id}
description: {_yaml_scalar(description)}
color: {profile.color}
tools: {tools}
injectAgentsMd: true
memory: project
maxTurns: {profile.max_turns}
background: false
x-managed-by: {MANAGED_BY}
x-source-digest: {digest}
---
You are the Agents Squad `{profile.agent_id}` persona. Assume this identity before acting.

Canonical runtime: `{runtime}`.
Before work, read and internalize:
1. `{runtime}/{profile.prompt_path}` — complete persona definition.
2. `{runtime}/{profile.manifest_path}` — skill, handoff, and memory manifest.
3. `{runtime}/{profile.native_skill_path}` — native method.
4. `AGENTS.md`, `config/workflow.yaml`, and the active `work/<WORK-ID>/status.yaml` and cited artifacts.

Use the canonical prompt in full; this condensed profile is only the native ZCode activation layer. Load only skills required by the task. Follow governed artifacts, handoffs, memory paths, WIP limits, gates, evidence, and role boundaries. Never approve your own work where segregation of duties applies. Do not invent a work item, approval, tool, credential, or result. If no work item is supplied, report that requirement instead of guessing one.
"""


def expected_profiles(root: Path, config_path: Path | None = None) -> dict[str, str]:
    """Retorna nome de arquivo e conteúdo esperado para todo o piloto."""
    return {f"{profile.agent_id}{PROFILE_SUFFIX}": render_profile(profile, root) for profile in load_pilot_profiles(root, config_path)}


def _is_managed(content: str) -> bool:
    """Reconhece proveniência somente no frontmatter YAML inicial e válido."""
    if not content.startswith("---\n"):
        return False
    closing = content.find("\n---\n", 4)
    if closing < 0:
        return False
    try:
        frontmatter = yaml.safe_load(content[4:closing])
    except yaml.YAMLError:
        return False
    return isinstance(frontmatter, dict) and frontmatter.get("x-managed-by") == MANAGED_BY


def _write_atomic(target: Path, content: str) -> None:
    """Grava conteúdo por temporário durável e substituição no mesmo diretório."""
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _restore_originals(originals: dict[Path, str | None]) -> list[str]:
    """Tenta restaurar todos os alvos e retorna falhas sem interromper o rollback."""
    errors: list[str] = []
    for target, original in originals.items():
        try:
            if original is None:
                target.unlink(missing_ok=True)
            else:
                _write_atomic(target, original)
        except OSError as exc:
            errors.append(f"{target.name}: {exc}")
    return errors


def sync_profiles(root: Path, destination: Path, *, check: bool = False, dry_run: bool = False) -> dict[str, list[str]]:
    """Instala ou verifica os perfis sem sobrescrever arquivos alheios."""
    expected = expected_profiles(root)
    changed: list[str] = []
    unchanged: list[str] = []
    conflicts: list[str] = []
    for filename, content in expected.items():
        target = destination / filename
        if target.exists():
            current = target.read_text(encoding="utf-8")
            if current == content:
                unchanged.append(filename)
                continue
            if not _is_managed(current):
                conflicts.append(filename)
                continue
        changed.append(filename)
    stale = []
    if destination.exists():
        stale = sorted(
            target.name
            for target in destination.glob(f"*{PROFILE_SUFFIX}")
            if target.name not in expected and _is_managed(target.read_text(encoding="utf-8"))
        )
    if conflicts:
        raise ZCodeProfileError("unmanaged profile conflict: " + ", ".join(conflicts))
    if check and stale:
        raise ZCodeProfileError("stale managed profiles: " + ", ".join(stale))
    if not check and not dry_run:
        originals: dict[Path, str | None] = {}
        try:
            for filename in changed:
                content = expected[filename]
                target = destination / filename
                originals[target] = target.read_text(encoding="utf-8") if target.exists() else None
                destination.mkdir(parents=True, exist_ok=True)
                _write_atomic(target, content)
        except OSError as exc:
            rollback_errors = _restore_originals(originals)
            details = f"profile sync failed: {exc}"
            if rollback_errors:
                details += "; rollback failed: " + ", ".join(rollback_errors)
            raise ZCodeProfileError(details) from exc
    if check and changed:
        raise ZCodeProfileError("profiles out of sync: " + ", ".join(changed))
    return {"changed": changed, "unchanged": unchanged, "conflicts": conflicts, "stale": stale}


def remove_managed_profiles(destination: Path, *, dry_run: bool = False) -> list[str]:
    """Remove somente os cinco perfis reconhecidos como gerenciados."""
    removed: list[str] = []
    if not destination.exists():
        return removed
    for target in sorted(destination.glob(f"*{PROFILE_SUFFIX}")):
        if _is_managed(target.read_text(encoding="utf-8")):
            removed.append(target.name)
            if not dry_run:
                target.unlink()
    return removed
