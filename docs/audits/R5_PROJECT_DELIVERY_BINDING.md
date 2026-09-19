# R5 — PROJECT + DELIVERY BACKEND BINDING FINAL CODE REVIEW & AUDIT REPORT
## Clean Architecture Audit · Ports & Adapters Binding Plane · Read-Only Azure Discovery · Host Neutrality · Zero-Secret SEC-R1-01 Invariants

**Document ID:** `DOC-AUDIT-R5-FINAL-REVIEW`  
**Milestone:** `R5 — PROJECT + DELIVERY BACKEND BINDING`  
**Stage:** `STAGE E — CODE REVIEW & AUDIT REPORT`  
**Date:** 2026-09-18  
**Auditor / Lead:** `09-code-reviewer` (Michael Feathers & Google Engineering - Static Analysis & Code Quality Auditor)  
**Solution Architect Sign-off:** `04-solution-architect` (`R5_BINDING_DESIGN = APPROVED`)  
**Security & Governance Sign-off:** `10-security-reviewer` & `14-governance-auditor` (`SEC-R1-01 = PASS`)  
**Status:** `APPROVED` (`R5_CODE_REVIEW = APPROVED`)  

---

## EXECUTIVE SUMMARY

Na qualidade de auditor líder de qualidade de código e análise estática (`09-code-reviewer`), executei a auditoria formal, exaustiva e independente referente ao marco **R5 — PROJECT + DELIVERY BACKEND BINDING** da plataforma Agent Squad.

O marco R5 estabelece a camada canônica de amarração e autoridade de identidade de projetos e backends de entrega (`scripts/runtime/delivery/`), consumindo os contratos canônicos e imutáveis de domínio formalizados no R1 (`scripts/domain/project.py`), integrando-se à outbox transacional de eventos de domínio do R2 (`scripts/runtime/events/`), ancorando-se na hierarquia física de diretórios do R3 (`scripts/runtime/work_items/`) e respeitando a máquina de estados estrita do ciclo de vida R4 (`scripts/runtime/lifecycle/`).

A auditoria confirma que **o marco R5 cumpre 100% dos requisitos arquiteturais, princípios de Clean Architecture / Hexagonal (Ports & Adapters), diretrizes de segurança SEC-R1-01 e proibições absolutas de mutação remota**:

1. **Baseline de Entrada Verificado:** O marco partiu do commit canônico estável `R5_START_SHA = c3971bc0bc8d1bbd9947b154f627cf3f3b914306`.
2. **Separação Ontológica Inviolável (Produto vs. Team Project):** Erradicação definitiva do defeito `R0-ADO-002`. O Team Project corporativo é tratado estritamente como um container organizacional compartilhado. Produtos de software são mapeados como subentidades (Repositórios Git, Áreas de Backlog e Times Dedicados). É estritamente impossibilitada a criação de um Team Project por produto (`POST /_apis/projects` = 0).
3. **Descoberta Topológica Azure Estritamente Read-Only:** A porta `AzureDiscoveryPort` e o adaptador `ReadOnlyAzureDiscovery` emitem exclusivamente requisições HTTP `GET`. Nenhuma chamada `POST`, `PUT`, `PATCH` ou `DELETE` foi introduzida contra endpoints remotos do Azure DevOps.
4. **Resolução Determinística de Projeto e Neutralidade de Host:** Erradicação do defeito `R0-DIR-001`. A resolução de projeto é guiada por configuração declarativa explícita (`.agents_squad/config/project.yaml`), com guardas estritas de contenção de caminho (`PathContainmentGuard`) que proíbem diretórios de trabalho `./work` locais aos projetos. Removido todo e qualquer scaffolding de configuração proprietária de host (e.g. `.agents/plugins/agent-squad/mcp_config.json`), garantindo total independência de ambiente.
5. **Eliminação Integral de Fallbacks Silenciosos:** Erradicação dos defeitos `R0-ADO-001` e `R0-ADO-003`. Tokens legados hardcoded (`cbvgas`, `arthemis`, `deepvision`, `test_root`, `test_item`) são bloqueados fail-closed em runtime por `_check_forbidden_tokens()`. Tentativas de execução com configurações ausentes resultam em interrupção tipada (`DeliveryBackendNotConfiguredError` / `ProjectNotResolvedError`), impedindo bypasses silenciosos.
6. **Segurança de Credenciais SEC-R1-01:** Higienização sistemática de strings e URLs (`sanitize_credentials`), expurgando tokens básicos ou PATs embutidos. Zero segredos, PATs ou credenciais são persistidos em tabelas SQLite (`project_bindings`, `project_binding_history`), em arquivos de configuração ou em payloads de eventos.
7. **Persistência SQLite WAL e Monotonicidade de Revisões:** O repositório `SqliteBindingRepository` opera no banco central `%SQUAD_RUNTIME%/banco/squad.db` sob WAL mode, foreign keys ativas e controle transacional atômico. Idempotência garantida via cálculo de fingerprint SHA-256 e incremento estritamente monotônico de revisões (`revision = current_revision + 1`).
8. **Pureza Arquitetural e Isolamento:** O pacote `scripts/runtime/delivery/` depende estritamente da biblioteca padrão do Python (`urllib`, `sqlite3`, `pathlib`, `json`, `dataclasses`, etc.) e dos modelos puros do domínio (`scripts.domain`). Zero dependências de SDKs externos ou chamadas a LLMs.
9. **Resultados de Testes e Governança:**
   - **R5 Unit & Integration Suite:** 68/68 testes aprovados (100%).
   - **R1–R4 Cumulative Regression Suite:** 218/218 testes preservados intactos (100%).
   - **Structural Validation (`validate_structure.py`):** Exit code 0 (41 agentes, 170 skills ativas, 18 esquemas).
   - **CLI Governance Audit (`agent_squad.py audit`):** Exit code 0 (`AUDIT_OK`).
   - **R0 Diagnostic Suite (`r0_workitem_ado_contract_red.py`):** Preservado intacto sob `scripts/tests/diagnostics/` para atestar a blindagem histórica de defeitos.

