"""Tests for R12 Security, QA, and Quality Verification Rules.

Strictly stdlib-only.
"""

import sqlite3
import unittest

from scripts.domain.common import ValidationError
from scripts.runtime.execution.errors import InvalidEvidenceError
from scripts.runtime.execution.repository import ExecutionReceiptRepository
from scripts.runtime.execution.service import ExecutionReceiptService


class TestR12SecurityQAEnforcement(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.repo = ExecutionReceiptRepository(self.conn)
        self.service = ExecutionReceiptService(repository=self.repo)

        self.service.record_execution(
            work_item_id="feat-300",
            project_id="proj-alpha",
            agent_id="06-software-engineer",
            stage="IMPLEMENTATION",
            instruction_hash="inst-hash",
            evidence_hash="ev-hash",
            files_modified=["api.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ def api(): pass",
        )

    def tearDown(self):
        self.conn.close()

    def test_security_critical_vulnerability_blocks_approval(self):
        # Domain contract itself prevents critical_count > 0 with APPROVED verdict
        with self.assertRaises((ValidationError, InvalidEvidenceError)):
            self.service.record_security(
                work_item_id="feat-300",
                project_id="proj-alpha",
                agent_id="10-security-reviewer",
                stage="SECURITY_REVIEW",
                instruction_hash="inst-sec",
                evidence_hash="ev-sec",
                security_role="10-security-reviewer",
                critical_count=2,
                verdict="APPROVED",
            )

    def test_review_changes_requested_rejected_by_validator(self):
        with self.assertRaises(InvalidEvidenceError):
            self.service.record_review(
                work_item_id="feat-300",
                project_id="proj-alpha",
                agent_id="09-code-reviewer",
                stage="CODE_REVIEW",
                instruction_hash="inst-rev",
                evidence_hash="ev-rev",
                reviewer_role="09-code-reviewer",
                verdict="CHANGES_REQUESTED",
            )

    def test_test_receipt_with_failures_rejected(self):
        with self.assertRaises(InvalidEvidenceError):
            self.service.record_test(
                work_item_id="feat-300",
                project_id="proj-alpha",
                agent_id="11-test-engineer",
                stage="TEST_VALIDATION",
                instruction_hash="inst-tst",
                evidence_hash="ev-tst",
                tester_role="11-test-engineer",
                total_tests=10,
                passed_tests=8,
                failed_tests=2,
            )

    def test_qa_receipt_with_nonzero_exit_code_rejected(self):
        with self.assertRaises(InvalidEvidenceError):
            self.service.record_qa(
                work_item_id="feat-300",
                project_id="proj-alpha",
                agent_id="12-qa-engineer",
                stage="QA_VALIDATION",
                instruction_hash="inst-qa",
                evidence_hash="ev-qa",
                qa_role="12-qa-engineer",
                scenarios_verified=5,
                bdd_exit_code=1,
                verdict="APPROVED",
            )


if __name__ == "__main__":
    unittest.main()
