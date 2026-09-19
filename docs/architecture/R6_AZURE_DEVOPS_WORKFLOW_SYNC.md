# R6 — AZURE DEVOPS WORKFLOW + BIDIRECTIONAL SYNC ARCHITECTURAL SPECIFICATION
## Enterprise Architecture Specification · Ports & Adapters Sync Plane · Transactional Outbox · Monotonic Revisions · Deterministic Drift Reconciliation · Strict Secret Containment

**Document ID:** `DOC-ARCH-R6-AZURE-DEVOPS-WORKFLOW-SYNC`  
**Milestone:** `R6 — AZURE DEVOPS WORKFLOW + BIDIRECTIONAL SYNC`  
**Stage:** `STAGE B — SYNC ARCHITECTURE`  
**Date:** 2026-09-18  
**Author / Lead:** `04-solution-architect` (Martin Fowler & Gregor Hohpe — Solution Architect & Enterprise Integration Lead)  
**Collaborators & Reviewers:**  
- `27-platform-engineer` (Platform Engineering, Azure DevOps REST API & Infrastructure Discovery)  
- `13-devops-release-engineer` (Release Engineering, Service Hooks & Outbound/Inbound Pipeline Gates)  
- `14-governance-auditor` (Segregation of Duties, Secret Containment & Audit Ledger Invariants)  
**Status:** `APPROVED` (`R6_SYNC_DESIGN = APPROVED`)

---

## 1. SCOPE

### 1.1 Purpose & Authority
This specification establishes the authoritative, enterprise-grade architecture for **Milestone R6: Azure DevOps Workflow + Bidirectional Sync** within the Agent Squad platform.

Building directly upon the foundations established in prior milestones:
- **R1 (`DOC-ARCH-R1-CANONICAL-CONTRACTS`):** Canonical domain models (`AdoWorkItemBinding`, `SyncState`, `SyncStatus`, `ReconciliationDecision`, `ReconciliationAction`, `ReconciliationOutcome`).
- **R2 (`DOC-ARCH-R2-EVENT-TRIGGER-ENGINE`):** Transactional SQLite event outbox, deduplication, and retry policies (`DomainEvent`, `RetryPolicy`, `EventDelivery`).
- **R3 (`DOC-ARCH-R3-WORK-ITEM-RUNTIME-MIGRATION`):** Normalized hierarchical work item tree (`EPIC -> FEATURE -> STORY -> TASK`), canonical IDs, and isolated path containment.
- **R4 (`DOC-ARCH-R4-MANDATORY-LIFECYCLE-ENGINE`):** Canonical 13-stage delivery lifecycle engine, formal gate checkpoints (`G1–G6`), and transition invariant enforcement.
- **R5 (`DOC-ARCH-R5-PROJECT-DELIVERY-BINDING`):** Sole source of authority for project identity, enterprise Team Project decoupling, and read-only topology discovery.

**Milestone R6 establishes the bidirectional synchronization and delivery mutation plane connecting the governed local Agent Squad lifecycle with Microsoft Azure DevOps Boards & Repos.**

The Sync Plane resides exclusively within:
```
scripts/runtime/sync/
```
Its mandate is to serve as the **Authoritative Integration Boundary for External Delivery Synchronization**, guaranteeing:
1. Transactional, fail-closed outbox synchronization between local work item transitions and remote Azure Boards work items.
2. Monotonic revision tracking (`System.Rev`) using optimistic concurrency tests to prevent blind overwrites.
3. Strict parentage linkage using `System.LinkTypes.Hierarchy-Reverse` in top-down topological order, eliminating orphan cards.
4. Clean separation between enterprise containers (Team Projects) and product delivery resources (Repositories, Teams, Area Paths, Iteration Paths).
5. Robust inbound event ingestion via authenticated, deduplicated Service Hooks.
6. Deterministic drift reconciliation governed by canonical R4 lifecycle rules with zero silent swallowings.
7. Absolute containment of credentials (SEC-R1-01), zero secrets in databases, logs, event payloads, or disk.

```
+-----------------------------------------------------------------------------------+
|                                HOST RUNTIME & CLI                                 |
|          (Antigravity, Claude Desktop, Cursor, squad advance-state, CI)          |
+-----------------------------------------------------------------------------------+
                                        | consumes
                                        v
+-----------------------------------------------------------------------------------+
|                        CANONICAL LIFECYCLE ENGINE (R4)                           |
|                    (scripts/runtime/lifecycle/engine.py)                          |
+-----------------------------------------------------------------------------------+
                                        | emits state transitions
                                        v
+-----------------------------------------------------------------------------------+
|                           TRANSACTIONAL SYNC OUTBOX                               |
|                  (scripts/runtime/sync/outbox.py -> SQLite WAL)                   |
|                Tabela: delivery_sync_outbox in %SQUAD_RUNTIME%/banco/squad.db     |
+-----------------------------------------------------------------------------------+
             |                                                  ^
             | delivers via worker                              | enqueues inbound
             v                                                  |
+------------------------------------+          +-----------------------------------+
|      OUTBOUND SYNC DISPATCHER      |          |       INBOUND WEBHOOK SERVICE     |
|   (scripts/runtime/sync/outbound)  |          |   (scripts/runtime/sync/inbound)  |
| - Optimistic Concurrency (/rev)    |          | - HMAC-SHA256 Token Auth          |
| - Hierarchy-Reverse Linking        |          | - Deduplication & Replay Guard    |
| - Exponential Backoff Retries      |          | - Fail-Closed Drift Evaluation    |
+------------------------------------+          +-----------------------------------+
                 \                                  /
                  \                                /
                   v                              v
+-----------------------------------------------------------------------------------+
|                       AZURE DEVOPS REST API v7.1 ADAPTER                          |
|                     (integrations/devops_platform_connector.py)                   |
+-----------------------------------------------------------------------------------+
                                        | HTTPS (JSON-Patch / REST)
                                        v
+-----------------------------------------------------------------------------------+
|                         MICROSOFT AZURE DEVOPS SERVICES                           |
|       (Shared Enterprise Team Project · Product Repos · Boards · Areas · Teams)   |
+-----------------------------------------------------------------------------------+
```

