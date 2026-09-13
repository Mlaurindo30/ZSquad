"""RED TDD para o contrato type -> cycle -> entry de work items.

Os testes exercitam a superfície pública (API/CLI) e os artefatos persistidos.
Não há inspeção textual do código de produção: uma falha de RED deve indicar
uma capacidade contratada que ainda não existe ou um comportamento divergente.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import agent_squad, sre_incident_loop
from scripts.agent_squad import AgentSquad, SquadError, read_yaml, write_yaml


RUNTIME = Path(__file__).resolve().parents[2]
VALID_TYPES = {
    "epic": ("development", "blueprint"),
    "story": ("user-story", "blueprint"),
    "task": ("development", "blueprint"),
    "bug": ("bugfix", "implementation"),
    "release": ("release", "implementation"),
    "evolution": ("evolution", "blueprint"),
    "study": ("spike", "blueprint"),
    "spike": ("spike", "blueprint"),
}


def _squad() -> AgentSquad:
    return AgentSquad(RUNTIME, project_name="agent_squad")


def _init_with_type(
    squad: AgentSquad,
    work_id: str,
    tmp_path: Path,
    item_type: str,
) -> Path:
    """Chama a seam aprovada e transforma API ausente em RED explícito."""
    try:
        return squad.init_work_item(work_id, "medium", base=tmp_path, item_type=item_type)
    except TypeError as exc:
        pytest.fail(f"RED: init_work_item ainda não aceita item_type: {exc}")


def _repair(
    squad: AgentSquad,
    item: Path,
    *,
    actor: str = "delivery-orchestrator",
    reason: str = "reconciliar entrada do ciclo aprovada",
    authorization_ref: str | None = "HUMAN-APPROVAL-20260913.md",
) -> dict:
    """Invoca o contrato de recovery sem esconder ausência da implementação."""
    try:
        return squad.advance_state(
            item,
            repair_entry=True,
            actor=actor,
            reason=reason,
            authorization_ref=authorization_ref,
        )
    except TypeError as exc:
        pytest.fail(f"RED: advance_state ainda não expõe repair-entry: {exc}")


def test_legacy_prefix_without_type_persists_compatible_cycle_and_entry(tmp_path: Path) -> None:
    item = _squad().init_work_item("TASK-CYCLE-LEGACY-20260913", "medium", base=tmp_path)
    status = read_yaml(item / "status.yaml")

    assert status["type"] == "task"
    assert status["cycle"] == "development"
    assert status["state"] == "blueprint"


def test_explicit_type_overrides_id_prefix_and_uses_cycle_entry(tmp_path: Path) -> None:
    item = _init_with_type(_squad(), "TASK-CYCLE-OVERRIDE-20260913", tmp_path, "bug")
    status = read_yaml(item / "status.yaml")

    assert status["type"] == "bug"
    assert status["cycle"] == "bugfix"
    assert status["state"] == "implementation"


@pytest.mark.parametrize("item_type,expected", sorted(VALID_TYPES.items()))
def test_each_accepted_type_starts_at_configured_cycle_entry(
    tmp_path: Path, item_type: str, expected: tuple[str, str]
) -> None:
    item = _init_with_type(_squad(), f"TASK-CYCLE-{item_type.upper()}-20260913", tmp_path, item_type)
    status = read_yaml(item / "status.yaml")
    assert (status["cycle"], status["state"]) == expected


def test_invalid_type_is_rejected_before_work_item_mutation(tmp_path: Path) -> None:
    work_id = "TASK-CYCLE-INVALID-20260913"
    with pytest.raises(SystemExit) as raised:
        agent_squad.main(
            [
                "--root",
                str(RUNTIME),
                "--project-name",
                "agent_squad",
                "init-work-item",
                "--id",
                work_id,
                "--type",
                "unknown",
            ]
        )
    assert raised.value.code == 2
    assert not (tmp_path / work_id).exists()


def test_cli_parser_accepts_optional_type_and_repair_contract() -> None:
    parser = agent_squad._build_parser()
    init_args = parser.parse_args(
        ["init-work-item", "--id", "TASK-CYCLE-PARSER-20260913", "--type", "bug"]
    )
    repair_args = parser.parse_args(
        [
            "advance-state",
            "--work-item",
            "BUG-CYCLE-PARSER-20260913",
            "--repair-entry",
            "--actor",
            "delivery-orchestrator",
            "--reason",
            "reconciliar entrada",
            "--authorization-ref",
            "HUMAN-APPROVAL-20260913.md",
        ]
    )
    assert init_args.item_type == "bug"
    assert repair_args.repair_entry is True
    assert repair_args.actor == "delivery-orchestrator"
    assert repair_args.authorization_ref == "HUMAN-APPROVAL-20260913.md"


def test_invalid_cycle_config_fails_closed_without_creating_item(tmp_path: Path) -> None:
    squad = _squad()
    squad.cycles.setdefault("type_to_cycle", {})["bug"] = "missing-cycle"
    with pytest.raises(SquadError):
        _init_with_type(squad, "TASK-CYCLE-DRIFT-20260913", tmp_path, "bug")
    assert not (tmp_path / "TASK-CYCLE-DRIFT-20260913").exists()


def test_explicit_cycle_drift_does_not_fallback_to_default_on_advance(tmp_path: Path) -> None:
    squad = _squad()
    item = squad.init_work_item("TASK-CYCLE-ADVANCE-20260913", "medium", base=tmp_path)
    status_path = item / "status.yaml"
    status = read_yaml(status_path)
    status.update({"cycle": "cycle-that-does-not-exist", "state": "blueprint"})
    write_yaml(status_path, status)
    before = status_path.read_bytes()

    with pytest.raises(SquadError):
        squad.advance_state(item)
    assert status_path.read_bytes() == before


def _legacy_bug(tmp_path: Path, *, state: str = "blueprint", cycle: str | None = None) -> tuple[AgentSquad, Path]:
    squad = _squad()
    item = squad.init_work_item("BUG-CYCLE-LEGACY-20260913", "medium", base=tmp_path)
    status_path = item / "status.yaml"
    status = read_yaml(status_path)
    status["type"] = "bug"
    status["state"] = state
    if cycle is None:
        status.pop("cycle", None)
    else:
        status["cycle"] = cycle
    write_yaml(status_path, status)
    return squad, item


def test_repair_entry_requires_explicit_actor_reason_and_authorization(tmp_path: Path) -> None:
    squad, item = _legacy_bug(tmp_path)
    with pytest.raises(SquadError):
        _repair(squad, item, actor="not-a-registry-agent")
    with pytest.raises(SquadError):
        _repair(squad, item, reason="", authorization_ref="HUMAN-APPROVAL-20260913.md")
    with pytest.raises(SquadError):
        _repair(squad, item, authorization_ref=None)


def test_repair_entry_normalizes_legacy_bug_and_records_backup_manifest_event(tmp_path: Path) -> None:
    squad, item = _legacy_bug(tmp_path)
    before_status = (item / "status.yaml").read_bytes()
    result = _repair(squad, item)

    assert result["status"] in {"committed", "no-op"}
    status = read_yaml(item / "status.yaml")
    assert status["cycle"] == "bugfix"
    assert status["state"] == "implementation"
    operation_id = result["operation_id"]
    evidence_dir = item / "evaluation" / "cycle-entry-repair" / operation_id
    assert (evidence_dir / "status.yaml.before").read_bytes() == before_status
    assert (evidence_dir / "manifest.json").is_file()
    assert (evidence_dir / "event.json").is_file()
    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["operation"] == "repair-entry"
    assert manifest["source"]["type"] == "bug"
    assert manifest["target"] == {"cycle": "bugfix", "state": "implementation"}
    assert manifest["before_sha256"] == hashlib.sha256(before_status).hexdigest()


def test_repair_entry_is_idempotent_and_does_not_create_gate_or_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    squad, item = _legacy_bug(tmp_path)
    calls: list[str] = []
    for name in ("decide_gate", "run_integration_engine", "_sdd_enforce"):
        monkeypatch.setattr(squad, name, lambda *args, _name=name, **kwargs: calls.append(_name), raising=False)

    first = _repair(squad, item)
    evidence_before = sorted(str(path.relative_to(item)) for path in (item / "evaluation").rglob("*"))
    second = _repair(squad, item)
    evidence_after = sorted(str(path.relative_to(item)) for path in (item / "evaluation").rglob("*"))

    assert first["operation_id"] == second["operation_id"]
    assert second["status"] == "no-op"
    assert evidence_after == evidence_before
    assert calls == []
    assert not list((item / "gate-decisions").glob("*cycle-entry*"))


@pytest.mark.parametrize("state", ["done", "design", "implementation"])
def test_repair_entry_rejects_terminal_or_intermediate_states(tmp_path: Path, state: str) -> None:
    squad, item = _legacy_bug(tmp_path, state=state)
    with pytest.raises(SquadError):
        _repair(squad, item)


def test_repair_entry_rejects_explicitly_divergent_cycle(tmp_path: Path) -> None:
    squad, item = _legacy_bug(tmp_path, cycle="development")
    with pytest.raises(SquadError):
        _repair(squad, item)


def test_sre_incident_loop_uses_bugfix_implementation_without_gt_entry_promise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = AgentSquad.init_work_item
    captured: dict[str, object] = {}

    def isolated_init(self: AgentSquad, work_id: str, risk: str, *args, **kwargs):
        captured["kwargs"] = dict(kwargs)
        return original(self, work_id, risk, base=tmp_path)

    monkeypatch.setattr(sre_incident_loop.AgentSquad, "init_work_item", isolated_init)
    loop = sre_incident_loop.SREIncidentLoop(RUNTIME, project_name="agent_squad")
    bug_id = loop.create_incident_bug("alert-1", "checkout", "HTTP 500", "high")
    item = next(tmp_path.glob(f"{bug_id}"))
    status = read_yaml(item / "status.yaml")

    assert "item_type" not in captured["kwargs"]
    assert status["type"] == "bug"
    assert status["cycle"] == "bugfix"
    assert status["state"] == "implementation"
    assert "GT-entry" not in status["next_action"]
