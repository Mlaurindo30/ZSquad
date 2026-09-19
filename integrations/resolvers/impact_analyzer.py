"""Impact Analyzer and Preflight Verification Resolvers for Agent Squad.

Provides blast radius calculation and deterministic preflight verification.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

try:
    from scripts.runtime.delegation.preflight import PreflightDecision, PreflightValidator
except ImportError:
    PreflightValidator = None
    PreflightDecision = None


def preflight(args: Dict[str, Any], ctx: Any, session_store: Any) -> Dict[str, Any]:
    """
    Component Contract:
    - Definition: preflight resolver function.
    - Responsibility: Validates paths, session and preconditions before execution.
    - Purpose: Fail closed before dispatching specialist agents.
    - Failure Behavior: Returns status 'block' on missing session, invalid paths, or failed checks.
    - Connections: SessionStore, PreflightValidator.
    """
    session_id = args.get("session")
    if not session_id or not str(session_id).strip():
        return {
            "status": "block",
            "decision": "block",
            "reason": "Session parameter is missing or empty",
            "reasons": ["Session parameter is missing or empty"],
        }

    session = session_store.get_session(session_id) if session_store else None
    if not session:
        return {
            "status": "block",
            "decision": "block",
            "reason": f"Session '{session_id}' not found, expired or blocked",
            "reasons": [f"Session '{session_id}' not found, expired or blocked"],
        }

    # Validate paths if provided
    paths = args.get("paths", [])
    if paths:
        project_root = Path(session.get("project_root", os.getcwd())).resolve()
        failed_paths = []
        for p in paths:
            path_obj = Path(p)
            if not path_obj.is_absolute():
                path_obj = (project_root / path_obj).resolve()
            else:
                path_obj = path_obj.resolve()
            if not path_obj.exists():
                failed_paths.append(str(p))

        if failed_paths:
            return {
                "status": "block",
                "decision": "block",
                "reason": f"Target path(s) do not exist on disk: {', '.join(failed_paths)}",
                "reasons": [f"Target path(s) do not exist on disk: {', '.join(failed_paths)}"],
            }

    # If PreflightValidator is available and manager exists, run full suite
    if (
        PreflightValidator is not None
        and hasattr(session_store, "_manager")
        and session_store._manager is not None
    ):
        try:
            validator = PreflightValidator(
                runtime_root=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                session_manager=session_store._manager,
            )
            res = validator.validate(
                session_id=session_id,
                paths=paths,
                briefing_hash=args.get("briefing_hash"),
            )
            return res.to_dict()
        except Exception as err:
            return {
                "status": "block",
                "decision": "block",
                "reason": f"Preflight evaluation error: {err}",
                "reasons": [f"Preflight evaluation error: {err}"],
            }

    return {
        "status": "allow",
        "decision": "allow",
        "reason": "Paths exist and session is active",
        "reasons": ["Paths exist and session is active"],
    }


def impact_analysis(args: Dict[str, Any], ctx: Any, session_store: Any, db: Any) -> Dict[str, Any]:
    """
    Component Contract:
    - Definition: impact_analysis resolver function.
    - Responsibility: Analyzes blast radius of changes.
    - Purpose: Protect critical paths.
    - Failure Behavior: Returns high impact score on error.
    - Connections: SessionStore, DBClient.
    """
    session_id = args.get("session")
    if session_id and session_store:
        session = session_store.get_session(session_id)
        if not session:
            raise ValueError(f"Invalid session or session not found: '{session_id}'")

    impact = db.get_impact_analysis(args.get("paths", []))
    return {"blast_radius": impact}
