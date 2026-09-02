import json
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations" / "experimental"))

import gitingest
import procedural_skill_engine
import prompt_quality_optimizer
import toon
from procedural_skill_engine import ProceduralSkillEngine
from prompt_quality_optimizer import PromptQualityOptimizer
from trajectory_refinement_engine import (
    EpisodeTrace,
    StepTrace,
    TrajectoryErrorCode,
    TrajectoryRefinementEngine,
)


class TestGitIngestCompleteCoverage:
    def test_filters_exclusions_includes_and_extensions(self, tmp_path):
        (tmp_path / "keep.py").write_text("print('yes')\n", encoding="utf-8")
        (tmp_path / "skip.md").write_text("skip", encoding="utf-8")
        excluded = tmp_path / "private"
        excluded.mkdir()
        (excluded / "hidden.py").write_text("hidden", encoding="utf-8")
        (tmp_path / "folder").mkdir()

        result = gitingest.ingest(
            tmp_path,
            include=["*.py"],
            exclude=["private"],
            extensions=["py"],
        )

        assert "keep.py" in result
        assert "skip.md" not in result
        assert "hidden.py" not in result

    def test_omits_preview_for_whitespace_file(self, tmp_path):
        (tmp_path / "blank.txt").write_text("   \n", encoding="utf-8")
        result = gitingest.ingest(tmp_path)
        assert "blank.txt" in result
        assert "preview:" not in result

    def test_ignores_file_read_errors(self, tmp_path):
        bad = tmp_path / "bad.txt"
        bad.write_text("data", encoding="utf-8")
        original = Path.read_text

        def fail_for_bad(path, *args, **kwargs):
            if path == bad:
                raise OSError("unreadable")
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", fail_for_bad):
            result = gitingest.ingest(tmp_path)
        assert "files=1" in result
        assert "- bad.txt" not in result

    def test_truncates_to_token_budget(self, tmp_path):
        (tmp_path / "large.txt").write_text("x" * 500, encoding="utf-8")
        result = gitingest.ingest(tmp_path, max_tokens=5)
        assert result.endswith("\n... [truncated by gitingest]")
        assert result.startswith("# git")


class TestProceduralSkillEngineCompleteCoverage:
    engine = ProceduralSkillEngine()

    def test_format_defaults_and_slug_normalization(self):
        result = self.engine.format_as_agentskill(" My Skill! ", "Useful", "Run it")
        assert "name: my-skill" in result
        assert "  - run_command" in result
        assert "# My Skill" in result

    def test_lint_rejects_missing_frontmatter(self):
        findings = self.engine.lint_skill_content("# body")
        assert [f.rule_id for f in findings] == ["MISSING_FRONTMATTER"]

    def test_lint_rejects_non_mapping_yaml(self):
        findings = self.engine.lint_skill_content("---\n- item\n---\n")
        assert [f.rule_id for f in findings] == ["INVALID_YAML"]

    def test_lint_reports_yaml_parser_error(self):
        findings = self.engine.lint_skill_content("---\nname: [\n---\n")
        assert [f.rule_id for f in findings] == ["YAML_PARSE_ERROR"]

    def test_lint_covers_all_findings_skips_and_bom(self):
        content = (
            "\ufeff---\n"
            "name: wrong\n"
            "description: revolutionary and magical\n"
            "---\n"
            "# cat in heading\n"
            "```cat\n"
            "Use grep here\n"
            "Use ls tool instead of shell\n"
        )
        findings = self.engine.lint_skill_content(content, expected_slug="expected")
        ids = [f.rule_id for f in findings]
        assert "NAME_DIRECTORY_MISMATCH" in ids
        assert ids.count("MARKETING_LANGUAGE") == 2
        assert ids.count("SHELL_UTIL_PROSE") == 1

    def test_lint_reports_each_missing_required_field(self):
        findings = self.engine.lint_skill_content("---\nversion: 1\n---\n")
        assert {f.rule_id for f in findings} == {"MISSING_NAME", "MISSING_DESCRIPTION"}

    def test_ast_audit_returns_empty_for_invalid_paths_and_syntax(self, tmp_path):
        assert self.engine.audit_python_script_ast(tmp_path / "missing.py") == []
        text = tmp_path / "file.txt"
        text.write_text("hello", encoding="utf-8")
        assert self.engine.audit_python_script_ast(text) == []
        broken = tmp_path / "broken.py"
        broken.write_text("def broken(:", encoding="utf-8")
        assert self.engine.audit_python_script_ast(broken) == []

    def test_ast_audit_detects_every_dynamic_pattern(self, tmp_path):
        script = tmp_path / "unsafe.py"
        script.write_text(
            "import importlib\n"
            "import importlib.util\n"
            "import os\n"
            "from importlib import import_module\n"
            "from importlib.metadata import version\n"
            "from os import path\n"
            "name = 'os'\n"
            "attr = 'path'\n"
            "importlib.import_module(name)\n"
            "__import__(name)\n"
            "__import__('os')\n"
            "getattr(object(), attr)\n"
            "getattr(object(), 'fixed')\n"
            "object().__dict__[attr]\n"
            "object().__dict__['fixed']\n",
            encoding="utf-8",
        )
        findings = self.engine.audit_python_script_ast(script)
        ids = [finding.pattern_id for finding in findings]
        assert ids.count("importlib_import") == 4
        assert "dynamic_import" in ids
        assert "dynamic_import_computed" in ids
        assert "dynamic_getattr" in ids
        assert "dict_access" in ids


