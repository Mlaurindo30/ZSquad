# R10 — CURRENT MCP / SESSION / PREFLIGHT MAP
**Author:** 27-platform-engineer (Kelsey Hightower & Team Topologies — Internal Developer Platform Architect)  
**Status:** COMPLETE (READ-ONLY INSPECTION)  
**Date:** 2026-09-18  
**Scope:** Agent Squad Control Plane — MCP Session Authority, Preflight Verification, and Delegation Pipeline

---

## 1. Executive Summary & Purpose

In milestone R10, the Agent Squad platform bridges the deterministic activation package constructed in R9 (`ActivationPacket`) to the execution environment via a validated, session-bound, preflight-approved `DelegationEnvelope`.

This document records the exhaustive read-only inspection of the existing MCP session subsystem, resolver interfaces, fallback mechanisms, preflight verification logic, and delegation construction points. It exposes the concrete failure modes (including `R0-DEL-001` and `R0-DEL-002`) and establishes the exact baseline for the canonical session authority, preflight engine, and delegation persistence layer to be specified in Stage B and implemented in Stage C.

---

## 2. Current Session Lifecycle

### 2.1 Session Creation
- **Source:** `integrations/mcp_session_store.py::SessionStore.create_session(host, project_root, work_item, capability_report_hash)`
- **Caller:** `integrations/resolvers/session_manager.py::start_session(args, ctx, session_store)`
- **Behavior:**
  - Generates `session_id = str(uuid.uuid4())`.
  - Sets hardcoded `ttl = 3600` seconds.
  - Computes `policy_hash = sha256(f"{host}:{project_root}:{work_item}".encode()).hexdigest()`.
  - Stores in-memory dictionary entry:
    ```python
    self.sessions[session_id] = {
        "session_id": session_id,
        "host": host,
        "project_root": project_root,
        "work_item": work_item,
        "capability_report_hash": capability_report_hash,
        "expires_at": time.time() + ttl,
        "policy_hash": policy_hash,
        "status": "active",
    }
    ```
- **Defects & Limitations:**
  - **Purely ephemeral in-memory storage:** Session state is held in `self.sessions = {}`. If the MCP process restarts or executes across processes, all sessions are lost.
  - **No durable persistence:** Sessions are not persisted to SQLite `banco/squad.db`.
  - **No project/governance binding:** Does not validate against R5 `ProjectBinding` or check if `project_root` matches canonical project root.
  - **Missing revision tracking:** Sessions do not initialize an explicit `revision` counter (e.g. `revision = 1`).

### 2.2 Session Storage
- **Implementation:** Ephemeral Python dictionary on `SessionStore` instance.
- **Locking / Concurrency:** No thread-safe locking or atomic database transactions.
- **Cross-process sharing:** Not possible without a durable database store.

### 2.3 Session TTL & Expiry
- **Current Behavior:** `get_session(session_id)` checks:
  ```python
  session = self.sessions.get(session_id)
  if session and session["expires_at"] > time.time() and session["status"] != "blocked":
      return session
  return None
  ```
- **Defects:**
  - Does not distinguish between "session does not exist" and "session expired" or "session blocked". Returning `None` triggers downstream fallback logic rather than typed errors (`SessionNotFoundError`, `SessionExpiredError`, `SessionBlockedError`).
  - No explicit renewal policy.

### 2.4 Session Resume & Revision Handling
- **Source:** `integrations/mcp_session_store.py::SessionStore.resume_session(session_id, last_revision)`
- **Caller:** `integrations/resolvers/session_manager.py::resume_session(args, ctx, session_store)`
- **Behavior:**
  ```python
  session = self.get_session(session_id)
  if not session:
      raise ValueError("Invalid or expired session")
  # For simplicity, we just extend the TTL
  session["expires_at"] = time.time() + 3600
  return {"session_id": session_id, "ttl": 3600, "status": session["status"]}
  ```
- **Defects:**
  - **Decorative `last_revision`:** Parameter `last_revision` is accepted in signature but completely ignored. No revision comparison or optimistic concurrency check is performed.
  - **Silent TTL extension:** Extends TTL by 3600 seconds unconditionally without validating capability report changes or work-item binding drift.

---

## 3. Project & Work-Item Correlation

- **Current State:**
  - `start_session` performs only a rudimentary check: `if not os.path.exists(args["project_root"]): raise ValueError("Project root does not exist")`.
  - No correlation with R5 `ProjectBinding` or canonical workspace directories (`%SQUAD_RUNTIME%/work/<project_id>`).
  - No validation that `work_item` belongs to `project_root`.
  - In `get_assignment` (`assignment_resolver.py`), session correlation is optional:
    ```python
    session = session_store.get_session(session_id) if session_id and session_store else None
    work_item_id = (session and session.get("work_item")) or args.get("work_item_id") or "WORK-ITEM-LEGACY"
    project_id = (session and session.get("project_root")) or args.get("project_id") or "default"
    ```
    This allows unauthenticated or decoupled requests to bypass session identity entirely.

