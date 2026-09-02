#!/usr/bin/env python3
"""
Simulação Completa do Projeto Fictício: App de Calculadora (US-CALCULATOR-APP-2026).
Demonstra a execução ponta a ponta de todos os agentes, contratos BDD/TDD,
arquitetura C4, gate de governança e persistência em banco SQLite.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_squad import AgentSquad, SquadError, read_yaml
from local_agent_db import LocalAgentDB
from tdd_evidence import evidence_digest, validate_tdd_cycle


def run_calculator_project():
    print("===============================================================")
    print("   INICIANDO PROJETO FICTÍCIO: APP DE CALCULADORA (US-CALCULATOR-APP-2026)")
    print("===============================================================\n")

    # 1. Isolamento de ambiente e banco de dados SQLite
    temp_dir = tempfile.TemporaryDirectory()
    work_dir = Path(temp_dir.name) / "work"
    work_dir.mkdir()
    db_path = Path(temp_dir.name) / "banco" / "squad.db"
    db_path.parent.mkdir()

    db = LocalAgentDB(db_path=db_path, project_id="calculator-app-2026")
    squad = AgentSquad(ROOT, project_name="calculator-app-2026", allow_legacy=True)

    # 2. Inicialização do Work Item
    item = squad.init_work_item("US-CALCULATOR-APP-2026", "medium", base=work_dir)
    print("🟢 [ORCHESTRATOR] Work Item Criado:", item.name)

    # -------------------------------------------------------------------------
    # FASE 1: LEVANTAMENTO DE REQUISITOS (01-requirements-analyst & 02-product-owner)
    # -------------------------------------------------------------------------
    (item / "specs" / "features").mkdir(parents=True, exist_ok=True)
    
    (item / "specs" / "features" / "calculator.feature").write_text("""Feature: Calculator Core Operations
  @AC-001
  Scenario: Basic arithmetic calculations
    Given problem statement defined for user arithmetic needs
    When user evaluates basic expressions
    Then correct mathematical output is calculated
""", encoding="utf-8")

    (item / "epic.md").write_text("""# US-CALCULATOR-APP-2026: Calculator Engine

- Problem: Users need a lightweight, accurate arithmetic calculation engine.
- Goal: Enable high-precision addition, subtraction, multiplication, and division operations.
- User Story INVEST: As a user, I want to execute calculations with input validation.
- Acceptance Criteria: Testable calculations with explicit error handling for division by zero.
- Priority: High value-prioritized core utility.
- Dependencies: Standard math library dependencies known.
""", encoding="utf-8")

    mem1 = squad.record_memory(
        item, "requirements-analyst",
        "Elicited INVEST user story and BDD Gherkin criteria for Calculator App.",
        "epic.md", kind="fact"
    )

    handoff_1 = squad.create_handoff(
        item, "requirements-analyst", "product-owner",
        "Requirements ready for G1 product gate validation.",
        ["epic.md"], ["epic.md"], f"memory/deltas/{mem1['id']}.yaml", next_gate="G1-product"
    )
    squad.ack_handoff(item, handoff_1["id"], "product-owner")

    g1_criteria = [(c, "pass") for c in squad.get_gate("G1-product")["criteria"]]
    g1_dec = squad.decide_gate(
        item, "G1-product", "product-owner", g1_criteria, ["epic.md"],
        human_approved_by="product-manager", human_evidence="epic.md"
    )
    print("✅ [GATE G1-PRODUCT] Aprovado pelo Product Owner (Decisão:", g1_dec["decision"] + ")")

    # -------------------------------------------------------------------------
    # FASE 2: DESENHO DE ARQUITETURA & ADR (04-solution-architect)
    # -------------------------------------------------------------------------
    arch_file = item / "specs" / "calculator-architecture.md"
    arch_file.write_text("""# Architecture & Design Spec: Calculator Engine (C4 Model)

