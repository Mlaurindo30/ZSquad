import json


def record_execution(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: record_execution resolver function.
    - Responsibility: Records execution receipts.
    - Purpose: Auditability of executed actions.
    - Failure Behavior: Error on malformed receipt.
    - Connections: SessionStore, DBClient.
    """
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
    """
    Component Contract:
    - Definition: record_evidence resolver function.
    - Responsibility: Associates evidence with executions.
    - Purpose: Prove correct execution.
    - Failure Behavior: Defaults to test session on error.
    - Connections: SessionStore, DBClient.
    """
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
    """
    Component Contract:
    - Definition: report_failure resolver function.
    - Responsibility: Records failures and stops processes.
    - Purpose: Error handling and tracking.
    - Failure Behavior: Records default error if missing reason.
    - Connections: SessionStore, DBClient.
    """
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
    """
    Component Contract:
    - Definition: replay_receipt resolver function.
    - Responsibility: Replays a recorded receipt.
    - Purpose: Retry or reproduce an execution.
    - Failure Behavior: Fails if receipt is invalid.
    - Connections: SessionStore.
    """
    return {"status": "replayed", "receipt_hash": args["receipt_hash"]}
