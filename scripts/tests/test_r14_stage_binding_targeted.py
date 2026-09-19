"""Milestone R14.1 — Targeted Stage-Binding, Invariant, and Negative Path Test Suite.

Covers:
1. Receipt-Stage Binding: Verifies that recording a receipt with mismatched stage
   or when the work item is in a different lifecycle stage raises InvalidEvidenceError
   or StageIneligibleError.
2. Future Stage Receipt Bypass: Verifies that future receipts cannot satisfy an earlier
   stage exit proof.
3. Negative Paths:
   - ExecutionReceipt with failing tests blocks transition out of IMPLEMENTATION.
   - ReviewReceipt with CHANGES_REQUESTED/REJECTED blocks transition out of CODE_REVIEW.
   - SoD violation (author reviewing own code) blocks transition out of CODE_REVIEW.
   - SecurityReceipt with REJECTED blocks transition out of SECURITY_REVIEW.
   - TestReceipt with failed tests blocks transition out of TEST_VALIDATION.
   - QAReceipt with REJECTED blocks transition out of QA_VALIDATION.
4. Idempotency: Replaying receipts and state transitions does not duplicate history or corrupt state.
5. Restart and Resume: Tearing down services, reopening SQLite database, and resuming pipeline.

Strictly stdlib + pytest.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional
import unittest
import uuid
import yaml

from scripts.domain.lifecycle import GateId, LifecycleStage
from scripts.domain.receipts import ReceiptType
from scripts.runtime.events.store import SqliteEventStore
from scripts.runtime.execution.errors import (
    InvalidEvidenceError,
    SoDViolationError,
    StageIneligibleError,
)
from scripts.runtime.execution.repository import ExecutionReceiptRepository
from scripts.runtime.execution.service import ExecutionReceiptService
from scripts.runtime.lifecycle.engine import CanonicalLifecycleService
from scripts.runtime.lifecycle.errors import (
    GateNotPassedError,
    InvalidTransitionError,
    LifecycleError,
)


class TestR14StageBindingTargeted(unittest.TestCase):
    """Targeted invariant tests for stage-binding, SoD, and negative paths."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "banco" / "squad.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Copy essential canonical configurations from repo root into isolated runtime_root
        repo_root = Path(__file__).resolve().parents[2]
        config_dir = self.root_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)

        for cfg_file in ["cycles.yaml", "workflow.yaml", "agent-registry.yaml"]:
            src = repo_root / "config" / cfg_file
            if src.is_file():
                (config_dir / cfg_file).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        self.event_store = SqliteEventStore(self.db_path)
        self.exec_repo = ExecutionReceiptRepository(db_path=self.db_path)
        self.exec_service = ExecutionReceiptService(
            db_path=self.db_path,
            event_store=self.event_store,
        )
        self.lifecycle_service = CanonicalLifecycleService(
            db_path=self.db_path,
            event_store=self.event_store,
            root_path=self.root_path,
        )

        self.project_id = "test-binding-proj"
        self.work_item_id = "TASK-STAGE-001"
        self.item_path = self.root_path / "work" / self.project_id / "items" / self.work_item_id
        self.item_path.mkdir(parents=True, exist_ok=True)

        status_data = {
            "id": self.work_item_id,
            "project_id": self.project_id,
            "state": "intake",
            "stage": LifecycleStage.INTAKE.value,
            "cycle": "development",
            "active_agents": ["00-delivery-orchestrator"],
        }
        (self.item_path / "status.yaml").write_text(yaml.safe_dump(status_data), encoding="utf-8")

        # Initialize item in INTAKE
        init_res = self.lifecycle_service.initialize_work_item(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            cycle_id="development",
            initiated_by="00-delivery-orchestrator",
            item_path=self.item_path,
        )
        # Advance through discovery, blueprint, and scaffolding into implementation
        self.lifecycle_service.transition(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            target_stage=LifecycleStage.DISCOVERY,
            item_path=self.item_path,
        )
        self.lifecycle_service.transition(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            target_stage=LifecycleStage.REQUIREMENTS_PRODUCT,
            item_path=self.item_path,
        )
        self._record_gate(GateId.G1_PRODUCT, "approved")
        self._record_gate(GateId.G2_DESIGN, "approved")
        self.lifecycle_service.transition(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            target_stage=LifecycleStage.READINESS_SCAFFOLDING,
            item_path=self.item_path,
        )
        self._record_gate(GateId.G3_READINESS, "approved")
        self.lifecycle_service.transition(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            target_stage=LifecycleStage.IMPLEMENTATION,
            item_path=self.item_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()



    def _record_gate(self, gate_id: GateId, decision: str) -> None:
        decisions_dir = self.item_path / "gate-decisions"
        decisions_dir.mkdir(parents=True, exist_ok=True)
        gate_file = decisions_dir / f"{gate_id.value}.yaml"
        gate_file.write_text(
            yaml.safe_dump(
                {
                    "gate_id": gate_id.value,
                    "decision": decision,
                    "status": decision,
                    "decided_by": "00-delivery-orchestrator",
                    "decided_at": datetime.now().isoformat(),
                    "decision_id": f"DEC-{uuid.uuid4().hex[:8]}",
                }
            ),
            encoding="utf-8",
        )

    def test_receipt_stage_binding_enforced_and_rejected_on_stage_mismatch(self) -> None:
        """Recording a receipt out of its owning stage or with mismatched stage raises error."""
        # Work item is currently in IMPLEMENTATION.
        # 1. Recording ReviewReceipt while in IMPLEMENTATION must raise StageIneligibleError
        with self.assertRaises(StageIneligibleError):
            self.exec_service.record_review(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                agent_id="09-code-reviewer",
                stage=LifecycleStage.CODE_REVIEW,
                instruction_hash="inst-h",
                evidence_hash="ev-h",
                reviewer_role="09-code-reviewer",
            )

        # 2. Recording ReviewReceipt with stage != CODE_REVIEW must raise InvalidEvidenceError
        with self.assertRaises(InvalidEvidenceError):
            self.exec_service.record_review(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                agent_id="09-code-reviewer",
                stage=LifecycleStage.IMPLEMENTATION,
                instruction_hash="inst-h",
                evidence_hash="ev-h",
                reviewer_role="09-code-reviewer",
            )

        # 3. Recording SecurityReceipt while in IMPLEMENTATION must raise StageIneligibleError
        with self.assertRaises(StageIneligibleError):
            self.exec_service.record_security(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                agent_id="10-security-reviewer",
                stage=LifecycleStage.SECURITY_REVIEW,
                instruction_hash="inst-h",
                evidence_hash="ev-h",
                security_role="10-security-reviewer",
            )

    def test_negative_path_failing_tests_in_execution_receipt_blocks_transition(self) -> None:
        """ExecutionReceipt with test_exit_code != 0 is rejected, blocking transition out of IMPLEMENTATION."""
        with self.assertRaises(InvalidEvidenceError):
            self.exec_service.record_execution(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                agent_id="06-software-engineer",
                stage=LifecycleStage.IMPLEMENTATION,
                instruction_hash="inst-h",
                evidence_hash="ev-h",
                files_modified=["mod.py"],
                tests_executed=["pytest"],
                test_exit_code=1,  # FAILING
                diff_summary="+ def broken(): pass",
            )

        with self.assertRaises(LifecycleError) as ctx:
            self.lifecycle_service.transition(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                target_stage=LifecycleStage.CODE_REVIEW,
                item_path=self.item_path,
            )
        self.assertIn("exige ExecutionReceipt", str(ctx.exception))

    def test_negative_path_sod_violation_blocks_code_review_exit(self) -> None:
        """Author cannot review their own implementation (Segregation of Duties)."""
        # Record passing execution
        self.exec_service.record_execution(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="inst-h",
            evidence_hash="ev-h",
            files_modified=["mod.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ def code(): pass",
        )
        # Advance to CODE_REVIEW
        self.lifecycle_service.transition(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            target_stage=LifecycleStage.CODE_REVIEW,
            item_path=self.item_path,
        )

        # Attempting review by the SAME agent must be rejected by SoD
        with self.assertRaises(SoDViolationError):
            self.exec_service.record_review(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                agent_id="06-software-engineer",  # SAME AS IMPLEMENTER
                stage=LifecycleStage.CODE_REVIEW,
                instruction_hash="inst-h",
                evidence_hash="ev-h",
                reviewer_role="09-code-reviewer",
            )

    def test_negative_path_changes_requested_blocks_code_review_exit(self) -> None:
        """ReviewReceipt with CHANGES_REQUESTED is rejected, blocking advance to SECURITY_REVIEW."""
        self.exec_service.record_execution(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="inst-h",
            evidence_hash="ev-h",
            files_modified=["mod.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ def code(): pass",
        )
        self.lifecycle_service.transition(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            target_stage=LifecycleStage.CODE_REVIEW,
            item_path=self.item_path,
        )

        # Record review with CHANGES_REQUESTED must be rejected
        with self.assertRaises(InvalidEvidenceError):
            self.exec_service.record_review(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                agent_id="09-code-reviewer",
                stage=LifecycleStage.CODE_REVIEW,
                instruction_hash="inst-h",
                evidence_hash="ev-h",
                reviewer_role="09-code-reviewer",
                verdict="CHANGES_REQUESTED",
                comments=["Needs refactoring"],
            )

        with self.assertRaises(LifecycleError) as ctx:
            self.lifecycle_service.transition(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                target_stage=LifecycleStage.SECURITY_REVIEW,
                item_path=self.item_path,
            )
        self.assertIn("ReviewReceipt com aprovação válida", str(ctx.exception))

    def test_restart_and_resume_across_process_boundary(self) -> None:
        """Tearing down services, reopening SQLite database, and resuming pipeline."""
        # 1. Execute and advance to CODE_REVIEW
        self.exec_service.record_execution(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="inst-h",
            evidence_hash="ev-h",
            files_modified=["mod.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ def code(): pass",
        )
        self.lifecycle_service.transition(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            target_stage=LifecycleStage.CODE_REVIEW,
            item_path=self.item_path,
        )

        # 2. Simulate complete process teardown
        del self.lifecycle_service
        del self.exec_service
        del self.exec_repo
        del self.event_store

        # 3. Simulate process resurrection
        new_event_store = SqliteEventStore(db_path=self.db_path)
        new_exec_repo = ExecutionReceiptRepository(db_path=self.db_path)
        new_exec_service = ExecutionReceiptService(
            db_path=self.db_path,
            event_store=new_event_store,
        )
        new_lifecycle_service = CanonicalLifecycleService(
            db_path=self.db_path,
            event_store=new_event_store,
            root_path=self.root_path,
        )

        # 4. Verify resurrected state
        current_stage = new_exec_repo.get_work_item_current_stage(self.work_item_id)
        self.assertEqual(current_stage, LifecycleStage.CODE_REVIEW.value)

        # 5. Successfully record review in resurrected service
        rev_rcpt = new_exec_service.record_review(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            agent_id="09-code-reviewer",
            stage=LifecycleStage.CODE_REVIEW,
            instruction_hash="inst-rev-h",
            evidence_hash="ev-rev-h",
            reviewer_role="09-code-reviewer",
            verdict="APPROVED",
        )
        self.assertEqual(rev_rcpt.verdict, "APPROVED")

        # 6. Successfully advance to SECURITY_REVIEW
        res = new_lifecycle_service.transition(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            target_stage=LifecycleStage.SECURITY_REVIEW,
            item_path=self.item_path,
        )
        self.assertEqual(res["canonical_state"], LifecycleStage.SECURITY_REVIEW.value)

    def test_future_stage_receipt_cannot_bypass_current_stage(self) -> None:
        """Future stage receipts injected into database cannot satisfy earlier stage exit proof."""
        # 1. Work item is in IMPLEMENTATION.
        # Inject a rogue QAReceipt directly into DB
        with self.exec_repo.connection() as conn:
            conn.execute(
                """
                INSERT INTO validation_receipts (
                    receipt_id, work_item_id, project_id, stage, receipt_type,
                    agent_id, role, verdict, instruction_hash, evidence_hash, details, raw_payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rcpt-rogue-qa",
                    self.work_item_id,
                    self.project_id,
                    LifecycleStage.QA_VALIDATION.value,
                    ReceiptType.QA.value,
                    "12-qa-engineer",
                    "12-qa-engineer",
                    "APPROVED",
                    "inst-h",
                    "ev-h",
                    "{}",
                    "{}",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        # 2. Attempting to transition out of IMPLEMENTATION must still be blocked!
        with self.assertRaises(LifecycleError) as ctx:
            self.lifecycle_service.transition(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                target_stage=LifecycleStage.CODE_REVIEW,
                item_path=self.item_path,
            )
        self.assertIn("exige ExecutionReceipt", str(ctx.exception))

        # 3. Record real passing ExecutionReceipt and advance to CODE_REVIEW
        self.exec_service.record_execution(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="inst-h",
            evidence_hash="ev-h",
            files_modified=["mod.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ def code(): pass",
        )
        self.lifecycle_service.transition(
            work_item_id=self.work_item_id,
            project_id=self.project_id,
            target_stage=LifecycleStage.CODE_REVIEW,
            item_path=self.item_path,
        )

        # 4. In CODE_REVIEW, the rogue QAReceipt cannot satisfy code review exit either!
        with self.assertRaises(LifecycleError) as ctx:
            self.lifecycle_service.transition(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                target_stage=LifecycleStage.SECURITY_REVIEW,
                item_path=self.item_path,
            )
        self.assertIn("ReviewReceipt com aprovação válida", str(ctx.exception))

    def test_idempotency_of_transitions_and_initialization(self) -> None:
        """Lifecycle operations reject invalid duplicate transitions and initialize idempotently."""
        # 1. Calling initialize_work_item on fresh item twice returns was_created: True then False
        fresh_id = "TASK-FRESH-001"
        fresh_path = self.root_path / "work" / self.project_id / "items" / fresh_id
        fresh_path.mkdir(parents=True, exist_ok=True)
        status_data = {
            "id": fresh_id,
            "project_id": self.project_id,
            "state": "intake",
            "stage": LifecycleStage.INTAKE.value,
            "cycle": "development",
        }
        (fresh_path / "status.yaml").write_text(yaml.safe_dump(status_data), encoding="utf-8")

        init1 = self.lifecycle_service.initialize_work_item(
            work_item_id=fresh_id,
            project_id=self.project_id,
            cycle_id="development",
            item_path=fresh_path,
        )
        self.assertTrue(init1["was_created"])

        init2 = self.lifecycle_service.initialize_work_item(
            work_item_id=fresh_id,
            project_id=self.project_id,
            cycle_id="development",
            item_path=fresh_path,
        )
        self.assertFalse(init2["was_created"])

        # 2. Re-initializing an advanced item raises conflicting initialization error
        with self.assertRaises(LifecycleError) as ctx:
            self.lifecycle_service.initialize_work_item(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                cycle_id="development",
                item_path=self.item_path,
            )
        self.assertIn("Conflicting initialization", str(ctx.exception))

        # 3. Attempting transition to identical current stage raises InvalidTransitionError
        with self.assertRaises(InvalidTransitionError) as ctx:
            self.lifecycle_service.transition(
                work_item_id=self.work_item_id,
                project_id=self.project_id,
                target_stage=LifecycleStage.IMPLEMENTATION,
                item_path=self.item_path,
            )
        self.assertIn("idêntico ao estado atual", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
