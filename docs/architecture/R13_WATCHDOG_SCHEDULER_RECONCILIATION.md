# R13 — WATCHDOG + SCHEDULER + RECONCILIATION SPECIFICATION

**Author:** `04-solution-architect` (Martin Fowler & Gregor Hohpe)  
**Contributors:** `10-security-reviewer`, `27-platform-engineer`, `14-governance-auditor`  
**Status:** Approved Architecture Specification  
**Milestone:** R13 Stage B Architecture  
**Date:** 2026-09-18  

---

## 1. Executive Summary & Scope

Milestone R13 establishes the unified, deterministic operational control loop for Agent Squad. It provides the capabilities of scheduling, operational recovery, watchdog monitoring, drift detection, and state reconciliation across existing canonical subsystems.

### The Inviolable Operational Principle:
> **R13 DETECTS AND REQUESTS. R13 DOES NOT ADJUDICATE OR MUTATE BUSINESS STATE.**

R13 acts strictly as an operational observer, timekeeper, and coordinator. It routes detected conditions to the owning canonical authorities:
- It **does not** advance lifecycle stages (R4 `CanonicalLifecycleService` is the sole lifecycle authority).
- It **does not** assign specialists (R8 `RoutingEngine` is the sole routing authority).
- It **does not** call host adapters directly or bypass envelopes (R11 `DispatchService` is the sole dispatch authority).
- It **does not** fabricate execution receipts or validation evidence (R12 `ExecutionReceiptService` is the sole receipt authority).
- It **does not** execute direct REST/PATCH calls to Azure Boards (R6 `DeliverySyncService` is the sole sync/writer authority).
- It **does not** require an active background daemon (fully deterministic via `tick(now)` and `run_once(now)`).

---

## 2. R0 Defects Owned by R13

R13 formally owns and resolves the following baseline operational defects identified in `docs/audits/R0_CORE_WORKFLOW_FAILURE_BASELINE.md`:
1. **R0-SCHED-001 (Ad-hoc Schedulers):** Elimination of fragmented, in-memory, or script-local loops; replaced by a durable SQLite-backed `SchedulerService`.
2. **R0-SCHED-002 (Unsafe Concurrency & Race Conditions):** Introduction of atomic multi-process durable leases (`OperationalLease`) in SQLite to guarantee mutual exclusion across worker nodes.
3. **R0-REC-001 (Silent Outbox Stalling):** Active recovery and scheduled draining of orphaned or retried transactional outbox deliveries in `SqliteEventStore` and `DeliverySyncService`.
4. **R0-WD-001 (Unmonitored Execution Timeouts):** Active watchdog detection of timed-out or hung host-native dispatch attempts without silent dropping or premature termination.
5. **R0-WD-002 (Passive Timebox / Handoff Deadlocks):** Conversion of passive timebox and handoff acknowledgment constraints into active watchdog events (`timebox_exceeded`, `handoff_ack_overdue`) alerting orchestrators deterministically.

---

## 3. Boundary & Non-Authority Matrix

To preserve strict separation of concerns and architectural integrity, R13 is bounded by the following prohibitions:

| Dimension | R13 Role (Observer / Coordinator) | Prohibited Action in R13 | Sole Owning Authority |
|---|---|---|---|
| **Lifecycle Transitions** | Detects timebox exceeded or stale handoff; emits domain event | Calling `engine.advance_stage()` or updating `status.yaml` directly | R4 (`CanonicalLifecycleService`) |
| **Specialist Assignment** | Detects unassigned work item; requests assignment | Calculating agent routing, scoring, or picking personas | R8 (`RoutingEngine`) |
| **Host Dispatch** | Detects retried dispatch; requests redispatch | Calling host CLI, MCP adapters, or subagents directly | R11 (`DispatchService`) |
| **Execution Receipts** | Detects completed host process; requests evidence ingestion | Writing to `execution_receipts` or `validation_receipts` | R12 (`ExecutionReceiptService`) |
| **Azure Boards Sync** | Schedules outbox drain; enqueues reconciliation job | Invoking Azure REST API, updating remote revisions directly | R6 (`DeliverySyncService`) |
| **Decision Logic** | Purely deterministic state-machine & mathematical retry policies | Using LLMs or non-deterministic heuristics to decide retries | Deterministic Policy Engine |

---

## 4. Architectural Topology & Target Flow

