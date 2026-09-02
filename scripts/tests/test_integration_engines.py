import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "integrations" / "experimental"))

from gitingest import ingest as gitingest_ingest
from toon import dumps as toon_dumps, loads as toon_loads


class GitIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "main.py").write_text("print('hello')\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# Repo\n", encoding="utf-8")
        sub = self.repo / "pkg"
        sub.mkdir()
        (sub / "utils.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_ingest_returns_non_empty_string(self):
        result = gitingest_ingest(self.repo)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_ingest_contains_files_count(self):
        result = gitingest_ingest(self.repo)
        self.assertIn("files=", result)

    def test_ingest_respects_exclude(self):
        result = gitingest_ingest(self.repo, exclude={"pkg"})
        self.assertNotIn("utils.py", result)

    def test_ingest_respects_extensions(self):
        result = gitingest_ingest(self.repo, extensions={".py"})
        self.assertIn("main.py", result)
        self.assertNotIn("README.md", result)


class ToonTests(unittest.TestCase):
    def test_roundtrip_dict(self):
        original = {"name": "agents-squad", "active": True, "count": 36, "tags": ["a", "b"]}
        text = toon_dumps(original)
        parsed = toon_loads(text)
        self.assertEqual(parsed["name"], original["name"])
        self.assertEqual(parsed["active"], original["active"])
        self.assertEqual(parsed["count"], original["count"])
        self.assertEqual(parsed["tags"], original["tags"])

    def test_null_and_bool_values(self):
        original = {"missing": None, "flag": False, "value": "ok"}
        text = toon_dumps(original)
        parsed = toon_loads(text)
        self.assertIsNone(parsed["missing"])
        self.assertFalse(parsed["flag"])
        self.assertEqual(parsed["value"], "ok")

    def test_empty_input(self):
        self.assertEqual(toon_loads(""), {})

    def test_list_dumps(self):
        original = ["x", "y", 1]
        text = toon_dumps(original)
        self.assertIn("x", text)
        self.assertIn("y", text)


if __name__ == "__main__":
    unittest.main()
