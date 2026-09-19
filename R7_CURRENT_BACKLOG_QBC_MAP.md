# R7 — CURRENT BACKLOG / QBC / MATERIALIZATION ARCHITECTURAL MAP
## Stage A — Exhaustive Baseline Audit & Technical Mapping
**Milestone:** `R7 — BACKLOG PLAN + SEMANTIC QBC + MATERIALIZATION`  
**Stage:** `STAGE A — CURRENT BACKLOG/QBC MAP`  
**Role:** `27-platform-engineer` (Kelsey Hightower & Team Topologies — IDP Architect)  
**Date:** 2026-09-18  
**Scope:** Read-Only Exhaustive Codebase Audit  

---

## 1. EXECUTIVE SUMMARY & SYSTEM ARCHITECTURE

O objetivo do marco **R7** é elevar o subsistema de planejamento, decomposição de backlog, consulta prévia à criação (**QBC — Query Before Create**), deduplicação semântica e materialização hierárquica para o padrão corporativo de arquitetura já estabelecido nos marcos R1 a R6.

Esta auditoria exaustiva (**Stage A**) mapeia todas as vias de criação de backlog, contratos existentes no domínio (`scripts/domain/backlog.py`), comportamento operacional da CLI e runtime (`scripts/agent_squad.py`), armazenamento SQLite (`banco/squad.db`), resoluções físicas no disco (`scripts/runtime/work_items/`), conectores legados (`integrations/devops_platform_connector.py`) e o novo plano de sincronização (`scripts/runtime/delivery/`).

### Síntese dos Achados Críticos:
1. **Desconexão entre Domínio R1 e Runtime:** O modelo de domínio `BacklogPlan` e `BacklogPlanItem` (`scripts/domain/backlog.py`), que prevê um ciclo de vida governado (`DRAFT -> VALIDATED -> APPROVED -> MATERIALIZED`), está formalizado, porém **completamente desacoplado do runtime de criação**. Work items continuam sendo criados um a um via CLI ad-hoc (`init-work-item`).
2. **QBC Atual é Primitivo e Restrito:** O QBC atual em `scripts/agent_squad.py:682-708` é um loop puramente sintático executado apenas para os tipos `epic` e `feature`. Ele compara exclusivamente `work_id.lower() == cand_id.lower()`. Não há QBC semântico (busca vetorial, embeddings ou TF-IDF), não há matching por similaridade de título, não há verificação de Story/Task, e a flag `--force` anula sumariamente a checagem.
3. **Detecção no SQLite Ausente para QBC:** A base central SQLite (`banco/squad.db`) possui tabelas de bindings (`delivery_work_item_bindings`), lifecycle (`work_item_lifecycle_state`) e eventos, mas **não é consultada no fluxo de QBC** de `init_work_item()`. A checagem depende unicamente de varredura no filesystem (`WorkItemPathResolver.find_all_work_items()` ou `parent.glob("*")`).
4. **Detecção no Azure DevOps Ausente Pré-Criação:** Antes de emitir uma criação no Azure DevOps, não é realizada nenhuma busca por WIQL, tags, títulos ou similaridade remota. Se o item não tiver sido registrado no binding local, uma nova entidade é criada cegamente no Azure Boards.
5. **Bypass Crítico do Barramento R6:** O método `init_work_item()` em `scripts/agent_squad.py:752-871` contorna integralmente o `DeliverySyncService` e o transactional outbox (`delivery_sync_outbox`), chamando diretamente a classe legada `DevOpsPlatformConnector.create_work_item()` via HTTP síncrono.
6. **Premissa de "1 Produto = 1 Epic" em Scripts Legados:** Enquanto o runtime moderno (`scripts/runtime/work_items/`) já suporta múltiplos épicos, scripts legados de inicialização e automação (`azure_devops_bootstrap.py`, `azure_devops_project_creator.py`) assumem que a raiz de um projeto é um único Epic acoplado.

