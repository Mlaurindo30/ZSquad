#!/usr/bin/env python3
"""
O que é: Suíte de cobertura (statements e branches) para scripts/squad_insights.py.
Responsabilidade: Verificar build_insights e o CLI contra bancos SQLite descartáveis com
o esquema EXATO copiado de local_agent_db.py._TABLE_DEFINITIONS.
Pra que serve: Provar zeros em banco vazio, agregações, filtro por projeto, falha suave
por tabela ausente e caminhos de sucesso/erro do CLI — sem nunca abrir banco/squad.db.
Comportamento em falha: Qualquer divergência de valores, chaves ou códigos de saída
reprova o teste correspondente.
Conexões: Importa scripts.squad_insights; usa pytest com tmp_path/capsys/monkeypatch.
Dependências & Imports: stdlib (runpy, sqlite3, sys, time) + pytest.
"""

import runpy
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import squad_insights
from scripts.squad_insights import (
    DEFAULT_DB_PATH,
    build_insights,
    format_report,
    main,
)

MODULE_PATH = Path(squad_insights.__file__)

# Esquema copiado VERBATIM de scripts/local_agent_db.py::_TABLE_DEFINITIONS
# (linhas 110-170). Nomes de colunas NÃO devem ser alterados neste arquivo.
TABLE_DDL = {
    "symbols": """(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        file_path TEXT NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        line_number INTEGER NOT NULL,
        docstring TEXT,
        complexity INTEGER DEFAULT 1,
        has_contract BOOLEAN DEFAULT 0,
        updated_at REAL NOT NULL,
        UNIQUE(project_id, file_path, name, kind)
    )""",
    "dependencies": """(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        source_file TEXT NOT NULL,
        target_module TEXT NOT NULL,
        target_symbol TEXT,
        kind TEXT NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(project_id, source_file, target_module, target_symbol, kind)
    )""",
    "token_metrics": """(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        work_item_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        step_name TEXT NOT NULL,
        prompt_tokens INTEGER NOT NULL,
        completion_tokens INTEGER NOT NULL,
        cost_usd REAL DEFAULT 0.0,
        recorded_at REAL NOT NULL
    )""",
    "trajectory_logs": """(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        benchmark_name TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        status TEXT NOT NULL,
        steps_count INTEGER NOT NULL,
        tool_calls_count INTEGER NOT NULL,
        duration_seconds REAL NOT NULL,
        details_json TEXT,
        recorded_at REAL NOT NULL
    )""",
    "quorum_votes": """(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        work_item_id TEXT NOT NULL,
        gate_id TEXT NOT NULL,
        voter_agent TEXT NOT NULL,
        vote TEXT NOT NULL,
        weight REAL DEFAULT 1.0,
        rationale TEXT,
        voted_at REAL NOT NULL,
        UNIQUE(project_id, work_item_id, gate_id, voter_agent)
    )""",
}

ALL_TABLES = ("symbols", "dependencies", "token_metrics", "trajectory_logs", "quorum_votes")


def make_db(tmp_path, tables=ALL_TABLES):
    """Cria um banco SQLite descartável com as tabelas pedidas; retorna o caminho."""
    db_path = tmp_path / "squad_test.db"
    conn = sqlite3.connect(str(db_path))
    for table in tables:
        conn.execute(f"CREATE TABLE {table} {TABLE_DDL[table]}")
    conn.commit()
    conn.close()
    return db_path


def add_token(conn, pid, wid, agent, step, prompt, completion, cost):
    conn.execute(
        "INSERT INTO token_metrics"
        " (project_id, work_item_id, agent_id, step_name,"
        "  prompt_tokens, completion_tokens, cost_usd, recorded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, wid, agent, step, prompt, completion, cost, time.time()),
    )


def add_trajectory(conn, pid, benchmark, agent, task, status):
    conn.execute(
        "INSERT INTO trajectory_logs"
        " (project_id, benchmark_name, agent_id, task_id, status, steps_count,"
        "  tool_calls_count, duration_seconds, details_json, recorded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, benchmark, agent, task, status, 1, 1, 1.0, "{}", time.time()),
    )


def add_vote(conn, pid, wid, gate, voter, vote):
    conn.execute(
        "INSERT INTO quorum_votes"
        " (project_id, work_item_id, gate_id, voter_agent, vote, weight, rationale, voted_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, wid, gate, voter, vote, 1.0, "", time.time()),
    )


def add_symbol(conn, pid, file_path, name, kind):
    conn.execute(
        "INSERT INTO symbols"
        " (project_id, file_path, name, kind, line_number, docstring, complexity,"
        "  has_contract, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, file_path, name, kind, 1, None, 1, 0, time.time()),
    )


