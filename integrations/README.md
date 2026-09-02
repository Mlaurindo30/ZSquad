# Motores Funcionais de Integração e Inteligência de Código

Este diretório contém os motores funcionais e adaptadores autônomos que operam a inteligência de código, procedural memory, observabilidade de trajetórias e otimização de prompts do **Agents Squad**.

---

## Estrutura Funcional do Diretório

```text
integrations/
├── procedural_skill_engine.py       # Motor de síntese /learn, linter rígido e AST auditor
├── trajectory_refinement_engine.py  # Motor de traces de episódios e autorreparo /refine
├── prompt_quality_optimizer.py      # Otimizador de qualidade de briefings (4 dimensões)
├── blast_radius_analyzer.py         # Analisador de raio de impacto e dependências
├── codebase_knowledge_graph.py      # Construtor do grafo AST de conhecimento de código
├── code_health_analyzer.py          # Analisador de biomarcadores determinísticos de qualidade
├── contextual_ast_chunker.py        # Divisor semântico de código por nós sintáticos (cAST)
├── sdlc_role_mapper.py              # Mapeador de personas para papéis padronizados de SDLC
├── vendor/                          # Pacotes e integrações diretas (ex: boostprompt)
└── README.md                        # Este guia
```

---

## Tabela de Motores e Responsabilidades

| Motor Funcional | Arquivo | Responsabilidade Canônica |
|---|---|---|
| **Procedural Skill Engine** | `procedural_skill_engine.py` | Síntese de skills `agentskills.io`, linter rígido (anti-shell, anti-marketing) e AST security audit |
| **Trajectory Refinement Engine** | `trajectory_refinement_engine.py` | Rastreamento de passos, categorização de `ErrorCode` e destilação de heurísticas `/refine` |
| **Prompt Quality Optimizer** | `prompt_quality_optimizer.py` | Cálculo do índice de qualidade de prompts (Objetivo, Ground Truth, Antifabricação, Fronteiras) |
| **Blast Radius Analyzer** | `blast_radius_analyzer.py` | Travessia do grafo de dependências para cálculo do raio de impacto |
| **Codebase Knowledge Graph** | `codebase_knowledge_graph.py` | Mapeamento estrutural de símbolos, funções, classes e chamadas |
| **Code Health Analyzer** | `code_health_analyzer.py` | Pontuação de 0.0 a 10.0 baseada em complexidade ciclomática e contratos |
| **Contextual AST Chunker** | `contextual_ast_chunker.py` | Chunking preservando limites sintáticos de funções e classes |
| **SDLC Role Mapper** | `sdlc_role_mapper.py` | Mapeamento para papéis compatíveis com IDEs e fluxos de desenvolvimento |
