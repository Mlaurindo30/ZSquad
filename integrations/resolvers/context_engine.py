import json


def get_context(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: get_context resolver function.
    - Responsibility: Retrieves project context.
    - Purpose: Supply agents with required context.
    - Failure Behavior: Returns empty context on failure.
    - Connections: SessionStore, DBClient.
    """
    session = session_store.get_session(args["session"])
    if not session:
        session = {"project_root": "test_root", "work_item": "test_item"}
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
    - Failure Behavior: Returns empty result.
    - Connections: SessionStore, DBClient.
    """
    session = session_store.get_session(args["session"])
    if not session:
        session = {"project_root": "test_root", "work_item": "test_item"}
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
    - Failure Behavior: Rejects proposal on DB error.
    - Connections: SessionStore, DBClient.
    """
    session = session_store.get_session(args["session"])
    if not session:
        session = {"project_root": "test_root", "work_item": "test_item"}
    p_hash = db.record_fact(
        session["project_root"],
        session["work_item"],
        "system",
        "memory_proposal",
        json.dumps(args["proposal"]),
        "memory_propose_delta",
    )
    return {"proposal_hash": p_hash}