```text
               +-----------------------------------------+
               |         OperationsControlService        |
               |       (tick(now) / run_once(now))       |
               +--------------------+--------------------+
                                    |
          +-------------------------+-------------------------+
          |                         |                         |
          v                         v                         v
+-------------------+     +-------------------+     +-------------------+
|  SchedulerService |     |  WatchdogService  |     |  ReconcilerService|
|  - schedule_job   |     |  - scan_stale     |     |  - process_job    |
|  - claim_due_jobs |     |  - check_timebox  |     |  - route_action   |
|  - release_job    |     |  - check_handoff  |     |  - emit_events    |
+---------+---------+     +---------+---------+     +---------+---------+
          |                         |                         |
          +-------------------------+-------------------------+
                                    |
                                    v
                     +-----------------------------+
                     |    OperationalRepository    |
                     |  (banco/squad.db - SQLite)  |
                     |  - scheduled_jobs           |
                     |  - operational_leases       |
                     |  - watchdog_checkpoints     |
                     +--------------+--------------+
                                    |
                 +------------------+------------------+
                 |                  |                  |
                 v                  v                  v
         +---------------+  +---------------+  +---------------+
         | R6 Sync Engine|  | R11 Dispatch  |  | R12 Execution |
         | (drain_outbox)|  | (check_status)|  | (receipt_ing) |
         +---------------+  +---------------+  +---------------+
```

---

## 5. Domain Models & Contracts

Located in `scripts/runtime/operations/models.py`:

```python
class JobKind(str, Enum):
    OUTBOX_DRAIN = "OUTBOX_DRAIN"
    DISPATCH_RECONCILE = "DISPATCH_RECONCILE"
    EXECUTION_RECONCILE = "EXECUTION_RECONCILE"
    AZURE_RECONCILE = "AZURE_RECONCILE"
    LIFECYCLE_STALE_CHECK = "LIFECYCLE_STALE_CHECK"
    HANDOFF_ACK_TIMEOUT = "HANDOFF_ACK_TIMEOUT"
    SESSION_EXPIRY_CHECK = "SESSION_EXPIRY_CHECK"

class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    CANCELLED = "CANCELLED"

@dataclass(frozen=True)
class ScheduledJob:
    job_id: str
    kind: JobKind
    entity_type: str
    entity_id: str
    due_at: datetime
    attempt: int
    max_attempts: int
    status: JobStatus
    payload: Dict[str, Any]
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    last_error: Optional[str] = None
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass(frozen=True)
class OperationalLease:
    lease_id: str
    resource_key: str
    owner: str
    expires_at: datetime
    acquired_at: datetime
```

---

## 6. Deterministic Clock & Time Abstraction

To ensure testability with zero `time.sleep()`, time must be fully injectable via `ClockPort` in `scripts/runtime/operations/clock.py`:
- `SystemClock`: Returns current system UTC datetime (`datetime.now(timezone.utc)`).
- `DeterministicClock`: In-memory controllable clock with explicit `set_time(dt)` and `advance(timedelta)`.
- All operations components (`SchedulerService`, `WatchdogService`, `OperationsControlService`) must accept an optional `ClockPort`.

---

## 7. Durable Storage Schema (`banco/squad.db`)

All tables are created in SQLite WAL mode with full parameterization:

### 7.1 `scheduled_jobs`
```sql
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    due_at TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    payload TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_due_status ON scheduled_jobs(status, due_at);
CREATE INDEX IF NOT EXISTS idx_jobs_entity ON scheduled_jobs(entity_type, entity_id);
```

### 7.2 `operational_leases`
```sql
CREATE TABLE IF NOT EXISTS operational_leases (
    resource_key TEXT PRIMARY KEY,
    lease_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    acquired_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leases_expiry ON operational_leases(expires_at);
```

### 7.3 `watchdog_checkpoints`
```sql
CREATE TABLE IF NOT EXISTS watchdog_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    component TEXT NOT NULL,
    cursor_value TEXT,
    updated_at TEXT NOT NULL
);
```

---

## 8. Multi-Process Concurrency & Atomic Lease Acquisition

To guarantee that multiple processes or worker threads never process the same job or acquire the same resource concurrently:
- Atomic SQL claim query:
  ```sql
  UPDATE scheduled_jobs
  SET lease_owner = ?, lease_expires_at = ?, status = 'RUNNING', updated_at = ?
  WHERE job_id = ?
    AND (lease_expires_at IS NULL OR lease_expires_at < ?)
    AND status IN ('PENDING', 'FAILED_RETRYABLE');
  ```
- If `rowcount == 1`, the lease was acquired exclusively. If `0`, another worker claimed it or it is no longer due.
- Similarly, for `operational_leases`:
  ```sql
  INSERT INTO operational_leases (resource_key, lease_id, owner, expires_at, acquired_at)
  VALUES (?, ?, ?, ?, ?)
  ON CONFLICT(resource_key) DO UPDATE SET
    lease_id = excluded.lease_id,
    owner = excluded.owner,
    expires_at = excluded.expires_at,
    acquired_at = excluded.acquired_at
  WHERE operational_leases.expires_at < ?;
  ```

