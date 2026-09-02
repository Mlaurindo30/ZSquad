#!/usr/bin/env python3
"""
Simulação Ponta a Ponta do Ciclo SDLC e Persistência no Agents Squad.
Excuta um projeto completo com todos os gates, handoffs, memória, evidências e banco de dados.
"""

from pathlib import Path
import json
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_squad import AgentSquad, SquadError
from local_agent_db import LocalAgentDB
from tdd_evidence import evidence_digest, validate_tdd_cycle


def run_e2e_simulation():
    print("=== STARTING END-TO-END SDLC SIMULATION ===")
    
    # 1. Prepare isolated workspace
    temp_dir = tempfile.TemporaryDirectory()
    work_dir = Path(temp_dir.name) / "work"
    work_dir.mkdir()
    db_path = Path(temp_dir.name) / "banco" / "squad.db"
    db_path.parent.mkdir()
    
    print("1. Workspace and database path initialized:", db_path)

    # 2. Test LocalAgentDB persistence
    db = LocalAgentDB(db_path=db_path, project_id="sim-project-2026")
    db.record_token_metrics("US-PAYMENT-2026", "requirements-analyst", "elicit_requirements", 1500, 450, cost_usd=0.002)
    db.record_token_metrics("US-PAYMENT-2026", "software-engineer", "implement_tdd", 3500, 1200, cost_usd=0.008)
    
    # Record code symbols
    symbols = db.index_python_file(
        file_path="src/payment_gateway.py",
        content="""
class PaymentGateway:
    def process_payment(self, amount: float, currency: str) -> bool:
        \"\"\"Process standard payment transaction.\"\"\"
        if amount <= 0:
            raise ValueError("Amount must be positive")
        return True
""",
    )
    
    # Record trajectory log
    db.log_trajectory("SDLC-BENCHMARK", "software-engineer", "US-PAYMENT-2026", "pass", 3, 5, 12.5, {"tdd_cycle": "valid"})
    
    assert len(symbols) >= 1, "Symbol indexing failed!"
    print("2. LocalAgentDB: Indexed symbol successfully:", symbols[0].name)

    # 3. Test AgentSquad SDLC Orchestration
    squad = AgentSquad(ROOT, project_name="sim-project-2026", allow_legacy=True)
    item = squad.init_work_item("US-PAYMENT-2026", "medium", base=work_dir)
    print("3. Work item created:", item)

    # Phase 1: Requirements Elicitation (01-requirements-analyst)
    (item / "specs" / "features").mkdir(parents=True, exist_ok=True)
    (item / "specs" / "features" / "checkout.feature").write_text("""Feature: Payment Checkout
  @AC-001
  Scenario: Process payment
    Given problem statement defined
    When payment is processed
    Then success response returns
""", encoding="utf-8")

    (item / "epic.md").write_text("""# US-PAYMENT-2026 Requirement Spec

- Problem: Processing checkout payments securely.
- Goal: Enable instant payment processing.
- User Story INVEST: As a customer, I want to pay online.
- Acceptance Criteria: Testable response within 1s.
- Priority: High value-prioritized feature.
- Dependencies: Payment Gateway API dependencies known.
""", encoding="utf-8")
    
    mem1 = squad.record_memory(
        item, "requirements-analyst",
        "Elicited INVEST story and BDD criteria for checkout payment.",
        "epic.md", kind="fact"
    )
    
    handoff_1 = squad.create_handoff(
        item, "requirements-analyst", "product-owner",
        "Requirements ready for G1 product validation.",
        ["epic.md"], ["epic.md"], f"memory/deltas/{mem1['id']}.yaml", next_gate="G1-product"
    )
    squad.ack_handoff(item, handoff_1["id"], "product-owner")
    print("4. Requirements handoff acknowledged by product-owner.")

    # Gate G1-product evaluation
    g1_criteria = [(c, "pass") for c in squad.get_gate("G1-product")["criteria"]]
    g1_dec = squad.decide_gate(
        item, "G1-product", "product-owner", g1_criteria, ["epic.md"],
        human_approved_by="stakeholder-lead", human_evidence="epic.md"
    )
    assert g1_dec["decision"] == "approved", "Gate G1 failed!"
    print("5. Gate G1-product APPROVED!")

    # Phase 2: Architecture & ADR (04-solution-architect)
    arch_file = item / "specs" / "architecture.md"
    arch_file.write_text("# C4 Architecture & STRIDE Threat Model\n\nADR-001: Payment Gateway Integration.\n", encoding="utf-8")
    
    mem2 = squad.record_memory(
        item, "solution-architect",
        "ADR-001 recorded with payment gateway C4 architecture.",
        "specs/architecture.md", kind="decision"
    )
    
    handoff_2 = squad.create_handoff(
        item, "solution-architect", "delivery-orchestrator",
        "Architecture ready for implementation.",
        ["specs/architecture.md"], ["specs/architecture.md"],
        f"memory/deltas/{mem2['id']}.yaml", next_gate="G3-readiness"
    )
    squad.ack_handoff(item, handoff_2["id"], "delivery-orchestrator")
    print("6. Architecture handoff acknowledged.")

    # Phase 3: TDD Implementation (06-software-engineer)
    (item / "implementation").mkdir(exist_ok=True)
    (item / "tests").mkdir(exist_ok=True)
    (item / "evaluation" / "tdd").mkdir(parents=True, exist_ok=True)
    
    code_file = item / "implementation" / "payment_gateway.py"
    test_file = item / "tests" / "test_payment_gateway.py"
    
    code_text = "class PaymentGateway:\n    def process(self): return True\n"
    test_text = "def test_process(): assert PaymentGateway().process() is True\n"
    
    code_file.write_text(code_text, encoding="utf-8")
    test_file.write_text(test_text, encoding="utf-8")
    
    import hashlib
    code_hash = hashlib.sha256(code_file.read_bytes()).hexdigest()
    test_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()
    hashes = {"implementation/payment_gateway.py": code_hash, "tests/test_payment_gateway.py": test_hash}
    
    # Create TDD cycle evidence files inside evaluation/tdd/
    red_ev = {"passed": False, "exit_code": 1, "file_hashes": hashes, "results": {"test_id": "test_payment", "criterion_id": "AC-1"}}
    green_ev = {"passed": True, "exit_code": 0, "file_hashes": hashes, "results": {"test_id": "test_payment", "criterion_id": "AC-1", "previous_digest": evidence_digest(red_ev)}}
    refactor_ev = {"passed": True, "exit_code": 0, "file_hashes": hashes, "results": {"test_id": "test_payment", "criterion_id": "AC-1", "previous_digest": evidence_digest(green_ev)}}
    
    (item / "evaluation" / "tdd" / "red.json").write_text(json.dumps(red_ev), encoding="utf-8")
    (item / "evaluation" / "tdd" / "green.json").write_text(json.dumps(green_ev), encoding="utf-8")
    (item / "evaluation" / "tdd" / "refactor.json").write_text(json.dumps(refactor_ev), encoding="utf-8")
    
    # Also write top-level stage files for compatibility
    (item / "red.json").write_text(json.dumps(red_ev), encoding="utf-8")
    (item / "green.json").write_text(json.dumps(green_ev), encoding="utf-8")
    (item / "refactor.json").write_text(json.dumps(refactor_ev), encoding="utf-8")
    
    assert validate_tdd_cycle(item / "evaluation" / "tdd")["approved"] is True, "TDD cycle validation failed!"
    print("7. TDD Cycle Red -> Green -> Refactor VALIDATED!")

    # Write executable verifiers for G4 and G5
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    
    def make_ev(verifier: str, extra_results: dict | None = None):
        data = {
            "schema_version": 1,
            "work_item": item.name,
            "verifier": verifier,
            "persona": "software-engineer",
            "command": ["pytest", "tests/test_payment_gateway.py"],
            "exit_code": 0,
            "passed": True,
            "timestamp": now_iso,
            "file_hashes": hashes,
            "results": extra_results or {"status": "PASS"},
        }
        return data

    (item / "evaluation" / "clean-code.json").write_text(json.dumps(make_ev("clean-code")), encoding="utf-8")
    (item / "evaluation" / "unit-tests.json").write_text(json.dumps(make_ev("unit-tests")), encoding="utf-8")
    (item / "evaluation" / "security.json").write_text(json.dumps(make_ev("security")), encoding="utf-8")
    (item / "evaluation" / "bdd.json").write_text(json.dumps(make_ev("bdd")), encoding="utf-8")
    (item / "evaluation" / "regression.json").write_text(json.dumps(make_ev("regression")), encoding="utf-8")
    (item / "evaluation" / "coverage.json").write_text(json.dumps(make_ev("coverage", {
        "status": "PASS", "minimum_percent": 80, "total_percent": 95, "branch_percent": 90
    })), encoding="utf-8")

    # Phase 4: Code & Security Review (09-code-reviewer & 10-security-reviewer)
    (item / "reviews").mkdir(exist_ok=True)
    (item / "reviews" / "code_security_review.md").write_text("# Code and Security Review\n\nNo OWASP vulnerabilities found.\n", encoding="utf-8")
    
    # Phase 5: QA & Release Governance (12-qa-engineer & 17-governance-auditor)
    (item / "validation").mkdir(exist_ok=True)
    (item / "validation" / "qa-report.md").write_text("""# QA Validation Report

- All BDD and regression suites passed.
- Failure paths documented: Network timeout and payment error handling verified.
- Evidence complete.
""", encoding="utf-8")
    (item / "documentation").mkdir(exist_ok=True)
    (item / "documentation" / "delivery-ledger.md").write_text("""# Delivery Ledger & Traceability Log

- Item: US-PAYMENT-2026
- Status: Approved and verified.
- Traceability: Complete requirements to code mapping.
- Observability: Datadog metrics and open-telemetry monitors active.
- Rollout: Blue-green deployment plan ready with automated rollback.
- Approvals: Approved by product, security, and governance leads.
""", encoding="utf-8")

    from gate_validators import validate_G4_code_security, validate_G5_quality
    print("DEBUG G4:", validate_G4_code_security(item))
    print("DEBUG G5:", validate_G5_quality(item))

    g4_criteria = [(c, "pass") for c in squad.get_gate("G4-code-security")["criteria"]]
    g4_dec = squad.decide_gate(
        item, "G4-code-security", "code-reviewer", g4_criteria,
        ["implementation/payment_gateway.py", "tests/test_payment_gateway.py", "reviews/code_security_review.md"],
        human_approved_by="tech-lead-security", human_evidence="reviews/code_security_review.md"
    )
    assert g4_dec["decision"] == "approved", "Gate G4 failed!"
    print("8. Gate G4-code-security APPROVED!")

    g5_criteria = [(c, "pass") for c in squad.get_gate("G5-quality")["criteria"]]
    g5_dec = squad.decide_gate(
        item, "G5-quality", "qa-engineer", g5_criteria,
        ["validation/qa-report.md", "documentation/delivery-ledger.md"],
        human_approved_by="qa-lead-governance", human_evidence="documentation/delivery-ledger.md"
    )
    assert g5_dec["decision"] == "approved", "Gate G5 failed!"
    print("9. Gate G5-quality APPROVED!")

    # Final audit check
    from agent_squad import read_yaml
    status = read_yaml(item / "status.yaml")
    print("10. Final Work Item Status:", status["id"], "-> State:", status.get("state", "approved"))

    temp_dir.cleanup()
    print("=== END-TO-END SIMULATION SUCCESSFUL ===")


if __name__ == "__main__":
    run_e2e_simulation()
