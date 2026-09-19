"""Tests for R12 Lifecycle Engine and Gate Integration with Execution Receipts.

Strictly stdlib-only.
"""

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import yaml

from scripts.domain.lifecycle import GateId, LifecycleStage
from scripts.runtime.execution.repository import ExecutionReceiptRepository
from scripts.runtime.execution.service import ExecutionReceiptService
from scripts.runtime.lifecycle.engine import CanonicalLifecycleService


class TestR12LifecycleGateIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name)
        self.db_path = self.root_path / "banco" / "squad.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.service = CanonicalLifecycleService(db_path=self.db_path, root_path=self.root_path)

        # Create dummy item
        self.item_dir = self.root_path / "work" / "proj-test" / "feat-500"
        self.item_dir.mkdir(parents=True, exist_ok=True)
        status_file = self.item_dir / "status.yaml"
        status_data = {
            "work_id": "feat-500",
            "project_id": "proj-test",
            "type": "feature",
            "cycle": "development",
            "state": "implementation",
            "owner": "06-software-engineer",
            "risk": "low",
        }
        status_file.write_text(yaml.safe_dump(status_data), encoding="utf-8")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_stage_satisfaction_and_gate_eligibility(self):
        exec_svc = self.service.execution_service

        # Before recording execution: implementation is not satisfied
        sat, msg = exec_svc.is_stage_satisfied("feat-500", LifecycleStage.IMPLEMENTATION)
        self.assertFalse(sat)
        self.assertIn("Missing ExecutionReceipt", msg)

        # Record valid execution
        exec_svc.record_execution(
            work_item_id="feat-500",
            project_id="proj-test",
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="inst-1",
            evidence_hash="ev-1",
            files_modified=["mod.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ code",
        )

        sat, _ = exec_svc.is_stage_satisfied("feat-500", LifecycleStage.IMPLEMENTATION)
        self.assertTrue(sat)

        # Security stage gate eligibility
        is_el, _ = exec_svc.verify_gate_eligibility("feat-500", GateId.G4_CODE_SECURITY)
        self.assertFalse(is_el)

        # Record valid security receipt
        exec_svc.record_security(
            work_item_id="feat-500",
            project_id="proj-test",
            agent_id="10-security-reviewer",
            stage=LifecycleStage.SECURITY_REVIEW,
            instruction_hash="inst-sec",
            evidence_hash="ev-sec",
            security_role="10-security-reviewer",
            critical_count=0,
            verdict="APPROVED",
        )

        is_el, _ = exec_svc.verify_gate_eligibility("feat-500", GateId.G4_CODE_SECURITY)
        self.assertTrue(is_el)

    def test_lifecycle_transition_respects_execution_receipt(self):
        # When execution receipt exists in database, _has_execution_proof evaluates to True
        exec_svc = self.service.execution_service
        exec_svc.record_execution(
            work_item_id="feat-500",
            project_id="proj-test",
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="inst-1",
            evidence_hash="ev-1",
            files_modified=["mod.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ code",
        )

        # Check proof
        has_proof = self.service._has_execution_proof(self.item_dir, work_item_id="feat-500")
        self.assertTrue(has_proof)


if __name__ == "__main__":
    unittest.main()
