# R10 — MCP SESSION AUTHORITY, PREFLIGHT & DELEGATION ENVELOPE ARCHITECTURE

**Lead Architect:** 04-solution-architect (Martin Fowler & Gregor Hohpe — Solution Architect & Enterprise Integration Lead)  
**Collaborators:** 10-security-reviewer, 14-governance-auditor, 27-platform-engineer  
**Status:** APPROVED FOR IMPLEMENTATION  
**Date:** 2026-09-18  
**Governing Milestone:** R10  

---

## 1. Scope
Milestone R10 establishes the canonical, fail-closed integration layer between the compiled R9 `ActivationPacket` and the specialist execution environment. R10 transforms a valid `ActivationPacket`, bound to an active MCP session and validated by deterministic preflight checks, into an authoritative `DelegationEnvelope` with state `READY_FOR_DISPATCH`.

The canonical pipeline is strictly defined as:
```
R9 ActivationPacket (READY)
        +
ProjectBinding (R5)
        +
Canonical MCP Session (R10)
        +
Required Tools & Capability Validation (R10)
        ↓
Preflight Verification (Real Conditions, Fail-Closed)
        ↓
DelegationEnvelope (Canonical R1 Domain Model)
        ↓
Persisted in banco/squad.db (delegation_envelopes)
        ↓
READY_FOR_DISPATCH
```

---

## 2. Non-Goals
To prevent scope creep and maintain strict separation of concerns, the following boundaries are absolute:
1. **ZERO Host Dispatch:** No execution of `invoke_subagent`, `invoke_agent`, `spawn_agent`, or background worker processes. Host-native dispatch belongs exclusively to R11.
2. **ZERO Specialist Execution / LLM Calls:** No invocation of LLM inference, prompt execution, or model providers.
3. **ZERO Rerouting:** Selected agent from R8 `ExecutionAssignment` cannot be changed.
4. **ZERO Skill Manipulation:** Skill load order and selection from R9 `ActivationPacket` cannot be modified.
5. **ZERO Instruction Mutation:** `compiled_instruction` from R9 `ActivationPacket` is immutable.
6. **ZERO Lifecycle Advance:** Does not transition R4 lifecycle stages or satisfy gates.
7. **ZERO Azure DevOps Mutation:** Does not write work items, iterations, or PRs.
8. **ZERO Scheduler / Watchdog:** Does not implement background polling, cron, or continuous loops.

---

## 3. Existing Defects Owned by R10
Stage A mapping revealed core architectural vulnerabilities that R10 directly remediates:
- **R0-DEL-001 (Invalid Session Fallbacks):** `assignment_resolver.py`, `context_engine.py`, `execution_recorder.py`, and `gate_evaluator.py` fell back to `test_root` and `test_item` when sessions were missing or invalid. Under R1/R5 domain rules, `test_root` and `test_item` are forbidden legacy tokens. R10 enforces fail-closed semantics (`SessionNotFoundError`, `SessionExpiredError`).
- **R0-DEL-002 (Preflight Stub):** `impact_analyzer.py::preflight()` returned unconditional `{"status": "allow"}` without validating session, paths, tools, or lifecycle stages. R10 replaces this with a real preflight engine.
- **Unverified Session Resume:** `SessionStore.resume_session()` accepted `last_revision` but ignored it, blindly extending TTL. R10 enforces revision parity and capability hash validation on resume.
- **Missing Durable Persistence:** Sessions and delegation envelopes were purely ephemeral in-memory dicts. R10 persists them into `banco/squad.db`.

---

## 4. Session Authority
There is exactly ONE canonical MCP session authority in the Agent Squad runtime, located in:
`scripts/runtime/delegation/sessions.py` (`CanonicalSessionManager`).

The `SessionStore` class in `integrations/mcp_session_store.py` is refactored into a compatibility facade backed by the canonical session manager and SQLite database, ensuring backward compatibility for MCP stdio/HTTP runners while guaranteeing durable state.

---

## 5. Session Identity
A canonical MCP session represents an authenticated, bounded execution context:
- `session_id`: Unique identifier (UUIDv4).
- `project_id`: Logical project identifier matching R5 `ProjectBinding`.
- `project_root`: Canonical filesystem path to the project root.
- `work_item_id`: Target work item identifier (or None for project-level sessions).
- `host`: Declared host runtime environment identifier.
- `capability_report_hash`: SHA-256 digest of declared session capabilities and tools.
- `policy_hash`: SHA-256 digest binding host, project, and work item.
- `revision`: Integer monotonic sequence counter (starts at 1, increments on state mutation or explicit renewal).
- `status`: Enum (`active`, `blocked`, `expired`, `closed`).
- `created_at`: UTC timestamp.
- `expires_at`: UTC timestamp.

---

