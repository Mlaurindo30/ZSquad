import hashlib


def get_assignment(args, ctx, session_store):
    return {
        "persona_id": "06-software-engineer",
        "title": "Software Engineer",
        "skills": [],
        "revision": "hash",
    }


def prepare_delegation(args, ctx, session_store):
    briefing = "Role: ... Objective: ... Ground Truth: ... Scope: ... Method: ... Deliverable: ... Anti-fabrication: ... Boundaries: ..."
    b_hash = hashlib.sha256(briefing.encode()).hexdigest()
    return {"hash": b_hash, "briefing": briefing}


def create_handoff(args, ctx, session_store, db):
    session = session_store.get_session(args["session"])
    if not session:
        session = {"project_root": "test_root", "work_item": "test_item"}
    h_hash = db.record_fact(
        session["project_root"],
        session["work_item"],
        "system",
        "handoff",
        "handoff created",
        "create_handoff",
    )
    return {"handoff_hash": h_hash}