---

## SEÇÃO A: BASELINE DE ENTRADA E VERIFICAÇÃO DE INÍCIO

A auditoria verificou a rastreabilidade do ponto de partida do marco R5:
- **`R5_START_SHA`:** `c3971bc0bc8d1bbd9947b154f627cf3f3b914306`
- **Branch Ativo:** `bugfix/mcp-foundation-fix`
- **Estado de Limpeza de Início:** O repositório apresentava a baseline dos marcos R1 a R4 completamente consolidada e validada, com 218 testes cumulativos passando e integridade estrutural confirmada.
- **Rastreabilidade Git:** Nenhum commit externo concorrente poluiu a árvore de trabalho durante a implementação do marco R5.

---

## SEÇÃO B: VERIFICAÇÃO DA ARQUITETURA APROVADA

A documentação arquitetural autoritativa do marco reside em:
`docs/architecture/R5_PROJECT_DELIVERY_BINDING.md` (`DOC-ARCH-R5-PROJECT-DELIVERY-BINDING`, assinado por `04-solution-architect`, `13-devops-release-engineer` e `14-governance-auditor` com status `R5_BINDING_DESIGN = APPROVED`).

### Avaliação de Aderência às Diretrizes Arquiteturais:
1. **Ports & Adapters:** A arquitetura definiu a separação rigorosa entre domínio (`scripts/domain/project.py`), portas (`AzureDiscoveryPort`), adaptadores secundários (`ReadOnlyAzureDiscovery`, `SqliteBindingRepository`) e o serviço orquestrador de casos de uso (`ProjectDeliveryBindingService`).
2. **Definição de Fronteiras R5 vs. R6/R7:** A arquitetura segregou expressamente que:
   - R5 limita-se a descoberta topológica, validação de regras de amarração e persistência de estado de binding (`ProjectBindingRecord`).
   - Provisionamento de recursos remotos (criar repositório Git, criar nós de área) é diferido estritamente para o marco R6.
   - Materialização de itens de backlog (cartões Epic, Feature, Story, Task no ADO ou em disco) é diferida para o marco R7.
3. **Invariantes do SQLite Central:** A especificação consagrou `%SQUAD_RUNTIME%/banco/squad.db` como autoridade única de persistência para `project_bindings` e `project_binding_history`, proibindo bancos paralelos.

---

## SEÇÃO C: ANÁLISE DO MAPEAMENTO DO STAGE A

