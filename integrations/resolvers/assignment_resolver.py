import os
import hashlib
try:
    from integrations.resolvers import load_yaml
except ModuleNotFoundError:
    from resolvers import load_yaml


def get_assignment(args, ctx, session_store):
    """
    Component Contract:
    - Definition: get_assignment resolver function.
    - Responsibility: Matches an objective digest to the best agent persona.
    - Purpose: Dispatch the right agent for a task.
    - Failure Behavior: Fallback to software-engineer if no match found.
    - Connections: Agent registry, filesystem, SessionStore.
    """
    config_dir = ctx.config_dir
    registry_path = os.path.join(config_dir, "agent-registry.yaml")
    registry = load_yaml(registry_path)

    agents = registry.get("agents", [])
    objective = (args.get("objective_digest") or "").lower()

    # Scoring based persona matching
    best_agent = None
    best_score = 0
    words = [w.strip() for w in objective.replace("/", " ").replace("-", " ").split() if len(w.strip()) > 3]

    for agent in agents:
        aid = agent.get("id", "").lower()
        title = (agent.get("title") or "").lower()
        purpose = (agent.get("purpose") or "").lower()

        score = 0
        if aid in objective:
            score += 10
        for w in aid.split("-"):
            if len(w) > 3 and w in objective:
                score += 5
        for w in words:
            if w in title:
                score += 3
            if w in purpose:
                score += 1

        if score > best_score:
            best_score = score
            best_agent = agent

    selected_agent = best_agent if best_score > 0 else None

    # Fallback to software-engineer or first dispatchable agent
    if not selected_agent:
        selected_agent = next(
            (a for a in agents if a.get("id") == "software-engineer"),
            agents[0] if agents else {"id": "software-engineer", "title": "Software Engineer"}
        )

    # Load skills from agent manifest if available
    skills = []
    manifest_rel = selected_agent.get("manifest")
    if manifest_rel:
        root_dir = os.path.abspath(os.path.join(config_dir, ".."))
        manifest_path = os.path.join(root_dir, manifest_rel)
        manifest = load_yaml(manifest_path)
        for s in manifest.get("assigned", []) + manifest.get("native", []):
            if isinstance(s, dict) and "path" in s:
                skills.append(s["path"])

    rev_content = f"{selected_agent.get('id')}:{len(skills)}"
    rev_hash = hashlib.sha256(rev_content.encode("utf-8")).hexdigest()

    return {
        "persona_id": selected_agent.get("id"),
        "title": selected_agent.get("title"),
        "skills": skills[:7],  # Max 7 skills budget rule
        "revision": rev_hash,
    }


def prepare_delegation(args, ctx, session_store):
    """
    Component Contract:
    - Definition: prepare_delegation resolver function.
    - Responsibility: Prepares the canonical briefing for a target role.
    - Purpose: Standardize task briefings for agents.
    - Failure Behavior: Uses default boundaries if session is missing.
    - Connections: SessionStore.
    """
    target_role = args.get("target_role", "software-engineer")
    scope = args.get("scope", "General SDLC execution")
    action = args.get("action", "Implementation")
    session_id = args.get("session", "unknown")

    session = session_store.get_session(session_id)
    project_root = session.get("project_root", os.getcwd()) if session else os.getcwd()
    work_item = session.get("work_item", "UNSPECIFIED") if session else "UNSPECIFIED"

    briefing = (
        f"1. Role: {target_role}\n"
        f"2. Objective: Execute {action} within project boundaries.\n"
        f"3. Ground truth: Project root is '{project_root}', active work item is '{work_item}'.\n"
        f"4. Scope: {scope}\n"
        f"5. Method: Spec-driven development with proportional evidence gates. TDD / BDD.\n"
        f"6. Deliverable: Tested and verified code artifacts with zero lint regressions.\n"
        f"7. Anti-fabrication: Rely exclusively on real files and confirmed codebase symbols; emit NOT FOUND/UNVERIFIED if absent.\n"
        f"8. Boundaries: Strict cognitive protection (Max 8 Story Points); no unverified dependencies.\n"
    )
    b_hash = hashlib.sha256(briefing.encode("utf-8")).hexdigest()

    rendered_prompt = ""
    try:
        from scripts.render_agent_prompt import render_agent_prompt
        w_item = work_item if work_item != "UNSPECIFIED" else None
        rendered_prompt = render_agent_prompt(agent=target_role, work_item=w_item)
    except Exception:
        pass

    return {"hash": b_hash, "briefing": briefing, "rendered_prompt": rendered_prompt}


def create_handoff(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: create_handoff resolver function.
    - Responsibility: Records a handoff creation fact.
    - Purpose: Track transitions and evidence hashes.
    - Failure Behavior: Records against test session if not found.
    - Connections: SessionStore, DBClient.
    """
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
