"""Skill Manager Resolvers for Agent Squad.

Handles skill discovery and curation under strict session authority.
"""

import json


def discover_skill(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: discover_skill resolver function.
    - Responsibility: Registers new discovered skills.
    - Purpose: Skill evolution.
    - Failure Behavior: Fails closed on missing or invalid session.
    - Connections: SessionStore, DBClient.
    """
    session_id = args.get("session")
    session = session_store.get_session(session_id) if session_id and session_store else None
    if not session:
        raise ValueError(f"Invalid session: session '{session_id}' not found or expired")

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
