"""O pacote de ativação deve anunciar memória no namespace do projeto consumidor."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_squad import AgentSquad


def _activated_packet(tmp_path: Path, project_name: str | None, work_id: str) -> dict:
    squad = AgentSquad(ROOT, project_name=project_name)
    squad._work_base = lambda: tmp_path  # noqa: SLF001 - isola o base fora do runtime real
    item = squad.init_work_item(work_id, "low", base=tmp_path)
    return squad.activation_packet("software-engineer", item=item)


def test_activation_memory_paths_include_project_namespace(tmp_path: Path):
    packet = _activated_packet(tmp_path, "consumer-a", "TASK-MEM-NS-001")
    assert packet["work_item"] == "TASK-MEM-NS-001"
    for path in packet["memory"].values():
        assert path.startswith("work/consumer-a/TASK-MEM-NS-001/memory/"), path


def test_activation_memory_paths_legacy_without_project(tmp_path: Path):
    packet = _activated_packet(tmp_path, None, "TASK-MEM-NS-002")
    assert packet["work_item"] == "TASK-MEM-NS-002"
    for path in packet["memory"].values():
        assert path.startswith("work/TASK-MEM-NS-002/memory/"), path
