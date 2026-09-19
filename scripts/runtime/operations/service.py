"""Unified Operations Control Service (Milestone R13).

Strictly stdlib-only.
Coordinates Watchdog scanning, deterministic Scheduler ticking, and Reconciliation execution.
Enforces zero daemon requirements: can be run once synchronously via run_once(now).
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from scripts.runtime.operations.clock import ClockPort, SystemClock
from scripts.runtime.operations.models import (
    JobStatus,
    OperationsRunReport,
    ReconciliationResult,
    ScheduledJob,
    WatchdogFinding,
)
from scripts.runtime.operations.reconciliation import ReconciliationService
from scripts.runtime.operations.scheduler import SchedulerService
from scripts.runtime.operations.watchdog import WatchdogService

logger = logging.getLogger(__name__)


class OperationsControlService:
    """Primary operational orchestrator unifying watchdog, scheduler, and reconciliation."""

    def __init__(
        self,
        scheduler: SchedulerService,
        watchdog: WatchdogService,
        reconciler: ReconciliationService,
        clock: Optional[ClockPort] = None,
    ) -> None:
        self.scheduler = scheduler
        self.watchdog = watchdog
        self.reconciler = reconciler
        self.clock = clock or SystemClock()

    def run_once(
        self,
        batch_limit: int = 20,
        lease_seconds: float = 60.0,
        worker_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> OperationsRunReport:
        """Executes a complete, deterministic operational cycle."""
        current_time = now or self.clock.now()
        run_id = f"ops-{uuid.uuid4()}"
        worker = worker_id or f"worker-{run_id[:8]}"

        report = OperationsRunReport(
            run_id=run_id,
            started_at=current_time,
            completed_at=current_time,
        )

        # 1. Run Watchdog Scanners to identify conditions and schedule reconciliation jobs
        findings = self.watchdog.scan_all(now=current_time)
        report.findings.extend(findings)

        # 2. Scheduler Tick: Claim all due jobs atomically
        claimed_jobs = self.scheduler.tick(
            worker_id=worker,
            limit=batch_limit,
            lease_seconds=lease_seconds,
            now=current_time,
        )
        for job in claimed_jobs:
            report.jobs_claimed.append(job.job_id)

        # 3. Reconciliation: Execute claimed jobs via canonical authorities
        for job in claimed_jobs:
            result = self.reconciler.process_job(job, now=current_time)
            if result.success:
                self.scheduler.complete_job(job.job_id, now=current_time)
                report.jobs_completed.append(job.job_id)
            else:
                failed_job = self.scheduler.fail_job(
                    job_id=job.job_id,
                    error_message=result.error or "Reconciliation failed",
                    now=current_time,
                )
                report.jobs_failed.append(job.job_id)
                if failed_job.status == JobStatus.FAILED_TERMINAL:
                    report.dead_letters.append(job.job_id)

        report_completed_time = self.clock.now()
        report = OperationsRunReport(
            run_id=run_id,
            started_at=current_time,
            completed_at=report_completed_time,
            findings=report.findings,
            jobs_scheduled=[f.suggested_action for f in findings if f.suggested_action],
            jobs_claimed=report.jobs_claimed,
            jobs_completed=report.jobs_completed,
            jobs_failed=report.jobs_failed,
            dead_letters=report.dead_letters,
            events_emitted=[f.finding_type for f in findings],
        )
        return report