---

## 2. NON-GOALS

To maintain absolute architectural clarity and segregation of duties, Milestone R6 explicitly declares the following **Non-Goals**:

1. **ZERO Enterprise Team Project Creation:** R6 will NEVER execute `POST /_apis/projects` to create a top-level Azure DevOps Team Project. Team Projects are corporate multi-tenant containers governed by enterprise administrators.
2. **ZERO Backlog Planning & Sizing Deliberation:** R6 does NOT perform backlog decomposition, user story discovery, Fibonacci sizing estimation, or QBC (Query Before Create) backlog queries. Backlog materialization and QBC belong strictly to **Milestone R7 (`R7 — BACKLOG/QBC/MATERIALIZATION`)**.
3. **ZERO Continuous Background Daemons:** R6 does NOT implement long-running OS background daemons, persistent while-loops, or unmonitored background threads. Outbox processing is driven explicitly via scheduled invocations, event triggers, or CLI commands. Autonomous watchdog scheduling belongs to **Milestone R13 (`R13 — WATCHDOG & MAINTENANCE`)**.
4. **ZERO Specialist Agent Dispatching:** R6 does NOT activate autonomous specialist personas (`06-software-engineer`, `10-software-quality-engineer`, `11-security-red-team`, etc.). Agent orchestration belongs to **Milestone R8/R10/R11**.
5. **ZERO Direct Bypass of Canonical Lifecycle:** External tools, webhooks, or users cannot mutate local work item states directly into completed SDLC phases without passing through the canonical R4 lifecycle engine and formal gate evaluations.
6. **ZERO Credential Handling or PAT Storage:** R6 does NOT store Personal Access Tokens (PAT) or plaintext passwords in database columns, configuration files, or logs.

---

## 3. PRIOR CONTRACTS CONSUMED (R1, R2, R3, R4, R5)

Milestone R6 directly consumes and honors existing immutable specifications without deviation:

| Consumed Contract | Origin Milestone | Specific Types & Invariants Consumed in R6 |
| :--- | :--- | :--- |
| **Domain Models & Contracts** | **R1** (`DOC-ARCH-R1`) | `AdoWorkItemBinding`, `SyncState`, `SyncStatus`, `ReconciliationDecision`, `ReconciliationAction`, `ReconciliationOutcome`, `BaseDomainModel`, `ValidationError`, `canonical_json`. |
| **Event Outbox & Retries** | **R2** (`DOC-ARCH-R2`) | `DomainEvent`, `TriggerPolicy`, `RetryPolicy` (initial=1s, multiplier=2.0, max=60s, max_attempts=3), `EventDelivery`, `DeliveryStatus`. Emits events under namespace `agent_squad.delivery.sync.*`. |
| **Work Item Hierarchy & Path Containment** | **R3** (`DOC-ARCH-R3`) | Canonical IDs (`EPIC-xxx`, `FEATURE-xxx`, `STORY-xxx`, `TASK-xxx`), `WorkItemKind`, `WorkItem` entity, `PathContainmentGuard`. Work items on disk strictly at `%SQUAD_RUNTIME%/work/<project_id>/<canonical_id>/`. |
| **Lifecycle State Machine & Gates** | **R4** (`DOC-ARCH-R4`) | `LifecycleStage` (13 stages), `GateId` (`G1`–`G6`), `GateDecision`, `StagePolicy`. R6 maps Azure states to canonical stages and blocks illegal remote transitions fail-closed. |
| **Project Delivery Binding** | **R5** (`DOC-ARCH-R5`) | `ProjectBinding`, `AdoBinding`, `SqliteBindingRepository`, `DeliveryBackendKind.AZURE_DEVOPS`, enterprise Team Project decoupling, area/iteration topology resolution. |

---

## 4. AZURE RESOURCE OWNERSHIP: CORPORATE TEAM PROJECT VS PRODUCT

### 4.1 Corporate Shared Container Principle
In an enterprise environment, an Azure DevOps Team Project represents an administrative boundary encompassing shared security policies, notification templates, build pools, and process configurations. Multiple product teams and software services reside within a single Team Project.

Milestone R6 enforces strict resource ownership:
1. **Team Project (`team_project_name` / `team_project_id`):** 
   - **Ownership:** Corporate IT / DevOps Infrastructure.
   - **Agent Squad Access:** `READ-ONLY / CONSUMER`.
   - **Constraint:** Creation (`POST /_apis/projects`) or deletion (`DELETE /_apis/projects/{id}`) by Agent Squad is **CATEGORICALLY PROHIBITED**. Any attempt violates invariant `R0-ADO-002` and raises `ForbiddenResourceMutationError`.
2. **Product Git Repository (`repository_name` / `repository_id`):**
   - **Ownership:** Product-specific.
   - **Classification:** May be `MANAGED` (provisioned/maintained by Squad) or `EXTERNAL` (pre-existing corporate repo).
3. **Product Engineering Team (`assigned_team_name` / `assigned_team_id`):**
   - **Ownership:** Product-specific.
   - **Classification:** May be `MANAGED` or `EXTERNAL`.
4. **Classification Nodes (Area Paths & Iteration Paths):**
   - **Ownership:** Product-specific subtree (e.g., `CorporateProject\ProductDomain\Component`).
   - **Classification:** `MANAGED` nodes under the project root.

### 4.2 Resource Ownership Matrix
```
+---------------------------+---------------+------------------+-----------------------------+
| Azure Resource            | Ownership     | Management Mode  | Permitted Action in R6      |
+---------------------------+---------------+------------------+-----------------------------+
| Team Project              | Corporate IT  | EXTERNAL_ONLY    | Query, Validate (NEVER MUT) |
| Git Repository            | Product Squad | MANAGED/EXTERNAL | Discover, Link, Provision   |
| Area Path Node            | Product Squad | MANAGED/EXTERNAL | Discover, Create if Missing |
| Iteration Path Node       | Product Squad | MANAGED/EXTERNAL | Discover, Create if Missing |
| Board Columns & WIP       | Product Squad | MANAGED          | Reconcile, Align SDLC       |
| Service Hook Subscription | Product Squad | MANAGED          | Discover, Register, Rotate  |
| Work Items (Cards)        | Product Squad | MANAGED          | Create, Update, Link        |
+---------------------------+---------------+------------------+-----------------------------+
```

