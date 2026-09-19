import hashlib
import os
from pathlib import Path
import sys

# Ensure repository root is in sys.path when invoked by standalone MCP runners
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from integrations.resolvers import load_yaml
except ModuleNotFoundError:
    from resolvers import load_yaml


def get_assignment(args, ctx, session_store):
    """
    Component Contract:
    - Definition: get_assignment resolver function (Compatibility facade over R8 SpecialistRouter).
    - Responsibility: Routes a task to the canonical specialist via R8 SpecialistRouter.
    - Purpose: Dispatch the right agent for a task without silent fallback.
    - Failure Behavior: Returns status BLOCKED/NEEDS_ROUTING with persona_id=None. Never silently defaults to software-engineer.
    - Connections: R8 SpecialistRouter, AgentRegistry, SessionStore.
    """
    from scripts.domain.delegation import RoutingRequest, RoutingStatus
    from scripts.runtime.routing.registry import AgentRegistry
    from scripts.runtime.routing.router import SpecialistRouter

    config_dir = ctx.config_dir
    registry_path = os.path.join(config_dir, "agent-registry.yaml")
    registry_data = load_yaml(registry_path)

    agent_registry = AgentRegistry(registry_data)
    router = SpecialistRouter(agent_registry)

    session_id = args.get("session")
    session = session_store.get_session(session_id) if session_id and session_store else None
    work_item_id = (session and session.get("work_item")) or args.get("work_item_id") or "WORK-ITEM-LEGACY"
    project_id = (session and session.get("project_root")) or args.get("project_id") or "default"

    # Translate legacy args into canonical RoutingRequest
    objective = (args.get("objective_digest") or "").strip()
    target_role = args.get("target_role") or args.get("role")
    required_capability = args.get("required_capability") or args.get("capability")
    stage = args.get("stage") or "IMPLEMENTATION"

    if not target_role and objective:
        resolved_agent = agent_registry.resolve_role(objective)
        if resolved_agent:
            target_role = resolved_agent.agent_id
        else:
            matched_caps = agent_registry.find_by_capability(objective)
            if matched_caps:
                required_capability = objective
            else:
                target_role = objective

    request = RoutingRequest(
        work_item_id=work_item_id,
        project_id=project_id,
        stage=stage,
        work_item_kind="STORY",
        cycle_id="development",
        required_role=target_role,
        required_capability=required_capability,
    )

    decision = router.route(request)

    if decision.status == RoutingStatus.ASSIGNED:
        selected_id = decision.selected_agent_id
        selected_agent = agent_registry.get_agent(selected_id)
        raw_agent = next((a for a in registry_data.get("agents", []) if a.get("id") == selected_id), None) or {}

        # Preserve minimum compatibility skills shape for legacy callers (R9 will formalize)
        skills = []
        manifest_rel = raw_agent.get("manifest")
        if manifest_rel:
            root_dir = os.path.abspath(os.path.join(config_dir, ".."))
            manifest_path = os.path.join(root_dir, manifest_rel)
            try:
                manifest = load_yaml(manifest_path)
                for s in manifest.get("assigned", []) + manifest.get("native", []):
                    if isinstance(s, dict) and "path" in s:
                        skills.append(s["path"])
            except Exception:
                pass

        rev_content = f"{selected_id}:{len(skills)}"
        rev_hash = hashlib.sha256(rev_content.encode("utf-8")).hexdigest()

        try:
            from scripts.runtime.activation.skills import SkillResolver
            skill_resolver = SkillResolver(root_dir)
            resolved = skill_resolver.resolve_skills(selected_id)
            final_skills = resolved.load_order
        except Exception:
            max_limit = min(len(skills), 7)
            final_skills = skills[:max_limit]

        return {
            "persona_id": selected_id,
            "title": (selected_agent and selected_agent.title) or raw_agent.get("title"),
            "skills": final_skills,
            "revision": rev_hash,
            "status": "ASSIGNED",
        }

    # Fail closed: BLOCKED or NEEDS_ROUTING (NEVER software-engineer fallback)
    return {
        "persona_id": None,
        "title": None,
        "skills": [],
        "revision": None,
        "status": decision.status.value,
        "reason": decision.reason,
        "candidates": decision.candidates,
    }


def prepare_delegation(args, ctx, session_store):
    """
    Component Contract:
    - Definition: prepare_delegation resolver function.
    - Responsibility: Prepares the canonical briefing for a target role.
    - Purpose: Standardize task briefings for agents.
    - Failure Behavior: Fails closed if session is missing, invalid or expired.
    - Connections: SessionStore.
    """
    session_id = args.get("session")
    if not session_id or not str(session_id).strip():
        raise ValueError("Invalid session: session parameter is missing")

    session = session_store.get_session(session_id) if session_store else None
    if not session:
        raise ValueError(f"Invalid session: session '{session_id}' not found or expired")

    target_role = args.get("target_role", "software-engineer")
    scope = args.get("scope", "General SDLC execution")
    action = args.get("action", "Implementation")
    project_root = session.get("project_root", os.getcwd())
    work_item = session.get("work_item", "UNSPECIFIED")

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

    rendered_prompt = ""
    try:
        from scripts.runtime.activation.errors import CompilationError
        from scripts.render_agent_prompt import render_agent_prompt
        w_item = work_item if work_item != "UNSPECIFIED" else None
        rendered_prompt = render_agent_prompt(agent=target_role, work_item=w_item)
    except CompilationError:
        raise
    except Exception:
        try:
            from scripts.render_agent_prompt import render_agent_prompt
            rendered_prompt = render_agent_prompt(agent=target_role, work_item=None)
        except CompilationError:
            raise
        except Exception:
            rendered_prompt = briefing

    if not rendered_prompt:
        rendered_prompt = briefing

    p_hash = hashlib.sha256(rendered_prompt.encode("utf-8")).hexdigest()
    return {"hash": p_hash, "briefing": briefing, "rendered_prompt": rendered_prompt}


def create_handoff(args, ctx, session_store, db):
    """
    Component Contract:
    - Definition: create_handoff resolver function.
    - Responsibility: Records a handoff creation fact.
    - Purpose: Track transitions and evidence hashes.
    - Failure Behavior: Fails closed with ValueError if session is not found or expired.
    - Connections: SessionStore, DBClient.
    """
    session_id = args.get("session")
    if not session_id or not str(session_id).strip():
        raise ValueError("Invalid session: session parameter is missing")

    session = session_store.get_session(session_id) if session_store else None
    if not session:
        raise ValueError(f"Invalid session: session '{session_id}' not found or expired")

    h_hash = db.record_fact(
        session["project_root"],
        session["work_item"],
        "system",
        "handoff",
        "handoff created",
        "create_handoff",
    )
    return {"handoff_hash": h_hash}
