# R6 — STAGE A: CURRENT AZURE WRITE / SYNC MAP
## Mapeamento Arquitetural Exaustivo dos Caminhos Atuais de Escrita, Criação de Recursos, Sincronização, Retries e Falhas de Governança com Azure DevOps

**Document ID:** `DOC-MAP-R6-CURRENT-AZURE-SYNC`  
**Milestone:** `R6 — AZURE DEVOPS WORKFLOW + BIDIRECTIONAL SYNC`  
**Stage:** `STAGE A — CURRENT AZURE WRITE/SYNC MAP`  
**Data:** 2026-09-18  
**Autor / Agente Responsável:** `27-platform-engineer` (Kelsey Hightower & Team Topologies — Internal Developer Platform Architect)  
**Status:** `COMPLETED`  
**Operação:** `READ-ONLY AUDIT & DISCOVERY` (Zero mutações em código de produção; Zero mutações remotas em Azure DevOps)  

---

## 1. SUMÁRIO EXECUTIVO & ESCOPO DA AUDITORIA

Este documento consolida o mapeamento exaustivo, factual e auditado (com referências exatas de `arquivo:linha`) de todos os fluxos de escrita, criação de recursos, mutação de work items, políticas de retry, engolimento de exceções, modelagem de parentesco e mecanismos de sincronização atualmente presentes no ecossistema **Agent Squad**.

A inspeção cobriu a totalidade dos componentes do Ground Truth:
1. `scripts/azure_devops_lifecycle.py`
2. `scripts/azure_devops_bootstrap.py`
3. `scripts/azure_devops_project_creator.py`
4. `scripts/azure_devops_project_setup.py`
5. `scripts/azure_devops_repo_importer.py`
6. `integrations/devops_platform_connector.py`
7. `integrations/mcp_devops_client.py`
8. `scripts/agent_squad.py`
9. `scripts/runtime/delivery/` (`azure_discovery.py`, `binding.py`, `repository.py`, `errors.py`)
10. `scripts/domain/sync.py`
11. `docs/architecture/R5_PROJECT_DELIVERY_BINDING.md`

### Diagnóstico de Alto Nível
O runtime atual opera com uma grave cisão arquitetural:
* **Desacoplamento em R5:** O marco R5 estabeleceu formalmente o princípio de desacoplamento entre Software Product e Azure DevOps Team Project (um produto é mapeado para Git Repo, Team e Area Path *dentro* de um Team Project compartilhado já existente; chamadas a `POST /_apis/projects` são categoricamente proibidas pela regra R0-ADO-002).
* **Violação Ativa no Legado:** Os scripts `azure_devops_lifecycle.py` e `azure_devops_project_creator.py` continuam implementando a tentativa de criar um novo Team Project no Azure (`POST /_apis/projects`) a cada ciclo de setup de produto, e acionam `DELETE /_apis/projects/{id}` em caso de rollback.
* **Engolimento Sistemático de Exceções:** Quase todas as integrações de mutação em `agent_squad.py` (`init_work_item`, `advance_state`, `memory_delta`) capturam `except Exception:` com `print(WARN)` ou `logger.warning`, falhando de modo aberto (*fail-open* silencioso). Se a rede cair ou o token PAT expirar, o estado local avança enquanto o Azure DevOps permanece desatualizado, gerando *drift* desgovernado sem outbox ou fila de retry.
* **Contratos R1 Desconectados da Execução:** O modelo de domínio canônico `scripts/domain/sync.py` (`AdoWorkItemBinding`, `SyncState`, `ReconciliationDecision`) foi especificado no marco R1, porém encontra-se **100% desconectado** dos scripts operacionais. A correlação entre work items locais e remotos ainda reside precariamente em um único campo escalar `devops_id` dentro do arquivo local `status.yaml`.

---

## 2. MAPA EXAUSTIVO DE CAMINHOS DE ESCRITA NO AZURE (WRITE PATHS)

Tabela canônica de todas as funções, classes e métodos que disparam chamadas HTTP mutantes (`POST`, `PATCH`, `PUT`, `DELETE`) ou CLIs de escrita contra o Azure DevOps REST API (v7.1).

