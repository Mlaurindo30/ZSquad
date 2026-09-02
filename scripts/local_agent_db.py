#!/usr/bin/env python3
"""
O que é: Camada de banco de dados local embedded (SQLite) de alta performance para o Agents Squad.
Responsabilidade: Armazenar e consultar grafos de símbolos AST, dependências, blast radius, métricas de tokens e histórico de trajetórias de agentes.
Pra que serve: Prover inteligência de código em sub-milissegundos (L1/L2) para os agentes, calculando impacto de mudanças e consumo de tokens sem latência de rede.
Comportamento em falha: Trata erros de I/O e SQL com transações atômicas seguras (WAL mode) e fallback para consultas estáticas.
Conexões: Utilizado por agent_squad.py, evaluate_agent_trajectories.py, sync_mcp_servers.py e linters de Clean Code.
Dependências & Imports:
  - sqlite3: Banco de dados relacional embedded embutido na standard library.
  - ast: Parser sintático de código Python para extração determinística de grafos.
  - json, pathlib, hashlib, time: Utilitários padrão de sistema e tipos.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple


class SymbolInfo(NamedTuple):
    """Representa um símbolo (classe, função ou módulo) extraído via AST."""
    name: str
    kind: str  # 'function', 'class', 'module'
    file_path: str
    line_number: int
    docstring: str
    complexity: int
    has_component_contract: bool


class CodeHealthBiomarkers(NamedTuple):
    """Métricas determinísticas de saúde de código inspiradas no Repowise."""
    file_path: str
    total_lines: int
    code_lines: int
    comment_lines: int
    symbol_count: int
    max_complexity: int
    avg_complexity: float
    docstring_coverage: float
    contract_compliant: bool


class LocalAgentDB:
    """Gerenciador de persistência local namespaced por projeto."""

    LEGACY_PROJECT_ID = "legacy"
    _PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

    def __init__(
        self,
        db_path: Path | str | None = None,
        project_id: str | None = None,
        *,
        allow_legacy: bool = False,
    ):
        """Inicializa o SQLite central para um projeto explicitamente identificado.

        Args:
            db_path: Caminho do arquivo SQLite. Se None, usa ``<runtime>/banco/squad.db``.
            project_id: Namespace obrigatório do projeto para todas as operações.
            allow_legacy: Migra e acessa registros antigos sob o namespace ``legacy``.
        """
        if project_id is None:
            project_id = os.environ.get("SQUAD_PROJECT_ID")
        if project_id is None and allow_legacy:
            project_id = self.LEGACY_PROJECT_ID
        if not project_id:
            raise ValueError("project_id é obrigatório; use allow_legacy=True apenas para dados legados")
        if not self._PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError(f"project_id inválido: {project_id!r}")

        self.project_id = project_id
        if db_path is None:
            env_path = os.environ.get("SQUAD_DB_PATH")
            if env_path:
                self.db_path = Path(env_path)
            else:
                root = Path(__file__).resolve().parents[1]
                banco_dir = root / "banco"
                banco_dir.mkdir(parents=True, exist_ok=True)
                self.db_path = banco_dir / "squad.db"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    @contextmanager
    def _connection(self):
        """Gerencia o ciclo de vida e fechamento atômico da conexão SQLite."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    _TABLE_DEFINITIONS = {
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

    def _init_db(self) -> None:
        """Cria ou migra atomicamente o esquema central namespaced."""
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            for table, definition in self._TABLE_DEFINITIONS.items():
                if table not in existing:
                    conn.execute(f"CREATE TABLE {table} {definition}")
                    continue
                columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                if "project_id" not in columns:
                    self._migrate_legacy_table(conn, table, definition, columns)

            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_symbols_project_file ON symbols(project_id, file_path);
                CREATE INDEX IF NOT EXISTS idx_symbols_project_name ON symbols(project_id, name);
                CREATE INDEX IF NOT EXISTS idx_deps_project_source ON dependencies(project_id, source_file);
                CREATE INDEX IF NOT EXISTS idx_deps_project_target ON dependencies(project_id, target_module);
                CREATE INDEX IF NOT EXISTS idx_tokens_project_work_item ON token_metrics(project_id, work_item_id);
                CREATE INDEX IF NOT EXISTS idx_trajectory_project_task ON trajectory_logs(project_id, task_id);
                CREATE INDEX IF NOT EXISTS idx_quorum_project_work_item ON quorum_votes(project_id, work_item_id, gate_id);
            """)

    def _migrate_legacy_table(
        self,
        conn: sqlite3.Connection,
        table: str,
        definition: str,
        old_columns: set[str],
    ) -> None:
        """Move registros sem namespace para ``legacy`` sem perda de dados."""
        legacy_table = f"{table}__legacy_migration"
        conn.execute(f"ALTER TABLE {table} RENAME TO {legacy_table}")
        conn.execute(f"CREATE TABLE {table} {definition}")
        target_columns = [
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})")
            if row["name"] != "project_id" and row["name"] in old_columns
        ]
        columns_sql = ", ".join(target_columns)
        conn.execute(
            f"INSERT INTO {table} (project_id, {columns_sql}) "
            f"SELECT ?, {columns_sql} FROM {legacy_table}",
            (self.LEGACY_PROJECT_ID,),
        )
        conn.execute(f"DROP TABLE {legacy_table}")

    @staticmethod
    def _extract_ast_symbols_and_deps(
        file_path: Path | str, content: str
    ) -> tuple[list[SymbolInfo], list[tuple[str, str, str | None, str]]]:
        """Extrai símbolos, complexidade ciclomática e dependências de um código Python via AST."""
        path = Path(file_path)
        try:
            tree = ast.parse(content, filename=str(path))
        except SyntaxError:
            return [], []

        rel_path = str(path).replace("\\", "/")
        symbols: list[SymbolInfo] = []
        deps: list[tuple[str, str, str | None, str]] = []

        has_component_contract = (
            "O que é:" in content
            and "Responsabilidade:" in content
            and "Pra que serve:" in content
        )

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node) or ""
                comp = 1 + sum(
                    1
                    for n in ast.walk(node)
                    if isinstance(n, (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.With))
                )
                symbols.append(
                    SymbolInfo(
                        name=node.name,
                        kind="function",
                        file_path=rel_path,
                        line_number=node.lineno,
                        docstring=doc,
                        complexity=comp,
                        has_component_contract=has_component_contract,
                    )
                )

            elif isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node) or ""
                symbols.append(
                    SymbolInfo(
                        name=node.name,
                        kind="class",
                        file_path=rel_path,
                        line_number=node.lineno,
                        docstring=doc,
                        complexity=1,
                        has_component_contract=has_component_contract,
                    )
                )

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    deps.append((rel_path, alias.name, None, "import"))

            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    deps.append((rel_path, mod, alias.name, "import"))

        return symbols, deps

    def index_python_file(self, file_path: Path | str, content: str | None = None) -> list[SymbolInfo]:
        """Extrai símbolos, docstrings e dependências de um arquivo Python via AST e persiste no banco.

        Args:
            file_path: Caminho do arquivo a ser indexado.
            content: Conteúdo em string opcional. Se None, lê do disco.

        Returns:
            list[SymbolInfo]: Lista de símbolos extraídos.
        """
        path = Path(file_path)
        if content is None:
            if not path.exists():
                return []
            content = path.read_text(encoding="utf-8", errors="replace")

        symbols, deps = self._extract_ast_symbols_and_deps(path, content)
        if not symbols and not deps:
            return []

        rel_path = str(path).replace("\\", "/")
        now = time.time()

        with self._connection() as conn:
            conn.execute(
                "DELETE FROM symbols WHERE project_id = ? AND file_path = ?",
                (self.project_id, rel_path),
            )
            conn.execute(
                "DELETE FROM dependencies WHERE project_id = ? AND source_file = ?",
                (self.project_id, rel_path),
            )

            for s in symbols:
                conn.execute("""
                    INSERT OR REPLACE INTO symbols
                    (project_id, file_path, name, kind, line_number, docstring, complexity, has_contract, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (self.project_id, s.file_path, s.name, s.kind, s.line_number, s.docstring, s.complexity, s.has_component_contract, now))

            for d in deps:
                conn.execute("""
                    INSERT OR IGNORE INTO dependencies
                    (project_id, source_file, target_module, target_symbol, kind, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (self.project_id, d[0], d[1], d[2], d[3], now))

        return symbols

    def index_directory(self, root_dir: Path | str) -> int:
        """Varre recursivamente um diretório e indexa todos os arquivos Python.

        Args:
            root_dir: Diretório raiz a ser indexado.

        Returns:
            int: Número de arquivos indexados.
        """
        root = Path(root_dir)
        count = 0
        for p in root.rglob("*.py"):
            if any(part in {".git", ".temp", ".pytest_cache", "venv", "__pycache__"} for part in p.parts):
                continue
            self.index_python_file(p)
            count += 1
        return count

    def get_blast_radius(self, target_file_or_module: str) -> dict[str, Any]:
        """Calcula o raio de impacto (blast radius) de alterações em um arquivo ou módulo.

        Args:
            target_file_or_module: Caminho do arquivo ou nome do módulo.

        Returns:
            dict[str, Any]: Dicionário com lista de arquivos dependentes e símbolos afetados.
        """
        norm_target = target_file_or_module.replace("\\", "/")
        stem = Path(norm_target).stem

        with self._connection() as conn:
            rows = conn.execute("""
                SELECT DISTINCT source_file, target_module, target_symbol, kind
                FROM dependencies
                WHERE project_id = ? AND source_file != ?
                  AND (source_file = ? OR target_module = ? OR target_module LIKE ? OR target_module = ?)
            """, (self.project_id, norm_target, norm_target, norm_target, f"%{stem}%", stem)).fetchall()

            dependent_files = sorted({row["source_file"] for row in rows})
            symbols = conn.execute(
                "SELECT name, kind, line_number FROM symbols WHERE project_id = ? AND file_path = ?",
                (self.project_id, norm_target),
            ).fetchall()

            return {
                "target": norm_target,
                "dependent_files_count": len(dependent_files),
                "dependent_files": dependent_files,
                "symbols_at_risk": [dict(r) for r in symbols],
            }

    @staticmethod
    def _compute_biomarkers(
        rel_path: str,
        total_lines: int,
        code_lines: int,
        comment_lines: int,
        symbols: list[Any],
    ) -> CodeHealthBiomarkers:
        """Calcula métricas e biomarcadores consolidados de um arquivo Python."""
        symbol_count = len(symbols)
        complexities = [int(r["complexity"]) for r in symbols] if symbols else [1]
        max_comp = max(complexities) if complexities else 1
        avg_comp = sum(complexities) / len(complexities) if complexities else 1.0

        with_doc = sum(1 for r in symbols if r["docstring"] and str(r["docstring"]).strip())
        coverage = with_doc / symbol_count if symbol_count > 0 else 1.0
        contract_ok = any(bool(r["has_contract"]) for r in symbols) or total_lines <= 20

        return CodeHealthBiomarkers(
            file_path=rel_path,
            total_lines=total_lines,
            code_lines=code_lines,
            comment_lines=comment_lines,
            symbol_count=symbol_count,
            max_complexity=max_comp,
            avg_complexity=round(avg_comp, 2),
            docstring_coverage=round(coverage, 2),
            contract_compliant=contract_ok,
        )

    def get_code_health(self, file_path: Path | str) -> CodeHealthBiomarkers | None:
        """Calcula biomarcadores de saúde do código para um arquivo específico.

        Args:
            file_path: Caminho do arquivo.

        Returns:
            CodeHealthBiomarkers | None: Métricas de saúde do arquivo ou None se inexistente.
        """
        path = Path(file_path)
        if not path.exists():
            return None

        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        total_lines = len(lines)
        comment_lines = sum(1 for l in lines if l.strip().startswith("#"))
        code_lines = sum(1 for l in lines if l.strip() and not l.strip().startswith("#"))

        rel_path = str(path).replace("\\", "/")
        with self._connection() as conn:
            symbols = conn.execute(
                "SELECT * FROM symbols WHERE project_id = ? AND file_path = ?",
                (self.project_id, rel_path),
            ).fetchall()

        if not symbols:
            # Tenta indexar sob demanda
            sym_list = self.index_python_file(path, content)
            if not sym_list:
                return CodeHealthBiomarkers(
                    file_path=rel_path,
                    total_lines=total_lines,
                    code_lines=code_lines,
                    comment_lines=comment_lines,
                    symbol_count=0,
                    max_complexity=1,
                    avg_complexity=1.0,
                    docstring_coverage=1.0,
                    contract_compliant=True if total_lines < 20 else False,
                )
            with self._connection() as conn:
                symbols = conn.execute(
                "SELECT * FROM symbols WHERE project_id = ? AND file_path = ?",
                (self.project_id, rel_path),
            ).fetchall()

        return self._compute_biomarkers(rel_path, total_lines, code_lines, comment_lines, symbols)

    def record_token_metrics(
        self,
        work_item_id: str,
        agent_id: str,
        step_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float = 0.0,
    ) -> None:
        """Registra o consumo de tokens e custo de uma etapa de execução de agente.

        Args:
            work_item_id: ID do work item (ex: TASK-123).
            agent_id: ID do agente que consumiu os tokens.
            step_name: Nome do passo (ex: 'planning', 'code-review').
            prompt_tokens: Quantidade de tokens de entrada.
            completion_tokens: Quantidade de tokens gerados.
            cost_usd: Custo estimado em dólares.
        """
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO token_metrics
                (project_id, work_item_id, agent_id, step_name, prompt_tokens, completion_tokens, cost_usd, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (self.project_id, work_item_id, agent_id, step_name, prompt_tokens, completion_tokens, cost_usd, time.time()))

    def get_token_summary(self, work_item_id: str) -> dict[str, Any]:
        """Obtém o resumo agregado de consumo de tokens e custo de um work item.

        Args:
            work_item_id: ID do work item.

        Returns:
            dict[str, Any]: Estatísticas agregadas de tokens e custo.
        """
        with self._connection() as conn:
            row = conn.execute("""
                SELECT 
                    COUNT(*) as total_steps,
                    COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                    COALESCE(SUM(cost_usd), 0.0) as total_cost_usd
                FROM token_metrics
                WHERE project_id = ? AND work_item_id = ?
            """, (self.project_id, work_item_id)).fetchone()

            by_agent = conn.execute("""
                SELECT agent_id, SUM(prompt_tokens) as p_tokens, SUM(completion_tokens) as c_tokens, SUM(cost_usd) as cost
                FROM token_metrics
                WHERE project_id = ? AND work_item_id = ?
                GROUP BY agent_id
            """, (self.project_id, work_item_id)).fetchall()

            return {
                "work_item_id": work_item_id,
                "total_steps": row["total_steps"],
                "total_prompt_tokens": row["total_prompt_tokens"],
                "total_completion_tokens": row["total_completion_tokens"],
                "total_tokens": row["total_prompt_tokens"] + row["total_completion_tokens"],
                "total_cost_usd": round(row["total_cost_usd"], 4),
                "breakdown_by_agent": [dict(r) for r in by_agent],
            }

    def record_quorum_vote(
        self,
        work_item_id: str,
        gate_id: str,
        voter_agent: str,
        vote: str,
        weight: float = 1.0,
        rationale: str = "",
    ) -> None:
        """Registra o voto de um agente em uma decisão de gate com quórum.

        Args:
            work_item_id: ID do work item.
            gate_id: ID do gate (ex: G4-code-security).
            voter_agent: ID do agente votante.
            vote: 'pass', 'fail', ou 'abstain'.
            weight: Peso do voto (default 1.0).
            rationale: Justificativa do voto.
        """
        with self._connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO quorum_votes
                (project_id, work_item_id, gate_id, voter_agent, vote, weight, rationale, voted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (self.project_id, work_item_id, gate_id, voter_agent, vote, weight, rationale, time.time()))

    def evaluate_quorum(self, work_item_id: str, gate_id: str, threshold: float = 0.67) -> dict[str, Any]:
        """Calcula o resultado do quórum de votação bizantina para um gate.

        Args:
            work_item_id: ID do work item.
            gate_id: ID do gate.
            threshold: Percentual mínimo de aprovação ponderada (padrão 67%).

        Returns:
            dict[str, Any]: Resultado consolidado do quórum (approved/rejected).
        """
        with self._connection() as conn:
            votes = conn.execute("""
                SELECT voter_agent, vote, weight, rationale, voted_at
                FROM quorum_votes
                WHERE project_id = ? AND work_item_id = ? AND gate_id = ?
            """, (self.project_id, work_item_id, gate_id)).fetchall()

        if not votes:
            return {
                "work_item_id": work_item_id,
                "gate_id": gate_id,
                "status": "pending_votes",
                "approved": False,
                "approval_ratio": 0.0,
                "total_votes": 0,
                "votes": [],
            }

        total_weight = sum(float(v["weight"]) for v in votes if v["vote"] != "abstain")
        pass_weight = sum(float(v["weight"]) for v in votes if v["vote"] == "pass")

        ratio = (pass_weight / total_weight) if total_weight > 0 else 0.0
        is_approved = ratio >= threshold

        return {
            "work_item_id": work_item_id,
            "gate_id": gate_id,
            "status": "approved" if is_approved else "rejected",
            "approved": is_approved,
            "approval_ratio": round(ratio, 4),
            "threshold_required": threshold,
            "total_weight": total_weight,
            "pass_weight": pass_weight,
            "votes_count": len(votes),
            "votes": [dict(v) for v in votes],
        }

    def log_trajectory(
        self,
        benchmark_name: str,
        agent_id: str,
        task_id: str,
        status: str,
        steps_count: int,
        tool_calls_count: int,
        duration_seconds: float,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Registra a execução de um benchmark ou trajetória de agente.

        Args:
            benchmark_name: Nome do benchmark ou tarefa.
            agent_id: ID do agente.
            task_id: ID da tarefa ou work item.
            status: 'pass', 'fail' ou 'converged'.
            steps_count: Quantidade de passos executados.
            tool_calls_count: Quantidade de chamadas de ferramentas.
            duration_seconds: Tempo decorrido.
            details: Dicionário adicional serializado em JSON.
        """
        details_json = json.dumps(details or {}, ensure_ascii=False)
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO trajectory_logs (
                    project_id, benchmark_name, agent_id, task_id, status, steps_count,
                    tool_calls_count, duration_seconds, details_json, recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.project_id, benchmark_name, agent_id, task_id, status, steps_count,
                tool_calls_count, duration_seconds, details_json, time.time()
            ))

    def get_trajectory_history(
        self,
        task_id: str,
        agent_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Recupera trajetórias em ordem cronológica, com limite opcional das mais recentes."""
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
            raise ValueError("limit deve ser um inteiro positivo")
        clauses = ["project_id = ?", "task_id = ?"]
        params: list[Any] = [self.project_id, task_id]
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        order = "DESC" if limit is not None else "ASC"
        query = f"SELECT * FROM trajectory_logs WHERE {' AND '.join(clauses)} ORDER BY recorded_at {order}"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        result = [dict(row) for row in rows]
        return list(reversed(result)) if limit is not None else result