def add_dependency(conn, pid, source_file, target_module, target_symbol, kind="import"):
    conn.execute(
        "INSERT INTO dependencies"
        " (project_id, source_file, target_module, target_symbol, kind, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (pid, source_file, target_module, target_symbol, kind, time.time()),
    )


def populate(conn):
    """Popula dois projetos com dados canônicos para todas as seções."""
    add_token(conn, "p1", "TASK-1", "agent-alpha", "planning", 100, 50, 0.01)
    add_token(conn, "p1", "TASK-1", "agent-alpha", "review", 200, 100, 0.02)
    add_token(conn, "p1", "TASK-2", "agent-beta", "build", 10, 5, 0.001)
    add_token(conn, "p2", "TASK-9", "agent-gamma", "build", 500, 250, 0.5)

    add_trajectory(conn, "p1", "bench-a", "agent-alpha", "TASK-1", "pass")
    add_trajectory(conn, "p1", "bench-b", "agent-alpha", "TASK-2", "pass")
    add_trajectory(conn, "p1", "bench-c", "agent-beta", "TASK-3", "fail")
    add_trajectory(conn, "p2", "bench-d", "agent-gamma", "TASK-9", "converged")

    add_vote(conn, "p1", "TASK-1", "G3-readiness", "voter-01", "approve")
    add_vote(conn, "p1", "TASK-1", "G3-readiness", "voter-02", "approve")
    add_vote(conn, "p1", "TASK-1", "G3-readiness", "voter-03", "reject")
    add_vote(conn, "p1", "TASK-2", "G4-code-security", "voter-01", "pass")
    add_vote(conn, "p2", "TASK-9", "G5-quality", "voter-07", "abstain")

    add_symbol(conn, "p1", "src/a.py", "func_a1", "function")
    add_symbol(conn, "p1", "src/a.py", "ClassA", "class")
    add_symbol(conn, "p1", "src/b.py", "func_b1", "function")
    add_symbol(conn, "p2", "src/c.py", "func_c1", "function")
    add_symbol(conn, "p2", "src/c.py", "ClassC", "class")
    add_symbol(conn, "p2", "src/c.py", "func_c2", "function")

    add_dependency(conn, "p1", "src/a.py", "os", None)
    add_dependency(conn, "p1", "src/b.py", "json", None)
    add_dependency(conn, "p1", "src/b.py", "sqlite3", "connect")
    add_dependency(conn, "p2", "src/c.py", "pathlib", "Path")


@pytest.fixture
def populated_db(tmp_path):
    db_path = make_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    populate(conn)
    conn.commit()
    conn.close()
    return db_path


def test_default_db_path_points_to_repo_bank():
    assert DEFAULT_DB_PATH == ROOT / "banco" / "squad.db"


def test_empty_database_aggregate_returns_zeros_with_by_project_empty(tmp_path):
    db_path = make_db(tmp_path)
    report = build_insights(db_path, project_id=None)
    tokens = report["tokens"]
    assert tokens["total_prompt_tokens"] == 0
    assert tokens["total_completion_tokens"] == 0
    assert tokens["total_cost_usd"] == 0.0
    assert tokens["by_agent"] == []
    assert tokens["by_work_item"] == []
    assert tokens["by_project"] == []
    trajectories = report["trajectories"]
    assert trajectories["by_status"] == {"pass": 0, "fail": 0, "converged": 0}
    assert trajectories["by_agent"] == []
    assert trajectories["by_project"] == []
    quorum = report["quorum"]
    assert quorum["by_vote"] == {"approve": 0, "reject": 0, "other": 0}
    assert quorum["by_gate"] == []
    assert quorum["by_project"] == []
    code_graph = report["code_graph"]
    assert code_graph["symbols_count"] == 0
    assert code_graph["dependencies_count"] == 0
    assert code_graph["top_files"] == []
    assert code_graph["by_project"] == []


def test_empty_database_project_filter_returns_zeros_without_by_project(tmp_path):
    db_path = make_db(tmp_path)
    report = build_insights(db_path, project_id="ghost")
    assert report["tokens"]["total_prompt_tokens"] == 0
    assert report["tokens"]["total_cost_usd"] == 0.0
    assert "by_project" not in report["tokens"]
    assert report["trajectories"]["by_status"] == {"pass": 0, "fail": 0, "converged": 0}
    assert "by_project" not in report["trajectories"]
    assert report["quorum"]["by_vote"] == {"approve": 0, "reject": 0, "other": 0}
    assert "by_project" not in report["quorum"]
    assert report["code_graph"]["symbols_count"] == 0
    assert "by_project" not in report["code_graph"]


