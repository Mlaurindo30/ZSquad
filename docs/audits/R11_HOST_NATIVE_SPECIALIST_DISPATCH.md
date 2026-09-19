# AUDIT REPORT: MILESTONE R11 — HOST-NATIVE SPECIALIST DISPATCH
## Authoritative Verification, Compliance & Architecture Conformance Audit

**Lead Auditor:** `09-code-reviewer` (Michael Feathers & Google Engineering — Static Analysis & Code Quality Auditor)  
**Collaborators:** `27-platform-engineer`, `04-solution-architect`, `06-software-engineer`, `11-test-engineer`, `10-security-reviewer`, `14-governance-auditor`  
**Governing Milestone:** Milestone R11 (HOST-NATIVE SPECIALIST DISPATCH)  
**Status:** FULLY COMPLIANT / APPROVED  
**Date:** 2026-09-18  
**Baseline Git Commit:** `c3971bc0bc8d1bbd9947b154f627cf3f3b914306`  
**Target Repository:** `agent_squad`  

---

### SECTION A: Executive Summary & Milestone Scope

Milestone R11 bridges the authoritative delegation pipeline established in R10 (`DelegationEnvelope` in state `READY_FOR_DISPATCH`) to actual host execution substrates without coupling the Agent Squad core control plane to any proprietary host API or process model.

Prior to R11, all host integration files in `integrations/` were purely static client installer scripts for MCP STDIO configuration. No runtime layer existed to spawn specialist agents or bridge them to the Host's native subagent tool mechanism. Furthermore, `ContinuousTriggerEngine` operated in a hollow loop returning empty `agent_dispatches: []`, and no cryptographic proof of dispatch (`DispatchReceipt`) was generated.

Milestone R11 successfully instituted:
1. A strict Hexagonal (Ports & Adapters) runtime seam centered around `HostDispatchPort`.
2. A deterministic `HostRegistry` with clean resolution precedence and fail-closed security.
3. Production-grade host adapters for Google Antigravity IDE (`invoke_subagent`), OpenAI Codex CLI, Google Gemini CLI (`agy`), Anthropic Claude Code CLI, and an in-memory `FakeHostAdapter` test double.
4. Canonical SQLite persistence in `banco/squad.db` via table `dispatch_attempts` tracking monotonic attempt counters and ACID state transitions.
5. Minting of canonical `DispatchReceipt` artifacts cryptographically bound to `instruction_hash` and `evidence_hash`.
6. Outbox domain event emission (`SpecialistDispatchedEvent`, `SpecialistDispatchFailedEvent`) into the R2 EventStore.
7. Absolute preservation of the 6 non-negotiable boundary invariants.

---

### SECTION B: Segregation of Duties & Specialist Engagement

All activities in Milestone R11 complied strictly with Agent Squad Segregation of Duties (SoD):
- `27-platform-engineer`: Conducted read-only platform mapping of existing host adapters and dispatch mechanisms (`R11_CURRENT_HOST_DISPATCH_MAP.md`).
- `04-solution-architect`: Formulated the complete 25-section architecture specification (`docs/architecture/R11_HOST_NATIVE_SPECIALIST_DISPATCH.md`).
- `06-software-engineer`: Authored the runtime dispatch package (`scripts/runtime/dispatch/`), implementing ports, adapters, registry, repository, and service.
- `11-test-engineer`: Developed and executed the 6-part test suite (`scripts/tests/test_r11_*.py`), achieving 100% test pass rate across all 20 targeted tests.
- `09-code-reviewer`: Executed this comprehensive audit and code review, verifying spec conformance, absence of side effects, and structural integrity.
- `00-delivery-orchestrator`: Orchestrated transitions without authoring technical deliverables.

---

### SECTION C: R0 Failure Baseline Audit & Resolution Verification

