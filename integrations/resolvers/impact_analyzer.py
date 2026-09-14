def preflight(args, ctx, session_store):
    return {"status": "allow", "reason": "Paths exist and session is active"}


def impact_analysis(args, ctx, session_store, db):
    impact = db.get_impact_analysis(args["paths"])
    return {"blast_radius": impact}
