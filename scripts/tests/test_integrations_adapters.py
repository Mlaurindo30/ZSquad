import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "integrations" / "experimental"))
sys.path.insert(0, str(ROOT / "integrations"))

from procedural_skill_engine import ProceduralSkillEngine
from trajectory_refinement_engine import TrajectoryRefinementEngine, TrajectoryErrorCode, EpisodeTrace, StepTrace
from blast_radius_analyzer import BlastRadiusAnalyzer
from codebase_knowledge_graph import CodebaseKnowledgeGraph
from code_health_analyzer import CodeHealthAnalyzer
from contextual_ast_chunker import ContextualASTChunker
from prompt_quality_optimizer import PromptQualityOptimizer
from sdlc_role_mapper import SDLCRoleMapper


class FunctionalEnginesTests(unittest.TestCase):
    def test_code_health_penalizes_very_large_and_complex_files(self):
        analyzer = CodeHealthAnalyzer()
        score = analyzer.compute_health_score(
            total_lines=501,
            max_complexity=11,
            docstring_cov=1.0,
            has_contract=True,
        )
        self.assertEqual(score, 5.0)

    def test_procedural_skill_engine_formatting_and_lint(self):
        engine = ProceduralSkillEngine()
        formatted = engine.format_as_agentskill("test-skill", "Test skill description", "Step 1\nStep 2", allowed_tools=["view_file"])
        self.assertIn("standard: agentskills.io", formatted)
        self.assertIn("allowed-tools:", formatted)
        self.assertIn("view_file", formatted)

        # Linting
        findings = engine.lint_skill_content(formatted, expected_slug="test-skill")
        self.assertEqual(len(findings), 0)

        # Invalid marketing prose
        bad_md = "---\nname: bad-skill\ndescription: A revolutionary magical tool.\n---\nRun cat and grep."
        bad_findings = engine.lint_skill_content(bad_md, expected_slug="other-skill")
        self.assertTrue(any(f.rule_id == "NAME_DIRECTORY_MISMATCH" for f in bad_findings))
        self.assertTrue(any(f.rule_id == "MARKETING_LANGUAGE" for f in bad_findings))
        self.assertTrue(any(f.rule_id == "SHELL_UTIL_PROSE" for f in bad_findings))

    def test_trajectory_refinement_engine(self):
        engine = TrajectoryRefinementEngine()
        self.assertEqual(engine.classify_error("file not found: src/main.py"), TrajectoryErrorCode.FILE_NOT_FOUND)
        self.assertEqual(engine.classify_error("SyntaxError: invalid syntax on line 10"), TrajectoryErrorCode.SYNTAX_ERROR)
        self.assertEqual(engine.classify_error("Gate G4 rejected by reviewer"), TrajectoryErrorCode.GATE_REJECTED)

        failed = [{"action": "edit", "error": "file not found in directory"}, {"action": "parse", "error": "syntax error on line 5"}]
        rules = engine.distill_refinement_rules(failed)
        self.assertEqual(len(rules), 2)
        self.assertTrue(any("list_dir" in r for r in rules))

        # Test episode formatting
        ep = EpisodeTrace("ep-001", "TASK-001", "software-engineer")
        ep.steps.append(StepTrace(1, "software-engineer", "edit", {"file": "a.py"}, "ok", True, duration_ms=12.5))
        jsonl = engine.format_trajectory_jsonl(ep)
        self.assertIn('"episode_id":"ep-001"', jsonl)

    def test_blast_radius_analyzer(self):
        analyzer = BlastRadiusAnalyzer()
        blast_data = {"target": "src/auth.py", "dependent_files_count": 2, "dependent_files": ["src/login.py", "src/admin.py"]}
        report = analyzer.format_blast_radius_report(blast_data)
        self.assertIn("src/auth.py", report)
        self.assertIn("src/login.py", report)

    def test_codebase_knowledge_graph(self):
        graph_builder = CodebaseKnowledgeGraph()
        symbols = [{"name": "UserService", "kind": "class", "file_path": "src/user.py"}]
        deps = [{"source_file": "src/app.py", "target_module": "src/user.py", "kind": "import"}]
        graph = graph_builder.build_graph_representation(symbols, deps)
        self.assertEqual(graph["nodes_count"], 1)
        self.assertEqual(graph["edges_count"], 1)

    def test_codebase_knowledge_graph_graphify_adapter(self):
        graph_builder = CodebaseKnowledgeGraph()
        symbols = [{"name": "UserService", "kind": "class", "file_path": "src/user.py"}]
        deps = [{"source_file": "src/app.py", "target_module": "src/user.py", "kind": "imports"}]
        extraction = graph_builder.to_graphify_extraction(symbols, deps)
        self.assertIn("nodes", extraction)
        self.assertIn("edges", extraction)
        self.assertTrue(any(n["id"] == "src/user.py:UserService" for n in extraction["nodes"]))
        self.assertTrue(any(e["relation"] == "imports" for e in extraction["edges"]))
        self.assertEqual(extraction["edges"][0]["confidence"], "EXTRACTED")

    def test_code_health_analyzer(self):
        analyzer = CodeHealthAnalyzer()
        score_high = analyzer.compute_health_score(total_lines=50, max_complexity=3, docstring_cov=1.0, has_contract=True)
        self.assertEqual(score_high, 10.0)

        score_low = analyzer.compute_health_score(total_lines=300, max_complexity=12, docstring_cov=0.4, has_contract=False)
        self.assertLess(score_low, 6.0)

    def test_contextual_ast_chunker(self):
        chunker = ContextualASTChunker()
        source = """
class DataModel:
    pass

def process_data():
    return 42
"""
        chunks = chunker.chunk_python_source(source, "model.py")
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["name"], "DataModel")
        self.assertEqual(chunks[1]["name"], "process_data")

    def test_prompt_quality_optimizer(self):
        optimizer = PromptQualityOptimizer()
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
        score = optimizer.evaluate_prompt(good_prompt)
        self.assertGreaterEqual(score.overall_score, 80.0)

    def test_sdlc_role_mapper(self):
        mapper = SDLCRoleMapper()
        roles = mapper.map_squad_agents_to_roles(["requirements-analyst", "software-engineer", "code-reviewer"])
        self.assertEqual(len(roles), 3)
        self.assertEqual(roles[0]["sdlc_role"], "analyst")
        self.assertEqual(roles[1]["sdlc_role"], "developer")

    def test_sdlc_role_mapper_canonical_discovery(self):
        mapper = SDLCRoleMapper()
        agents = mapper.get_canonical_sdlc_agents()
        self.assertIn("design", agents)
        self.assertIn("execution", agents)
        self.assertIn("qa", agents)
        self.assertEqual(len(agents), 7)


if __name__ == "__main__":
    unittest.main()