A execução do Stage A realizou o mapeamento e inventário exaustivo da topologia de entrega e das convenções de vinculação de projetos locais com backends remotos corporativos (`R5_CURRENT_PROJECT_DELIVERY_MAP.md`):

1. **Topologia de Mapeamento Identificada:**
   - Mapeamento explícito de produtos a repositórios Git específicos dentro de um Team Project empresarial.
   - Vinculação de escopo funcional por nós de Classificação de Área (`Area Paths`), garantindo que o escopo de cada produto seja um ramo hierárquico iniciado obrigatoriamente pelo nome do Team Project container (e.g. `Container-Project\Product-Node`).
   - Definição da equipe de desenvolvimento (`Assigned Team`) e resolução dinâmica do cadence/sprint (`Iteration Path`).
2. **Tratamento de Modos de Operação:**
   - **`AZURE_DEVOPS`:** Requer conectividade HTTPS estrita, validação de existência de Team Project e inspeção de recursos filhos.
   - **`LOCAL_ONLY`:** Operação offline autocontida sem chamadas remotas, gerando referência canônica `local://<project_id>` com governança local integral em SQLite.
   - **`MOCK`:** Sandbox controlado utilizado exclusivamente em testes automatizados, retornando topologia simulada determinística.

---

## SEÇÃO D: AUDITORIA DE IMPLEMENTAÇÃO DO PACOTE `scripts/runtime/delivery/`

Foi realizada a inspeção estática aprofundada nos arquivos implementados no pacote `scripts/runtime/delivery/`:

| Módulo | Linhas | Tamanho (Bytes) | Responsabilidade Arquitetural | Avaliação Estática |
| :--- | :---: | :---: | :--- | :--- |
| `__init__.py` | 95 | 2.460 | Fachada pública e exportação canônica com `__all__` explícito | **CONFORME** |
| `errors.py` | 187 | 8.308 | Hierarquia de exceções tipadas com códigos de erro (`PROJ_001`, `SEC_001`, `AZ_503`, etc.) | **CONFORME** |
| `repository.py` | 518 | 22.183 | Repositório SQLite WAL, DDL de tabelas/índices, SHA-256 fingerprinting e transacionalidade | **CONFORME** |
| `azure_discovery.py` | 516 | 19.783 | Adaptador read-only de descoberta Azure DevOps (HTTP GET puro, sanitização de tokens) | **CONFORME** |
| `binding.py` | 843 | 34.691 | Serviço orquestrador `ProjectDeliveryBindingService`, resolução local e integração com R2 Outbox | **CONFORME** |
| **Total** | **2.159** | **87.425** | **Zero dependências externas, 100% biblioteca padrão Python** | **APROVADO** |

### D.1 Avaliação Detalhada por Componente:
- **`errors.py`:** Todas as exceções herdam de `DeliveryBindingError` (com alias canônico `BindingError`), que por sua vez herda de `SquadError`. Códigos padronizados facilitam o diagnóstico e tratamento fail-closed (`PROJ_001`, `DEL_001`, `AZ_404_PROJ`, `AZ_409_AMBIG`, `BIND_409`, etc.).
- **`repository.py`:**
  - Gerenciamento de conexão determinístico via context manager `connection()`.
  - Pragmas obrigatórios executados: `PRAGMA foreign_keys = ON;`, `PRAGMA busy_timeout = 5000;`, `PRAGMA journal_mode = WAL;`, `PRAGMA synchronous = NORMAL;`.
  - Consultas 100% parametrizadas (placeholders `?`), eliminando qualquer risco de injeção SQL.
  - Snapshot de auditoria append-only inserido em `project_binding_history` a cada mutação de estado.
- **`azure_discovery.py`:**
  - Métodos implementam exclusivamente a semântica `GET`: `get_project`, `list_projects`, `get_repository`, `list_repositories`, `get_team`, `get_area_node`, `get_iteration_node`, `get_team_boards`, `get_process_template`.
  - Injeção de dependência via `TransportCallable`, viabilizando 100% de cobertura determinística em testes offline.
  - Detecção de ambiguidades (`AmbiguousRepositoryError`, `AmbiguousTeamError`) ao encontrar múltiplos candidatos, falhando de forma fechada em vez de selecionar por aproximação heurística.
