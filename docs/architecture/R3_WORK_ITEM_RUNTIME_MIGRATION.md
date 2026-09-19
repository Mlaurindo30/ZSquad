# R3 — WORK ITEM MODEL RUNTIME MIGRATION ARCHITECTURAL SPECIFICATION
## Enterprise Architecture Specification · Canonical 4-Tier Hierarchy · Physical Storage Containment · Zero Destructive Migration

**Document ID:** `DOC-ARCH-R3-WORK-ITEM-MIGRATION`  
**Milestone:** `R3 — WORK ITEM MODEL RUNTIME MIGRATION`  
**Stage:** `STAGE B — MIGRATION DESIGN`  
**Date:** 2026-09-18  
**Author / Lead:** `04-solution-architect` (Martin Fowler & Gregor Hohpe - Solution Architect & Enterprise Integration Lead)  
**Collaborators & Reviewers:**  
- `01-requirements-analyst` (Product Quality, Acceptance Criteria & Backlog Invariants)  
- `40-agile-coach` (Flow Governance, Sizing Limits & Slicing Discipline)  
**Status:** `APPROVED` (`R3_MIGRATION_DESIGN = APPROVED`)

---

## 1. SCOPE

### 1.1 Purpose & Mandate
Milestone R3 formalizes the runtime architecture, physical filesystem hierarchy, canonical identification, backward compatibility, and ancestor context resolution for Work Items across the Agent Squad platform.

In Milestone R1 (`DOC-ARCH-R1-CANONICAL-CONTRACTS`), pure domain entities and invariants were established (`WorkItem`, `WorkItemId`, `WorkHierarchy`, `BacklogPlanItem`, `AcceptanceCriterion`) strictly using Python standard library primitives. In Milestone R2 (`DOC-ARCH-R2-EVENT-TRIGGER-ENGINE`), the transactional outbox and event persistence plane were implemented.

The scope of Milestone R3 is to operationalize these contracts within the runtime file system, CLI, and agent context resolution pipelines without breaking existing repositories, without altering downstream Azure DevOps state, and without introducing destructive migrations.

### 1.2 Core Responsibilities of R3
1. **Canonical Identification Policy:** Enforce standard grammar for `EPIC`, `FEATURE`, `STORY`, `TASK`, and operational types.
2. **Canonical 4-Tier Hierarchy:** Govern strict parentage (`EPIC -> FEATURE -> STORY -> TASK`).
3. **Physical Storage Mirror:** Establish structured containment paths under `work/<project_id>/` reflecting hierarchy.
4. **Ancestor Context Resolution:** Enable any subagent or CLI command operating on a child item to recursively ascend the tree to extract high-level architectural goals and specs.
5. **Level-Specific Artifact Materialization:** Eliminate architectural leakage where low-level tasks materialize epic documents.
6. **Zero Destructive Migration & Transparent Fallback:** Guarantee that existing flat work items in `work/<project_id>/<ID>/` remain 100% accessible and operable.

### 1.3 Strict Out-of-Scope Boundaries
- **Zero Python Runtime Code Alterations in Stage B:** This document establishes the authoritative architecture and design. Implementation code in `scripts/runtime/work_items/` is reserved for Stage C.
- **Zero Template Mutations in Stage B:** Template adjustments are defined here but executed in Stage C/D.
- **Zero Azure DevOps Remote Mutations:** Synchronization with Azure DevOps boards is deferred to Milestone R8.
- **Zero Event Engine Wiring:** Binding event outbox triggers to work item lifecycle mutations belongs to Milestone R5/R6.
- **Zero Cycle Alterations:** Cycle definitions in `config/cycles.yaml` remain unchanged.

---

## 2. R0 DEFECTS OWNED BY R3

The R0 Core Workflow Failure Baseline (`docs/audits/R0_CORE_WORKFLOW_FAILURE_BASELINE.md`) identified critical defects in work item handling. Milestone R3 directly owns and resolves:

| Defect ID | R0 Identified Failure | Root Cause in Legacy Runtime | Architectural Resolution in R3 |
|---|---|---|---|
| **R0-WORK-001** | `_legacy_type_for_id` rejects canonical `FEATURE-*` and `STORY-*` IDs | `scripts/agent_squad.py` mapped only `FEAT` and `US`, raising `SquadError: ID inválido` or `KeyError` | `CanonicalIdPolicy` accepts `FEATURE` and `STORY` natively while retaining read-only normalization for legacy aliases (`FEAT-`, `US-`, `TK-`). |
| **R0-WORK-002** | Hierarchical work items flattened directly under `work/<proj>/` | `AgentSquad.init_work_item()` created all folders as flat siblings directly inside `parent` directory regardless of `parent_id` | Canonical physical nesting: `work/<proj>/EPIC-.../features/FEATURE-.../stories/STORY-.../tasks/TASK-...` with atomic parent directory resolution. |
| **R0-WORK-003** | Task materialization injects Epic artifacts (`epic.md`, `product-goal.md`) | `_init_work_item_unlocked()` copied a static set of initial files (`epic.md`, `product-goal.md`, `discovery/brief.md`) to every item | Level-specific artifact templates governed by work item kind. A `TASK` never materializes `epic.md` or `product-goal.md`. |
| **R0-WORK-004** | Competing backlog hierarchy definitions across modules | Ad-hoc hierarchy dicts (`{'epic': 1, 'feature': 2, 'story': 3, 'task': 4}`) without formal parent-child validation | Strict domain enforcement via `WorkHierarchy.validate_parent_child()` from R1 contract. |

---

## 3. R1 CONTRACT MAPPING

Milestone R3 is the runtime materialization of the canonical domain contracts formalized in `scripts/domain/work_items.py` and `scripts/domain/backlog.py`. The runtime components consume these contracts without modification or redefinition:

```
+-------------------------------------------------------------------------+
|                  R1 CANONICAL DOMAIN CONTRACTS                          |
|  - WorkItemKind: EPIC, FEATURE, STORY, TASK, BUG, SPIKE, INCIDENT...    |
|  - WorkItemId: normalize(), validate(), infer_kind()                   |
|  - WorkHierarchy: ALLOWED_PARENTS, validate_parent_child()             |
|  - WorkItem: immutable domain entity, sizing rules (<= 8 SP)           |
|  - BacklogPlanItem.get_required_artifacts_for_kind()                   |
+-------------------------------------------------------------------------+
                                    |
                                    v consumed by
+-------------------------------------------------------------------------+
|                 R3 RUNTIME WORK ITEM ARCHITECTURE                       |
|  +---------------------------+       +-------------------------------+  |
|  | scripts.runtime.work_items|       | scripts.runtime.work_items    |  |
|  |           ids.py          |       |           paths.py            |  |
|  | (CanonicalIdService)      |       | (WorkItemPathResolver)        |  |
|  +---------------------------+       +-------------------------------+  |
|               |                                       |                 |
|               +-------------------+-------------------+                 |
|                                   v                                     |
|  +---------------------------+       +-------------------------------+  |
|  | scripts.runtime.work_items|       | scripts.runtime.work_items    |  |
|  |        hierarchy.py       |       |          templates.py         |  |
|  | (HierarchyContextResolver)|       | (ArtifactMaterializer)        |  |
|  +---------------------------+       +-------------------------------+  |
+-------------------------------------------------------------------------+
```

### Direct Contract Invariant Bindings:
- **`WorkItemKind`:** All runtime validations and directory branches correspond 1:1 with `scripts.domain.work_items.WorkItemKind`.
- **`WorkItemId.normalize()`:** Automatically applied to any incoming user or CLI identifier before path calculation or filesystem lookup.
- **`WorkHierarchy.validate_parent_child()`:** Executed prior to creating any work item that specifies a `parent_id`.
- **`FIBONACCI_SIZING_ALLOWED` (`{1, 2, 3, 5, 8}`):** Strictly enforced for all `STORY` items. Stories > 8 SP are blocked from creation or execution.

---

## 4. LEGACY RUNTIME MAP

An architectural audit of the legacy runtime revealed the following structural dependencies that R3 must modernize while preserving backward compatibility:

