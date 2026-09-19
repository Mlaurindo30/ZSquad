"""Tests for R12 Evidence Hashing and Integrity.

Strictly stdlib-only.
"""

import tempfile
from pathlib import Path
import unittest

from scripts.runtime.execution.evidence import (
    hash_evidence_payload,
    hash_file,
    verify_evidence_hash,
)


class TestR12EvidenceIntegrity(unittest.TestCase):
    def test_hash_evidence_payload_deterministic(self):
        payload1 = {"b": 2, "a": 1}
        payload2 = {"a": 1, "b": 2}
        hash1 = hash_evidence_payload(payload1)
        hash2 = hash_evidence_payload(payload2)
        self.assertEqual(hash1, hash2)
        self.assertTrue(verify_evidence_hash(payload1, hash1))

    def test_hash_string_normalized_line_endings(self):
        diff_crlf = "diff --git\r\n+line1\r\n"
        diff_lf = "diff --git\n+line1\n"
        self.assertEqual(hash_evidence_payload(diff_crlf), hash_evidence_payload(diff_lf))

    def test_hash_file_and_verification(self):
        with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as f:
            f.write("sample evidence content")
            f_path = Path(f.name)

        try:
            h = hash_file(f_path)
            self.assertIsInstance(h, str)
            self.assertEqual(len(h), 64)
            self.assertTrue(verify_evidence_hash("sample evidence content", h))
        finally:
            f_path.unlink()


if __name__ == "__main__":
    unittest.main()
