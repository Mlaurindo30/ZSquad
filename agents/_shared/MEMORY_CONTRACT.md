# Contrato de Memória e Inteligência de Código do Squad

## 1. Topologia Oficial de 3 Pilares de Memória

A retenção de conhecimento e a cognição do Agents Squad estruturam-se em três pilares oficiais:

### Pilar 1: Memória Primária do Projeto (Local / Obrigatória)
- **Banco de Dados do Squad (`banco/squad.db`)**:
  - Persistência estruturada em SQLite WAL com isolamento por `project_id`.
  - Tabela `memory_facts`: Fatos confirmados, decisões técnicas e deltas tipados.
  - Tabelas `symbols` e `dependencies`: Indexação AST de classes, funções e chamadas de código.
  - Tabela `workflow_metrics`: Telemetria de execução, consumo e quóruns de gates.
  - Acesso e operações via CLI: `python scripts/agent_squad.py query-memory --work-item <ID>` e `python scripts/agent_squad.py record-fact`.
- **Grafo de Código / Graphify (`integrations/codebase_knowledge_graph.py`)**:
  - Mapeamento estrutural de símbolos, dependências e acoplamento arquitetural.
  - Cálculo determinístico de Blast Radius e impacto arquitetural para gates de design e código.

### Pilar 2: Colaboração e Rastreabilidade do Projeto (Azure DevOps)
- **Work Item Discussions & Comments**: Discussões de negócio, refinamentos, esclarecimento de critérios de aceite e histórico vivo de cada card.
- **Pull Request Threads & Reviews**: Pareceres técnicos de revisão (`[NN-persona-id] approve|reject`), trilha de auditoria e segregação estrita de funções (SoD).
- **Project Wiki**: Base durável de documentação de produto, especificações funcionais e arquitetura de referência.

### Pilar 3: Segundo Cérebro Global (Hive-Mind — `D:/Hive-Mind`)
- **Sinapse Global Vault**: Memória corporativa permanente cross-projeto para padrões de engenharia, decisões arquiteturais duradouras e aprendizados entre sessões.
- **Protocolo de Acesso**:
  - Consulta heurística antes de iniciar tarefas: `sinapse_query('<tema>')` para resgatar decisões corporativas prévias.
  - Consolidação durável ao concluir: `sinapse_save_decision` para registrar novos padrões validados.
  - Sessão e manutenção: apenas o orquestrador (`00-delivery-orchestrator`) executa session health e session end.
- O Hive-Mind atua estritamente como cérebro de suporte global; não substitui o banco primário do projeto nem as discussões no Azure DevOps.

---

## 2. Camada Depreciada (Legado em Disco)

- Os arquivos físicos de memória em disco sob `work/<project_id>/memory/` (`shared/summary.md`, `agents/<persona>.md`, `deltas/MEM-*.yaml`) estão **depreciados**.
- A persistência primária do projeto agora reside no `banco/squad.db` e as discussões colaborativas ocorrem diretamente no Azure DevOps.
- O diretório em disco é mantido temporariamente apenas para retrocompatibilidade com work items históricos, não devendo ser utilizado como fonte primária por novos agentes.

---

## 3. Regras de Promoção e Ciclo de Vida

1. **Consulta Prévia**: Antes de iniciar uma tarefa, o especialista consulta a memória primária do projeto via `python scripts/agent_squad.py query-memory --work-item <ID>` e inspeciona as discussões do card no Azure DevOps. Caso o tema envolva padrões globais, consulta o Hive-Mind via `sinapse_query`.
2. **Registro de Fato / Decisão**: Ao concluir uma etapa ou handoff, novos fatos e decisões são gravados na memória do projeto via `python scripts/agent_squad.py record-fact` e resumidos no card do Azure DevOps.
3. **Promoção ao Segundo Cérebro**: Decisões arquiteturais de impacto duradouro e aplicabilidade cross-projeto são propostas para consolidação no Hive-Mind via `sinapse_save_decision`.
4. **Memória Procedural**: Soluções e fluxos repetíveis são sintetizados via `/learn` para `skills/discovery/intake/` e avaliados pelo `18-skill-curator`.

---

## 4. Higiene, Segurança e Verificação

- **Proibição Absoluta de Segredos**: Nunca registrar credenciais, chaves de API, PATs, senhas ou dados pessoais (PII) em qualquer camada de memória.
- **Metadados Obrigatórios**: Toda entrada de memória deve conter `source`, `recorded_at`, `confidence`, `sensitivity` e `invalidates_when`.
- **Memória é Pista, Não Prova**: A memória atua como guia heurístico; o código-fonte, a execução real de testes e as evidências em disco são a única verdade irrefutável.
