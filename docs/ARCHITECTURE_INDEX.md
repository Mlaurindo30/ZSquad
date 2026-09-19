# Agent Squad Architecture & Governance Index (R0 — R13.1)

Este índice consolida as especificações autoritativas de arquitetura e os relatórios de auditoria formal executados durante o ciclo de maturidade do **Agent Squad** (Marcos R0 até R13.1).

---

## 1. Marcos e Especificações de Arquitetura

| Marco | Título / Responsabilidade Canônica | Especificação Arquitetural |
|---|---|---|
| **R0** | *Baseline de Falhas e Débitos Operacionais* | [`docs/audits/R0_CORE_WORKFLOW_FAILURE_BASELINE.md`](audits/R0_CORE_WORKFLOW_FAILURE_BASELINE.md) |
| **R1** | *Contratos de Domínio Canônicos (Stdlib-only)* | [`docs/architecture/R1_CANONICAL_DOMAIN_CONTRACTS.md`](architecture/R1_CANONICAL_DOMAIN_CONTRACTS.md) |
| **R2** | *Motor de Eventos e Gatilhos Reativos* | [`docs/architecture/R2_EVENT_TRIGGER_ENGINE.md`](architecture/R2_EVENT_TRIGGER_ENGINE.md) |
| **R3** | *Migração e Hierarquia Canônica de Work Items* | [`docs/architecture/R3_WORK_ITEM_RUNTIME_MIGRATION.md`](architecture/R3_WORK_ITEM_RUNTIME_MIGRATION.md) |
| **R4** | *Motor Obrigatório de Ciclo de Vida e Gates* | [`docs/architecture/R4_MANDATORY_LIFECYCLE_ENGINE.md`](architecture/R4_MANDATORY_LIFECYCLE_ENGINE.md) |
| **R5** | *Vinculação de Projetos e Descoberta Azure DevOps* | [`docs/architecture/R5_PROJECT_DELIVERY_BINDING.md`](architecture/R5_PROJECT_DELIVERY_BINDING.md) |
| **R6** | *Sincronização Bidirecional e Outbox Azure DevOps* | [`docs/architecture/R6_AZURE_DEVOPS_WORKFLOW_SYNC.md`](architecture/R6_AZURE_DEVOPS_WORKFLOW_SYNC.md) |
| **R7** | *Materialização de Backlog, QBC e Alocação de IDs* | [`docs/architecture/R7_BACKLOG_PLAN_QBC_MATERIALIZATION.md`](architecture/R7_BACKLOG_PLAN_QBC_MATERIALIZATION.md) |
| **R8** | *Roteamento de Especialistas Ciente do Estágio* | [`docs/architecture/R8_STAGE_AWARE_SPECIALIST_ROUTING.md`](architecture/R8_STAGE_AWARE_SPECIALIST_ROUTING.md) |
| **R9** | *Contexto de Trabalho, Resolução de Skills e Ativação* | [`docs/architecture/R9_WORK_CONTEXT_SKILLS_ACTIVATION.md`](architecture/R9_WORK_CONTEXT_SKILLS_ACTIVATION.md) |
| **R10** | *Autoridade de Sessão MCP, Preflight e Envelope* | [`docs/architecture/R10_MCP_SESSION_PREFLIGHT_DELEGATION.md`](architecture/R10_MCP_SESSION_PREFLIGHT_DELEGATION.md) |
| **R11** | *Despacho de Especialistas em Substratos Nativos do Host* | [`docs/architecture/R11_HOST_NATIVE_SPECIALIST_DISPATCH.md`](architecture/R11_HOST_NATIVE_SPECIALIST_DISPATCH.md) |
| **R12** | *Recibos de Execução e Imposição de Revisão/QA/SoD* | [`docs/architecture/R12_EXECUTION_REVIEW_TEST_QA_ENFORCEMENT.md`](architecture/R12_EXECUTION_REVIEW_TEST_QA_ENFORCEMENT.md) |
| **R13** | *Watchdog, Scheduler e Reconciliação Operacional* | [`docs/architecture/R13_WATCHDOG_SCHEDULER_RECONCILIATION.md`](architecture/R13_WATCHDOG_SCHEDULER_RECONCILIATION.md) |

---

## 2. Relatórios de Auditoria e Verificação Formal

- **R0 Baseline:** [`docs/audits/R0_CORE_WORKFLOW_FAILURE_BASELINE.md`](audits/R0_CORE_WORKFLOW_FAILURE_BASELINE.md)
- **R1 Domínio:** [`docs/audits/R1_CANONICAL_DOMAIN_CONTRACTS.md`](audits/R1_CANONICAL_DOMAIN_CONTRACTS.md)
- **R2 Eventos:** [`docs/audits/R2_EVENT_TRIGGER_ENGINE.md`](audits/R2_EVENT_TRIGGER_ENGINE.md)
- **R3 Work Items:** [`docs/audits/R3_WORK_ITEM_RUNTIME_MIGRATION.md`](audits/R3_WORK_ITEM_RUNTIME_MIGRATION.md)
- **R4 Ciclo de Vida:** [`docs/audits/R4_MANDATORY_LIFECYCLE_ENGINE.md`](audits/R4_MANDATORY_LIFECYCLE_ENGINE.md)
- **R5 Vinculação:** [`docs/audits/R5_PROJECT_DELIVERY_BINDING.md`](audits/R5_PROJECT_DELIVERY_BINDING.md)
- **R6 Sincronização ADO:** [`docs/audits/R6_AZURE_DEVOPS_WORKFLOW_SYNC.md`](audits/R6_AZURE_DEVOPS_WORKFLOW_SYNC.md)
- **R7 Backlog:** [`docs/audits/R7_BACKLOG_PLAN_QBC_MATERIALIZATION.md`](audits/R7_BACKLOG_PLAN_QBC_MATERIALIZATION.md)
- **R8 Roteamento:** [`docs/audits/R8_STAGE_AWARE_SPECIALIST_ROUTING.md`](audits/R8_STAGE_AWARE_SPECIALIST_ROUTING.md)
- **R9 Ativação:** [`docs/audits/R9_WORK_CONTEXT_SKILLS_ACTIVATION.md`](audits/R9_WORK_CONTEXT_SKILLS_ACTIVATION.md)
- **R10 Delegação:** [`docs/audits/R10_MCP_SESSION_PREFLIGHT_DELEGATION.md`](audits/R10_MCP_SESSION_PREFLIGHT_DELEGATION.md)
- **R11 Despacho:** [`docs/audits/R11_HOST_NATIVE_SPECIALIST_DISPATCH.md`](audits/R11_HOST_NATIVE_SPECIALIST_DISPATCH.md)
- **R12 Recibos & SoD:** [`docs/audits/R12_EXECUTION_REVIEW_TEST_QA_ENFORCEMENT.md`](audits/R12_EXECUTION_REVIEW_TEST_QA_ENFORCEMENT.md)
- **R12.1 Semântica de Execução:** [`docs/audits/R12_1_EXECUTION_SEMANTICS_GOVERNANCE_VERIFICATION.md`](audits/R12_1_EXECUTION_SEMANTICS_GOVERNANCE_VERIFICATION.md)
- **R13 Watchdog & Scheduler:** [`docs/audits/R13_WATCHDOG_SCHEDULER_RECONCILIATION.md`](audits/R13_WATCHDOG_SCHEDULER_RECONCILIATION.md)
- **R13.1 Recuperação & Multi-Processo:** [`docs/audits/R13_1_OPERATIONAL_CONTROL_VERIFICATION.md`](audits/R13_1_OPERATIONAL_CONTROL_VERIFICATION.md)
