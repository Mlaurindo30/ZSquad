"""Tests for R12 ExecutionReceipt creation, persistence, and retrieval.

Strictly stdlib-only.
"""

import sqlite3
import unittest

from scripts.domain.receipts import ExecutionReceipt, ReceiptType
from scripts.runtime.execution.errors import InvalidEvidenceError
from scripts.runtime.execution.repository import ExecutionReceiptRepository
from scripts.runtime.execution.service import ExecutionReceiptService


class TestR12ExecutionReceipts(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.repo = ExecutionReceiptRepository(self.conn)
        self.service = ExecutionReceiptService(repository=self.repo)

    def tearDown(self):
        self.conn.close()

    def test_record_execution_receipt_success(self):
        receipt = self.service.record_execution(
            work_item_id="feat-100",
            project_id="proj-alpha",
            agent_id="06-software-engineer",
            stage="IMPLEMENTATION",
            instruction_hash="inst-hash-123",
            evidence_hash="ev-hash-456",
            files_modified=["src/foo.py"],
            tests_executed=["pytest tests/test_foo.py"],
            test_exit_code=0,
            diff_summary="+ def foo(): return True",
        )
        self.assertEqual(receipt.receipt_type, ReceiptType.EXECUTION.value)
        self.assertEqual(receipt.work_item_id, "feat-100")
        self.assertEqual(receipt.agent_id, "06-software-engineer")
        self.assertEqual(receipt.test_exit_code, 0)

        # Retrieve
        fetched = self.repo.get_latest_execution_receipt("feat-100")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.receipt_id, receipt.receipt_id)
        self.assertEqual(fetched.files_modified, ["src/foo.py"])

    def test_record_execution_receipt_rejects_empty_diff(self):
        with self.assertRaises(InvalidEvidenceError):
            self.service.record_execution(
                work_item_id="feat-101",
                project_id="proj-alpha",
                agent_id="06-software-engineer",
                stage="IMPLEMENTATION",
                instruction_hash="inst-hash-123",
                evidence_hash="ev-hash-456",
                files_modified=["src/foo.py"],
                tests_executed=["pytest tests/test_foo.py"],
                test_exit_code=0,
                diff_summary="   ",
            )

    def test_record_execution_receipt_rejects_failing_test_exit_code(self):
        with self.assertRaises(InvalidEvidenceError):
            self.service.record_execution(
                work_item_id="feat-102",
                project_id="proj-alpha",
                agent_id="06-software-engineer",
                stage="IMPLEMENTATION",
                instruction_hash="inst-hash-123",
                evidence_hash="ev-hash-456",
                files_modified=["src/foo.py"],
                tests_executed=["pytest tests/test_foo.py"],
                test_exit_code=1,
                diff_summary="+ def foo(): fail",
            )


if __name__ == "__main__":
    unittest.main()
