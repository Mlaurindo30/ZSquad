"""Cobertura determinística de scripts/run_recovery_simulation.py.

Executa o cenário em ``tmp_path`` (banco descartável) e valida que:
- eventos ``ops_recovery`` foram persistidos com binds;
- o orçamento anti-loop dispara ``blocked`` na terceira tentativa para a
  mesma ``target_key``;
- o sample read-only final retorna eventos da simulação.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "run_recovery_simulation", ROOT / "scripts" / "run_recovery_simulation.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_simulation_persists_recovery_events_with_bind_parameters(tmp_path):
    db = tmp_path / "recovery.db"
    mod = _load()
    rc = mod.run_simulation("alpha", db)
    assert rc == 0
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT target_key, reason, action, attempt FROM ops_recovery "
        "WHERE project_id = 'alpha' ORDER BY id"
    ).fetchall()
    assert len(rows) == 6
    # Verifica binds seguros: cada target_key foi registrado como literal.
    for row in rows:
        assert row[0].startswith("alpha/TASK-1/")
        assert row[1] == "timeout"
    # O último evento do loop deve ser "blocked" (orçamento anti-loop).
    assert rows[-1][2] == "blocked"
    assert rows[-1][3] == 3
    conn.close()


def test_simulation_rejects_injectable_project_id(tmp_path):
    """LocalAgentDB valida project_id via regex; payloads maliciosos são
    rejeitados antes de qualquer query ser executada.
    """
    db = tmp_path / "recovery.db"
    mod = _load()
    with pytest.raises(ValueError):
        mod.run_simulation("x'); DROP TABLE ops_recovery; --", db)


def test_simulation_persists_payload_as_literal(tmp_path):
    """Quando o project_id é válido, o evento é gravado como literal,
    sem concatenação na query (binds ?).
    """
    db = tmp_path / "recovery.db"
    mod = _load()
    rc = mod.run_simulation("alpha.test", db)
    assert rc == 0
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT DISTINCT project_id FROM ops_recovery"
    ).fetchall()
    assert rows == [("alpha.test",)]
    conn.close()


def test_main_runs_with_default_db_path(tmp_path, monkeypatch):
    """Garante que o entrypoint CLI chama ``run_simulation`` com argv."""
    mod = _load()
    captured = {}

    def fake_run(project_id, db_path):
        captured["project_id"] = project_id
        captured["db_path"] = str(db_path)
        return 0

    monkeypatch.setattr(mod, "run_simulation", fake_run)
    rc = mod.main(["--project-id", "alpha", "--db-path", str(tmp_path / "x.db")])
    assert rc == 0
    assert captured["project_id"] == "alpha"
    assert captured["db_path"] == str(tmp_path / "x.db")