### 4.1 Legacy Components & Deficiencies
1. **`scripts/agent_squad.py::_legacy_type_for_id()`:**
   - Contained hardcoded dictionary mapping: `{'EPIC': 'epic', 'FEAT': 'feature', 'US': 'story', 'TASK': 'task', ...}`.
   - Raised `SquadError: ID inválido` when presented with canonical `FEATURE-001` or `STORY-001`.
2. **`scripts/agent_squad.py::init_work_item()`:**
   - Forced flat directory creation: `item = (parent / work_id).resolve()`.
   - Verified parent existence by checking `(parent / parent_id / 'status.yaml').exists()`, which assumes siblings reside in the same flat folder.
   - Performed naive QBC check using prefix string splitting (`split('-')[0]`), causing false duplicate errors across distinct epics.
3. **`scripts/agent_squad.py::_init_work_item_unlocked()`:**
   - Inconditionally created `epic.md` and `product-goal.md` regardless of whether the item was a top-level Epic or a leaf Task.
4. **`scripts/agent_squad.py::_item()`:**
   - Assumed flat item path: `candidate = (self._work_base() / value_path).resolve()`.
   - Incapable of discovering nested items without knowing their full relative ancestor path.
5. **`scripts/render_agent_prompt.py::_build_work_item_context()`:**
   - Looked up work item status only at `squad._work_base() / packet['work_item'] / 'status.yaml'`.
   - Injected zero parent, feature, or epic context into subagent instructions.

---

## 5. CANONICAL ID MODEL

The canonical ID model defines unambiguous naming conventions for all demands entering the system.

### 5.1 Grammar & Formatting Rules
All canonical IDs are uppercase, alphanumeric strings composed of an entity prefix, a hyphen separator, and a zero-padded integer sequence.

| Entity Kind | Canonical Prefix | Minimum Numeric Digits | Canonical Regex Pattern | Example Canonical ID |
|---|---|---|---|---|
| **Epic** | `EPIC` | 3 | `^EPIC-\d{3,}$` | `EPIC-001`, `EPIC-042` |
| **Feature** | `FEATURE` | 3 | `^FEATURE-\d{3,}$` | `FEATURE-001`, `FEATURE-105` |
| **Story** | `STORY` | 3 | `^STORY-\d{3,}$` | `STORY-001`, `STORY-089` |
| **Task** | `TASK` | 4 | `^TASK-\d{4,}$` | `TASK-0001`, `TASK-0120` |
| **Bug** | `BUG` | 3 | `^BUG-\d{3,}$` | `BUG-001` |
| **Spike** | `SPIKE` | 3 | `^SPIKE-\d{3,}$` | `SPIKE-001` |
| **Incident** | `INCIDENT` | 3 | `^INCIDENT-\d{3,}$` | `INCIDENT-001` |
| **Release** | `RELEASE` | 3 | `^RELEASE-\d{3,}$` | `RELEASE-001` |
| **Setup** | `SETUP` | 3 | `^SETUP-\d{3,}$` | `SETUP-001` |

### 5.2 Numeric Generation & Progression
When creating new items via CLI or automated decomposition:
1. Identifiers are allocated sequentially per scope (e.g., tasks within a story start at `TASK-0001`, `TASK-0002`).
2. Digits must not be stripped or compressed (e.g., `TASK-1` is illegal; it must be zero-padded to `TASK-0001`).

---

## 6. LEGACY ALIASES & COMPATIBILITY POLICY

To ensure seamless operation during the migration period, legacy identifier aliases are supported exclusively in **Read-Only Compatibility Mode**.

### 6.1 Supported Aliases
- `FEAT-*` -> Normalizes to `FEATURE-*`
- `US-*` -> Normalizes to `STORY-*`
- `TK-*` -> Normalizes to `TASK-*`
- `REL-*` -> Normalizes to `RELEASE-*`

