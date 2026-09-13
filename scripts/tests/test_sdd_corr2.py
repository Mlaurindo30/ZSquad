"""CORR-2 — correções da auditoria pós-entrega Spec Kit (rodada 2).

Cobre os achados remanescentes (findings/spec-kit-post-delivery-audit.md):

- P1#4: ativação durável da política SDD — apagar ``sdd-policy.yaml`` em
  projeto ativado NÃO rebaixa silenciosamente para legado: o registro
  ``.agents_squad/config/sdd-activation.yaml`` mantém o estado ativo com
  falha fechada (``SDD_POLICY_INVALID``) até restauração ou desativação
  formal (``sdd deactivate``).
- P1#5: ``sdd.required: false`` tem UMA semântica em todo o pipeline —
  política presente mas SDD NÃO obrigatório (compatível com legado, sem
  requisitos SDD impostos; ``sdd status`` mantém o relatório informativo).
- Passo 8 da auditoria: ``activate-agent`` inclui no packet o briefing
  governado do estágio atual (mesma montagem de contexto de
  ``sdd render``/``sdd run``, somente leitura) e a persona de
  STAGE_PERSONA — sem conceder autorização de escrita.
- P2/passos 4: o scanner de skills do audit exclui código vendor governado
  sob ``integrations/spec-kit/**`` (regra VENDOR_SKILL_SCAN_EXCLUSIONS)
  com linha informativa na saída — nunca silenciosa.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import project_context
from agent_squad import AgentSquad, SquadError, main as squad_main
from project_context import (
    SDD_ACTIVATION_REL,
    SDD_POLICY_REL,
    resolve_sdd_policy,
    sdd_required,
)

PROJECT_ID = "test-sdd-corr2"

SDD_INPUT_FILES = {
    "spec": "spec.md",
    "clarifications": "clarifications.yaml",
    "plan": "plan.md",
    "tasks": "tasks.yaml",
}
SDD_GATE_INPUTS = {
    "G1-product": ("spec", "clarifications", "constitution"),
    "G2-design": ("plan", "constitution"),
    "G3-readiness": ("tasks", "constitution"),
}
SDD_GATE_OWNER = {
    "G1-product": "product-owner",
    "G2-design": "solution-architect",
    "G3-readiness": "delivery-orchestrator",
}

G1_CRITERIA = [
    "blueprint-complete",
    "bdd-specification-valid",
    "data-contracts-defined-when-applicable",
    "timebox-defined",
    "question-stated",
    "finding-documented",
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path, name: str, *, required: bool | None) -> Path:
    """Projeto consumidor; ``required=None`` = sem política (legado)."""
    root = tmp_path / name
    config = root / ".agents_squad" / "config"
    config.mkdir(parents=True)
    (config / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "runtime": str(ROOT),
                "project_id": PROJECT_ID,
                "project_name": PROJECT_ID,
                "project_root": str(root),
                "work_dir": str(ROOT / "work" / PROJECT_ID),
                "db_path": str(ROOT / "banco" / "squad.db"),
                "overrides": {},
            }
        ),
        encoding="utf-8",
    )
    if required is not None:
        (config / "sdd-policy.yaml").write_text(
            yaml.safe_dump({"policy_version": 1, "sdd": {"required": required}}),
            encoding="utf-8",
        )
    return root


@pytest.fixture()
def required_project(tmp_path):
    return _make_project(tmp_path, "ativo", required=True)


@pytest.fixture()
def optional_project(tmp_path):
    return _make_project(tmp_path, "opcional", required=False)


@pytest.fixture()
def legacy_project(tmp_path):
    return _make_project(tmp_path, "legado", required=None)


@pytest.fixture()
def squad_factory():
    """Cria AgentSquad isolando work/<PROJECT_ID> e .locks (padrão CORR-1)."""
    created: list[AgentSquad] = []
    work_dir = ROOT / "work" / PROJECT_ID
    lock_dir = ROOT / ".locks" / PROJECT_ID

    def _factory(project_root: Path) -> AgentSquad:
        squad = AgentSquad(ROOT, project_name=PROJECT_ID, project_root=project_root)
        created.append(squad)
        return squad

    shutil.rmtree(work_dir, ignore_errors=True)
    shutil.rmtree(lock_dir, ignore_errors=True)
    try:
        yield _factory
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)


def _run_cli(capsys, project_root: Path, *argv):
    rc = squad_main(["--project-root", str(project_root), *argv])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


# ---------------------------------------------------------------------------
# Helpers de pacote/decisões (mesmo padrão de test_sdd_e2e_flow.py)
# ---------------------------------------------------------------------------


def _materialize_package(item: Path) -> dict[str, str]:
    sdd = item / "sdd"
    sdd.mkdir(parents=True, exist_ok=True)
    (item / "reviews").mkdir(parents=True, exist_ok=True)
    clarifications = (
        "questions:\n"
        "  - id: Q-001\n"
        "    requirement_ids: [REQ-001]\n"
        "    severity: nonblocking\n"
        "    status: resolved\n"
        "    question: \"Formato?\"\n"
        "    answer: \"Markdown.\"\n"
        "    source: \"ata 2026-09-11\"\n"
    )
    tasks = (
        "tasks:\n"
        "  - id: T-001\n"
        "    requirement_ids: [REQ-001]\n"
        "    acceptance_ids: [AC-001]\n"
        "    owner: backend-engineer\n"
        "    points: 3\n"
        "    depends_on: []\n"
        "    paths: [\"sdd/spec.md\"]\n"
        "    test_ids: [\"tests/test_exemplo.py\"]\n"
        "    status: open\n"
    )
    files = {
        sdd / "spec.md": "# Spec — CORR2\n\nREQ-001: fluxo de exemplo do pacote.\n",
        sdd / "clarifications.yaml": clarifications,
        sdd / "plan.md": "# Plan — CORR2\n\nPlano de exemplo.\n",
        sdd / "tasks.yaml": tasks,
        sdd / "constitution.md": "# Constituição\n\nPrincípios.\n",
        item / "reviews" / "rev-gate.md": "# Evidência do gate\n\nConteúdo real.\n",
    }
    for path, content in files.items():
        path.write_bytes(content.encode("utf-8"))
    hashes = {key: _sha256(files[sdd / name].encode("utf-8")) for key, name in SDD_INPUT_FILES.items()}
    hashes["constitution"] = _sha256(files[sdd / "constitution.md"].encode("utf-8"))
    package = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "work_id": item.name,
        "constitution_path": "sdd/constitution.md",
        "constitution_sha256": hashes["constitution"],
        "inputs": {
            key: {"path": f"sdd/{name}", "revision": "r1", "sha256": hashes[key]}
            for key, name in SDD_INPUT_FILES.items()
        },
        "requirements": [
            {"id": "REQ-001", "acceptance_ids": ["AC-001"], "classification": "code"},
        ],
        "reviews": [],
    }
    (sdd / "package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return hashes


def _write_bdd(item: Path) -> None:
    features = item / "specs" / "features"
    features.mkdir(parents=True, exist_ok=True)
    (features / "corr2.feature").write_text(
        "Feature: CORR2\n"
        "  Scenario: fluxo\n"
        "    Given um work item\n"
        "    When os gates são aprovados\n"
        "    Then o item avança\n"
        "    @AC-001\n",
        encoding="utf-8",
    )
    (item / "epic.md").write_text(
        "# Objetivo\n\nProblema: exemplo. Contrato de dados: schema. "
        "Timebox: 3 dias. Pergunta: como? Achado e objetivo documentados.\n",
        encoding="utf-8",
    )


def _write_decision(
    item: Path,
    gate: str,
    hashes: dict[str, str] | None,
    *,
    with_hashes: bool = True,
    policy_version: int | None = 1,
) -> Path:
    """Decisão real de decide-gate; ``with_hashes=False`` = estilo legado."""
    record: dict = {
        "decision_id": f"GD-{item.name}-{gate.upper()}",
        "gate_id": gate,
        "work_item_id": item.name,
        "decision": "approved",
        "decider": SDD_GATE_OWNER[gate],
        "criteria": [{"name": "criterio-exemplo", "result": "pass"}],
        "evidence": ["reviews/rev-gate.md"],
        "human_approval": {
            "required": False,
            "status": "not_required",
            "approved_by": None,
            "evidence": None,
        },
        "conditions": [],
        "valid_until": None,
        "decided_at": "2026-09-11T12:00:00Z",
    }
    if policy_version is not None:
        record["policy_version"] = policy_version
    if with_hashes and hashes is not None:
        record["input_hashes"] = {key: hashes[key] for key in SDD_GATE_INPUTS[gate]}
    path = item / "gate-decisions" / f"{record['decision_id']}.yaml"
    path.write_text(yaml.safe_dump(record), encoding="utf-8")
    return path


def _read_state(item: Path) -> str:
    return yaml.safe_load((item / "status.yaml").read_text(encoding="utf-8"))["state"]


# ---------------------------------------------------------------------------
# P1#4 — ativação durável (fail-closed) e desativação formal
# ---------------------------------------------------------------------------


def test_sdd_activate_writes_durable_record(required_project, squad_factory, capsys):
    """`sdd activate` grava o registro durável com hash/versão da política."""
    squad_factory(required_project)
    rc, out, err = _run_cli(capsys, required_project, "sdd", "activate")
    assert rc == 0, err
    record_path = required_project / SDD_ACTIVATION_REL
    assert record_path.is_file()
    record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    assert record["schema_version"] == 1
    assert record["activated_at"].endswith("Z")
    policy_bytes = (required_project / SDD_POLICY_REL).read_bytes()
    assert record["policy_sha256"] == _sha256(policy_bytes)
    assert record["policy_version"] == 1
    # Ativação idempotente é recusada (registro já existe).
    rc, out, err = _run_cli(capsys, required_project, "sdd", "activate")
    assert rc == 2
    assert "já existe" in err


def test_sdd_activate_requires_policy(legacy_project, squad_factory, capsys):
    squad_factory(legacy_project)
    rc, out, err = _run_cli(capsys, legacy_project, "sdd", "activate")
    assert rc == 2
    assert "ausente" in err


def test_delete_policy_with_record_fails_closed(required_project, squad_factory, capsys):
    """P1#4: apagar a política de projeto ativado NÃO rebaixa para legado."""
    squad_factory(required_project)
    rc, out, err = _run_cli(capsys, required_project, "sdd", "activate")
    assert rc == 0, err
    (required_project / SDD_POLICY_REL).unlink()

    resolution = resolve_sdd_policy(required_project)
    assert resolution.state == "active", "registro durável mantém o estado ativo"
    assert resolution.errors, "fail-closed: erro obrigatório"
    assert "restaurar" in resolution.errors[0]
    assert sdd_required(required_project) is True

    squad = squad_factory(required_project)
    item = squad.init_work_item("US-C2-FC-01", "low")
    _materialize_package(item)
    with pytest.raises(SquadError) as exc:
        squad.advance_state(item)
    assert "SDD_POLICY_INVALID" in str(exc.value)
    assert "restaurar" in str(exc.value)

    # CLI também denuncia: sdd status reporta SDD_POLICY_INVALID (exit 0, relatório).
    rc, out, err = _run_cli(capsys, required_project, "sdd", "status", "--work-item", "US-C2-FC-01")
    assert rc == 0, err
    assert "SDD_POLICY_INVALID" in out


