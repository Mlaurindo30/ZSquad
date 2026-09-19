# R12 — CURRENT EXECUTION & EVIDENCE RUNTIME MAP
**Author:** `27-platform-engineer` (Kelsey Hightower & Team Topologies)
**Date:** 2026-09-18
**Milestone:** R12 Stage A Baseline

---

## 1. Executive Summary & Context

Milestone R12 establishes canonical execution receipts, evidence validation, and strict segregation of duties (SoD) across implementation, review, testing, and QA before R4 lifecycle stage transitions and gate progression.
This audit examines the current state of execution recording, gate evaluation, and lifecycle verification in the Agent Squad codebase.

---

## 2. Current Callers & Execution Mechanics

### 2.1 Facades in `integrations/resolvers/execution_recorder.py`
- `record_execution(args, ctx, session_store, db)`:
  - Validates `session_id` via `session_store`.
  - Serializes `args["receipt"]` directly to JSON.
  - Calls `db.record_fact(..., "execution_receipt", ...)` into legacy `memory_facts`.
  - **Flaw:** Does not instantiate or validate canonical `ExecutionReceipt` from `scripts/domain/receipts.py`. Does not check instruction hash, evidence hash, test exit codes, or files modified.
- `record_evidence(args, ctx, session_store, db)`:
  - Calls `db.record_fact(..., "evidence", "evidence recorded", "record_evidence")`.
  - **Flaw:** Does not perform cryptographic hashing of raw evidence or diffs.
- `report_failure(args, ctx, session_store, db)`:
  - Marks session blocked in `session_store` and records fact in `memory_facts`.
- `replay_receipt(args, ctx, session_store)`:
  - Returns `{"status": "replayed", "receipt_hash": args["receipt_hash"]}` without verification.

### 2.2 Gate Evaluation in `integrations/resolvers/gate_evaluator.py`
- `evaluate_gate(args, ctx, session_store, db)`:
  - Checks if `len(evidence_refs) > 0`.
  - **Flaw:** Gate eligibility is declared purely based on whether evidence references list is non-empty, without verifying receipt type, SoD compliance, or stage eligibility.

### 2.3 Provisional Lifecycle Proofs in `scripts/runtime/lifecycle/engine.py`
- `_has_execution_proof(item_path)`:
  - Scans `receipts/` for filenames containing `"execution"` or `"dispatch"`.
  - **Major Flaw:** Treats `dispatch` as execution (`DISPATCHED != EXECUTED` violation).
  - Checks for presence of `implementation.diff` or evaluation JSON files without validating actual receipt schema or author.
- `_has_review_proof(item_path)`:
  - Scans `receipts/` for `"review"` in filename or presence of `review-verdict.md`.
  - **Major Flaw:** Does not check reviewer identity or SoD (`assert_sod_compliance`).

---

## 3. Existing Canonical Contracts in `scripts/domain/receipts.py`

Canonical domain classes already exist in `scripts/domain/receipts.py`:
- `BaseReceipt`: `receipt_id`, `receipt_type`, `work_item_id`, `agent_id`, `instruction_hash`, `evidence_hash`, `created_at`.
- `DispatchReceipt`: Extends `BaseReceipt` with `target_agent_id`, `delegation_id`, `dispatched_at`.
- `ExecutionReceipt`: Extends `BaseReceipt` with `files_modified`, `tests_executed`, `test_exit_code`, `diff_summary`.
- `ReviewReceipt`: Extends `BaseReceipt` with `reviewer_role`, `verdict` (`APPROVED` | `CHANGES_REQUESTED`), `comments`, `reviewed_files`.
- `SecurityReceipt`: Extends `BaseReceipt` with `security_role`, `vulnerabilities_detected`, `critical_count`, `sast_tool_output`, `verdict` (`APPROVED` | `REJECTED`).
- `TestReceipt`: Extends `BaseReceipt` with `tester_role`, `total_tests`, `passed_tests`, `failed_tests`, `coverage_percentage`.
- `QAReceipt`: Extends `BaseReceipt` with `qa_role`, `scenarios_verified`, `bdd_exit_code`, `verdict` (`APPROVED` | `REJECTED`).
- `GovernanceReceipt`: Extends `BaseReceipt` with `auditor_role`, `gate_approvals`, `ledger_entry_id`, `compliance_verdict`.
- `assert_sod_compliance(execution_receipt, validator_receipt)`: Enforces that `execution_receipt.agent_id != validator_receipt.agent_id`.