---

## 4. Capability Hash Mechanics

- **Current State:**
  - `start_session` expects `capability_report_hash: str` as an input argument from the caller.
  - The hash is stored as a decorative attribute; it is never verified against actual system capabilities, host runtime features, or available MCP tools.
  - `policy_hash` is computed as `sha256(f"{host}:{project_root}:{work_item}".encode()).hexdigest()`, ignoring actual tools or capabilities.

---

## 5. Fake Fallback Sessions & Synthetic Replacements

A pervasive anti-pattern exists across `integrations/resolvers/` where missing or invalid sessions are masked with hardcoded dummy defaults:

### 5.1 `assignment_resolver.py`
- In `create_handoff`:
  ```python
  session = session_store.get_session(args["session"])
  if not session:
      session = {"project_root": "test_root", "work_item": "test_item"}
  ```
  *(Directly causes `R0-DEL-001` failure; silently fabricates invalid project/work-item references).*
- In `prepare_delegation`:
  ```python
  session = session_store.get_session(session_id)
  project_root = session.get("project_root", os.getcwd()) if session else os.getcwd()
  work_item = session.get("work_item", "UNSPECIFIED") if session else "UNSPECIFIED"
  ```
  *(Uses `os.getcwd()` and `"UNSPECIFIED"`, bypassing path containment and project binding).*

### 5.2 `context_engine.py`
- In `get_context`, `memory_query`, and `memory_propose_delta`:
  ```python
  session = session_store.get_session(args["session"])
  if not session:
      session = {"project_root": "test_root", "work_item": "test_item"}
  ```

### 5.3 `execution_recorder.py`
- In `record_execution` and `report_failure`:
  ```python
  session = session_store.get_session(args["session"])
  if not session:
      project_root = "test_root"
  ```
  ```python
  session = session_store.get_session(args["session"])
  if not session:
      session = {"project_root": "test_root", "work_item": "test_item"}
  ```

### 5.4 `gate_evaluator.py`
- In `evaluate_gate`:
  ```python
  session = session_store.get_session(args["session"])
  if not session:
      session = {"project_root": "test_root", "work_item": "test_item"}
  project_root = session.get("project_root", "test_root")
  ```

### 5.5 `skill_manager.py`
- In `discover_skill`:
  ```python
  session = session_store.get_session(args["session"])
  if not session:
      session = {"project_root": "test_root", "work_item": "test_item"}
  ```

**Impact:** Every single one of these fallbacks violates the R1/R5 invariant (`FORBIDDEN_LEGACY_TOKENS` forbids `"test_root"` and `"test_item"`). In normal runtime, any operation with an absent or invalid session MUST FAIL CLOSED.

---

## 6. Preflight Implementation & Flaws

- **Source:** `integrations/resolvers/impact_analyzer.py::preflight(args, ctx, session_store)`
- **Current Code:**
  ```python
  def preflight(args, ctx, session_store):
      return {"status": "allow", "reason": "Paths exist and session is active"}
  ```
- **Analysis:**
  - **Constant Allow Stub:** Preflight does zero checks. It does not verify that `session` exists, does not inspect `args["paths"]`, does not check filesystem scope, does not verify tool availability, and does not check lifecycle readiness.
  - Directly causes `R0-DEL-002` failure in `scripts/tests/diagnostics/r0_delegation_contract_red.py`.
  - Must be replaced by a deterministic preflight engine that executes real checks and fails closed.

---

## 7. Delegation Flow & DelegationEnvelope

### 7.1 `prepare_delegation` Flow
- **Current Behavior:**
  - Takes `target_role`, `scope`, `action`, `session`.
  - Extracts `project_root` and `work_item` (with `os.getcwd()` / `"UNSPECIFIED"` fallbacks).
  - Constructs a synthetic 8-block briefing text.
  - Calls `render_agent_prompt(agent=target_role, work_item=w_item)`.
  - Computes `p_hash = sha256(rendered_prompt.encode("utf-8")).hexdigest()`.
  - Returns `{"hash": p_hash, "briefing": briefing, "rendered_prompt": rendered_prompt}`.
- **Gaps:**
  - Does not construct or return a canonical `DelegationEnvelope`.
  - Does not consume R9 `ActivationPacket`.
  - Does not invoke preflight before preparing delegation.
  - Does not record the delegation event or persist the delegation envelope.

