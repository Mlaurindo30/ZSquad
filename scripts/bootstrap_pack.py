"""Agent Bootstrap Pack — gera pacote de inicialização consumível por runtimes externos.

Estrutura do pack:
    <output_dir>/
      system_prompt.md            # prompt completo renderizado
      skills_manifest.json        # catálogo de skills carregadas, ordenadas e com metadados
      work_item_context.json      # contexto estruturado do work item
      bootstrap_metadata.json     # metadados do pack, agente, timestamp, origem
      runtime_hints.yaml          # instruções de consumo para o runtime externo

Uso:
    from scripts.bootstrap_pack import create_bootstrap_pack

    create_bootstrap_pack(
        agent="software-engineer",
        work_item="work/TASK-001",
        output_dir="dist/bootstrap-packs/software-engineer-TASK-001",
        auto_select_skills=True,
        max_discovered=3,
    )
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

import sys
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from agent_squad import AgentSquad, SquadError


def _extract_work_item_text(work_item_path: Path) -> str:
    parts: list[str] = []
    status_file = work_item_path / "status.yaml"
    if status_file.exists():
        try:
            data = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                title = data.get("title", "")
                description = data.get("description", "")
                if title:
                    parts.append(title)
                if description:
                    parts.append(str(description))
        except Exception:
            pass
    epic_file = work_item_path / "epic.md"
    if epic_file.exists():
        try:
            parts.append(epic_file.read_text(encoding="utf-8")[:2000])
        except Exception:
            pass
    return " ".join(parts)


def _extract_work_item_structured(work_item_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(work_item_path), "exists": work_item_path.exists()}
    if not work_item_path.exists():
        return result

    status_file = work_item_path / "status.yaml"
    if status_file.exists():
        try:
            result["status"] = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
        except Exception:
            result["status"] = {}

    epic_file = work_item_path / "epic.md"
    if epic_file.exists():
        try:
            result["epic_md_preview"] = epic_file.read_text(encoding="utf-8")[:4000]
        except Exception:
            pass

    return result


def _skill_frontmatter(skill_md: Path) -> dict[str, Any]:
    try:
        text = skill_md.read_text(encoding="utf-8")
        if text.startswith("\ufeff"):
            text = text[1:]
        if not text.startswith("---"):
            return {}
        end = text.find("\n---", 3)
        if end == -1:
            return {}
        data = yaml.safe_load(text[3:end]) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def create_bootstrap_pack(
    agent: str,
    output_dir: str | Path,
    work_item: str | None = None,
    assigned: list[str] | None = None,
    discovered: list[str] | None = None,
    auto_select_skills: bool = False,
    max_discovered: int = 3,
    root: str | Path | None = None,
) -> Path:
    """Gera um pacote de inicialização completo para um agente.

    Args:
        agent: ID do agente (ex: ``software-engineer``).
        output_dir: diretório onde o pack será escrito.
        work_item: caminho do work item (ex: ``work/TASK-001``).
        assigned: skills atribuídas explícitas.
        discovered: skills descobertas explícitas.
        auto_select_skills: se ``True``, auto-seleciona skills relevantes via selector.
        max_discovered: máximo de skills descobertas auto-selecionadas.
        root: raiz do squad. Se ``None``, detecta automaticamente.

    Returns:
        Path para o diretório do pack criado.
    """
    root_dir = Path(root).resolve() if root else Path(__file__).resolve().parent.parent
    squad = AgentSquad(root=root_dir)
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    if discovered is None:
        discovered = []

    if auto_select_skills and work_item and not discovered:
        try:
            from scripts.skill_selector import suggest_and_load
            item_path = root_dir / work_item
            task_text = _extract_work_item_text(item_path)
            if task_text.strip():
                suggestions = suggest_and_load(
                    task=task_text,
                    skills_dir=root_dir / "skills",
                    max_results=max_discovered,
                    cache=True,
                )
                discovered = [s.ranked.skill.path for s in suggestions if s.loaded]
        except Exception:
            discovered = discovered or []

    try:
        packet = squad.activation_packet(agent, assigned=assigned, discovered=discovered, item=work_item)
    except SquadError as exc:
        if auto_select_skills and discovered:
            try:
                from agent_squad import read_yaml
                manifest = read_yaml(root_dir / squad.agents[agent]["manifest"])
                native_count = len(manifest.get("native", []))
                assigned_count = len(assigned or [])
                budget = max(0, 7 - native_count - assigned_count)
                reduced = discovered[:budget]
                if reduced != discovered:
                    packet = squad.activation_packet(agent, assigned=assigned, discovered=reduced, item=work_item)
                else:
                    discovered = []
                    packet = squad.activation_packet(agent, assigned=assigned, discovered=discovered, item=work_item)
            except Exception:
                discovered = []
                packet = squad.activation_packet(agent, assigned=assigned, discovered=discovered, item=work_item)
        else:
            raise

    # 1. system_prompt.md
    import importlib.util
    spec = importlib.util.spec_from_file_location("render_agent_prompt", str(root_dir / "scripts" / "render_agent_prompt.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    system_prompt = mod.render_agent_prompt(
        agent=agent,
        work_item=work_item,
        assigned=assigned,
        discovered=discovered,
        auto_select_skills=False,
        max_discovered=max_discovered,
    )
    (out / "system_prompt.md").write_text(system_prompt, encoding="utf-8")

    # 2. skills_manifest.json
    skills_manifest: dict[str, Any] = {
        "agent": agent,
        "work_item": work_item,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "skills": [],
    }
    for relative in packet.get("load_order", []):
        skill_path = root_dir / relative
        skill_dir = skill_path.parent
        fm = _skill_frontmatter(skill_path)
        skills_manifest["skills"].append({
            "path": relative,
            "name": fm.get("name", skill_dir.name),
            "description": fm.get("description", ""),
            "source": "native" if relative in packet.get("native", []) else (
                "assigned" if relative in packet.get("assigned", []) else "discovered"
            ),
            "metadata": {k: v for k, v in fm.items() if k not in ("name", "description")},
        })
    (out / "skills_manifest.json").write_text(json.dumps(skills_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3. work_item_context.json
    item_path = root_dir / work_item if work_item else None
    work_context = _extract_work_item_structured(item_path) if item_path else {}
    (out / "work_item_context.json").write_text(json.dumps(work_context, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4. bootstrap_metadata.json
    metadata = {
        "agent": agent,
        "work_item": work_item,
        "generated_at": skills_manifest["generated_at"],
        "root": str(root_dir),
        "pack_version": "1.0.0",
        "assigned_count": len(packet.get("assigned", [])),
        "discovered_count": len(packet.get("discovered", [])),
        "native_count": len(packet.get("native", [])),
        "total_skills": len(packet.get("load_order", [])),
        "auto_selected": discovered if auto_select_skills else [],
    }
    (out / "bootstrap_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5. runtime_hints.yaml
    runtime_hints = {
        "consumption": {
            "required_files": ["system_prompt.md", "skills_manifest.json", "bootstrap_metadata.json"],
            "optional_files": ["work_item_context.json", "runtime_hints.yaml"],
            "system_prompt_entry": "system_prompt.md",
            "skills_loading_order": "skills_manifest.json -> skills in order",
        },
        "agent": {
            "id": agent,
            "primary_prompt": packet.get("prompt"),
            "total_skills": len(packet.get("load_order", [])),
            "budget_7_skills_enforced": True,
        },
        "runtime_constraints": {
            "max_skills_per_session": 7,
            "skill_sources": ["native", "assigned", "discovered"],
            "discovery_policy": "curated-local-first",
            "quarantine_is_not_loadable": True,
        },
        "sinapse": {
            "enabled": True,
            "access_via_mcp": True,
            "tools": [
                "sinapse_health", "sinapse_query", "sinapse_save_decision",
                "sinapse_save_learning", "sinapse_session_end",
            ],
            "rule": "consult before acting; never claim project state without sinapse_query first",
        },
        "engines": [
            {"name": "blast_radius_analyzer", "when": "before production/schema/credential/sensitive changes"},
            {"name": "code_health_analyzer", "when": "periodic debt/duplication/complexity audit"},
            {"name": "trajectory_refinement_engine", "when": "after real agent execution"},
        ],
        "delivery": {
            "gates": ["G1-product", "G2-design", "G3-readiness", "G4-code-security", "G5-quality", "G6-governance-release"],
            "handoff_required": True,
            "handoff_schema": "contracts/handoff.schema.json",
        },
    }
    (out / "runtime_hints.yaml").write_text(
        yaml.dump(runtime_hints, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    return out
