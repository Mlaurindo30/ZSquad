# Motores Funcionais de Integração e Inteligência de Código

Este diretório contém os motores funcionais e adaptadores autônomos que operam a inteligência de código, procedural memory, observabilidade de trajetórias e otimização de prompts do **Agents Squad**.

---

## Estrutura Funcional do Diretório

```text
integrations/
├── devops_platform_connector.py   # Conector Azure DevOps/Jira (ativo)
├── experimental/                  # Motores órfãos (sem caller ativo — audit 2026-09-02)
│   ├── procedural_skill_engine.py
│   ├── trajectory_refinement_engine.py
│   ├── prompt_quality_optimizer.py
│   ├── blast_radius_analyzer.py
│   ├── codebase_knowledge_graph.py
│   ├── code_health_analyzer.py
│   ├── contextual_ast_chunker.py
│   ├── sdlc_role_mapper.py
│   ├── clone_or_update_repos.py
│   ├── gitingest.py
│   ├── toon.py
│   ├── zcode_subagents.py
│   └── README.md
└── README.md                      # Este guia
```

> **Nota:** Os motores em `experimental/` foram movidos durante o audit de 2026-09-02
> (seção 4.1, issue B-4) — não possuem callers ativos. Veja
> `integrations/experimental/README.md` para detalhes e instruções de restauração.

---

## Tabela de Motores e Responsabilidades

| Motor Funcional | Arquivo | Responsabilidade Canônica |
|---|---|---|
| **Procedural Skill Engine** | `experimental/procedural_skill_engine.py` | Síntese de skills `agentskills.io`, linter rígido (anti-shell, anti-marketing) e AST security audit |
| **Trajectory Refinement Engine** | `experimental/trajectory_refinement_engine.py` | Rastreamento de passos, categorização de `ErrorCode` e destilação de heurísticas `/refine` |
| **Prompt Quality Optimizer** | `experimental/prompt_quality_optimizer.py` | Cálculo do índice de qualidade de prompts (Objetivo, Ground Truth, Antifabricação, Fronteiras) |
| **Blast Radius Analyzer** | `experimental/blast_radius_analyzer.py` | Travessia do grafo de dependências para cálculo do raio de impacto |
| **Codebase Knowledge Graph** | `experimental/codebase_knowledge_graph.py` | Mapeamento estrutural de símbolos, funções, classes e chamadas |
| **Code Health Analyzer** | `experimental/code_health_analyzer.py` | Pontuação de 0.0 a 10.0 baseada em complexidade ciclomática e contratos |
| **Contextual AST Chunker** | `experimental/contextual_ast_chunker.py` | Chunking preservando limites sintáticos de funções e classes |
| **SDLC Role Mapper** | `experimental/sdlc_role_mapper.py` | Mapeamento para papéis compatíveis com IDEs e fluxos de desenvolvimento |
