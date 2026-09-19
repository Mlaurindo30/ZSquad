"""Authority and Isolation Invariant Tests for Milestone R13.

Verifies:
1. R13 DOES NOT advance lifecycle stage directly.
2. R13 DOES NOT assign specialists directly (R8 is sole authority).
3. R13 DOES NOT invoke host adapters directly (R11 is sole authority).
4. R13 DOES NOT fabricate execution receipts (R12 is sole authority).
5. R13 DOES NOT execute direct Azure write operations (R6 is sole authority).
"""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from scripts.runtime.operations.clock import DeterministicClock
from scripts.runtime.operations.models import JobKind, JobStatus
from scripts.runtime.operations.reconciliation import ReconciliationService
from scripts.runtime.operations.repository import OperationalRepository
from scripts.runtime.operations.scheduler import SchedulerService


class TestR13AuthorityBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_squad.db"
        self.repo = OperationalRepository(self.db_path)
        self.clock = DeterministicClock(datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc))
        self.scheduler = SchedulerService(self.repo, clock=self.clock)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_r13_reconciliation_does_not_mutate_lifecycle_status_file(self):
        # Setup mock work item with status.yaml
        item_dir = Path(self.temp_dir.name) / "work" / "WI-AUTH-01"
        item_dir.mkdir(parents=True)
        status_file = item_dir / "status.yaml"
        original_content = "id: WI-AUTH-01\nstate: implementation\n"
        status_file.write_text(original_content, encoding="utf-8")

        job = self.scheduler.schedule_job(
            kind=JobKind.LIFECYCLE_STALE_CHECK,
            entity_type="work_item",
            entity_id="WI-AUTH-01",
        )

        reconciler = ReconciliationService(clock=self.clock)
        result = reconciler.process_job(job)

        self.assertTrue(result.success)
        self.assertEqual(result.status, JobStatus.COMPLETED)
        # Verify status.yaml remained completely untouched (R4 retains sole lifecycle authority)
        self.assertEqual(status_file.read_text(encoding="utf-8"), original_content)

    def test_r13_reconciliation_delegates_to_dispatch_service_without_calling_host_directly(self):
        job = self.scheduler.schedule_job(
            kind=JobKind.DISPATCH_RECONCILE,
            entity_type="dispatch",
            entity_id="disp-auth-1",
            payload={"dispatch_id": "disp-auth-1"},
        )

        class SpyDispatchService:
            def __init__(self):
                self.calls = []

            def check_dispatch_status(self, dispatch_id):
                self.calls.append(dispatch_id)
                from scripts.runtime.dispatch.receipts import HostStatusResult, DispatchStatus
                return HostStatusResult(
                    host_execution_id="host-handle",
                    status=DispatchStatus.DISPATCHED,
                    is_alive=True,
                )

        spy = SpyDispatchService()
        reconciler = ReconciliationService(dispatch_service=spy, clock=self.clock)
        result = reconciler.process_job(job)

        self.assertTrue(result.success)
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(spy.calls[0], "disp-auth-1")

    def test_r13_reconciliation_delegates_to_delivery_sync_service_without_direct_azure_patch(self):
        job = self.scheduler.schedule_job(
            kind=JobKind.AZURE_RECONCILE,
            entity_type="azure",
            entity_id="global-azure",
        )

        class SpyDeliverySyncService:
            def __init__(self):
                self.drain_calls = 0

            def drain_outbox(self, batch_size=50):
                self.drain_calls += 1
                return []

        spy = SpyDeliverySyncService()
        reconciler = ReconciliationService(delivery_sync_service=spy, clock=self.clock)
        result = reconciler.process_job(job)

        self.assertTrue(result.success)
        self.assertEqual(spy.drain_calls, 1)

    def test_r13_reconciliation_delegates_execution_ingestion_to_r12_without_fabricating_receipts(self):
        job = self.scheduler.schedule_job(
            kind=JobKind.EXECUTION_RECONCILE,
            entity_type="work_item",
            entity_id="WI-AUTH-EXEC-01",
            payload={
                "work_item_id": "WI-AUTH-EXEC-01",
                "evidence_to_ingest": {
                    "project_id": "proj-auth",
                    "agent_id": "06-software-engineer",
                    "stage": "implementation",
                    "instruction_hash": "hash-inst-123",
                    "evidence_hash": "hash-evid-456",
                    "files_modified": ["src/main.py"],
                    "tests_executed": ["test_main.py"],
                    "test_exit_code": 0,
                    "diff_summary": "+ 10 lines",
                },
            },
        )

        class SpyExecutionReceiptService:
            def __init__(self):
                self.recorded_calls = []

            def record_execution(self, **kwargs):
                self.recorded_calls.append(kwargs)
                from scripts.domain.receipts import ExecutionReceipt, ReceiptType
                return ExecutionReceipt(
                    receipt_id="rcpt-auth-999",
                    receipt_type=ReceiptType.EXECUTION.value,
                    work_item_id=kwargs["work_item_id"],
                    agent_id=kwargs["agent_id"],
                    instruction_hash=kwargs["instruction_hash"],
                    evidence_hash=kwargs["evidence_hash"],
                    files_modified=kwargs["files_modified"],
                    tests_executed=kwargs["tests_executed"],
                    test_exit_code=kwargs["test_exit_code"],
                    diff_summary=kwargs["diff_summary"],
                )

        spy = SpyExecutionReceiptService()
        reconciler = ReconciliationService(execution_service=spy, clock=self.clock)
        result = reconciler.process_job(job)

        self.assertTrue(result.success)
        self.assertEqual(result.action_taken, "EXECUTION_INGESTED_VIA_R12")
        self.assertEqual(len(spy.recorded_calls), 1)
        self.assertEqual(spy.recorded_calls[0]["work_item_id"], "WI-AUTH-EXEC-01")

    def test_r13_core_does_not_import_forbidden_subsystem_internals(self):
        """Architecture invariant: scripts.runtime.operations must NOT directly import:

        - SpecialistRouter (R8)
        - SkillResolver (R9)
        - SpecialistInstructionCompiler (R9)
        - MCP session creators (R10)
        - Host-specific adapters (R11)
        - Azure writer / REST clients directly (R6 writer)
        - Receipt domain model constructors for business fabrication
        """
        import inspect
        import scripts.runtime.operations.clock as op_clock
        import scripts.runtime.operations.errors as op_errors
        import scripts.runtime.operations.models as op_models
        import scripts.runtime.operations.reconciliation as op_reconcile
        import scripts.runtime.operations.repository as op_repo
        import scripts.runtime.operations.retry as op_retry
        import scripts.runtime.operations.scheduler as op_sched
        import scripts.runtime.operations.service as op_service
        import scripts.runtime.operations.watchdog as op_watchdog

        modules = [
            op_clock, op_errors, op_models, op_reconcile,
            op_repo, op_retry, op_sched, op_service, op_watchdog
        ]

        forbidden_names = [
            "SpecialistRouter",
            "SkillResolver",
            "SpecialistInstructionCompiler",
            "AntigravityDispatchAdapter",
            "CodexDispatchAdapter",
            "GeminiDispatchAdapter",
            "ClaudeDispatchAdapter",
            "AzureWriter",
            "advance_stage",
        ]

        for mod in modules:
            source = inspect.getsource(mod)
            for forbidden in forbidden_names:
                self.assertNotIn(
                    f"import {forbidden}",
                    source,
                    f"Module {mod.__name__} violates authority by importing '{forbidden}'",
                )
                self.assertNotIn(
                    f"from {forbidden}",
                    source,
                    f"Module {mod.__name__} violates authority by importing from '{forbidden}'",
                )


if __name__ == "__main__":
    unittest.main()
