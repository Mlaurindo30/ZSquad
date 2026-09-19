# R13 — CURRENT OPERATIONAL CONTROL LOOP MAP
**Author:** `27-platform-engineer` (Kelsey Hightower & Team Topologies)  
**Date:** 2026-09-18  
**Milestone:** R13 Stage A Baseline  
**Status:** READ-ONLY AUDIT COMPLETE  

---

## 1. Executive Summary

Milestone R13 establishes the unified, deterministic operational recovery, scheduling, watchdog, and reconciliation control loop for Agent Squad.
This audit examines all existing operational loops, schedulers, retry managers, outbox handlers, and recovery controllers across the repository.

---

## 2. Inventory of Existing Operational Loops & Handlers

### 2.1 Event Store & Outbox (`scripts/runtime/events/store.py`)
- **Authority:** `SqliteEventStore`
- **Capabilities:**
  - Table `events`: Durable append-only event store.
  - Table `event_deliveries`: Transactional outbox with state machine (`PENDING`, `CLAIMED`, `DELIVERED`, `FAILED_RETRYABLE`, `FAILED_TERMINAL`, `DEAD_LETTER`).
  - Claiming and Leasing: `claim_pending_deliveries(subscriber, limit, lease_seconds)`.
  - Retry & Backoff: Exponential backoff calculation using `next_attempt_at`.
- **Finding:** Canonical and robust. Must be reused by R13 for durable event outbox draining and delivery.

### 2.2 Azure Delivery Sync & Outbox (`scripts/runtime/delivery/sync.py`)
- **Authority:** `DeliverySyncService`
- **Capabilities:**
  - Table `delivery_sync_outbox`: Transactional outbox for outbound Azure Boards mutations.
  - Method `drain_outbox(batch_size, max_retries, base_backoff_seconds)`: Drains pending outbound sync actions synchronously without background daemons.
  - Dead-Letter Handling: Emits `agent_squad.delivery.sync.dead_letter` and marks record `FAILED_TERMINAL` upon retry exhaustion.
- **Finding:** Canonical sync coordinator for R6. R13 must schedule and invoke `drain_outbox` and `reconcile_all` through this service without issuing direct Azure PATCH calls.

### 2.3 Dispatch Execution & Status Checking (`scripts/runtime/dispatch/service.py`)
- **Authority:** `DispatchService`
- **Capabilities:**
  - Table `dispatch_attempts`: Persists dispatch attempts, host kinds, execution IDs, and status.
  - Method `check_dispatch_status(dispatch_id)`: Queries host adapter status safely.
  - Method `cancel_dispatch(dispatch_id)`: Cancels execution via host adapter.
- **Finding:** R13 must invoke `check_dispatch_status` and dispatch retry entrypoints via `DispatchService`, strictly avoiding direct imports or calls to host adapters.

### 2.4 Execution & Validation Receipts (`scripts/runtime/execution/service.py`)
- **Authority:** `ExecutionReceiptService`
- **Capabilities:**
  - Tables `execution_receipts` and `validation_receipts`.
  - Methods `is_stage_satisfied`, `verify_gate_eligibility`, and `verify_governance_chain`.
- **Finding:** Sole authority for execution receipts. R13 detects stalled executions and requests reconciliation from R12, but never fabricates receipts.

### 2.5 Lifecycle Stale & Timebox Monitoring (`scripts/runtime/lifecycle/engine.py`)
- **Authority:** `CanonicalLifecycleService`
- **Capabilities:**
  - Method `_check_timebox_status`: Calculates elapsed time in current stage against configured timebox.
  - Method `_check_handoff_status`: Detects unacknowledged handoffs.
- **Finding:** Purely passive in R4. R13 watchdog observes when timeboxes or handoff ACKs are overdue and emits domain events, leaving state transition authority exclusively to R4.

### 2.6 Error Classification & Provider Recovery (`scripts/orchestration_controller.py`)
- **Component:** `OrchestrationController`
- **Capabilities:**
  - Classifies errors into `FailoverReason`.
  - Determines actions (`retry-same`, `retry-compressed`, `rotate-agent`, `escalate`, `blocked`).
  - Records events in table `ops_recovery`.
- **Classification:** `CANONICAL_FACADE`. Serves as an error classification and provider failover policy facade. R13 can utilize its classification without allowing it to independently mutate lifecycle.

### 2.7 Continuous Trigger Engine (`scripts/continuous_trigger_engine.py`)
- **Component:** `ContinuousTriggerEngine`
- **Capabilities:**
  - Circuit Breaker (`CircuitBreakerState`) with max 2 retries.
  - Reacts to `EVENT_HANDOFF_CREATED`, `EVENT_GATE_EVALUATED`, `EVENT_STATE_ADVANCED`.
- **Classification:** Edge trigger controller. Must be bridged to canonical R2 trigger engine and R13 scheduler.

### 2.8 Legacy SDD Dispatch (`scripts/sdd_dispatch.py`)
- **Component:** `FileSDDDispatcher`
- **Capabilities:**
  - Filesystem-based JSON queue (`requests/`, `claims/`, `receipts/`).
- **Classification:** `DEPRECATED` / `OUTBOX_COMPATIBILITY`. Superseded by canonical R11 `DispatchService`.

---

## 3. Operational Gaps Identified

1. **No Unified Scheduler:**
   Scheduling logic was scattered across ad-hoc loops or CLI entrypoints. A unified, durable `SchedulerService` backed by SQLite in `banco/squad.db` is needed.
2. **No Multi-Process Lease Mechanism for Scheduled Jobs:**
   To prevent double-processing in multi-process or concurrent worker environments, durable leases (`claimed_at`, `claimed_by`, `lease_expiry`) must be enforced at the SQLite level.
3. **Passive vs Active Observation:**
   Timeboxes and handoff ACKs existed as passive transition checks. R13 watchdog is needed to actively observe stale states and emit canonical events.
4. **Deterministic Time Testing:**
   All timers and schedulers must support an injectable clock (`ClockPort` / `now_provider`) to allow unit testing without `time.sleep()`.

---

## 4. Recommendations for Stages B & C

1. **New Package Layout:**
   `scripts/runtime/operations/`:
   - `__init__.py`
   - `errors.py`
   - `clock.py` (Injectable deterministic clock abstraction)
   - `models.py` (ScheduledCheck, JobKind, JobStatus, Lease)
   - `repository.py` (SQLite persistence for scheduled jobs and leases in `banco/squad.db`)
   - `scheduler.py` (Deterministic `tick(now)`, job registration, due queries)
   - `watchdog.py` (Active condition observation for stale dispatches, executions, timeboxes, handoffs)
   - `reconciliation.py` (Reconciliation runner invoking R6, R11, R12)
   - `service.py` (OperationalControlService unifying scheduler, watchdog, and reconciler)
2. **Strict Authority Isolation:**
   - Zero lifecycle transitions in R13.
   - Zero direct host adapter dispatches.
   - Zero direct Azure PATCH writes.
   - Pure observation, scheduling, and delegation to owning canonical subsystems.
