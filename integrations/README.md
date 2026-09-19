# Motores Funcionais de Integração e Inteligência de Código

Este diretório contém os motores funcionais e adaptadores autônomos que operam a inteligência de código, procedural memory, observabilidade de trajetórias e otimização de prompts do **Agents Squad**.

---

## Estrutura Funcional do Diretório

```text
integrations/
├── clone_or_update_repos.py       # Gerenciador canônico de clones e atualizações upstream vendor
├── devops_platform_connector.py   # Conector Azure DevOps/Jira (ativo)
├── spec-kit/                      # Integração SDD governada (snapshot upstream + adapter) — ver abaixo
├── experimental/                  # Motores órfãos (sem caller ativo — audit 2026-09-02)
│   ├── procedural_skill_engine.py
│   ├── trajectory_refinement_engine.py
│   ├── prompt_quality_optimizer.py
│   ├── blast_radius_analyzer.py
│   ├── codebase_knowledge_graph.py
│   ├── code_health_analyzer.py
│   ├── contextual_ast_chunker.py
│   ├── sdlc_role_mapper.py
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

## Integração spec-kit (SDD — Especificação como Pré-condição Governada)

| Propriedade | Valor |
|---|---|
| **Diretório** | `integrations/spec-kit/` |
| **Tipo** | Snapshot upstream versionado + adapter Squad (vendor governado, sem repositório Git aninhado) |
| **Origem** | [github/spec-kit](https://github.com/github/spec-kit), commit `c173bf19a6654e3b05386ec3599349a55282b897` (567 arquivos, licença MIT) |
| **adapter_version** | 0.1.0 (`integrations/spec-kit/PROVENANCE.yaml`) |
| **Entrada canônica** | CLI do Squad (`python scripts/agent_squad.py`) — não há segundo orquestrador |
| **Componentes ativos** | `adapter/` (contratos, validação, política, rendering, backlog), `overlays/commands/` (7 comandos governados), enforcement no CLI (`decide-gate`, `advance-state`, `run-engine`) |
| **Integridade** | `UPSTREAM_FILES.sha256` (567 entradas) verificado por `verify_snapshot.py` |
| **Operação** | `docs/spec-kit-operations.md` (atualização, rollback, diagnóstico, adoção legada) |
| **Política por projeto** | `<project_root>/.agents_squad/config/sdd-policy.yaml` (schema: `contracts/sdd-policy.schema.json`) |
| **Proveniência** | `integrations/spec-kit/PROVENANCE.yaml`; patches em `integrations/spec-kit/PATCHES.md` |

**Notas de catálogo:** o `agent_squad.py audit` reporta ~255 apontamentos "skill
ativa fora do catálogo" sob `integrations/spec-kit/` (upstream + adapter/tests) —
classe conhecida, baseline documentada na seção 6.1 de
`docs/spec-kit-operations.md` (tratamento: exclusão de catálogo ou aceite
de governança; não é silenciosamente ignorado).

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
