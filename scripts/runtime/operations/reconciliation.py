"""Authoritative Reconciliation Service (Milestone R13).

Strictly stdlib-only.
Executes scheduled operational jobs by delegating strictly to canonical owning authorities:
- OUTBOX_DRAIN -> SqliteEventStore / DeliverySyncService.drain_outbox
- DISPATCH_RECONCILE -> DispatchService.check_dispatch_status
- EXECUTION_RECONCILE -> ExecutionReceiptService
- AZURE_RECONCILE -> DeliverySyncService.drain_outbox

PROHIBITION: ReconciliationService NEVER bypasses R4, R6, R8, R11, or R12 authorities.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Dict, Optional

from scripts.runtime.operations.clock import ClockPort, SystemClock
from scripts.runtime.operations.errors import ReconciliationError
from scripts.runtime.operations.models import (
    JobKind,
    JobStatus,
    ReconciliationResult,
    ScheduledJob,
)

logger = logging.getLogger(__name__)


class ReconciliationService:
    """Dispatches scheduled jobs to the appropriate canonical subsystems."""

    def __init__(
        self,
        event_store: Optional[Any] = None,
        delivery_sync_service: Optional[Any] = None,
        dispatch_service: Optional[Any] = None,
        execution_service: Optional[Any] = None,
        clock: Optional[ClockPort] = None,
    ) -> None:
        self.event_store = event_store
        self.delivery_sync_service = delivery_sync_service
        self.dispatch_service = dispatch_service
        self.execution_service = execution_service
        self.clock = clock or SystemClock()

    def process_job(self, job: ScheduledJob, now: Optional[datetime] = None) -> ReconciliationResult:
        """Processes a single leased job through its owning authority."""
        current_time = now or self.clock.now()

        try:
            if job.kind == JobKind.OUTBOX_DRAIN:
                return self._process_outbox_drain(job)
            elif job.kind == JobKind.AZURE_RECONCILE:
                return self._process_azure_reconcile(job)
            elif job.kind == JobKind.DISPATCH_RECONCILE:
                return self._process_dispatch_reconcile(job)
            elif job.kind == JobKind.EXECUTION_RECONCILE:
                return self._process_execution_reconcile(job)
            elif job.kind in (JobKind.LIFECYCLE_STALE_CHECK, JobKind.HANDOFF_ACK_TIMEOUT, JobKind.SESSION_EXPIRY_CHECK):
                # Pure observation jobs - completing records the reconciliation step without mutating lifecycle
                return ReconciliationResult(
                    job_id=job.job_id,
                    kind=job.kind,
                    success=True,
                    status=JobStatus.COMPLETED,
                    action_taken="OBSERVATION_RECORDED",
                    details={"entity_id": job.entity_id},
                )
            else:
                raise ReconciliationError(f"Unsupported job kind: {job.kind}")
        except Exception as exc:
            logger.error(f"Reconciliation error on job '{job.job_id}' ({job.kind}): {exc}")
            return ReconciliationResult(
                job_id=job.job_id,
                kind=job.kind,
                success=False,
                status=JobStatus.FAILED_RETRYABLE,
                error=str(exc),
            )

    def _process_outbox_drain(self, job: ScheduledJob) -> ReconciliationResult:
        """Drains pending deliveries via SqliteEventStore or DeliverySyncService."""
        drained_events = 0
        drained_azure = 0

        # Drain EventStore outbox if present
        if self.event_store and hasattr(self.event_store, "list_pending_deliveries"):
            pending = self.event_store.list_pending_deliveries(limit=50)
            drained_events = len(pending)
            for item in pending:
                # Mark claimed/delivered if handler available
                if hasattr(self.event_store, "claim_next_delivery"):
                    self.event_store.claim_next_delivery()

        # Drain DeliverySyncService outbox if present
        if self.delivery_sync_service and hasattr(self.delivery_sync_service, "drain_outbox"):
            records = self.delivery_sync_service.drain_outbox(batch_size=20)
            drained_azure = len(records)

        return ReconciliationResult(
            job_id=job.job_id,
            kind=job.kind,
            success=True,
            status=JobStatus.COMPLETED,
            action_taken="OUTBOX_DRAINED",
            details={"events_drained": drained_events, "azure_records_drained": drained_azure},
        )

    def _process_azure_reconcile(self, job: ScheduledJob) -> ReconciliationResult:
        """Executes Azure outbox drain and sync reconciliation via R6 DeliverySyncService."""
        if not self.delivery_sync_service:
            return ReconciliationResult(
                job_id=job.job_id,
                kind=job.kind,
                success=True,
                status=JobStatus.COMPLETED,
                action_taken="SKIPPED_NO_DELIVERY_SERVICE",
            )

        drained = self.delivery_sync_service.drain_outbox(batch_size=50)
        return ReconciliationResult(
            job_id=job.job_id,
            kind=job.kind,
            success=True,
            status=JobStatus.COMPLETED,
            action_taken="AZURE_DRAINED",
            details={"drained_count": len(drained)},
        )

    def _process_dispatch_reconcile(self, job: ScheduledJob) -> ReconciliationResult:
        """Reconciles in-flight dispatch status via R11 DispatchService."""
        dispatch_id = job.payload.get("dispatch_id", job.entity_id)
        if not self.dispatch_service:
            return ReconciliationResult(
                job_id=job.job_id,
                kind=job.kind,
                success=True,
                status=JobStatus.COMPLETED,
                action_taken="SKIPPED_NO_DISPATCH_SERVICE",
            )

        status_result = self.dispatch_service.check_dispatch_status(dispatch_id)
        return ReconciliationResult(
            job_id=job.job_id,
            kind=job.kind,
            success=True,
            status=JobStatus.COMPLETED,
            action_taken="DISPATCH_STATUS_CHECKED",
            details={
                "dispatch_id": dispatch_id,
                "status": status_result.status.value if hasattr(status_result.status, "value") else str(status_result.status),
                "is_alive": status_result.is_alive,
            },
        )

    def _process_execution_reconcile(self, job: ScheduledJob) -> ReconciliationResult:
        """Checks for execution evidence via R12 ExecutionReceiptService."""
        work_item_id = job.payload.get("work_item_id", job.entity_id)
        if not self.execution_service:
            return ReconciliationResult(
                job_id=job.job_id,
                kind=job.kind,
                success=True,
                status=JobStatus.COMPLETED,
                action_taken="SKIPPED_NO_EXECUTION_SERVICE",
            )

        # If job payload carries complete execution evidence to ingest, delegate to R12 record_execution
        evidence_payload = job.payload.get("evidence_to_ingest")
        if evidence_payload and hasattr(self.execution_service, "record_execution"):
            try:
                receipt = self.execution_service.record_execution(
                    work_item_id=work_item_id,
                    project_id=evidence_payload.get("project_id", "default"),
                    agent_id=evidence_payload["agent_id"],
                    stage=evidence_payload["stage"],
                    instruction_hash=evidence_payload["instruction_hash"],
                    evidence_hash=evidence_payload["evidence_hash"],
                    files_modified=evidence_payload.get("files_modified", []),
                    tests_executed=evidence_payload.get("tests_executed", []),
                    test_exit_code=evidence_payload.get("test_exit_code", 0),
                    diff_summary=evidence_payload.get("diff_summary", ""),
                    receipt_id=evidence_payload.get("receipt_id"),
                )
                return ReconciliationResult(
                    job_id=job.job_id,
                    kind=job.kind,
                    success=True,
                    status=JobStatus.COMPLETED,
                    action_taken="EXECUTION_INGESTED_VIA_R12",
                    details={"receipt_id": receipt.receipt_id, "work_item_id": work_item_id},
                )
            except Exception as e:
                return ReconciliationResult(
                    job_id=job.job_id,
                    kind=job.kind,
                    success=False,
                    status=JobStatus.FAILED_RETRYABLE,
                    error=f"R12 ingestion failed: {e}",
                )

        # Query if execution receipt is recorded
        has_exec = False
        if hasattr(self.execution_service, "repository"):
            latest = self.execution_service.repository.get_latest_execution_receipt(work_item_id)
            has_exec = latest is not None

        return ReconciliationResult(
            job_id=job.job_id,
            kind=job.kind,
            success=True,
            status=JobStatus.COMPLETED,
            action_taken="EXECUTION_RECEIPT_CHECKED",
            details={"work_item_id": work_item_id, "has_execution_receipt": has_exec},
        )