---

## 4. Database Schema Analysis (`banco/squad.db`)

Current tables:
- `work_item_lifecycle_state`, `lifecycle_history`, `events`, `event_deliveries`, `project_bindings`, `project_binding_history`, `delivery_work_item_bindings`, `delivery_sync_outbox`, `delivery_inbound_events`, `mcp_sessions`.

Missing tables:
- `execution_receipts` / `validation_receipts`: Currently stored as unstructured blobs in `memory_facts` or ad-hoc JSON on disk.
- Canonical storage table needed in SQLite WAL mode for fast, relational, durable queries of receipts by `work_item_id`, `stage`, `agent_id`, and `receipt_type`.

---

## 5. Invariants and Architectural Boundaries for R12

1. **`DISPATCHED != EXECUTED`**:
   `DispatchReceipt` proves only that host adapter dispatched a task. An `ExecutionReceipt` requires actual execution evidence (diffs, exit codes, artifacts, execution logs).
2. **`EXECUTED != VERIFIED`**:
   `ExecutionReceipt` emitted by implementer (`06-software-engineer` or other builder) proves implementation occurred, but NOT that tests passed or requirements were validated.
3. **`VERIFIED != APPROVED`**:
   `TestReceipt` (`11-test-engineer`) proves tests were executed. `ReviewReceipt` (`09-code-reviewer`) and `SecurityReceipt` (`10-security-reviewer`) are independently required for signoff.
4. **`APPROVED != LIFECYCLE ADVANCED`**:
   Lifecycle stage transition belongs exclusively to R4 canonical engine (`CanonicalLifecycleService`). Receipts provide prerequisite proof, but R4 transitions the state.
5. **Strict Segregation of Duties (SoD)**:
   - Implementer cannot review own code (`assert_sod_compliance`).
   - Implementer cannot act as QA/Tester for their own implementation.
   - Distinct logical agent identities (`06` != `09`, `06` != `10`, `06` != `11`, `06` != `12`).
6. **ZERO Scheduler / ZERO Watchdog**:
   R12 performs synchronous validation upon request. No background threads, loops, or polling.
7. **ZERO Blind Success / Text Claims**:
   Raw text "all tests pass" is rejected. Structured evidence with hashes, counts, and exit codes is required.

---

## 6. Recommendations for Stage B & C

1. **New Runtime Package**:
   `scripts/runtime/execution/`:
   - `__init__.py`
   - `errors.py`: Domain errors (`ExecutionReceiptError`, `SoDViolationError`, `InvalidEvidenceError`, `GatePrerequisiteError`).
   - `evidence.py`: Deterministic evidence hashing, validation, diff hashing.
   - `repository.py`: SQLite persistence for `execution_receipts` and `validation_receipts` in `banco/squad.db`.
   - `validation.py`: Strict validator enforcing schema, SoD, test exit codes, vulnerability zero-tolerance.
   - `stage_policy.py`: Maps stages to required receipts and roles.
   - `service.py`: Canonical `ExecutionReceiptService` providing ingest, verify, query, and gate-eligibility endpoints.
2. **Upgrade R4 Lifecycle Engine**:
   Refactor `_has_execution_proof` and `_has_review_proof` in `scripts/runtime/lifecycle/engine.py` to leverage `ExecutionReceiptService`, completely eliminating provisional file-stem checks while maintaining backwards compatibility for existing unit tests.
3. **Upgrade Facades**:
   `integrations/resolvers/execution_recorder.py` and `gate_evaluator.py` must delegate directly to `ExecutionReceiptService`.
