# 01 — Arquitetura do Agents Squad

## 1. Visão Geral

O **Agents Squad** é uma organização autônoma e governada de desenvolvimento de software operando por meio de artefatos locais, handoffs tipados, deltas de memória e gates formais de verificação (G1 a G6).

---

## 2. Pilares Arquiteturais

1. **Estado Autoritativo em Arquivo**: Todo o estado de execução reside em `work/<WORK-ID>/status.yaml` e nos artefatos do work item. Memória e conversa são pistas; arquivos e evidências são a verdade.
2. **Segregação de Funções (SoD)**: O autor de um artefato não pode revisar ou aprovar seu próprio trabalho em itens de risco médio, alto ou crítico.
3. **Handoffs Tipados**: Toda transição entre agentes é formalizada via `handoffs/HANDOFF-*.yaml`, exigindo links de artefatos, saída de testes, deltas de memória e confirmação (ACK) do destinatário.
4. **Gates de Qualidade (G1–G6)**: Pontos de controle formais que bloqueiam o avanço do trabalho sem evidência executada.
5. **Arquitetura de Memória e Dados em Camadas (L1/L2 em `banco/` + L3 Global)**:
   - **L1/L2 Local (`banco/squad.db`)**: SQLite WAL e FalkorDB containerizados via Docker (`docker-compose.yml`) gerenciando símbolos AST, Blast Radius, tokens, quóruns bizantinos e logs de trajetória.
   - **L3 Global (`D:\Hive-Mind`)**: Sinapse Vault durável para padrões e aprendizados inter-projetos.
6. **Motores Funcionais Autônomos (`integrations/`)**:
   - Módulos canônicos desacoplados (`procedural_skill_engine`, `trajectory_refinement_engine`, `prompt_quality_optimizer`, `blast_radius_analyzer`, `codebase_knowledge_graph`, `code_health_analyzer`, `contextual_ast_chunker`, `sdlc_role_mapper`).
   - 7 repositórios vendor integrados em `integrations/vendor/` (`boostprompt`, `chunkhound`, `codebase-memory-mcp`, `graphify`, `repowise`, `sdlc-agents`, `trace-mcp`).
7. **Provisionamento Total Zero-to-Hero**:
   - Execução via `install.ps1` ou `scripts/setup_environment.py` provisionando ambiente virtual isolado (`.venv`), banco de dados, MCPs e Docker.

---

## 3. Topologia de Comunicação e Execução

```text
delivery-orchestrator (00)
    │
    ├──> requirements-analyst (01) ──> G1-product ──> product-owner (02)
    │
    ├──> solution-architect (04) ────> G2-design ───> data-ai-architect (05)
    │
    ├──> delivery-orchestrator ──────> G3-readiness
    │
    ├──> software-engineer (06) ─────> G4-code ─────> code-reviewer (09) + security-reviewer (10)
    │
    ├──> test-engineer (11) ─────────> G5-quality ──> qa-engineer (12)
    │
    └──> devops-engineer (13) ───────> G6-release ──> governance-auditor (14) + Humano
```
