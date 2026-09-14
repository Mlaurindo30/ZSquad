import os


def start_session(args, ctx, session_store):
    """
    Component Contract:
    - Definition: start_session resolver function.
    - Responsibility: Initializes a new session for a given host and project.
    - Purpose: Start tracking work for a squad.
    - Failure Behavior: Fails if host or project root are missing.
    - Connections: SessionStore.
    """
    if not os.path.exists(args["project_root"]):
        raise ValueError("Project root does not exist")
    return session_store.create_session(
        args["host"],
        args["project_root"],
        args["work_item"],
        args["capability_report_hash"],
    )


def resume_session(args, ctx, session_store):
    """
    Component Contract:
    - Definition: resume_session resolver function.
    - Responsibility: Retrieves an existing session.
    - Purpose: Continue previous work.
    - Failure Behavior: Returns an error status if session missing.
    - Connections: SessionStore.
    """
    return session_store.resume_session(args["session"], args["last_revision"])
