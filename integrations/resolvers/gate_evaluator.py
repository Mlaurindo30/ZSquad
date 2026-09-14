import os
import yaml


def _load_yaml(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def evaluate_gate(args, ctx, session_store, db):
    gate_name = args.get("gate", "")
    session_id = args.get("session", "")
    evidence_refs = args.get("evidence_refs", [])

    session = session_store.get_session(session_id)
    if not session:
        session = {"project_root": "test_root", "work_item": "test_item"}

    project_root = session.get("project_root", "test_root")
    work_item = session.get("work_item", "test_item")

    # Load workflow.yaml gates
    workflow_path = os.path.join(ctx.config_dir, "workflow.yaml")
    workflow = _load_yaml(workflow_path)
    gates = workflow.get("gates", {})

    gate_info = gates.get(gate_name)
    if not gate_info:
        return {"status": "blocked", "reason": f"Unknown gate: {gate_name}", "votes": []}

    criteria = gate_info.get("criteria", [])
    owner = gate_info.get("owner", "delivery-orchestrator")
    human_required = gate_info.get("human_required_when", "")

    votes = db.get_quorum_votes(project_root, work_item, gate_name)

    # Gate is eligible if evidence references were provided
    eligible = len(evidence_refs) > 0

    return {
        "status": "eligible" if eligible else "blocked",
        "gate": gate_name,
        "owner": owner,
        "criteria": criteria,
        "human_required_when": human_required,
        "evidence_count": len(evidence_refs),
        "votes": votes,
    }