---

## 5. RECONCILIATION MODEL: DISCOVER, BIND, RESOLVE

The synchronization plane never creates resources or work items blindly. Every provisioning and synchronization workflow follows a strict three-phase reconciliation model:

```
[Target Entity / Name]
          │
          ▼
┌──────────────────┐
│ Discovery Query  │ (REST GET / WIQL Query / Classification Nodes)
└─────────┬────────┘
          │
          ├───────────────────────────────────────────────────────┐
          │                                                       │
          ▼ (0 matches)                                           ▼ (1 match)
┌─────────────────────────────────┐                     ┌──────────────────┐
│ Check Management Mode           │                     │ Bind Canonical   │
│ - If EXTERNAL: Abort NOT_FOUND  │                     │ Entity to Remote │
│ - If MANAGED: Provision Resource│                     │ Update squad.db  │
└─────────────────────────────────┘                     └──────────────────┘
                                                                  ▲
                                                                  │
                                            (> 1 matches)         │
                                  ┌───────────────────────────────┴┐
                                  │ Ambiguous Match Resolution     │
                                  │ - Status: AMBIGUOUS_MATCH      │
                                  │ - Action: FAIL-CLOSED BLOCK    │
                                  │ - Human Resolution Required    │
                                  └────────────────────────────────┘
```

1. **Discovery Phase:** Query remote Azure DevOps REST API for the specific resource (Repository by name, Area Path by hierarchical path, Work Item by title/hash tags).
2. **Cardinality Resolution:**
   - **Exact 0 Matches:**
     - If mode is `EXTERNAL`: Abort with `ResourceNotFoundError`.
     - If mode is `MANAGED`: Proceed to deliberate, audited resource provisioning.
   - **Exact 1 Match:** Verify resource compatibility, capture immutable remote ID and revision, and bind to canonical entity.
   - **Greater than 1 Match (>1):** Multiple matching resources detected (e.g., duplicate repository names across forks or ambiguous work item titles). Sync transitions immediately to `SyncStatus.CONFLICT` with reason `AMBIGUOUS_REMOTE_MATCH`. The operation fails closed, emitting an alert and requiring human architectural intervention.

---

## 6. WORK ITEM TYPE MAPPING ACROSS PROCESS TEMPLATES

Azure DevOps supports distinct process templates (Agile, Scrum, Basic, CMMI), each defining specific work item type taxonomies. R6 reads the resolved `process_template` from `ProjectBinding` (R5) and maps canonical `WorkItemKind` accordingly:

| Canonical WorkItemKind | Agile Process | Scrum Process | Basic Process | CMMI Process |
| :--- | :--- | :--- | :--- | :--- |
| **EPIC** | `Epic` | `Epic` | `Epic` | `Epic` |
| **FEATURE** | `Feature` | `Feature` | `Issue` *(or Feature)* | `Feature` |
| **STORY** | `User Story` | `Product Backlog Item` | `Issue` | `Requirement` |
| **TASK** | `Task` | `Task` | `Task` | `Task` |
| **BUG** | `Bug` | `Bug` | `Issue` | `Bug` |
| **SPIKE** | `User Story` *(Tag: Spike)* | `Product Backlog Item` *(Tag)* | `Issue` *(Tag)* | `Requirement` *(Tag)* |

### Invariant:
If a project's process template does not support a direct type mapping, the adapter falls back to the nearest supported hierarchy level with an explicit `System.Tags` annotation (e.g., `agent-squad; type:spike`), preserving semantic meaning without violating Azure process rules.

---

## 7. OUTBOUND CREATE: FROM CANONICAL WORK ITEMS TO AZURE BOARDS

### 7.1 Strict Preconditions
Outbound creation of work items in Azure DevOps occurs **only from existing, valid canonical WorkItems already instantiated locally** under `%SQUAD_RUNTIME%/work/<project_id>/<work_id>/`.
R6 NEVER fabricates work items in Azure without a local canonical counterpart.

### 7.2 Idempotent Duplicate Prevention
Prior to issuing HTTP `POST /_apis/wit/workitems/${type}`, the outbound engine executes duplicate prevention:
1. Verify `delivery_work_item_bindings` in `%SQUAD_RUNTIME%/banco/squad.db`. If `ado_id` exists, abort creation and route to update.
2. Query Azure DevOps via WIQL using a deterministic tag query:
   ```sql
   SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project 
   AND [System.Tags] CONTAINS 'canonical_id:${canonical_id}'
   ```
3. If an existing card is found, link immediately (`SyncStatus.SYNCED`), record revision, and avoid creating a duplicate card.

### 7.3 Payload Construction (JSON Patch)
Outbound creation uses `application/json-patch+json` with strictly typed fields:
```json
[
  {"op": "add", "path": "/fields/System.Title", "value": "STORY-042: Implement JWT Token Verification"},
  {"op": "add", "path": "/fields/System.Description", "value": "<div>...Structured HTML Description...</div>"},
  {"op": "add", "path": "/fields/System.AreaPath", "value": "SharedProject\\PaymentsTeam"},
  {"op": "add", "path": "/fields/System.IterationPath", "value": "SharedProject\\Sprint 24"},
  {"op": "add", "path": "/fields/Microsoft.VSTS.Scheduling.StoryPoints", "value": 5},
  {"op": "add", "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria", "value": "<ul><li>AC1...</li></ul>"},
  {"op": "add", "path": "/fields/System.Tags", "value": "agent-squad; canonical_id:STORY-042; risk:medium; stage:REQUIREMENTS_PRODUCT"},
  {
    "op": "add",
    "path": "/relations/-",
    "value": {
      "rel": "System.LinkTypes.Hierarchy-Reverse",
      "url": "https://dev.azure.com/org/SharedProject/_apis/wit/workItems/1039",
      "attributes": {"comment": "Canonical parent link FEATURE-012"}
    }
  }
]
```

---

