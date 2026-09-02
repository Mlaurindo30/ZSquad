"""Cobertura determinística de scripts/sql_safety_lint.py.

Não lê disco de produção: cria diretório temporário com arquivos sintéticos
e verifica que o linter aponta somente o que merece apontar.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINTER = ROOT / "scripts" / "sql_safety_lint.py"


def _load_linter():
    spec = importlib.util.spec_from_file_location("sql_safety_lint", LINTER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    markers = {
        "markers": [
            {"label": "f-string", "marker": "f'"},
            {"label": "f-string", "marker": 'f"'},
            {"label": ".format()", "marker": ".format("},
        ]
    }
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sql_safety_markers.json").write_text(json.dumps(markers), encoding="utf-8")
    src = tmp_path / "scripts"
    src.mkdir()
    (src / "safe.py").write_text(
        "import sqlite3\n"
        "def f():\n"
        "    conn = sqlite3.connect(':memory:')\n"
        "    conn.execute(\"SELECT * FROM t WHERE id = ?\", (1,))\n",
        encoding="utf-8",
    )
    (src / "unsafe.py").write_text(
        "import sqlite3\n"
        "def g(x):\n"
        "    conn = sqlite3.connect(':memory:')\n"
        "    conn.execute(f\"SELECT * FROM t WHERE id = {x}\")\n",
        encoding="utf-8",
    )
    return tmp_path


def test_linter_flags_only_unsafe(capsys, workspace):
    linter = _load_linter()
    rc = linter.main(["--target", str(workspace / "scripts")], root=workspace)
    out = capsys.readouterr().out
    assert rc == 1
    assert "unsafe.py" in out
    assert "safe.py" in out or "f-string" in out


def test_linter_returns_zero_when_only_safe_files(capsys, workspace):
    safe_only = workspace / "scripts" / "safe_only"
    safe_only.mkdir()
    (safe_only / "ok.py").write_text(
        'import sqlite3\ndef f():\n    sqlite3.connect(":memory:").execute("SELECT 1")\n',
        encoding="utf-8",
    )
    linter = _load_linter()
    rc = linter.main(["--target", str(safe_only)], root=workspace)
    out = capsys.readouterr().out
    assert rc == 0
    assert "SQL_SAFETY_OK" in out


def test_linter_handles_multiline_call(capsys, workspace):
    src = workspace / "scripts" / "multi.py"
    src.write_text(
        "import sqlite3\n"
        "def h(x):\n"
        "    conn = sqlite3.connect(':memory:')\n"
        "    conn.execute(\n"
        "        f\"SELECT * FROM users WHERE name = {x}\"\n"
        "    )\n",
        encoding="utf-8",
    )
    linter = _load_linter()
    rc = linter.main(["--target", str(src.parent)], root=workspace)
    out = capsys.readouterr().out
    assert rc == 1
    assert "multi.py" in out


def test_linter_detects_double_quote_fstring(capsys, workspace):
    src = workspace / "scripts" / "dq.py"
    src.write_text(
        "import sqlite3\n"
        "def f(x):\n"
        "    conn = sqlite3.connect(':memory:')\n"
        "    conn.execute(f\"SELECT * FROM t WHERE id = {x}\")\n",
        encoding="utf-8",
    )
    linter = _load_linter()
    rc = linter.main(["--target", str(src.parent)], root=workspace)
    out = capsys.readouterr().out
    assert rc == 1
    assert "dq.py" in out