## 6. TTL Semantics
- Default session TTL is 3600 seconds (1 hour), configurable per project policy.
- `expires_at = created_at + ttl_seconds`.
- A session is expired if `current_utc_timestamp >= expires_at`.
- Expired sessions immediately fail closed with `SessionExpiredError`.
- Accessing a session (`get_session`) NEVER silently extends TTL.
- Renewal requires an explicit call to `resume_session` or `renew_session` with valid revision and capability proofs.

---

## 7. Resume & Revision Integrity
`resume_session(session_id, last_revision, expected_capability_hash=None)` must enforce:
1. **Existence:** Session must exist; otherwise raise `SessionNotFoundError`.
2. **Freshness:** Session must not be expired; otherwise raise `SessionExpiredError`.
3. **Non-Blocked:** Session status must not be `blocked` or `closed`.
4. **Revision Compatibility:** `last_revision` must match current session revision. If mismatched, raise `SessionRevisionConflictError`.
5. **Capability Stability:** If `expected_capability_hash` is provided, it must match current `capability_report_hash`.
6. **Explicit Increment:** Upon successful resume, `revision` increments by 1, `expires_at` is extended by TTL, and state is persisted to SQLite.

---

## 8. Project Binding Validation
Session parameters are validated against the canonical R5 `ProjectBinding`:
- `project_id` must match `ProjectBinding.project_id`.
- `project_root` must match canonical resolved `ProjectBinding.project_root`.
- Cross-project session reuse is strictly prohibited.
- Paths using `os.getcwd()` or synthetic `"test_root"` are rejected with `PathContainmentViolation` / `ValidationError`.

---

## 9. Work-Item Binding Validation
Where an activation targets a work item:
- `session.work_item_id` must match `ActivationPacket.work_item_id`.
- If session was created for a specific work item, it cannot be used to delegate for another work item.
- If session is project-wide (`work_item_id` is None/empty), it may delegate for any valid work item within that project.

---

## 10. Capability Hash Mechanics
- Canonical `compute_capability_hash(tools: List[str], capabilities: Dict[str, Any]) -> str`:
  - Normalizes tool names into sorted unique list.
  - Normalizes capability keys/values into deterministic JSON (`canonical_json`).
  - Returns `sha256(canonical_string).hexdigest()`.
- Deterministic across processes and platforms.
- Does NOT include volatile timestamps or session IDs.

---

## 11. Required Tools Validation
- `ActivationPacket` declares `required_tools` (or derived from agent manifest/devops config).
- Control-plane tools:
  - `agent-squad-mcp`: Mandatory control plane tool.
  - `azure-devops-mcp`: Required ONLY if the assigned stage/work item requires Azure DevOps interaction (e.g. PR review, board updates).
- Filesystem & test tools: Required based on stage (e.g. `IMPLEMENTATION` requires write and test execution capabilities).
- Tools $\neq$ Skills: A tool is an executable interface (MCP tool, CLI binary, file capability); a skill is a cognitive instruction markdown file loaded into the prompt.

---

## 12. Preflight Authority
There is exactly ONE canonical preflight authority:
`scripts/runtime/delegation/preflight.py` (`PreflightValidator`).

The legacy `impact_analyzer.py::preflight()` delegates directly to `PreflightValidator`.

---

## 13. Preflight Deterministic Checks
The preflight pipeline executes 9 sequential checks:
1. **Activation Readiness Check:** `ActivationPacket` status must be valid, hash verified, non-empty compiled instruction.
2. **Session Validity Check:** Session exists, is active, not expired, not blocked.
3. **Project & Path Containment Check:** Project matches session and R5 binding; target paths exist within project root.
4. **Work Item Correlation Check:** Work item ID in activation matches session work item.
5. **Required Tools Availability Check:** All tools required by the activation are available in session capability set.
6. **Capability Set Parity Check:** Capability hash matches declared capabilities.
7. **Assignment Freshness Check:** R8 `ExecutionAssignment` remains in `ASSIGNED` or `ACTIVE` status (not revoked/superseded).
8. **Lifecycle Stage Compatibility Check:** Work item's current R4 lifecycle stage matches the stage for which the packet was compiled.
9. **Context Freshness Check:** WorkContext fingerprint matches current state of ancestor artifacts and work item definition.

---

## 14. Fail-Closed Semantics
- Any check failure immediately halts evaluation and yields `PreflightDecision.BLOCK`.
- Any unhandled exception during preflight yields `PreflightDecision.BLOCK` with typed error details.
- Code pattern `except Exception: return allow` is STRICTLY FORBIDDEN.
- Return structure:
  ```python
  @dataclass(frozen=True)
  class PreflightResult:
      decision: PreflightDecision  # ALLOW or BLOCK
      preflight_id: str
      session_id: str
      activation_id: str
      checks_evaluated: List[PreflightCheck]
      reasons: List[str]
      timestamp: datetime
  ```

---

## 15. Activation Staleness
If any ancestor artifact or work item description was modified after `ActivationPacket.created_at`:
- `WorkContextBuilder.compute_fingerprint()` will yield a different fingerprint.
- Preflight detects mismatch and marks activation `STALE`.
- Preflight decision: `BLOCK`.

