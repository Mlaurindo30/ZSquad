"""Tests for R12.1 Execution Receipt Semantics, Locality, and Governance Chain.

Strictly stdlib-only.
"""

import sqlite3
import unittest

from scripts.domain.lifecycle import LifecycleStage
from scripts.runtime.execution.errors import SoDViolationError
from scripts.runtime.execution.repository import ExecutionReceiptRepository
from scripts.runtime.execution.service import ExecutionReceiptService
from scripts.runtime.execution.stage_policy import STAGE_RECEIPT_REQUIREMENTS


class TestR121ContractSemantics(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.repo = ExecutionReceiptRepository(self.conn)
        self.service = ExecutionReceiptService(repository=self.repo)

    def tearDown(self):
        self.conn.close()

    def test_1_execution_receipt_final_without_downstream_receipts(self):
        """1. ExecutionReceipt can become valid/final without TestReceipt or other downstream receipts."""
        exec_receipt = self.service.record_execution(
            work_item_id="wi-101",
            project_id="proj-1",
            agent_id="06-software-engineer",
            stage="IMPLEMENTATION",
            instruction_hash="inst-1",
            evidence_hash="ev-1",
            files_modified=["a.py"],
            tests_executed=["pytest test_a.py"],
            test_exit_code=0,
            diff_summary="+ code",
        )
        self.assertIsNotNone(exec_receipt)
        self.assertEqual(exec_receipt.receipt_type, "EXECUTION")

        # Ingested and retrievable as final execution evidence
        fetched = self.repo.get_latest_execution_receipt("wi-101")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.receipt_id, exec_receipt.receipt_id)

    def test_2_implementation_exits_with_execution_evidence_only(self):
        """2. IMPLEMENTATION exits with valid execution evidence only."""
        self.service.record_execution(
            work_item_id="wi-102",
            project_id="proj-1",
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="inst-2",
            evidence_hash="ev-2",
            files_modified=["impl.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ impl",
        )

        sat, reason = self.service.is_stage_satisfied("wi-102", LifecycleStage.IMPLEMENTATION)
        self.assertTrue(sat)
        self.assertIn("satisfied", reason.lower())

    def test_3_code_review_requires_independent_review_receipt(self):
        """3. CODE_REVIEW does not pass without independent ReviewReceipt."""
        self.service.record_execution(
            work_item_id="wi-103",
            project_id="proj-1",
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="inst-3",
            evidence_hash="ev-3",
            files_modified=["impl.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ impl",
        )

        # Before review: not satisfied
        sat, reason = self.service.is_stage_satisfied("wi-103", LifecycleStage.CODE_REVIEW)
        self.assertFalse(sat)
        self.assertIn("Missing REVIEW receipt", reason)

        # Implementer cannot review own work
        with self.assertRaises(SoDViolationError):
            self.service.record_review(
                work_item_id="wi-103",
                project_id="proj-1",
                agent_id="06-software-engineer",
                stage=LifecycleStage.CODE_REVIEW,
                instruction_hash="inst-r",
                evidence_hash="ev-r",
                reviewer_role="09-code-reviewer",
                verdict="APPROVED",
            )

        # Independent review passes
        self.service.record_review(
            work_item_id="wi-103",
            project_id="proj-1",
            agent_id="09-code-reviewer",
            stage=LifecycleStage.CODE_REVIEW,
            instruction_hash="inst-r",
            evidence_hash="ev-r",
            reviewer_role="09-code-reviewer",
            verdict="APPROVED",
        )
        sat, _ = self.service.is_stage_satisfied("wi-103", LifecycleStage.CODE_REVIEW)
        self.assertTrue(sat)

    def test_4_test_validation_does_not_pass_using_implementer_evidence(self):
        """4. TEST_VALIDATION does not pass using implementer's own test evidence."""
        self.service.record_execution(
            work_item_id="wi-104",
            project_id="proj-1",
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="inst-4",
            evidence_hash="ev-4",
            files_modified=["impl.py"],
            tests_executed=["pytest tests/"],
            test_exit_code=0,
            diff_summary="+ impl",
        )

        # Implementation evidence alone does NOT satisfy TEST_VALIDATION
        sat, reason = self.service.is_stage_satisfied("wi-104", LifecycleStage.TEST_VALIDATION)
        self.assertFalse(sat)
        self.assertIn("Missing TEST receipt", reason)

        # Implementer self-test signoff is blocked by SoD
        with self.assertRaises(SoDViolationError):
            self.service.record_test(
                work_item_id="wi-104",
                project_id="proj-1",
                agent_id="06-software-engineer",
                stage=LifecycleStage.TEST_VALIDATION,
                instruction_hash="inst-t",
                evidence_hash="ev-t",
                tester_role="11-test-engineer",
                total_tests=10,
                passed_tests=10,
                failed_tests=0,
            )

    def test_5_qa_does_not_pass_using_test_receipt_alone(self):
        """5. QA does not pass using TestReceipt alone."""
        self.service.record_execution(
            work_item_id="wi-105",
            project_id="proj-1",
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="inst-5",
            evidence_hash="ev-5",
            files_modified=["impl.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ impl",
        )
        self.service.record_test(
            work_item_id="wi-105",
            project_id="proj-1",
            agent_id="11-test-engineer",
            stage=LifecycleStage.TEST_VALIDATION,
            instruction_hash="inst-t",
            evidence_hash="ev-t",
            tester_role="11-test-engineer",
            total_tests=10,
            passed_tests=10,
            failed_tests=0,
        )

        # Test receipt exists, but QA is still not satisfied
        sat, reason = self.service.is_stage_satisfied("wi-105", LifecycleStage.QA_VALIDATION)
        self.assertFalse(sat)
        self.assertIn("Missing QA receipt", reason)

    def test_6_security_not_required_behaves_explicitly(self):
        """6. Security NOT_REQUIRED behaves explicitly without manufacturing fake PASS."""
        self.service.record_execution(
            work_item_id="wi-106",
            project_id="proj-1",
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="inst-6",
            evidence_hash="ev-6",
            files_modified=["impl.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ impl",
        )

        # When security_required=False, stage is satisfied with explicit policy reason
        sat, reason = self.service.is_stage_satisfied(
            "wi-106",
            LifecycleStage.SECURITY_REVIEW,
            security_required=False,
        )
        self.assertTrue(sat)
        self.assertEqual(reason, "Security review explicitly not required by policy")

        # When security_required=True, it requires explicit SecurityReceipt
        sat, reason = self.service.is_stage_satisfied(
            "wi-106",
            LifecycleStage.SECURITY_REVIEW,
            security_required=True,
        )
        self.assertFalse(sat)
        self.assertIn("Missing SECURITY receipt", reason)

    def test_7_governance_chain_validation(self):
        """7. Governance rejects incomplete required evidence chain and approves complete chain."""
        wid = "wi-107"
        # 0 receipts: incomplete
        complete, msg, _ = self.service.verify_governance_chain(wid, is_security_required=True)
        self.assertFalse(complete)
        self.assertIn("Missing ExecutionReceipt", msg)

        # 1. Add Execution
        self.service.record_execution(
            work_item_id=wid,
            project_id="proj-1",
            agent_id="06-software-engineer",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="i",
            evidence_hash="e",
            files_modified=["m.py"],
            tests_executed=["t"],
            test_exit_code=0,
            diff_summary="+ m",
        )
        complete, msg, _ = self.service.verify_governance_chain(wid, is_security_required=True)
        self.assertFalse(complete)
        self.assertIn("Missing ReviewReceipt", msg)

        # 2. Add Review
        self.service.record_review(
            work_item_id=wid,
            project_id="proj-1",
            agent_id="09-code-reviewer",
            stage=LifecycleStage.CODE_REVIEW,
            instruction_hash="ir",
            evidence_hash="er",
            reviewer_role="09-code-reviewer",
            verdict="APPROVED",
        )
        complete, msg, _ = self.service.verify_governance_chain(wid, is_security_required=True)
        self.assertFalse(complete)
        self.assertIn("Missing required SecurityReceipt", msg)

        # 3. Add Security
        self.service.record_security(
            work_item_id=wid,
            project_id="proj-1",
            agent_id="10-security-reviewer",
            stage=LifecycleStage.SECURITY_REVIEW,
            instruction_hash="is",
            evidence_hash="es",
            security_role="10-security-reviewer",
            critical_count=0,
            verdict="APPROVED",
        )
        complete, msg, _ = self.service.verify_governance_chain(wid, is_security_required=True)
        self.assertFalse(complete)
        self.assertIn("Missing TestReceipt", msg)

        # 4. Add Test
        self.service.record_test(
            work_item_id=wid,
            project_id="proj-1",
            agent_id="11-test-engineer",
            stage=LifecycleStage.TEST_VALIDATION,
            instruction_hash="it",
            evidence_hash="et",
            tester_role="11-test-engineer",
            total_tests=5,
            passed_tests=5,
            failed_tests=0,
        )
        complete, msg, _ = self.service.verify_governance_chain(wid, is_security_required=True)
        self.assertFalse(complete)
        self.assertIn("Missing QAReceipt", msg)

        # 5. Add QA
        self.service.record_qa(
            work_item_id=wid,
            project_id="proj-1",
            agent_id="12-qa-engineer",
            stage=LifecycleStage.QA_VALIDATION,
            instruction_hash="iq",
            evidence_hash="eq",
            qa_role="12-qa-engineer",
            scenarios_verified=3,
            bdd_exit_code=0,
            verdict="APPROVED",
        )

        # Now complete chain
        complete, msg, chain = self.service.verify_governance_chain(wid, is_security_required=True)
        self.assertTrue(complete)
        self.assertEqual(chain["status"], "COMPLETE")

    def test_8_stage_evidence_locality_no_future_dependencies(self):
        """8. No stage requires future-stage receipts."""
        # STAGE_RECEIPT_REQUIREMENTS must only reference local receipts
        for stage, req in STAGE_RECEIPT_REQUIREMENTS.items():
            if stage == LifecycleStage.IMPLEMENTATION:
                self.assertEqual(req.required_receipt_types, ["EXECUTION"])
            elif stage == LifecycleStage.CODE_REVIEW:
                self.assertEqual(req.required_receipt_types, ["REVIEW"])
            elif stage == LifecycleStage.SECURITY_REVIEW:
                self.assertEqual(req.required_receipt_types, ["SECURITY"])
            elif stage == LifecycleStage.TEST_VALIDATION:
                self.assertEqual(req.required_receipt_types, ["TEST"])
            elif stage == LifecycleStage.QA_VALIDATION:
                self.assertEqual(req.required_receipt_types, ["QA"])
            elif stage == LifecycleStage.GOVERNANCE_RELEASE:
                self.assertEqual(req.required_receipt_types, ["GOVERNANCE"])

    def test_9_sod_normalized_identity_blocks_dispatch_suffix_bypass(self):
        """9. Same logical agent cannot bypass SoD using another dispatch suffix."""
        self.service.record_execution(
            work_item_id="wi-109",
            project_id="proj-1",
            agent_id="06-software-engineer#dispatch-1",
            stage=LifecycleStage.IMPLEMENTATION,
            instruction_hash="i9",
            evidence_hash="e9",
            files_modified=["z.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ z",
        )

        # Try to review using same logical agent with different dispatch ID
        with self.assertRaises(SoDViolationError):
            self.service.record_review(
                work_item_id="wi-109",
                project_id="proj-1",
                agent_id="06-software-engineer#dispatch-2",  # Attempted bypass!
                stage=LifecycleStage.CODE_REVIEW,
                instruction_hash="ir9",
                evidence_hash="er9",
                reviewer_role="09-code-reviewer",
                verdict="APPROVED",
            )


if __name__ == "__main__":
    unittest.main()