- **`binding.py`:**
  - Resolução segura de projetos através de busca ascendente por `.agents_squad/config/project.yaml` (ou fallback `.squad/project.yaml`).
  - Verificação de gramática estrita do `project_id` via regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`.
  - Verificação ativa contra tokens legados proibidos via `_check_forbidden_tokens()`.
  - Relatório granular de recursos via `ResourceStatusReport` e resultado agregado via `BindingResolutionResult`.

---

## SEÇÃO E: AUDITORIA DE SEGREGAÇÃO ONTOLÓGICA: PRODUTO VS. TEAM PROJECT

A auditoria confirmou a eliminação integral do anti-padrão documentado no defeito **R0-ADO-002**:
1. **Container vs. Produto:** O subsistema R5 consagra que o Azure DevOps Team Project é um container compartilhado empresarial (e.g. `Core-Banking`, `Cloud-Platform`).
2. **Subordinação do Produto:** Um produto de software reside dentro do container e se projeta exclusivamente em:
   - Um Repositório Git dedicado ou compartilhado (`repository_name`).
   - Um nó subordinado na árvore de áreas: o caminho da área (`area_path`) **deve obrigatoriamente iniciar com o nome do Team Project** (e.g. `Core-Banking\Payments`). Caso o `area_path` aponte para um container divergente, o serviço lança imediatamente `InvalidBindingConfigurationError`.
3. **Inexistência de Métodos de Criação de Team Project:** Uma auditoria estática por métodos `create_project`, `provision_project` ou chamadas `POST /_apis/projects` no código de `scripts/runtime/delivery/` confirmou a ocorrência de **0 (zero)** métodos ou chamadas desse tipo, validado formalmente pelo teste automatizado `test_zero_team_project_creation_methods_in_delivery`.

---

## SEÇÃO F: AUDITORIA DE ELIMINAÇÃO DE FALLBACKS SILENCIOSOS

A auditoria verificou a erradicação de fallbacks silenciosos e comportamentos permissivos inseguros em `scripts/project_context.py` e `scripts/agent_squad.py`:

1. **`scripts/project_context.py`:**
   - A função `find_project_root()` não realiza fallback cego para `Path.cwd()` ou `os.getcwd()` quando nenhum caminho é fornecido; retorna `None`.
   - A função `resolve_project_context(start=None, explicit_project_root=None)` exige parâmetros válidos e falha de forma explícita com `ProjectContextError("Nenhum caminho inicial ou raiz explícita informados para resolução de projeto")`.
   - Resolução de runtime `resolve_runtime_root(start_path)` desacoplada, utilizando busca ascendente parametrizada.
   - Suporte transparente ao formato moderno declarativo de projeto com seção `delivery`, resolvendo a raiz compartilhada de runtime sem exigir campos redundantes no marcador local.
2. **`scripts/agent_squad.py`:**
   - Linhas 766–780: O antigo fallback silencioso que injetava o valor estático `"Arthemis\agent-squad"` quando `area_path` estava ausente foi **completamente removido**.
   - O novo fluxo consulta o `ProjectDeliveryBindingService` pelo binding registrado do projeto. Caso a área não esteja configurada nem no arquivo local nem no binding em banco, a execução é abortada imediatamente com:
     `raise SquadError("area_path não configurada em devops.yaml nem no binding de entrega; fallback hardcoded proibido")`.

---

## SEÇÃO G: AUDITORIA DE NEUTRALIDADE DE HOST

A auditoria inspecionou as modificações em `scripts/bootstrap_project_squad.py` para verificar a neutralidade de host:
1. **Remoção de Scaffolding Proprietário:** Foram eliminadas 21 linhas de código que realizavam a criação arbitrária de arquivos de configuração específicos de IDEs host (anteriormente `.agents/plugins/agent-squad/mcp_config.json`).
2. **Agnosticismo de Ambiente:** O projeto bootstrappado pelo Agent Squad contém exclusivamente o marcador declarativo padrão `.agents_squad/config/project.yaml`.
3. **Isolamento de Adaptadores de Host:** A integração com ferramentas como Antigravity IDE, Claude Desktop, Cursor ou terminal CLI opera exclusivamente na borda do sistema (camada periférica), sem impor artefatos proprietários dentro do repositório do projeto consumidor.

---

## SEÇÃO H: AUDITORIA DE SEGURANÇA SEC-R1-01

A política de segurança **SEC-R1-01** estabelece como imperativo categórico que credenciais, senhas, tokens PAT e segredos corporativos jamais sejam persistidos em disco ou expostos em logs:

1. **Higienização em Ingestão (`sanitize_credentials`):**
   - Utilização de expressão regular `re.compile(r"https?://([^/@:]+(:[^/@:]+)?@)")` para expurgar credenciais embutidas do tipo `https://pat@dev.azure.com` ou `https://user:password@host`.
   - O teste `test_sec_r1_01_sanitizes_inline_pat_in_organization_url` confirma que a URL `https://my-secret-pat@dev.azure.com/myorg` é automaticamente normalizada para `https://dev.azure.com/myorg`.