---

## 16. Assignment Staleness
If the assignment in `banco/squad.db` (`execution_assignments` table) has been superseded, revoked, or transitioned to completed:
- Preflight detects assignment is no longer active.
- Preflight decision: `BLOCK`.

---

## 17. Lifecycle Staleness
If the work item's current stage in `lifecycle_history` or work item file has advanced beyond the assignment's designated stage:
- Preflight detects stage mismatch.
- Preflight decision: `BLOCK`.

---

## 18. DelegationEnvelope Construction
When preflight evaluates to `ALLOW`, the `DelegationEnvelopeBuilder` constructs the canonical `DelegationEnvelope` from `scripts/domain/delegation.py`:
- `delegation_id`: `DEL-<hex12>`
- `sender_role`: `"00-delivery-orchestrator"` (or caller role)
- `target_role`: `ActivationPacket.role_name`
- `work_item_id`: `ActivationPacket.work_item_id`
- `scope_summary`: Summary derived from `WorkContext`
- `action_requested`: Action corresponding to lifecycle stage
- `compiled_instruction`: `ActivationPacket.compiled_instruction` (verbatim)
- `instruction_hash`: `ActivationPacket.instruction_hash` (verbatim)

The core contract schema `contracts/core/delegation-envelope.schema.json` is strictly satisfied.

---

## 19. Payload Immutability
- `compiled_instruction` is transferred byte-for-byte from `ActivationPacket` to `DelegationEnvelope`.
- SHA-256 digest is re-verified upon envelope construction: `sha256(compiled_instruction) == instruction_hash`.
- Zero prefixing, zero appending, zero host-specific framing.

---

## 20. Persistence Architecture
Delegations are persisted in `banco/squad.db` in table `delegation_envelopes`:
```sql
CREATE TABLE IF NOT EXISTS delegation_envelopes (
    delegation_id TEXT PRIMARY KEY,
    activation_id TEXT NOT NULL,
    assignment_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    selected_agent_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    instruction_hash TEXT NOT NULL,
    context_fingerprint TEXT NOT NULL,
    preflight_id TEXT NOT NULL,
    preflight_decision TEXT NOT NULL,
    envelope_payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delegation_work_item ON delegation_envelopes(work_item_id);
CREATE INDEX IF NOT EXISTS idx_delegation_session ON delegation_envelopes(session_id);
```

Sessions are persisted in `mcp_sessions`:
```sql
CREATE TABLE IF NOT EXISTS mcp_sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    project_root TEXT NOT NULL,
    work_item_id TEXT,
    host TEXT NOT NULL,
    capability_report_hash TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON mcp_sessions(project_id);
```

---

## 21. Idempotency & Deduplication
- Re-requesting delegation for identical `(activation_id, session_id, session.revision)` returns the previously constructed `DelegationEnvelope`.
- If session revision increments, or activation context changes, a new delegation envelope is constructed with a distinct `delegation_id`.

---

## 22. Security Architecture (STRIDE Alignment)
- **Spoofing:** Sessions validated against SQLite store with cryptographic policy hash; fake session identifiers immediately rejected.
- **Tampering:** `instruction_hash` protects compiled prompt; envelope schema has `additionalProperties: false`.
- **Repudiation:** Full audit trail recorded in `delegation_envelopes` and domain events published to `DomainEvent` outbox.
- **Information Disclosure:** Zero plaintext credentials stored in session or envelope; secrets filtered before compilation.
- **Denial of Service:** Session TTL prevents stale session accumulation; memory locks protect SQLite writes.
- **Elevation of Privilege:** Preflight validates required tools and capabilities; unprivileged sessions cannot execute sensitive tools.

---

## 23. Legacy Compatibility (`prepare_delegation`)
- `prepare_delegation` in `assignment_resolver.py` is upgraded to call `DelegationService`.
- If session is missing or invalid: raises `ValueError` / `SessionNotFoundError` (fixing `R0-DEL-001`).
- If preflight blocks: returns status `BLOCKED` with detailed reasons.
- Returns legacy compatible dictionary keys (`hash`, `briefing`, `rendered_prompt`, `envelope`) while enforcing canonical contracts.

---

## 24. R11 Boundary
R10 ends strictly at `DelegationEnvelope` with status `READY_FOR_DISPATCH` stored in SQLite and returned to caller.
R11 is the host-native dispatch milestone that will ingest the `DelegationEnvelope` and trigger the appropriate host adapter (`antigravity`, `gemini`, `claude`, etc.).

---

## 25. Remaining Phases & Roadmap
- **R10:** MCP Session Authority, Preflight Engine & DelegationEnvelope (CURRENT)
- **R11:** Host-Native Specialist Dispatch & Subagent Lifecycle
- **R12:** Execution Verification & Receipt Ledger
- **R13:** Continuous Autonomous Orchestration & Feedback Loop
