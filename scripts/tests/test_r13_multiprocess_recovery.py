"""Multiprocess Lease Safety and Crash Recovery Tests for Milestone R13.1.

Proves:
1. Real OS multi-process concurrency safety: 8 worker processes claiming 1 due job.
   Exactly ONE worker acquires the active lease.
2. Crash recovery: Worker A claims, crashes, lease expires, Worker B reclaims and processes once.
3. Durable restart: Process A schedules, exits, Process B opens DB and verifies state/claims.
"""

from datetime import datetime, timedelta, timezone
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from scripts.runtime.operations.clock import DeterministicClock
from scripts.runtime.operations.models import JobKind, JobStatus, ScheduledJob
from scripts.runtime.operations.repository import OperationalRepository
from scripts.runtime.operations.scheduler import SchedulerService


def _worker_claim_task(db_path_str: str, worker_id: str, result_queue: multiprocessing.Queue):
    """Separate OS process claiming due jobs from shared SQLite database."""
    try:
        repo = OperationalRepository(Path(db_path_str))
        claimed = repo.claim_due_jobs(worker_id=worker_id, limit=5, lease_seconds=30.0)
        claimed_ids = [j.job_id for j in claimed]
        result_queue.put({"worker_id": worker_id, "claimed_ids": claimed_ids, "error": None})
    except Exception as e:
        result_queue.put({"worker_id": worker_id, "claimed_ids": [], "error": str(e)})


class TestR13MultiprocessLeaseSafety(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "shared_squad.db"
        self.repo = OperationalRepository(self.db_path)
        self.clock = DeterministicClock(datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc))
        self.scheduler = SchedulerService(self.repo, clock=self.clock)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_multiprocess_lease_claim_8_concurrent_os_workers(self):
        """Scenario: 1 due job + 8 concurrent OS processes attempting to claim it.

        Required: Exactly ONE worker obtains the active lease.
        """
        # Schedule 1 due job
        job = self.scheduler.schedule_job(
            kind=JobKind.OUTBOX_DRAIN,
            entity_type="outbox",
            entity_id="global-outbox-mp",
        )

        num_workers = 8
        queue = multiprocessing.Queue()
        processes = []

        for i in range(num_workers):
            p = multiprocessing.Process(
                target=_worker_claim_task,
                args=(str(self.db_path), f"worker-{i}", queue),
            )
            processes.append(p)

        # Launch all 8 OS processes simultaneously
        for p in processes:
            p.start()

        for p in processes:
            p.join(timeout=10.0)

        # Collect results
        results = []
        while not queue.empty():
            results.append(queue.get())

        self.assertEqual(len(results), num_workers)

        workers_with_claims = [r for r in results if job.job_id in r["claimed_ids"]]
        self.assertEqual(
            len(workers_with_claims),
            1,
            f"Expected exactly 1 worker to claim job '{job.job_id}', but got: {workers_with_claims}",
        )

        # Verify job state in DB: status = RUNNING, lease_owner matches claiming worker
        db_job = self.repo.get_job(job.job_id)
        self.assertIsNotNone(db_job)
        self.assertEqual(db_job.status, JobStatus.RUNNING)
        self.assertEqual(db_job.lease_owner, workers_with_claims[0]["worker_id"])
        self.assertEqual(db_job.attempt, 1)

    def test_crash_recovery_orphan_lease_stealing_and_safe_completion(self):
        """Scenario: Worker A claims job, worker A crashes, clock advances past lease expiry,

        Worker B reclaims same job, job completes safely.
        """
        job = self.scheduler.schedule_job(
            kind=JobKind.DISPATCH_RECONCILE,
            entity_type="dispatch",
            entity_id="disp-crash-1",
        )

        # Worker A claims with 30s lease at T0
        claimed_a = self.scheduler.tick(worker_id="worker-A", lease_seconds=30.0)
        self.assertEqual(len(claimed_a), 1)
        self.assertEqual(claimed_a[0].lease_owner, "worker-A")
        self.assertEqual(claimed_a[0].status, JobStatus.RUNNING)

        # Worker A crashes (does not call complete_job or fail_job)
        # Immediate tick at T0+5s -> lease still valid -> nobody can claim
        self.clock.advance_seconds(5.0)
        claimed_empty = self.scheduler.tick(worker_id="worker-B")
        self.assertEqual(len(claimed_empty), 0)

        # Advance past lease expiry (T0+35s)
        self.clock.advance_seconds(30.0)

        # Worker B ticks -> reclaims expired lease
        claimed_b = self.scheduler.tick(worker_id="worker-B", lease_seconds=30.0)
        self.assertEqual(len(claimed_b), 1)
        self.assertEqual(claimed_b[0].job_id, job.job_id)
        self.assertEqual(claimed_b[0].lease_owner, "worker-B")
        self.assertEqual(claimed_b[0].attempt, 2)

        # Worker B completes job safely
        completed = self.scheduler.complete_job(job.job_id)
        self.assertEqual(completed.status, JobStatus.COMPLETED)
        self.assertIsNone(completed.lease_owner)

    def test_durable_restart_across_independent_process_instances(self):
        """Scenario: Process A schedules future/due job, exits.

        New process opens same SQLite DB, job exists, due state and correlation preserved.
        """
        job = self.scheduler.schedule_job(
            kind=JobKind.AZURE_RECONCILE,
            entity_type="azure",
            entity_id="sync-restart-1",
            correlation_id="corr-restart-99",
            causation_id="caus-restart-88",
            payload={"action": "full_sync"},
        )

        # Process A exits (destroy scheduler and repo instances)
        del self.scheduler
        del self.repo

        # Process B opens existing DB file
        new_repo = OperationalRepository(self.db_path)
        new_scheduler = SchedulerService(new_repo, clock=self.clock)

        loaded_job = new_repo.get_job(job.job_id)
        self.assertIsNotNone(loaded_job)
        self.assertEqual(loaded_job.status, JobStatus.PENDING)
        self.assertEqual(loaded_job.correlation_id, "corr-restart-99")
        self.assertEqual(loaded_job.causation_id, "caus-restart-88")
        self.assertEqual(loaded_job.payload.get("action"), "full_sync")

        # Process B can claim and process normally
        claimed = new_scheduler.tick(worker_id="worker-proc-B")
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].job_id, job.job_id)


if __name__ == "__main__":
    unittest.main()
