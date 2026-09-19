import os
import sys
from pathlib import Path


def doctor(args, ctx, session_store):
    """
    Component Contract:
    - Definition: doctor resolver function.
    - Responsibility: Performs health check of MCP environment.
    - Purpose: Verify squad tooling health.
    - Failure Behavior: Returns failing status if issues found.
    - Connections: AgentSquad, SessionStore.
    """
    try:
        try:
            from agent_squad import AgentSquad
        except ModuleNotFoundError:
            scripts_dir = str(Path(__file__).resolve().parents[2] / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from agent_squad import AgentSquad

        runtime_root = Path(os.environ["SQUAD_RUNTIME"]).resolve() if os.environ.get("SQUAD_RUNTIME") else Path(__file__).resolve().parents[2]
        squad = AgentSquad(root=runtime_root)
        errors = squad.audit()
        if not errors:
            return {"status": "healthy", "report": "All checks passed"}
        return {"status": "unhealthy", "report": f"Audit found {len(errors)} issue(s)", "errors": errors}
    except Exception as exc:
        return {"status": "unhealthy", "report": str(exc), "errors": [str(exc)]}

