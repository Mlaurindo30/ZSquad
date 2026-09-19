# R9 — WORK CONTEXT, CANONICAL SKILL RESOLUTION & ACTIVATION PACKET
## Canonical Architecture Specification

**Status:** APPROVED (Stage B Architecture Specification)  
**Author:** 04-solution-architect  
**Collaborators:** 27-platform-engineer, 14-governance-auditor, 06-software-engineer  
**Authority:** Canonical Agent Squad Control Plane  

---

## 1. Executive Summary & Scope Boundaries

Milestone **R9** establishes the canonical pipeline that transforms an R8 `ExecutionAssignment` into an immutable, auditable, deterministic `ActivationPacket` containing complete hierarchical work context, resolved canonical skills, tool requirements, and compiled specialist instructions.

### 1.1 Inviolable Boundaries (Zero-Leakage Invariants)
- **ZERO MCP Session Authority:** R9 does NOT create, validate, or mutate MCP sessions (`SessionStore`, `start_session`). Session lifecycle belongs exclusively to R10.
- **ZERO DelegationEnvelope Construction:** R9 builds `ActivationPacket`. Construction of legacy or transport-level `DelegationEnvelope` remains deferred or decoupled.
- **ZERO MCP Preflight:** Preflight execution validation belongs to R10.
- **ZERO Host Dispatch:** Subagent spawning (`invoke_subagent`), background processes, and host execution belong to R11.
- **ZERO Specialist Execution / LLM Calls:** No generative inference is invoked in R9.
- **ZERO Lifecycle Mutation:** R9 reads lifecycle stages and policies; it never advances or mutates work item lifecycle state.
- **ZERO Azure DevOps Mutation:** R9 reads project DevOps configuration; it never mutates Azure Boards, Repos, or Pipelines.

---

## 2. Consumed Domain Models & Invariants

R9 consumes canonical domain models established in R1 (`scripts/domain/delegation.py`, `scripts/domain/work_items.py`):

```
ExecutionAssignment (R8)
        │
        ▼
WorkContextBuilder
   ├── WorkItemPathResolver (R3)
   ├── HierarchyContextResolver (R3)
   └── Semantic Ancestor Normalizer (R9)
        │
        ▼
   WorkContext
        │
        ├─────────────────────────────┐
        ▼                             ▼
SkillResolver                 ToolRequirementsResolver
   ├── Native Skills             ├── @agent-squad/mcp
   ├── Assigned Skills           └── @azure-devops/mcp
   └── Discovered Skills (Budget <= 7)
        │                             │
        └──────────────┬──────────────┘
                       ▼
        SpecialistInstructionCompiler
                       │
                       ▼
                ActivationPacket
                       │
                       ▼
        ActivationRepository (SQLite)
```

### 2.1 Domain Models
1. **`AncestorSnapshot`**: Represents an ancestor in the hierarchy (`work_item_id`, `kind`, `title`, `stage`, `spec_summary`).
2. **`WorkContext`**: Complete context package containing:
   - `work_item_id`, `project_id`, `current_stage`
   - `title`, `description`, `definition_of_done`, `acceptance_criteria`
   - `ancestors`: Ordered chain `[Parent, Grandparent, Root]`
   - `ancestor_artifacts`: Map of relative path to content of ancestor specs (`epic.md`, `feature.md`, `story.md`)
   - `active_receipts`: Required receipts from StagePolicy
   - `filesystem_scope`: Explicitly allowed workspace directories
3. **`ActivationPacket`**: Immutable data contract delivered to activation repository:
   - `session_id` / `activation_id`: Deterministic unique identifier
   - `agent_id`, `role_name`, `work_item_id`
   - `work_context`: Complete `WorkContext`
   - `skill_manifest`: Structured manifest of loaded skills
   - `compiled_instruction`: The authoritative prompt payload
   - `instruction_hash`: SHA-256 of `compiled_instruction`
   - `created_at`: UTC timestamp

---

## 3. Hierarchical Work Context & Ancestor Traversal

### 3.1 Defect Resolution (R0-DEL-007)
Previously, `_build_work_item_context` loaded strictly `status.yaml` of the immediate work item folder. For tasks (`TASK-0001`), the specialist received zero context regarding the parent Story's acceptance criteria, the Feature's architectural scope, or the Epic's business goals.

### 3.2 Canonical Ancestor Traversal
1. `WorkContextBuilder` invokes `HierarchyContextResolver` with the target `work_item_id`.
2. Ascends the tree: `TASK -> STORY -> FEATURE -> EPIC`.
3. For each ancestor:
   - Loads `status.yaml` (title, stage, description, metadata).
   - Resolves canonical specification artifacts:
     - **Epic:** `epic.md` or `product-goal.md` $\rightarrow$ Strategic vision & business constraints.
     - **Feature:** `feature.md` or `component-design.md` $\rightarrow$ Capability scope & architecture requirements.
     - **Story:** `story.md` or `acceptance-criteria.md` $\rightarrow$ User value & acceptance criteria.
   - Normalizes into an `AncestorSnapshot` and adds file contents to `ancestor_artifacts`.
4. **Fail-Closed Semantics:** If an ancestor directory or required parent reference is declared in `status.yaml` but missing on disk, the builder fails closed with `IncompleteContextError`. Partial context execution is prohibited.

---

## 4. Deterministic Context Fingerprinting & Compilation Cache

### 4.1 Cache Invalidation Strategy
The compiler cache key must invalidate whenever:
1. The agent's `PROMPT.md` or `manifest.yaml` changes.
2. Any loaded skill file (`SKILL.md`) changes.
3. The work item's `status.yaml` changes.
4. **ANY ancestor's `status.yaml` or specification artifact changes.**