### 6.2 Normalization Invariants
1. **Read/Lookup Normalization:** Any lookup, CLI argument, or API call providing `FEAT-001` will internally match both `FEAT-001` (if on disk as legacy) and `FEATURE-001` (if migrated or newly created).
2. **Never Emit for New Items:** The runtime must **NEVER** generate a new work item with legacy prefixes (`FEAT-`, `US-`, `TK-`). All newly initialized items must strictly receive canonical prefixes (`FEATURE-`, `STORY-`, `TASK-`).
3. **Idempotent Normalization:** Calling `WorkItemId.normalize('FEATURE-001')` returns `'FEATURE-001'` without modification.

---

## 7. CANONICAL HIERARCHY

Agent Squad enforces a strict 4-tier demand breakdown hierarchy derived from Agile and Portfolio Kanban principles.

```
       [ EPIC ]                  (Portfolio / Strategic Theme)
          |
          v (1 to N)
      [ FEATURE ]                (Architectural / Functional Capability)
          |
          v (1 to N)
       [ STORY ]                 (User Story / Deliverable Value Unit <= 8 SP)
          |
          v (1 to N)
       [ TASK ]                  (Technical Execution Unit / Work Breakdown)
```

### 7.1 Hierarchy Rules & Parent Invariants
1. **Epic Parentage:** An `EPIC` is the root container of strategic value. An Epic **MUST NOT** have a parent (`parent_id == None`).
2. **Feature Parentage:** A `FEATURE` represents a cohesive capability. Its parent **MUST** be an `EPIC`.
3. **Story Parentage:** A `STORY` represents a vertically sliced unit of value <= 8 SP. Its parent **MUST** be a `FEATURE`.
4. **Task Parentage:** A `TASK` represents a concrete implementation step. Its parent **MUST** be a `STORY`.
5. **Operational Work Types:**
   - `BUG`: Parent may be a `STORY` (in-sprint defect) or a `FEATURE` (escaped defect).
   - `SPIKE`: Parent may be an `EPIC` or a `FEATURE`.
   - `INCIDENT`: Standalone (`None`) or linked to a `FEATURE` / `RELEASE`.
   - `RELEASE`: Standalone (`None`).

---

## 8. LOCAL PATH MODEL

To reflect the canonical hierarchy physically on disk and prevent flat directory pollution under `work/<project_id>/`, R3 establishes a deterministic nested path model.

### 8.1 Physical Directory Structure
All work items for a given project are rooted inside `%SQUAD_RUNTIME%/work/<project_id>/`.

```text
work/<project_id>/
└── EPIC-001/
    ├── status.yaml
    ├── epic.md
    ├── product-goal.md
    ├── architecture-vision.md
    ├── documentation/
    │   └── delivery-ledger.md
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
                        ├── TASK-0001/
                        │   ├── status.yaml
                        │   └── task-scope.md
                        └── TASK-0002/
                            ├── status.yaml
                            └── task-scope.md
```

### 8.2 Subdirectory Plural Containers
Between hierarchical levels, explicit plural routing directories are used:
- Features under an Epic reside in `features/`
- Stories under a Feature reside in `stories/`
- Tasks under a Story reside in `tasks/`

This prevents namespace collisions between child work item IDs and artifact subdirectories (such as `documentation/`, `evidence/`, `gate-decisions/`).

---

## 9. PARENT RESOLVER

The runtime must deterministically resolve the parent of any work item, whether located via nested physical paths or registered logically in `status.yaml`.

### 9.1 Resolver Algorithm (`get_parent`)
1. **Inspection of `status.yaml`:**
   - Read `status.yaml` of the target work item.
   - Extract `parent_id`.
   - If `parent_id` is `None` or absent: return `None`.
2. **Physical Hierarchy Short-Circuit:**
   - If the item is in a nested directory structure (e.g., `.../stories/STORY-001/tasks/TASK-0001`), the parent directory is immediately accessible at `../../` (skipping the plural folder `tasks/`).
   - Validate that `../../status.yaml` exists and matches `parent_id`.
3. **Global Project Search Fallback (Compatibility Mode):**
   - If the physical parent is not at `../../` (legacy flat structure or hybrid relocation), perform an indexed recursive search across `work/<project_id>/**/status.yaml` matching `id: <parent_id>`.