---

## 2. ALL BACKLOG CREATION PATHS

Atualmente, existem quatro vias principais de criação e materialização de backlog no repositório:

### 2.1 Ponto de Entrada Canônico CLI: `init-work-item`
* **Localização no Código:**
  * Definição do Parser CLI: [`scripts/agent_squad.py:3884-3896`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L3884-L3896)
  * Dispatcher CLI: [`scripts/agent_squad.py:4114-4127`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L4114-L4127)
  * Função Núcleo: [`scripts/agent_squad.py:608-875`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L608-L875) (`AgentSquad.init_work_item()`)
  * Materialização no Disco: [`scripts/agent_squad.py:999-1080`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L999-L1080) (`AgentSquad._init_work_item_unlocked()`)
* **Mecanismo:**
  1. Adquire lock de arquivo interprocesso (`.locks/<project_id>/<work_id>.lock`).
  2. Valida Fibonacci sizing (1, 2, 3, 5, 8) se `story_points` for informado.
  3. Resolve tipo via flag `--type` ou fallback para prefixo via `_legacy_type_for_id(work_id)`.
  4. Valida hierarquia de parentesco (4-tier).
  5. Executa QBC local para `epic` e `feature`.
  6. Calcula o path físico canônico via `WorkItemPathResolver.construct_canonical_path()`.
  7. Cria os diretórios governados (`src`, `tests`, `docs`, `evidence`, `memory`, `reviews`, etc.).
  8. Materializa templates específicos por nível através de `ArtifactMaterializer` (`templates.py`).
  9. Cria `status.yaml` inicial no estado de entrada do ciclo (`config/cycles.yaml`).
  10. Se `--devops` for passado ou `devops.yaml` estiver ativo, invoca o Azure DevOps.

### 2.2 Ponto de Entrada Alternativo: Modo Light (`light-start`)
* **Localização no Código:**
  * CLI: [`scripts/agent_squad.py:3897-3901`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L3897-L3901)
  * Implementação: [`scripts/agent_squad.py:885-916`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L885-L916) (`AgentSquad.init_light_item()`)
* **Mecanismo:**
  * Cria um arquivo markdown flat isolado em `work/<project_id>/light/<work_id>.md`.
  * Não cria diretório de work item, não gera `status.yaml`, não roda QBC, não integra com Azure DevOps.