def test_no_record_no_policy_is_legacy(legacy_project, squad_factory):
    """Sem política e sem registro de ativação: legado compatível (sem SDD)."""
    resolution = resolve_sdd_policy(legacy_project)
    assert resolution.state == "legacy"
    assert resolution.errors == ()
    assert sdd_required(legacy_project) is False

    squad = squad_factory(legacy_project)
    item = squad.init_work_item("US-C2-LEG-01", "low")
    _materialize_package(item)
    _write_decision(item, "G1-product", None, with_hashes=False, policy_version=None)
    result = squad.advance_state(item)
    assert result["state"] == "scaffolding"


def test_sdd_init_writes_activation_record_on_adoption(required_project, legacy_project, squad_factory, capsys):
    """`sdd init` é o caminho de adoção: grava o registro durável quando a
    política ativa declara sdd.required: true; nada em projeto legado."""
    squad = squad_factory(required_project)
    squad.init_work_item("US-C2-ADOPT-01", "low")
    rc, out, err = _run_cli(capsys, required_project, "sdd", "init", "--work-item", "US-C2-ADOPT-01")
    assert rc == 0, err
    assert (required_project / SDD_ACTIVATION_REL).is_file()
    record = yaml.safe_load((required_project / SDD_ACTIVATION_REL).read_text(encoding="utf-8"))
    assert record["policy_version"] == 1

    squad_legacy = squad_factory(legacy_project)
    squad_legacy.init_work_item("US-C2-ADOPT-02", "low")
    rc, out, err = _run_cli(capsys, legacy_project, "sdd", "init", "--work-item", "US-C2-ADOPT-02")
    assert rc == 0, err
    assert not (legacy_project / SDD_ACTIVATION_REL).exists()