4. **Validation:**
   - Enforce `WorkHierarchy.validate_parent_child(parent.kind, child.kind)`.

---

## 10. ANCESTOR RESOLVER & CONTEXT PROPAGATION

A primary failure in multi-agent orchestration (R0-DEL-007) is context isolation: when a subagent (such as `06-software-engineer`) is dispatched to implement `TASK-0001`, it often lacks the architectural context of `FEATURE-001` or the business goals of `EPIC-001`.

### 10.1 Ancestor Chain Resolution (`get_ancestors`)
The Ancestor Resolver computes the complete lineage from the current item up to the root Epic:

Lineage(Item) = [Item, Parent, Grandparent, ..., RootEpic]

For `TASK-0001`:
```python
ancestors = resolver.get_ancestors("TASK-0001")
# Returns: [WorkItem(STORY-001), WorkItem(FEATURE-001), WorkItem(EPIC-001)]
```

### 10.2 Context Aggregation for Prompt Compilation
When compiling the subagent prompt via `scripts/render_agent_prompt.py`, the ancestor chain is injected as structured context:

```markdown
# CONTEXTO HIERÁRQUICO DO WORK ITEM
Você está atuando no item: TASK-0001 (Technical Implementation)

## LINHAGEM E ESPECIFICAÇÕES ANCESTRAIS
- 🔷 STORY: STORY-001 - Autenticação JWT com Refresh Token (3 SP)
  * Acceptance Criteria: AC-01 (Token expira em 15m), AC-02 (Refresh rotativo)
  * Path: work/auth-proj/EPIC-001/features/FEATURE-001/stories/STORY-001
- 🟣 FEATURE: FEATURE-001 - Módulo de Identidade e Acesso
  * Component Design: microservice-auth-v2
  * Path: work/auth-proj/EPIC-001/features/FEATURE-001
- 🔶 EPIC: EPIC-001 - Modernização da Plataforma de Clientes
  * Business Goal: Zero-trust architecture migration
  * Path: work/auth-proj/EPIC-001
```

This guarantees full cognitive alignment across all subagent delegations without manual human briefing.

---

## 11. TEMPLATES & ARTIFACT MATERIALIZATION

Resolving defect **R0-WORK-003** requires that artifact materialization is strictly level-specific.

### 11.1 Level-to-Template Matrix
The materializer inspects `WorkItemKind` and instantiates only authorized templates:

| Work Item Kind | Mandatory Materialized Artifacts | Prohibited Artifacts (Strictly Blocked) |
|---|---|---|
| **`EPIC`** | `status.yaml`<br>`epic.md`<br>`product-goal.md`<br>`architecture-vision.md`<br>`documentation/delivery-ledger.md` | `task-scope.md`<br>`acceptance-criteria.md` |
| **`FEATURE`** | `status.yaml`<br>`feature-spec.md`<br>`component-design.md`<br>`documentation/delivery-ledger.md` | `epic.md`<br>`product-goal.md`<br>`task-scope.md` |
| **`STORY`** | `status.yaml`<br>`user-story.md`<br>`acceptance-criteria.md`<br>`documentation/delivery-ledger.md` | `epic.md`<br>`product-goal.md`<br>`architecture-vision.md` |
| **`TASK`** | `status.yaml`<br>`task-scope.md` | **`epic.md`**<br>**`product-goal.md`**<br>`architecture-vision.md` |
| **`BUG`** | `status.yaml`<br>`bug-report.md`<br>`reproduction-steps.md` | `epic.md`<br>`product-goal.md` |
| **`SPIKE`** | `status.yaml`<br>`spike-report.md`<br>`findings.md` | `epic.md`<br>`product-goal.md` |

### 11.2 Invariant Enforcement
Any execution attempt that detects `epic.md` or `product-goal.md` being created inside a `TASK` folder will abort immediately with `ArtifactContainmentViolation`.

---

## 12. COMPATIBILITY READ MODE & FALLBACK LOOKUP

Existing Agent Squad repositories contain legacy flat directories (e.g., `work/my-project/FEAT-001/`, `work/my-project/US-001/`). R3 guarantees 100% non-breaking read and operation over these items.