| # | Arquivo:Linha | Classe / Função | Método HTTP | Endpoint Azure DevOps | Objetivo / Payload |
|---|---|---|---|---|---|
| **W01** | `scripts/azure_devops_project_creator.py:227` | `create_project()` | `POST` | `/_apis/projects?api-version=7.1` | Criação de Team Project no Azure. Violação direta de R0-ADO-002. |
| **W02** | `scripts/azure_devops_repo_importer.py:233` | `RepoImportClient.import_repo()` | `POST` | `/_apis/git/repositories?api-version=7.1` | Criação e importação de repositório Git (`isImport=True`). |
| **W03** | `scripts/azure_devops_lifecycle.py:303` | `AzureDevOpsLifecycle._rollback()` | `DELETE` | `/_apis/projects/{project_id}?api-version=7.1` | Destruição de Team Project em caso de falha nas fases 2, 3 ou 4. |
| **W04** | `scripts/azure_devops_project_setup.py:167` | `AzureDevOpsProjectSetup.apply_areas()` | `POST` | `/_apis/wit/classificationnodes/areas?api-version=7.1` | Criação de nó de Area Path (`{"name": name}`). |
| **W05** | `scripts/azure_devops_project_setup.py:189` | `AzureDevOpsProjectSetup.apply_iterations()` | `PATCH` | `/_apis/wit/classificationnodes/iterations/{name}?api-version=7.1` | Atualização de datas (`startDate`, `finishDate`) em nó de iteração existente. |
| **W06** | `scripts/azure_devops_project_setup.py:192` | `AzureDevOpsProjectSetup.apply_iterations()` | `POST` | `/_apis/wit/classificationnodes/iterations?api-version=7.1` | Criação de novo nó de iteração na árvore de classificação. |
| **W07** | `scripts/azure_devops_project_setup.py:241` | `AzureDevOpsProjectSetup.apply_team_iterations()` | `PATCH` | `/_apis/work/teamsettings?api-version=7.1` | Configura `backlogIteration` do Team para o nó raiz do projeto. |
| **W08** | `scripts/azure_devops_project_setup.py:255` | `AzureDevOpsProjectSetup.apply_team_iterations()` | `POST` | `/_apis/work/teamsettings/iterations?api-version=7.1` | Vincula nó de iteração existente ao backlog do Team. |
| **W09** | `scripts/azure_devops_project_setup.py:268` | `AzureDevOpsProjectSetup.assign_iteration_to_team()` | `POST` | `/{project_id}/{team_id}/_apis/work/teamsettings/iterations?api-version=7.1` | Associação direta de sprint/iteração a um time. |
| **W10** | `scripts/azure_devops_project_setup.py:300` | `AzureDevOpsProjectSetup.apply_queries()` | `POST` | `/{project}/_apis/wit/queries/{path}?api-version=7.1` | Criação de pasta de queries salvas (`isFolder=True`). |
| **W11** | `scripts/azure_devops_project_setup.py:382` | `AzureDevOpsProjectSetup.apply_queries()` | `POST` | `/{project}/_apis/wit/queries/{folder}?api-version=7.1` | Criação de queries salvas WIQL (`Active`, `WIP by State`, etc.). |
| **W12** | `scripts/azure_devops_project_setup.py:417` | `AzureDevOpsProjectSetup.apply_team_settings()` | `PATCH` | `/_apis/work/teamsettings?api-version=7.1` | Configuração de `bugsBehavior` e `backlogVisibilities`. |
| **W13** | `scripts/azure_devops_project_setup.py:424` | `AzureDevOpsProjectSetup.apply_team_settings()` | `PATCH` | `/_apis/work/teamsettings/teamfieldvalues?api-version=7.1` | Configura Team Field Values (`defaultValue`, `includeChildren`). |
| **W14** | `scripts/azure_devops_project_setup.py:515` | `AzureDevOpsProjectSetup.apply_board_columns()` | `PUT` | `/_apis/work/boards/Stories/columns?api-version=7.1` | Substituição atômica das colunas do Kanban board (Stories). |
| **W15** | `scripts/azure_devops_project_setup.py:584` | `AzureDevOpsProjectSetup.apply_portfolio_board_columns()` | `PUT` | `/_apis/work/boards/{Features|Epics}/columns?api-version=7.1` | Substituição atômica de colunas nos boards de portfólio. |
| **W16** | `scripts/azure_devops_project_setup.py:613` | `AzureDevOpsProjectSetup.apply_board_wip()` | `PUT` | `/_apis/work/boards/Stories/columns?api-version=7.1` | Atualização de limites de WIP (`itemLimit`) nas colunas do board. |
| **W17** | `scripts/azure_devops_project_setup.py:629` | `AzureDevOpsProjectSetup.apply_card_colors()` | `PATCH` | `/_apis/work/boards/{board}/cardrulesettings?api-version=7.1` | Reset ou injeção de regras de estilo/cores de cartões no board. |
| **W18** | `scripts/azure_devops_project_setup.py:701` | `AzureDevOpsProjectSetup.apply_card_fields()` | `PUT` | `/_apis/work/boards/Stories/cardsettings?api-version=7.1` | Configura campos adicionais exibidos no card (StoryPoints, etc.). |
| **W19** | `scripts/azure_devops_project_setup.py:723` | `AzureDevOpsProjectSetup.apply_notifications()` | `PUT` | `/_apis/notification/Subscriptions/{subId}/UserSettings/{userId}?api-version=7.1` | Atualiza opt-out de notificações para usuário master. |
| **W20** | `scripts/azure_devops_project_setup.py:760` | `AzureDevOpsProjectSetup.apply_notifications()` | `POST` | `/_apis/notification/subscriptions?api-version=7.1` | Criação de assinaturas de notificação de eventos (work items, PRs). |
| **W21** | `scripts/azure_devops_project_setup.py:799` | `AzureDevOpsProjectSetup.apply_branch_policies()` | `POST` | `/{project}/_apis/policy/configurations?api-version=7.1` | Criação de Branch Policy (Mínimo de revisores, linkagem de card). |
| **W22** | `scripts/azure_devops_project_setup.py:879` | `AzureDevOpsProjectSetup.apply_delivery_plans()` | `POST` | `/_apis/work/plans?api-version=7.1` | Criação de Delivery Plan cross-team. |
| **W23** | `scripts/azure_devops_project_setup.py:904` | `AzureDevOpsProjectSetup.apply_wiki()` | `POST` | `/_apis/wiki/wikis?api-version=7.1` | Provisionamento de Project Wiki raiz. |
| **W24** | `scripts/azure_devops_project_setup.py:922` | `AzureDevOpsProjectSetup.apply_wiki()` | `PUT` | `/_apis/wiki/wikis/{wiki_id}/pages?path={slug}&api-version=7.1` | Criação/atualização de páginas markdown na wiki do projeto. |
| **W25** | `scripts/azure_devops_project_setup.py:959` | `AzureDevOpsProjectSetup.apply_dashboards()` | `POST` | `/_apis/dashboard/dashboards?api-version=7.1-preview.3` | Criação de dashboards de time e de projeto. |
| **W26** | `scripts/azure_devops_project_setup.py:991` | `AzureDevOpsProjectSetup.create_team()` | `POST` | `/_apis/projects/{project_id}/teams?api-version=7.1` | Criação de engineering teams adicionais. |
| **W27** | `scripts/azure_devops_project_setup.py:1038` | `AzureDevOpsProjectSetup.apply_swimlanes()` | `PUT` | `/_apis/work/boards/Stories/rows?api-version=7.1` | Atualização de raias (swimlanes/rows) no board de Stories. |
| **W28** | `scripts/azure_devops_project_setup.py:1172` | `AzureDevOpsProjectSetup.apply_security_acls()` | `POST` | `/_apis/accesscontrollists/{namespace_id}?api-version=7.1` | Aplicação de Access Control Lists (ACLs de segurança). |
| **W29** | `scripts/azure_devops_project_setup.py:1221` | `AzureDevOpsProjectSetup.create_service_connections()` | `POST` | `/_apis/serviceendpoint/endpoints?api-version=7.1` | Criação de Service Endpoints (ARM, GitHub, Docker, K8s). |
| **W30** | `integrations/devops_platform_connector.py:337` | `AzureDevOpsClient.update_item_state()` | `PATCH` | `/_apis/wit/workitems/{id}?api-version=7.1` | Atualização de `System.State`, histórico e tags em work item. |
| **W31** | `integrations/devops_platform_connector.py:343` | `AzureDevOpsClient.add_work_item_comment()` | `PATCH` | `/_apis/wit/workitems/{id}?api-version=7.1` | Adiciona comentário append-only no histórico (`System.History`). |
| **W32** | `integrations/devops_platform_connector.py:361` | `AzureDevOpsClient.update_sizing()` | `PATCH` | `/_apis/wit/workitems/{id}?api-version=7.1` | Registra Story Points ou Effort numérico no card. |
| **W33** | `integrations/devops_platform_connector.py:389` | `AzureDevOpsClient.create_work_item()` | `POST` | `/_apis/wit/workitems/${type}?api-version=7.1` | Criação atômica de work item com JSON Patch payload. |
| **W34** | `integrations/devops_platform_connector.py:427` | `AzureDevOpsClient.create_pull_request()` | `POST` | `/_apis/git/repositories/{repo}/pullrequests?api-version=7.1` | Abertura de PR em Azure Repos com vínculo a work items. |
| **W35** | `scripts/agent_squad.py:3052` | `advance_state()` (chamada inline direta) | `PATCH` | `/_apis/wit/workitems/{devops_id}?api-version=7.1` | Atualização de `System.State` no avanço de fase SDLC. |
| **W36** | `scripts/agent_squad.py:3168` | `memory_delta()` (chamada inline direta) | `PATCH` | `/_apis/wit/workitems/{devops_id}?api-version=7.1` | Publica comentário de fato de memória em `System.History`. |

