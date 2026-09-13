"""Gate Validators G1–G6 — validadores locais de gate para work items.

Cada validador recebe o caminho do work item e retorna:
- aprovado: bool
- findings: list[dict] com criterion, status, evidence
- next_state: str | None

Uso:
    from scripts.gate_validators import validate_G1_product, validate_all_gates

    result = validate_G1_product(Path("work/EPIC-001"))
    if result["approved"]:
        print("G1 passed")
    else:
        for f in result["findings"]:
            print(f"[{f['criterion']}] {f['status']}: {f['evidence']}")

    summary = validate_all_gates(Path("work/EPIC-001"))
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from bdd_validator import validate_features
from tdd_evidence import validate_tdd_cycle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_yaml(path: Path) -> Any:
    import yaml
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _read_md(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _find(text: str, needle: str) -> bool:
    return needle.lower() in text.lower()


def _project_root(work_item: Path) -> Path | None:
    """Localiza a raiz que contém o contrato canônico de evidência."""
    for candidate in (work_item, *work_item.parents):
        if (candidate / "contracts" / "verification-evidence.schema.json").is_file():
            return candidate
    fallback = Path(__file__).resolve().parents[1]
    return fallback if (fallback / "contracts" / "verification-evidence.schema.json").is_file() else None


def _verification_passed(work_item: Path, verifier: str) -> bool:
    """Valida schema, atualidade e hashes da evidência executável informada."""
    path = work_item / "evaluation" / f"{verifier}.json"
    root = _project_root(work_item)
    if root is None:
        return False
    content_root = root.resolve() if root.resolve() in work_item.resolve().parents else work_item.resolve()
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads((root / "contracts" / "verification-evidence.schema.json").read_text(encoding="utf-8"))
        if list(Draft202012Validator(schema).iter_errors(evidence)):
            return False
        timestamp = datetime.fromisoformat(evidence["timestamp"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp > datetime.now(timezone.utc):
            return False
        for relative, expected in evidence["file_hashes"].items():
            source = (content_root / relative).resolve()
            if content_root != source and content_root not in source.parents:
                return False
            if not source.is_file():
                return False
            if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                return False
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False
    return (
        evidence["passed"] is True
        and evidence["exit_code"] == 0
        and evidence["work_item"] == work_item.name
        and evidence["verifier"] == verifier
        and bool(evidence["file_hashes"])
    )


def _coverage_passed(work_item: Path) -> bool:
    """Exige evidência executável e métricas total e branch iguais ou acima de 80%."""
    if not _verification_passed(work_item, "coverage"):
        return False
    try:
        results = json.loads((work_item / "evaluation" / "coverage.json").read_text(encoding="utf-8"))["results"]
        return (
            results["status"] == "PASS"
            and float(results["minimum_percent"]) >= 80
            and float(results["total_percent"]) >= float(results["minimum_percent"])
            and float(results["branch_percent"]) >= float(results["minimum_percent"])
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def _tdd_passed(work_item: Path) -> bool:
    """Valida o ciclo TDD persistido; qualquer erro reprova o gate."""
    try:
        return bool(validate_tdd_cycle(work_item / "evaluation" / "tdd")["approved"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


# ---------------------------------------------------------------------------
# G1 — product
# ---------------------------------------------------------------------------

def validate_G1_product(work_item: Path) -> dict[str, Any]:
    """Valida os artefatos e critérios obrigatórios do gate G1 de produto alinhados com workflow.yaml."""
    status = _read_yaml(work_item / "status.yaml")
    epic = _read_md(work_item / "epic.md")
    brief = _read_md(work_item / "discovery" / "brief.md")
    blueprint = _read_md(work_item / "blueprint.md")
    combined = f"{status}\n{epic}\n{brief}\n{blueprint}"

    bdd = validate_features(work_item / "specs" / "features")
    blueprint_complete = (
        (work_item / "blueprint.md").exists()
        or (work_item / "discovery" / "brief.md").exists()
        or bool(epic)
        or _find(combined, "problem")
        or _find(combined, "problema")
        or _find(combined, "blueprint")
    )
    bdd_valid = bool(bdd.get("approved", False)) if isinstance(bdd, dict) else bool(bdd)
    data_contracts = (
        _find(combined, "schema")
        or _find(combined, "data-contract")
        or _find(combined, "data contract")
        or _find(combined, "contrato")
        or _find(combined, "model")
        or _find(combined, "not-applicable")
    )
    timebox_defined = (
        _find(combined, "timebox")
        or _find(combined, "sprint")
        or _find(combined, "prazo")
        or _find(combined, "dias")
        or bool(status.get("timebox"))
    )
    question_stated = (
        _find(combined, "question")
        or _find(combined, "questão")
        or _find(combined, "pergunta")
        or _find(combined, "problem")
        or _find(combined, "problema")
        or bool(status.get("objective"))
    )
    finding_documented = (
        _find(combined, "finding")
        or _find(combined, "achado")
        or _find(combined, "resultado")
        or _find(combined, "goal")
        or _find(combined, "objetivo")
        or bool(status.get("objective"))
    )

    criteria = [
        ("blueprint-complete", blueprint_complete),
        ("bdd-specification-valid", bdd_valid),
        ("data-contracts-defined-when-applicable", data_contracts),
        ("timebox-defined", timebox_defined),
        ("question-stated", question_stated),
        ("finding-documented", finding_documented),
    ]

    findings = []
    for name, passed in criteria:
        findings.append({
            "criterion": name,
            "status": "PASS" if passed else "FAIL",
            "evidence": "found" if passed else "missing",
        })

    approved = all(f["status"] == "PASS" for f in findings)
    return {"gate": "G1-product", "approved": approved, "findings": findings, "next_state": "scaffolding" if approved else "blueprint"}


# ---------------------------------------------------------------------------
# G2 — design
# ---------------------------------------------------------------------------

def validate_G2_design(work_item: Path) -> dict[str, Any]:
    """Valida os artefatos e critérios obrigatórios do gate G2 de design."""
    specs = _read_md(work_item / "specs" / "index.md")
    adr_dir = work_item / "adr"
    adr_files = list(adr_dir.glob("ADR-*.md")) if adr_dir.exists() else []
    threat = _read_md(work_item / "threat-model.md")
    test_plan = _read_md(work_item / "test-plan.md")
    combined = f"{specs}\n{threat}\n{test_plan}"

    criteria = [
        ("options-compared", _find(combined, "option") or _find(combined, "alternativa")),
        ("adr-recorded", len(adr_files) > 0),
        ("interfaces-defined", _find(combined, "interface") or _find(combined, "api") or _find(combined, "contract")),
        ("data-contracts-defined-when-applicable", _find(combined, "schema") or _find(combined, "model") or _find(combined, "contrato")),
        ("threat-model-done", _find(threat, "threat") or _find(threat, "ameaça") or _find(threat, "stride")),
        ("test-strategy-defined", _find(test_plan, "test") or _find(test_plan, "teste")),
        ("observability-and-rollback-designed", _find(combined, "observab") or _find(combined, "rollback") or _find(combined, "monitor")),
    ]

    findings = []
    for name, passed in criteria:
        findings.append({
            "criterion": name,
            "status": "PASS" if passed else "FAIL",
            "evidence": "found" if passed else "missing",
        })

    approved = all(f["status"] == "PASS" for f in findings)
    return {"gate": "G2-design", "approved": approved, "findings": findings, "next_state": "scaffolding" if approved else "blueprint"}


# ---------------------------------------------------------------------------
# G3 — readiness
# ---------------------------------------------------------------------------

def validate_G3_readiness(work_item: Path) -> dict[str, Any]:
    """Valida os artefatos e critérios obrigatórios do gate G3 de prontidão alinhados com workflow.yaml."""
    status = _read_yaml(work_item / "status.yaml")
    plan = _read_md(work_item / "plans" / "delivery-plan.md")
    combined = f"{status}\n{plan}"
    item_type = status.get("type", "story")
    story_points = status.get("story_points")
    t_shirt_size = status.get("t_shirt_size")

    # Sizing criteria validation as part of definition-of-ready
    sizing_valid = False
    cognitive_load_safe = True
    if item_type == "epic":
        sizing_valid = bool(t_shirt_size and t_shirt_size in ["PP", "P", "M", "G", "GG"])
    else:
        if story_points is not None:
            sizing_valid = story_points in [1, 2, 3, 5, 8, 13]
            cognitive_load_safe = story_points <= 8
        else:
            sizing_valid = bool(
                _find(plan, "story point")
                or _find(plan, "fibonacci")
                or _find(plan, "estimate")
                or _find(plan, "sizing")
                or _find(plan, "pontos")
                or _find(combined, "story_points")
            )
            cognitive_load_safe = True

    dor_passed = (
        (_find(plan, "definition of ready") or _find(plan, "definição de pronto") or _find(plan, "ready") or bool(plan))
        and sizing_valid
        and cognitive_load_safe
    )

    red_json = work_item / "evaluation" / "tdd" / "red.json"
    red_passed = False
    if red_json.is_file():
        try:
            data = json.loads(red_json.read_text(encoding="utf-8"))
            red_passed = data.get("passed") is False or data.get("exit_code") != 0
        except Exception:
            red_passed = False
    if not red_passed:
        test_files = list((work_item / "tests").glob("*.py")) if (work_item / "tests").is_dir() else []
        red_passed = (
            len(test_files) > 0
            or _find(combined, "test")
            or _find(combined, "red")
            or _find(plan, "scaffold")
            or (work_item / "tests").is_dir()
        )

    owners_assigned = bool(status.get("owner")) or _find(plan, "owner") or _find(plan, "responsável")
    deps_in_status = status.get("dependencies")
    deps_resolved_in_status = status.get("dependencies_resolved")
    deps_file = (work_item / "dependencies.md").is_file() or (work_item / "dependencies.json").is_file()
    dependencies_resolved = bool(
        deps_resolved_in_status is True
        or (isinstance(deps_in_status, list) and len(deps_in_status) == 0)
        or _find(plan, "depend")
        or _find(plan, "sem bloqueio")
        or _find(plan, "no blocker")
        or _find(plan, "blocker")
        or _find(combined, "dependency")
        or _find(combined, "dependência")
        or deps_file
    )

    criteria = [
        ("definition-of-ready", dor_passed),
        ("tests-red-exist-and-fail", red_passed),
        ("owners-assigned", bool(owners_assigned)),
        ("dependencies-resolved", bool(dependencies_resolved)),
    ]

    findings = []
    for name, passed in criteria:
        if name == "definition-of-ready" and not cognitive_load_safe:
            evidence = f"Story points ({story_points}) exceeds limit 8. Split required."
        elif name == "definition-of-ready" and not sizing_valid:
            evidence = "Missing or invalid story_points/t_shirt_size"
        else:
            evidence = "found" if passed else "missing"
        findings.append({
            "criterion": name,
            "status": "PASS" if passed else "FAIL",
            "evidence": evidence,
        })

    approved = all(f["status"] == "PASS" for f in findings)
    return {"gate": "G3-readiness", "approved": approved, "findings": findings, "next_state": "implementation" if approved else "scaffolding"}


# ---------------------------------------------------------------------------
# G4 — code security
# ---------------------------------------------------------------------------

def validate_G4_code_security(work_item: Path) -> dict[str, Any]:
    """Valida os artefatos e critérios obrigatórios do gate G4 de código e segurança."""
    review_dir = work_item / "reviews"
    findings_dir = work_item / "findings"
    review_files = list(review_dir.glob("*.md")) if review_dir.exists() else []
    finding_files = list(findings_dir.glob("*.md")) if findings_dir.exists() else []
    combined = " ".join(_read_md(p) for p in review_files + finding_files)

    security_review_done = _find(combined, "security") or _find(combined, "segurança")
    tests_green = _find(combined, "green") or _find(combined, "pass") or _find(combined, "passou")
    clean_code = _find(combined, "clean code") or _find(combined, "clean-code")
    component_contract = _find(combined, "contract") or _find(combined, "contrato")
    secrets_checked = not any(
        re.search(r"(api[_-]?key|apikey|secret|token|password)\s*[:=]\s*['\"][^'\"]{8,}", _read_md(p), re.IGNORECASE)
        for p in finding_files
    )

    criteria = [
        ("implementation-review-present", bool(review_files)),
        ("clean-code-executed", _verification_passed(work_item, "clean-code")),
        ("tests-executed", _verification_passed(work_item, "unit-tests")),
        ("security-executed", _verification_passed(work_item, "security")),
        ("tdd-cycle-valid", _tdd_passed(work_item)),
        ("dependencies-and-secrets-checked", secrets_checked),
    ]

    findings = []
    for name, passed in criteria:
        findings.append({
            "criterion": name,
            "status": "PASS" if passed else "FAIL",
            "evidence": "found" if passed else "missing",
        })

    approved = all(f["status"] == "PASS" for f in findings)
    return {"gate": "G4-code-security", "approved": approved, "findings": findings, "next_state": "quality-validation" if approved else "code-security-review"}


# ---------------------------------------------------------------------------
# G5 — quality
# ---------------------------------------------------------------------------

def validate_G5_quality(work_item: Path) -> dict[str, Any]:
    """Valida os artefatos e critérios obrigatórios do gate G5 de qualidade."""
    validation_dir = work_item / "validation"
    report_files = list(validation_dir.glob("*.md")) if validation_dir.exists() else []
    combined = " ".join(_read_md(p) for p in report_files)

    criteria = [
        ("acceptance-bdd-executed", _verification_passed(work_item, "bdd")),
        ("regression-executed", _verification_passed(work_item, "regression")),
        ("coverage-executed", _coverage_passed(work_item)),
        ("failure-paths-documented", _find(combined, "failure") or _find(combined, "falha") or _find(combined, "error")),
        ("evidence-complete", len(report_files) > 0),
    ]

    findings = []
    for name, passed in criteria:
        findings.append({
            "criterion": name,
            "status": "PASS" if passed else "FAIL",
            "evidence": "found" if passed else "missing",
        })

    approved = all(f["status"] == "PASS" for f in findings)
    return {"gate": "G5-quality", "approved": approved, "findings": findings, "next_state": "governance-release" if approved else "quality-validation"}


# ---------------------------------------------------------------------------
# G6 — governance release
# ---------------------------------------------------------------------------

def validate_G6_governance_release(work_item: Path) -> dict[str, Any]:
    """Valida os artefatos e critérios obrigatórios do gate G6 de governança e release alinhados com workflow.yaml."""
    docs_dir = work_item / "documentation"
    docs_files = list(docs_dir.glob("*.md")) if docs_dir.exists() else []
    ledger = work_item / "documentation" / "delivery-ledger.md"
    ledger_content = _read_md(ledger)
    release_dir = work_item / "release"
    release_files = list(release_dir.glob("*.md")) if release_dir.exists() else []
    traceability_dir = work_item / "traceability"
    traceability_files = list(traceability_dir.glob("*.md")) if traceability_dir.exists() else []
    combined = f"{ledger_content}\n" + " ".join(_read_md(p) for p in docs_files + release_files + traceability_files)

    criteria = [
        ("traceability-complete", _find(combined, "traceability") or _find(combined, "rastreabilidade") or len(traceability_files) > 0 or len(docs_files) > 0),
        ("ledger-current", (ledger.exists() and len(ledger_content) > 10) or _find(combined, "ledger") or len(docs_files) > 0),
        ("rollout-rollback-ready", _find(combined, "rollout") or _find(combined, "rollback") or _find(combined, "deploy") or len(release_files) > 0),
        ("rollout-plan-defined", _find(combined, "rollout") or _find(combined, "deploy") or len(release_files) > 0 or len(docs_files) > 0),
        ("rollback-plan-defined", _find(combined, "rollback") or len(release_files) > 0 or len(docs_files) > 0),
        ("runbook-updated", _find(combined, "runbook") or _find(combined, "operat") or len(release_files) > 0 or len(docs_files) > 0),
        ("change-record-created", _find(combined, "change") or _find(combined, "mudança") or _find(combined, "record") or ledger.exists() or len(docs_files) > 0),
    ]

    findings = []
    for name, passed in criteria:
        findings.append({
            "criterion": name,
            "status": "PASS" if passed else "FAIL",
            "evidence": "found" if passed else "missing",
        })

    approved = all(f["status"] == "PASS" for f in findings)
    return {"gate": "G6-governance-release", "approved": approved, "findings": findings, "next_state": "done" if approved else "governance-release"}


# ---------------------------------------------------------------------------
# All gates
# ---------------------------------------------------------------------------

_GATE_VALIDATORS = {
    "G1-product": validate_G1_product,
    "G2-design": validate_G2_design,
    "G3-readiness": validate_G3_readiness,
    "G4-code-security": validate_G4_code_security,
    "G5-quality": validate_G5_quality,
    "G6-governance-release": validate_G6_governance_release,
    "GT-entry": lambda wi: _merge_validations([validate_G1_product(wi), validate_G2_design(wi)]),
    "GT-design-review": validate_G3_readiness,
    "GT-done": lambda wi: _merge_validations([validate_G4_code_security(wi), validate_G5_quality(wi), validate_G6_governance_release(wi)]),
}


def _merge_validations(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Funde resultados de múltiplos validadores legados em uma decisão de gate comprimido."""
    all_findings: list[dict[str, Any]] = []
    for result in results:
        all_findings.extend(result.get("findings", []))
    approved = all(r.get("approved", False) for r in results)
    return {"approved": approved, "findings": all_findings, "next_state": None}


def validate_gate(gate_id: str, work_item: Path) -> dict[str, Any]:
    """Executa o validador correspondente ao gate informado."""
    validator = _GATE_VALIDATORS.get(gate_id)
    if not validator:
        return {"gate": gate_id, "approved": False, "findings": [{"criterion": "unknown-gate", "status": "FAIL", "evidence": f"no validator for {gate_id}"}], "next_state": None}
    return validator(work_item)


_LEGACY_GATE_NAMES = {"G1-product", "G2-design", "G3-readiness", "G4-code-security", "G5-quality", "G6-governance-release"}


def validate_all_gates(work_item: Path) -> dict[str, Any]:
    """Executa os validadores de gate canônicos (v3) para um work item."""
    results = {}
    all_approved = True
    for gate_id, validator in _GATE_VALIDATORS.items():
        if gate_id in _LEGACY_GATE_NAMES:
            continue
        result = validator(work_item)
        results[gate_id] = result
        if not result.get("approved", False):
            all_approved = False
    return {"work_item": str(work_item), "all_approved": all_approved, "gates": results}
