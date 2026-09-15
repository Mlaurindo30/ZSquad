import json
import os
from pathlib import Path
import pytest
import yaml

from scripts.project_context import (
    ProjectContextError,
    _is_valid_runtime,
    resolve_runtime_root,
)


def _make_dummy_runtime(path: Path) -> Path:
    (path / "scripts").mkdir(parents=True, exist_ok=True)
    (path / "agents").mkdir(parents=True, exist_ok=True)
    return path


def test_is_valid_runtime(tmp_path):
    assert not _is_valid_runtime(tmp_path)
    (tmp_path / "scripts").mkdir()
    assert not _is_valid_runtime(tmp_path)
    (tmp_path / "agents").mkdir()
    assert _is_valid_runtime(tmp_path)


def test_resolve_runtime_via_env(tmp_path, monkeypatch):
    dummy = _make_dummy_runtime(tmp_path / "custom_runtime")
    monkeypatch.setenv("SQUAD_RUNTIME", str(dummy))
    resolved = resolve_runtime_root()
    assert resolved == dummy.resolve()


def test_resolve_runtime_via_project_marker(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUAD_RUNTIME", raising=False)
    dummy_runtime = _make_dummy_runtime(tmp_path / "my_runtime")
    project_dir = tmp_path / "consumer_project"
    marker_dir = project_dir / ".agents_squad" / "config"
    marker_dir.mkdir(parents=True)
    (marker_dir / "project.yaml").write_text(
        yaml.safe_dump({"version": 2, "project_id": "demo", "runtime": str(dummy_runtime)}),
        encoding="utf-8",
    )
    monkeypatch.chdir(project_dir)
    # Ensure tier 3 does not interfere
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "non_existent_home")

    resolved = resolve_runtime_root()
    assert resolved == dummy_runtime.resolve()


def test_resolve_runtime_via_global_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUAD_RUNTIME", raising=False)
    dummy_runtime = _make_dummy_runtime(tmp_path / "global_runtime")
    fake_home = tmp_path / "fake_home"
    reg_dir = fake_home / ".agents_squad" / "config"
    reg_dir.mkdir(parents=True)
    (reg_dir / "active_runtime.json").write_text(
        json.dumps({"runtime": str(dummy_runtime)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)

    resolved = resolve_runtime_root()
    assert resolved == dummy_runtime.resolve()


def test_resolve_runtime_via_self_relative(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUAD_RUNTIME", raising=False)
    fake_home = tmp_path / "fake_home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)

    # In our repository, scripts/project_context.py has parents[1] as the agent squad repo root
    resolved = resolve_runtime_root()
    assert (resolved / "scripts").is_dir()
    assert (resolved / "agents").is_dir()


def test_resolve_runtime_failure_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUAD_RUNTIME", raising=False)
    fake_home = tmp_path / "fake_home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)

    # Mock _is_valid_runtime to return False for everything
    monkeypatch.setattr("scripts.project_context._is_valid_runtime", lambda p: False)

    with pytest.raises(ProjectContextError, match="SQUAD_RUNTIME could not be resolved"):
        resolve_runtime_root()