---

## 3. MAPA DE CRIAÇÃO DE RECURSOS (RESOURCE CREATION PATHS)

| Recurso | Status de Implementação | Módulo e Ponto de Entrada | Mecanismo e Detalhes | Conformidade R5 / Arquitetural |
|---|---|---|---|---|
| **Team Project** | `LEGACY VIOLATION` | `scripts/azure_devops_project_creator.py:155` (`create_project`) | REST `POST /_apis/projects?api-version=7.1`. Envia nome, descrição e template de processo (Scrum, Agile, CMMI). Aguarda polling até `wellFormed`. | **VIOLAÇÃO GRAVE DE R0-ADO-002.** Team Projects são contêineres corporativos compartilhados. O Agent Squad está proibido de provisionar Team Projects por produto. |
| **Git Repository** | `IMPLEMENTED` | `scripts/azure_devops_repo_importer.py:214` (`import_repo`) | REST `POST /_apis/git/repositories?api-version=7.1` com `isImport=True` e `remoteUrl`. Aguarda polling até `isImport=False`. | Válido sob R6 (desde que executado no Team Project já existente e resolvido pelo R5). |
| **Engineering Team** | `IMPLEMENTED` | `scripts/azure_devops_project_setup.py:963` (`create_team`) | REST `POST /_apis/projects/{project_id}/teams?api-version=7.1`. Lê lista `teams` de `devops.yaml`. Idempotente (verifica nomes existentes via GET). | Válido sob R6. |
| **Area Path** | `IMPLEMENTED` | `scripts/azure_devops_project_setup.py:146` (`apply_areas`) | REST `POST /_apis/wit/classificationnodes/areas?api-version=7.1`. Cria nós na árvore de classificação. | Parcialmente alinhado: cria apenas nós planos (profundidade 1 sob a raiz). Requer reconciliação com o `area_path` hierárquico do R5. |
| **Iteration Path** | `IMPLEMENTED` | `scripts/azure_devops_project_setup.py:177` (`apply_iterations`) | REST `POST /_apis/wit/classificationnodes/iterations?api-version=7.1`. Cria nós com `startDate` e `finishDate`. | Parcialmente alinhado: cria nós no projeto, mas dependente de `apply_team_iterations` para vincular ao backlog do time. |
| **Board & Colunas** | `IMPLEMENTED` | `scripts/azure_devops_project_setup.py:430` (`apply_board_columns`) | REST `PUT /_apis/work/boards/Stories/columns?api-version=7.1`. Cria e mapeia as 7 colunas canônicas do SDLC com preservação de IDs para incoming/outgoing. | Alinhado com a taxonomia do SDLC (Blueprint, Scaffolding, Implementation, Review, Validation, Governance, Done). |
| **Service Hooks (Inbound/Outbound)** | `ABSENT / NOT IMPLEMENTED` | *Nenhum arquivo executável* | **Nenhum código existente.** Não há chamadas a `POST /_apis/hooks/subscriptions` para registrar Service Hooks no Azure DevOps. | **MISSING.** Requer implementação no marco R6. |

