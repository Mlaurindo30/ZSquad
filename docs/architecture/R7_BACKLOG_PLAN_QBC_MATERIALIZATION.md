# R7 — BACKLOG PLAN + SEMANTIC QBC + HIERARCHICAL MATERIALIZATION ARCHITECTURAL SPECIFICATION
## Enterprise Architecture Specification · Atomic Backlog Planning · Multi-Source Semantic Query-Before-Create (QBC) · Resilient Hierarchical Materialization Saga · Fail-Closed Drift Prevention · Clean Architecture Integration

**Document ID:** `DOC-ARCH-R7-BACKLOG-PLAN-QBC-MATERIALIZATION`  
**Milestone:** `R7 — BACKLOG PLAN + SEMANTIC QBC + MATERIALIZATION`  
**Stage:** `STAGE B — BACKLOG ARCHITECTURE`  
**Date:** 2026-09-18  
**Author / Lead:** `04-solution-architect` (Martin Fowler & Gregor Hohpe — Clean Architecture & Systems Integration Pioneer)  
**Collaborators & Reviewers:**  
- `00-delivery-orchestrator` (Delivery Orchestrator & Henrik Kniberg Governance)  
- `27-platform-engineer` (Platform Engineering, Azure DevOps & Baseline Audit)  
- `40-agile-coach` (Agile Coaching, Sizing, Fibonacci Boundaries & Vertical Slicing)  
- `14-governance-auditor` (Segregation of Duties, Governance Gates & Audit Ledger Invariants)  
**Status:** `APPROVED` (`R7_BACKLOG_DESIGN = APPROVED`)

---