### 12.1 Two-Tier Resolution Strategy
When resolving a path for work item identifier `X`:
1. **Tier 1 — Canonical Hierarchy Lookup (Fast Path):**
   - Search within indexed path cache or traverse canonical nesting:
     `work/<project_id>/**/<X>/status.yaml`
   - Also search for canonicalized name if `X` is an alias (e.g., `X = US-001` searches for both `US-001` and `STORY-001`).
2. **Tier 2 — Legacy Flat Path Fallback (Compatibility Path):**
   - Check direct legacy path:
     `work/<project_id>/<X>/status.yaml`
   - If found, treat the item as a valid work item operating in compatibility mode.

```
                  Resolve Work Item: 'STORY-001'
                               |
                               v
            +------------------------------------+
            | Tier 1: Nested Hierarchy Search    |
            | work/<proj>/**/STORY-001/          |
            +------------------------------------+
                               |
                   +-----------+-----------+
                   | Found                 | Not Found
                   v                       v
            [ Return Path ]     +------------------------------------+
                                | Tier 2: Legacy Flat Search         |
                                | work/<proj>/STORY-001/ OR US-001/  |
                                +------------------------------------+
                                           |
                               +-----------+-----------+
                               | Found                 | Not Found
                               v                       v
                        [ Return Path ]       [ SquadError: Not Found ]
```

---

## 13. NO DESTRUCTIVE MIGRATION POLICY

### 13.1 Strict Invariant
**Milestone R3 will NEVER execute mass physical relocation, directory renaming, or deletion of existing work item files on disk.**

### 13.2 Rationale & Guarantees
- **Git History Preservation:** Moving folders breaks `git log --follow` tracking across large codebases.
- **Concurrent Execution Safety:** Running agents or external CI jobs holding file handles to `work/<proj>/US-001/` must not experience file descriptor invalidation.
- **Opt-In Progressive Adoption:** Existing items remain where they are. Newly created items automatically adopt the nested canonical structure.

---

## 14. PATH CONTAINMENT & SECURITY INTEGRITY

Agent Squad strictly prohibits work state leakage outside the governed runtime root (`PathContainmentViolation`).

### 14.1 Path Containment Guard Invariants
Every path resolved or constructed by R3 components must be validated against `PathContainmentGuard.validate_work_path()`:
1. Target path must reside strictly under `%SQUAD_RUNTIME%/work/<project_id>/`.
2. Path traversal attempts (`..`, symlinks pointing outside, absolute drive escapes) raise `PathContainmentViolation` immediately.

### 14.2 Identifier & Directory Sanitization
- Work item identifiers and title slugs are strictly checked against `^[A-Za-z0-9_-]+$`.
- Windows-reserved file names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`, `LPT1`) are prohibited.
- Total path lengths are kept well within the Windows 260-character MAX_PATH limit through compact directory names (`features/`, `stories/`, `tasks/`).

---

## 15. OPERATIONAL WORK TYPES

In addition to standard product backlog items, the Agent Squad runtime recognizes five specialized operational demand types:

1. **`BUG` (Defect Remediation):**
   - Cycle: `bugfix`
   - Hierarchy: Child of `STORY` or `FEATURE`.
   - Entry State: `implementation` (preflight G1+G2+G3).
2. **`SPIKE` (Timeboxed Architectural / Technical Investigation):**
   - Cycle: `spike`
   - Hierarchy: Child of `FEATURE` or `EPIC`.
   - Deliverable: Spike report and architectural findings.
3. **`INCIDENT` (Production Triage & Mitigation):**
   - Cycle: `incident`
   - Hierarchy: Top-level or linked to affected `FEATURE`.
   - Entry State: `triage`.
4. **`RELEASE` (Release Orchestration & Deployment):**
   - Cycle: `release`
   - Hierarchy: Top-level container.
   - Entry State: `blueprint`.
5. **`PROJECT_SETUP` (Platform Onboarding):**
   - Cycle: `new-project`
   - Hierarchy: Standalone system initialization.

All operational types are preserved without regression, retaining their specific lifecycle bindings and validation rules.

---

## 16. CYCLE COMPATIBILITY BOUNDARY (`R3_COMPATIBILITY_ONLY`)

### 16.1 Isolation Principle
Milestone R3 is **exclusively a work item model and path migration**. It does **NOT** alter the active lifecycle engine or `config/cycles.yaml`.

### 16.2 Cycle Mapping Guard
The mapping in `scripts/agent_squad.py` and `config/cycles.yaml` remains strictly:
```yaml
type_to_cycle:
  epic: development
  feature: development
  story: user-story
  task: development
  bug: bugfix
  release: release
  evolution: evolution
  study: spike
  spike: spike
  incident: incident