def test_sdd_deactivate_archives_record(required_project, squad_factory, capsys):
    """Desativação formal: arquiva o registro com eco de confirmação."""
    squad_factory(required_project)
    rc, out, err = _run_cli(capsys, required_project, "sdd", "activate")
    assert rc == 0, err
    rc, out, err = _run_cli(capsys, required_project, "sdd", "deactivate")
    assert rc == 0, err
    assert "arquivado" in (out + err).lower()
    assert not (required_project / SDD_ACTIVATION_REL).exists()
    archives = list((required_project / ".agents_squad" / "config").glob("sdd-activation.archived-*.yaml"))
    assert len(archives) == 1
    # Sem registro: nova desativação falha.
    rc, out, err = _run_cli(capsys, required_project, "sdd", "deactivate")
    assert rc == 2


# ---------------------------------------------------------------------------
# P1#5 — sdd.required: false tem UMA semântica (não obrigatório em todo o pipeline)
# ---------------------------------------------------------------------------


def test_required_false_is_non_mandatory_everywhere(optional_project, squad_factory, capsys):
    """required:false = política presente mas SEM requisitos SDD impostos:
    decide-gate sem author/reviewer/vínculos, advance sem enforce SDD e
    sdd status informativo (authorized None)."""
    squad = squad_factory(optional_project)
    item = squad.init_work_item("US-C2-OPT-01", "low")
    _materialize_package(item)
    _write_bdd(item)

    # decide-gate SEM author/reviewer: sem exigência SDD (decide-gate não impõe).
    decision = squad.decide_gate(
        item,
        "G1-product",
        "product-owner",
        [(name, "pass") for name in G1_CRITERIA],
        ["reviews/rev-gate.md"],
    )
    assert decision["decision"] == "approved"
    assert "policy_version" not in decision
    assert "input_hashes" not in decision

    # advance-state: sem enforce SDD (decisão estilo legado não bloqueia).
    result = squad.advance_state(item)
    assert result["state"] == "scaffolding"

    # sdd status: relatório informativo; authorized None (não obrigatório).
    rc, out, err = _run_cli(capsys, optional_project, "sdd", "status", "--work-item", "US-C2-OPT-01")
    assert rc == 0, err
    report = json.loads(out)
    assert report["policy"] == "active"
    assert report["authorized"] is None
    assert any("não obrigatório" in line for line in report["human"])


