import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

from auto_skill_learner import AutoSkillLearner


class AutoSkillLearnerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.squad_root = Path(self.temp_dir.name)
        (self.squad_root / "work" / "TASK-TEST").mkdir(parents=True)
        (self.squad_root / "skills" / "discovery" / "intake").mkdir(parents=True)
        self.learner = AutoSkillLearner(self.squad_root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_synthesize_and_deposit_skill(self):
        """Synthesize a skill from work item and verify agentskills.io frontmatter format."""
        draft = self.learner.synthesize_skill_from_work_item(
            work_item_id="TASK-TEST",
            skill_name="fastapi-jwt-auth",
            target_persona="software-engineer",
            summary_text="Implemented JWT Bearer token authentication with OAuth2PasswordBearer.",
        )

        self.assertEqual(draft.skill_name, "fastapi-jwt-auth")
        self.assertIn("standard: agentskills.io", draft.raw_markdown)
        self.assertIn("target_persona: software-engineer", draft.raw_markdown)

        deposited_path = self.learner.deposit_in_intake(draft)
        self.assertTrue(deposited_path.is_file())
        self.assertIn("Fastapi Jwt Auth", deposited_path.read_text(encoding="utf-8"))

    def test_hermes_skill_linter(self):
        """Verify Hermes-style hardline linter on valid and invalid SKILL.md markdown."""
        valid_md = """---
name: my-sample-skill
description: Clean and concise operational skill for testing.
version: 1.0.0
---

# My Sample Skill
Use native tool `view_file` to inspect paths.
"""
        findings = self.learner.lint_skill_markdown(valid_md, expected_slug="my-sample-skill")
        self.assertEqual(len(findings), 0)

        invalid_md = """---
name: wrong-name
description: A magical and revolutionary tool.
---
Run cat and grep on files.
"""
        bad_findings = self.learner.lint_skill_markdown(invalid_md, expected_slug="correct-name")
        self.assertTrue(any(f.rule_id == "NAME_DIRECTORY_MISMATCH" for f in bad_findings))
        self.assertTrue(any(f.rule_id == "MARKETING_LANGUAGE" for f in bad_findings))
        self.assertTrue(any(f.rule_id == "SHELL_UTIL_PROSE" for f in bad_findings))

    def test_prime_refine_heuristics(self):
        """Verify trajectory refinement extracting corrective rules."""
        rules = self.learner.refine_persona_heuristics("TASK-TEST", "software-engineer")
        self.assertGreaterEqual(len(rules), 1)

    def test_boostprompt_prompt_eval(self):
        """Verify BoostPrompt quality index computation."""
        good_prompt = """
## Objective
Deliver verified endpoint implementation.

## Ground Truth
Per docs/architecture.md §3.

## Anti-Fabrication
Do not invent anything. EMPTY if empty, NOT FOUND if missing.

## Boundaries
Read-only paths: src/core. Writable: src/api.
"""
        report = self.learner.evaluate_prompt_quality(good_prompt)
        self.assertGreaterEqual(report.overall_score, 80.0)

    def test_skill_curator_promotion(self):
        """Verify promoting a skill from intake to engineering domain."""
        draft = self.learner.synthesize_skill_from_work_item(
            work_item_id="TASK-TEST",
            skill_name="code-cleanup-flow",
            target_persona="software-engineer",
        )
        self.learner.deposit_in_intake(draft)
        success, msg = self.learner.promote_skill("code-cleanup-flow", "engineering")
        self.assertTrue(success)
        self.assertTrue((self.squad_root / "skills" / "engineering" / "code-cleanup-flow" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