### 2.3 Materialização em Lote / Scripts de Onda (Wave Scripts)
* **Localização no Código:**
  * [`scripts/materialize_xquads_wave_backlog.py:88-109`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/materialize_xquads_wave_backlog.py#L88-L109)
* **Mecanismo:**
  * Lê um manifesto estruturado de histórias e tarefas.
  * Executa subprocessos chamando repetidamente:
    `python scripts/agent_squad.py --root <root> init-work-item --id <id> --risk <risk>`
  * Falta atomicidade: se o script falhar no meio de uma onda de 104 tarefas, as tarefas anteriores já foram criadas sem rollback.

### 2.4 Bootstrap de Árvore Azure DevOps Direto
* **Localização no Código:**
  * [`scripts/azure_devops_bootstrap.py:28-69`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/azure_devops_bootstrap.py#L28-L69) (`create_work_item_tree()`)
* **Mecanismo:**
  * Cria diretamente no Azure DevOps um Epic e, sob ele, Stories e Tasks em cascata via `AzureDevOpsClient.create_work_item()`.
  * Grava o `devops_id` do Epic de volta no `status.yaml` local.
  * Ignora o runtime `init_work_item()`, ignorando templates, gates e contenção física do Squad.

### 2.5 Contrato de Domínio R1 Desconectado: `BacklogPlan`
* **Localização no Código:**
  * [`scripts/domain/backlog.py:15-137`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/domain/backlog.py#L15-L137)
* **Status:** `NOT HOOKED / DISCONNECTED`
  * As entidades puras `BacklogPlan`, `BacklogPlanItem` e os estados `DRAFT -> VALIDATED -> APPROVED -> MATERIALIZED` foram implementados e testados no marco R1, mas **não possuem CLI, API de persistência ou orquestrador que os materializem no filesystem ou no Azure**.

---

## 3. ALL QBC (QUERY BEFORE CREATE) IMPLEMENTATIONS

### 3.1 Implementação Operacional Atual
* **Arquivo:** [`scripts/agent_squad.py:682-708`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L682-L708)
* **Código Fonte Exato:**
```python
682:         # Query Before Create (QBC) protocol for Epics and Features
683:         if resolved_type in {"epic", "feature"} and not force:
684:             all_candidate_paths = []
685:             if self.project_name:
686:                 try:
687:                     resolver = WorkItemPathResolver(self.root, self.project_name)
688:                     all_candidate_paths = resolver.find_all_work_items()
689:                 except Exception:
690:                     pass
691:             if not all_candidate_paths:
692:                 all_candidate_paths = [p for p in parent.glob("*") if p.is_dir()]
693: 
694:             for candidate_path in all_candidate_paths:
695:                 if (candidate_path / "status.yaml").exists():
696:                     try:
697:                         cand_status = read_yaml(candidate_path / "status.yaml")
698:                         cand_type = str(cand_status.get("type", "")).lower()
699:                         if cand_type == resolved_type:
700:                             cand_id = candidate_path.name
701:                             if work_id.lower() == cand_id.lower():
702:                                 raise SquadError(
703:                                     f"QBC Violation: Duplicate {resolved_type} detected ({cand_id}). Use --force to override."
704:                                 )
705:                     except Exception as exc:
706:                         if isinstance(exc, SquadError) and "QBC Violation" in str(exc):
707:                             raise
708:                         pass
```

### 3.2 Limitações e Defeitos do QBC Atual:
1. **Restrito a Epics e Features:** `story`, `task`, `bug`, `spike`, `incident` estão excluídos da checagem (`if resolved_type in {"epic", "feature"}`). Duplicatas de histórias e tarefas passam sem qualquer barreira.
2. **Matching Exclusivamente por ID Idêntico:** O matching testa apenas `work_id.lower() == cand_id.lower()`. Duas histórias ou épicos com títulos e escopos idênticos, mas IDs diferentes (ex: `EPIC-001` "Migração de Banco" e `EPIC-002` "Migração de Banco de Dados"), são considerados distintos.
3. **Zero Semântica / NLP:** Não há tokenização, TF-IDF, embeddings, matching fuzzy de títulos ou busca semântica em memórias (`squad.db` ou `Hive-Mind`).
4. **Sem Consulta Remota:** Não verifica se o card já existe no Azure Boards.
5. **Vulnerabilidade a Flag `--force`:** `--force` desativa o bloco por completo sem gerar log de auditoria estruturado.

---

## 4. LOCAL DUPLICATE DETECTION (`work/<project_id>/`)

### 4.1 Mecanismo de Busca no Filesystem
* **Arquivos e Linhas:**
  * [`scripts/agent_squad.py:684-692`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L684-L692)
  * [`scripts/runtime/work_items/paths.py:162-176`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/runtime/work_items/paths.py#L162-L176) (`WorkItemPathResolver.find_all_work_items()`)
* **Como funciona:**
  1. `WorkItemPathResolver.find_all_work_items()` executa um `self.project_work_dir.rglob("status.yaml")`.
  2. Para cada ocorrência, valida a contenção de path (`PathContainmentGuard.validate_work_path`).
  3. Retorna a lista de diretórios de itens.
  4. Em `init_work_item()`, lê cada `status.yaml` do disco via PyYAML.
  5. Se o diretório alvo já existir fisicamente na hora do `mkdir(parents=True, exist_ok=False)` ([`agent_squad.py:1031`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L1031)), o Python lança `FileExistsError`, abortando a criação.

### 4.2 Avaliação Técnica:
* **Prós:** Garante que o disco não seja sobrescrito silenciosamente caso o ID coincida.
* **Contras:** Leitura intensiva de I/O em disco (múltiplos `rglob` e parses de YAML a cada item criado). Escala mal para repositórios com centenas de work items.

---

## 5. SQLITE DUPLICATE DETECTION (`banco/squad.db`)

### 5.1 Tabelas Existentes no SQLite (`banco/squad.db`)
Verificadas via inspeção de schema de produção:
```
['symbols', 'sqlite_sequence', 'dependencies', 'token_metrics', 'trajectory_logs',
 'quorum_votes', 'ops_recovery', 'workflow_metrics', 'memory_facts',
 'work_item_lifecycle_state', 'lifecycle_history', 'events', 'event_deliveries',
 'project_bindings', 'project_binding_history', 'delivery_work_item_bindings',
 'delivery_sync_outbox', 'delivery_inbound_events']
```

### 5.2 Uso do SQLite para QBC
* **Status:** `ABSENT / NOT IMPLEMENTED`
* **Detalhes da Inspeção:**
  * O arquivo [`scripts/agent_squad.py`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py) utiliza o banco SQLite exclusivamente para:
    1. Gravação de fatos de memória (`memory_facts`) em [`agent_squad.py:3200-3218`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L3200-L3218).
    2. Lookup de binding de entrega (`delivery_work_item_bindings`) em [`agent_squad.py:784-789, 845-860`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L784-L789).
  * **Nenhuma query SQL é emitida contra `banco/squad.db` para validação de duplicatas ou QBC antes de criar um work item.**
  * As tabelas `work_item_lifecycle_state` e `delivery_work_item_bindings` poderiam atuar como índice O(1) rápido de work items existentes, mas são ignoradas pelo QBC.

---

## 6. AZURE DEVOPS DUPLICATE DETECTION (REMOTE BOARDS)

### 6.1 Mecanismo Atual de Consulta ao Azure Pré-Criação
* **Status:** `ABSENT / NOT IMPLEMENTED`
* **Inspeção de Código:**
  * [`scripts/agent_squad.py:739-830`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L739-L830): Não executa nenhuma chamada GET ou consulta WIQL antes de invocar `connector.create_work_item()`.
  * [`integrations/devops_platform_connector.py:363-397`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/integrations/devops_platform_connector.py#L363-L397): A função `create_work_item()` monta o patch JSON e envia imediatamente um `POST /_apis/wit/workitems/${type}`. Não realiza query prévia de itens com mesmo título ou tag na Area Path.
  * [`scripts/runtime/delivery/azure_discovery.py`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/runtime/delivery/azure_discovery.py): Fornece portas e métodos para consultar projetos, repos, áreas e iterações, mas **não implementa consulta a work items remotos via WIQL**.
  * [`scripts/runtime/delivery/sync.py:148-158`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/runtime/delivery/sync.py#L148-L158): Apenas verifica se `delivery_work_item_bindings` no SQLite local já possui um `ado_id`. Se a tabela local estiver vazia ou recém-criada, ela presume que o item não existe no Azure e agenda um `CREATE`.

---

## 7. HIERARCHY MATERIALIZATION (EPIC -> FEATURE -> STORY -> TASK)

### 7.1 Travessia e Validação Hierárquica
* **Validação de Invariantes:**
  * Regras de Domínio: [`scripts/domain/work_items.py:140-160`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/domain/work_items.py#L140-L160) (`WorkHierarchy.ALLOWED_PARENTS`)
  * Runtime de Validação: [`scripts/runtime/work_items/hierarchy.py:31-41`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/runtime/work_items/hierarchy.py#L31-L41) (`validate_parent_child()`)
  * Verificação no `init_work_item`: [`scripts/agent_squad.py:641-680`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L641-L680)
* **Regras Estritas de Parentesco:**
  * `EPIC`: Não pode ter pai (`parent_id` proibido; [`agent_squad.py:642-643`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L642-L643)).
  * `FEATURE`: Requer pai do tipo `epic` ([`agent_squad.py:645-646`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L645-L646)).
  * `STORY` / `PBI`: Requer pai do tipo `feature` ([`agent_squad.py:647-648`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L647-L648)).
  * `TASK`: Requer pai do tipo `story` ([`agent_squad.py:649-650`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L649-L650)).
  * `BUG`: Aceita pai `story` ou `feature` ([`work_items.py:145`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/domain/work_items.py#L145)).
  * `SPIKE`: Aceita pai `feature` ou `epic` ([`work_items.py:146`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/domain/work_items.py#L146)).

### 7.2 Estrutura Física em Disco
* **Implementação:** [`scripts/runtime/work_items/paths.py:30-37, 122-161`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/runtime/work_items/paths.py#L122-L161) (`WorkItemPathResolver.construct_canonical_path()`)
* **Organização dos Containers Plurais:**
  ```
  work/<project_id>/
  └── EPIC-001/
      ├── status.yaml
      ├── epic.md
      ├── product-goal.md
      └── features/
          └── FEATURE-001/
              ├── status.yaml
              ├── feature-spec.md
              ├── component-design.md
              └── stories/
                  └── STORY-001/
                      ├── status.yaml
                      ├── user-story.md
                      ├── acceptance-criteria.md
                      └── tasks/
                          └── TASK-0001/
                              ├── status.yaml
                              └── task-scope.md
  ```
* **Fallback Transparente:**
  * O método `resolve_item_path` ([`paths.py:72-120`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/runtime/work_items/paths.py#L72-L120)) suporta o layout flat legado (`work/<project_id>/<ID>/status.yaml`) através de busca em 2 camadas, garantindo compatibilidade retroativa.

---

## 8. PARENT HANDLING (LOCAL VS AZURE DEVOPS)

### 8.1 Resolução do Pai no Runtime Local
* **Arquivo:** [`scripts/agent_squad.py:652-670`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L652-L670)
* O pai é resolvido via `WorkItemPathResolver.resolve_item_path(parent_id)`. Se não encontrado, tenta fallback relativo `parent / parent_id`.
* Lê o `status.yaml` do pai para conferir se o tipo bate com o esperado (`parent_type == expected_parent_type`).

### 8.2 Passagem do Pai para o Azure DevOps
* **Arquivo:** [`scripts/agent_squad.py:781-806`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L781-L806)
* **Algoritmo de Resolução do ADO ID do Pai:**
  1. Consulta a tabela `delivery_work_item_bindings` via `SqliteBindingRepository.get_work_item_binding(str(parent_id))`. Se houver `ado_id`, usa-o.
  2. Fallback: Lê o `status.yaml` do pai no disco (`status.get("devops_id")`).
  3. Se ainda assim não encontrar `parent_ado_id`, passa o próprio `parent_id` sintético como fallback (`resolved_parent = parent_ado_id or parent_id`).
* **Envio para a API do Azure DevOps:**
  * Em [`integrations/devops_platform_connector.py:380-387`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/integrations/devops_platform_connector.py#L380-L387) e em [`scripts/runtime/delivery/azure_writer.py:307-320`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/runtime/delivery/azure_writer.py#L307-L320):
    * O vínculo hierárquico é adicionado como relação reversa:
      ```json
      {
        "op": "add",
        "path": "/relations/-",
        "value": {
          "rel": "System.LinkTypes.Hierarchy-Reverse",
          "url": "{org}/{project}/_apis/wit/workItems/{parent_ado_id}",
          "attributes": { "comment": "Canonical parent link" }
        }
      }
      ```
* **Ponto Crítico / Risco Identificado:** Se `parent_ado_id` não for numérico (caso do fallback sintético `parent_id="FEATURE-001"`), a API do Azure DevOps falha com HTTP 400 (`TF400898` ou erro de URL inválida em `/workItems/FEATURE-001`). O `DeliverySyncService` em R6 ([`sync.py:159-170`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/runtime/delivery/sync.py#L159-L170)) já bloqueia isso com `OrphanWorkItemViolationError`, mas o `agent_squad.py` não usa o `DeliverySyncService`.

---

## 9. TITLE MATCHING VS ID MATCHING & O HISTÓRICO DO `split("-")[0]`

### 9.1 O Histórico do Bug R0-WORK-005
* **Defeito Original:**
  * No código legado inicial, a validação de QBC realizava:
    `work_id.split('-')[0] == cand_id.split('-')[0]`
  * Como `EPIC-001.split('-')[0]` resulta em `'EPIC'` e `EPIC-002.split('-')[0]` também resulta em `'EPIC'`, o sistema considerava `EPIC-002` como duplicata de `EPIC-001`, bloqueando a criação de qualquer segundo épico no projeto!
* **Correção Efetuada no Marco R3:**
  * Diagnosticado em [`scripts/tests/diagnostics/r0_workitem_ado_contract_red.py:90-104`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/tests/diagnostics/r0_workitem_ado_contract_red.py#L90-L104).
  * Corrigido em [`scripts/agent_squad.py:701`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L701):
    `if work_id.lower() == cand_id.lower(): raise SquadError(...)`
  * Validado pelo teste [`scripts/tests/test_r3_work_item_hierarchy.py:192-206`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/tests/test_r3_work_item_hierarchy.py#L192-L206).

### 9.2 Situação Atual: Title Matching vs ID Matching
* **ID Matching:**
  * O sistema possui suporte canônico via `CanonicalIdService` (`scripts/runtime/work_items/ids.py`) para normalizar aliases (`FEAT-` -> `FEATURE-`, `US-` -> `STORY-`, `TK-` -> `TASK-`).
* **Title Matching:**
  * **Completamente inexistente no runtime.**
  * O parâmetro `title` sequer é passado para o método `init_work_item()`! O método recebe apenas `work_id` (`scripts/agent_squad.py:608`), usando o próprio ID como título em status e chamadas DevOps (`title=work_id` em [`agent_squad.py:810`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L810)).
  * Não há comparação léxica (Levenshtein, Jaccard) nem semântica entre títulos de itens existentes e novos itens planejados.

---

## 10. LEGACY SEMANTIC ASSUMPTIONS & HARDCODED PREMISES

### 10.1 A Premissa "1 Produto = 1 Epic"
* **No Runtime R3/R6:**
  * A premissa foi quebrada e refatorada com sucesso. `WorkItemPathResolver` permite que múltiplos épicos coexistam lado a lado sob `work/<project_id>/` (ex: `EPIC-001`, `EPIC-002`).
* **Nos Scripts Utilitários Legados:**
  * [`scripts/azure_devops_bootstrap.py:28-69`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/azure_devops_bootstrap.py#L28-L69): Assume explicitamente que todo projeto possui **apenas 1 Epic raiz** (`--epic-title`), subordinando todas as histórias diretamente a este único épico.
  * [`scripts/materialize_xquads_wave_backlog.py:97`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/materialize_xquads_wave_backlog.py#L97): Aponta diretamente para um épico fixo: `work/agent_squad/EPIC-SQUAD-EVOLUTION-20260818/`.

### 10.2 Tokens e Premissas Legadas do Deepvision / Arthemis
* **Auditoria de Tokens Proibidos:**
  * Em conformidade com os marcos R1 e R5, a lista de tokens proibidos (`cbvgas`, `arthemis`, `deepvision`, `test_root`, `test_item`) é ativamente rejeitada pelos modelos de domínio (`scripts/domain/project.py:18` e testes de autoridade).
  * **Resultado da Busca:** Não foram encontrados vazamentos ou acoplamentos ativos do Deepvision no código funcional de `scripts/runtime/` ou `scripts/agent_squad.py`. O desacoplamento de produto foi concluído nas etapas anteriores.

---

## 11. DIRECT AZURE CREATION BYPASSING R6 (O BYPASS ATUAL)

### 11.1 Mapeamento do Bypass no Ponto Central (`init_work_item`)
O marco R6 estabeleceu a arquitetura formal do plano de sincronização (`scripts/runtime/delivery/sync.py`) com fila de outbox transacional (`delivery_sync_outbox`), idempotência e suporte a testes offline determinísticos.

Contudo, a auditoria constatou que **o comando central `init-work-item` ainda contorna totalmente o R6**:

| Componente Chamado | Linha de Código | Por Que é um Bypass |
|---|---|---|
| `from devops_platform_connector import DevOpsPlatformConnector` | [`scripts/agent_squad.py:757`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L757) | Carrega o conector REST legado não transacional. |
| `ado_item = connector.create_work_item(...)` | [`scripts/agent_squad.py:809`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L809) | Executa HTTP POST direto síncrono para a API do Azure DevOps. |
| `_repo.upsert_work_item_binding(...)` | [`scripts/agent_squad.py:860`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L860) | Grava diretamente no SQLite como `SYNCED` após o fato, sem passar pelo transactional outbox. |

### 11.2 Outros Pontos de Criação Direta Identificados:
1. [`scripts/azure_devops_bootstrap.py:47-67`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/azure_devops_bootstrap.py#L47-L67): Chama diretamente `AzureDevOpsClient.create_work_item()`.
2. [`integrations/devops_platform_connector.py:399-412`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/integrations/devops_platform_connector.py#L399-L412) (`create_split_stories`): Cria fatias de histórias diretamente via REST sem registro de binding transacional.
3. [`scripts/agent_squad.py:3113-3118`](file:///c:/Users/miche/OneDrive/Documentos/agent_squad/scripts/agent_squad.py#L3113-L3118) (`advance_state`): Executa mutação direta de estado no Azure via `connector.client.send("PATCH", ...)` sem passar pelo `DeliverySyncService.enqueue_outbound_update()`.

---

## 12. GAPS & REQUISITOS TÉCNICOS PARA O MARCO R7

Com base no mapeamento exaustivo do Stage A, a arquitetura do marco R7 deve resolver os seguintes gaps estruturais:

1. **Ativação e Persistência do `BacklogPlan` (Domain Engine):**
   * Conectar `BacklogPlan` e `BacklogPlanItem` (`scripts/domain/backlog.py`) ao SQLite e filesystem.
   * Exigir que a criação de itens em lote passe pelo ciclo formal `DRAFT -> VALIDATED -> APPROVED -> MATERIALIZED`.
2. **Motor de QBC Semântico Multicamadas:**
   * **Camada 1 (Sintática Local):** Checagem no SQLite (`banco/squad.db`) por canonical ID e legacy aliases em tempo O(1).
   * **Camada 2 (Semântica Textual):** Busca por similaridade de títulos e escopos (Levenshtein / Token Overlap / Embeddings) em itens do mesmo projeto para evitar duplicações conceituais de histórias/épicos.
   * **Camada 3 (Consulta Remota Azure Boards):** Execução de query WIQL prévia por título e tags na Area Path antes de aprovar novos cards.
3. **Erradicação do Bypass do Azure DevOps:**
   * Substituir as chamadas diretas a `DevOpsPlatformConnector.create_work_item()` em `agent_squad.py:752-871` por enfileiramento via `DeliverySyncService.enqueue_outbound_create()`.
4. **Materialização Atômica e Transacional:**
   * Garantir que a materialização física dos diretórios e arquivos só ocorra após a aprovação formal do `BacklogPlan` (`plan.assert_can_materialize()`), eliminando diretórios órfãos gerados por falhas intermediárias.