---

## 9. Mathematical Backoff & Retry Policies

Located in `scripts/runtime/operations/retry.py`:
- Purely deterministic calculation:
  $$\Delta t = \min(t_{\text{cap}}, t_{\text{base}} \times 2^{\text{attempt}})$$
- Jitter support: Configurable deterministic pseudo-random or hash-seeded jitter to prevent thundering herds while remaining repeatable in test fixtures.
- State transitions:
  - If $\text{attempt} < \text{max\_attempts}$: transition to `FAILED_RETRYABLE`, calculate `due_at = now + \Delta t`.
  - If $\text{attempt} \ge \text{max\_attempts}$: transition to `FAILED_TERMINAL` (Dead-letter), emit `agent_squad.operations.dead_letter` event.

---

## 10. Watchdog Scanners & Active Condition Observation

Located in `scripts/runtime/operations/watchdog.py`:

### 10.1 Stale Dispatch Scanner
- Queries `dispatch_attempts` where `status = 'IN_FLIGHT'` and elapsed time exceeds `dispatch_timeout_seconds` (default: 1800s).
- Schedules a `DISPATCH_RECONCILE` job to query host adapter status via `DispatchService.check_dispatch_status`.

### 10.2 Lifecycle Timebox Scanner
- Queries active work items in `work/`.
- Compares stage duration against configured `phase_timeboxes` in `workflow.yaml`.
- If exceeded, emits `agent_squad.lifecycle.timebox_exceeded` event with work item ID, stage, and elapsed seconds. Does **not** force stage transition.

### 10.3 Handoff Acknowledgment Scanner
- Inspects pending handoffs where `acknowledgement.status == 'pending'`.
- If age exceeds `handoff_ack_timeout_seconds` (default: 3600s), emits `agent_squad.lifecycle.handoff_ack_overdue` event. Does **not** auto-acknowledge.

### 10.4 Outbox Health Scanner
- Scans `event_deliveries` and `delivery_sync_outbox` for records in `PENDING` or `FAILED` state older than threshold.
- Schedules `OUTBOX_DRAIN` and `AZURE_RECONCILE` jobs.

---

## 11. Reconciliation Execution & Delegation to Authorities

Located in `scripts/runtime/operations/reconciliation.py`:

- **Job `OUTBOX_DRAIN`:**
  Invokes `SqliteEventStore.claim_next_delivery` and processes subscriber delivery, or calls `DeliverySyncService.drain_outbox`.
- **Job `DISPATCH_RECONCILE`:**
  Calls `DispatchService.check_dispatch_status(dispatch_id)`. If terminal failed, records failure event. If completed, triggers R12 receipt check.
- **Job `EXECUTION_RECONCILE`:**
  Checks for evidence on disk (`receipts/`, `evidence/`) and calls `ExecutionReceiptService.record_execution_receipt`.
- **Job `AZURE_RECONCILE`:**
  Calls `DeliverySyncService.drain_outbox()` and evaluates pending sync outbox items.

---

## 12. Unification Service: `OperationsControlService`

Located in `scripts/runtime/operations/service.py`:
- Exposes single point of execution: `run_once(now: Optional[datetime] = None) -> OperationsRunReport`.
- Orchestrates:
  1. Watchdog scan (identifies stale conditions and schedules jobs).
  2. Scheduler tick (claims all due jobs up to batch limit).
  3. Reconciler execution (processes claimed jobs through canonical authorities).
  4. Cleanup / expired lease release.
- Provides a comprehensive, structured return object (`OperationsRunReport`) recording all jobs claimed, executed, failed, and events emitted.

---

## 13. Threat Modeling & Security Review (Jim Manico & AppSec)

1. **Credential Safety:** Scheduled job payloads MUST NEVER store plain secrets, tokens, or PATs (`SEC-R13-01`). Only reference durable entity IDs or secure vaults.
2. **Replay & Amplification Attacks:** Atomic lease claims and monotonic attempt increments prevent duplicate execution storms.
3. **Denial of Service / Loop Exhaustion:** Strict `max_attempts` caps and exponential backoff prevent infinite retry cycles.
4. **Attestation:** Signed off with `R13_SECURITY_REVIEW = PASS`.

---

## 14. Governance & Audit Traceability (ISO 27001 / SOC 2)

1. Every dead-letter event is permanently appended to `events` table with causation and correlation IDs.
2. Every lease claim, job status transition, and reconciliation attempt updates audit timestamps in SQLite.
3. No silent drops: Any unrecoverable failure produces an explicit `FAILED_TERMINAL` state and alert event.
4. Signed off with `R13_GOVERNANCE_AUDIT = PASS`.
