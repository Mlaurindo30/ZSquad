"""Enforcement SDD na state machine e no dispatch (Tarefa T5) — backend-engineer.

Cobre o checklist do plano (docs/plans/2026-09-11-spec-kit-integration.md §7-T5):

(a) G1 aprovado sem G2 não conclui blueprint (erro SDD, estado inalterado);
(b) G3 ausente não inicia implementação;
(c) bugfix/retomada não contornam preflight (dispatch gerenciado bloqueado);
(d) lock + releitura de hashes + escrita atômica: alteração concorrente é rejeitada
    sem mudança de estado;
(e) chamada direta via CLI a partir de cwd alternativo funciona;
(f) policy ativada -> authorize é executado e erros SDD bloqueiam o avanço;
(g) política legada (não ativada) preserva o caminho compatível.

Além disso, pendências da auditoria T4 incorporadas ao T5:
- MINOR-1: decide-gate emite author/reviewer e o contrato aceita os campos;
- NOTE-2: human_approval.evidence precisa existir dentro do work item;
- vínculos input_hashes/policy_version registrados no decide-gate quando a
  política SDD está ativa.

O pacote SDD é materializado em tmp_path (nada de produção). O runtime Squad é
o real (ROOT), com projeto de teste isolado em work/test-sdd-t5 (limpo no fim).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_squad import AgentSquad, SquadError, _build_parser, main as squad_main
from governed_io import file_lock

PROJECT_ID = "test-sdd-t5"

WORKFLOW_G2_CRITERIA = [
    "adr-recorded",
    "interfaces-defined",
    "threat-model-done",
    "test-strategy-defined",
    "blast-radius-defined",
    "deprecation-strategy-defined",
    "migration-plan-defined",
]

SPEC_MD = "# Spec\n\nRequisito de exemplo com acentuação: título ção.\n"
PLAN_MD = "# Plan\n\nPlano de exemplo.\n"
CONSTITUTION_MD = "# Constituição do projeto\n\nPrincípios.\n"
CLARIFICATIONS_YAML = """questions:
  - id: Q-001
    requirement_ids: [REQ-001]
    severity: nonblocking
    status: resolved
    question: "Formato?"
    answer: "Markdown."
    source: "ata 2026-09-10"
    owner: product-owner
"""
TASKS_YAML = """tasks:
  - id: T-001
    requirement_ids: [REQ-001]
    acceptance_ids: [AC-001]
    owner: backend-engineer
    points: 3
    depends_on: []
    paths: ["sdd/spec.md"]
    evidence: ["reviews/rev-gate.md"]
    test_ids: ["tests/test_exemplo.py"]
    status: open