2. **Rejeição de URLs Inseguras:**
   - URLs de organização com esquema HTTP puro são rejeitadas categoricamente (`test_sec_r1_01_rejects_insecure_http_organization_url`), exigindo canal seguro HTTPS.
3. **Armazenamento de Referências e Não de Valores:**
   - O campo `service_hook_secret_ref` armazena exclusivamente o identificador ou nome da variável de ambiente que contém o segredo, nunca o valor do token em si.
4. **Isolamento na Camada de Dados:**
   - O método `ProjectBindingRecord.sanitized_dict()` aplica sanitização ativa antes do cálculo do fingerprint e da persistência em SQLite.

---

## SEÇÃO I: AUDITORIA DE RESILIÊNCIA SQLITE WAL, TRANSACIONALIDADE E REVISÕES MONOTÔNICAS

A auditoria inspecionou os mecanismos de tolerância a falhas e atomicidade do banco de dados:

1. **Ativação de WAL Mode e Foreign Keys:**
   - Em conexões em disco, o repositório configura deterministicamente:
     ```sql
     PRAGMA foreign_keys = ON;
     PRAGMA busy_timeout = 5000;
     PRAGMA journal_mode = WAL;
     PRAGMA synchronous = NORMAL;
     ```
   - Confirmado via teste automatizado `test_pragmas_enforcement_disk`.
2. **Transacionalidade Atômica:**
   - O método `upsert_binding()` executa a verificação de estado prévio, a inserção/atualização da tabela `project_bindings` e o registro de auditoria na tabela `project_binding_history` dentro de um único bloco transacional (`with conn:`).
3. **Idempotência por Fingerprint SHA-256:**
   - O hash determinístico gerado por `compute_binding_fingerprint()` baseia-se na representação canônica JSON normalizada (chaves ordenadas, strings aparadas em lowercase).
   - Se uma operação de upsert submeter dados idênticos com o mesmo fingerprint e status, o repositório retorna o registro existente com `changed = False`, sem incrementar revisão desnecessariamente (`test_upsert_binding_idempotency`).
4. **Revisões Monotônicas:**
   - Qualquer mutação em campos materiais incrementa a revisão de forma estritamente sequencial (`revision = current.revision + 1`), preservando o histórico completo em `project_binding_history` com chave estrangeira em cascata (`test_upsert_binding_monotonic_revision_increment` e `test_delete_binding_cascades_history`).

---

## SEÇÃO J: AUDITORIA DE PUREZA ARQUITETURAL

Foi avaliado o cumprimento das diretrizes de arquitetura limpa e padrão de projeto Ports & Adapters:

1. **Zero Dependências Externas / SDKs:**
   - O subsistema `scripts/runtime/delivery/` utiliza exclusivamente módulos da biblioteca padrão Python (`urllib.request`, `urllib.parse`, `sqlite3`, `json`, `dataclasses`, `hashlib`, `uuid`, `logging`, `pathlib`, `re`, `enum`).
   - A única dependência de terceiros é o `PyYAML` (já canônico na raiz do projeto).
   - Zero bibliotecas externas de requisição HTTP (e.g. `requests`, `aiohttp`, `httpx`).
   - Zero dependências de SDKs de provedores de LLM (e.g. `openai`, `google-genai`, `anthropic`, `langchain`).
2. **Fluxo Unidirecional de Dependência:**
   - A camada de entrega importa modelos do domínio canônico (`scripts.domain.project`, `scripts.domain.common`, `scripts.domain.events`).
   - O domínio **nunca** importa da camada de runtime, respeitando o princípio de inversão de dependência.

---

