"""Deterministic Scheduler Service (Milestone R13).

Strictly stdlib-only.
Coordinates job scheduling, leasing, and due job extraction without background daemons.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import uuid

from scripts.runtime.operations.clock import ClockPort, SystemClock
from scripts.runtime.operations.errors import JobAlreadyExistsError
from scripts.runtime.operations.models import (
    JobKind,
    JobStatus,
    ScheduledJob,
)
from scripts.runtime.operations.repository import OperationalRepository
from scripts.runtime.operations.retry import RetryPolicy


class SchedulerService:
    """Authoritative service for scheduling and claiming operational jobs."""

    def __init__(
        self,
        repository: OperationalRepository,
        clock: Optional[ClockPort] = None,
        default_retry_policy: Optional[RetryPolicy] = None,
    ) -> None:
        self.repository = repository
        self.clock = clock or SystemClock()
        self.retry_policy = default_retry_policy or RetryPolicy()

    def schedule_job(
        self,
        kind: JobKind,
        entity_type: str,
        entity_id: str,
        due_at: Optional[datetime] = None,
        payload: Optional[Dict[str, Any]] = None,
        max_attempts: Optional[int] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        deduplicate_active: bool = True,
    ) -> ScheduledJob:
        """Schedules a new operational job, optionally suppressing active duplicates."""
        now = self.clock.now()

        if deduplicate_active:
            active = self.repository.find_active_job_for_entity(kind, entity_type, entity_id)
            if active:
                return active

        job_id = f"job-{uuid.uuid4()}"
        target_due = due_at or now

        job = ScheduledJob(
            job_id=job_id,
            kind=kind,
            entity_type=entity_type,
            entity_id=entity_id,
            due_at=target_due,
            attempt=0,
            max_attempts=max_attempts or self.retry_policy.max_attempts,
            status=JobStatus.PENDING,
            payload=payload or {},
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        return self.repository.create_job(job, now=now)

    def tick(
        self,
        worker_id: str = "scheduler-worker",
        limit: int = 20,
        lease_seconds: float = 60.0,
        now: Optional[datetime] = None,
    ) -> List[ScheduledJob]:
        """Atomically leases and returns all jobs due at the given (or current clock) time."""
        current_time = now or self.clock.now()
        return self.repository.claim_due_jobs(
            worker_id=worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
            now=current_time,
        )

    def complete_job(self, job_id: str, now: Optional[datetime] = None) -> ScheduledJob:
        """Completes a job and releases its lease."""
        current_time = now or self.clock.now()
        return self.repository.complete_job(job_id, now=current_time)

    def fail_job(
        self,
        job_id: str,
        error_message: str,
        now: Optional[datetime] = None,
    ) -> ScheduledJob:
        """Handles job failure according to the exponential backoff retry policy."""
        current_time = now or self.clock.now()
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError(f"Job '{job_id}' not found")

        # Check if retry is permitted
        if self.retry_policy.is_retryable(job.attempt):
            # job.attempt was already incremented to 1 on first claim; calculate backoff from (job.attempt - 1)
            attempt_idx = max(0, job.attempt - 1)
            next_due = self.retry_policy.next_due_time(
                current_time=current_time,
                attempt=attempt_idx,
                entity_id=job.entity_id,
            )
            return self.repository.fail_job(
                job_id=job_id,
                error_message=error_message,
                retryable=True,
                next_due_at=next_due,
                now=current_time,
            )
        else:
            return self.repository.fail_job(
                job_id=job_id,
                error_message=error_message,
                retryable=False,
                next_due_at=None,
                now=current_time,
            )