## Component Contract
- `Calculator`: Stateless arithmetic processing engine.
- Methods: `add(a, b)`, `subtract(a, b)`, `multiply(a, b)`, `divide(a, b)`.
- ADR-001: Pure functional operations with zero external state dependencies.
- STRIDE Threat Model: Input sanitization to prevent expression injection.
""", encoding="utf-8")

    mem2 = squad.record_memory(
        item, "solution-architect",
        "ADR-001 recorded with stateless C4 model architecture and input threat model.",
        "specs/calculator-architecture.md", kind="decision"
    )

    handoff_2 = squad.create_handoff(
        item, "solution-architect", "delivery-orchestrator",
        "Architecture design ready for TDD implementation.",
        ["specs/calculator-architecture.md"], ["specs/calculator-architecture.md"],
        f"memory/deltas/{mem2['id']}.yaml", next_gate="G3-readiness"
    )
    squad.ack_handoff(item, handoff_2["id"], "delivery-orchestrator")
    print("✅ [ARCHITECTURE] C4 Model e ADR-001 validados por Solution Architect.")

    # -------------------------------------------------------------------------
    # FASE 3: IMPLEMENTAÇÃO TDD & CLEAN CODE (06-software-engineer)
    # -------------------------------------------------------------------------
    (item / "implementation").mkdir(exist_ok=True)
    (item / "tests").mkdir(exist_ok=True)
    (item / "evaluation" / "tdd").mkdir(parents=True, exist_ok=True)

    code_file = item / "implementation" / "calculator.py"
    test_file = item / "tests" / "test_calculator.py"

    code_content = """class Calculator:
    \"\"\"Engine de calculadora stateless de alta precisão.\"\"\"

    def add(self, a: float, b: float) -> float:
        return a + b

    def subtract(self, a: float, b: float) -> float:
        return a - b

    def multiply(self, a: float, b: float) -> float:
        return a * b

    def divide(self, a: float, b: float) -> float:
        if b == 0:
            raise ValueError("Divisão por zero não é permitida.")
        return a / b
"""

    test_content = """from implementation.calculator import Calculator
import pytest

def test_add():
    calc = Calculator()
    assert calc.add(10, 5) == 15

def test_subtract():
    calc = Calculator()
    assert calc.subtract(10, 4) == 6

def test_multiply():
    calc = Calculator()
    assert calc.multiply(3, 7) == 21

def test_divide():
    calc = Calculator()
    assert calc.divide(20, 4) == 5

def test_divide_by_zero():
    calc = Calculator()
    with pytest.raises(ValueError, match="Divisão por zero"):
        calc.divide(10, 0)
"""

    code_file.write_text(code_content, encoding="utf-8")
    test_file.write_text(test_content, encoding="utf-8")

    # Indexação de símbolos no Banco SQLite
    db.index_python_file(code_file, content=code_content)
    db.record_token_metrics(item.name, "software-engineer", "implement_tdd", 2100, 650, cost_usd=0.004)
    db.log_trajectory("CALCULATOR-BENCHMARK", "software-engineer", item.name, "pass", 3, 4, 8.2, {"clean_code": True})

    code_hash = hashlib.sha256(code_file.read_bytes()).hexdigest()
    test_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()
    hashes = {
        "implementation/calculator.py": code_hash,
        "tests/test_calculator.py": test_hash
    }

    # Evidências do Ciclo TDD (Red -> Green -> Refactor)
    red_ev = {"passed": False, "exit_code": 1, "file_hashes": hashes, "results": {"test_id": "test_calculator", "criterion_id": "AC-001"}}
    green_ev = {"passed": True, "exit_code": 0, "file_hashes": hashes, "results": {"test_id": "test_calculator", "criterion_id": "AC-001", "previous_digest": evidence_digest(red_ev)}}
    refactor_ev = {"passed": True, "exit_code": 0, "file_hashes": hashes, "results": {"test_id": "test_calculator", "criterion_id": "AC-001", "previous_digest": evidence_digest(green_ev)}}

    (item / "evaluation" / "tdd" / "red.json").write_text(json.dumps(red_ev), encoding="utf-8")
    (item / "evaluation" / "tdd" / "green.json").write_text(json.dumps(green_ev), encoding="utf-8")
    (item / "evaluation" / "tdd" / "refactor.json").write_text(json.dumps(refactor_ev), encoding="utf-8")

    assert validate_tdd_cycle(item / "evaluation" / "tdd")["approved"] is True
    print("✅ [TDD & CLEAN CODE] Ciclo Red -> Green -> Refactor validado com sucesso!")

    # -------------------------------------------------------------------------
    # FASE 4: EVIDÊNCIAS EXECUTÁVEIS E GATES DE REVISÃO (09-code-reviewer & 10-security-reviewer)
    # -------------------------------------------------------------------------
    now_iso = datetime.now(timezone.utc).isoformat()
    def make_ev(verifier: str, extra_results: dict | None = None):
        return {
            "schema_version": 1,
            "work_item": item.name,
            "verifier": verifier,
            "persona": "software-engineer",
            "command": ["pytest", "tests/test_calculator.py"],
            "exit_code": 0,
            "passed": True,
            "timestamp": now_iso,
            "file_hashes": hashes,
            "results": extra_results or {"status": "PASS"},
        }

    (item / "evaluation" / "clean-code.json").write_text(json.dumps(make_ev("clean-code")), encoding="utf-8")
    (item / "evaluation" / "unit-tests.json").write_text(json.dumps(make_ev("unit-tests")), encoding="utf-8")
    (item / "evaluation" / "security.json").write_text(json.dumps(make_ev("security")), encoding="utf-8")
    (item / "evaluation" / "bdd.json").write_text(json.dumps(make_ev("bdd")), encoding="utf-8")
    (item / "evaluation" / "regression.json").write_text(json.dumps(make_ev("regression")), encoding="utf-8")
    (item / "evaluation" / "coverage.json").write_text(json.dumps(make_ev("coverage", {
        "status": "PASS", "minimum_percent": 80, "total_percent": 100, "branch_percent": 100
    })), encoding="utf-8")

    (item / "reviews").mkdir(exist_ok=True)
    (item / "reviews" / "code_security_review.md").write_text("""# Code & Security Review Report

