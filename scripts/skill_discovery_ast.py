"""AST scan para auto-descoberta de skills/tools (padrão Hermes Agent).

Varre ``skills/**/SKILL.md`` e módulos Python em busca de registros
auto-declarados, com cache disco por ``(mtime_ns, size)``.

Uso:
    from scripts.skill_discovery_ast import discover_skills, discover_tools

    skills = discover_skills(Path("skills"))
    tools = discover_tools(Path("tools"))
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveredSkill:
    name: str
    path: str
    source: str = "local"
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveredTool:
    name: str
    module: str
    source: str = "local"
    schema: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / ".skill_discovery_cache.json"


def _load_cache(cache_dir: Path) -> Dict[str, Dict[str, Any]]:
    path = _cache_path(cache_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache_dir: Path, cache: Dict[str, Dict[str, Any]]) -> None:
    path = _cache_path(cache_dir)
    try:
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _file_key(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _is_registry_register_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "register"
        and isinstance(func.value, ast.Name)
        and func.value.id == "registry"
    )


def _module_registers_tools(module_path: Path) -> bool:
    try:
        source = module_path.read_text(encoding="utf-8")
    except OSError:
        return False
    if "registry" not in source or "register" not in source:
        return False
    try:
        tree = ast.parse(source, filename=str(module_path))
    except SyntaxError:
        return False
    return any(_is_registry_register_call(stmt) for stmt in tree.body)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_skills(
    skills_dir: Path,
    *,
    cache: bool = True,
    cache_dir: Optional[Path] = None,
) -> List[DiscoveredSkill]:
    """Descobre skills locais escaneando ``skills/**/SKILL.md``.

    Args:
        skills_dir: diretório base de skills.
        cache: se ``True``, usa cache disco.
        cache_dir: diretório de cache. Se ``None``, usa ``skills_dir``.

    Returns:
        Lista de ``DiscoveredSkill``.
    """
    cache_dir = cache_dir or skills_dir
    disk_cache = _load_cache(cache_dir) if cache else {}
    fresh_cache: Dict[str, Dict[str, Any]] = {}

    skills: List[DiscoveredSkill] = []
    if not skills_dir.exists():
        return skills

    for skill_md in sorted(skills_dir.rglob("SKILL.md")):
        key = _file_key(skill_md)
        cache_key = str(skill_md)
        if cache and cache_key in disk_cache and disk_cache[cache_key].get("key") == key:
            data = disk_cache[cache_key]
            skills.append(DiscoveredSkill(
                name=data.get("name", skill_md.parent.name),
                path=str(skill_md),
                source="local",
                description=data.get("description", ""),
                metadata=data.get("metadata", {}),
            ))
            fresh_cache[cache_key] = data
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        name, description, metadata = _parse_skill_frontmatter(content)
        data = {
            "name": name or skill_md.parent.name,
            "path": str(skill_md),
            "source": "local",
            "description": description or "",
            "metadata": metadata,
            "key": key,
        }
        skills.append(DiscoveredSkill(
            name=data["name"],
            path=data["path"],
            source=data["source"],
            description=data["description"],
            metadata=data["metadata"],
        ))
        fresh_cache[cache_key] = data

    if cache:
        _save_cache(cache_dir, fresh_cache)
    return skills


def discover_tools(
    tools_dir: Path,
    *,
    cache: bool = True,
    cache_dir: Optional[Path] = None,
) -> List[DiscoveredTool]:
    """Descobre tools auto-registradas escaneando módulos Python.

    Usa AST scan para encontrar ``registry.register(...)`` no top-level.
    Cache por ``(mtime_ns, size)``.

    Args:
        tools_dir: diretório base de tools.
        cache: se ``True``, usa cache disco.
        cache_dir: diretório de cache. Se ``None``, usa ``tools_dir``.

    Returns:
        Lista de ``DiscoveredTool``.
    """
    cache_dir = cache_dir or tools_dir
    disk_cache = _load_cache(cache_dir) if cache else {}
    fresh_cache: Dict[str, Dict[str, Any]] = {}

    tools: List[DiscoveredTool] = []
    if not tools_dir.exists():
        return tools

    py_files = sorted(tools_dir.rglob("*.py"))
    for module_path in py_files:
        key = _file_key(module_path)
        cache_key = str(module_path)
        if cache and cache_key in disk_cache and disk_cache[cache_key].get("key") == key:
            data = disk_cache[cache_key]
            if data.get("registers"):
                tools.append(DiscoveredTool(
                    name=data["name"],
                    module=data["module"],
                    source="local",
                    schema=data.get("schema", {}),
                ))
                fresh_cache[cache_key] = data
            continue
        registers = _module_registers_tools(module_path)
        data = {
            "name": module_path.stem,
            "module": str(module_path),
            "source": "local",
            "schema": {},
            "registers": registers,
            "key": key,
        }
        if registers:
            tools.append(DiscoveredTool(
                name=data["name"],
                module=data["module"],
                source=data["source"],
                schema=data["schema"],
            ))
        fresh_cache[cache_key] = data

    if cache:
        _save_cache(cache_dir, fresh_cache)
    return tools


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def _coerce_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _coerce_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_json_safe(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _parse_skill_frontmatter(content: str) -> tuple[str, str, Dict[str, Any]]:
    name = ""
    description = ""
    metadata: Dict[str, Any] = {}
    if content.startswith("\ufeff"):
        content = content[1:]
    if not content.startswith("---"):
        return name, description, metadata
    end = content.find("\n---", 3)
    if end == -1:
        return name, description, metadata
    frontmatter_text = content[3:end]
    try:
        import yaml
        data = yaml.safe_load(frontmatter_text) or {}
    except Exception:
        data = {}
    if isinstance(data, dict):
        name = data.get("name", "")
        description = data.get("description", "")
        metadata = _coerce_json_safe({k: v for k, v in data.items() if k not in ("name", "description")})
    return name, description, metadata