## 8. OUTBOUND UPDATE: OPTIMISTIC CONCURRENCY & REVISION TESTING

### 8.1 Optimistic Concurrency Invariant
To eliminate blind overwrites (where an automated script silently destroys edits made by humans or peer systems in Azure Boards), all outbound updates MUST execute an atomic revision test:
```json
[
  {
    "op": "test",
    "path": "/rev",
    "value": 4
  },
  {
    "op": "add",
    "path": "/fields/System.State",
    "value": "Active"
  },
  {
    "op": "add",
    "path": "/fields/System.History",
    "value": "State advanced to IMPLEMENTATION by Agent Squad Lifecycle Engine."
  }
]
```

### 8.2 Handling HTTP 412 Precondition Failed
If the remote work item has evolved in Azure DevOps since the last sync (e.g., remote `System.Rev` is now 5, but local binding records 4), Azure DevOps responds with **HTTP 412 (Precondition Failed)** or **HTTP 400 (TF400898)**.
- **Handling:**
  1. The outbound engine traps the revision failure.
  2. The outbox item status transitions to `SyncStatus.CONFLICT`.
  3. Outbox worker does NOT retry blindly.
  4. The engine initiates an immediate **Inbound Refresh**, fetches remote revision 5, and invokes the **Conflict & Reconciliation Engine (Section 20)**.

---

## 9. PARENT RELATIONS & TOPOLOGICAL CREATION ORDER

### 9.1 Hierarchy-Reverse Linkage
Parent-child relationships in Azure DevOps are directional:
- Link type: `System.LinkTypes.Hierarchy-Reverse` represents **Child -> Parent**.
- Link type: `System.LinkTypes.Hierarchy-Forward` represents **Parent -> Child**.

Per Azure DevOps REST best practices, hierarchy links are established from the child pointing to its parent using `System.LinkTypes.Hierarchy-Reverse`.

### 9.2 Strict Topological Creation Order
Outbound synchronization enforces strict topological dependency ordering:
```
EPIC (Root) ──► FEATURE (Child of Epic) ──► STORY (Child of Feature) ──► TASK (Child of Story)
```
1. **Rule:** A child work item can NEVER be dispatched to Azure DevOps before its canonical parent has been successfully created and has obtained an authoritative remote `ado_id`.
2. **Orphan Prevention:** If `init_work_item` is invoked for `TASK-1001` whose parent `STORY-042` has `SyncStatus.PENDING_CREATE`, the creation of `TASK-1001` is held in `SyncStatus.PENDING_CREATE` until `STORY-042` transitions to `SyncStatus.SYNCED`.
3. **No Silent Orphans:** Creating work items as unanchored root items when a canonical parent exists is strictly prohibited. If parent resolution fails, the operation raises `OrphanWorkItemViolationError`.

---

## 10. REVISION MODEL & IDENTIFIER NAMESPACE SEGREGATION

### 10.1 Dual-Identifier Namespace
The architecture enforces strict segregation between local canonical identifiers and remote external identifiers:
- **Canonical Work Item ID (`work_item_id`):** Formatted string (`EPIC-001`, `FEATURE-012`, `STORY-042`, `TASK-1001`). Owned and generated exclusively by Agent Squad.
- **Remote Azure DevOps ID (`ado_id`):** Positive integer (`1042`, `1043`). Generated exclusively by Microsoft Azure DevOps.
- **Invariant:** Neither system overloads or aliases its primary key into the other's namespace. The relationship is stored in an audited relational mapping table.

### 10.2 Monotonic Revision Tracking
Azure DevOps increments `System.Rev` monotonically (1, 2, 3, ...) on every mutation.
- Local repository stores `remote_rev: int` in `delivery_work_item_bindings`.
- On every successful outbound sync or inbound event, `remote_rev` is updated to `MAX(remote_rev, payload.rev)`.
- If an event arrives with `payload.rev <= current_remote_rev`, the event is recognized as stale or duplicate and safely acknowledged as a NOOP.

---

## 11. SYNC STATE CANONICAL VOCABULARY

In strict compliance with `scripts/domain/sync.py`, the sync state machine governs every bound work item through the canonical `SyncStatus` enum:

```
                  ┌──────────────────────┐
                  │    PENDING_CREATE    │
                  └──────────┬───────────┘
                             │ Outbound Success
                             ▼
┌──────────────────┐  Sync   ┌───────────┐  Local Mutation  ┌──────────────────┐
│ FAILED_RETRYABLE │◄───────►│  SYNCED   │─────────────────►│  PENDING_UPDATE  │
└────────┬─────────┘ (Error) └─────┬─────┘                  └────────┬─────────┘
         │                         │                                 │
         │ Max Retries             │ Remote Drift                    │ Revision Conflict
         ▼                         ▼                                 ▼
┌──────────────────┐     ┌───────────────────┐             ┌──────────────────┐
│ FAILED_TERMINAL  │     │      CONFLICT     │◄────────────│  PENDING_DELETE  │
└──────────────────┘     └───────────────────┘             └──────────────────┘
```

- **`SYNCED`:** Local canonical work item and remote Azure work item are identical in state, revision, and metadata.
- **`PENDING_CREATE`:** Local entity exists; awaiting initial outbound creation in Azure DevOps.
- **`PENDING_UPDATE`:** Local entity has mutated (state, title, sizing); queued for outbound PATCH.
- **`PENDING_DELETE`:** Queued for remote soft-delete or state closure.
- **`CONFLICT`:** Divergent edits detected between local and remote revisions, requiring reconciliation.
- **`FAILED_RETRYABLE`:** Outbound sync failed due to transient error (HTTP 429, 502, 503, 504); eligible for exponential backoff retry.
- **`FAILED_TERMINAL`:** Outbound sync failed due to permanent error (HTTP 401, 403, 404, invalid field schema) or retries exhausted. Requires operator remediation.

---

## 12. SYNC OUTBOX: TRANSACTIONAL SQLite PERSISTENCE

### 12.1 Transactional Outbox Pattern
To prevent distributed transaction failures (where a local state transitions but the Azure DevOps call fails, or vice-versa), mutations to work items commit atomically to the **Sync Outbox** in SQLite within the same database transaction that updates local lifecycle state.