### 7.2 `DelegationEnvelope` Contract
- Defined in `scripts/domain/delegation.py` and `contracts/core/delegation-envelope.schema.json`.
- Holds: `delegation_id`, `sender_role`, `target_role`, `work_item_id`, `scope_summary`, `action_requested`, `compiled_instruction`, `instruction_hash`.
- Validated with `additionalProperties: false` in core schema.
- In R10, the canonical `DelegationService` will build this envelope directly from the preflight-approved `ActivationPacket`, binding it to the active MCP session and persisting metadata in `banco/squad.db`.

---

## 8. MCP Tool & Caller Audit Matrix

| MCP Tool | Resolver | Session Dependency | Current Fallback | Required R10 Behavior |
|---|---|---|---|---|
| `start_session` | `session_manager.py` | Creates session | None (`ValueError` if root missing) | Durable persistence, project validation, deterministic capability hash |
| `resume_session` | `session_manager.py` | Resumes session | None (`ValueError` if missing) | Validate revision match, capability integrity, project match, TTL check |
| `get_assignment` | `assignment_resolver.py` | Resolves role via R8 | Soft fallback to args | Enforce session context if session passed |
| `prepare_delegation` | `assignment_resolver.py` | Prepares briefing/envelope | `os.getcwd()`, `UNSPECIFIED` | Consume R9 `ActivationPacket`, enforce session, require preflight `ALLOW`, return canonical envelope |
| `create_handoff` | `assignment_resolver.py` | Records handoff | `test_root`, `test_item` | Fail closed (`SessionNotFoundError`) |
| `preflight` | `impact_analyzer.py` | Validates preconditions | Hardcoded `allow` | Real validation: session active, paths exist, tools available, stage compatible, fail closed |
| `get_context` | `context_engine.py` | Retrieves context | `test_root`, `test_item` | Fail closed if session missing/invalid |
| `memory_query` | `context_engine.py` | Queries SQLite memory | `test_root`, `test_item` | Fail closed if session missing/invalid |
| `memory_propose_delta` | `context_engine.py` | Proposes memory change | `test_root`, `test_item` | Fail closed if session missing/invalid |
| `record_execution` | `execution_recorder.py` | Records execution | `test_root`, `test_item` | Fail closed if session missing/invalid |
| `record_evidence` | `execution_recorder.py` | Records evidence hash | None | Fail closed if session missing/invalid |
| `evaluate_gate` | `gate_evaluator.py` | Evaluates lifecycle gate | `test_root`, `test_item` | Fail closed if session missing/invalid |
| `report_failure` | `execution_recorder.py` | Records failure | `test_root`, `test_item` | Fail closed if session missing/invalid |
| `discover_skill` | `skill_manager.py` | Skill discovery | `test_root`, `test_item` | Fail closed if session missing/invalid |
| `curate_skill` | `skill_manager.py` | Skill curation | None | Fail closed if session missing/invalid |
| `impact_analysis` | `impact_analyzer.py` | Blast radius calculation | None | Fail closed if session missing/invalid |
| `doctor` | `doctor.py` | System health check | Read-only | Retain diagnostics |
| `replay_receipt` | `execution_recorder.py` | Receipt verification | Read-only | Retain receipt verification |

---

## 9. Architectural Defects Owned by R10

1. **R0-DEL-001 (Invalid Session Fallback):**
   - Resolvers substitute `test_root` and `test_item` when session is absent or expired, silently succeeding instead of failing closed.
2. **R0-DEL-002 (Preflight is Stub):**
   - `preflight` returns `{"status": "allow"}` without validating session, paths, tools, or lifecycle stages.
3. **Absence of Canonical Session Authority:**
   - In-memory `SessionStore` lacks durable SQLite persistence, revision enforcement, and strict project binding.
4. **Decoupled Delegation Creation:**
   - `prepare_delegation` does not consume `ActivationPacket` from R9, fails to verify preflight, and constructs detached strings instead of an authoritative `DelegationEnvelope`.
5. **Lack of Delegation Envelope Persistence:**
   - No table in `banco/squad.db` tracks delegation envelopes, preflight decisions, or dispatch readiness.

---

## 10. Platform Readiness Statement

The current system has clear separation boundaries but suffers from soft-fallback defaults that violate delivery invariants.
Stage B (Architecture) can proceed immediately to design the canonical session manager, preflight validator, and delegation repository under `scripts/runtime/delegation/`, resolving `R0-DEL-001` and `R0-DEL-002` while preserving host neutrality and strictly forbidding subagent dispatch.
