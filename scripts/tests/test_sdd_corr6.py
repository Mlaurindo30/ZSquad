from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import pytest
import yaml

from scripts.agent_squad import AgentSquad, SquadError


def _item(tmp_path: Path) -> tuple[AgentSquad, Path]:
    runtime = Path(__file__).resolve().parents[2]
    project = tmp_path / "project"
    project.mkdir()
    squad = AgentSquad(runtime, project_name="corr6", project_root=project)
    item = runtime / "work" / "corr6" / f"TASK-CORR6-{uuid.uuid4().hex.upper()}"
    item.mkdir(parents=True, exist_ok=True)
    (item / "status.yaml").write_text(
        f"id: {item.name}\ntype: task\nstate: blueprint\nrisk: low\nowner: delivery-orchestrator\n",
        encoding="utf-8",
    )
    squad.sdd_init(item)
    return squad, item


def test_dispatch_is_durable_and_receipt_is_verified(tmp_path):
    squad, item = _item(tmp_path)
    result = squad.sdd_run(item, "constitution")
    receipt = result["dispatch"]["receipt"]
    assert receipt["status"] == "accepted"
    assert (item / "sdd" / receipt["request_ref"]).is_file()
    stages = yaml.safe_load((item / "sdd" / "stages.yaml").read_text(encoding="utf-8"))
    assert stages["stages"]["constitution"]["dispatch_receipt"]["request_sha256"] == receipt["request_sha256"]
    assert stages["stages"]["constitution"]["status"] == "queued"


def test_execution_ack_is_required_before_stage_can_complete(tmp_path):
    squad, item = _item(tmp_path)
    squad.sdd_run(item, "constitution")
    (item / "sdd" / "constitution.md").write_text(
        "# Constitution\nA substantive governing principle.", encoding="utf-8"
    )
    with pytest.raises(SquadError, match="SDD_NOT_DISPATCHED"):
        squad.sdd_stage_complete(item, "constitution")

    claim = squad.sdd_dispatch_claim(item, "constitution", "local-test-consumer")
    assert claim["status"] == "claimed"
    execution = squad.sdd_dispatch_ack(
        item, "constitution", "local-test-consumer", "sdd/constitution.md"
    )
    assert execution["status"] == "executed"
    completed = squad.sdd_stage_complete(item, "constitution")
    assert completed["status"] == "completed"
    stages = yaml.safe_load((item / "sdd" / "stages.yaml").read_text(encoding="utf-8"))
    assert stages["stages"]["constitution"]["execution_receipt"]["status"] == "executed"


def test_dispatch_failure_is_fail_closed(tmp_path):
    squad, item = _item(tmp_path)

    class BrokenDispatcher:
        def enqueue(self, *_args, **_kwargs):
            raise OSError("disk full")

    squad.sdd_dispatcher = BrokenDispatcher()
    with pytest.raises(SquadError, match="SDD_DISPATCH_FAILED"):
        squad.sdd_run(item, "constitution")
    stages = yaml.safe_load((item / "sdd" / "stages.yaml").read_text(encoding="utf-8"))
    assert stages["stages"]["constitution"]["status"] == "pending"


def test_completion_rejects_tampered_dispatch_request(tmp_path):
    squad, item = _item(tmp_path)
    result = squad.sdd_run(item, "constitution")
    (item / "sdd" / "constitution.md").write_text("# Constitution\nA substantive governing principle.", encoding="utf-8")
    squad.sdd_dispatch_ack(item, "constitution", "local-test-consumer", "sdd/constitution.md")
    request = item / "sdd" / result["dispatch"]["receipt"]["request_ref"]
    request.write_text("{}", encoding="utf-8")
    with pytest.raises(SquadError, match="SDD_INVALID_DISPATCH_RECEIPT"):
        squad.sdd_stage_complete(item, "constitution")


def test_stale_briefing_recalculation_errors_fail_closed(tmp_path, monkeypatch):
    squad, item = _item(tmp_path)
    squad.sdd_run(item, "constitution")
    (item / "sdd" / "constitution.md").write_text("# Constitution\nA substantive governing principle.", encoding="utf-8")
    squad.sdd_dispatch_ack(item, "constitution", "local-test-consumer", "sdd/constitution.md")
    monkeypatch.setattr(squad, "_sdd_briefing", lambda *_args: (_ for _ in ()).throw(OSError("broken")))
    with pytest.raises(SquadError, match="SDD_BRIEFING_VALIDATION_FAILED"):
        squad.sdd_stage_complete(item, "constitution")


def test_implement_requires_exact_planned_path(tmp_path):
    squad, item = _item(tmp_path)
    # Unit-level setup focuses on canonical path validation.
    stages_file = item / "sdd" / "stages.yaml"
    stages = yaml.safe_load(stages_file.read_text(encoding="utf-8"))
    stages["stages"]["implement"]["status"] = "dispatched"
    stages["stages"]["implement"]["briefing_sha256"] = "a" * 64
    stages_file.write_text(yaml.safe_dump(stages, sort_keys=False), encoding="utf-8")
    (item / "sdd" / "tasks.yaml").write_text(
        "tasks:\n  - id: T-1\n    owner: backend-engineer\n    points: 1\n    paths: [src/planned.py]\n",
        encoding="utf-8",
    )
    (item / "unplanned.py").write_text("x = 1", encoding="utf-8")
    with pytest.raises(SquadError, match="SDD_OUTPUT_NOT_IN_TASKS"):
        squad._sdd_validate_output_refs(item, "implement", ["unplanned.py"])
    with pytest.raises(SquadError, match="SDD_OUTPUT_TRAVERSAL"):
        squad._sdd_validate_output_refs(item, "implement", ["../unplanned.py"])


def test_implement_rejects_planned_symlink(tmp_path):
    squad, item = _item(tmp_path)
    target = tmp_path / "outside.py"
    target.write_text("secret = True", encoding="utf-8")
    link = item / "planned.py"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink indisponível neste Windows: {exc}")
    (item / "sdd" / "tasks.yaml").write_text(
        "tasks:\n  - id: T-1\n    owner: backend-engineer\n    points: 1\n    paths: [planned.py]\n",
        encoding="utf-8",
    )
    with pytest.raises(SquadError, match="SDD_OUTPUT_SYMLINK_DISALLOWED"):
        squad._sdd_validate_output_refs(item, "implement", ["planned.py"])