- Code Quality: 100% compliant with SOLID, single responsibility, no duplication.
- Security: Zero injection risks, input boundary checks present.
""", encoding="utf-8")

    (item / "validation").mkdir(exist_ok=True)
    (item / "validation" / "qa-report.md").write_text("""# QA Validation Report

- All unit, integration and BDD scenarios executed cleanly.
- Failure paths documented: Division by zero exception handling verified.
- Evidence complete.
""", encoding="utf-8")

    (item / "documentation").mkdir(exist_ok=True)
    (item / "documentation" / "delivery-ledger.md").write_text("""# Delivery Ledger & Traceability Log

- Item: US-CALCULATOR-APP-2026
- Status: Approved and verified for release.
- Traceability: Complete requirements-to-code traceability verified.
- Observability: Execution metrics logged in SQLite database.
- Rollout: Automated deployment ready with rollback verification.
- Approvals: Approved by code-reviewer, security-reviewer, and governance auditor.
""", encoding="utf-8")

    g4_criteria = [(c, "pass") for c in squad.get_gate("G4-code-security")["criteria"]]
    g4_dec = squad.decide_gate(
        item, "G4-code-security", "code-reviewer", g4_criteria,
        ["implementation/calculator.py", "tests/test_calculator.py", "reviews/code_security_review.md"],
        human_approved_by="tech-lead", human_evidence="reviews/code_security_review.md"
    )
    print("✅ [GATE G4-CODE-SECURITY] Aprovado por Code Reviewer & Security Reviewer!")

    g5_criteria = [(c, "pass") for c in squad.get_gate("G5-quality")["criteria"]]
    g5_dec = squad.decide_gate(
        item, "G5-quality", "qa-engineer", g5_criteria,
        ["validation/qa-report.md", "documentation/delivery-ledger.md"],
        human_approved_by="qa-lead", human_evidence="documentation/delivery-ledger.md"
    )
    print("✅ [GATE G5-QUALITY & GOVERNANCE] Aprovado por QA Engineer & Governance Auditor!")

    status = read_yaml(item / "status.yaml")
    symbols = db.search_symbols("Calculator") if hasattr(db, "search_symbols") else [s for s in db.index_python_file(code_file)]

    print("\n===============================================================")
    print("   RESUMO DA EXECUÇÃO DO PROJETO DA CALCULADORA")
    print("===============================================================")
    print("🔹 Work Item ID:        ", status["id"])
    print("🔹 Status Final:        ", status.get("state", "approved"))
    print("🔹 Classes Criadas:     ", [s.name for s in symbols])
    print("🔹 Cobertura de Testes:  100%")
    print("🔹 TDD Red-Green-Refactor: VALIDADO")
    print("🔹 Registros em Banco:   Indexado em squad.db (SQLite)")
    print("===============================================================\n")

    temp_dir.cleanup()


if __name__ == "__main__":
    run_calculator_project()