| R0 Baseline Defect | Pre-R11 Failure State | R11 Architectural Resolution | Audit Verification Finding |
| :--- | :--- | :--- | :--- |
| **Area H (Host Boundary)** | Host adapters in `integrations/` were static configuration scripts; zero runtime subagent dispatch existed. | Implemented `HostDispatchPort` protocol and modular adapters in `scripts/runtime/dispatch/adapters/`. | **RESOLVED & VERIFIED**. Native subagent invocation and CLI dispatch isolated in adapters. |
| **R0-LIFE-011** | `ContinuousTriggerEngine` advanced lifecycle state machine while emitting empty `agent_dispatches: []`. | `DispatchService` provides the canonical dispatch seam invoked by orchestration. | **RESOLVED & VERIFIED**. Full dispatch pipeline returns verifiable `DispatchReceipt`. |
| **R0-DEL-004** | Compiled instruction decoupled from dispatch payload and cryptographic hash. | Immutable transport of `compiled_instruction` verified against `instruction_hash` before dispatch. | **RESOLVED & VERIFIED**. `DispatchReceiptIntegrityError` raised if tampering occurs. |
| **R0-DEL-001** | Missing/fallback session tokens in dispatch resolvers. | Enforced session freshness validation via `CanonicalSessionManager`. | **RESOLVED & VERIFIED**. Inactive sessions blocked with `SessionInvalidForDispatchError`. |
| **R0-LIFE-006** | Absence of formal proof of specialist dispatch prior to gate transitions. | Minting and persistence of canonical `DispatchReceipt` in SQLite. | **RESOLVED & VERIFIED**. `DispatchReceipt` contains full cryptographic audit trail. |

---

### SECTION D: Canonical Domain Contract Conformance

The implementation rigorously consumes and respects upstream canonical contracts:
- `DelegationEnvelope` (`scripts/domain/delegation.py#L122`): Verified for presence and `READY_FOR_DISPATCH` state before any adapter interaction.
- `HostCapabilities` (`scripts/domain/delegation.py#L180`): Evaluated dynamically via `adapter.can_dispatch(envelope)` against token context and tool requirements.
- `DispatchReceipt` (`scripts/domain/receipts.py#L55`): Minted via `mint_dispatch_receipt()` using `ReceiptType.DISPATCH.value` ("DISPATCH"), matching `work_item_id`, `agent_id`, `target_agent_id`, `instruction_hash`, and `evidence_hash`.
- `DomainEvent` (`scripts/domain/events.py#L50`): Canonical event creation via `DomainEvent.create()` with causation and correlation bindings.

---

### SECTION E: Boundary Invariants Enforcement (The 6 Invariants)

1. **ZERO Lifecycle Completion:** Dispatching a specialist agent never advances lifecycle stages or marks work items as complete. Verified by `test_zero_lifecycle_completion_invariant` (`stage == 'IMPLEMENTATION'`).
2. **ZERO Execution-Success Assumption:** `DispatchService` mints `DispatchReceipt`, NEVER `ExecutionReceipt`. Proving launch occurred is strictly decoupled from whether code passes tests.
3. **ZERO Review/Test Gate Approval:** Dispatch is strictly an outbound actuation, not a verification signoff. Gates G4, G5, and G6 remain untouched.
4. **ZERO Background Watchdog Loop:** `DispatchService` operates on-demand; zero `while True` or `cron` loops spawned.
5. **ZERO Instruction Mutation:** `DelegationEnvelope.compiled_instruction` is forwarded verbatim to host adapters without prefixing, wrapping, or summarization.
6. **STRICT Control Plane Neutrality:** The core control plane (`scripts/runtime/dispatch/service.py`) contains zero host-specific branching or `if host == ...` conditionals.

---

### SECTION F: Hexagonal Architecture (Ports & Adapters) Conformance

- **Port (`scripts/runtime/dispatch/port.py`):** Defines `HostDispatchPort` with `@runtime_checkable` protocol semantics:
  - `host_kind: str`
  - `get_capabilities() -> HostCapabilities`
  - `can_dispatch(envelope) -> Tuple[bool, Optional[str]]`
  - `dispatch(envelope, binding) -> HostDispatchResult`
  - `check_status(host_execution_id) -> HostStatusResult`
  - `cancel(host_execution_id) -> bool`
- **Application Core:** `DispatchService` interacts solely with `HostDispatchPort`. Adapters implement the port and translate contracts into host-specific payloads.

---

### SECTION G: Canonical Data Contracts & Receipts Evaluation

- `DispatchStatus` Enum: Standardized states (`PENDING`, `DISPATCHED`, `ACKNOWLEDGED`, `FAILED_RETRYABLE`, `FAILED_TERMINAL`, `UNSUPPORTED`, `CANCELLED`).
- `HostExecutionBinding`: Immutable execution context holding host kind, session ID, execution handle, and capability snapshot.
- `HostDispatchResult`: Normalized adapter output holding `status`, `host_execution_id`, `evidence_payload`, and standardized error codes.
- `compute_evidence_hash`: Deterministic SHA-256 computation over sorted canonical JSON evidence payload.
- `mint_dispatch_receipt`: Factory generating immutable `DispatchReceipt` artifacts.

---

### SECTION H: HostDispatchPort Protocol Verification