```

R3 implements an internal adapter (`R3_COMPATIBILITY_ONLY`) that maps canonical domain kinds (`WorkItemKind.FEATURE` -> 'feature', `WorkItemKind.STORY` -> 'story') into the exact legacy cycle strings expected by `cycles.yaml`.

---

## 17. QBC (QUERY-BEFORE-CREATE) BOUNDARY

### 17.1 Boundary Definition
Defect **R0-WORK-005** documented false duplicate errors when creating distinct Epics with similar prefixes. 
- **Full Semantic QBC (Vector/Intent Comparison):** Belongs to Milestone **R7 (Backlog, QBC & Materialization)**.
- **R3 Boundary:** R3 fixes the lexical prefix parsing defect in `agent_squad.py` by:
  1. Comparing exact canonical IDs (`cand_id.lower() == work_id.lower()`).
  2. Comparing exact normalized titles rather than naive `split('-')[0]` tokens.
  3. Ensuring QBC traverses both flat and nested hierarchies under `work/<project_id>/`.

---

## 18. AZURE DEVOPS BOUNDARY

### 18.1 Isolation Principle
Milestone R3 executes with **ZERO remote mutational interactions with Azure DevOps**.
- The `devops_platform_connector` calls in `init_work_item()` are guarded and mocked during R3 tests.
- Local work item paths and hierarchies are decoupled from Azure DevOps Team Project creation (resolving R0-ADO-002).
- Formal bidirectional synchronization, work item link writes, and WIQL board reconciliations are deferred to **R8 (Azure DevOps Integration)**.

---

## 19. EVENT ENGINE BOUNDARY

### 19.1 Isolation Principle
Although Milestone R2 (`DOC-ARCH-R2-EVENT-TRIGGER-ENGINE`) completed the `SqliteEventStore` and outbox engine, R3 maintains complete architectural decoupling:
- R3 work item creation and hierarchy resolution **DO NOT** require active trigger evaluation.
- Domain events (e.g., `WORK_ITEM_CREATED`, `WORK_ITEM_NESTED`) will be emitted via standard domain calls, but no background worker daemon is invoked or required for R3 operations.
- Full event-driven autonomous transitions belong to **R5/R6**.

---

## 20. PUBLIC RUNTIME APIS & MODULAR DESIGN

To cleanly replace monolithic methods in `scripts/agent_squad.py`, R3 introduces a dedicated, modular package: `scripts/runtime/work_items/`.

```
scripts/runtime/work_items/
├── __init__.py          # Public API facade
├── ids.py               # CanonicalIdService & LegacyAliasNormalizer
├── paths.py             # WorkItemPathResolver & PhysicalHierarchyManager
├── hierarchy.py         # HierarchyContextResolver & AncestorChainService
└── templates.py         # LevelSpecificArtifactMaterializer
```

### 20.1 `scripts/runtime/work_items/ids.py`
```python
class CanonicalIdService:
    @staticmethod
    def normalize(raw_id: str) -> str:
        '''Normalizes aliases (FEAT-, US-, TK-) to canonical prefixes.'''
        ...

    @staticmethod
    def validate(canonical_id: str) -> bool:
        '''Validates canonical regex and minimum digit length.'''
        ...

    @staticmethod
    def infer_kind(raw_or_canonical_id: str) -> WorkItemKind:
        '''Infers WorkItemKind enum from ID string.'''
        ...
