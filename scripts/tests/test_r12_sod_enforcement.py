"""Tests for R12 Segregation of Duties (SoD) enforcement across all verification stages.

Strictly stdlib-only.
"""

import sqlite3
import unittest

from scripts.runtime.execution.errors import SoDViolationError
from scripts.runtime.execution.repository import ExecutionReceiptRepository
from scripts.runtime.execution.service import ExecutionReceiptService


class TestR12SoDEnforcement(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.repo = ExecutionReceiptRepository(self.conn)
        self.service = ExecutionReceiptService(repository=self.repo)

        # Register an execution by 06-software-engineer
        self.service.record_execution(
            work_item_id="feat-200",
            project_id="proj-alpha",
            agent_id="06-software-engineer",
            stage="IMPLEMENTATION",
            instruction_hash="inst-hash",
            evidence_hash="ev-hash",
            files_modified=["core.py"],
            tests_executed=["pytest"],
            test_exit_code=0,
            diff_summary="+ pass",
        )

    def tearDown(self):
        self.conn.close()

    def test_author_cannot_review_own_code(self):
        with self.assertRaises(SoDViolationError):
            self.service.record_review(
                work_item_id="feat-200",
                project_id="proj-alpha",
                agent_id="06-software-engineer",  # Self-review!
                stage="CODE_REVIEW",
                instruction_hash="inst-rev",
                evidence_hash="ev-rev",
                reviewer_role="09-code-reviewer",
                verdict="APPROVED",
            )

    def test_author_cannot_security_scan_own_code(self):
        with self.assertRaises(SoDViolationError):
            self.service.record_security(
                work_item_id="feat-200",
                project_id="proj-alpha",
                agent_id="06-software-engineer",  # Self-scan!
                stage="SECURITY_REVIEW",
                instruction_hash="inst-sec",
                evidence_hash="ev-sec",
                security_role="10-security-reviewer",
                critical_count=0,
                verdict="APPROVED",
            )

    def test_author_cannot_test_own_code(self):
        with self.assertRaises(SoDViolationError):
            self.service.record_test(
                work_item_id="feat-200",
                project_id="proj-alpha",
                agent_id="06-software-engineer",  # Self-test!
                stage="TEST_VALIDATION",
                instruction_hash="inst-tst",
                evidence_hash="ev-tst",
                tester_role="11-test-engineer",
                total_tests=10,
                passed_tests=10,
                failed_tests=0,
            )

    def test_author_cannot_qa_approve_own_code(self):
        with self.assertRaises(SoDViolationError):
            self.service.record_qa(
                work_item_id="feat-200",
                project_id="proj-alpha",
                agent_id="06-software-engineer",  # Self-QA!
                stage="QA_VALIDATION",
                instruction_hash="inst-qa",
                evidence_hash="ev-qa",
                qa_role="12-qa-engineer",
                scenarios_verified=3,
                bdd_exit_code=0,
                verdict="APPROVED",
            )

    def test_independent_reviewers_pass_sod(self):
        # 09-code-reviewer reviews
        rev = self.service.record_review(
            work_item_id="feat-200",
            project_id="proj-alpha",
            agent_id="09-code-reviewer",
            stage="CODE_REVIEW",
            instruction_hash="inst-rev",
            evidence_hash="ev-rev",
            reviewer_role="09-code-reviewer",
            verdict="APPROVED",
        )
        self.assertEqual(rev.verdict, "APPROVED")

        # 10-security-reviewer scans
        sec = self.service.record_security(
            work_item_id="feat-200",
            project_id="proj-alpha",
            agent_id="10-security-reviewer",
            stage="SECURITY_REVIEW",
            instruction_hash="inst-sec",
            evidence_hash="ev-sec",
            security_role="10-security-reviewer",
            critical_count=0,
            verdict="APPROVED",
        )
        self.assertEqual(sec.verdict, "APPROVED")

        # 11-test-engineer tests
        tst = self.service.record_test(
            work_item_id="feat-200",
            project_id="proj-alpha",
            agent_id="11-test-engineer",
            stage="TEST_VALIDATION",
            instruction_hash="inst-tst",
            evidence_hash="ev-tst",
            tester_role="11-test-engineer",
            total_tests=5,
            passed_tests=5,
            failed_tests=0,
        )
        self.assertEqual(tst.failed_tests, 0)

        # 12-qa-engineer signs off
        qa = self.service.record_qa(
            work_item_id="feat-200",
            project_id="proj-alpha",
            agent_id="12-qa-engineer",
            stage="QA_VALIDATION",
            instruction_hash="inst-qa",
            evidence_hash="ev-qa",
            qa_role="12-qa-engineer",
            scenarios_verified=2,
            bdd_exit_code=0,
            verdict="APPROVED",
        )
        self.assertEqual(qa.verdict, "APPROVED")


if __name__ == "__main__":
    unittest.main()
