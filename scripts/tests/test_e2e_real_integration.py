import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "integrations" / "experimental"))
sys.path.insert(0, str(ROOT / "scripts"))

from procedural_skill_engine import ProceduralSkillEngine
from trajectory_refinement_engine import TrajectoryRefinementEngine, EpisodeTrace, StepTrace, TrajectoryErrorCode
from prompt_quality_optimizer import PromptQualityOptimizer
from local_agent_db import LocalAgentDB


class RealE2EIntegrationTests(unittest.TestCase):
    """Testes E2E reais e sem mocks das ferramentas e banco de dados."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_squad.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_real_database_operations(self):
        """Testa operações reais de banco de dados SQLite (DDL, DML, consultas via AST e métricas)."""
        db = LocalAgentDB(db_path=self.db_path, project_id="test-project")

        # Cria um arquivo Python real temporário para indexação AST
        sample_code = """
class AuthService:
    \"\"\"Serviço central de autenticação.\"\"\"
    def login_user(self, username, password):
        \"\"\"Executa login.\"\"\"
        return True
"""
        code_file = Path(self.temp_dir.name) / "sample_auth.py"
        code_file.write_text(sample_code, encoding="utf-8")

        # Indexa símbolos reais
        symbols = db.index_python_file(code_file)
        self.assertGreaterEqual(len(symbols), 2)

        # Insere métricas de tokens reais
        db.record_token_metrics("TASK-100", "06-software-engineer", "step_impl", prompt_tokens=1500, completion_tokens=350, cost_usd=0.0042)

        # Registra log de trajetória real
        db.log_trajectory("smoke-test", "06-software-engineer", "TASK-100", status="converged", steps_count=3, tool_calls_count=5, duration_seconds=1.2, details={"info": "ok"})

        # Registra voto de quórum real
        db.record_quorum_vote("TASK-100", "G4-code-security", "09-code-reviewer", vote="pass", weight=1.0, rationale="Código limpo e testado")

        # Validações reais no SQLite
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM symbols")
        self.assertGreaterEqual(cur.fetchone()[0], 2)

        cur.execute("SELECT prompt_tokens, cost_usd FROM token_metrics WHERE work_item_id='TASK-100'")
        row = cur.fetchone()
        self.assertEqual(row[0], 1500)
        self.assertAlmostEqual(row[1], 0.0042)

        cur.execute("SELECT status, steps_count FROM trajectory_logs WHERE task_id='TASK-100'")
        log_row = cur.fetchone()
        self.assertEqual(log_row[0], "converged")
        self.assertEqual(log_row[1], 3)

        cur.execute("SELECT voter_agent, vote FROM quorum_votes WHERE work_item_id='TASK-100'")
        vote_row = cur.fetchone()
        self.assertEqual(vote_row[0], "09-code-reviewer")
        self.assertEqual(vote_row[1], "pass")
        conn.close()

    def test_real_skill_creation_lint_and_ast_audit(self):
        """Testa o ciclo de vida real de síntese de skill, linter e AST scanner de segurança em disco."""
        engine = ProceduralSkillEngine()
        skill_dir = Path(self.temp_dir.name) / "fastapi-jwt-auth"
        skill_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        # Gera SKILL.md real
        skill_content = engine.format_as_agentskill(
            name="fastapi-jwt-auth",
            description="Configuração de autenticação JWT em APIs FastAPI.",
            instructions="1. Crie o schema Pydantic.\n2. Utilize `view_file` para inspecionar endpoints.",
            allowed_tools=["view_file", "replace_file_content"],
            author="software-engineer"
        )
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(skill_content, encoding="utf-8")

        # Executa linter real
        findings = engine.lint_skill_content(skill_content, expected_slug="fastapi-jwt-auth")
        self.assertEqual(len(findings), 0)

        # Cria script Python real com importlib para testar auditoria AST real
        script_file = scripts_dir / "helper.py"
        script_file.write_text("import importlib\ndef load(m):\n    return importlib.import_module(m)\n", encoding="utf-8")

        ast_findings = engine.audit_python_script_ast(script_file)
        self.assertTrue(len(ast_findings) >= 2)
        pattern_ids = [f.pattern_id for f in ast_findings]
        self.assertIn("dynamic_import", pattern_ids)
        self.assertIn("importlib_import", pattern_ids)

    def test_real_trajectory_refinement(self):
        """Testa o motor de refinamento com erros reais capturados."""
        engine = TrajectoryRefinementEngine()
        failed_steps = [
            {"action": "replace_file_content", "error": "file not found: src/nonexistent.py"},
            {"action": "save_file", "error": "SyntaxError: unmatched ')' on line 22"},
            {"action": "gate_evaluation", "error": "Gate G4 rejected: missing automated tests"}
        ]
        rules = engine.distill_refinement_rules(failed_steps)
        self.assertEqual(len(rules), 3)
        self.assertTrue(any("list_dir" in r for r in rules))
        self.assertTrue(any("sintaxe" in r for r in rules))
        self.assertTrue(any("G4" in r or "evidência" in r for r in rules))

    def test_real_prompt_quality_evaluation_on_actual_agent_prompt(self):
        """Testa o avaliador de qualidade de prompt contra um arquivo PROMPT.md real de agente do squad."""
        optimizer = PromptQualityOptimizer()
        agent_prompt_path = ROOT / "agents" / "06-software-engineer" / "PROMPT.md"
        self.assertTrue(agent_prompt_path.exists())

        prompt_text = agent_prompt_path.read_text(encoding="utf-8")
        score = optimizer.evaluate_prompt(prompt_text)
        self.assertGreaterEqual(score.overall_score, 70.0)


if __name__ == "__main__":
    unittest.main()