class TestPromptQualityOptimizerCompleteCoverage:
    def test_evaluate_prompt_applies_every_heuristic_penalty(self):
        score = PromptQualityOptimizer().evaluate_prompt("vague request")
        assert score.overall_score == 45.0
        assert (score.objective_clarity, score.ground_truth_score) == (10.0, 10.0)
        assert (score.anti_fabrication_score, score.boundary_score) == (10.0, 15.0)
        assert len(score.suggestions) == 4

    def test_evaluate_prompt_awards_full_score(self):
        prompt = "Mission. Ground Truth standards docs/x. Do not invent; EMPTY. Boundaries read-only scope."
        score = PromptQualityOptimizer().evaluate_prompt(prompt)
        assert score.overall_score == 100.0
        assert score.suggestions == []

    def test_vendor_evaluator_success_path(self, tmp_path):
        vendor_src = tmp_path / "integrations" / "vendor" / "boostprompt" / "src"
        vendor_src.mkdir(parents=True)
        quality = types.ModuleType("boostprompt.services.prompt_quality")
        schemas = types.ModuleType("boostprompt.models.schemas")

        class Evaluator:
            def evaluate(self, **kwargs):
                return types.SimpleNamespace(prompt_readiness=True)

        quality.PromptQualityEvaluator = Evaluator
        schemas.DiscoveryMode = types.SimpleNamespace(ESTRUTURACAO_PROMPT_FINAL="mode")
        modules = {
            "boostprompt": types.ModuleType("boostprompt"),
            "boostprompt.services": types.ModuleType("boostprompt.services"),
            "boostprompt.services.prompt_quality": quality,
            "boostprompt.models": types.ModuleType("boostprompt.models"),
            "boostprompt.models.schemas": schemas,
        }
        with (
            mock.patch.object(prompt_quality_optimizer, "ROOT", tmp_path),
            mock.patch.object(prompt_quality_optimizer, "sys", sys, create=True),
            mock.patch.dict(sys.modules, modules),
        ):
            score = PromptQualityOptimizer().evaluate_prompt(
                "Mission ground truth anti-fabrication boundaries"
            )
        assert score.overall_score == 100.0
        assert str(vendor_src) in sys.path
        sys.path.remove(str(vendor_src))

    def test_vendor_path_already_loaded_and_unready_result(self, tmp_path):
        vendor_src = tmp_path / "integrations" / "vendor" / "boostprompt" / "src"
        vendor_src.mkdir(parents=True)
        quality = types.ModuleType("boostprompt.services.prompt_quality")
        schemas = types.ModuleType("boostprompt.models.schemas")

        class Evaluator:
            def evaluate(self, **kwargs):
                return None

        quality.PromptQualityEvaluator = Evaluator
        schemas.DiscoveryMode = types.SimpleNamespace(ESTRUTURACAO_PROMPT_FINAL="mode")
        modules = {
            "boostprompt": types.ModuleType("boostprompt"),
            "boostprompt.services": types.ModuleType("boostprompt.services"),
            "boostprompt.services.prompt_quality": quality,
            "boostprompt.models": types.ModuleType("boostprompt.models"),
            "boostprompt.models.schemas": schemas,
        }
        sys.path.insert(0, str(vendor_src))
        try:
            with (
                mock.patch.object(prompt_quality_optimizer, "ROOT", tmp_path),
                mock.patch.object(prompt_quality_optimizer, "sys", sys, create=True),
                mock.patch.dict(sys.modules, modules),
            ):
                score = PromptQualityOptimizer().evaluate_prompt(
                    "Mission ground truth anti-fabrication boundaries"
                )
        finally:
            sys.path.remove(str(vendor_src))
        assert score.overall_score == 100.0

    def test_optimize_briefing_strips_inputs_and_builds_guardrails(self):
        result = PromptQualityOptimizer().optimize_briefing(
            "testing", " objective ", " truth ", " scope ", " method "
        )
        assert result["role"] == "You are a dedicated specialist in testing."
        assert result["objective"] == "objective"
        assert result["ground_truth"] == "truth"
        assert result["scope"] == "scope"
        assert result["method"] == "method"
        assert "Do not invent" in result["anti_fabrication"]
        assert "Read-only" in result["boundaries"]