## SEÇÃO K: AUDITORIA DE TESTES AUTOMATIZADOS (R5 TEST SUITE)

A suíte de testes unitários e de integração de R5 foi executada e auditada:
- **Comando de Execução:**
  `python -m pytest scripts/tests/test_r5_azure_discovery.py scripts/tests/test_r5_binding_authority.py scripts/tests/test_r5_binding_events.py scripts/tests/test_r5_binding_persistence.py scripts/tests/test_r5_project_binding.py -v`
- **Resultados:** **68 testes executados, 68 testes aprovados (100% PASS), 0 falhas, 0 erros** em 0,65s.

### Detalhamento por Arquivo de Teste:
1. `test_r5_azure_discovery.py` (16 testes):
   - Cobertura completa de descoberta de Team Projects, Repositórios, Times, Nós de Área e Iteração, Boards e Process Templates (Agile, Scrum, CMMI, Basic, Custom).
   - Testes rigorosos de tratamento de erro e fail-closed para `AzureAuthenticationError`, `AzureUnavailableError`, `AzureTimeoutError` e ambiguidade.
2. `test_r5_binding_authority.py` (7 testes):
   - Verificação de isolamento de biblioteca padrão e domínio.
   - Prova de zero métodos de criação de Team Project em `scripts/runtime/delivery/`.
   - Subordinação de área a Team Project e sanitização de credenciais SEC-R1-01.
   - Paridade de aliases canônicos (`DeliveryBindingManager == ProjectDeliveryBindingService`).
3. `test_r5_binding_events.py` (5 testes):
   - Emissão de eventos de domínio R2 via Outbox: `agent_squad.project.resolved`, `agent_squad.delivery.bound`, `agent_squad.delivery.binding_blocked`.
   - Resiliência na ausência de `event_store`.
4. `test_r5_binding_persistence.py` (10 testes):
   - Bootstrap de DDL, índices, pragmas WAL, cálculo determinístico de fingerprint.
   - Upsert atômico, detecção de idempotência, incremento monotônico de revisão e exclusão em cascata.
5. `test_r5_project_binding.py` (30 testes):
   - Resolução local de projeto (caminho padrão, fallback squad, travessia ascendente).
   - Falha fechada para configurações ausentes, YAML inválido, `project_id` ausente ou malformado (gramática regex).
   - Rejeição de tokens legados proibidos (`cbvgas`, `test_item`, `deepvision`, `test_root`, `arthemis`).
   - Amarração completa para backends `LOCAL_ONLY`, `MOCK` e `AZURE_DEVOPS`.

---

## SEÇÃO L: AUDITORIA DE PRESERVAÇÃO CUMULATIVA DE REGRESSÃO

Para certificar que a introdução do marco R5 e as refatorações associadas não introduziram regressões nos marcos anteriores, a suíte cumulativa R1–R4 foi executada:
- **Comando de Execução:**
  `python -m pytest scripts/tests/test_r1_*.py scripts/tests/test_r2_*.py scripts/tests/test_r3_*.py scripts/tests/test_r4_*.py -q`
- **Resultados:** **218 testes executados, 218 testes aprovados (100% PASS), 0 falhas, 0 erros** em 14,58s.
- **Distribuição de Regressão Cumulativa:**
  - Marco R1 (Contratos Canônicos de Domínio): 42 testes PASS.
  - Marco R2 (Motor de Eventos e Outbox Transacional): 58 testes PASS.
  - Marco R3 (Hierarquia Física e Normalização de Work Items): 57 testes PASS.
  - Marco R4 (Máquina de Estados de Ciclo de Vida e WIP Control): 61 testes PASS.

---

## SEÇÃO M: AUDITORIA DE INTEGRIDADE ESTRUTURAL E DE GOVERNANÇA

Foram executados os validadores globais da base de código do Agent Squad:

1. **Validação de Estrutura de Agentes, Skills e Schemas:**
   - Comando: `python scripts/validate_structure.py`
   - Saída: `VALID structure agents=41 active_skills=170 schemas=18`
   - Exit Code: `0`
2. **Auditoria Geral de Governança CLI:**
   - Comando: `python scripts/agent_squad.py audit`
   - Saída:
     ```text
     INFO audit: exclusão de vendor 'integrations/spec-kit/' (255 caminhos) — regra VENDOR_SKILL_SCAN_EXCLUSIONS; integridade do vendor é atestada por integrations/spec-kit/upstream/UPSTREAM_FILES.sha256 (verify_snapshot.py), não pelo catálogo de skills
     AUDIT_OK
     ```
   - Exit Code: `0`

