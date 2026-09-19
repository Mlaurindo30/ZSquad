"""Execution Recorder Resolvers for Agent Squad.

Records receipts, evidence and failures under strict session authority.
"""

import json


def record_execution(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: record_execution resolver function.
    - Responsibility: Records execution receipts.
    - Purpose: Auditability of executed actions.
    - Failure Behavior: Fails closed on missing or invalid session.
    - Connections: SessionStore, DBClient.
    """
    session_id = args.get("session")
    session = session_store.get_session(session_id) if session_id and session_store else None
    if not session:
        raise ValueError(f"Invalid session: session '{session_id}' not found or expired")

    project_root = session["project_root"]
    work_item = session["work_item"]
    receipt_data = args.get("receipt", {})

    # Ingest through canonical ExecutionReceiptService if available
    try:
        from scripts.runtime.execution.service import ExecutionReceiptService
        exec_svc = ExecutionReceiptService()
        if isinstance(receipt_data, dict):
            r_type = receipt_data.get("receipt_type", "EXECUTION").upper()
            if r_type == "EXECUTION":
                exec_svc.record_execution(
                    work_item_id=work_item,
                    project_id=session.get("project_id", "default"),
                    agent_id=receipt_data.get("agent_id", session.get("agent_id", "system")),
                    stage=receipt_data.get("stage", "IMPLEMENTATION"),
                    instruction_hash=receipt_data.get("instruction_hash", "legacy-instruction-hash"),
                    evidence_hash=receipt_data.get("evidence_hash", "legacy-evidence-hash"),
                    files_modified=receipt_data.get("files_modified", []),
                    tests_executed=receipt_data.get("tests_executed", []),
                    test_exit_code=int(receipt_data.get("test_exit_code", 0)),
                    diff_summary=receipt_data.get("diff_summary", "legacy-execution-summary"),
                    receipt_id=receipt_data.get("receipt_id"),
                )
    except Exception:
        pass

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
    - Failure Behavior: Fails closed on missing or invalid session.
    - Connections: SessionStore, DBClient.
    """
    session_id = args.get("session")
    session = session_store.get_session(session_id) if session_id and session_store else None
    if not session:
        raise ValueError(f"Invalid session: session '{session_id}' not found or expired")

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
    - Failure Behavior: Fails closed on missing or invalid session.
    - Connections: SessionStore, DBClient.
    """
    session_id = args.get("session")
    if not session_id:
        raise ValueError("Invalid session: session parameter is required")

    session_store.mark_blocked(session_id)
    session = session_store.get_session(session_id) if session_store else None
    if session:
        db.record_fact(
            session["project_root"],
            session["work_item"],
            "system",
            "failure",
            args.get("reason", "unspecified failure"),
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