def test_required_true_still_enforced_everywhere(required_project, squad_factory, capsys):
    """Direção oposta: required:true mantém o enforcement completo."""
    squad = squad_factory(required_project)
    item = squad.init_work_item("US-C2-REQ-01", "low")
    _materialize_package(item)
    _write_bdd(item)

    # decide-gate SDD exige author/reviewer segregados (MINOR-1).
    with pytest.raises(SquadError) as exc:
        squad.decide_gate(
            item,
            "G1-product",
            "product-owner",
            [(name, "pass") for name in G1_CRITERIA],
            ["reviews/rev-gate.md"],
        )
    assert "author" in str(exc.value).lower()

    # Decisão estilo legado (sem vínculos) não autoriza o advance.
    _write_decision(item, "G1-product", None, with_hashes=False, policy_version=None)
    with pytest.raises(SquadError) as exc:
        squad.advance_state(item)
    assert "SDD_STALE_GATE" in str(exc.value)
    assert _read_state(item) == "blueprint"


# ---------------------------------------------------------------------------
# Passo 8 — overlay wiring no activate-agent (packet com briefing do estágio)
# ---------------------------------------------------------------------------


def test_activation_packet_includes_stage_briefing_and_persona(required_project, squad_factory):
    """Packet de ativação inclui briefing governado do estágio atual + persona,
    mantendo write_authorization: state-machine (nenhuma autorização de escrita)."""
    squad = squad_factory(required_project)
    item = squad.init_work_item("US-C2-PA-01", "low")
    _materialize_package(item)
    _write_bdd(item)
    squad.decide_gate(
        item,
        "G1-product",
        "product-owner",
        [(name, "pass") for name in G1_CRITERIA],
        ["reviews/rev-gate.md"],
        author="requirements-analyst",
        reviewer="security-reviewer",
    )
    packet = squad.activation_packet("solution-architect", item=item)
    sdd = packet["sdd"]
    assert sdd["write_authorization"] == "state-machine"
    briefing = sdd["stage_briefing"]
    assert briefing["status"] == "ok"
    assert briefing["mode"] == "mandatory"
    assert briefing["stage"] == "plan"  # blueprint -> 'plan' (mesma fonte do §5)
    assert briefing["persona"] == "solution-architect"  # STAGE_PERSONA
    assert "Briefing de agente" in briefing["briefing"]


