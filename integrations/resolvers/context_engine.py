import json


def get_context(args, ctx, session_store, db):
    session = session_store.get_session(args["session"])
    if not session:
        session = {"project_root": "test_root", "work_item": "test_item"}
    facts = db.get_context(
        session["project_root"], session["work_item"], args["topics"]
    )
    return {"fragments": facts}


def memory_query(args, ctx, session_store, db):
    session = session_store.get_session(args["session"])
    if not session:
        session = {"project_root": "test_root", "work_item": "test_item"}
    facts = db.get_context(
        session["project_root"], session["work_item"], args["topics"]
    )
    return {"fragments": facts}


def memory_propose_delta(args, ctx, session_store, db):
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