---

## 4. MAPA DE CRIAÇÃO E ATUALIZAÇÃO DE WORK ITEMS

### 4.1 Criação de Work Items (`create_work_item`)
* **Implementação Canônica:** `integrations/devops_platform_connector.py:363` (`AzureDevOpsClient.create_work_item`).
* **Endpoint HTTP:** `POST {base_url}/wit/workitems/${item_type}?api-version=7.1`
* **Content-Type:** `application/json-patch+json`
* **Payload JSON Patch construído:**
  ```json
  [
    {"op": "add", "path": "/fields/System.Title", "value": "US-AUTH-001"},
    {"op": "add", "path": "/fields/System.Description", "value": "..."},
    {"op": "add", "path": "/fields/System.AreaPath", "value": "Core-Banking\\Payments"},
    {"op": "add", "path": "/fields/System.IterationPath", "value": "Core-Banking\\Sprint 1"},
    {"op": "add", "path": "/fields/Microsoft.VSTS.Scheduling.StoryPoints", "value": 5},
    {
      "op": "add",
      "path": "/relations/-",
      "value": {
        "rel": "System.LinkTypes.Hierarchy-Reverse",
        "url": "https://dev.azure.com/org/project/_apis/wit/workItems/{parent_id}"
      }
    }
  ]
  ```
* **Ponto de Chamada em `agent_squad.py`:**
  * Linhas `783-793`: No comando `init_work_item(..., devops=True)`.
  * **Falha Crítica Identificada (`agent_squad.py:783`):** Quando invocado em `init_work_item`, `connector.create_work_item` recebe apenas `title=work_id, wit_type=wit_type, area_path=area_path`. **O parâmetro `parent_id` NÃO É PASSADO!** Como consequência, qualquer work item instanciado via CLI `squad init-work-item --devops` é criado como **órfão na raiz** do Azure DevOps, sem vínculo ao seu Epic ou Feature pai.

### 4.2 Bootstrap em Árvore (`create_work_item_tree`)
* **Arquivo:** `scripts/azure_devops_bootstrap.py:28`
* **Sequência de Criação:**
  1. Cria o Epic raiz: `client.create_work_item("epic", epic_title, epic_description)` (Linha 47).
  2. Itera sobre a lista de histórias, chamando `client.create_work_item("story", ..., parent_id=epic.id)` (Linha 54).
  3. Para cada história, itera sobre as tarefas filhas chamando `client.create_work_item("task", ..., parent_id=story.id)` (Linha 63).
* **Persistência do ID:** Chama `record_devops_ids()` (Linha 72), que grava apenas `status["devops_id"] = epic.id` no `status.yaml` do diretório do work item. As User Stories e Tasks filhas têm seus IDs remotos descartados em memória (não são persistidos em nenhum arquivo ou banco de dados).

### 4.3 Atualização de Work Items (`update_work_item` / `update_item_state`)
* **Implementação de Biblioteca:** `integrations/devops_platform_connector.py:312` (`update_item_state`).
  * Converte o estado SDLC local usando `self.state_map` (ex: `blueprint` -> `New`, `implementation` -> `Active`, `done` -> `Closed`).
  * Atualiza tags de fase (`phase_tags`) em `System.Tags` e opcionalmente insere nota de histórico em `System.History`.
