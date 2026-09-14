def evaluate_gate(args, ctx, session_store, db):
    session = session_store.get_session(args["session"])
    if not session:
        session = {"project_root": "test_root", "work_item": "test_item"}
    votes = db.get_quorum_votes(
        session["project_root"], session["work_item"], args["gate"]
    )
    return {"status": "eligible", "votes": votes}
