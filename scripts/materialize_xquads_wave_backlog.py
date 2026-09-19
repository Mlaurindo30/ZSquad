"""Materialize deterministic work-item commands for the 13 xquads source squads.

O que é: gerador local de manifesto para as stories e tasks da ingestão xquads.
Responsabilidade: eliminar expansão implícita e produzir IDs, dependências e comandos reproduzíveis.
Pra que serve: tornar o épico de ondas executável exclusivamente via agent_squad.py.
Comportamento em falha: valida colisões/contagens e não cria work items sem --apply.
Conexões: plans/agents-squad-2.0-build-backlog.yaml e scripts/agent_squad.py.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml


TASK_SUFFIXES = (
    "SQUAD",
    "PERSONAS",
    "TASKS",
    "WORKFLOWS",
    "SUPPORT",
    "MAPPING",
    "REVIEW",
    "PROMOTION",
)


def slug(value: str) -> str:
    """Normaliza texto para um identificador seguro em kebab-case."""
    return value.upper().replace("_", "-")


def build_manifest(root: Path) -> dict:
    """Gera a estrutura de manifesto de work items para ondas de evolução."""
    plan_path = root / "work/agent_squad/EPIC-SQUAD-EVOLUTION-20260818/plans/agents-squad-2.0-build-backlog.yaml"
    if not plan_path.is_file():
        plan_path = root / "scripts/tests/fixtures/agents-squad-2.0-build-backlog.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    epic = next(item for item in plan["epics"] if item["id"] == "EPIC-SQUAD-2-WAVES")
    squads = []
    for wave in epic["waves"]:
        for source in wave["squads"]:
            squad_slug = slug(source["id"])
            story_id = f"US-SQUAD-2-{squad_slug}"
            tasks = []
            previous = None
            for suffix in TASK_SUFFIXES:
                task_id = f"TASK-SQUAD-2-{squad_slug}-{suffix}"
                tasks.append(
                    {
                        "id": task_id,
                        "type": "task",
                        "risk": "high" if source["id"] == "cybersecurity" else "medium",
                        "depends_on": [previous] if previous else [story_id],
                        "owner": "governance-auditor" if suffix == "REVIEW" else "skill-curator",
                    }
                )
                previous = task_id
            squads.append(
                {
                    "source_squad": source["id"],
                    "wave": wave["wave"],
                    "counts": {key: source[key] for key in ("personas", "tasks", "workflows")},
                    "story": {"id": story_id, "type": "story", "risk": tasks[0]["risk"], "owner": "delivery-orchestrator"},
                    "tasks": tasks,
                }
            )
    ids = [entry["story"]["id"] for entry in squads] + [task["id"] for entry in squads for task in entry["tasks"]]
    if len(squads) != 13 or len(ids) != 117 or len(ids) != len(set(ids)):
        raise ValueError("manifest counts or IDs are invalid")
    return {
        "version": 1,
        "generator": "scripts/materialize_xquads_wave_backlog.py",
        "apply_default": False,
        "stories": 13,
        "tasks": 104,
        "squads": squads,
    }


WORK_ITEM_TIMEOUT_SECONDS = 30


def main() -> int:
    """Ponto de entrada CLI para materialização do backlog de ondas."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = build_manifest(root)
    output = args.output or root / "work/agent_squad/EPIC-SQUAD-EVOLUTION-20260818/plans/xquads-wave-work-items.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.apply:
        for entry in manifest["squads"]:
            for item in (entry["story"], *entry["tasks"]):
                subprocess.run(
                    [sys.executable, str(root / "scripts/agent_squad.py"), "--root", str(root), "init-work-item", "--id", item["id"], "--risk", item["risk"]],
                    check=True,
                    timeout=WORK_ITEM_TIMEOUT_SECONDS,
                )
    print(f"WAVE_BACKLOG_OK stories={manifest['stories']} tasks={manifest['tasks']} apply={args.apply}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
