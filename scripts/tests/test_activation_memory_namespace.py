"""O pacote de ativação deve anunciar memória no namespace do projeto consumidor."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_squad import AgentSquad


def _link_or_copy(src: Path, dst: Path) -> None:
    if not src.exists() or dst.exists():
        return
    if sys.platform == "win32":
        try:
            import _winapi

            _winapi.CreateJunction(str(src), str(dst))
            return
        except Exception:
            pass
    try:
        os.symlink(src, dst, target_is_directory=src.is_dir())
    except Exception:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def make_authorized_runtime(tmp_path: Path, project_id: str | None = None) -> tuple[AgentSquad, Path]:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for name in ("agents", "config", "contracts", "templates", "skills"):
        _link_or_copy(ROOT / name, runtime_dir / name)
    if project_id:
        work_dir = runtime_dir / "work" / project_id
    else:
        work_dir = runtime_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    squad = AgentSquad(runtime_dir, project_name=project_id, allow_legacy=project_id is None)
    return squad, work_dir


def _activated_packet(tmp_path: Path, project_name: str | None, work_id: str) -> dict:
    squad, work_dir = make_authorized_runtime(tmp_path, project_name)
    item = squad.init_work_item(work_id, "low", base=work_dir)
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