* **Chamada Duplicada e Bypassed em `agent_squad.py:3052`:**
  * O método `advance_state()` em `agent_squad.py` ignora `update_item_state()` e constrói diretamente um PATCH cru contra o endpoint REST do Azure:
    ```python
    connector.client.send(
        "PATCH",
        f"{connector.client.org}/{connector.client.project}/_apis/wit/workitems/{devops_id}?api-version=7.1",
        [{"op": "add", "path": "/fields/System.State", "value": ado_state}],
        content_type="application/json-patch+json",
    )
    ```
  * Essa chamada crua não atualiza `System.Tags`, não envia histórico e não faz validação de transição legal.

---

## 5. COMPORTAMENTO ATUAL DE RETRIES E BACKOFF

A análise do mecanismo de tolerância a falhas revelou uma discrepância gritante entre chamadas de infraestrutura e chamadas de ciclo de vida do SDLC:

### 5.1 Retries via Biblioteca `tenacity` (Camada Connector / Infraestrutura)
* **Localização:** `integrations/devops_platform_connector.py:225` e `scripts/azure_devops_project_creator.py:69`.
* **Decorador:**
  ```python
  @retry(
      retry=retry_if_exception_type(_RetryableHTTPError),
      stop=stop_after_attempt(3),
      wait=_retry_wait,
      reraise=True,
  )
  ```
* **Condição de Retry (`_RetryableHTTPError`):**
  * HTTP 429 (Rate Limit / Throttling)
  * HTTP 502 (Bad Gateway)
  * HTTP 503 (Service Unavailable)
  * HTTP 504 (Gateway Timeout)
  * `urllib.error.URLError` (queda de conexão / DNS / socket reset)
* **Algoritmo de Espera (`_retry_wait`):**
  * Se o cabeçalho `Retry-After` estiver presente na resposta HTTP do Azure, aguarda exatamente o número de segundos solicitado pelo servidor.
  * Caso contrário, aplica espera exponencial truncada: `wait_exponential(multiplier=1, min=1, max=4)`.
* **Características:**
  * **Síncrono e Bloqueante:** A thread de execução do processo Python é congelada durante o `sleep`, travando o chamador.
  * **Tentativas Máximas:** Estritamente limitado a 3 tentativas (`stop_after_attempt(3)`). Não há loop infinito.

### 5.2 Polling de Longa Duração (Criador de Projetos e Repositórios)
* `azure_devops_project_creator.py:120` (`_wait_for_project`): Realiza polling com intervalo dinâmico (`poll = min(poll * 1.5, 30)`), com timeout global de 600 segundos (10 minutos).
* `azure_devops_repo_importer.py:194` (`_wait_for_import`): Polling síncrono a cada 10 segundos com timeout global de 900 segundos (15 minutos).
* `azure_devops_lifecycle.py:157` (`_phase2_wait_ready`): Loop fixo de 10 tentativas com `time.sleep(5)` invariável (sem backoff).

### 5.3 ZERO Retries na Sincronização de Ciclo de Vida (`agent_squad.py`)
* Nas operações de ciclo de vida em `scripts/agent_squad.py`:
  * Linhas `3033-3063` (`advance_state` -> Azure State Sync)
  * Linhas `3153-3184` (`memory_delta` -> Azure Comment Sync)
* **Comportamento:** **NÃO HÁ RETRY.** Uma única tentativa HTTP PATCH direta é executada. Se ocorrer timeout de socket, 503 temporário ou 429 de rate limit, o erro é impresso no console e descartado sem nenhuma re-tentativa posterior.

---

## 6. MAPA DE ENGOLIMENTO DE EXCEÇÕES (EXCEPTION SWALLOWING)

Foram identificadas 7 ocorrências graves onde o código intercepta erros genéricos (`except Exception:`) sem propagar a falha e sem aplicar o princípio do *Fail-Closed*.

| # | Arquivo:Linha | Bloco de Código / Operação | Tratamento de Erro Atual | Consequência Arquitetural / Risco |
|---|---|---|---|---|
| **E01** | `scripts/agent_squad.py:807-810` | `init_work_item()`: Falha na criação do work item no Azure DevOps. | `except Exception as _e: print(f"WARN init_work_item_devops_create_failed: {_e}")` | **GRAVE.** O work item local é gravado no disco com `devops_id = None`. O usuário assume que o card foi criado no Azure, mas o sistema falhou silenciosamente. Não há enqueue em Outbox. |
| **E02** | `scripts/agent_squad.py:3060-3063` | `advance_state()`: Falha na sincronização de estado SDLC com o Azure Boards. | `except Exception as _e: print(f"WARN advance_state_devops_sync_failed: {_e}")` | **GRAVE (Drift Desgovernado).** O estado local avança (ex: de `scaffolding` para `implementation`), mas o card remoto continua em `Active` ou `New`. O Azure Boards deixa de refletir a realidade sem notificação de bloqueio. |
| **E03** | `scripts/agent_squad.py:3181-3184` | `memory_delta()`: Falha na adição de comentário de memória ao Azure Work Item. | `except Exception as _ado_err: print(f"WARN memory_delta_devops_sync_failed: {_ado_err}")` | **MÉDIO.** O fato de memória é persistido localmente no SQLite (`squad.db`), mas a auditoria externa no card do Azure DevOps é perdida sem erro fail-closed. |
| **E04** | `integrations/devops_platform_connector.py:263-268` | `AzureDevOpsClient._request()`: Wrapper de chamadas HTTP com retry. | `except _RetryableHTTPError: return None` | **GRAVE (Mascaramento).** Quando as 3 tentativas de retry esgotam ou ocorre erro transitório persistente, o método engole `_RetryableHTTPError` e retorna `None`. O chamador não recebe a causa do erro (ex: 429 vs 503). |
| **E05** | `scripts/azure_devops_lifecycle.py:318-320` | `_rollback()`: Falha ao deletar Team Project após erro de provisionamento. | `except Exception as exc: self._record("rollback", "error", str(exc))` | **ALTO.** O erro na chamada `DELETE` é registrado em uma lista em memória, mas não impede o script de retornar dicionário parcial. O projeto órfão permanece ativo no Azure. |
| **E06** | `scripts/agent_squad.py:749-750` | `init_work_item()`: Tentativa de leitura de `devops.yaml`. | `except Exception: pass` | **MÉDIO.** Se `devops.yaml` tiver erro de sintaxe YAML, o bloco captura e ignora, desativando silenciosamente a criação de cards no Azure. |
| **E07** | `scripts/agent_squad.py:775-776` | `init_work_item()`: Resolução de `area_path` via `ProjectDeliveryBindingService`. | `except Exception: pass` | **MÉDIO.** Falhas de resolução do binding R5 são silenciadas, fazendo o código cair na validação estrita posterior que aborta com `SquadError`. |

