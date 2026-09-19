# R9 — CURRENT ACTIVATION & CONTEXT MAP
## Stage A Architectural Analysis & Diagnostic Survey

**Author:** 27-platform-engineer  
**Mode:** READ-ONLY Architectural Audit  
**Date:** 2026-09-18  

---

### 1. Current WorkContext Construction & Gaps

- **Current Implementation:** `scripts/render_agent_prompt.py::_build_work_item_context()` loads strictly the local `status.yaml` of the work item.
- **Defects Identified:**
  - **Zero Ancestor Traversal:** For a Task (`work/<project_id>/EPIC-001/FEATURE-001/STORY-001/TASK-0001`), the renderer only reads `TASK-0001/status.yaml`. It completely ignores `STORY-001/story.md`, `FEATURE-001/feature.md`, and `EPIC-001/epic.md`.
  - **No Normalized Sections:** Does not extract strategic goals from Epic, capabilities from Feature, or acceptance criteria / outcomes from Story.
  - **R0 Diagnostic Impact:** Confirms defect `R0-DEL-007` (`_build_work_item_context only loads local status.yaml without ancestor context`).

---

### 2. Current Renderer Cache & Cache Key Invalidation

- **Current Implementation:** `scripts/render_agent_prompt.py::_build_cache_key()` hashes the file modification timestamps (`mtime`) of `status.yaml` and `epic.md` directly inside the work item directory.
- **Defects Identified:**
  - When working on a Task or Story, ancestor artifacts are never checked for `mtime`.
  - If a Product Owner modifies `EPIC-001/epic.md` or a Story's acceptance criteria, the Task renderer cache key remains identical and serves stale compiled prompts.

---

### 3. Current Skill Resolution & Duplicate Logic

- **Multiple Disconnected Resolvers:**
  1. `scripts/agent_squad.py::AgentSquad.activation_packet()`: Parses `agents/<id>/skills/manifest.yaml` for `native`, `assigned`, and `discovered`. Enforces budget `len(selected) <= 7`.
  2. `integrations/resolvers/assignment_resolver.py::get_assignment()`: Used to parse manifests directly and hardcode `skills[:7]`.
- **Defects Identified:**
  - `skills[:7]` arbitrary slice in legacy resolver (addressed in R8.2, but formal skill resolution authority must reside in a unified `SkillResolver`).
  - No clean architectural distinction between domain skills and mandatory control-plane tools (`agent-squad-mcp`, `azure-devops-mcp`). Tools are mistakenly treated as domain skills in some manifests.

---

### 4. Manifest Skill Semantics (Native vs Assigned vs Discovered)

- `manifest.yaml` declares:
  - `native`: Core persona skills required for identity (e.g., TDD craftsman skills).
  - `assigned`: Technical capabilities and integration engines explicitly authorized for the persona.
  - `discovery`: Policy-driven optional skills constrained by `maximum_loaded`.
- **Defects Identified:**
  - Missing native skills previously raised untyped errors or were bypassed.
  - No single authority validates that all resolved skill files (`SKILL.md`) physically exist and belong to the canonical catalog before compilation.

---

### 5. Render Failure Handling in Delegation

- **Current Implementation:** `integrations/resolvers/assignment_resolver.py::prepare_delegation()` wrapped the renderer in `try: ... except Exception: pass`.
- **Defects Identified:**
  - Render failures resulted in empty `rendered_prompt: ""` while returning a synthetic briefing hash.
  - Triggers `R0-DEL-003` (`render failure was swallowed quietly`) and `R0-DEL-004` (`Delegation hash is derived from synthetic briefing, not authoritative compiled prompt`).

---

### 6. Raw `PROMPT.md` Bypasses

- `agents/*/PROMPT.md` is accessed directly by script utilities and legacy adapters rather than always passing through the canonical compiler.
- Direct invocation using raw personas loses all project context, lifecycle stage rules, ancestor goals, and skill instructions.

---

### 7. Recommendations for Stage B Architecture

1. Create a single canonical `WorkContext` builder consuming `WorkItemPathResolver` and R3 hierarchy.
2. Implement semantic ancestor normalization:
   - Epic: Strategic Objective & Scope
   - Feature: Functional Capability & Architecture Vision
   - Story: Acceptance Criteria & User Value
   - Task: Bounded Execution Scope
3. Build a deterministic context fingerprint over ancestor content to invalidate renderer caches.
4. Establish `SkillResolver` as the sole authority:
   - Native $\rightarrow$ Explicit Assigned $\rightarrow$ Discovered (constrained by budget).
   - Tools (`agent-squad-mcp`, `azure-devops-mcp`) explicitly segregated from domain skill budget.
5. Make `ActivationPacket.compiled_instruction` the single authoritative execution payload with fail-closed render failure propagation.
