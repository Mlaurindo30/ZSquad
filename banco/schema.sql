-- =============================================================================
-- Canonical Relational & AST Schema for Agents Squad (banco/schema.sql)
-- Version: 1.1.0
-- Description: Armazena símbolos AST, dependências, blast radius, métricas de
--              tokens, quórum de votação bizantina e trajetórias de benchmark.
-- Fonte de verdade real: scripts/local_agent_db.py (LocalAgentDB._create_table).
-- Este arquivo é documentação de referência e deve espelhar aquele DDL;
-- toda tabela é isolada por projeto via ``project_id`` (namespace obrigatório).
-- =============================================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- 1. Tabela de Símbolos AST e Contratos
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL, -- 'function', 'class', 'module'
    line_number INTEGER NOT NULL,
    docstring TEXT,
    complexity INTEGER DEFAULT 1,
    has_contract BOOLEAN DEFAULT 0,
    updated_at REAL NOT NULL,
    UNIQUE(project_id, file_path, name, kind)
);

-- 2. Tabela de Dependências e Grafo de Chamadas (Inspirado em Graphify e Trace-MCP)
CREATE TABLE IF NOT EXISTS dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    target_module TEXT NOT NULL,
    target_symbol TEXT,
    kind TEXT NOT NULL, -- 'import', 'call', 'inheritance'
    updated_at REAL NOT NULL,
    UNIQUE(project_id, source_file, target_module, target_symbol, kind)
);

-- 3. Tabela de Métricas de Tokens e Custos por Work Item
CREATE TABLE IF NOT EXISTS token_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
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
    project_id TEXT NOT NULL,
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
    project_id TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    gate_id TEXT NOT NULL,
    voter_agent TEXT NOT NULL,
    vote TEXT NOT NULL, -- 'pass', 'fail', 'abstain'
    weight REAL DEFAULT 1.0,
    rationale TEXT,
    voted_at REAL NOT NULL,
    UNIQUE(project_id, work_item_id, gate_id, voter_agent)
);

-- 6. Tabela de Métricas de Workflow & Flow Analytics (DORA & Agile Flow)
CREATE TABLE IF NOT EXISTS workflow_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_item_id TEXT NOT NULL,
    project_id TEXT,
    item_type TEXT NOT NULL, -- 'epic', 'story', 'bug', 'task', 'spike'
    story_points INTEGER,
    t_shirt_size TEXT,
    phase TEXT NOT NULL, -- 'blueprint', 'scaffolding', 'implementation', 'code-security-review', 'quality-validation', 'governance-release', 'done' (US-10 2026-09-02: removed 'review-qa')
    started_at REAL NOT NULL,
    ended_at REAL,
    cycle_time_hours REAL DEFAULT 0.0,
    lead_time_hours REAL DEFAULT 0.0,
    blocked_time_hours REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'active', -- 'active', 'completed', 'blocked'
    recorded_at REAL NOT NULL
);

-- 7. Tabela de Recuperação Operacional (Auto-Correction / SRE Closed-Loop)
CREATE TABLE IF NOT EXISTS ops_recovery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    target_key TEXT NOT NULL,
    work_item_id TEXT,
    agent_id TEXT,
    provider TEXT,
    model TEXT,
    phase TEXT NOT NULL,
    reason TEXT NOT NULL,
    action TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    backoff_seconds REAL NOT NULL,
    error_message TEXT,
    rationale TEXT,
    recorded_at REAL NOT NULL
);

-- Índices de Alta Performance
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(project_id, file_path);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(project_id, name);
CREATE INDEX IF NOT EXISTS idx_deps_source ON dependencies(project_id, source_file);
CREATE INDEX IF NOT EXISTS idx_deps_target ON dependencies(project_id, target_module);
CREATE INDEX IF NOT EXISTS idx_tokens_work_item ON token_metrics(project_id, work_item_id);
CREATE INDEX IF NOT EXISTS idx_trajectory_benchmark ON trajectory_logs(project_id, benchmark_name);
CREATE INDEX IF NOT EXISTS idx_quorum_work_gate ON quorum_votes(project_id, work_item_id, gate_id);
CREATE INDEX IF NOT EXISTS idx_workflow_work_item ON workflow_metrics(work_item_id);
CREATE INDEX IF NOT EXISTS idx_workflow_phase ON workflow_metrics(phase);

