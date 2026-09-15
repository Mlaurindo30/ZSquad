"""Cobertura determinística de scripts/build_provider_prompts.py.

Cria um repositório temporário com 4 prompts sintéticos, ajusta limites
via ``--max-claude-codex-overlap`` e verifica que ``main()`` detecta
estouros e sobreposição.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_provider_prompts.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_provider_prompts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _setup_fake_root(tmp_path: Path, *, agents: str, claude: str, codex: str, gemini: str) -> Path:
    (tmp_path / "AGENTS.md").write_text(agents, encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text(claude, encoding="utf-8")
    (tmp_path / "CODEX.md").write_text(codex, encoding="utf-8")
    (tmp_path / "GEMINI.md").write_text(gemini, encoding="utf-8")
    return tmp_path


def test_check_sizes_detects_oversize(tmp_path, monkeypatch):
    root = _setup_fake_root(tmp_path, agents="a", claude="c", codex="x", gemini="g")
    mod = _load()
    monkeypatch.setattr(mod, "ROOT", root)
    issues = mod._check_sizes()
    assert any("OVERSIZE" in i or "MISSING" in i for i in issues) or issues == []


def test_check_overlap_returns_ratio(tmp_path, monkeypatch):
    common = "alpha bravo charlie delta echo foxtrot golf hotel india"
    root = _setup_fake_root(
        tmp_path,
        agents=common,
        claude=common + " extra",
        codex=common + " more",
        gemini=common,
    )
    mod = _load()
    monkeypatch.setattr(mod, "ROOT", root)
    issues, ratio = mod._check_overlap()
    assert 0.7 < ratio < 1.0


def test_main_returns_zero_when_within_limits(tmp_path, monkeypatch):
    root = _setup_fake_root(
        tmp_path,
        agents="short agent prompt",
        claude="claude only different content here " * 5,
        codex="codex only different content here " * 5,
        gemini="gemini short",
    )
    mod = _load()
    monkeypatch.setattr(mod, "ROOT", root)
    rc = mod.main(["--max-claude-codex-overlap", "1.0"])
    assert rc == 0


def test_main_returns_one_when_size_exceeds(tmp_path, monkeypatch, capsys):
    big = "x" * 13_000
    root = _setup_fake_root(tmp_path, agents=big, claude="c", codex="x", gemini="g")
    mod = _load()
    monkeypatch.setattr(mod, "ROOT", root)
    rc = mod.main(["--max-claude-codex-overlap", "1.0"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "OVERSIZE" in out or "PROVIDER_PROMPTS_FAIL" in out


def test_main_writes_report_when_flag_present(tmp_path, monkeypatch, capsys):
    root = _setup_fake_root(
        tmp_path,
        agents="minimal",
        claude="claude payload",
        codex="codex payload",
        gemini="gemini payload",
    )
    (root / "docs").mkdir(exist_ok=True)
    mod = _load()
    monkeypatch.setattr(mod, "ROOT", root)
    rc = mod.main(["--write", "--max-claude-codex-overlap", "1.0"])
    report = json.loads((root / "docs" / "provider-prompts-hashes.json").read_text())
    assert report["hashes"]["AGENTS.md"]


def test_hash_determinism():
    mod = _load()
    h1 = mod._hash(ROOT / "AGENTS.md")
    h2 = mod._hash(ROOT / "AGENTS.md")
    assert h1 == h2
    assert len(h1) == 64
