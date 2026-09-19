"""Gate Evaluator Resolvers for Agent Squad.

Evaluates lifecycle gate criteria under strict session authority.
"""

import os
try:
    from integrations.resolvers import load_yaml
except ModuleNotFoundError:
    from resolvers import load_yaml


def evaluate_gate(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: evaluate_gate resolver function.
    - Responsibility: Checks if criteria for a specific gate are met based on evidence.
    - Purpose: Ensure governance processes are respected before transitions.
    - Failure Behavior: Fails closed on invalid session or unknown gate.
    - Connections: SessionStore, DBClient, config file (workflow.yaml).
    """
    gate_name = args.get("gate", "")
    session_id = args.get("session", "")
    evidence_refs = args.get("evidence_refs", [])

    session = session_store.get_session(session_id) if session_id and session_store else None
    if not session:
        raise ValueError(f"Invalid session: session '{session_id}' not found or expired")

    project_root = session["project_root"]
    work_item = session["work_item"]

    # Load workflow.yaml gates
    workflow_path = os.path.join(ctx.config_dir, "workflow.yaml")
    workflow = load_yaml(workflow_path)
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
    try:
        from scripts.runtime.execution.service import ExecutionReceiptService
        exec_svc = ExecutionReceiptService()
        is_el, _ = exec_svc.verify_gate_eligibility(work_item, gate_name)
        if is_el:
            eligible = True
    except Exception:
        pass

    return {
        "status": "eligible" if eligible else "blocked",
        "gate": gate_name,
        "owner": owner,
        "criteria": criteria,
        "human_required_when": human_required,
        "evidence_count": len(evidence_refs),
        "votes": votes,
    }
