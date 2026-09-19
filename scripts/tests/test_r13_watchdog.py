"""Tests for R13 Watchdog, Reconciliation, and OperationsControlService.

Strictly stdlib-only testing with zero time.sleep().
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
import yaml

from scripts.runtime.operations.clock import DeterministicClock
from scripts.runtime.operations.models import (
    JobKind,
    JobStatus,
    OperationsRunReport,
)
from scripts.runtime.operations.reconciliation import ReconciliationService
from scripts.runtime.operations.repository import OperationalRepository
from scripts.runtime.operations.scheduler import SchedulerService
from scripts.runtime.operations.service import OperationsControlService
from scripts.runtime.operations.watchdog import WatchdogService


class MockEventStore:
    def __init__(self):
        self.events = []
        self.deliveries = []

    def record(self, event):
        self.events.append(event)

    def save_event(self, event):
        self.events.append(event)

    def list_pending_deliveries(self, limit=50):
        return self.deliveries[:limit]

    def claim_next_delivery(self):
        if self.deliveries:
            return self.deliveries.pop(0)
        return None


class MockDeliverySyncService:
    def __init__(self):
        self.outbox = []

    def drain_outbox(self, batch_size=20):
        drained = self.outbox[:batch_size]
        self.outbox = self.outbox[batch_size:]
        return drained


class MockDispatchService:
    def __init__(self):
        self.checked_dispatches = []

    def check_dispatch_status(self, dispatch_id):
        self.checked_dispatches.append(dispatch_id)
        from scripts.runtime.dispatch.receipts import HostStatusResult, DispatchStatus
        return HostStatusResult(
            host_execution_id="host-123",
            status=DispatchStatus.DISPATCHED,
            is_alive=False,
            details={"return_code": 0},
        )


class TestR13WatchdogAndReconciliation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_squad.db"
        self.work_dir = Path(self.temp_dir.name) / "work"
        self.work_dir.mkdir(parents=True, exist_ok=True)

        self.repo = OperationalRepository(self.db_path)
        self.clock = DeterministicClock(datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc))
        self.scheduler = SchedulerService(self.repo, clock=self.clock)
        self.event_store = MockEventStore()

        self.workflow_cfg = {
            "flow": {
                "phase_timeboxes": {
                    "implementation": 60,  # 60 minutes = 3600s
                }
            }
        }

        self.watchdog = WatchdogService(
            scheduler=self.scheduler,
            event_store=self.event_store,
            clock=self.clock,
            dispatch_timeout_seconds=1800.0,
            handoff_ack_timeout_seconds=3600.0,
            work_dir=self.work_dir,
            workflow_config=self.workflow_cfg,
        )

        self.sync_service = MockDeliverySyncService()
        self.dispatch_service = MockDispatchService()
        self.reconciler = ReconciliationService(
            event_store=self.event_store,
            delivery_sync_service=self.sync_service,
            dispatch_service=self.dispatch_service,
            clock=self.clock,
        )

        self.ops_service = OperationsControlService(
            scheduler=self.scheduler,
            watchdog=self.watchdog,
            reconciler=self.reconciler,
            clock=self.clock,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_watchdog_detects_timebox_exceeded_and_emits_event(self):
        # Create work item folder with status.yaml
        item_dir = self.work_dir / "ITEM-001"
        item_dir.mkdir()
        status_file = item_dir / "status.yaml"

        started_at = (self.clock.now() - timedelta(hours=2)).isoformat()
        status_data = {
            "id": "ITEM-001",
            "state": "implementation",
            "phase_started_at": started_at,
        }
        status_file.write_text(yaml.safe_dump(status_data), encoding="utf-8")

        # Run watchdog scanner
        findings = self.watchdog.scan_lifecycle_timeboxes()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_type, "TIMEBOX_EXCEEDED")
        self.assertEqual(findings[0].entity_id, "ITEM-001")

        # Verify domain event emitted
        self.assertEqual(len(self.event_store.events), 1)
        self.assertEqual(self.event_store.events[0].event_type, WatchdogService.EVENT_TIMEBOX_EXCEEDED)

        # Verify job scheduled
        claimed = self.scheduler.tick()
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].kind, JobKind.LIFECYCLE_STALE_CHECK)
        self.assertEqual(claimed[0].entity_id, "ITEM-001")

    def test_watchdog_detects_unacknowledged_handoff_timeout(self):
        item_dir = self.work_dir / "ITEM-002"
        item_dir.mkdir()
        handoffs_dir = item_dir / "handoffs"
        handoffs_dir.mkdir()

        created_at = (self.clock.now() - timedelta(hours=3)).isoformat()
        handoff_data = {
            "id": "HANDOFF-002",
            "created_at": created_at,
            "acknowledgement": {"status": "pending"},
        }
        (handoffs_dir / "HANDOFF-002.yaml").write_text(yaml.safe_dump(handoff_data), encoding="utf-8")

        findings = self.watchdog.scan_handoff_timeouts()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_type, "HANDOFF_ACK_OVERDUE")
        self.assertEqual(findings[0].entity_id, "HANDOFF-002")

        # Verify domain event
        self.assertEqual(len(self.event_store.events), 1)
        self.assertEqual(self.event_store.events[0].event_type, WatchdogService.EVENT_HANDOFF_OVERDUE)

    def test_operations_control_service_run_once_e2e(self):
        # 1. Enqueue outbox items in MockDeliverySyncService
        self.sync_service.outbox = [{"id": "sync-1"}, {"id": "sync-2"}]

        # 2. Schedule outbox drain job
        self.scheduler.schedule_job(
            kind=JobKind.OUTBOX_DRAIN,
            entity_type="outbox",
            entity_id="global-outbox",
        )

        # 3. Schedule dispatch reconcile job
        self.scheduler.schedule_job(
            kind=JobKind.DISPATCH_RECONCILE,
            entity_type="dispatch",
            entity_id="disp-rec-1",
            payload={"dispatch_id": "disp-rec-1"},
        )

        # Run operational cycle
        report: OperationsRunReport = self.ops_service.run_once()

        self.assertEqual(len(report.jobs_claimed), 2)
        self.assertEqual(len(report.jobs_completed), 2)
        self.assertEqual(len(report.jobs_failed), 0)

        # Verify sync outbox was drained
        self.assertEqual(len(self.sync_service.outbox), 0)

        # Verify dispatch service was invoked
        self.assertIn("disp-rec-1", self.dispatch_service.checked_dispatches)

    def test_watchdog_detects_session_expiry_without_silent_renewal(self):
        # Create mcp_sessions table and insert an expired session
        with self.repo.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    project_root TEXT NOT NULL,
                    work_item_id TEXT,
                    host TEXT NOT NULL,
                    capability_report_hash TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    tools TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            now_ts = self.clock.now().timestamp()
            expired_ts = now_ts - 3600.0
            conn.execute(
                """
                INSERT INTO mcp_sessions (
                    session_id, project_id, project_root, work_item_id, host,
                    capability_report_hash, policy_hash, revision, status, tools,
                    capabilities, created_at, expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sess-expired-123",
                    "proj-alpha",
                    "C:/root",
                    "WI-SESS-01",
                    "antigravity",
                    "cap-hash",
                    "pol-hash",
                    1,
                    "active",
                    "[]",
                    "{}",
                    "2026-09-18T12:00:00Z",
                    expired_ts,
                    "2026-09-18T12:00:00Z",
                ),
            )

        findings = self.watchdog.scan_session_expiries()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_type, "SESSION_EXPIRED")
        self.assertEqual(findings[0].entity_id, "sess-expired-123")

        # Verify domain event
        self.assertEqual(len(self.event_store.events), 1)
        self.assertEqual(self.event_store.events[0].event_type, WatchdogService.EVENT_SESSION_EXPIRED)

        # Verify session was NOT silently renewed or updated in DB (status remained active/expired without new session created)
        with self.repo.connection() as conn:
            sess_count = conn.execute("SELECT COUNT(*) FROM mcp_sessions").fetchone()[0]
            self.assertEqual(sess_count, 1)

    def test_watchdog_overdue_handoff_never_auto_acks(self):
        item_dir = self.work_dir / "ITEM-NO-ACK"
        item_dir.mkdir()
        handoffs_dir = item_dir / "handoffs"
        handoffs_dir.mkdir()

        created_at = (self.clock.now() - timedelta(hours=4)).isoformat()
        handoff_data = {
            "id": "HANDOFF-NO-ACK",
            "created_at": created_at,
            "acknowledgement": {"status": "pending"},
        }
        handoff_file = handoffs_dir / "HANDOFF-NO-ACK.yaml"
        handoff_file.write_text(yaml.safe_dump(handoff_data), encoding="utf-8")

        self.watchdog.scan_handoff_timeouts()

        # Verify file on disk still has status pending (NEVER auto ACKed)
        disk_data = yaml.safe_load(handoff_file.read_text(encoding="utf-8"))
        self.assertEqual(disk_data["acknowledgement"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