### 4.2 Work Context Fingerprint
```python
fingerprint_payload = {
    "work_item_id": work_item_id,
    "item_mtime": mtime(item_dir / "status.yaml"),
    "ancestor_mtimes": [
        (anc_id, mtime(anc_file))
        for anc_id, anc_file in resolved_ancestor_files
    ],
    "stage": current_stage,
}
context_fingerprint = sha256(json.dumps(fingerprint_payload, sort_keys=True))
```
This guarantees that modifications to an Epic or Story immediately invalidate cached Task prompts.

---

## 5. Canonical Skill Resolution Authority

### 5.1 Architecture & Separation of Concerns
1. **Domain Skills:**
   - Declared in `agents/<agent-id>/skills/manifest.yaml`.
   - Categories: `native` (identity axioms), `assigned` (explicit capabilities), `discovered` (runtime semantic matches).
   - Subject to **Strict Skill Budget:** `len(native) + len(assigned) + len(discovered) <= 7`.
   - If budget is exceeded:
     - In auto-select mode, `discovered` skills are trimmed to fit `7 - (native + assigned)`.
     - In explicit configuration, if `native + assigned > 7`, `SkillBudgetExceededError` is raised.
     - Silent arbitrary slicing (`skills[:7]`) without budgeting is strictly prohibited (resolving `R0-DEL-006`).
2. **Control Plane Tools (Segregated):**
   - Control tools (`@agent-squad/mcp`, `@azure-devops/mcp`) are NOT domain skills.
   - They do not consume domain skill budget slots.
   - They are resolved into a dedicated `ToolRequirements` structure injected into the prompt.

### 5.2 Resolution Loading Order
Specialist loading order adheres strictly to the 5-step rule:
1. `Persona` (`PROMPT.md`)
2. `Manifest` (`manifest.yaml`)
3. `Native Skills` (`skills/native/.../SKILL.md`)
4. `Assigned Skills` (`skills/.../SKILL.md`)
5. `Discovered Skills` (vector/semantic discovery constrained by remaining budget)

---

## 6. Specialist Instruction Compiler

### 6.1 Authoritative Compilation Pipeline
The compiler assembles the canonical prompt in strict order:
1. `Environment Section`: Timestamp, portable runtime paths, workspace scope.
2. `Persona Prompt`: `agents/<id>/PROMPT.md`.
3. `Cognitive Contract`: Anti-hallucination rules, SoD, verification requirements.
4. `Resolved Skills`: Formatted `SKILL.md` contents with runtime metadata.
5. `Hierarchical Work Context`:
   - Ancestor lineage with icons (🔶 Epic, 🟣 Feature, 🔷 Story, 🟡 Task).
   - Extracted ancestor specification summaries.
   - Work item status, description, and acceptance criteria.
6. `Operational Tooling & DevOps Context`: ADO accounts, permissions, MCP tools.
7. `Memory Architecture`: 3-pillar memory protocol.
8. `Integration Engines`: Declared engine tools if present in manifest.

### 6.2 Fail-Closed Error Propagation (R0-DEL-003, R0-DEL-004)
- Any failure in prompt compilation (missing file, invalid YAML, budget overflow) MUST immediately raise a typed `CompilationError`.
- Catching exceptions and returning empty `rendered_prompt: ""` is forbidden.
- The returned hash (`instruction_hash`) is derived exclusively from the SHA-256 of the compiled prompt string (`compiled_instruction`), never from a synthetic briefing string.

---

## 7. Activation Service & Persistence

### 7.1 SQLite Schema (`activation_packets`)
Activation packets are persisted in `%SQUAD_RUNTIME%/banco/squad.db`:

```sql
CREATE TABLE IF NOT EXISTS activation_packets (
    activation_id       TEXT PRIMARY KEY,
    assignment_id       TEXT NOT NULL,
    agent_id            TEXT NOT NULL,
    role_name           TEXT NOT NULL,
    work_item_id        TEXT NOT NULL,
    project_id          TEXT NOT NULL,
    stage               TEXT NOT NULL,
    context_fingerprint TEXT NOT NULL,
    skill_manifest_json TEXT NOT NULL,
    compiled_instruction TEXT NOT NULL,
    instruction_hash    TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(assignment_id, context_fingerprint, instruction_hash)
);
```

### 7.2 Idempotency
Calling `ActivationService.activate(assignment)` multiple times with unchanged context, skills, and prompt returns the existing `ActivationPacket` from the repository without recompilation.

---

## 8. Diagnostic & Legacy Alignment

| Diagnostic ID | Description | Resolution in R9 |
|---|---|---|
| **R0-DEL-003** | Render failure swallowed in `prepare_delegation` | **RESOLVED:** Fail-closed exception propagation |
| **R0-DEL-004** | Compiled instruction not authoritative payload | **RESOLVED:** Hash derived directly from compiled instruction |
| **R0-DEL-006** | Skill selection uses arbitrary slice `skills[:7]` | **RESOLVED:** Unified `SkillResolver` with semantic budgeting |
| **R0-DEL-007** | Work context missing ancestor artifacts | **RESOLVED:** Full ancestor traversal via `WorkContextBuilder` |
| **R0-DEL-001** | Invalid session fallback | *Deferred to R10 (MCP Session Authority)* |
| **R0-DEL-002** | Preflight dummy validation | *Deferred to R10 (MCP Preflight)* |
| **R0-DEL-005** | Silent fallback to software-engineer | *Resolved in R8 / R8.2* |