def test_activation_packet_briefing_failure_is_explicit(required_project, squad_factory):
    """Pré-requisito ausente (sem G1): o packet declara a falha explicitamente
    (fail-closed em política obrigatória) em vez de omitir o briefing."""
    squad = squad_factory(required_project)
    item = squad.init_work_item("US-C2-PA-02", "low")
    _materialize_package(item)
    packet = squad.activation_packet("solution-architect", item=item)
    briefing = packet["sdd"]["stage_briefing"]
    assert briefing["status"] == "blocked"
    assert briefing["mode"] == "mandatory"
    assert briefing["stage"] == "plan"
    assert "g1_evidence" in briefing["error"]
    assert "briefing" not in briefing  # nenhum briefing parcial


def test_activation_packet_informational_briefing_when_not_required(optional_project, squad_factory):
    """required:false: briefing é informativo; falha vira 'unavailable', não 'blocked'."""
    squad = squad_factory(optional_project)
    item = squad.init_work_item("US-C2-PA-03", "low")
    _materialize_package(item)
    _write_bdd(item)
    _write_decision(item, "G1-product", None, with_hashes=False, policy_version=None)
    packet = squad.activation_packet("solution-architect", item=item)
    briefing = packet["sdd"]["stage_briefing"]
    assert briefing["status"] == "ok"
    assert briefing["mode"] == "informational"

    # Sem evidência de G1: indisponível (informativo), nunca "blocked".
    item2 = squad.init_work_item("US-C2-PA-04", "low")
    _materialize_package(item2)
    packet2 = squad.activation_packet("solution-architect", item=item2)
    briefing2 = packet2["sdd"]["stage_briefing"]
    assert briefing2["status"] == "unavailable"
    assert briefing2["mode"] == "informational"


def test_activation_packet_without_package_has_no_sdd(required_project, squad_factory):
    squad = squad_factory(required_project)
    item = squad.init_work_item("US-C2-PA-05", "low")
    packet = squad.activation_packet("solution-architect", item=item)
    assert "sdd" not in packet


# ---------------------------------------------------------------------------
# P2 / passos 4 — scanner do audit exclui vendor governado com linha informativa
# ---------------------------------------------------------------------------


def test_audit_excludes_spec_kit_vendor_informationally(capsys):
    """Vendor governado sob integrations/spec-kit/** sai do scan de skills;
    a exclusão aparece como linha informativa (nunca silenciosa) e o audit
    comum volta a exit 0."""
    from agent_squad import VENDOR_SKILL_SCAN_EXCLUSIONS

    assert "integrations/spec-kit/" in VENDOR_SKILL_SCAN_EXCLUSIONS
    squad = AgentSquad(ROOT)
    errors = squad.audit()
    out = capsys.readouterr().out
    assert errors == [], f"audit deve ficar verde para o runtime real: {errors[:5]}"
    assert "VENDOR_SKILL_SCAN_EXCLUSIONS" in out
    assert "integrations/spec-kit/" in out
    argv = ["--root", str(ROOT), "audit"]
    rc = squad_main(argv)
    out2 = capsys.readouterr()
    assert rc == 0
    combined = out2.out + out2.err
    assert "AUDIT_OK" in combined
    assert "VENDOR_SKILL_SCAN_EXCLUSIONS" in combined


def test_audit_still_flags_non_vendor_stray_skill():
    """Skill fora do catálogo FORA do vendor continua sendo reportada."""
    stray = ROOT / "integrations" / "_stray_corr2_probe.py"
    stray.write_text("# probe CORR-2: skill fora do catálogo fora do vendor\n", encoding="utf-8")
    try:
        squad = AgentSquad(ROOT)
        errors = squad.audit()
        assert any("_stray_corr2_probe.py" in error for error in errors)
    finally:
        stray.unlink(missing_ok=True)