## TABLE OF CONTENTS
1. [Scope](#1-scope)
2. [Non-Goals](#2-non-goals)
3. [Current Defects (Stage A Mapping)](#3-current-defects-stage-a-mapping)
4. [BacklogPlan Authority](#4-backlogplan-authority)
5. [Plan Lifecycle (`DRAFT -> VALIDATED -> APPROVED -> MATERIALIZED`, `REJECTED`)](#5-plan-lifecycle)
6. [Hierarchy (`EPIC -> FEATURE -> STORY -> TASK`) & Multi-Epic Support](#6-hierarchy--multi-epic-support)
7. [Content Contract & Quality Standards](#7-content-contract--quality-standards)
8. [Canonical ID Allocation Engine](#8-canonical-id-allocation-engine)
9. [QBC (Query Before Create) Architecture](#9-qbc-query-before-create-architecture)
10. [QBC Source Model & Authority Precedence](#10-qbc-source-model--authority-precedence)
11. [Exact Matching (Canonical ID, External Binding, Fingerprint)](#11-exact-matching)
12. [Semantic Matching (Lexical, N-Gram & Contextual Similarity)](#12-semantic-matching)
13. [Parent-Context Semantics & Scoped Disambiguation](#13-parent-context-semantics--scoped-disambiguation)
14. [Ambiguity Handling & Fail-Closed Review Gate](#14-ambiguity-handling--fail-closed-review-gate)
15. [Plan Validation Engine](#15-plan-validation-engine)
16. [Formal Approval Boundary & Segregation of Duties](#16-formal-approval-boundary--segregation-of-duties)
17. [Hierarchical Materialization Saga](#17-hierarchical-materialization-saga)
18. [R3 Local Runtime Integration](#18-r3-local-runtime-integration)
19. [R6 Sync Plane & Transactional Outbox Integration](#19-r6-sync-plane--transactional-outbox-integration)
20. [Partial Failure Handling & Crash Recovery](#20-partial-failure-handling--crash-recovery)
21. [Idempotency & Re-execution Guarantees](#21-idempotency--re-execution-guarantees)
22. [Persistence Architecture & SQLite WAL Schema](#22-persistence-architecture--sqlite-wal-schema)
23. [Domain Events & R2 Trigger Bus Integration](#23-domain-events--r2-trigger-bus-integration)
24. [Concurrency Control & Multi-Agent Allocation Locks](#24-concurrency-control--multi-agent-allocation-locks)
25. [Agent-Routing Architectural Boundary](#25-agent-routing-architectural-boundary)
26. [Remaining Phases & Roadmap Boundaries (R8+)](#26-remaining-phases--roadmap-boundaries-r8)

---

## 1. SCOPE

### 1.1 Purpose & Authority
This specification establishes the authoritative technical architecture for **Milestone R7: Backlog Plan, Semantic Query-Before-Create (QBC), and Hierarchical Materialization** within the Agent Squad platform.

Milestone R7 unifies the domain modeling established in R1 with the physical filesystem hierarchy of R3, the transactional state engine of R4, and the external delivery synchronization plane of R6. It elevates backlog planning from an uncoordinated, one-by-one file generation mechanism into a formal, governed, transactional sub-system.

```
+-------------------------------------------------------------------------------------------------+
|                                     MILESTONE R7 CONTEXT                                        |
+-------------------------------------------------------------------------------------------------+
|  [R1: Canonical Domain] ---> BacklogPlan, BacklogPlanItem, WorkHierarchy                        |
|  [R2: Event Engine]     ---> DomainEvent outbox, event deliveries, retry policies               |
|  [R3: Local Runtime]    ---> Normalized path containment (work/<project_id>/...), Templates     |
|  [R4: Lifecycle Engine] ---> Canonical 13-stage lifecycle, G1-G6 gate enforcement              |
|  [R5: Project Binding]  ---> Project identity, Team Project / Area Path discovery               |
|  [R6: Sync Outbox]      ---> Outbound creation queue, monotonic revisions, drift reconcile      |
+-------------------------------------------------------------------------------------------------+
                                                |
                                                v
+-------------------------------------------------------------------------------------------------+
|                         R7: BACKLOG PLAN, QBC & MATERIALIZATION ENGINE                         |
|                                                                                                 |
|   1. BacklogPlan Domain Model & Lifecycle (DRAFT -> VALIDATED -> APPROVED -> MATERIALIZED)       |
|   2. Tripartite QBC Engine (SQLite Fast-Index + Local Filesystem + Remote Azure WIQL)          |
|   3. Two-Tier Deduplication (Exact Identity + Parent-Aware Semantic NLP Matching)               |
|   4. Atomic Materialization Saga (R3 Physical Scaffold + R6 Outbox Transaction)                 |
+-------------------------------------------------------------------------------------------------+
```

### 1.2 Architectural Placement
The R7 engine resides in the runtime architecture under:
```
scripts/runtime/backlog/
├── __init__.py
├── plan_service.py         # Plan lifecycle orchestrator (Draft, Validate, Approve, Materialize)
├── qbc_engine.py           # Multi-source Query Before Create engine
├── semantic_matcher.py     # Lexical, token overlap, and parent-aware similarity scoring
├── id_allocator.py         # Collision-free canonical ID allocation across 4 namespaces
├── materializer_saga.py    # Atomic multi-tier materialization saga with rollback/recovery
├── repository.py           # SQLite persistence adapter for plans, items, and QBC decisions
```

---

## 2. NON-GOALS

To preserve clean separation of concerns and maintain tight architectural cohesion, Milestone R7 explicitly defines the following non-goals:

1. **No Agent Dispatching or Specialist Selection:** R7 does NOT assign agents, select personas, or invoke subagents. Determining which specialist executes a work item belongs exclusively to **Milestone R8 (Specialist Routing & Swarm Coordination)**.
2. **No Application Source Code Generation:** R7 creates work item scaffolding, governance metadata, and specification templates (`user-story.md`, `acceptance-criteria.md`, etc.). It does NOT write production application code or tests.
3. **No Direct Remote Board REST Mutations:** R7 does NOT invoke direct HTTP REST mutations against Azure DevOps. All remote creations are strictly dispatched via the **R6 Transactional Outbox** (`delivery_sync_outbox`).
4. **No Dynamic Workflow Transitions Beyond Intake:** R7 materializes work items strictly into their entry delivery stage (`G1_PROD_INTAKE` in status `DRAFT`). Advancing items across subsequent gates (`G1` through `G6`) is governed by the **R4 Lifecycle Engine**.
5. **No Ephemeral In-Memory Only Plans:** R7 prohibits transient backlog plans that exist solely in memory. Every plan must be persisted in SQLite WAL to ensure crash recovery, auditability, and multi-agent coordination.

---

## 3. CURRENT DEFECTS (STAGE A MAPPING)

The Stage A Baseline Audit documented six critical architectural defects in the existing codebase that R7 is designed to eradicate:

| Defect ID | Description in Existing Codebase | Severity | Resolution in R7 Architecture |
|---|---|---|---|
| **DEF-R7-01** | **Domain-Runtime Disconnection:** `BacklogPlan` in `scripts/domain/backlog.py` is disconnected from `scripts/agent_squad.py` CLI. Creation is performed via uncoordinated ad-hoc calls to `init-work-item`. | CRITICAL | Implement `BacklogPlanService` binding domain entities directly to CLI, SQLite persistence, and runtime materialization. |
| **DEF-R7-02** | **Primitive QBC:** Current QBC (`agent_squad.py:682-708`) only checks `epic` and `feature`, compares exact `work_id.lower() == cand_id.lower()`, and is completely disabled by `--force`. | CRITICAL | Multi-source QBC covering all tiers (`epic`, `feature`, `story`, `task`), lexical/semantic similarity matching, and audited overrides. |
| **DEF-R7-03** | **SQLite QBC Blindness:** `banco/squad.db` contains bindings and lifecycle tables, but is never queried prior to work item creation. | HIGH | First-class SQLite Fast-Index querying `delivery_work_item_bindings` and `work_item_lifecycle_state` in $O(1)$. |
| **DEF-R7-04** | **Azure Remote Blindness:** No WIQL query or remote search is performed before issuing creation requests to Azure DevOps. | HIGH | Remote WIQL pre-flight query integrated into QBC to catch existing boards cards before creating local/remote duplicates. |
| **DEF-R7-05** | **R6 Outbox Bypass:** `init_work_item()` invokes legacy `DevOpsPlatformConnector.create_work_item()` directly via synchronous HTTP, bypassing R6 outbox. | CRITICAL | Eradicate synchronous HTTP calls from creation flow; route all creations through `DeliverySyncService.enqueue_outbound_create()`. |
| **DEF-R7-06** | **"1 Product = 1 Epic" Assumption:** Scripts (`azure_devops_bootstrap.py`, wave materializers) hardcode single-epic roots or fixed paths, risking collisions. | HIGH | Enforce native multi-epic support across all planning tools, validating that each Feature resolves to a specific parent Epic. |

---

## 4. BACKLOGPLAN AUTHORITY

### 4.1 Sole Source of Planning Authority
A `BacklogPlan` is the sole authoritative entity through which new work items may be planned, sized, validated, and materialized in the Agent Squad system. 

```
                                    +-----------------------+
                                    |    User / Agent /     |
                                    |   Decomposition Tool  |
                                    +-----------------------+
                                                |
                                                v
                                    +-----------------------+
                                    |      BacklogPlan      |
                                    |    (Plan Aggregate)   |
                                    +-----------------------+
                                    | - plan_id: UUID       |
                                    | - project_id: str     |
                                    | - status: Status      |
                                    | - items: List[Item]   |
                                    | - created_by: str     |
                                    | - approved_by: Opt[str|
                                    +-----------------------+
                                                |
                                                v
                        +-----------------------------------------------+
                        |        Atomic Validation & Approval           |
                        | (QBC + Hierarchy + Fibonacci + Content Check) |
                        +-----------------------------------------------+
                                                |
                                                v
                        +-----------------------------------------------+
                        |         Hierarchical Materializer Saga        |
                        +-----------------------------------------------+
```

### 4.2 Invariant Rules of Plan Authority
1. **No Out-of-Plan Work Items:** Work items of tier `feature`, `story`, and `task` MUST NOT be initialized without belonging to an approved `BacklogPlan`. Single ad-hoc demands must be wrapped in a micro-plan containing 1 item.
2. **Immutable Approval:** Once a `BacklogPlan` reaches `APPROVED` status, its item list, hierarchy, and scoping parameters become completely immutable. Any scope change requires rejecting the plan or creating a delta plan.
3. **Pre-Materialization Integrity:** Materialization (`assert_can_materialize()`) is strictly prohibited unless the plan has successfully traversed `DRAFT -> VALIDATED -> APPROVED`.

---

## 5. PLAN LIFECYCLE

The lifecycle of a `BacklogPlan` follows a deterministic finite state machine (FSM) with strict guard conditions and audit trails.

```
       +-------------------------------------------------------+
       |                                                       |
       v                                                       |
   +-------+   validate()    +-----------+   approve()   +----------+   materialize()   +--------------+
   | DRAFT | --------------> | VALIDATED | ------------> | APPROVED | ----------------> | MATERIALIZED |
   +-------+                 +-----------+               +----------+                   +--------------+
       |                           |                           |
       | reject()                  | reject()                  | reject()
       v                           v                           v
   +----------------------------------------------------------------+
   |                            REJECTED                            |
   +----------------------------------------------------------------+
```

### 5.1 State Definitions & Invariants

#### 1. `DRAFT`
- **Description:** The plan is being authored, structured, or decomposed. Items are added, updated, or re-parented.
- **Allowed Transitions:** `VALIDATED`, `REJECTED`.
- **Invariants:** At least one `BacklogPlanItem` present. Proposed IDs conform to canonical syntax.

#### 2. `VALIDATED`
- **Description:** Automated technical validation has passed. QBC has executed across all 3 sources (SQLite, Filesystem, Azure WIQL) with zero unhandled duplicates. Hierarchy and Fibonacci constraints are satisfied.
- **Allowed Transitions:** `APPROVED`, `REJECTED`, `DRAFT` (if amended).
- **Invariants:** All items have valid parent references. Sizing $\le 8$ SP for Stories. No open ambiguity flags.

#### 3. `APPROVED`
- **Description:** Formal business and governance sign-off recorded. The plan is frozen and ready for execution.
- **Allowed Transitions:** `MATERIALIZED`, `REJECTED`.
- **Invariants:** `approved_by` is mandatory and non-empty. Plan contents are cryptographically hashed (`content_hash`).

#### 4. `MATERIALIZED`
- **Description:** Physical directories, metadata files (`status.yaml`), templates, SQLite lifecycle rows, and R6 outbox records have been successfully committed.
- **Allowed Transitions:** None (Terminal state).
- **Invariants:** All items exist on disk, in SQLite, and in `delivery_sync_outbox`.

#### 5. `REJECTED`
- **Description:** The plan was rejected due to structural violations, unresolved semantic duplicates, or governance denial.
- **Allowed Transitions:** None (Terminal state).

---

## 6. HIERARCHY & MULTI-EPIC SUPPORT

### 6.1 Four-Tier Canonical Hierarchy
Agent Squad enforces a strict four-tier delivery hierarchy:

$$\text{EPIC} \longrightarrow \text{FEATURE} \longrightarrow \text{STORY} \longrightarrow \text{TASK}$$

In addition, operational items attach to specific tiers:
- `BUG`: Attaches to `STORY` or `FEATURE`.
- `SPIKE`: Attaches to `FEATURE` or `EPIC`.
- `INCIDENT`: Standalone or attaches to `FEATURE`/`EPIC`.

```
                        +----------------------------+
                        |      EPIC (Parent=None)     |
                        +----------------------------+
                                      |
                                      v
                        +----------------------------+
                        |   FEATURE (Parent=EPIC)    |
                        +----------------------------+
                                      |
                                      v
                        +----------------------------+
                        |   STORY (Parent=FEATURE)   |
                        +----------------------------+
                                      |
                                      v
                        +----------------------------+
                        |     TASK (Parent=STORY)    |
                        +----------------------------+
```

### 6.2 Multi-Epic Architectural Mandate
1. **Coexistence of Multiple Epics:** A project MAY contain any number of parallel Epics under `work/<project_id>/` (e.g., `EPIC-001`, `EPIC-002`, `EPIC-003`).
2. **Prohibition of Implicit Single-Epic Root:** No component may assume that a project has a single root Epic. Every Feature MUST explicitly declare its `parent_id` matching an existing or planned `EPIC-XXX`.
3. **Cross-Epic Isolation:** Features, Stories, and Tasks are contained strictly within their parent Epic's physical and logical sub-tree.

---

## 7. CONTENT CONTRACT & QUALITY STANDARDS

To prevent the materialization of low-quality, vague, or placeholder items, every `BacklogPlanItem` must satisfy rigorous quality criteria before validation:

```
+---------------+------------------------------------------------+-----------------------------------------------+
| WorkItem Kind | Mandatory Fields                               | Required Initial Artifacts                    |
+---------------+------------------------------------------------+-----------------------------------------------+
| EPIC          | Title (>=10 chars), Vision, Business Goal      | epic.md, product-goal.md,                     |
|               | Scope In/Out, Architecture Guidelines          | architecture-vision.md                        |
+---------------+------------------------------------------------+-----------------------------------------------+
| FEATURE       | Title (>=10 chars), Functional Spec,           | feature-spec.md, component-design.md          |
|               | Component Architecture, Parent EPIC ID         |                                               |
+---------------+------------------------------------------------+-----------------------------------------------+
| STORY         | Title (>=10 chars), User Story Narrative,      | user-story.md, acceptance-criteria.md         |
|               | >=1 BDD Criteria (Given-When-Then),            |                                               |
|               | Fibonacci SP (1, 2, 3, 5, 8), Parent FEAT ID   |                                               |
+---------------+------------------------------------------------+-----------------------------------------------+
| TASK          | Title (>=10 chars), Technical Scope,           | task-scope.md                                 |
|               | Implementation Steps, Parent STORY ID          |                                               |
+---------------+------------------------------------------------+-----------------------------------------------+
```

### 7.1 Anti-Vagueness & Anti-Hallucination Gate
- **Prohibited Tokens:** Descriptions containing placeholders such as `"TBD"`, `"TODO"`, `"Lorem Ipsum"`, `"Implement later"`, or strings shorter than 10 characters are rejected with `ValidationError`.
- **BDD Anchor Requirement:** Stories must define at least one structured acceptance criterion:
  $$\text{Scenario: } \langle \text{name} \rangle \implies \text{Given } \langle \text{precondition} \rangle \land \text{When } \langle \text{action} \rangle \land \text{Then } \langle \text{outcome} \rangle$$
- **Fibonacci Strictness:** Story points must strictly belong to $\{1, 2, 3, 5, 8\}$. Any value $>8$ triggers a hard failure: `"Story exceeds 8 SP. Slicing required."`

---

## 8. CANONICAL ID ALLOCATION ENGINE

### 8.1 Four-Namespace Collision Protection
The `CanonicalIdAllocationService` guarantees collision-free sequence generation by verifying candidates against **four disjoint namespaces**:

$$\mathcal{N} = \mathcal{N}_{\text{SQLite}} \cup \mathcal{N}_{\text{Filesystem}} \cup \mathcal{N}_{\text{AzureBindings}} \cup \mathcal{N}_{\text{PlanInFlight}}$$

```
                                  +---------------------------------------+
                                  |     Candidate ID: "STORY-005"         |
                                  +---------------------------------------+
                                                      |
                  +-------------------+---------------+-------------------+-------------------+
                  |                   |                                   |                   |
                  v                   v                                   v                   v
        +-------------------+ +-------------------+             +-------------------+ +-------------------+
        |  1. SQLite State  | |   2. Filesystem   |             |  3. Azure Binding | | 4. In-Flight Plan |
        | (lifecycle_state) | |  (work/<proj>/..) |             | (ado_work_items)  | |  (current items)  |
        +-------------------+ +-------------------+             +-------------------+ +-------------------+
                  |                   |                                   |                   |
                  +-------------------+---------------+-------------------+-------------------+
                                                      |
                                                      v
                                        [ Any Match Detected? ]
                                            /           \
                                         YES             NO
                                         /                 \
                     [ Increment Sequence to 006 ]     [ ID Accepted ]
```

### 8.2 Canonical Patterns
- **EPIC:** `^EPIC-\d{3,}$` (e.g., `EPIC-001`)
- **FEATURE:** `^FEATURE-\d{3,}$` (e.g., `FEATURE-001`)
- **STORY:** `^STORY-\d{3,}$` (e.g., `STORY-001`)
- **TASK:** `^TASK-\d{4,}$` (e.g., `TASK-0001`)
- **OPERATIONAL:** `^(BUG|SPIKE|INCIDENT)-\d{3,}$`

Legacy prefixes (`FEAT-`, `US-`, `TK-`) are transparently normalized to canonical prefixes during parsing, but **never emitted** for newly allocated IDs.

---

## 9. QBC (QUERY BEFORE CREATE) ARCHITECTURE

### 9.1 The Tripartite QBC Funnel
Query Before Create operates across three complementary sources to ensure no work item is duplicated locally, remotely, or semantically:

```
                                +-------------------------------+
                                |  Proposed Item (Title, Kind)  |
                                +-------------------------------+
                                                |
                                                v
               +-----------------------------------------------------------------+
               |                   PHASE 1: SQLite Fast-Index                    |
               | Query banco/squad.db (work_item_lifecycle_state, bindings)      |
               +-----------------------------------------------------------------+
                                                | (No exact match)
                                                v
               +-----------------------------------------------------------------+
               |                  PHASE 2: Local Filesystem                      |
               | Scan work/<project_id>/ via WorkItemPathResolver                |
               +-----------------------------------------------------------------+
                                                | (No exact match)
                                                v
               +-----------------------------------------------------------------+
               |                 PHASE 3: Remote Azure Boards                    |
               | Execute WIQL Query against Area Path / Work Item Type           |
               +-----------------------------------------------------------------+
                                                | (No remote match)
                                                v
               +-----------------------------------------------------------------+
               |                 PHASE 4: Semantic NLP Matcher                   |
               | Lexical similarity, token overlap, parent-scoped comparison     |
               +-----------------------------------------------------------------+
                                                |
                                                v
                                        [ QBC PASS / FAIL ]
```

---

## 10. QBC SOURCE MODEL & AUTHORITY PRECEDENCE

When conflicting information is detected across sources, the system adheres to strict authority precedence:

| Source | Latency | Scope of Authority | Precedence Rank |
|---|---|---|---|
| **1. SQLite (`squad.db`)** | Sub-millisecond ($O(1)$ index) | Local lifecycle status, active reservations, outbox synchronization state. | **Rank 1 (Fastest / Authoritative for Local State)** |
| **2. Filesystem (`work/`)** | 10–50ms (Disk I/O) | Physical artifact reality, existing files, directory structures. | **Rank 2 (Authoritative for Physical Assets)** |
| **3. Azure DevOps (WIQL)** | 200–800ms (Network REST) | Remote board truth, enterprise identity (`System.Id`), remote card status. | **Rank 3 (Authoritative for External Identity)** |

### 10.1 Conflict Resolution Invariants
- If an item exists on the **Filesystem** but is missing from **SQLite**, the Filesystem wins: the ID is treated as occupied and an automated reconciliation record is scheduled.
- If an item exists in **Azure DevOps** with a title/scope identical to a proposed item, the plan blocks: the item must be bound to the existing Azure ID rather than creating a duplicate.

---

## 11. EXACT MATCHING

Exact matching evaluates three deterministic identifiers:

1. **Normalized Canonical ID Match:** Evaluates if `CanonicalIdService.normalize(proposed_id) == CanonicalIdService.normalize(existing_id)`.
2. **External Binding Match:** Evaluates if a proposed item references an `ado_id` that is already bound to another work item in `delivery_work_item_bindings`.
3. **Exact Scope Fingerprint Match:** Computes a SHA-256 hash of the normalized tuple:
   $$\text{Fingerprint} = \text{SHA256}(\text{kind} \parallel \text{parent\_id} \parallel \text{normalize\_text}(\text{title}))$$
   If an existing item possesses an identical fingerprint, it is flagged as an **EXACT DUPLICATE** ($Score = 1.00$) and creation is rejected immediately.

---

## 12. SEMANTIC MATCHING

To catch duplication where titles differ slightly (e.g., *"Database Migration Engine"* vs. *"Data Migration and Schema Engine"*), the `SemanticMatcher` computes a multi-factor similarity score:

### 12.1 Scoring Algorithm
The overall similarity $S(A, B) \in [0.0, 1.0]$ between candidate $A$ and existing item $B$ is defined as:

$$S(A, B) = w_1 \cdot J(A, B) + w_2 \cdot L(A, B) + w_3 \cdot C(A, B)$$

Where:
- $J(A, B)$: **Token Jaccard Similarity** over normalized, stopword-stripped token sets.
- $L(A, B)$: **Levenshtein String Ratio** over canonical lowercase titles.
- $C(A, B)$: **N-Gram Character Overlap** ($n=3$) to capture inflectional variations.
- Weights: $w_1 = 0.50$, $w_2 = 0.25$, $w_3 = 0.25$.

```
Normalized Title A: "Implement JWT Token Authentication" -> Tokens: {auth, jwt, token}
Normalized Title B: "Add JWT Authentication Support"    -> Tokens: {add, auth, jwt, support}
Token Jaccard J(A, B) = 2 / 5 = 0.40
Levenshtein L(A, B)   = 0.68
Tri-Gram C(A, B)      = 0.62
Composite Score       = 0.50(0.40) + 0.25(0.68) + 0.25(0.62) = 0.525 (Clear)
```

---

## 13. PARENT-CONTEXT SEMANTICS

A critical architectural innovation of R7 is **Parent-Context Scoped Disambiguation**.

### 13.1 The Same-Title Problem
In software engineering backlogs, child tasks frequently share identical titles across different features. For example:
- `FEATURE-001` (User Authentication) $\longrightarrow$ `TASK-0001` (*"Setup Unit Tests"*)
- `FEATURE-002` (Payment Processing)  $\longrightarrow$ `TASK-0005` (*"Setup Unit Tests"*)

A naive global semantic matcher would falsely flag `TASK-0005` as a duplicate of `TASK-0001`.

### 13.2 Scope Invariant Matrix
R7 defines semantic uniqueness scopes strictly by hierarchy tier:

```
+---------------+---------------------+-------------------------------------------------------+
| WorkItem Kind | Uniqueness Scope    | Semantic Comparison Boundary                          |
+---------------+---------------------+-------------------------------------------------------+
| EPIC          | Global Project      | Compared against ALL Epics in work/<project_id>/      |
+---------------+---------------------+-------------------------------------------------------+
| FEATURE       | Parent EPIC         | Compared ONLY against Features under the SAME Epic    |
+---------------+---------------------+-------------------------------------------------------+
| STORY         | Parent FEATURE      | Compared ONLY against Stories under the SAME Feature  |
+---------------+---------------------+-------------------------------------------------------+
| TASK          | Parent STORY        | Compared ONLY against Tasks under the SAME Story      |
+---------------+---------------------+-------------------------------------------------------+
```

Under this model, `TASK-0005` (*"Setup Unit Tests"*) under Story B is **NEVER** compared against `TASK-0001` under Story A. False-positive rate drops to zero for standard operational tasks.

---

## 14. AMBIGUITY HANDLING & FAIL-CLOSED REVIEW GATE

When comparing items within the same scope, the composite similarity score $S$ triggers deterministic gate thresholds:

```
  0.00                      0.70                      0.85                      1.00
   |--------------------------|-------------------------|--------------------------|
   |          CLEAR           |     AMBIGUOUS ZONE      |    CERTAIN DUPLICATE     |
   |         (Allowed)        |    (REVIEW_REQUIRED)    |       (Hard Block)       |
   |   Automated Pass to G1   |    Fail-Closed Block    |   Immediate Rejection    |
   +--------------------------+-------------------------+--------------------------+
```

### 14.1 Threshold Decision Matrix
1. **$S < 0.70$ (CLEAR):** No duplication detected. Item proceeds through automated validation.
2. **$0.70 \le S < 0.85$ (AMBIGUOUS):** The system flags `AMBIGUITY_DETECTED`. 
   - **Action:** Transition to `VALIDATED` is **BLOCKED**.
   - A `backlog_qbc_decisions` entry is recorded with `status = 'REVIEW_REQUIRED'`.
   - Requires explicit human or governance architect resolution: either confirm distinctness with documented rationale or merge/link to existing item.
3. **$S \ge 0.85$ (DUPLICATE):** Hard failure. Validation aborts immediately with `DuplicateItemError`.

---

## 15. PLAN VALIDATION ENGINE

The `PlanValidationEngine` executes a deterministic 7-step pipeline before any plan can reach `VALIDATED`:

```
+-----------------------------------------------------------------------------------+
|                           PLAN VALIDATION PIPELINE                                |
+-----------------------------------------------------------------------------------+
| 1. Structural Checks: plan_id, project_id, created_by non-empty                   |
| 2. Item Integrity: title >= 5 chars, description >= 10 chars, no TBD/TODO tokens  |
| 3. Sizing Checks: Story points in {1, 2, 3, 5, 8}; SP <= 8; Epics/Tasks SP=None   |
| 4. Hierarchy Checks: Parentage conforms to WorkHierarchy.ALLOWED_PARENTS          |
| 5. DAG Acyclicity: Verify no circular parent-child references exist in plan       |
| 6. QBC Tripartite Verification: SQLite, Filesystem, Azure WIQL, Semantic Matcher  |
| 7. Ambiguity Gate: Ensure zero unreviewed AMBIGUITY_DETECTED findings             |
+-----------------------------------------------------------------------------------+
```

If all 7 checks pass, the plan transitions to `VALIDATED` and emits `agent_squad.backlog.validated`.

---

## 16. FORMAL APPROVAL BOUNDARY & SEGREGATION OF DUTIES

### 16.1 Segregation of Duties (SoD) Invariants
Technical validity does not equal business or governance approval:

- **Validation (`VALIDATED`):** Fully automated technical verification.
- **Approval (`APPROVED`):** Requires an explicit human or authorized governance persona (`00-delivery-orchestrator`, `01-product-manager`, `14-governance-auditor`, or `arthemis@`).

```
+------------------------------------------------------------------------------------+
|                               SEGREGATION OF DUTIES                                |
+------------------------------------------------------------------------------------+
|  Role: Author (e.g., 04-architect, Developer, Decomposition Script)                |
|  - Creates Draft Plan                                                              |
|  - Executes Automated Validation                                                   |
|                                                                                    |
|  [ SO-D BARRIER ] ---------------------------------------------------------------- |
|  - Author CANNOT approve own plan if Risk >= MEDIUM or items > 3 Stories           |
|                                                                                    |
|  Role: Approver (00-delivery-orchestrator, 01-product-manager, Governance Auditor) |
|  - Reviews Scope, Business Alignment, Budget & Capacity                           |
|  - Issues Formal Approval: records approved_by and freezes plan hash               |
+------------------------------------------------------------------------------------+
```

---

## 17. HIERARCHICAL MATERIALIZATION SAGA

Materialization is executed as an **atomic, top-down saga** governed by `BacklogMaterializerSaga`.

```
               +-----------------------------------------------------+
               |              START MATERIALIZATION SAGA             |
               +-----------------------------------------------------+
                                          |
                                          v
               +-----------------------------------------------------+
               | Step 1: Assert Plan Status == APPROVED              |
               | (assert_can_materialize())                          |
               +-----------------------------------------------------+
                                          |
                                          v
               +-----------------------------------------------------+
               | Step 2: Acquire Inter-Process SQLite & Disk Locks   |
               | (.locks/<project_id>/materialize.lock)              |
               +-----------------------------------------------------+
                                          |
                                          v
               +-----------------------------------------------------+
               | Step 3: Top-Down Topological Sort                   |
               | Order: EPICs -> FEATUREs -> STORYs -> TASKs         |
               +-----------------------------------------------------+
                                          |
                                          v
               +-----------------------------------------------------+
               | Step 4: Physical R3 Scaffolding                     |
               | Create dirs, status.yaml, and markdown templates    |
               +-----------------------------------------------------+
                                          |
                                          v
               +-----------------------------------------------------+
               | Step 5: SQLite Lifecycle Registration               |
               | Insert into work_item_lifecycle_state (G1 / DRAFT)  |
               +-----------------------------------------------------+
                                          |
                                          v
               +-----------------------------------------------------+
               | Step 6: R6 Transactional Outbox Enqueue             |
               | Insert OUTBOUND_CREATE messages in delivery outbox  |
               +-----------------------------------------------------+
                                          |
                                          v
               +-----------------------------------------------------+
               | Step 7: Finalize Plan Status == MATERIALIZED        |
               | Commit SQLite transaction & Release Locks           |
               +-----------------------------------------------------+
```

---

## 18. R3 LOCAL RUNTIME INTEGRATION

The materialization saga integrates directly with the components established in Milestone R3:

1. **`WorkItemPathResolver`:** Resolves canonical nested paths under plural containers:
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
2. **`PathContainmentGuard`:** Strictly asserts that every generated path resides inside `%SQUAD_RUNTIME%\work\<project_id>\`. Any traversal attempt (`../`) raises `PathContainmentViolationError`.
3. **`ArtifactMaterializer`:** Instantiates level-specific Markdown templates without cross-tier pollution (e.g., Tasks never receive Epic-level documents).

---

## 19. R6 SYNC PLANE & TRANSACTIONAL OUTBOX INTEGRATION

### 19.1 Eradication of the Direct HTTP Bypass
The legacy synchronous call in `agent_squad.py:809` (`connector.create_work_item()`) is permanently deprecated and removed from the creation path.

### 19.2 Transactional Outbox Enqueue Pattern
During Step 6 of the materialization saga, the engine enqueues creation events directly into `delivery_sync_outbox` within the **same SQLite transaction** that commits the local lifecycle states:

```python
# Within SQLite transaction:
outbox_repo.enqueue(
    event_type="OUTBOUND_CREATE",
    work_item_id=item.canonical_id,
    project_id=plan.project_id,
    payload={
        "kind": item.kind.value,
        "title": item.title,
        "description": item.description,
        "parent_id": item.parent_id,
        "story_points": item.story_points,
        "hierarchy_path": str(canonical_path),
    },
    idempotency_key=f"CREATE:{plan.project_id}:{item.canonical_id}",
)
```

The background `DeliverySyncWorker` (R6) reads from `delivery_sync_outbox`, resolves parent `ado_id`s in topological order, issues the Azure DevOps REST call with `System.LinkTypes.Hierarchy-Reverse`, and updates `delivery_work_item_bindings`.

---

## 20. PARTIAL FAILURE HANDLING & CRASH RECOVERY

To guarantee zero orphaned directories and zero lost work during power outages, process terminations, or network failures, the materializer implements a **Compensating Journal / Re-entrant Saga**:

```
+-----------------------------------------------------------------------------------+
|                           CRASH RECOVERY STRATEGY                                 |
+-----------------------------------------------------------------------------------+
| 1. Journaling: Every disk mutation is logged in memory & SQLite staging table.    |
| 2. Failure Prior to SQLite Commit:                                                |
|    - Uncommitted disk directories are cleaned up via Compensation Handler.        |
|    - Plan status remains APPROVED (eligible for re-execution).                     |
| 3. Failure After SQLite Commit:                                                   |
|    - Plan is marked MATERIALIZED.                                                 |
|    - R6 Outbox ensures remote sync will eventually succeed asynchronously.       |
| 4. Network Failure:                                                               |
|    - Zero impact on local materialization.                                        |
|    - Messages remain safely buffered in SQLite WAL delivery_sync_outbox.          |
+-----------------------------------------------------------------------------------+
```

---

## 21. IDEMPOTENCY & RE-EXECUTION GUARANTEES

1. **Re-executing an `APPROVED` Plan:** If materialization crashes midway, invoking `materialize_plan(plan_id)` checks each item:
   - If directory and `status.yaml` already exist $\implies$ skips creation without error.
   - If SQLite lifecycle row already exists $\implies$ skips insert.
   - If outbox message already queued $\implies$ skips enqueue via unique `idempotency_key`.
2. **Re-executing a `MATERIALIZED` Plan:** Calling `materialize_plan()` on an already materialized plan is an instantaneous, verified **NOOP** returning the existing receipt.

---

## 22. PERSISTENCE ARCHITECTURE & SQLITE WAL SCHEMA

All backlog planning data persists inside `%SQUAD_RUNTIME%\banco\squad.db` using WAL mode (`PRAGMA journal_mode=WAL`).

### 22.1 DDL Schema

```sql
-- 1. Backlog Plans Table
CREATE TABLE IF NOT EXISTS backlog_plans (
    plan_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('DRAFT', 'VALIDATED', 'APPROVED', 'MATERIALIZED', 'REJECTED')),
    created_by TEXT NOT NULL,
    approved_by TEXT,
    content_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backlog_plans_proj_status ON backlog_plans(project_id, status);

-- 2. Backlog Plan Items Table
CREATE TABLE IF NOT EXISTS backlog_plan_items (
    item_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    proposed_id TEXT NOT NULL,
    canonical_id TEXT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    story_points INTEGER,
    parent_id TEXT,
    sequence_order INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES backlog_plans(plan_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_plan_items_plan_seq ON backlog_plan_items(plan_id, sequence_order);
CREATE INDEX IF NOT EXISTS idx_plan_items_proposed ON backlog_plan_items(proposed_id);

-- 3. QBC Decisions Audit Table
CREATE TABLE IF NOT EXISTS backlog_qbc_decisions (
    decision_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    proposed_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    matched_id TEXT,
    matched_source TEXT CHECK(matched_source IN ('SQLITE', 'FILESYSTEM', 'AZURE_BOARDS', 'IN_FLIGHT_PLAN', 'NONE')),
    similarity_score REAL NOT NULL DEFAULT 0.0,
    decision_status TEXT NOT NULL CHECK(decision_status IN ('PASSED', 'AMBIGUITY_DETECTED', 'DUPLICATE_REJECTED', 'OVERRIDDEN')),
    resolution_notes TEXT,
    resolved_by TEXT,
    evaluated_at TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES backlog_plans(plan_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_qbc_decisions_plan ON backlog_qbc_decisions(plan_id);
```

---

## 23. DOMAIN EVENTS & R2 TRIGGER BUS INTEGRATION

The planning sub-system publishes canonical domain events conforming to `scripts/domain/events.py`:

```
+-------------------------------------------+-------------------------------------------------------+
| Event Type                                | Trigger Condition                                     |
+-------------------------------------------+-------------------------------------------------------+
| agent_squad.backlog.drafted               | Plan created in DRAFT status                          |
| agent_squad.backlog.validated             | Plan passes all 7 validation steps                    |
| agent_squad.backlog.ambiguity_detected    | QBC flags item with 0.70 <= score < 0.85              |
| agent_squad.backlog.approved              | Authorized persona approves the plan                  |
| agent_squad.backlog.materialize_started   | Materialization saga begins execution                 |
| agent_squad.backlog.materialized          | All items created on disk, in SQLite, and in outbox  |
| agent_squad.backlog.materialize_failed    | Materialization saga encounters unrecoverable error   |
| agent_squad.backlog.rejected              | Plan explicitly rejected during review                |
+-------------------------------------------+-------------------------------------------------------+
```

Each event computes a deterministic `idempotency_key` via SHA-256 and is recorded in `events` / `event_deliveries` tables for consumption by triggers and watchdog policies.

---

## 24. CONCURRENCY CONTROL & MULTI-AGENT ALLOCATION LOCKS

When multiple subagents or CLI processes plan backlogs simultaneously, race conditions during ID allocation and materialization are prevented via **two-tier synchronization**:

1. **SQLite Immediate Transactions:** Sequential ID allocation queries and reservations use `BEGIN IMMEDIATE` transactions on `squad.db`. This serializes ID generation at the database engine level with a 5000ms timeout.
2. **Inter-Process Lock Files:** File operations acquire lock files using cross-platform file locking (`msvcrt` on Windows / `fcntl` on POSIX):
   ```
   %SQUAD_RUNTIME%\.locks\<project_id>\id_allocation.lock
   %SQUAD_RUNTIME%\.locks\<project_id>\materialize.lock
   ```

---

## 25. AGENT-ROUTING ARCHITECTURAL BOUNDARY

To ensure modularity and strict boundary defense, Milestone R7 establishes a rigid border with Milestone R8:

```
+-----------------------------------------------------------------------------------+
|               MILESTONE R7 RESPONSIBILITY (ENDS AT MATERIALIZATION)               |
+-----------------------------------------------------------------------------------+
| - Decompose user requirements into a structured BacklogPlan.                      |
| - Validate parentage, Fibonacci sizing, and content contracts.                   |
| - Execute multi-source QBC and semantic deduplication.                            |
| - Allocate canonical collision-free IDs.                                          |
| - Materialize physical scaffolding on disk and enqueue in R6 Outbox.              |
| - Initialize work item lifecycle state to (Stage: G1_PROD_INTAKE, Status: DRAFT).|
+-----------------------------------------------------------------------------------+
                                          |
                                [ HARD BOUNDARY ]
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|               MILESTONE R8 RESPONSIBILITY (BEGINS AT AGENT DISPATCH)              |
+-----------------------------------------------------------------------------------+
| - Inspect materialized work items awaiting intake.                                |
| - Select specialist personas (01-pm, 04-architect, 06-backend, etc.).             |
| - Compile rendered prompts via squad render-prompt.                               |
| - Invoke subagents and govern multi-agent swarm WIP limits.                       |
| - Advance lifecycle states across G1 through G6.                                  |
+-----------------------------------------------------------------------------------+
```

R7 code **NEVER** invokes subagents, selects personas, or advances lifecycle states beyond intake initialization.

---

## 26. REMAINING PHASES & ROADMAP BOUNDARIES (R8+)

The completion of Milestone R7 unlocks the subsequent milestones of the Agent Squad architecture:

- **Milestone R8 (Specialist Routing & Swarm Orchestration):** Dynamic routing, prompt compilation, agent lifecycle supervision, and automated handoffs.
- **Milestone R9 (Continuous Delivery & Enterprise Governance):** Full-loop automated verification, CI/CD pipeline gating, red-team security audits, and cross-project executive metrics.

---

## FORMAL ARCHITECTURAL VERDICT

```
========================================================================================
                      AGENT SQUAD ARCHITECTURAL REVIEW BOARD
                             MILESTONE R7 — STAGE B
========================================================================================

Specification Document: docs/architecture/R7_BACKLOG_PLAN_QBC_MATERIALIZATION.md
Status: EXHAUSTIVE & AUTHORITATIVE

All 26 required architectural sections of Section 58 have been fully articulated:
[X] 1. Scope                                [X] 14. Ambiguity / Review-Required
[X] 2. Non-Goals                            [X] 15. Plan Validation Engine
[X] 3. Current Defects (Stage A)            [X] 16. Formal Approval & SoD
[X] 4. BacklogPlan Authority                [X] 17. Hierarchical Materialization Saga
[X] 5. Plan Lifecycle                       [X] 18. R3 Local Runtime Integration
[X] 6. Hierarchy & Multi-Epic               [X] 19. R6 Outbox Integration (No Bypass)
[X] 7. Content Contracts                    [X] 20. Partial Failure & Crash Recovery
[X] 8. Canonical ID Allocation              [X] 21. Idempotency Guarantees
[X] 9. QBC Architecture                     [X] 22. Persistence & SQLite Schema
[X] 10. QBC Source Model                    [X] 23. Domain Events & R2 Integration
[X] 11. Exact Matching                      [X] 24. Concurrency & Allocation Locks
[X] 12. Semantic Matching                   [X] 25. Agent-Routing Boundary (R8)
[X] 13. Parent-Context Semantics            [X] 26. Remaining Phases (R8+)

FINAL ARCHITECTURAL VERDICT:
>>> R7_BACKLOG_DESIGN = APPROVED <<<
========================================================================================
```