Static analysis and runtime protocol checks confirm that all 5 adapters (`AntigravityDispatchAdapter`, `CodexDispatchAdapter`, `GeminiCliDispatchAdapter`, `ClaudeDispatchAdapter`, `FakeHostAdapter`) fully satisfy `isinstance(adapter, HostDispatchPort)`.

---

### SECTION I: HostRegistry Discovery & Dynamic Resolution Analysis

`HostRegistry` (`scripts/runtime/dispatch/host_registry.py`) implements deterministic resolution precedence:
1. `SQUAD_HOST_ADAPTER` environment variable override.
2. Session-bound host declared in MCP session metadata.
3. Environment heuristics / probing:
   - Antigravity IDE detection (`ANTIGRAVITY_AGENT_ID`, `GEMINI_CLI_MODE`, etc.).
   - Codex CLI binary probe (`CODEX_CLI_PATH` or `which codex`).
   - Gemini CLI binary probe (`agy` / `gemini`).
   - Claude Code CLI probe (`CLAUDE_CODE_ENTRYPOINT` / `which claude`).
4. Test environment fallback (`SQUAD_ENV == 'test'` or `pytest` in `sys.modules`) -> `fake`.
5. Fail-closed: Raises `HostResolutionError` if no host matches.

---

### SECTION J: Concrete Host Adapters Implementation Audit

1. **Antigravity Adapter (`scripts/runtime/dispatch/adapters/antigravity.py`):** Translates envelope into `invoke_subagent` tool structure (`Subagents` with `TypeName="self"`, `Role=envelope.target_role`, `Prompt=envelope.compiled_instruction`). Max token context: 2,000,000.
2. **Codex Adapter (`scripts/runtime/dispatch/adapters/codex.py`):** Headless CLI execution mode with PID tracking. Max token context: 128,000.
3. **Gemini CLI Adapter (`scripts/runtime/dispatch/adapters/gemini_cli.py`):** Headless runner binding for `agy`. Max token context: 1,000,000.
4. **Claude Adapter (`scripts/runtime/dispatch/adapters/claude.py`):** CLI execution runner. Max token context: 200,000.
5. **Fake Host Adapter (`scripts/runtime/dispatch/adapters/fake.py`):** Deterministic in-memory double supporting configurable fault injection, status probing, and cancellations.

---

### SECTION K: DispatchService Workflow & Lifecycle Verification

`DispatchService.dispatch_delegation()` implements an atomic, audited 12-step sequence:
1. Retrieves envelope; enforces `READY_FOR_DISPATCH` state.
2. Cryptographically verifies `sha256(compiled_instruction) == instruction_hash`.
3. Asserts active MCP session status via `session_manager`.
4. Enforces idempotency: returns existing `DispatchReceipt` if already dispatched.
5. Enforces retry budget (max 3 retries).
6. Negotiates capabilities via `adapter.can_dispatch()`.
7. Records `PENDING` attempt in SQLite with monotonic `attempt_number`.
8. Dispatches through adapter under error containment.
9. Mints `DispatchReceipt` and transitions SQLite attempt to `DISPATCHED`.
10. Updates `DelegationEnvelope` status to `DISPATCHED` in `DelegationRepository`.
11. Emits `SpecialistDispatchedEvent` to R2 Outbox.
12. Returns canonical `DispatchReceipt`.

---

### SECTION L: Durable SQLite Persistence Model (`dispatch_attempts`)

Managed by `DispatchRepository` in `banco/squad.db`:
- Table: `dispatch_attempts` (ACID WAL mode).
- Primary Key: `dispatch_id` (`DSP-<hex12>`).
- Indexed by `delegation_id`, `work_item_id`, `session_id`, `status`, `instruction_hash`.
- Full serialized receipt payload preserved in `receipt_payload`.

---

### SECTION M: Idempotency & Concurrency Verification

- **Duplicate Calls:** `test_idempotent_duplicate_dispatch` verified that re-dispatching an already dispatched envelope returns the exact same receipt without invoking the host adapter a second time.
- **In-Flight Collisions:** Concurrent calls with an active `PENDING` attempt fail safely with `DispatchIdempotencyConflictError`.
- **Retry Monotonicity:** Attempt numbers increment sequentially (`1, 2, 3`) and terminate at `_MAX_DISPATCH_RETRIES = 3` with `DispatchTerminalError(ERR_MAX_RETRIES_EXCEEDED)`.

---

### SECTION N: Failure Modes, Error Taxonomy & Resilience Assessment