"""

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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def project_root(tmp_path):
    """Projeto consumidor com política SDD ativada."""
    root = tmp_path / "consumidor"
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
    (config / "sdd-policy.yaml").write_text(
        yaml.safe_dump({"policy_version": 1, "sdd": {"required": True}}), encoding="utf-8"
    )
    return root


@pytest.fixture()
def legacy_project_root(tmp_path):
    """Projeto consumidor SEM política SDD (modo legado, sem ativação)."""
    root = tmp_path / "legado"
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
    return root


@pytest.fixture()
def squad(project_root):
    """Squad com política SDD ativa e work items isolados em work/test-sdd-t5."""
    import shutil

    work_dir = ROOT / "work" / PROJECT_ID
    lock_dir = ROOT / ".locks" / PROJECT_ID

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)

    cleanup()
    try:
        yield AgentSquad(ROOT, project_name=PROJECT_ID, project_root=project_root)
    finally:
        cleanup()


def _item(squad: AgentSquad, work_id: str, risk: str = "low") -> Path:
    return squad.init_work_item(work_id, risk)


def _materialize_sdd(item: Path) -> dict[str, str]:
    """Cria o pacote SDD completo em <item>/sdd e devolve os hashes reais."""
    sdd = item / "sdd"
    sdd.mkdir(parents=True, exist_ok=True)
    (item / "reviews").mkdir(parents=True, exist_ok=True)

    files = {
        sdd / "spec.md": SPEC_MD,
        sdd / "clarifications.yaml": CLARIFICATIONS_YAML,
        sdd / "plan.md": PLAN_MD,
        sdd / "tasks.yaml": TASKS_YAML,
        sdd / "constitution.md": CONSTITUTION_MD,
        item / "reviews" / "rev-gate.md": "# Evidência do gate\n\nConteúdo real.\n",
    }
    for path, content in files.items():
        # Escrita em bytes: evita tradução de newline do Windows (\n -> \r\n)
        # que divergiria dos hashes computados sobre o conteúdo em memória.
        path.write_bytes(content.encode("utf-8"))

    hashes = {
        "spec": _sha256(SPEC_MD.encode("utf-8")),
        "clarifications": _sha256(CLARIFICATIONS_YAML.encode("utf-8")),
        "plan": _sha256(PLAN_MD.encode("utf-8")),
        "tasks": _sha256(TASKS_YAML.encode("utf-8")),
        "constitution": _sha256(CONSTITUTION_MD.encode("utf-8")),
    }
    package = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "work_id": item.name,
        "constitution_path": "sdd/constitution.md",
        "constitution_sha256": hashes["constitution"],
        "inputs": {
            "spec": {"path": "sdd/spec.md", "revision": "r1", "sha256": hashes["spec"]},
            "clarifications": {
                "path": "sdd/clarifications.yaml",
                "revision": "r1",
                "sha256": hashes["clarifications"],
            },
            "plan": {"path": "sdd/plan.md", "revision": "r1", "sha256": hashes["plan"]},
            "tasks": {"path": "sdd/tasks.yaml", "revision": "r1", "sha256": hashes["tasks"]},
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


def _write_decision(
    item: Path,
    gate: str,
    hashes: dict[str, str] | None,
    *,
    stored_gate_id: str | None = None,
    decision: str = "approved",
    with_hashes: bool = True,
    policy_version: int | None = 1,
    valid_until: str | None = None,
    decided_at: str = "2026-09-11T12:00:00Z",
) -> Path:
    """Grava um GD-*.yaml com a estrutura real do decide-gate + vínculos SDD."""
    stored_gate = stored_gate_id or ("GT-design-review" if gate == "G3-readiness" else gate)
    record: dict = {
        "decision_id": f"GD-{item.name}-{stored_gate.upper()}",
        "gate_id": stored_gate,
        "work_item_id": item.name,
        "decision": decision,
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
        "valid_until": valid_until,
        "decided_at": decided_at,
    }
    if with_hashes and hashes is not None:
        record["input_hashes"] = {key: hashes[key] for key in SDD_GATE_INPUTS[gate]}
    if policy_version is not None:
        record["policy_version"] = policy_version
    path = item / "gate-decisions" / f"{record['decision_id']}.yaml"
    path.write_text(yaml.safe_dump(record), encoding="utf-8")
    return path


def _read_state(item: Path) -> str:
    return yaml.safe_load((item / "status.yaml").read_text(encoding="utf-8"))["state"]


# ---------------------------------------------------------------------------
# (a) G1 sem G2 não conclui blueprint
# ---------------------------------------------------------------------------


def test_g1_without_g2_cannot_conclude_blueprint(squad):
    item = _item(squad, "US-SDD-BP-01")
    hashes = _materialize_sdd(item)
    _write_decision(item, "G1-product", hashes)

    with pytest.raises(SquadError) as exc:
        squad.advance_state(item)
    assert "SDD_MISSING_INPUT" in str(exc.value)
    assert "G2-design" in str(exc.value)
    assert _read_state(item) == "blueprint"


# ---------------------------------------------------------------------------
# (b) G3 ausente não inicia implementação (+ caminho positivo G1+G2)
# ---------------------------------------------------------------------------


def test_missing_g3_blocks_implementation_start(squad):
    item = _item(squad, "US-SDD-IMPL-01")
    hashes = _materialize_sdd(item)
    _write_decision(item, "G1-product", hashes)
    _write_decision(item, "G2-design", hashes)

    # Positivo: G1+G2 concluem blueprint.
    result = squad.advance_state(item)
    assert result["state"] == "scaffolding"

    # G3 ausente: avanço para implementação bloqueado.
    with pytest.raises(SquadError) as exc:
        squad.advance_state(item)
    assert "SDD_MISSING_INPUT" in str(exc.value)
    assert "G3-readiness" in str(exc.value)
    assert _read_state(item) == "scaffolding"


def test_full_preflight_authorizes_implementation_start(squad):
    item = _item(squad, "US-SDD-IMPL-02")
    hashes = _materialize_sdd(item)
    _write_decision(item, "G1-product", hashes)
    _write_decision(item, "G2-design", hashes)
    squad.advance_state(item)
    _write_decision(item, "G3-readiness", hashes)
    result = squad.advance_state(item)
    assert result["state"] == "implementation"
    assert _read_state(item) == "implementation"


# ---------------------------------------------------------------------------
# (c) bugfix/retomada não contornam preflight (dispatch gerenciado)
# ---------------------------------------------------------------------------


def test_bugfix_resume_cannot_skip_preflight_on_dispatch(squad):
    item = _item(squad, "BUG-SDD-RESUME-01")
    _materialize_sdd(item)
    # Retomada direta em implementation (o loophole que o plano fecha).
    status_path = item / "status.yaml"
    status = yaml.safe_load(status_path.read_text(encoding="utf-8"))
    status["state"] = "implementation"
    status_path.write_text(yaml.safe_dump(status), encoding="utf-8")

    with pytest.raises(SquadError) as exc:
        squad.run_integration_engine("sdd-inexistente", work_item=item.name)
    assert "SDD_" in str(exc.value)


# ---------------------------------------------------------------------------
# MAJOR-1 (revisão T5): dispatch fail-closed com work item não resolvível
# ---------------------------------------------------------------------------


def test_dispatch_fail_closed_when_work_item_unresolvable_under_active_policy(squad):
    with pytest.raises(SquadError) as exc:
        squad.run_integration_engine("sdd-inexistente", work_item="US-NAO-EXISTE-01")
    assert "fail-closed" in str(exc.value).lower()
    assert "US-NAO-EXISTE-01" in str(exc.value)


def test_dispatch_unresolvable_item_stays_compatible_in_legacy_policy(legacy_project_root):
    import shutil

    work_dir = ROOT / "work" / PROJECT_ID
    lock_dir = ROOT / ".locks" / PROJECT_ID
    shutil.rmtree(work_dir, ignore_errors=True)
    shutil.rmtree(lock_dir, ignore_errors=True)
    try:
        squad = AgentSquad(ROOT, project_name=PROJECT_ID, project_root=legacy_project_root)
        result = squad.run_integration_engine("sdd-inexistente", work_item="US-NAO-EXISTE-LEG-01")
        assert isinstance(result, dict)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# MAJOR-2 (revisão T5): política ativa + pacote SDD ausente => fail-closed
# ---------------------------------------------------------------------------


def test_active_policy_blocks_dispatch_without_sdd_package(squad):
    item = _item(squad, "BUG-SDD-NOPKG-01")
    status_path = item / "status.yaml"
    status = yaml.safe_load(status_path.read_text(encoding="utf-8"))
    status["state"] = "implementation"
    status_path.write_text(yaml.safe_dump(status), encoding="utf-8")

    with pytest.raises(SquadError) as exc:
        squad.run_integration_engine("sdd-inexistente", work_item=item.name)
    assert "SDD_MISSING_INPUT" in str(exc.value)
    assert "pacote" in str(exc.value).lower()


def test_active_policy_blocks_advance_without_sdd_package(squad):
    item = _item(squad, "US-SDD-NOPKG-02")

    with pytest.raises(SquadError) as exc:
        squad.advance_state(item)
    assert "SDD_MISSING_INPUT" in str(exc.value)
    assert "pacote" in str(exc.value).lower()
    assert _read_state(item) == "blueprint"


# ---------------------------------------------------------------------------
# (d) lock + releitura de hashes + escrita atômica
# ---------------------------------------------------------------------------


def test_concurrent_lock_holder_rejects_advance_without_state_change(squad, monkeypatch):
    import agent_squad as agent_module

    item = _item(squad, "US-SDD-LOCK-01")
    hashes = _materialize_sdd(item)
    _write_decision(item, "G1-product", hashes)
    _write_decision(item, "G2-design", hashes)

    lock_path = ROOT / ".locks" / PROJECT_ID / f"{item.name}.lock"

    def short_lock(path, **kwargs):
        return file_lock(path, timeout=0.3, poll_interval=0.01)

    monkeypatch.setattr(agent_module, "file_lock", short_lock)
    with file_lock(lock_path, timeout=5):
        with pytest.raises(SquadError) as exc:
            squad.advance_state(item)
    assert "lock" in str(exc.value).lower() or "timeout" in str(exc.value).lower()
    assert _read_state(item) == "blueprint"


def test_concurrent_document_edit_is_caught_by_hash_reread(squad):
    item = _item(squad, "US-SDD-RACE-01")
    hashes = _materialize_sdd(item)
    _write_decision(item, "G1-product", hashes)
    _write_decision(item, "G2-design", hashes)
    squad.advance_state(item)

    # Edição concorrente do spec entre a decisão e o avanço (releitura pega).
    (item / "sdd" / "spec.md").write_text(SPEC_MD + "\nEdição concorrente.\n", encoding="utf-8")

    with pytest.raises(SquadError) as exc:
        squad.advance_state(item)
    assert "SDD_STALE_GATE" in str(exc.value)
    assert _read_state(item) == "scaffolding"


# ---------------------------------------------------------------------------
# (e) chamada direta via CLI a partir de cwd alternativo
# ---------------------------------------------------------------------------


def test_cli_advance_blocked_from_alternate_cwd(squad, project_root, tmp_path, monkeypatch, capsys):
    item = _item(squad, "US-SDD-CLI-01")
    hashes = _materialize_sdd(item)
    _write_decision(item, "G1-product", hashes)

    alternate_cwd = tmp_path / "cwd-alternativo"
    alternate_cwd.mkdir()
    monkeypatch.chdir(alternate_cwd)

    rc = squad_main(["--project-root", str(project_root), "advance-state", "--work-item", item.name])
    assert rc == 2
    assert "SDD_MISSING_INPUT" in capsys.readouterr().err
    assert _read_state(item) == "blueprint"

    _write_decision(item, "G2-design", hashes)
    rc = squad_main(["--project-root", str(project_root), "advance-state", "--work-item", item.name])
    assert rc == 0
    assert _read_state(item) == "scaffolding"


# ---------------------------------------------------------------------------
# (f) valid_until é avaliado na camada CLI (relógio do T5)
# ---------------------------------------------------------------------------


def test_expired_valid_until_blocks_advance(squad):
    item = _item(squad, "US-SDD-EXP-01")
    hashes = _materialize_sdd(item)
    _write_decision(item, "G1-product", hashes, valid_until="2020-01-01T00:00:00Z")
    _write_decision(item, "G2-design", hashes)

    with pytest.raises(SquadError) as exc:
        squad.advance_state(item)
    assert "SDD_STALE_GATE" in str(exc.value)
    assert _read_state(item) == "blueprint"


# ---------------------------------------------------------------------------
# (g) política legada preserva o caminho compatível
# ---------------------------------------------------------------------------


def test_legacy_policy_without_activation_keeps_compat(legacy_project_root):
    import shutil

    work_dir = ROOT / "work" / PROJECT_ID
    lock_dir = ROOT / ".locks" / PROJECT_ID
    shutil.rmtree(work_dir, ignore_errors=True)
    shutil.rmtree(lock_dir, ignore_errors=True)
    try:
        squad = AgentSquad(ROOT, project_name=PROJECT_ID, project_root=legacy_project_root)
        item = _item(squad, "US-SDD-LEG-01")
        _materialize_sdd(item)
        # Decisão estilo legado: SEM input_hashes e SEM policy_version.
        _write_decision(item, "G1-product", None, with_hashes=False, policy_version=None)

        result = squad.advance_state(item)
        assert result["state"] == "scaffolding"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# decide-gate: vínculos SDD, identidade (MINOR-1) e evidência humana (NOTE-2)
# ---------------------------------------------------------------------------


def _g2_setup(item: Path) -> None:
    """Artefatos para o validador textual de G2 (ADR, ameaças, testes, specs)."""
    (item / "adr").mkdir(parents=True, exist_ok=True)
    (item / "adr" / "ADR-001.md").write_text("# ADR-001 option alternativa\n", encoding="utf-8")
    (item / "specs").mkdir(parents=True, exist_ok=True)
    (item / "specs" / "index.md").write_text(
        "# Spec\n\ninterface schema contract option observability rollback "
        "blast radius deprecation strategy migration plan\n",
        encoding="utf-8",
    )
    (item / "threat-model.md").write_text("# Threat model\n\nSTRIDE threat ameaça\n", encoding="utf-8")
    (item / "test-plan.md").write_text("# Test plan\n\ntest strategy teste\n", encoding="utf-8")


def test_decide_gate_records_sdd_bindings_and_identity(squad):
    item = _item(squad, "US-SDD-DG-01")
    hashes = _materialize_sdd(item)
    _g2_setup(item)

    decision = squad.decide_gate(
        item,
        "G2-design",
        "solution-architect",
        [(name, "pass") for name in WORKFLOW_G2_CRITERIA],
        ["reviews/rev-gate.md"],
        author="requirements-analyst",
        reviewer="security-reviewer",
    )
    assert decision["decision"] == "approved"
    assert decision["policy_version"] == 1
    assert decision["input_hashes"] == {
        "plan": hashes["plan"],
        "constitution": hashes["constitution"],
    }
    assert decision["author"] == "requirements-analyst"
    assert decision["reviewer"] == "security-reviewer"


def test_decide_gate_sdd_requires_distinct_identity(squad):
    item = _item(squad, "US-SDD-DG-02")
    _materialize_sdd(item)
    _g2_setup(item)

    with pytest.raises(SquadError) as exc:
        squad.decide_gate(
            item,
            "G2-design",
            "solution-architect",
            [(name, "pass") for name in WORKFLOW_G2_CRITERIA],
            ["reviews/rev-gate.md"],
            reviewer="solution-architect",
        )
    assert "segregação" in str(exc.value).lower()


def test_decide_gate_human_evidence_must_exist_within_item(squad):
    item = _item(squad, "US-SDD-DG-03", risk="medium")
    _materialize_sdd(item)
    _g2_setup(item)

    with pytest.raises(SquadError) as exc:
        squad.decide_gate(
            item,
            "G2-design",
            "solution-architect",
            [(name, "pass") for name in WORKFLOW_G2_CRITERIA],
            ["reviews/rev-gate.md"],
            human_approved_by="human-master",
            human_evidence="nao-existe.md",
        )
    assert "evidência de aprovação humana" in str(exc.value)


def test_decide_gate_without_sdd_package_keeps_legacy_shape(squad):
    item = _item(squad, "US-SDD-DG-04")
    _g2_setup(item)

    decision = squad.decide_gate(
        item,
        "G2-design",
        "solution-architect",
        [(name, "pass") for name in WORKFLOW_G2_CRITERIA],
        ["specs/index.md"],
    )
    assert decision["decision"] == "approved"
    assert "input_hashes" not in decision
    assert "policy_version" not in decision


def test_decide_gate_cli_accepts_author_and_reviewer():
    args = _build_parser().parse_args(
        [
            "decide-gate",
            "--work-item",
            "X",
            "--gate",
            "G1-product",
            "--decider",
            "product-owner",
            "--criteria",
            "c=pass",
            "--evidence",
            "e",
            "--author",
            "requirements-analyst",
            "--reviewer",
            "security-reviewer",
        ]
    )
    assert args.author == "requirements-analyst"
    assert args.reviewer == "security-reviewer"


# ---------------------------------------------------------------------------
# activation_packet: contexto de leitura, nunca autorização de escrita
# ---------------------------------------------------------------------------


def test_activation_packet_provides_read_context_without_write_authorization(squad):
    item = _item(squad, "US-SDD-RO-01")
    _materialize_sdd(item)

    packet = squad.activation_packet("software-engineer", item=item)
    assert "sdd" in packet
    assert packet["sdd"]["write_authorization"] == "state-machine"
    assert packet["sdd"]["read_context"]["spec_path"] == "sdd/spec.md"


# ---------------------------------------------------------------------------
# CLI parser avança com trabalho pendente do contrato (sanidade)
# ---------------------------------------------------------------------------


def test_advance_state_parser_unchanged():
    args = _build_parser().parse_args(["advance-state", "--work-item", "X"])
    assert args.command == "advance-state"
    assert args.work_item == "X"


# ---------------------------------------------------------------------------
# MINOR-3 (revisão T5): valid_until a partir de TTL da política no decide-gate
# ---------------------------------------------------------------------------


def _enable_policy_ttl(project_root, days: int = 7) -> None:
    (project_root / ".agents_squad" / "config" / "sdd-policy.yaml").write_text(
        yaml.safe_dump({"policy_version": 1, "sdd": {"required": True}, "valid_until_days": days}),
        encoding="utf-8",
    )


def test_policy_ttl_key_is_rejected_by_current_schema(squad, project_root):
    """Sob o contrato atual (additionalProperties: false), política com TTL é
    inválida => decide-gate falha fechado (SDD_POLICY_INVALID)."""
    _enable_policy_ttl(project_root)
    item = _item(squad, "US-SDD-TTL-REJ-01")
    _materialize_sdd(item)
    _g2_setup(item)

    with pytest.raises(SquadError) as exc:
        squad.decide_gate(
            item,
            "G2-design",
            "solution-architect",
            [(name, "pass") for name in WORKFLOW_G2_CRITERIA],
            ["reviews/rev-gate.md"],
            author="requirements-analyst",
            reviewer="security-reviewer",
        )
    assert "SDD_POLICY_INVALID" in str(exc.value)


def test_decide_gate_emits_valid_until_from_policy_ttl(squad, project_root, monkeypatch):
    """Com o contrato de política estendido (TTL aceito), decide-gate emite
    valid_until = decided_at + valid_until_days ( wiring provado por monkeypatch
    do validador, que só existirá quando o schema for estendido no épico)."""
    from datetime import datetime, timedelta, timezone

    from agent_squad import _sdd_adapter_modules

    _enable_policy_ttl(project_root)
    _, policy_mod = _sdd_adapter_modules()
    monkeypatch.setattr(policy_mod, "validate_policy", lambda policy: [])

    item = _item(squad, "US-SDD-TTL-OK-02")
    _materialize_sdd(item)
    _g2_setup(item)

    decision = squad.decide_gate(
        item,
        "G2-design",
        "solution-architect",
        [(name, "pass") for name in WORKFLOW_G2_CRITERIA],
        ["reviews/rev-gate.md"],
        author="requirements-analyst",
        reviewer="security-reviewer",
    )
    assert decision["valid_until"]
    parsed = datetime.fromisoformat(decision["valid_until"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed <= datetime.now(timezone.utc) + timedelta(days=8)
    assert parsed >= datetime.now(timezone.utc) + timedelta(days=6)


def test_policy_without_ttl_keeps_valid_until_none(squad):
    item = _item(squad, "US-SDD-TTL-NONE-03")
    _materialize_sdd(item)
    _g2_setup(item)

    decision = squad.decide_gate(
        item,
        "G2-design",
        "solution-architect",
        [(name, "pass") for name in WORKFLOW_G2_CRITERIA],
        ["reviews/rev-gate.md"],
        author="requirements-analyst",
        reviewer="security-reviewer",
    )
    assert decision["valid_until"] is None


# ---------------------------------------------------------------------------
# MINOR-4 (revisão T5): sdd_preflight de cycles.yaml é consumido pelo enforcement
# ---------------------------------------------------------------------------


def test_sdd_preflight_config_is_consumed_by_enforcement(squad, monkeypatch):
    """Com o fallback SDD_STATE_STAGES desligado, o preflight do ciclo bugfix
    ainda é aplicado porque cycles.yaml declara sdd_preflight: true."""
    import agent_squad as agent_module

    item = _item(squad, "BUG-SDD-CFG-01")
    _materialize_sdd(item)
    status_path = item / "status.yaml"
    status = yaml.safe_load(status_path.read_text(encoding="utf-8"))
    status["state"] = "implementation"
    status_path.write_text(yaml.safe_dump(status), encoding="utf-8")

    monkeypatch.setattr(agent_module, "SDD_STATE_STAGES", {})
    bugfix_def = squad.cycles["cycles"]["bugfix"]
    assert squad._sdd_stages_for_state(bugfix_def, "implementation") == ("implementation",)

    with pytest.raises(SquadError) as exc:
        squad.run_integration_engine("sdd-inexistente", work_item=item.name)
    assert "SDD_" in str(exc.value)


def test_cycle_without_sdd_preflight_falls_back_to_state_map(squad, monkeypatch):
    """Ciclo sem sdd_preflight (ex.: release, entry implementation) NÃO ganha
    preflight via config — só via SDD_STATE_STAGES (fallback canônico)."""
    import agent_squad as agent_module

    monkeypatch.setattr(agent_module, "SDD_STATE_STAGES", {})
    release_def = squad.cycles["cycles"]["release"]
    assert squad._sdd_stages_for_state(release_def, "implementation") == ()


# ---------------------------------------------------------------------------
# T7 (piloto, plano §8) — cenários 7, 8 e 12 no nível do CLI Squad
# ---------------------------------------------------------------------------


def test_scenario_7_new_project_cannot_skip_preflight_on_dispatch(squad):
    """§8-7: Given chamada direta/new-project, When tentar pular preflight,
    Then aplicar a mesma política. Item new-project retomado DIRETO em
    implementation é bloqueado pelo dispatch gerenciado (o fallback canônico
    SDD_STATE_STAGES['implementation'] exige G1+G2+G3)."""
    item = _item(squad, "TASK-SDD-NP-01")
    _materialize_sdd(item)
    status_path = item / "status.yaml"
    status = yaml.safe_load(status_path.read_text(encoding="utf-8"))
    status["state"] = "implementation"
    status["cycle"] = "new-project"
    status_path.write_text(yaml.safe_dump(status), encoding="utf-8")

    with pytest.raises(SquadError) as exc:
        squad.run_integration_engine("sdd-inexistente", work_item=item.name)
    assert "SDD_" in str(exc.value)
    assert _read_state(item) == "implementation"


def test_scenario_7_bugfix_preflight_covers_full_chain(squad):
    """§8-7 (complemento): bugfix retomado em implementation SEM G2 exige G2
    explicitamente — o preflight cobre a cadeia inteira G1->G2->G3, não só G3."""
    item = _item(squad, "BUG-SDD-CHAIN-01")
    hashes = _materialize_sdd(item)
    _write_decision(item, "G1-product", hashes)
    _write_decision(item, "G3-readiness", hashes)
    status_path = item / "status.yaml"
    status = yaml.safe_load(status_path.read_text(encoding="utf-8"))
    status["state"] = "implementation"
    status_path.write_text(yaml.safe_dump(status), encoding="utf-8")

    with pytest.raises(SquadError) as exc:
        squad.run_integration_engine("sdd-inexistente", work_item=item.name)
    assert "SDD_MISSING_INPUT" in str(exc.value)
    assert "G2-design" in str(exc.value)


def test_scenario_8_checklist_incomplete_with_continue_keeps_block(squad):
    """§8-8: Given checklist incompleto e usuário responde 'continuar' ao
    prompt, Then o controle Squad mantém o bloqueio.

    Contratos verificados:
    (a) o overlay do comando implement mantém a cláusula que proíbe override
        textual de checklist incompleto;
    (b) o CLI não expõe flag de override/force no advance-state — não existe
        caminho que converta 'continuar' em autorização;
    (c) com G2 ausente, o avanço é bloqueado (estado inalterado), exatamente
        como ocorreria se o operador pedisse para continuar.
    """
    # (a) cláusula do overlay (contrato de texto do T3, re-verificado no T7)
    overlay = (
        ROOT / "integrations" / "spec-kit" / "overlays" / "commands" / "implement.md"
    ).read_text(encoding="utf-8")
    assert "Checklist incompleto não aceita confirmação textual como override" in overlay

    # (b) nenhuma flag de override/force/continuar no advance-state
    for flag in ("--force", "--override", "--continuar", "--skip-checks"):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(
                ["advance-state", "--work-item", "X", flag]
            )

    # (c) bloqueio mantido apesar do pedido de continuar (não há parâmetro
    #     a passar: a API do advance_state só recebe o item)
    item = _item(squad, "US-SDD-CONT-01")
    hashes = _materialize_sdd(item)
    _write_decision(item, "G1-product", hashes)
    with pytest.raises(SquadError) as exc:
        squad.advance_state(item)
    assert "SDD_MISSING_INPUT" in str(exc.value)
    assert _read_state(item) == "blueprint"


def test_scenario_12_g4_criteria_require_code_and_test_evidence(squad):
    """§8-12 (parte automatizável): Given agente edita código manualmente,
    When abrir PR, Then CI rejeita falta de evidências.

    Nível verificado aqui: os critérios configurados do G4-code-security
    incluem clean-code-executed e tests-executed (calculados por
    validate_G4_code_security), e o validador executável rejeita um work item
    sem as evidências de execução. A PREVENÇÃO da escrita em si requer
    sandbox — fora do escopo do runtime e marcada como UNVERIFIED no
    relatório do piloto (plano §9)."""
    workflow = yaml.safe_load(
        (ROOT / "config" / "workflow.yaml").read_text(encoding="utf-8")
    )
    criteria = workflow["gates"]["G4-code-security"]["criteria"]
    assert "clean-code-executed" in criteria
    assert "tests-executed" in criteria

    from gate_validators import validate_G4_code_security

    # Work item sem evidências de execução (limpo, criado pelo fixture com
    # cleanup garantido) não é aprovado pelo validador executável do G4.
    item = _item(squad, "US-SDD-CI-12")
    result = validate_G4_code_security(item)
    assert result["approved"] is False