### 12.2 DDL Schema: `delivery_sync_outbox` & `delivery_work_item_bindings`
Location: `%SQUAD_RUNTIME%/banco/squad.db`

```sql
-- Authoritative Work Item to Azure DevOps Binding
CREATE TABLE IF NOT EXISTS delivery_work_item_bindings (
    work_item_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    ado_id INTEGER NOT NULL UNIQUE,
    remote_url TEXT NOT NULL,
    remote_rev INTEGER NOT NULL,
    sync_status TEXT NOT NULL,
    sync_hash TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY (project_id) REFERENCES project_bindings(project_id) ON DELETE CASCADE
);

-- Transactional Outbox Queue for External Sync
CREATE TABLE IF NOT EXISTS delivery_sync_outbox (
    outbox_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    operation TEXT NOT NULL,          -- 'CREATE', 'UPDATE', 'DELETE', 'RECONCILE'
    payload_json TEXT NOT NULL,       -- Canonical JSON Patch or entity payload
    status TEXT NOT NULL,             -- 'PENDING', 'IN_FLIGHT', 'COMPLETED', 'FAILED_RETRYABLE', 'FAILED_TERMINAL'
    expected_rev INTEGER,             -- Expected remote revision for optimistic concurrency
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TEXT NOT NULL,
    last_attempt_at TEXT,
    error_message TEXT,
    correlation_id TEXT NOT NULL,
    causation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (work_item_id) REFERENCES delivery_work_item_bindings(work_item_id) ON DELETE CASCADE
);

-- Indices for performance and polling
CREATE INDEX IF NOT EXISTS idx_sync_outbox_polling 
ON delivery_sync_outbox (status, next_attempt_at) 
WHERE status IN ('PENDING', 'FAILED_RETRYABLE');

CREATE INDEX IF NOT EXISTS idx_work_item_bindings_ado 
ON delivery_work_item_bindings (ado_id);

CREATE INDEX IF NOT EXISTS idx_sync_outbox_work_item 
ON delivery_sync_outbox (work_item_id);
```

---

## 13. RETRY, BACKOFF & DEAD-LETTER STRATEGY

### 13.1 Retry Policy Invariants
The sync outbox reuses the canonical `RetryPolicy` defined in `scripts/domain/events.py`:
- `max_attempts`: 3
- `initial_backoff_seconds`: 1.0
- `backoff_multiplier`: 2.0
- `max_backoff_seconds`: 60.0

### 13.2 Retry Schedule Calculation
Upon receiving a transient failure (HTTP 429, 502, 503, 504, connection reset):
1. If Azure DevOps provides an HTTP header `Retry-After: <seconds>`, `next_attempt_at` is set to `now() + seconds`.
2. Otherwise, exponential backoff with jitter is applied:
   $$\text{backoff} = \min(60.0, 1.0 \times 2^{(\text{attempt} - 1)}) \pm \text{jitter}$$
3. Attempt counter is incremented: `attempt_count += 1`.
4. If `attempt_count >= max_attempts`, the item transitions to `status = 'FAILED_TERMINAL'` (Dead-Letter).
5. A `DomainEvent` of type `agent_squad.delivery.sync.dead_letter` is emitted via the R2 event store.

### 13.3 Explicit Invocation Model
Outbox draining is **never** delegated to an uncontrolled infinite loop. It is processed deterministically via:
- Event triggers upon lifecycle state transitions.
- The CLI command: `squad sync drain-outbox [--project <id>] [--max-items <n>]`.
- Scheduled execution by the external host orchestrator or CI pipeline.

---

## 14. STATE MAPPING: CANONICAL LIFECYCLE (R4) VS AZURE STATES

The Agent Squad SDLC enforces thirteen (13) canonical lifecycle stages. Because Azure DevOps Agile/Scrum process templates contain fewer states (typically `New`, `Active`, `Resolved`, `Closed`), the mapping is explicitly defined:

| Canonical SDLC Stage (R4) | Board Column Name | Agile Process `System.State` | Scrum Process `System.State` | Board `columnType` |
| :--- | :--- | :--- | :--- | :--- |
| **INTAKE** | Blueprint | `New` | `To Do` | `incoming` |
| **DISCOVERY** | Blueprint | `New` | `To Do` | `incoming` |
| **REQUIREMENTS_PRODUCT** | Blueprint | `New` | `To Do` | `incoming` |
| **PLANNING** | Blueprint | `New` | `To Do` | `incoming` |
| **ARCHITECTURE_DESIGN** | Blueprint | `New` | `To Do` | `incoming` |
| **READINESS_SCAFFOLDING** | Scaffolding | `Active` | `In Progress` | `inProgress` |
| **IMPLEMENTATION** | Implementation | `Active` | `In Progress` | `inProgress` |
| **CODE_REVIEW** | Code Review | `Active` | `In Progress` | `inProgress` |
| **SECURITY_REVIEW** | Security Review | `Active` | `In Progress` | `inProgress` |
| **TEST_VALIDATION** | Quality Validation | `Resolved` *(Task: Active)* | `Done` *(or Committed)* | `inProgress` |
| **QA_VALIDATION** | Quality Validation | `Resolved` *(Task: Active)* | `Done` | `inProgress` |
| **GOVERNANCE_RELEASE** | Governance Release | `Resolved` *(Task: Active)* | `Done` | `inProgress` |
| **DONE** | Done | `Closed` | `Done` | `outgoing` |

---

## 15. BOARD COLUMN VS WORKFLOW STATE DISTINCTION

### 15.1 The Collapsing State Problem
As shown in Section 14, five distinct local stages (`READINESS_SCAFFOLDING`, `IMPLEMENTATION`, `CODE_REVIEW`, `SECURITY_REVIEW`) all collapse into the single Azure state `Active`. If an adapter updates only `System.State = "Active"`, Azure DevOps automatically snaps the card into the **first** column mapped to `Active` (Scaffolding). Moving a card across columns that share the same `System.State` requires explicitly manipulating the Kanban board column fields.