Exhaustive typed hierarchy under `DispatchError`:
- `HostResolutionError` (`ERR_HOST_UNRESOLVED`)
- `UnsupportedHostError` (`ERR_HOST_UNRESOLVED`)
- `HostCapabilityMismatchError` (`ERR_CAPABILITY_MISMATCH`)
- `SessionInvalidForDispatchError` (`ERR_SESSION_INVALID`)
- `DelegationEnvelopeNotReadyError` (`ERR_ENVELOPE_NOT_READY`)
- `DispatchExecutionFailedError` (`ERR_HOST_INVOCATION_FAILED`)
- `DispatchRetryableError` (`ERR_HOST_SPAWN_TIMEOUT`)
- `DispatchTerminalError` (`ERR_HOST_INVOCATION_FAILED`)
- `DispatchIdempotencyConflictError` (`ERR_CONCURRENT_DISPATCH_CONFLICT`)
- `DispatchReceiptIntegrityError` (`ERR_INSTRUCTION_HASH_MISMATCH`)

All failure modes record detailed error metadata in `dispatch_attempts` and emit `SpecialistDispatchFailedEvent` where applicable.

---

### SECTION O: R2 Outbox & Event Store Integration

- Successful dispatch emits `agent_squad.specialist.dispatched` containing `dispatch_id`, `delegation_id`, `work_item_id`, `target_role`, `host_kind`, `host_execution_id`, `receipt_id`, and `instruction_hash`.
- Failed dispatch emits `agent_squad.specialist.dispatch_failed` containing failure codes, retryable status, and error diagnostics.
- Event store failures are safely contained without interrupting core dispatch execution.

---

### SECTION P: Security, Isolation & Boundary Containment (STRIDE Audit)

1. **Spoofing:** All dispatch attempts bound to verified `session_id` and authoritative `DelegationEnvelope`.
2. **Tampering:** `DelegationEnvelope.compiled_instruction` is verified against `instruction_hash` on every dispatch.
3. **Repudiation:** Monotonic `dispatch_attempts` and canonical `DispatchReceipt` in SQLite WAL provide undeniable auditability.
4. **Information Disclosure:** Plaintext credentials and secrets sanitized; metadata restricted to execution handles and hashes.
5. **Denial of Service:** Maximum retry limits (3) prevent runaway invocation loops; timeout bounds enforced.
6. **Elevation of Privilege:** Segregation of duties enforced; specialist personas cannot sign off on their own work.

---

### SECTION Q: Test Suite Topology, Coverage & Diagnostics Audit

- **Targeted R11 Test Suite:** 20 tests across 6 files, 100% PASSING:
  - `scripts/tests/test_r11_dispatch_service.py` (2 tests) — PASS
  - `scripts/tests/test_r11_dispatch_idempotency.py` (3 tests) — PASS
  - `scripts/tests/test_r11_host_adapters.py` (5 tests) — PASS
  - `scripts/tests/test_r11_dispatch_receipts.py` (3 tests) — PASS
  - `scripts/tests/test_r11_dispatch_authority.py` (3 tests) — PASS
  - `scripts/tests/test_r11_dispatch_failures.py` (4 tests) — PASS
- **Core Delegation Diagnostic:** `scripts/tests/diagnostics/r0_delegation_contract_red.py` — 7/7 PASS (GREEN).
- **Milestone Regression:** All R1 through R10 suites verified and passing.
- **Full Suite Regression:** 1741 tests passed (0 failed, 6 skipped).

---

### SECTION R: Static Analysis, Code Health & Lint Compliance

- `python scripts/agent_squad.py audit` -> `AUDIT_OK` (exit code 0).
- `python scripts/validate_structure.py` -> `VALID structure agents=41 active_skills=170 schemas=18`.
- `git diff --check` -> Clean (no trailing whitespace or whitespace errors).

---

### SECTION S: Migration & Legacy Harmonization Assessment

- Existing legacy dispatch (`FileSDDDispatcher` in `scripts/sdd_dispatch.py`) flagged as deprecated.
- Clean separation between static MCP installer scripts in `integrations/` and dynamic runtime adapters in `scripts/runtime/dispatch/adapters/`.
- Zero project-local `./work` directories introduced; all state strictly contained within `%SQUAD_RUNTIME%\work\<project_id>\` and `banco/squad.db`.

---

### SECTION T: Final Verdict, Signoff & Readiness Attestation

As Lead Code Reviewer (`09-code-reviewer`), I formally certify that Milestone R11 (HOST-NATIVE SPECIALIST DISPATCH) has achieved complete architectural, contract, and test compliance.

**VERDICT:** **APPROVED / READY FOR HARMONIZATION**  
**RECOMMENDATION:** Milestone R11 is complete. Delivery Orchestrator (`00`) is instructed to execute the Section 57 Hard Stop. **DO NOT START R12.**
