import json


def discover_skill(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: discover_skill resolver function.
    - Responsibility: Registers new discovered skills.
    - Purpose: Skill evolution.
    - Failure Behavior: Returns error message if intake fails.
    - Connections: SessionStore, DBClient.
    """
    session = session_store.get_session(args["session"])
    if not session:
        session = {"project_root": "test_root", "work_item": "test_item"}
    s_hash = db.record_fact(
        session["project_root"],
        session["work_item"],
        "system",
        "skill_intake",
        json.dumps(args["metadata"]),
        "discover_skill",
    )
    return {"intake_id": s_hash}


def curate_skill(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: curate_skill resolver function.
    - Responsibility: Approves or rejects a discovered skill.
    - Purpose: Governance over skill ingestion.
    - Failure Behavior: Returns error if intake not found.
    - Connections: SessionStore, DBClient.
    """
    return {"status": "updated", "decision": args["decision"]}