### 15.2 Dual-Field Outbound Update
To reflect true progress on the visual Azure Board without corrupting the underlying process state machine, outbound updates patch both fields simultaneously:
1. `System.State`: Mapped to the valid process template state.
2. `System.BoardColumn`: Mapped to the specific target column name (`Implementation`, `Code Review`, etc.).
3. `System.Tags`: Appends tag `stage:<CANONICAL_STAGE>` ensuring lossless roundtrip disambiguation.

---

## 16. INBOUND SYNC: EVENT INGESTION & FAIL-CLOSED VALIDATION

### 16.1 Inbound Processing Flow
When an external mutation occurs in Azure DevOps (e.g., a human drags a card on the Kanban board or edits fields via the web UI), the event enters the sync plane:

```
[Azure DevOps Mutation]
          │
          ▼
┌─────────────────────────────────┐
│ Service Hook HTTP Receiver      │ (POST /api/v1/sync/webhooks/azure)
└────────────────┬────────────────┘
                 │ 1. Authenticate Token / Shared Secret
                 │ 2. Deduplicate messageId / eventId
                 ▼
┌─────────────────────────────────┐
│ Inbound Event Normalizer        │
└────────────────┬────────────────┘
                 │ Produces Canonical InboundSyncEvent
                 ▼
┌─────────────────────────────────┐
│ R4 Lifecycle Policy Validator   │
└────────────────┬────────────────┘
                 │
                 ├───────────────────────────────┐
                 │ Valid Transition              │ Invalid / Illegal Transition
                 ▼                               ▼
┌─────────────────────────────────┐   ┌─────────────────────────────────┐
│ Invoke R4 Transition Engine     │   │ Block Inbound Transition        │
│ Advance Local State             │   │ Emit WORKFLOW_DRIFT_DETECTED    │
│ Update Binding Revision         │   │ Schedule Board Column Revert    │
└─────────────────────────────────┘   └─────────────────────────────────┘
```

### 16.2 Fail-Closed Invariant
If a user in Azure DevOps moves a card from `Blueprint` directly to `Done`, bypassing required security reviews, tests, and gate approvals (`G1`–`G6`):
- The inbound sync engine validates the attempted transition against the local `StagePolicy` of R4.
- Because the transition violates required gates and prerequisites, **it is rejected fail-closed**.
- Local state remains untouched.
- An outbound corrective action is enqueued in `delivery_sync_outbox` to move the remote card back to its authorized board column with an explanatory audit comment in `System.History`.

---

## 17. SERVICE HOOKS: CONFIGURATION & PAYLOAD INGESTION

### 17.1 Supported Event Types
Agent Squad subscribes to the following Azure DevOps Service Hook event types:
1. `workitem.created`: New work item instantiated in Azure Boards.
2. `workitem.updated`: Work item fields, state, or relations mutated.
3. `workitem.commented`: Discussion comments added.
4. `git.pullrequest.created` & `git.pullrequest.updated`: PR lifecycle tracking.

### 17.2 Subscription Payload Schema
Subscriptions are registered targeting the Agent Squad inbound webhook endpoint with consumer action `httpRequest`:
```json
{
  "publisherId": "tfs",
  "eventType": "workitem.updated",
  "consumerId": "webHooks",
  "consumerActionId": "httpRequest",
  "publisherInputs": {
    "projectId": "7b8c2d91-..."
  },
  "consumerInputs": {
    "url": "https://squad.internal.corp/api/v1/sync/webhooks/azure",
    "httpHeaders": "X-Squad-Secret-Ref: vault://ado-webhook-token"
  }
}
```

---

## 18. WEBHOOK AUTHENTICITY & ZERO-SECRET INGESTION

### 18.1 Authenticity Verification Protocol
To guarantee that incoming HTTP payloads originate exclusively from the authorized Azure DevOps organization without exposing secrets:
1. **Secret Reference Resolution:** The project binding stores `service_hook_secret_ref` (e.g., `env://AZURE_WEBHOOK_SECRET` or `vault://ado-hook-secret`).
2. **Timing-Safe HMAC Verification:**
   - Azure DevOps Service Hooks provide basic authentication or custom headers. When configured with a shared secret:
   - The inbound receiver computes the HMAC-SHA256 signature over the raw request body using the securely resolved secret.
   - Comparison uses `hmac.compare_digest(computed_hash, header_hash)` to prevent timing side-channel attacks.
3. **Rejection:** Requests with missing headers, invalid signatures, or mismatched secrets are rejected immediately with **HTTP 401 Unauthorized** before any payload parsing or database operations occur.

---

## 19. REPLAY ATTACK PREVENTION & IDEMPOTENCY

### 19.1 Inbound Deduplication Guard
Every incoming webhook event payload from Azure DevOps contains a unique top-level identifier: `id` (or `notificationId`).
1. Inbound receiver verifies `inbound_event_ledger` in SQLite:
   ```sql
   CREATE TABLE IF NOT EXISTS delivery_inbound_events (
       event_id TEXT PRIMARY KEY,
       subscription_id TEXT NOT NULL,
       event_type TEXT NOT NULL,
       ado_id INTEGER NOT NULL,
       remote_rev INTEGER NOT NULL,
       received_at TEXT NOT NULL,
       payload_hash TEXT NOT NULL
   );
   ```
2. If `event_id` already exists in `delivery_inbound_events`, the receiver returns **HTTP 200 OK** immediately with body `{"status": "DUPLICATE_IGNORED"}`. No state machine processing is triggered.

### 19.2 Out-of-Order Packet Protection
Because HTTP delivery across networks is asynchronous, webhook notifications may arrive out of order (e.g., update for `rev=5` arrives before `rev=4` finishes processing).
- The receiver compares the event's `payload.resource.rev` against the recorded `remote_rev` in `delivery_work_item_bindings`.
- If `event_rev < recorded_rev`: The event is stale and discarded as an obsolete historical replay.
- If `event_rev == recorded_rev`: The event is redundant and acknowledged as NOOP.
- If `event_rev > recorded_rev + 1`: A revision gap is detected (an intermediate event was missed). The engine schedules a full work item refresh via REST `GET /_apis/wit/workitems/{id}` to restore authoritative state continuity.

