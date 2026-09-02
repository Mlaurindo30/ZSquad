#!/usr/bin/env python3
"""
O que é: Motor de insights sobre o banco central do squad (SQLite em ``banco/squad.db``),
conceito portado de ``hermes-agent/agent/insights.py`` para o esquema real de
``scripts/local_agent_db.py`` (_TABLE_DEFINITIONS).
Responsabilidade: Agregar consumo de tokens e custo estimado, padrões por agente e por
work item, resultados de trajetórias, distribuição de votos de quórum e tamanho do grafo
de código, com filtro opcional por ``project_id`` (None agrega todos os projetos e anexa
quebra ``by_project`` em cada seção).
Pra que serve: Dar ao coordenador visibilidade quantitativa de custo, desempenho e
governança dos work items sem abrir o banco manualmente.
Comportamento em falha: Tabela ausente => a seção vira ``{"error": "table-not-found"}``
(falha suave por tabela, nunca derruba o relatório inteiro); erro de SQLite/OSError no
CLI imprime ``INSIGHTS_ERROR: <msg>`` e sai com código 1; sucesso imprime
``INSIGHTS_OK`` seguido de linhas compactas chave=valor e sai com código 0.
Conexões: Lê o esquema real definido em ``scripts/local_agent_db.py``
(``_TABLE_DEFINITIONS``); consumido por ``scripts/tests/test_squad_insights_coverage.py``
e pelo operador via linha de comando.
Dependências & Imports: apenas stdlib — argparse, sqlite3, sys, pathlib.Path.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "banco" / "squad.db"

_TOP_LIMIT = 10
_PROJECT_FILTER = "WHERE (? IS NULL OR project_id = ?)"

_TRAJECTORY_STATUS_SEED = ("pass", "fail", "converged")
_QUORUM_BUCKETS = ("approve", "reject", "other")


class _TableMissing(Exception):
    """Sinaliza tabela ausente no banco para ativar a falha suave da seção."""


def _open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Abre o banco em modo somente leitura (URI) com linhas como sqlite3.Row."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Executa a consulta e retorna todas as linhas; tabela ausente virá _TableMissing."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            raise _TableMissing(str(exc)) from exc
        raise


def _row(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row:
    """Executa agregação escalar (sem GROUP BY) que produz exatamente uma linha."""
    return _rows(conn, sql, params)[0]


def _tokens_section(conn: sqlite3.Connection, project_id: str | None) -> dict:
    """Agrega tokens e custo de token_metrics: totais, top agentes e top work items."""
    params = (project_id, project_id)
    try:
        totals = _row(conn, f"""
            SELECT COALESCE(SUM(prompt_tokens), 0) AS total_prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS total_completion_tokens,
                   ROUND(COALESCE(SUM(cost_usd), 0.0), 4) AS total_cost_usd
            FROM token_metrics {_PROJECT_FILTER}
        """, params)
        by_agent = _rows(conn, f"""
            SELECT agent_id, SUM(prompt_tokens + completion_tokens) AS total_tokens
            FROM token_metrics {_PROJECT_FILTER}
            GROUP BY agent_id
            ORDER BY total_tokens DESC, agent_id ASC
            LIMIT {_TOP_LIMIT}
        """, params)
        by_work_item = _rows(conn, f"""
            SELECT work_item_id, SUM(prompt_tokens + completion_tokens) AS total_tokens
            FROM token_metrics {_PROJECT_FILTER}
            GROUP BY work_item_id
            ORDER BY total_tokens DESC, work_item_id ASC
            LIMIT {_TOP_LIMIT}
        """, params)
        section = {
            "total_prompt_tokens": totals["total_prompt_tokens"],
            "total_completion_tokens": totals["total_completion_tokens"],
            "total_cost_usd": totals["total_cost_usd"],
            "by_agent": [(r["agent_id"], r["total_tokens"]) for r in by_agent],
            "by_work_item": [
                (r["work_item_id"], r["total_tokens"]) for r in by_work_item
            ],
        }
    except _TableMissing:
        return {"error": "table-not-found"}
    if project_id is None:
        section["by_project"] = [
            dict(r) for r in _rows(conn, f"""
                SELECT project_id,
                       COALESCE(SUM(prompt_tokens), 0) AS total_prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS total_completion_tokens,
                       ROUND(COALESCE(SUM(cost_usd), 0.0), 4) AS total_cost_usd
                FROM token_metrics {_PROJECT_FILTER}
                GROUP BY project_id
                ORDER BY project_id ASC
            """, params)
        ]
    return section


def _trajectories_section(conn: sqlite3.Connection, project_id: str | None) -> dict:
    """Agrega trajectory_logs por status (com zeros semeados) e por agente."""
    params = (project_id, project_id)
    try:
        by_status_rows = _rows(conn, f"""
            SELECT status, COUNT(*) AS runs
            FROM trajectory_logs {_PROJECT_FILTER}
            GROUP BY status
            ORDER BY status ASC
        """, params)
        by_agent_rows = _rows(conn, f"""
            SELECT agent_id, COUNT(*) AS runs
            FROM trajectory_logs {_PROJECT_FILTER}
            GROUP BY agent_id
            ORDER BY runs DESC, agent_id ASC
        """, params)
    except _TableMissing:
        return {"error": "table-not-found"}
    by_status = {status: 0 for status in _TRAJECTORY_STATUS_SEED}
    for r in by_status_rows:
        by_status[r["status"]] = r["runs"]
    section = {
        "by_status": by_status,
        "by_agent": [(r["agent_id"], r["runs"]) for r in by_agent_rows],
    }
    if project_id is None:
        section["by_project"] = [
            dict(r) for r in _rows(conn, f"""
                SELECT project_id, COUNT(*) AS runs
                FROM trajectory_logs {_PROJECT_FILTER}
                GROUP BY project_id
                ORDER BY project_id ASC
            """, params)
        ]
    return section


def _quorum_section(conn: sqlite3.Connection, project_id: str | None) -> dict:
    """Agrega quorum_votes nos buckets approve/reject/other e o top de gates."""
    params = (project_id, project_id)
    try:
        by_vote_rows = _rows(conn, f"""
            SELECT CASE WHEN vote = 'approve' THEN 'approve'
                        WHEN vote = 'reject' THEN 'reject'
                        ELSE 'other' END AS bucket,
                   COUNT(*) AS votes
            FROM quorum_votes {_PROJECT_FILTER}
            GROUP BY bucket
        """, params)
        by_gate_rows = _rows(conn, f"""
            SELECT gate_id, COUNT(*) AS votes
            FROM quorum_votes {_PROJECT_FILTER}
            GROUP BY gate_id
            ORDER BY votes DESC, gate_id ASC
            LIMIT {_TOP_LIMIT}
        """, params)
    except _TableMissing:
        return {"error": "table-not-found"}
    by_vote = {bucket: 0 for bucket in _QUORUM_BUCKETS}
    for r in by_vote_rows:
        by_vote[r["bucket"]] = r["votes"]
    section = {
        "by_vote": by_vote,
        "by_gate": [(r["gate_id"], r["votes"]) for r in by_gate_rows],
    }
    if project_id is None:
        section["by_project"] = [
            dict(r) for r in _rows(conn, f"""
                SELECT project_id, COUNT(*) AS votes
                FROM quorum_votes {_PROJECT_FILTER}
                GROUP BY project_id
                ORDER BY project_id ASC
            """, params)
        ]
    return section


def _code_graph_section(conn: sqlite3.Connection, project_id: str | None) -> dict:
    """Mede o grafo de código: contagem de símbolos, dependências e arquivos líderes."""
    params = (project_id, project_id)
    try:
        symbols_row = _row(conn, f"SELECT COUNT(*) AS c FROM symbols {_PROJECT_FILTER}", params)
        dependencies_row = _row(
            conn, f"SELECT COUNT(*) AS c FROM dependencies {_PROJECT_FILTER}", params
        )
        top_file_rows = _rows(conn, f"""
            SELECT file_path, COUNT(*) AS symbol_count
            FROM symbols {_PROJECT_FILTER}
            GROUP BY file_path
            ORDER BY symbol_count DESC, file_path ASC
            LIMIT {_TOP_LIMIT}
        """, params)
    except _TableMissing:
        return {"error": "table-not-found"}
    section = {
        "symbols_count": symbols_row["c"],
        "dependencies_count": dependencies_row["c"],
        "top_files": [(r["file_path"], r["symbol_count"]) for r in top_file_rows],
    }
    if project_id is None:
        symbols_by_project = {
            r["project_id"]: r["c"]
            for r in _rows(conn, f"""
                SELECT project_id, COUNT(*) AS c
                FROM symbols {_PROJECT_FILTER}
                GROUP BY project_id
            """, params)
        }
        dependencies_by_project = {
            r["project_id"]: r["c"]
            for r in _rows(conn, f"""
                SELECT project_id, COUNT(*) AS c
                FROM dependencies {_PROJECT_FILTER}
                GROUP BY project_id
            """, params)
        }
        section["by_project"] = [
            {
                "project_id": pid,
                "symbols_count": symbols_by_project.get(pid, 0),
                "dependencies_count": dependencies_by_project.get(pid, 0),
            }
            for pid in sorted(set(symbols_by_project) | set(dependencies_by_project))
        ]
    return section


def build_insights(db_path: str | Path, project_id: str | None = None) -> dict:
    """Constrói o relatório completo de insights; None agrega todos os projetos."""
    conn = _open_readonly(db_path)
    try:
        return {
            "tokens": _tokens_section(conn, project_id),
            "trajectories": _trajectories_section(conn, project_id),
            "quorum": _quorum_section(conn, project_id),
            "code_graph": _code_graph_section(conn, project_id),
        }
    finally:
        conn.close()


def _format_pairs(pairs: list[tuple]) -> str:
    """Serializa uma lista de pares nome:valor separada por barra vertical."""
    return "|".join(f"{name}:{value}" for name, value in pairs)


def _format_by_project_lines(section_name: str, entries: list[dict]) -> list[str]:
    """Emite uma linha chave=valor por métrica de cada projeto no modo agregado."""
    lines = []
    for entry in entries:
        pid = entry["project_id"]
        for key in sorted(entry):
            if key == "project_id":
                continue
            lines.append(f"{section_name}.by_project.{pid}.{key}={entry[key]}")
    return lines


def _format_tokens(section: dict) -> list[str]:
    """Linhas compactas da seção de tokens."""
    lines = [
        f"tokens.total_prompt_tokens={section['total_prompt_tokens']}",
        f"tokens.total_completion_tokens={section['total_completion_tokens']}",
        f"tokens.total_cost_usd={section['total_cost_usd']}",
        f"tokens.by_agent={_format_pairs(section['by_agent'])}",
        f"tokens.by_work_item={_format_pairs(section['by_work_item'])}",
    ]
    lines.extend(_format_by_project_lines("tokens", section.get("by_project", [])))
    return lines


def _format_trajectories(section: dict) -> list[str]:
    """Linhas compactas da seção de trajetórias."""
    lines = [
        f"trajectories.by_status={_format_pairs(sorted(section['by_status'].items()))}",
        f"trajectories.by_agent={_format_pairs(section['by_agent'])}",
    ]
    lines.extend(_format_by_project_lines("trajectories", section.get("by_project", [])))
    return lines


def _format_quorum(section: dict) -> list[str]:
    """Linhas compactas da seção de quórum."""
    lines = [
        f"quorum.by_vote={_format_pairs(sorted(section['by_vote'].items()))}",
        f"quorum.by_gate={_format_pairs(section['by_gate'])}",
    ]
    lines.extend(_format_by_project_lines("quorum", section.get("by_project", [])))
    return lines


def _format_code_graph(section: dict) -> list[str]:
    """Linhas compactas da seção do grafo de código."""
    lines = [
        f"code_graph.symbols_count={section['symbols_count']}",
        f"code_graph.dependencies_count={section['dependencies_count']}",
        f"code_graph.top_files={_format_pairs(section['top_files'])}",
    ]
    lines.extend(_format_by_project_lines("code_graph", section.get("by_project", [])))
    return lines


_SECTION_FORMATTERS = {
    "tokens": _format_tokens,
    "trajectories": _format_trajectories,
    "quorum": _format_quorum,
    "code_graph": _format_code_graph,
}


def format_report(report: dict) -> list[str]:
    """Formata o relatório como linhas texto com cabeçalho INSIGHTS_OK."""
    lines = ["INSIGHTS_OK"]
    for name in ("tokens", "trajectories", "quorum", "code_graph"):
        section = report[name]
        if "error" in section:
            lines.append(f"{name}.error={section['error']}")
            continue
        lines.extend(_SECTION_FORMATTERS[name](section))
    return lines


def main(argv: list[str] | None = None) -> int:
    """CLI: imprime INSIGHTS_OK + linhas chave=valor (exit 0) ou INSIGHTS_ERROR (exit 1)."""
    parser = argparse.ArgumentParser(
        prog="squad_insights",
        description="Insights sobre o banco central do squad (somente leitura).",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="caminho do squad.db")
    parser.add_argument("--project", default=None, help="filtra por project_id")
    args = parser.parse_args(argv)
    try:
        report = build_insights(args.db, args.project)
    except (sqlite3.Error, OSError) as exc:
        print(f"INSIGHTS_ERROR: {exc}")
        return 1
    print("\n".join(format_report(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
