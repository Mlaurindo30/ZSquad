# R12 — EXECUTION RECEIPTS + REVIEW / TEST / QA ENFORCEMENT SPECIFICATION

**Author:** `04-solution-architect` (Martin Fowler & Gregor Hohpe)  
**Status:** Approved  
**Milestone:** R12 Stage B Architecture  
**Date:** 2026-09-18  

---

## 1. Executive Summary & Scope

Milestone R12 establishes the canonical execution verification and gate eligibility layer for the Agent Squad runtime. It transforms raw specialist execution outputs into cryptographically verified execution receipts, and enforces strict Segregation of Duties (SoD) across implementation, peer review, security scanning, testing, and QA validation before any R4 lifecycle state transition or gate passage is permitted.

### Core Non-Negotiable Invariants:
1. **`DISPATCHED != EXECUTED`**:
   A `DispatchReceipt` (produced by R11 host dispatch) only proves that a specialist was invoked. It does not prove that execution took place or produced valid output.
2. **`EXECUTED != VERIFIED`**:
   An `ExecutionReceipt` (produced by builder/implementer, e.g. `06-software-engineer`) proves code was written or modified, diffs produced, and tests run. It does NOT constitute independent verification.
3. **`VERIFIED != APPROVED`**:
   A `TestReceipt` (produced by `11-test-engineer`) proves test execution and metrics. Independent signoff from `09-code-reviewer` (`ReviewReceipt`) and `10-security-reviewer` (`SecurityReceipt`) is mandatory.
4. **`APPROVED != LIFECYCLE ADVANCED`**:
   Approval receipts make a work item eligible for gate evaluation and stage transition, but the R4 canonical lifecycle engine (`CanonicalLifecycleService`) remains the sole transition authority.
5. **Strict Segregation of Duties (SoD)**:
   The implementing agent cannot review, approve, test, or sign off on their own work (`execution_receipt.agent_id != validator_receipt.agent_id`).
6. **ZERO Blind Success / Text Claims**:
   Natural language assertions ("all tests pass", "looks good to me") are strictly rejected. Concrete structured receipts with hashes, exit codes, file lists, and findings are enforced.
7. **ZERO Scheduler / Watchdog**:
   Verification is purely synchronous and deterministic upon request.

---

## 2. Domain Models & Contracts

R12 directly consumes the canonical stdlib domain models from `scripts/domain/receipts.py`:

- **`BaseReceipt`**:
  `receipt_id: str`, `receipt_type: str`, `work_item_id: str`, `agent_id: str`, `instruction_hash: str`, `evidence_hash: str`, `created_at: datetime`.
- **`ExecutionReceipt`** (`ReceiptType.EXECUTION`):
  `files_modified: List[str]`, `tests_executed: List[str]`, `test_exit_code: int`, `diff_summary: str`.
- **`ReviewReceipt`** (`ReceiptType.REVIEW`):
  `reviewer_role: str`, `verdict: str` (`"APPROVED" | "CHANGES_REQUESTED"`), `comments: List[str]`, `reviewed_files: List[str]`.
- **`SecurityReceipt`** (`ReceiptType.SECURITY`):
  `security_role: str`, `vulnerabilities_detected: int`, `critical_count: int`, `sast_tool_output: str`, `verdict: str` (`"APPROVED" | "REJECTED"`).
- **`TestReceipt`** (`ReceiptType.TEST`):
  `tester_role: str`, `total_tests: int`, `passed_tests: int`, `failed_tests: int`, `coverage_percentage: float`.
- **`QAReceipt`** (`ReceiptType.QA`):
  `qa_role: str`, `scenarios_verified: int`, `bdd_exit_code: int`, `verdict: str` (`"APPROVED" | "REJECTED"`).
- **`GovernanceReceipt`** (`ReceiptType.GOVERNANCE`):
  `auditor_role: str`, `gate_approvals: List[str]`, `ledger_entry_id: str`, `compliance_verdict: str`.

---

## 3. Cryptographic Evidence Hashing & Verification

All evidence submitted by specialists must be hashed deterministically using SHA-256:
- Canonical JSON serialization of structured dictionaries (`canonical_json`).
- Normalized whitespace and line endings for diffs (`diff.strip().replace("\r\n", "\n")`).
- `evidence_hash = sha256(canonical_payload).hexdigest()`.
- Tamper-proofing: Modifying any field in an ingested receipt or tampering with evidence files causes validation failure.

---

## 4. SQLite Persistence Schema

Two dedicated tables in `banco/squad.db` (WAL mode) provide durable storage for receipts:

```sql
CREATE TABLE IF NOT EXISTS execution_receipts (
    receipt_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    receipt_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    instruction_hash TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    files_modified TEXT NOT NULL,       -- JSON list
    tests_executed TEXT NOT NULL,       -- JSON list
    test_exit_code INTEGER NOT NULL,
    diff_summary TEXT NOT NULL,
    raw_payload TEXT NOT NULL,          -- Full canonical JSON
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_exec_receipts_work_item 
ON execution_receipts(work_item_id, stage);

CREATE TABLE IF NOT EXISTS validation_receipts (
    receipt_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    receipt_type TEXT NOT NULL,         -- REVIEW, SECURITY, TEST, QA, GOVERNANCE
    stage TEXT NOT NULL,
    instruction_hash TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    verdict TEXT NOT NULL,
    details TEXT NOT NULL,              -- Specific fields (findings, tests, etc.)
    raw_payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_val_receipts_work_item 
ON validation_receipts(work_item_id, stage, receipt_type);
```

