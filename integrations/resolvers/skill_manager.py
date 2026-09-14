import json


def discover_skill(args, ctx, session_store, db):
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
    return {"status": "updated", "decision": args["decision"]}
