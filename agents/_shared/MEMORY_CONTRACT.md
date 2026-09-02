# Contrato de Memória e Inteligência de Código do Squad

## 1. Camadas da Arquitetura de Memória

A cognição e retenção de conhecimento do Agents Squad estruturam-se em quatro níveis complementares:

1. **Memória de Trabalho & Contexto Volátil**: Contexto temporário da sessão de execução; não é fonte durável.
2. **Memória de Trabalho Local & Cache L1/L2 (`banco/squad.db`)**:
   - Persistência estruturada em SQLite WAL e FalkorDB local.
   - Gerencia símbolos de código (AST), grafo de dependências, cálculo de Blast Radius, telemetria de consumo de tokens/custos, quóruns bizantinos de gates e traces de trajetórias.
3. **Memória do Work Item (`work/<WORK-ID>/memory/`)**:
   - `shared/summary.md`: Somente fatos confirmados, decisões técnicas, dependências, riscos e pendências validadas.
   - `agents/<persona>.md`: Checkpoints privados, hipóteses e próximos passos de cada papel.
   - `deltas/MEM-*.yaml`: Deltas tipados com `kind: fact | decision | dependency | risk | pending` vinculados aos handoffs.
4. **Memória Procedural & Auto-Skills (`integrations/experimental/procedural_skill_engine.py`)**:
   - Padrão `agentskills.io` com linter de convenções e auditoria AST para transformar soluções consolidadas em skills ativas via `/learn`.
5. **Memória de Trajetória & Autorreparo (`integrations/experimental/trajectory_refinement_engine.py`)**:
   - Traces de passos em JSONL e classificação de erros para destilação automática de regras corretivas de briefing via `/refine`.
6. **Memória Durável Global L3 (`D:/Hive-Mind`)**:
   - Sinapse Vault durável para padrões arquiteturais, decisões e aprendizados reutilizáveis entre múltiplos projetos.

---

## 2. Regra de Promoção de Memória

- O agente emissor propõe um `memory-delta` (`MEM-*.yaml`) acompanhando o seu `HANDOFF-*.yaml`.
- O orquestrador valida origem, sensibilidade, vigência e duplicidade antes de consolidar o fato em `memory/shared/summary.md`.
- Conhecimento procedural reutilizável é sintetizado pelo comando `/learn` para `skills/discovery/intake/` e promovido pelo `18-skill-curator` após quarentena e auditoria estática.

---

## 3. Higiene, Segurança e Verificação

- **Proibição de Dados Sensíveis**: Nunca gravar credenciais, tokens, segredos ou dados pessoais em arquivos de memória.
- **Metadados Obrigatórios**: Toda entrada de memória deve possuir `source`, `recorded_at`, `confidence`, `sensitivity` e `invalidates_when`.
- **Memória é Pista, Não Prova**: A memória serve como guia heurístico; fatos mutáveis sobre código, testes e ambiente devem ser confirmados diretamente no artefato e na execução real.

