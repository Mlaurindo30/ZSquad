"""Context Engine Resolvers for Agent Squad.

Retrieves and proposes facts for memory and context under strict session authority.
"""

import json


def get_context(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: get_context resolver function.
    - Responsibility: Retrieves project context.
    - Purpose: Supply agents with required context.
    - Failure Behavior: Fails closed if session is invalid or missing.
    - Connections: SessionStore, DBClient.
    """
    session_id = args.get("session")
    session = session_store.get_session(session_id) if session_id and session_store else None
    if not session:
        raise ValueError(f"Invalid session: session '{session_id}' not found or expired")

    facts = db.get_context(
        session["project_root"], session["work_item"], args["topics"]
    )
    return {"fragments": facts}


def memory_query(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: memory_query resolver function.
    - Responsibility: Queries memory database for facts.
    - Purpose: Agent recall.
    - Failure Behavior: Fails closed if session is invalid or missing.
    - Connections: SessionStore, DBClient.
    """
    session_id = args.get("session")
    session = session_store.get_session(session_id) if session_id and session_store else None
    if not session:
        raise ValueError(f"Invalid session: session '{session_id}' not found or expired")

    facts = db.get_context(
        session["project_root"], session["work_item"], args["topics"]
    )
    return {"fragments": facts}


def memory_propose_delta(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: memory_propose_delta resolver function.
    - Responsibility: Proposes changes to memory facts.
    - Purpose: Update agent memory over time.
    - Failure Behavior: Fails closed if session is invalid or missing.
    - Connections: SessionStore, DBClient.
    """
    session_id = args.get("session")
    session = session_store.get_session(session_id) if session_id and session_store else None
    if not session:
        raise ValueError(f"Invalid session: session '{session_id}' not found or expired")

    p_hash = db.record_fact(
        session["project_root"],
        session["work_item"],
        "system",
        "memory_proposal",
        json.dumps(args["proposal"]),
        "memory_propose_delta",
    )
    return {"proposal_hash": p_hash}
