#!/usr/bin/env python3
"""
O que é: Camada de banco de dados local embedded (SQLite) de alta performance para o Agents Squad.
Responsabilidade: Armazenar e consultar AST, dependências, blast radius, métricas de tokens e histórico de trajetórias.
Pra que serve: Prover inteligência de código em sub-milissegundos (L1/L2) para os agentes, calculando impacto de mudanças e consumo de tokens sem latência de rede.
Comportamento em falha: Trata erros de I/O e SQL com transações atômicas seguras (WAL mode) e fallback para consultas estáticas.
Conexões: Utilizado por agent_squad.py, evaluate_agent_trajectories.py, sync_mcp_servers.py e linters de Clean Code.
Dependências & Imports:
  - sqlite3: Banco de dados relacional embedded embutido na standard library.
  - ast: Parser sintático de código Python para extração determinística.
  - json, pathlib, hashlib, time: Utilitários padrão.
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

    _TABLE_NAMES = (
        "symbols",
        "dependencies",
        "token_metrics",
        "trajectory_logs",
        "quorum_votes",
        "ops_recovery",
        "workflow_metrics",
        "memory_facts",
    )
    _TABLE_COLUMNS = {
        "symbols": ("file_path", "name", "kind", "line_number", "docstring", "complexity", "has_contract", "updated_at"),
        "dependencies": ("source_file", "target_module", "target_symbol", "kind", "updated_at"),
        "token_metrics": ("work_item_id", "agent_id", "step_name", "prompt_tokens", "completion_tokens", "cost_usd", "recorded_at"),
        "trajectory_logs": ("benchmark_name", "agent_id", "task_id", "status", "steps_count", "tool_calls_count", "duration_seconds", "details_json", "recorded_at"),
        "quorum_votes": ("work_item_id", "gate_id", "voter_agent", "vote", "weight", "rationale", "voted_at"),
        "ops_recovery": ("target_key", "work_item_id", "agent_id", "provider", "model", "phase", "reason", "action", "attempt", "backoff_seconds", "error_message", "rationale", "recorded_at"),
        "workflow_metrics": ("work_item_id", "item_type", "story_points", "t_shirt_size", "phase", "started_at", "ended_at", "cycle_time_hours", "lead_time_hours", "blocked_time_hours", "status", "recorded_at"),
        "memory_facts": ("work_item_id", "author", "kind", "statement", "source", "confidence", "sensitivity", "invalidates_when", "recorded_at"),
    }

    @classmethod
    def _validate_table(cls, table: str) -> None:
        """Rejeita qualquer identificador fora do conjunto fechado de tabelas internas."""
        if table not in cls._TABLE_NAMES:
            raise ValueError(f"tabela de esquema desconhecida: {table}")

    @staticmethod
    def _create_table(conn: sqlite3.Connection, table: str) -> None:
        """Cria uma tabela interna por SQL literal após validação do chamador."""
        LocalAgentDB._validate_table(table)
        # Cada chamada usa DDL literal com identificador validado.
        if table == "symbols":
            conn.execute(
                "CREATE TABLE symbols (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, file_path TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, line_number INTEGER NOT NULL, docstring TEXT, complexity INTEGER DEFAULT 1, has_contract BOOLEAN DEFAULT 0, updated_at REAL NOT NULL, UNIQUE(project_id, file_path, name, kind))"
            )
            return
        if table == "dependencies":
            conn.execute(
                "CREATE TABLE dependencies (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, source_file TEXT NOT NULL, target_module TEXT NOT NULL, target_symbol TEXT, kind TEXT NOT NULL, updated_at REAL NOT NULL, UNIQUE(project_id, source_file, target_module, target_symbol, kind))"
            )
            return
        if table == "token_metrics":
            conn.execute(
                "CREATE TABLE token_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, work_item_id TEXT NOT NULL, agent_id TEXT NOT NULL, step_name TEXT NOT NULL, prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL, cost_usd REAL DEFAULT 0.0, recorded_at REAL NOT NULL)"
            )
            return
        if table == "trajectory_logs":
            conn.execute(
                "CREATE TABLE trajectory_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, benchmark_name TEXT NOT NULL, agent_id TEXT NOT NULL, task_id TEXT NOT NULL, status TEXT NOT NULL, steps_count INTEGER NOT NULL, tool_calls_count INTEGER NOT NULL, duration_seconds REAL NOT NULL, details_json TEXT, recorded_at REAL NOT NULL)"
            )
            return
        if table == "quorum_votes":
            conn.execute(
                "CREATE TABLE quorum_votes (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, work_item_id TEXT NOT NULL, gate_id TEXT NOT NULL, voter_agent TEXT NOT NULL, vote TEXT NOT NULL, weight REAL DEFAULT 1.0, rationale TEXT, voted_at REAL NOT NULL, UNIQUE(project_id, work_item_id, gate_id, voter_agent))"
            )
            return
        if table == "ops_recovery":
            conn.execute(
                "CREATE TABLE ops_recovery (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, target_key TEXT NOT NULL, work_item_id TEXT, agent_id TEXT, provider TEXT, model TEXT, phase TEXT NOT NULL, reason TEXT NOT NULL, action TEXT NOT NULL, attempt INTEGER NOT NULL, backoff_seconds REAL NOT NULL, error_message TEXT, rationale TEXT, recorded_at REAL NOT NULL)"
            )
            return
        if table == "workflow_metrics":
            conn.execute(
                "CREATE TABLE workflow_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, work_item_id TEXT NOT NULL, item_type TEXT NOT NULL, story_points INTEGER, t_shirt_size TEXT, phase TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL, cycle_time_hours REAL DEFAULT 0.0, lead_time_hours REAL DEFAULT 0.0, blocked_time_hours REAL DEFAULT 0.0, status TEXT NOT NULL DEFAULT 'active', recorded_at REAL NOT NULL)"
            )
            return
        if table == "memory_facts":
            conn.execute(
                "CREATE TABLE memory_facts (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, work_item_id TEXT NOT NULL, author TEXT NOT NULL, kind TEXT NOT NULL, statement TEXT NOT NULL, source TEXT NOT NULL, confidence REAL DEFAULT 1.0, sensitivity TEXT NOT NULL DEFAULT 'internal', invalidates_when TEXT, recorded_at REAL NOT NULL)"
            )
            return
        # _validate_table já levantou ValueError para identificadores
        # desconhecidos; o fluxo nunca alcança este ponto.

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        """Consulta metadados com PRAGMAs literais para tabelas internas."""
        LocalAgentDB._validate_table(table)
        # Cada branch usa PRAGMA literal; o identificador ``table`` foi validado
        # pela whitelist. Não há concatenação dinâmica com entrada do usuário.
        if table == "symbols":
            return {row["name"] for row in conn.execute("PRAGMA table_info(symbols)")}
        if table == "dependencies":
            return {row["name"] for row in conn.execute("PRAGMA table_info(dependencies)")}
        if table == "token_metrics":
            return {row["name"] for row in conn.execute("PRAGMA table_info(token_metrics)")}
        if table == "trajectory_logs":
            return {row["name"] for row in conn.execute("PRAGMA table_info(trajectory_logs)")}
        if table == "quorum_votes":
            return {row["name"] for row in conn.execute("PRAGMA table_info(quorum_votes)")}
        if table == "ops_recovery":
            return {row["name"] for row in conn.execute("PRAGMA table_info(ops_recovery)")}
        if table == "workflow_metrics":
            return {row["name"] for row in conn.execute("PRAGMA table_info(workflow_metrics)")}
        if table == "memory_facts":
            return {row["name"] for row in conn.execute("PRAGMA table_info(memory_facts)")}
        return set()

    def _init_db(self) -> None:
        """Cria ou migra atomicamente o esquema central namespaced."""
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            for table in self._TABLE_NAMES:
                if table not in existing:
                    self._create_table(conn, table)
                    continue
                columns = self._table_columns(conn, table)
                if "project_id" not in columns:
                    self._migrate_legacy_table(conn, table, columns)

    @classmethod
    def _migrate_legacy_table(cls, conn: sqlite3.Connection, table: str, legacy_columns: set[str]) -> None:
        """Reconstrói uma tabela legada usando somente instruções SQL literais."""
        cls._validate_table(table)
        statements = {
            "symbols": ("ALTER TABLE symbols RENAME TO symbols_legacy", "INSERT INTO symbols (project_id, file_path, name, kind, line_number, docstring, complexity, has_contract, updated_at) SELECT ?, file_path, name, kind, line_number, docstring, complexity, has_contract, updated_at FROM symbols_legacy", "DROP TABLE symbols_legacy"),
            "dependencies": ("ALTER TABLE dependencies RENAME TO dependencies_legacy", "INSERT INTO dependencies (project_id, source_file, target_module, target_symbol, kind, updated_at) SELECT ?, source_file, target_module, target_symbol, kind, updated_at FROM dependencies_legacy", "DROP TABLE dependencies_legacy"),
            "token_metrics": ("ALTER TABLE token_metrics RENAME TO token_metrics_legacy", "INSERT INTO token_metrics (project_id, work_item_id, agent_id, step_name, prompt_tokens, completion_tokens, cost_usd, recorded_at) SELECT ?, work_item_id, agent_id, step_name, prompt_tokens, completion_tokens, cost_usd, recorded_at FROM token_metrics_legacy", "DROP TABLE token_metrics_legacy"),
            "trajectory_logs": ("ALTER TABLE trajectory_logs RENAME TO trajectory_logs_legacy", "INSERT INTO trajectory_logs (project_id, benchmark_name, agent_id, task_id, status, steps_count, tool_calls_count, duration_seconds, details_json, recorded_at) SELECT ?, benchmark_name, agent_id, task_id, status, steps_count, tool_calls_count, duration_seconds, details_json, recorded_at FROM trajectory_logs_legacy", "DROP TABLE trajectory_logs_legacy"),
            "quorum_votes": ("ALTER TABLE quorum_votes RENAME TO quorum_votes_legacy", "INSERT INTO quorum_votes (project_id, work_item_id, gate_id, voter_agent, vote, weight, rationale, voted_at) SELECT ?, work_item_id, gate_id, voter_agent, vote, weight, rationale, voted_at FROM quorum_votes_legacy", "DROP TABLE quorum_votes_legacy"),
        }
        required = set(cls._TABLE_COLUMNS[table])
        if not required.issubset(legacy_columns):
            missing = sorted(required - legacy_columns)
            raise ValueError(f"colunas legadas ausentes em {table}: {missing}")
        rename_sql, insert_sql, drop_sql = statements[table]
        conn.execute(rename_sql)
        cls._create_table(conn, table)
        conn.execute(insert_sql, (cls.LEGACY_PROJECT_ID,))
        conn.execute(drop_sql)

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

    def get_project_symbols(self, file_path: str | None = None) -> list[dict[str, Any]]:
        """Recupera símbolos AST do projeto, opcionalmente filtrados por arquivo.

        Args:
            file_path: Caminho opcional do arquivo para filtro.

        Returns:
            list[dict[str, Any]]: Lista de símbolos do projeto.
        """
        clauses = ["project_id = ?"]
        params: list[Any] = [self.project_id]
        if file_path:
            clauses.append("file_path = ?")
            params.append(file_path.replace("\\", "/"))

        query = f"SELECT * FROM symbols WHERE {' AND '.join(clauses)} ORDER BY file_path ASC, line_number ASC"
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_project_dependencies(self, source_file: str | None = None) -> list[dict[str, Any]]:
        """Recupera dependências do projeto, opcionalmente filtradas por arquivo de origem.

        Args:
            source_file: Caminho opcional do arquivo de origem para filtro.

        Returns:
            list[dict[str, Any]]: Lista de dependências do projeto.
        """
        clauses = ["project_id = ?"]
        params: list[Any] = [self.project_id]
        if source_file:
            clauses.append("source_file = ?")
            params.append(source_file.replace("\\", "/"))

        query = f"SELECT * FROM dependencies WHERE {' AND '.join(clauses)} ORDER BY source_file ASC"
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


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

    def record_recovery_event(
        self,
        target_key: str,
        phase: str,
        reason: str,
        action: str,
        attempt: int,
        backoff_seconds: float,
        work_item_id: str | None = None,
        agent_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        error_message: str | None = None,
        rationale: str | None = None,
    ) -> int:
        """Persiste um ``RecoveryDecision`` (Etapa 6) usando binds de parâmetros."""
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO ops_recovery ("
                "project_id, target_key, work_item_id, agent_id, provider, model,"
                " phase, reason, action, attempt, backoff_seconds,"
                " error_message, rationale, recorded_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.project_id,
                    target_key,
                    work_item_id,
                    agent_id,
                    provider,
                    model,
                    phase,
                    reason,
                    action,
                    attempt,
                    backoff_seconds,
                    error_message,
                    rationale,
                    time.time(),
                ),
            )
            return int(cursor.lastrowid or 0)

    def list_recovery_events(
        self,
        target_key: str | None = None,
        since: float | None = None,
        limit: int | None = 100,
    ) -> list[dict[str, Any]]:
        """Lista eventos de recuperação do projeto, mais recentes primeiro."""
        clauses = ["project_id = ?"]
        params: list[Any] = [self.project_id]
        if target_key:
            clauses.append("target_key = ?")
            params.append(target_key)
        if since is not None:
            clauses.append("recorded_at >= ?")
            params.append(since)
        order = "DESC" if limit is not None else "ASC"
        query = f"SELECT * FROM ops_recovery WHERE {' AND '.join(clauses)} ORDER BY recorded_at {order}"
        params_list: list[Any] = list(params)
        if limit is not None:
            query += " LIMIT ?"
            params_list.append(limit)
        with self._connection() as conn:
            rows = conn.execute(query, params_list).fetchall()
        return [dict(r) for r in rows]

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

    def record_workflow_metric(
        self,
        work_item_id: str,
        item_type: str,
        phase: str,
        story_points: int | None = None,
        t_shirt_size: str | None = None,
        started_at: float | None = None,
        ended_at: float | None = None,
        cycle_time_hours: float = 0.0,
        lead_time_hours: float = 0.0,
        blocked_time_hours: float = 0.0,
        status: str = "active",
    ) -> None:
        """Registra métricas de fluxo e ciclo de vida de um work item."""
        now_ts = time.time()
        start_ts = started_at if started_at is not None else now_ts
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO workflow_metrics (
                    project_id, work_item_id, item_type, story_points, t_shirt_size,
                    phase, started_at, ended_at, cycle_time_hours, lead_time_hours,
                    blocked_time_hours, status, recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.project_id, work_item_id, item_type, story_points, t_shirt_size,
                phase, start_ts, ended_at, cycle_time_hours, lead_time_hours,
                blocked_time_hours, status, now_ts
            ))

    def get_workflow_metrics(
        self,
        work_item_id: str | None = None,
        phase: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recupera métricas de fluxo do projeto atual."""
        clauses = ["project_id = ?"]
        params: list[Any] = [self.project_id]
        if work_item_id:
            clauses.append("work_item_id = ?")
            params.append(work_item_id)
        if phase:
            clauses.append("phase = ?")
            params.append(phase)
        query = f"SELECT * FROM workflow_metrics WHERE {' AND '.join(clauses)} ORDER BY recorded_at DESC"
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def record_memory_fact(
        self,
        project_id: str,
        work_item_id: str,
        author: str,
        kind: str,
        statement: str,
        source: str,
        confidence: float = 1.0,
        sensitivity: str = "internal",
        invalidates_when: str | None = None,
    ) -> int:
        """Registra um fato de memória (cognição L1/L2) para um work item.

        Args:
            project_id: Identificador do projeto (valida isolamento multi-tenant).
            work_item_id: Identificador do work item.
            author: Persona ou agente emissor do fato.
            kind: Tipo do fato ('fact', 'decision', 'dependency', 'risk', 'pending').
            statement: Descrição/conteúdo do fato.
            source: Origem do fato (ex: handoff, arquivo, comando).
            confidence: Nível de confiança heurística (0.0 a 1.0).
            sensitivity: Nível de sensibilidade do dado ('internal', 'public', etc.).
            invalidates_when: Condição de invalidação do fato, se aplicável.

        Returns:
            int: ID sequencial do fato inserido.
        """
        if project_id != self.project_id:
            raise ValueError(f"project_id '{project_id}' incompatível com namespace '{self.project_id}'")

        now_ts = time.time()
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO memory_facts ("
                "project_id, work_item_id, author, kind, statement, source,"
                " confidence, sensitivity, invalidates_when, recorded_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.project_id,
                    work_item_id,
                    author,
                    kind,
                    statement,
                    source,
                    float(confidence),
                    sensitivity,
                    invalidates_when,
                    now_ts,
                ),
            )
            return int(cursor.lastrowid or 0)

    def get_memory_facts(
        self,
        project_id: str,
        work_item_id: str,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recupera os fatos de memória registrados para um work item.

        Args:
            project_id: Identificador do projeto (valida isolamento multi-tenant).
            work_item_id: Identificador do work item.
            kind: Filtro opcional por tipo ('fact', 'decision', etc.).

        Returns:
            list[dict[str, Any]]: Lista de fatos de memória em ordem cronológica.
        """
        if project_id != self.project_id:
            raise ValueError(f"project_id '{project_id}' incompatível com namespace '{self.project_id}'")

        clauses = ["project_id = ?", "work_item_id = ?"]
        params: list[Any] = [self.project_id, work_item_id]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)

        query = f"SELECT * FROM memory_facts WHERE {' AND '.join(clauses)} ORDER BY recorded_at ASC, id ASC"
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_memory_summary(
        self,
        project_id: str,
        work_item_id: str,
    ) -> dict[str, Any]:
        """Gera um sumário agregado dos fatos de memória de um work item.

        Args:
            project_id: Identificador do projeto (valida isolamento multi-tenant).
            work_item_id: Identificador do work item.

        Returns:
            dict[str, Any]: Dicionário com total de fatos, contagem por tipo e lista de fatos.
        """
        if project_id != self.project_id:
            raise ValueError(f"project_id '{project_id}' incompatível com namespace '{self.project_id}'")

        facts = self.get_memory_facts(project_id, work_item_id)
        by_kind: dict[str, int] = {}
        for f in facts:
            k = f.get("kind", "unknown")
            by_kind[k] = by_kind.get(k, 0) + 1

        return {
            "project_id": self.project_id,
            "work_item_id": work_item_id,
            "total_facts": len(facts),
            "by_kind": by_kind,
            "facts": facts,
        }