---

## 5. Segregation of Duties (SoD) Verification

The runtime enforces segregation between the author of an implementation and subsequent validators:
1. **Rule**: For a given `work_item_id` and implementation cycle, an agent cannot review, security scan, test, or QA approve their own work.
2. **Implementation**:
   ```python
   def assert_sod(execution_receipt: ExecutionReceipt, validator_receipt: BaseReceipt) -> None:
       if execution_receipt.agent_id == validator_receipt.agent_id:
           raise SoDViolationError(
               f"Author '{execution_receipt.agent_id}' cannot validate their own work in receipt '{validator_receipt.receipt_id}'"
           )
   ```
3. **Role Checks**:
   - Reviewer must have review capability (`09-code-reviewer` or approved architect).
   - Security reviewer must be `10-security-reviewer` or `34-offensive-cyber-operator`.
   - Tester must be `11-test-engineer`.
   - QA must be `12-qa-engineer`.

---

## 6. Stage Completion & Gate Prerequisite Policies

R4 Lifecycle Stages map to required receipts:

| Lifecycle Stage | Required Receipt(s) | Required Agent / Role | Required Verdict / Exit Code | Gate |
|---|---|---|---|---|
| `IMPLEMENTATION` | `ExecutionReceipt` | Implementer (`06`, `07`, `08`, etc.) | `test_exit_code == 0`, diff non-empty | - |
| `CODE_REVIEW` | `ReviewReceipt` | `09-code-reviewer` | `verdict == "APPROVED"`, SoD compliant | - |
| `SECURITY_REVIEW` | `SecurityReceipt` | `10-security-reviewer` | `verdict == "APPROVED"`, `critical_count == 0` | `G4-code-security` |
| `TEST_VALIDATION` | `TestReceipt` | `11-test-engineer` | `failed_tests == 0`, `total_tests > 0` | - |
| `QA_VALIDATION` | `QAReceipt` | `12-qa-engineer` | `verdict == "APPROVED"`, `bdd_exit_code == 0` | `G5-quality` |
| `GOVERNANCE_RELEASE` | `GovernanceReceipt` | `14-governance-auditor` | `compliance_verdict == "COMPLIANT"` | `G6-governance-release` |

---

## 7. Package Layout (`scripts/runtime/execution/`)

```
scripts/runtime/execution/
├── __init__.py               # Exports ExecutionReceiptService, models, errors
├── errors.py                 # Domain errors (ExecutionReceiptError, SoDViolationError, etc.)
├── evidence.py               # Cryptographic hashing and evidence normalization
├── receipts.py               # Receipt builders and validation logic
├── repository.py             # SQLite persistence for execution and validation receipts
├── validation.py             # SoD and criteria verification logic
├── stage_policy.py           # Stage-to-receipt requirements mapping
└── service.py                # Canonical ExecutionReceiptService façade
```

---

## 8. Integration with R4 Lifecycle Engine & Legacy Resolvers

1. **`scripts/runtime/lifecycle/engine.py`**:
   - `_has_execution_proof(item_path, work_item_id)`:
     First queries `ExecutionReceiptService.get_execution_receipt(work_item_id)`. If present and valid, returns True.
     Falls back to filesystem proofs for legacy mock items only if no DB record exists.
   - `_has_review_proof(item_path, work_item_id)`:
     Queries `ExecutionReceiptService.get_validation_receipts(work_item_id, ReceiptType.REVIEW)`.
     Verifies verdict is `APPROVED` and checks SoD against the execution receipt.
2. **`integrations/resolvers/execution_recorder.py`**:
   - `record_execution`: Delegates to `ExecutionReceiptService.record_execution`.
   - `record_evidence`: Delegates to `ExecutionReceiptService.record_evidence`.
   - `report_failure`: Records structured failure receipt and sets blocked status.
3. **`integrations/resolvers/gate_evaluator.py`**:
   - `evaluate_gate`: Queries `ExecutionReceiptService.verify_gate_eligibility(work_item_id, gate_id)`.

---

## 9. Failure Modes & Fail-Closed Guarantees

- **Missing Evidence**: Attempting to record an execution without valid diff or test results raises `InvalidEvidenceError`.
- **SoD Violation**: Attempting to record a review where `reviewer.agent_id == author.agent_id` raises `SoDViolationError`.
- **Failing Tests**: `ExecutionReceipt` with non-zero test exit code or `TestReceipt` with `failed_tests > 0` cannot satisfy stage exit.
- **Critical Vulnerabilities**: `SecurityReceipt` with `critical_count > 0` cannot be approved.
- **Tampering**: If evidence on disk does not match `evidence_hash`, verification fails closed.