---

## SEÇÃO N: AUDITORIA DOS DIAGNÓSTICOS R0

A suíte diagnóstica original `scripts/tests/diagnostics/r0_workitem_ado_contract_red.py` foi auditada para atestar sua preservação:
- **Status do Arquivo:** Preservado intacto sob `scripts/tests/diagnostics/`.
- **Propósito:** Demonstrar as falhas pré-reforma nos contratos legados de Work Item e Azure DevOps (`R0-WORK-*` e `R0-ADO-*`).
- **Constatação:** Nenhuma modificação foi realizada no arquivo de teste diagnóstico, assegurando que o baseline de referência histórica permaneça inalterado como testemunho de conformidade.

---

## SEÇÃO O: AUDITORIA DE PROIBIÇÕES ABSOLUTAS

A auditoria inspecionou minuciosamente o código implementado para atestar o cumprimento das proibições absolutas da arquitetura:

| Proibição Arquitetural | Status | Evidência de Auditoria |
| :--- | :---: | :--- |
| **`AZURE_MUTATIONS = 0`** | **CUMPRIDO** | Zero chamadas HTTP `POST`, `PUT`, `PATCH`, `DELETE` em `ReadOnlyAzureDiscovery`. Zero criação de repositórios, times ou áreas. |
| **`BACKLOG_MATERIALIZATION = NOT_STARTED_BY_DESIGN`** | **CUMPRIDO** | Zero criação de cartões Epic, Feature, Story, Task em Azure DevOps ou geração de templates markdown em work dirs. Diferido para R7. |
| **`AGENT_DISPATCH = NOT_STARTED_BY_DESIGN`** | **CUMPRIDO** | Zero instanciação, despacho ou ativação de personas especialistas em R5. Diferido para marcos R8, R10 e R11. |
| **`SCHEDULER = NOT_STARTED_BY_DESIGN`** | **CUMPRIDO** | Zero ativação de daemons autônomos de polling em segundo plano em R5. Diferido para o marco R13. |

---

## SEÇÃO P: AUDITORIA DE GIT DIFF E ESCOPO

Foi verificado o diff completo do espaço de trabalho em relação ao commit base `c3971bc`:
- **Arquivos Fora de Escopo:** Nenhum arquivo alheio ao marco R5 foi modificado.
- **Arquivos Modificados de Forma Justificada:**
  - `scripts/project_context.py`: Eliminação de fallbacks cegos de cwd e compatibilidade com configuração declarativa de delivery.
  - `scripts/agent_squad.py`: Eliminação do fallback hardcoded `"Arthemis\agent-squad"` e integração com binding de entrega.
  - `scripts/bootstrap_project_squad.py`: Remoção de scaffolding proprietário de host.
- **Arquivos Novos em Escopo:** Pacote `scripts/runtime/delivery/`, testes `scripts/tests/test_r5_*.py`, documento de arquitetura e relatório de auditoria.

---

## SEÇÃO Q: ACCEPTANCE MATRIX

A tabela a seguir consolida a avaliação de aceitação final do marco R5 frente a todos os critérios e invariantes estabelecidos:

| Requisito / Critério de Aceitação | Especificação | Status | Evidência Concreta |
| :--- | :--- | :---: | :--- |
| **AC-R5-01: Baseline R5_START_SHA** | Iniciar sobre commit canônico rastreável | **PASS** | Commit `c3971bc0bc8d1bbd9947b154f627cf3f3b914306` verificado |
| **AC-R5-02: Package Scripts/Runtime/Delivery** | Implementar subsistema completo de delivery | **PASS** | 5 módulos Python (`__init__`, `errors`, `repository`, `azure_discovery`, `binding`) |
| **AC-R5-03: Segregação Produto vs Team Project** | Erradicar R0-ADO-002; proibir criação de projetos | **PASS** | `test_zero_team_project_creation_methods_in_delivery` aprovado |
| **AC-R5-04: Subordinação de Nós de Área** | Area path deve iniciar com o nome do Team Project | **PASS** | `test_product_vs_team_project_area_subordination_*` aprovados |
| **AC-R5-05: Descoberta Read-Only Azure** | Somente requisições HTTP GET; zero mutações | **PASS** | `ReadOnlyAzureDiscovery` estritamente read-only; 16 testes aprovados |
| **AC-R5-06: Detecção de Ambiguidade** | Falhar de forma fechada em duplicidades de repos/times | **PASS** | `AmbiguousRepositoryError` e `AmbiguousTeamError` validados |
| **AC-R5-07: Resolução Local Determinística** | Buscar declarativamente `.agents_squad/config/project.yaml` | **PASS** | `test_resolve_local_project_*` aprovados (30 testes) |
| **AC-R5-08: Path Containment Guard** | Bloquear diretórios `./work` locais aos projetos | **PASS** | `PathContainmentViolationError` disparado em violações |
| **AC-R5-09: Rejeição de Tokens Proibidos** | Bloquear `cbvgas`, `arthemis`, etc., fail-closed | **PASS** | `_check_forbidden_tokens()` validado em IDs e parâmetros ADO |
| **AC-R5-10: Segurança SEC-R1-01** | Zero PATs/credenciais persistidos ou em logs | **PASS** | `sanitize_credentials` e `sanitized_dict()` validados |
| **AC-R5-11: Persistência SQLite WAL** | Tabelas `project_bindings` e `project_binding_history` | **PASS** | DDL, índices, pragmas WAL e foreign keys validados |
| **AC-R5-12: Monotonicidade de Revisão** | Revisões incrementais + auditoria append-only | **PASS** | `test_upsert_binding_monotonic_revision_increment` aprovado |
| **AC-R5-13: Idempotência SHA-256** | Fingerprint determinístico para evitar mutações redundantes | **PASS** | `test_compute_binding_fingerprint_deterministic` aprovado |
| **AC-R5-14: Integração com R2 Outbox** | Emissão de eventos `agent_squad.delivery.*` | **PASS** | 5 testes de eventos aprovados em `test_r5_binding_events.py` |
| **AC-R5-15: Neutralidade de Host** | Zero scaffolding proprietário injetado no cliente | **PASS** | Remoção validada em `bootstrap_project_squad.py` |
| **AC-R5-16: Eliminação de Fallbacks Silenciosos** | Erradicar cwd fallback e default `"Arthemis\agent-squad"` | **PASS** | Validação no diff de `project_context.py` e `agent_squad.py` |
| **AC-R5-17: Pureza Arquitetural** | Zero dependências externas; biblioteca padrão Python | **PASS** | Análise estática confirma uso exclusivo de stdlib e domínio |
| **AC-R5-18: Suíte de Testes R5** | Cobertura integral de testes do marco R5 | **PASS** | 68/68 testes aprovados (100% PASS) |
| **AC-R5-19: Preservação Cumulativa R1-R4** | Garantir zero regressão nas fases anteriores | **PASS** | 218/218 testes aprovados (100% PASS) |
| **AC-R5-20: Validação de Estrutura Global** | Validar conformidade de agentes e skills | **PASS** | `validate_structure.py` exit code 0 |
| **AC-R5-21: Auditoria de Governança CLI** | Validar integridade via CLI `agent_squad.py audit` | **PASS** | `agent_squad.py audit` retorna `AUDIT_OK` |
| **AC-R5-22: R0 Diagnostic Preserved** | Suite diagnóstica R0 mantida inalterada | **PASS** | `r0_workitem_ado_contract_red.py` intacto |

---

## SEÇÃO R: VEREDITO FORMAL DE AUDITORIA

Com base em inspeção estática rigorosa de 100% dos arquivos do pacote `scripts/runtime/delivery/`, verificação detalhada do git diff, confirmação da eliminação de fallbacks silenciosos e scaffolding proprietário, conformidade estrita com a política de segurança de credenciais SEC-R1-01, execução de 68/68 testes de R5 e preservação integral de 218/218 testes cumulativos dos marcos R1 a R4:

Eu, na condição de **09-code-reviewer** (Static Analysis & Code Quality Auditor), declaro formalmente o marco **R5 — PROJECT + DELIVERY BACKEND BINDING** integralmente **APROVADO**:

```text
================================================================================
FINAL VERDICT: R5_CODE_REVIEW = APPROVED
================================================================================
```