def test_populated_aggregate_sections_with_by_project_breakdown(populated_db):
    report = build_insights(populated_db, project_id=None)

    tokens = report["tokens"]
    assert tokens["total_prompt_tokens"] == 810
    assert tokens["total_completion_tokens"] == 405
    assert tokens["total_cost_usd"] == pytest.approx(0.531)
    assert tokens["by_agent"] == [
        ("agent-gamma", 750),
        ("agent-alpha", 450),
        ("agent-beta", 15),
    ]
    assert tokens["by_work_item"] == [
        ("TASK-9", 750),
        ("TASK-1", 450),
        ("TASK-2", 15),
    ]
    by_project = tokens["by_project"]
    assert [e["project_id"] for e in by_project] == ["p1", "p2"]
    p1_tokens = by_project[0]
    assert p1_tokens["total_prompt_tokens"] == 310
    assert p1_tokens["total_completion_tokens"] == 155
    assert p1_tokens["total_cost_usd"] == pytest.approx(0.031)
    p2_tokens = by_project[1]
    assert p2_tokens["total_prompt_tokens"] == 500
    assert p2_tokens["total_completion_tokens"] == 250
    assert p2_tokens["total_cost_usd"] == pytest.approx(0.5)

    trajectories = report["trajectories"]
    assert trajectories["by_status"] == {"pass": 2, "fail": 1, "converged": 1}
    assert trajectories["by_agent"] == [
        ("agent-alpha", 2),
        ("agent-beta", 1),
        ("agent-gamma", 1),
    ]
    assert trajectories["by_project"] == [
        {"project_id": "p1", "runs": 3},
        {"project_id": "p2", "runs": 1},
    ]

    quorum = report["quorum"]
    assert quorum["by_vote"] == {"approve": 2, "reject": 1, "other": 2}
    assert quorum["by_gate"] == [
        ("G3-readiness", 3),
        ("G4-code-security", 1),
        ("G5-quality", 1),
    ]
    assert quorum["by_project"] == [
        {"project_id": "p1", "votes": 4},
        {"project_id": "p2", "votes": 1},
    ]

    code_graph = report["code_graph"]
    assert code_graph["symbols_count"] == 6
    assert code_graph["dependencies_count"] == 4
    assert code_graph["top_files"] == [
        ("src/c.py", 3),
        ("src/a.py", 2),
        ("src/b.py", 1),
    ]
    assert code_graph["by_project"] == [
        {"project_id": "p1", "symbols_count": 3, "dependencies_count": 3},
        {"project_id": "p2", "symbols_count": 3, "dependencies_count": 1},
    ]


def test_populated_project_filter_sections_without_by_project(populated_db):
    report = build_insights(populated_db, project_id="p1")

    tokens = report["tokens"]
    assert tokens["total_prompt_tokens"] == 310
    assert tokens["total_completion_tokens"] == 155
    assert tokens["total_cost_usd"] == pytest.approx(0.031)
    assert tokens["by_agent"] == [("agent-alpha", 450), ("agent-beta", 15)]
    assert tokens["by_work_item"] == [("TASK-1", 450), ("TASK-2", 15)]
    for section in report.values():
        assert "by_project" not in section

    assert report["trajectories"]["by_status"] == {"pass": 2, "fail": 1, "converged": 0}
    assert report["trajectories"]["by_agent"] == [("agent-alpha", 2), ("agent-beta", 1)]

    assert report["quorum"]["by_vote"] == {"approve": 2, "reject": 1, "other": 1}
    assert report["quorum"]["by_gate"] == [("G3-readiness", 3), ("G4-code-security", 1)]

    assert report["code_graph"]["symbols_count"] == 3
    assert report["code_graph"]["dependencies_count"] == 3
    assert report["code_graph"]["top_files"] == [("src/a.py", 2), ("src/b.py", 1)]


def test_token_top_lists_truncate_at_ten(tmp_path):
    db_path = make_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    for i in range(12):
        add_token(conn, "p1", f"US-{i}", f"agent-{i:02d}", "step", 10, 1, 0.0)
    conn.commit()
    conn.close()
    report = build_insights(db_path, project_id="p1")
    assert len(report["tokens"]["by_agent"]) == 10
    assert report["tokens"]["by_agent"][0] == ("agent-00", 11)
    assert "agent-10" not in [agent for agent, _ in report["tokens"]["by_agent"]]


