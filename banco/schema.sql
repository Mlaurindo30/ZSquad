-- =============================================================================
-- Canonical Relational & AST Schema for Agents Squad (banco/schema.sql)
-- Version: 1.0.0
-- Description: Armazena símbolos AST, dependências, blast radius, métricas de
--              tokens, quórum de votação bizantina e trajetórias de benchmark.
-- =============================================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- 1. Tabela de Símbolos AST e Contratos
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL, -- 'function', 'class', 'module'
    line_number INTEGER NOT NULL,
    docstring TEXT,
    complexity INTEGER DEFAULT 1,
    has_contract BOOLEAN DEFAULT 0,
    updated_at REAL NOT NULL,
    UNIQUE(file_path, name, kind)
);

-- 2. Tabela de Dependências e Grafo de Chamadas (Inspirado em Graphify e Trace-MCP)
CREATE TABLE IF NOT EXISTS dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    target_module TEXT NOT NULL,
    target_symbol TEXT,
    kind TEXT NOT NULL, -- 'import', 'call', 'inheritance'
    updated_at REAL NOT NULL,
    UNIQUE(source_file, target_module, target_symbol, kind)
);

-- 3. Tabela de Métricas de Tokens e Custos por Work Item
CREATE TABLE IF NOT EXISTS token_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_item_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    step_name TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_usd REAL DEFAULT 0.0,
    recorded_at REAL NOT NULL
);

-- 4. Tabela de Trajetórias de Execução de Agentes (EDD & Benchmarks)
CREATE TABLE IF NOT EXISTS trajectory_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark_name TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL, -- 'pass', 'fail', 'converged'
    steps_count INTEGER NOT NULL,
    tool_calls_count INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    details_json TEXT,
    recorded_at REAL NOT NULL
);

-- 5. Tabela de Votação e Quórum Bizantino de Gates (Swarm BFT)
CREATE TABLE IF NOT EXISTS quorum_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_item_id TEXT NOT NULL,
    gate_id TEXT NOT NULL,
    voter_agent TEXT NOT NULL,
    vote TEXT NOT NULL, -- 'pass', 'fail', 'abstain'
    weight REAL DEFAULT 1.0,
    rationale TEXT,
    voted_at REAL NOT NULL,
    UNIQUE(work_item_id, gate_id, voter_agent)
);

-- Índices de Alta Performance
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_deps_source ON dependencies(source_file);
CREATE INDEX IF NOT EXISTS idx_deps_target ON dependencies(target_module);
CREATE INDEX IF NOT EXISTS idx_tokens_work_item ON token_metrics(work_item_id);
CREATE INDEX IF NOT EXISTS idx_trajectory_benchmark ON trajectory_logs(benchmark_name);
CREATE INDEX IF NOT EXISTS idx_quorum_work_gate ON quorum_votes(work_item_id, gate_id);
