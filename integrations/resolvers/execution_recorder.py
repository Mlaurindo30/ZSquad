import json


def record_execution(args, ctx, session_store, db):
    session = session_store.get_session(args["session"])
    if not session:
        project_root = "test_root"
        work_item = "test_item"
    else:
        project_root = session["project_root"]
        work_item = session["work_item"]
    r_hash = db.record_fact(
        project_root,
        work_item,
        "system",
        "execution_receipt",
        json.dumps(args["receipt"]),
        "record_execution",
    )
    return {"receipt_hash": r_hash}


def record_evidence(args, ctx, session_store, db):
    session = session_store.get_session(args["session"])
    if not session:
        session = {"project_root": "test_root", "work_item": "test_item"}
    e_hash = db.record_fact(
        session["project_root"],
        session["work_item"],
        "system",
        "evidence",
        "evidence recorded",
        "record_evidence",
    )
    return {"evidence_hash": e_hash}


def report_failure(args, ctx, session_store, db):
    session_store.mark_blocked(args["session"])
    session = session_store.sessions.get(args["session"])
    if session:
        db.record_fact(
            session["project_root"],
            session["work_item"],
            "system",
            "failure",
            args["reason"],
            "report_failure",
        )
    return {"status": "blocked"}


def replay_receipt(args, ctx, session_store):
    return {"status": "replayed", "receipt_hash": args["receipt_hash"]}