def test_missing_all_tables_fail_soft_per_section(tmp_path):
    db_path = make_db(tmp_path, tables=())
    report = build_insights(db_path, project_id=None)
    for name in ("tokens", "trajectories", "quorum", "code_graph"):
        assert report[name] == {"error": "table-not-found"}


def test_partial_tables_fail_soft_only_for_missing(tmp_path):
    db_path = make_db(tmp_path, tables=("token_metrics",))
    conn = sqlite3.connect(str(db_path))
    add_token(conn, "p1", "TASK-1", "agent-alpha", "planning", 10, 5, 0.01)
    conn.commit()
    conn.close()
    report = build_insights(db_path, project_id="p1")
    assert report["tokens"]["total_prompt_tokens"] == 10
    assert "error" not in report["tokens"]
    assert report["trajectories"] == {"error": "table-not-found"}
    assert report["quorum"] == {"error": "table-not-found"}
    assert report["code_graph"] == {"error": "table-not-found"}


def test_unrelated_operational_error_propagates(tmp_path):
    db_path = tmp_path / "wrong_schema.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE token_metrics (foo INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.OperationalError) as excinfo:
        build_insights(db_path, project_id=None)
    assert "no such column" in str(excinfo.value).lower()


def test_format_report_lines_shape(populated_db):
    lines = format_report(build_insights(populated_db, project_id=None))
    assert lines[0] == "INSIGHTS_OK"
    joined = "\n".join(lines)
    assert "tokens.total_prompt_tokens=810" in joined
    assert "tokens.by_agent=agent-gamma:750|agent-alpha:450|agent-beta:15" in joined
    assert "tokens.by_project.p1.total_cost_usd=0.031" in joined
    assert "trajectories.by_status=converged:1|fail:1|pass:2" in joined
    assert "quorum.by_vote=approve:2|other:2|reject:1" in joined
    assert "code_graph.top_files=src/c.py:3|src/a.py:2|src/b.py:1" in joined


def test_cli_success_prints_insights_ok(populated_db, capsys):
    exit_code = main(["--db", str(populated_db)])
    captured = capsys.readouterr()
    assert exit_code == 0
    output_lines = captured.out.splitlines()
    assert output_lines[0] == "INSIGHTS_OK"
    assert any(line.startswith("code_graph.symbols_count=") for line in output_lines)
    assert any(line.startswith("quorum.by_gate=") for line in output_lines)


def test_cli_project_filter_omits_by_project_lines(populated_db, capsys):
    exit_code = main(["--db", str(populated_db), "--project", "p1"])
    captured = capsys.readouterr()
    assert exit_code == 0
    out = captured.out
    assert "tokens.total_prompt_tokens=310" in out
    assert ".by_project." not in out


def test_cli_empty_database_ok_with_zero_lines(tmp_path, capsys):
    db_path = make_db(tmp_path)
    exit_code = main(["--db", str(db_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    out = captured.out
    assert "INSIGHTS_OK" in out
    assert "tokens.total_prompt_tokens=0" in out
    assert "tokens.by_agent=\n" in out
    assert "trajectories.by_status=converged:0|fail:0|pass:0" in out
    assert "quorum.by_vote=approve:0|other:0|reject:0" in out
    assert "code_graph.symbols_count=0" in out


def test_cli_renders_error_line_for_missing_table_section(tmp_path, capsys):
    db_path = make_db(tmp_path, tables=("token_metrics", "trajectory_logs"))
    exit_code = main(["--db", str(db_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    out = captured.out
    assert "INSIGHTS_OK" in out
    assert "quorum.error=table-not-found" in out
    assert "code_graph.error=table-not-found" in out
    assert "tokens.total_prompt_tokens=0" in out


def test_cli_error_exit_code_and_message_on_missing_db(tmp_path, capsys):
    missing = tmp_path / "nao_existe.db"
    exit_code = main(["--db", str(missing)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out.startswith("INSIGHTS_ERROR:")


def test_main_guard_via_runpy_success(populated_db, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["squad_insights.py", "--db", str(populated_db)])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(MODULE_PATH), run_name="__main__")
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.startswith("INSIGHTS_OK")


def test_main_guard_via_runpy_error(tmp_path, capsys, monkeypatch):
    missing = tmp_path / "nao_existe.db"
    monkeypatch.setattr(sys, "argv", ["squad_insights.py", "--db", str(missing)])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(MODULE_PATH), run_name="__main__")
    assert excinfo.value.code == 1
    assert capsys.readouterr().out.startswith("INSIGHTS_ERROR:")