---

## 20. CONFLICT MODEL & RECONCILIATION DECISION MATRIX

In strict conformance with `ReconciliationAction` from `scripts/domain/sync.py`, when divergent edits are detected between the local canonical state and the remote Azure state, the engine applies the **Deterministic Conflict Resolution Matrix**:

```
+────────────────────────┬─────────────────────────┬──────────────────────────────────┬─────────────────────────────────────────────────────────────+
| Local State            | Remote State            | ReconciliationAction Taken       | Architectural Rationale & Action                            |
+────────────────────────┬─────────────────────────┬──────────────────────────────────┬─────────────────────────────────────────────────────────────+
| Unchanged              | Unchanged               | NOOP                             | Both systems synchronized. Zero action.                     |
+────────────────────────┬─────────────────────────┬──────────────────────────────────┬─────────────────────────────────────────────────────────────+
| Advanced (Legally)     | Unchanged               | APPLY_LOCAL_TO_REMOTE            | Local engine progressed normally. Outbox sends PATCH.       |
+────────────────────────┬─────────────────────────┬──────────────────────────────────┬─────────────────────────────────────────────────────────────+
| Unchanged              | Legal Forward Move      | APPLY_REMOTE_TO_LOCAL            | Human advanced card legally in Azure Boards; R4 transitions.|
+────────────────────────┬─────────────────────────┬──────────────────────────────────┬─────────────────────────────────────────────────────────────+
| Any                    | Illegal Forward Skip    | BLOCK_ILLEGAL_REMOTE_TRANSITION  | Remote card jumped past gates/tests. Revert remote card.    |
+────────────────────────┬─────────────────────────┬──────────────────────────────────┬─────────────────────────────────────────────────────────────+
| Advanced (Local)       | Advanced (Remote Legal) | CONFLICT_REQUIRES_RESOLUTION     | Dual concurrent progression. Lock card & alert architect.   |
+────────────────────────┬─────────────────────────┬──────────────────────────────────┬─────────────────────────────────────────────────────────────+
| Field Edit (Local)     | Field Edit (Remote)     | CONFLICT_REQUIRES_RESOLUTION     | Divergent content. Require operator resolution.             |
+────────────────────────┬─────────────────────────┬──────────────────────────────────┬─────────────────────────────────────────────────────────────+
```

### Invariant:
The Agent Squad platform **NEVER** applies a blind "Last-Write-Wins" (LWW) resolution policy. Any scenario where local business logic and remote modifications cannot be safely reconciled deterministically requires transition to `SyncStatus.CONFLICT`, freezing automated mutations on that entity until human resolution is recorded.

---

## 21. ILLEGAL REMOTE TRANSITIONS & WORKFLOW DRIFT DETECTION

### 21.1 Definition of Illegal Remote Transition
An illegal remote transition occurs whenever an external actor (human developer, manager, external script) moves an Azure DevOps work item to a state that violates the canonical delivery lifecycle rules:
- Moving to `Closed`/`Done` when Gate `G6-governance-release` has not been approved.
- Moving to `Resolved`/`Quality Validation` without test execution receipts or test cases.
- Moving backwards into an invalid stage violating `allowed_next_stages` in `StagePolicy`.

### 21.2 Corrective Action Sequence
When an illegal remote transition is detected:
1. **Emit Domain Event:**
   ```json
   {
     "event_type": "agent_squad.delivery.sync.workflow_drift_detected",
     "work_item_id": "STORY-042",
     "project_id": "PRJ-CORR6",
     "source": "sync_inbound_validator",
     "payload": {
       "attempted_remote_state": "Closed",
       "canonical_local_state": "IMPLEMENTATION",
       "reason": "Illegal transition: Stage IMPLEMENTATION cannot jump to DONE without passing G4, G5, G6 gates."
     }
   }
   ```
2. **Retain Canonical Local State:** Local disk and SQLite databases do NOT change their lifecycle stage.
3. **Enqueue Corrective Patch in Outbox:** An outbound compensation message is queued to patch the Azure DevOps card back to `Active` (Board Column: `Implementation`), appending a comment explaining the governance reversion.

---

## 22. CAUSAL LOOP PREVENTION (ECHO SUPPRESSION)

### 22.1 The Distributed Loop Hazard
A classic failure mode in bidirectional synchronization is the infinite causal loop:
1. Agent Squad updates Azure DevOps ->
2. Azure DevOps fires `workitem.updated` webhook ->
3. Agent Squad receives webhook and treats it as a remote update ->
4. Agent Squad updates local entity ->
5. Local entity change queues another Azure DevOps update -> **Infinite Loop**.

### 22.2 Three-Tier Loop Suppression Guard
R6 eliminates causal echo loops using three complementary mechanisms:

```
[Incoming Update / Webhook]
             │
             ▼
   Tier 1: Correlation ID & Causation ID
   - If payload.header contains Squad correlation ID originating from local outbox:
     ==> DETECTED AS ECHO. DISCARD IMMEDIATELY (NOOP).
             │ (Not local correlation)
             ▼
   Tier 2: Revision Hash Match
   - Compare SHA-256 fingerprint of inbound payload with last synced hash in squad.db:
     ==> If payload_hash == sync_hash: DISCARD AS REDUNDANT (NOOP).
             │ (Different content)
             ▼
   Tier 3: Exact State & Column Equality
   - If incoming (State, BoardColumn) matches current local (State, BoardColumn):
     ==> DISCARD AS METADATA ECHO (NOOP).
```

---

## 23. MANAGED RESOURCE CREATION RULES (INFRASTRUCTURE)

When a project binding is configured in `MANAGED` mode, R6 is authorized to provision specific project-level infrastructure within the pre-existing enterprise Team Project:

### 23.1 Git Repositories
- **Creation Endpoint:** `POST {org}/{team_project}/_apis/git/repositories?api-version=7.1`
- **Rules:**
  - Name must match `binding.repository_name`.
  - Must verify discovery first. If exists, bind directly.
  - Team Project is NEVER created.

