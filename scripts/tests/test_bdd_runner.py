import json
from pathlib import Path

from scripts.bdd_runner import run_bdd_evaluation

ROOT = Path(__file__).resolve().parents[2]


def test_bdd_runner_fails_on_empty_specs(tmp_path):
    work_item = tmp_path / "US-EMPTY"
    work_item.mkdir()
    (work_item / "status.yaml").write_text("id: US-EMPTY\n", encoding="utf-8")
    
    evidence = run_bdd_evaluation(work_item)
    assert evidence["passed"] is False
    assert evidence["exit_code"] == 1
    assert (work_item / "evaluation/bdd.json").is_file()


def test_bdd_runner_succeeds_on_valid_feature(tmp_path):
    work_item = tmp_path / "US-VALID"
    specs = work_item / "specs"
    specs.mkdir(parents=True)
    (work_item / "status.yaml").write_text("id: US-VALID\n", encoding="utf-8")
    
    feature_content = """Feature: Login de Usuario
  @AC-01
  Scenario: Login com sucesso
    Given que o usuario existe
    When ele informa credenciais validas
    Then o token JWT eh retornado
"""
    (specs / "login.feature").write_text(feature_content, encoding="utf-8")
    
    evidence = run_bdd_evaluation(work_item)
    assert evidence["passed"] is True
    assert evidence["exit_code"] == 0
    assert "login.feature" in str(evidence["file_hashes"])
    
    loaded = json.loads((work_item / "evaluation/bdd.json").read_text(encoding="utf-8"))
    assert loaded["passed"] is True