---

## 7. COMPORTAMENTO ATUAL DE RELAÇÃO DE PARENTESCO (HIERARCHY & ORPHANS)

### 7.1 Formato do Vínculo de Parentesco no Azure DevOps
* O vínculo é estabelecido via JSON Patch com o tipo de relação:
  * `rel`: `"System.LinkTypes.Hierarchy-Reverse"`
  * `url`: `f"{self.base_url}/wit/workItems/{parent_id}"`
* Na semântica interna do Azure DevOps:
  * `Hierarchy-Reverse` vincula **Filho -> Pai** (o card filho aponta para seu contêiner pai).
  * `Hierarchy-Forward` vincula **Pai -> Filho**.
  * A REST API v7.1 recomenda vincular via `Hierarchy-Reverse` a partir do filho durante sua criação (em `POST /wit/workitems/${type}`).

### 7.2 Validação de Parentesco Pré-Criação
* **No conector (`AzureDevOpsClient.create_work_item`):** **ZERO VALIDAÇÃO.**
  * O método aceita qualquer string em `parent_id` (ex: `parent_id="9999999"`).
  * Não faz consulta prévia (`GET /wit/workitems/{parent_id}`) para verificar se o ID pai existe no Azure.
  * Não valida compatibilidade de tipo de work item (ex: se o pai de uma `Task` é uma `User Story`, ou se o pai de uma `User Story` é uma `Feature` ou `Epic`).

### 7.3 Tratamento de Órfãos e Respostas de Erro do Azure
* Se o `parent_id` informado for inválido ou não existir no servidor:
  * O Azure DevOps rejeita a requisição inteira com **HTTP 400 Bad Request** (`TF401349` / `TF26176: Work item {parent_id} does not exist`).
  * Como o wrapper `_request` engole o erro e retorna `None`, a criação do work item filho **falha por completo**.
* **Ausência de Fallback de Órfão:**
  * O sistema não possui política de desvincular o pai e criar como item raiz temporário, nem enfileira o item para resolução tardia de parentesco.
* **Criação de Órfãos Involuntária em `agent_squad.py`:**
  * Como mapeado na seção 4.1, `agent_squad.py:init_work_item` **omite** o envio de `parent_id` para o connector. Portanto, todos os itens criados via CLI local nascem como órfãos no Azure DevOps.

---

## 8. MAPEAMENTO ATUAL DE ESTADOS (STATE MAPPING)

O ecossistema possui múltiplas tabelas de mapeamento concorrentes entre os 13 estados do SDLC local e os estados nativos do processo **Agile** do Azure Boards:

### 8.1 Mapeamento Canônico Declarado (`devops_platform_connector.py:44`)
```python
DEFAULT_STATE_MAP = {
    "blueprint": "New",
    "scaffolding": "Active",
    "implementation": "Active",
    "code-security-review": "Active",
    "quality-validation": "Resolved",
    "governance-release": "Resolved",
    "done": "Closed",
}
```

### 8.2 Mapeamento das 7 Colunas do Board Stories (`azure_devops_project_setup.py:476`)
O setup do board cria as seguintes colunas visíveis no Azure DevOps Boards:

| Ordem | Nome da Coluna no Board | Tipo da Coluna (`columnType`) | Estado Agile Mapeado (Story / Bug) | Estado Agile Mapeado (Task) |
|---|---|---|---|---|
| 1 | **Blueprint** | `incoming` | `New` | `New` |
| 2 | **Scaffolding** | `inProgress` | `Active` | `Active` |
| 3 | **Implementation** | `inProgress` | `Active` | `Active` |
| 4 | **Code Security Review** | `inProgress` | `Active` | `Active` |
| 5 | **Quality Validation** | `inProgress` | `Resolved` | `Active` *(Task não possui Resolved)* |
| 6 | **Governance Release** | `inProgress` | `Resolved` | `Active` *(Task não possui Resolved)* |
| 7 | **Done** | `outgoing` | `Closed` | `Closed` |