```

### 20.2 `scripts/runtime/work_items/paths.py`
```python
class WorkItemPathResolver:
    def __init__(self, runtime_root: Path, project_id: str):
        ...

    def resolve_item_path(self, work_item_id: str) -> Path:
        '''Resolves absolute path for an item, checking nested then flat fallback.'''
        ...

    def construct_canonical_path(
        self,
        work_item_id: str,
        kind: WorkItemKind,
        parent_id: Optional[str] = None,
    ) -> Path:
        '''Constructs canonical nested path under work/<project_id>/.'''
        ...

    def find_all_work_items(self) -> List[Path]:
        '''Scans project directory returning paths to all valid status.yaml files.'''
        ...
```

### 20.3 `scripts/runtime/work_items/hierarchy.py`
```python
class HierarchyContextResolver:
    def __init__(self, path_resolver: WorkItemPathResolver):
        ...

    def get_parent(self, work_item_id: str) -> Optional[WorkItem]:
        '''Resolves direct parent WorkItem entity.'''
        ...

    def get_ancestor_chain(self, work_item_id: str) -> List[WorkItem]:
        '''Returns ordered list of ancestors up to root Epic.'''
        ...

    def compile_hierarchy_context(self, work_item_id: str) -> Dict[str, Any]:
        '''Builds structured dictionary for injection into subagent briefings.'''
        ...
```

### 20.4 `scripts/runtime/work_items/templates.py`
```python
class ArtifactMaterializer:
    def __init__(self, templates_dir: Path):
        ...

    def materialize_artifacts(
        self,
        target_dir: Path,
        kind: WorkItemKind,
        work_item_id: str,
        metadata: Dict[str, Any],
    ) -> List[str]:
        '''Instantiates authorized level-specific artifacts on disk.'''
        ...
```

---

## 21. REMAINING WORK FOR R4–R14

The delivery of Milestone R3 unlocks subsequent architectural milestones across the Agent Squad roadmap:

| Milestone | Title | Direct Dependency on R3 |
|---|---|---|
| **R4** | `LIFECYCLE & GATE ENGINE` | Consumes canonical work item states and executes transitions on hierarchical items. |
| **R5** | `CONTINUOUS TRIGGER ENGINE` | Uses hierarchy awareness to trigger parent state updates when children complete. |
| **R6** | `SUBAGENT DELIBERATION & SOD` | Injects ancestor chain into rendered subagent prompts via `HierarchyContextResolver`. |
| **R7** | `BACKLOG & SPECIFICATION MATERIALIZATION` | Implements full semantic QBC and backlog decomposition plans creating nested items. |
| **R8** | `AZURE DEVOPS INTEGRATION` | Maps nested local hierarchy (`EPIC -> FEATURE -> STORY -> TASK`) 1:1 to Azure Boards links. |
| **R9** | `WATCHDOG & TIMEBOX SCHEDULER` | Evaluates SLA and timeboxes across hierarchical deliverables. |
| **R10** | `SECURITY & GOVERNANCE AUDIT` | Validates immutable audit trails and SoD compliance across parent-child trees. |
| **R11–R14** | `E2E VALIDATION & RELEASE` | Full system end-to-end verification under canonical hierarchy. |

---

## ARCHITECTURAL VERDICT & FORMAL SIGN-OFF

The architectural migration specification for Work Items defined in this document completely satisfies the requirements of Stage B of Milestone R3. It eliminates defects R0-WORK-001, R0-WORK-002, R0-WORK-003, and R0-WORK-004 while maintaining 100% backward compatibility and zero destructive mutations.

```text
================================================================================
                    R3 MIGRATION DESIGN VERDICT
================================================================================
  Specification Status: COMPLETE & VALIDATED
  Contract Parity: 100% MATCH WITH R1 DOMAIN CONTRACTS
  Defect Resolution: R0-WORK-001, R0-WORK-002, R0-WORK-003, R0-WORK-004
  Backward Compatibility: FULL READ FALLBACK (ZERO DESTRUCTIVE MIGRATION)
  Verdict: APPROVED (R3_MIGRATION_DESIGN = APPROVED)
================================================================================
```