### 23.2 Area & Iteration Classification Nodes
- **Creation Endpoint:** `POST {org}/{team_project}/_apis/wit/classificationnodes/{areas|iterations}?api-version=7.1`
- **Rules:**
  - Nodes are created recursively down the configured path (e.g., `Root \ Domain \ Subdomain`).
  - Iteration nodes must record `startDate` and `finishDate` matching project cycles.

### 23.3 Engineering Teams & Board Columns
- **Creation Endpoint:** `POST {org}/_apis/projects/{team_project_id}/teams?api-version=7.1`
- **Board Customization:** `PUT {org}/{team_project}/{team_id}/_apis/work/boards/Stories/columns?api-version=7.1`
- **Rules:**
  - Board columns are aligned to the 7 canonical SDLC columns (`Blueprint`, `Scaffolding`, `Implementation`, `Code Review`, `Security Review`, `Quality Validation`, `Done`).
  - Existing custom columns must be preserved during reconciliation by mapping them to appropriate `columnType` categories.

---

## 24. SECURITY BOUNDARY & ZERO-SECRET STORAGE (SEC-R1-01)

### 24.1 Absolute Secret Isolation
In strict adherence to rule `SEC-R1-01`:
1. **Zero Secret Persistence:** Personal Access Tokens (PAT), client secrets, and webhook signing keys are **NEVER** stored in SQLite (`squad.db`), `status.yaml`, event payloads, or git commit history.
2. **Reference-Only Configuration:** All credentials are referenced via indirection keys:
   - `env://AZURE_DEVOPS_PAT`
   - `vault://azure-devops/release-token`
3. **Log Sanitization:** All HTTP logging and exception formatters pass through regex sanitizers stripping inline basic auth and token parameters (`https://***@dev.azure.com/...`).
4. **Segregation of Duties (SoD):** The identity running the sync plane is restricted to `squads@` service credentials, distinct from approval roles (`arthemis@`) and security audit roles (`cyber_red@`).

---

## 25. INTERFACE WITH MILESTONE R7 (BACKLOG & QBC)

### 25.1 Milestone Boundaries
- **Milestone R6 Responsibility:** The synchronization transport and state fidelity plane. R6 provides the reliable pipe, transactional outbox, revision testing, and drift reconciliation.
- **Milestone R7 Responsibility:** Backlog structuring, User Story splitting, QBC (Query Before Create) deduplication against remote epics, acceptance criteria drafting, and sizing verification.

### 25.2 Clean Contract Interface
R6 exposes a clean internal Python API for R7 to consume:
```python
class WorkItemSyncService:
    def enqueue_outbound_create(self, work_item: WorkItem, correlation_id: str) -> SyncState:
        """Enqueues a canonical work item for outbound creation in Azure Boards."""
        ...
        
    def get_sync_status(self, work_item_id: str) -> SyncState:
        """Queries authoritative sync status and remote binding details."""
        ...
```
**Constraint:** R6 does NOT query external backlogs to generate new local user stories. It operates strictly upon canonical entities provided to it.

---

## 26. INTERFACE WITH MILESTONE R13 (WATCHDOG & MAINTENANCE)

### 26.1 Outbox & Dead-Letter Observability
R6 structures the transactional outbox (`delivery_sync_outbox`) and dead-letter entities to be seamlessly supervised by **Milestone R13 (`R13 — WATCHDOG & MAINTENANCE`)**:
- Outbox entries have indexed `next_attempt_at` and `status` columns.
- R13 will implement the recurring scheduler/watchdog to inspect stale outbox entries, alert on `FAILED_TERMINAL` items, and execute periodic background maintenance.
- In R6, outbox drainage is exposed via discrete CLI commands and library calls without requiring background daemon processes.

---

## 27. REMAINING GAPS & IMPLEMENTATION BACKLOG (STAGE C & D)

With the completion and approval of this architectural specification, the remaining deliverables for Milestone R6 are organized into concrete implementation stages:

```
+───────────────────────────────────────────────────────────────────────────────────+
|                                MILESTONE R6 ROADMAP                               |
+───────────────────────────────────────────────────────────────────────────────────+
  [STAGE A] Read-Only Audit & Current Azure Write Map (COMPLETED in Stage A)
      │
      ▼
  [STAGE B] Authoritative Sync Architecture Specification (COMPLETED - THIS DOCUMENT)
      │
      ▼
  [STAGE C] Production Implementation:
      ├─ 1. scripts/runtime/sync/repository.py (Outbox & Binding SQLite WAL DDL)
      ├─ 2. scripts/runtime/sync/outbox_service.py (Transactional Outbox Dispatcher)
      ├─ 3. scripts/runtime/sync/reconciliation.py (Deterministic Drift Engine)
      ├─ 4. scripts/runtime/sync/inbound_receiver.py (Service Hook Ingestion & HMAC)
      ├─ 5. Decommissioning of legacy Team Project creation in azure_devops_lifecycle.py
      └─ 6. Removal of exception swallowing across agent_squad.py & connectors
      │
      ▼
  [STAGE D] Verification & Test Suite:
      ├─ 1. Unit Tests: Revisions, Outbox, Hierarchy, HMAC Auth, Conflict Resolution
      ├─ 2. Mock REST 7.1 Integration Tests: Fail-Closed Transitions, Replay Protection
      └─ 3. Audit Gate G4 Evaluation & Formal Certification
```

---

## 28. FORMAL ARCHITECTURAL VERDICT

As Solution Architect (`04-solution-architect`), with systems design review from Platform Engineer (`27-platform-engineer`), Release Engineer (`13-devops-release-engineer`), and Governance Auditor (`14-governance-auditor`):

1. This specification exhaustively addresses all 27 required architectural areas of Milestone R6.
2. It completely eliminates legacy defects `R0-ADO-004`, `R0-ADO-005`, `R0-ADO-006`, `R0-ADO-007`, `R0-ADO-008`, and `R0-ADO-009`.
3. It enforces strict Clean Architecture / Hexagonal isolation, absolute credential safety under `SEC-R1-01`, monotonic revision checks, and fail-closed lifecycle governance.

**FORMAL VERDICT:**
```
R6_SYNC_DESIGN = APPROVED
```