class TestToonCompleteCoverage:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, "null"),
            (True, "true"),
            (False, "false"),
            (2, "2"),
            (2.5, "2.5"),
            ("plain", "plain"),
            ({"a": 1, "b": False}, "{a=1; b=false}"),
            ([1, None, "x"], "[1, null, x]"),
        ],
    )
    def test_dumps_values(self, value, expected):
        assert toon._to_value(value) == expected

    def test_dumps_top_level_list_and_scalar(self):
        class Custom:
            def __str__(self):
                return "custom"

        assert toon.dumps([1, "x"]) == "- 1\n- x"
        assert toon.dumps(Custom()) == "custom"
        assert toon._to_value(Custom()) == "custom"

    def test_loads_empty_skips_lines_and_parses_all_types(self):
        assert toon.loads("  ") == {}
        parsed = toon.loads(
            "- ignored\n"
            "without equals\n"
            "none=null\ntrue=true\nfalse=false\n"
            "empty=[]\nlist=[1, 2.5, false]\n"
            "dict={a=1; malformed; b=true}\n"
            'quoted="hello"\ninteger=3\nfloat=1.5\nword=text\n'
        )
        assert parsed == {
            "none": None,
            "true": True,
            "false": False,
            "empty": [],
            "list": [1, 2.5, False],
            "dict": {"a": 1, "b": True},
            "quoted": "hello",
            "integer": 3,
            "float": 1.5,
            "word": "text",
        }

    def test_optimize_skill_metadata_defaults_and_values(self):
        result = toon.loads(toon.optimize_skill_metadata({"name": "skill"}))
        assert result == {
            "name": "skill",
            "path": None,
            "domain": None,
            "assigned_to": [],
            "load": "on-demand",
        }


class TestTrajectoryRefinementEngineCompleteCoverage:
    engine = TrajectoryRefinementEngine()

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("file not found", TrajectoryErrorCode.FILE_NOT_FOUND),
            ("no such file", TrajectoryErrorCode.FILE_NOT_FOUND),
            ("syntax invalid", TrajectoryErrorCode.SYNTAX_ERROR),
            ("parse failed", TrajectoryErrorCode.SYNTAX_ERROR),
            ("indentation issue", TrajectoryErrorCode.SYNTAX_ERROR),
            ("permission denied", TrajectoryErrorCode.SCOPE_VIOLATION),
            ("unauthorized", TrajectoryErrorCode.SCOPE_VIOLATION),
            ("outside scope", TrajectoryErrorCode.SCOPE_VIOLATION),
            ("gate rejected", TrajectoryErrorCode.GATE_REJECTED),
            ("rate limit", TrajectoryErrorCode.RATE_LIMITED),
            ("status 429", TrajectoryErrorCode.RATE_LIMITED),
            ("timeout", TrajectoryErrorCode.TIMEOUT),
            ("something else", TrajectoryErrorCode.UNKNOWN_ERROR),
        ],
    )
    def test_classify_every_error(self, message, expected):
        assert self.engine.classify_error(message) is expected

    def test_distills_every_rule_and_deduplicates(self):
        failed = [
            {"action": "read", "error": "not found"},
            {"action": "read", "error": "not found"},
            {"action": "write", "error": "syntax error"},
            {"action": "edit", "error": "permission denied"},
            {"action": "approve", "error": "gate rejected"},
            {"action": "call", "error": "rate limit " + "x" * 100},
        ]
        rules = self.engine.distill_refinement_rules(failed)
        assert len(rules) == 5
        assert "list_dir" in rules[0]
        assert "verificação estática" in rules[1]
        assert "diretórios autorizados" in rules[2]
        assert "aprovação de gate" in rules[3]
        assert rules[4].startswith("Atenção ao passo `call`")

    def test_formats_episode_with_and_without_error_codes(self):
        episode = EpisodeTrace("ep", "work", "agent")
        episode.final_status = "failed"
        episode.steps = [
            StepTrace(1, "agent", "read", {}, "ok", True, duration_ms=1.5),
            StepTrace(
                2,
                "agent",
                "write",
                {},
                "bad",
                False,
                TrajectoryErrorCode.SYNTAX_ERROR,
                2.5,
            ),
        ]
        text = self.engine.format_trajectory_jsonl(episode)
        assert text.endswith("\n")
        record = json.loads(text)
        assert record["steps_count"] == 2
        assert record["steps"][0]["error_code"] is None
        assert record["steps"][1]["error_code"] == "syntax_error"