### 8.3 Deficiências Críticas no Mapeamento de Estados
1. **Perda de Granularidade no Azure Boards:** Como os estados `Scaffolding`, `Implementation` e `Code Security Review` colapsam todos no estado nativo `Active`, mover o estado no Azure DevOps não permite inferir em qual coluna ou fase SDLC o item se encontra sem inspecionar a tag (`phase_tags`) ou o campo `System.BoardColumn`.
2. **Atualização Incompleta no `advance_state`:** Quando `agent_squad.py:3052` envia PATCH de `System.State = "Active"`, o Azure DevOps move o card para a **primeira coluna** que mapeia para `Active` (neste caso, a coluna *Scaffolding*). Mover para *Implementation* ou *Code Security Review* exigiria atualizar expressamente o campo `System.BoardColumn`, o que o código atual **não faz**.
3. **Hardcoding no `agent_squad.py`:** A função `advance_state` duplica `DEFAULT_STATE_MAP` em um dicionário local hardcoded (linhas `3041-3049`), ignorando configurações customizadas declaradas em `devops.yaml`.

---

## 9. CÓDIGO EXISTENTE DE SERVICE HOOKS / WEBHOOKS

* **Status:** `ABSENT / NOT IMPLEMENTED` (100% Inexistente para Azure DevOps).
* **Auditoria Detalhada:**
  1. **Receptor HTTP (Inbound Listener):** Não existe nenhum endpoint web (FastAPI, Flask, aiohttp ou `http.server`) implementado no repositório para escutar requisições vindas do Azure DevOps.
  2. **Autenticação de Payloads:** Em `scripts/domain/project.py:85` e `scripts/runtime/delivery/repository.py:86`, existe o campo `service_hook_secret_ref`, que aceita uma referência a cofre (ex: `vault://ado-token`). Contudo, **não há código que consuma ou valide esse token**.
  3. **Verificação de Assinatura:** Não há cálculo de HMAC-SHA256 para eventos do Azure. *(Nota: O pacote `integrations/vendor/repowise/` possui handlers de webhook para GitHub e GitLab, mas trata-se de ferramenta de terceiros isolada e não conectada ao pipeline de entrega do Agent Squad).*
  4. **Deduplicação e Idempotência:** Inexistente para eventos externos de ALM.
  5. **Provisionamento de Subscriptions:** O script `azure_devops_project_setup.py` cria assinaturas de notificação de e-mail (`/_apis/notification/subscriptions`), mas **não provisiona** Service Hooks HTTP (`POST /_apis/hooks/subscriptions`).

---

## 10. TRACKING DE REVISÃO DO AZURE (`System.Rev`) E CONFLITOS

* **Status no Domínio (R1):** `DEFINED` (`scripts/domain/sync.py:39` declara o campo `remote_rev: int` no modelo `AdoWorkItemBinding`).
* **Status no Runtime em Produção:** `ABSENT / NOT IMPLEMENTED`.
* **Auditoria Detalhada:**
  1. **Persistência de `rev`:** Nem o arquivo `status.yaml` nem o banco de dados `%SQUAD_RUNTIME%/banco/squad.db` gravam a versão de revisão remota (`System.Rev`) do Azure DevOps após criar ou atualizar cards. Apenas o ID escalar (`devops_id`) é armazenado.
  2. **Detecção de Conflitos Otimistas:** Inexistente.
     * Na API REST do Azure DevOps, a concorrência otimista é garantida enviando uma operação de teste no JSON Patch:
       ```json
       {"op": "test", "path": "/rev", "value": 3}
       ```
     * Nenhum arquivo (`devops_platform_connector.py`, `agent_squad.py`, `bootstrap.py`) envia operação de teste de revisão.
     * Todas as chamadas PATCH são do tipo *blind overwrite* (`op: add`), sobrescrevendo incondicionalmente qualquer edição que um desenvolvedor humano ou outro agente tenha feito remotamente no card.

---

## 11. CORRELAÇÃO ENTRE ENTIDADES LOCAIS E REMOTAS

### 11.1 Onde reside o vínculo atualmente
* **Armazenamento Primário:** Exclusivamente no arquivo de metadados do work item local:
  ```
  %SQUAD_RUNTIME%/work/<project_id>/<work_id>/status.yaml
  ```
* **Chave Utilizada:** `devops_id: <int>` (ex: `devops_id: 1042`).
* **Escopo no Banco de Dados (`banco/squad.db`):**
  * O banco SQLite WAL armazena apenas a tabela `project_bindings`, que vincula a identidade do projeto (`project_id`) com o contêiner do Azure (`team_project_id`, `repository_id`, `assigned_team_id`).
  * **Não existe tabela `work_item_bindings` no banco.** A tabela `AdoWorkItemBinding` definida no domínio R1 nunca foi criada via DDL no banco de dados.

### 11.2 Cardinalidade e Fragilidade
* A correlação é estritamente 1:1, dependente da integridade de um arquivo de texto YAML local.
* Se o diretório local for limpo ou se o arquivo `status.yaml` for corrompido, o vínculo com o Azure DevOps é perdido permanentemente, tornando impossível para o sistema determinar qual card remoto pertencia àquela User Story.

