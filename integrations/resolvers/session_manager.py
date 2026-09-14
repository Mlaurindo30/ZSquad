import os


def start_session(args, ctx, session_store):
    if not os.path.exists(args["project_root"]):
        raise ValueError("Project root does not exist")
    return session_store.create_session(
        args["host"],
        args["project_root"],
        args["work_item"],
        args["capability_report_hash"],
    )


def resume_session(args, ctx, session_store):
    return session_store.resume_session(args["session"], args["last_revision"])
