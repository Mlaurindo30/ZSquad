"""Tests for R13 Operations Scheduler, Models, Clock, and Persistence.

Strictly stdlib-only testing with zero time.sleep().
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from scripts.runtime.operations.clock import DeterministicClock
from scripts.runtime.operations.errors import JobAlreadyExistsError, LeaseAcquisitionError
from scripts.runtime.operations.models import JobKind, JobStatus, ScheduledJob
from scripts.runtime.operations.repository import OperationalRepository
from scripts.runtime.operations.retry import RetryPolicy
from scripts.runtime.operations.scheduler import SchedulerService


class TestR13Scheduler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_squad.db"
        self.repo = OperationalRepository(self.db_path)
        self.clock = DeterministicClock(datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc))
        self.scheduler = SchedulerService(self.repo, clock=self.clock)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_schedule_and_claim_due_job(self):
        job = self.scheduler.schedule_job(
            kind=JobKind.OUTBOX_DRAIN,
            entity_type="outbox",
            entity_id="outbox-1",
        )
        self.assertEqual(job.status, JobStatus.PENDING)
        self.assertEqual(job.attempt, 0)

        # Claim due jobs at current time
        claimed = self.scheduler.tick(worker_id="worker-1")
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].job_id, job.job_id)
        self.assertEqual(claimed[0].status, JobStatus.RUNNING)
        self.assertEqual(claimed[0].lease_owner, "worker-1")
        self.assertEqual(claimed[0].attempt, 1)

    def test_future_job_not_claimed_until_due(self):
        future_time = self.clock.now() + timedelta(minutes=10)
        job = self.scheduler.schedule_job(
            kind=JobKind.DISPATCH_RECONCILE,
            entity_type="dispatch",
            entity_id="disp-1",
            due_at=future_time,
        )

        # Immediate tick should return empty
        claimed = self.scheduler.tick()
        self.assertEqual(len(claimed), 0)

        # Advance clock by 5 minutes -> still not due
        self.clock.advance(timedelta(minutes=5))
        claimed = self.scheduler.tick()
        self.assertEqual(len(claimed), 0)

        # Advance clock past due time -> claimed
        self.clock.advance(timedelta(minutes=6))
        claimed = self.scheduler.tick()
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].job_id, job.job_id)

    def test_complete_job_releases_lease_and_updates_status(self):
        job = self.scheduler.schedule_job(
            kind=JobKind.AZURE_RECONCILE,
            entity_type="azure",
            entity_id="sync-1",
        )
        claimed = self.scheduler.tick()[0]
        completed = self.scheduler.complete_job(claimed.job_id)

        self.assertEqual(completed.status, JobStatus.COMPLETED)
        self.assertIsNone(completed.lease_owner)
        self.assertIsNone(completed.lease_expires_at)

    def test_retry_policy_exponential_backoff_and_dead_letter(self):
        policy = RetryPolicy(max_attempts=2, base_backoff_seconds=10.0, multiplier=2.0)
        scheduler = SchedulerService(self.repo, clock=self.clock, default_retry_policy=policy)

        job = scheduler.schedule_job(
            kind=JobKind.OUTBOX_DRAIN,
            entity_type="outbox",
            entity_id="outbox-retry-1",
        )

        # Attempt 1
        claimed1 = scheduler.tick()[0]
        self.assertEqual(claimed1.attempt, 1)

        # Fail attempt 1 -> should become FAILED_RETRYABLE due at +10s
        failed1 = scheduler.fail_job(claimed1.job_id, error_message="Network timeout")
        self.assertEqual(failed1.status, JobStatus.FAILED_RETRYABLE)
        self.assertEqual(failed1.due_at, self.clock.now() + timedelta(seconds=10))

        # Tick immediately -> not claimed
        self.assertEqual(len(scheduler.tick()), 0)

        # Advance 10s -> Attempt 2 claimed
        self.clock.advance_seconds(10)
        claimed2 = scheduler.tick()[0]
        self.assertEqual(claimed2.attempt, 2)

        # Fail attempt 2 -> reached max_attempts (2) -> FAILED_TERMINAL (Dead letter)
        failed2 = scheduler.fail_job(claimed2.job_id, error_message="Fatal crash")
        self.assertEqual(failed2.status, JobStatus.FAILED_TERMINAL)

    def test_duplicate_job_suppression(self):
        job1 = self.scheduler.schedule_job(
            kind=JobKind.DISPATCH_RECONCILE,
            entity_type="dispatch",
            entity_id="disp-dup",
        )
        # Re-scheduling while job1 is active should return existing job
        job2 = self.scheduler.schedule_job(
            kind=JobKind.DISPATCH_RECONCILE,
            entity_type="dispatch",
            entity_id="disp-dup",
        )
        self.assertEqual(job1.job_id, job2.job_id)

    def test_operational_leases_mutual_exclusion(self):
        lease1 = self.repo.acquire_lease("global-azure-sync", owner="worker-A", lease_seconds=30.0, now=self.clock.now())
        self.assertEqual(lease1.owner, "worker-A")

        # Second worker trying to acquire same active lease must fail
        with self.assertRaises(LeaseAcquisitionError):
            self.repo.acquire_lease("global-azure-sync", owner="worker-B", lease_seconds=30.0, now=self.clock.now())

        # Advance past expiration (35s) -> worker-B can now steal/acquire expired lease
        self.clock.advance_seconds(35.0)
        lease2 = self.repo.acquire_lease("global-azure-sync", owner="worker-B", lease_seconds=30.0, now=self.clock.now())
        self.assertEqual(lease2.owner, "worker-B")

        # Release lease
        self.assertTrue(self.repo.release_lease("global-azure-sync", owner="worker-B"))
        self.assertIsNone(self.repo.get_lease("global-azure-sync"))


if __name__ == "__main__":
    unittest.main()