---

## 12. REDUNDÂNCIA E DUPLICAÇÃO DE LÓGICA DE SINCRONIZAÇÃO

A auditoria identificou redundâncias funcionais severas entre três camadas de código:

1. **Bypass do Conector por `agent_squad.py`:**
   * Em `agent_squad.py:3052` e `agent_squad.py:3168`, o código importa `DevOpsPlatformConnector`, mas em vez de chamar os métodos de alto nível (`update_item_state`, `add_work_item_comment`), acessa diretamente `connector.client.send("PATCH", ...)` ou `connector.client.org` montando strings de URL brutas. Isso anula o encapsulamento e a lógica de tratamento de tags.
2. **Duplicação de `state_map`:**
   * Declarado em `devops_platform_connector.py:44` (`DEFAULT_STATE_MAP`).
   * Redefinido de forma hardcoded em `agent_squad.py:3041` (`ado_state_map`).
   * Redefinido com mapeamento de colunas em `azure_devops_project_setup.py:476` (`target_columns`).
3. **Import Quebrado em `agent_squad.py:915`:**
   * Na função `init_project()`, linha 915 tenta fazer:
     ```python
     from azure_devops_lifecycle import run as azure_devops_run
     azure_devops_run(project_name=project_name, project_root=project_root, dry_run=dry_run)
     ```
   * O módulo `scripts/azure_devops_lifecycle.py` **NÃO POSSUI** nenhuma função pública de nível superior chamada `run()`. O método `run()` pertence à classe `AzureDevOpsLifecycle`. Invocar `squad init-project --devops` resulta imediatamente em:
     `ImportError: cannot import name 'run' from 'azure_devops_lifecycle'`.
4. **Duplicação de Leitura de Configuração:**
   * `load_devops_config` em `devops_platform_connector.py:53`.
   * `load_setup_config` em `azure_devops_project_setup.py:1367`.
   * Ambas as funções implementam buscas concorrentes e parciais pelo mesmo arquivo `.agents_squad/config/devops.yaml`.

---

## 13. SÍNTESE FACTUAL DE CONFORMIDADE PARA O MARCO R6

| Requisito do Marco R6 | Situação Atual no Código | Veredito Factual | Ação Requerida no R6 |
|---|---|---|---|
| **Eliminação de Criação de Team Project** | `azure_devops_project_creator.py` e `azure_devops_lifecycle.py` ainda chamam `POST /_apis/projects`. | **NON-COMPLIANT** | Desativar provisionamento de Team Projects; reutilizar Team Project corporativo via `ProjectBinding` (R5). |
| **Outbox Transacional de Sync** | Mutação direta inline via HTTP PATCH com engolimento de erro via `print(WARN)`. | **NON-COMPLIANT** | Implementar `SyncOutbox` transacional em SQLite WAL (`squad.db`) com estados `PENDING_CREATE`, `PENDING_UPDATE`, etc. |
| **Eliminação de Exception Swallowing** | 7 ocorrências de `except Exception` com log de warning ou pass sem fail-closed. | **NON-COMPLIANT** | Implementar tratamento fail-closed; se backend falhar, registrar `FAILED_RETRYABLE` no Outbox sem quebrar governança. |
| **Vínculo de Parentesco Hierárquico** | `init_work_item` não envia `parent_id` ao Azure; criação de cards como órfãos. | **NON-COMPLIANT** | Passar `parent_id` do contêiner hierárquico resolvido (`Feature` -> `Epic`, `Story` -> `Feature`, `Task` -> `Story`). |
| **Tracking de Revisão Remota (`System.Rev`)** | `status.yaml` só grava `devops_id`; zero persistência de `rev`. | **NON-COMPLIANT** | Persistir `remote_rev` e aplicar `{"op": "test", "path": "/rev"}` em todos os patches para concorrência otimista. |
| **Receptor de Webhooks / Service Hooks** | Inexistente. Sem listener HTTP, sem autenticação, sem deduplicação. | **ABSENT** | Desenvolver listener HTTP seguro com autenticação por token de referência e fila de reconciliação de drift. |
| **Reconciliação Bidirecional de Drift** | `domain/sync.py` possui contratos declarados, mas sem engine executável. | **NOT IMPLEMENTED** | Implementar `ReconciliationEngine` aplicando `ReconciliationDecision` (bloqueando transições remotas ilegais). |

---

## 14. CERTIFICAÇÃO FORMAL DO ENGENHEIRO DE PLATAFORMA

Eu, `27-platform-engineer` (Kelsey Hightower & Team Topologies — Internal Developer Platform Architect), certifico formalmente que a auditoria do **STAGE A (CURRENT AZURE WRITE/SYNC MAP)** foi executada de forma estritamente factual e read-only, sem alterar nenhuma linha de código de produção e sem disparar requisições mutantes ao Azure DevOps.

O diagnóstico acima documenta as vulnerabilidades exatas, duplicações e lacunas estruturais que devem ser saneadas no STAGE B e implementadas no STAGE C do marco **R6 — AZURE DEVOPS WORKFLOW + BIDIRECTIONAL SYNC**.
