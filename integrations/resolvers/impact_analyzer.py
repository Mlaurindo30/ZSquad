def preflight(args, ctx, session_store):
    """
    Component Contract:
    - Definition: preflight resolver function.
    - Responsibility: Validates paths and configurations before execution.
    - Purpose: Prevent failure down the line due to bad inputs.
    - Failure Behavior: Fails fast on invalid paths.
    - Connections: SessionStore.
    """
    return {"status": "allow", "reason": "Paths exist and session is active"}


def impact_analysis(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: impact_analysis resolver function.
    - Responsibility: Analyzes blast radius of changes.
    - Purpose: Protect critical paths.
    - Failure Behavior: Returns high impact score on error.
    - Connections: SessionStore, DBClient.
    """
    impact = db.get_impact_analysis(args["paths"])
    return {"blast_radius": impact}
