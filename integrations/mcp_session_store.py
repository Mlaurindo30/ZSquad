"""MCP Session Store for Agent Squad.

Provides session state management with TTL, revision tracking, capability hashing,
and SQLite WAL persistence via CanonicalSessionManager.
"""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any, Dict, Optional

try:
    from scripts.runtime.delegation.sessions import (
        CanonicalSessionManager,
        DEFAULT_SESSION_TTL_SECONDS,
    )
    from scripts.runtime.delegation.errors import (
        SessionBlockedError,
        SessionExpiredError,
        SessionNotFoundError,
        SessionRevisionConflictError,
    )
except ImportError:
    # Standalone or fallback
    CanonicalSessionManager = None
    DEFAULT_SESSION_TTL_SECONDS = 3600
    SessionBlockedError = ValueError
    SessionExpiredError = ValueError
    SessionNotFoundError = ValueError
    SessionRevisionConflictError = ValueError


class SessionStore:
    """Compatibility facade wrapping CanonicalSessionManager with SQLite persistence."""

    def __init__(self, db_path: Optional[str] = None):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "banco", "squad.db"
            )
        self.db_path = db_path
        if CanonicalSessionManager is not None:
            self._manager: Optional[CanonicalSessionManager] = CanonicalSessionManager(
                self.db_path
            )
        else:
            self._manager = None

    def create_session(
        self,
        host: str,
        project_root: str,
        work_item: str,
        capability_report_hash: str,
        ttl: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> Dict[str, Any]:
        """Creates a session and records it in both memory and SQLite."""
        if self._manager is not None:
            result = self._manager.create_session(
                host=host,
                project_root=project_root,
                work_item=work_item,
                capability_report_hash=capability_report_hash,
                ttl=ttl,
            )
            session_id = result["session_id"]
            session_dict = self._manager.get_session(session_id)
            if session_dict:
                self.sessions[session_id] = session_dict
            return result

        # Fallback if manager unavailable
        import hashlib
        import uuid

        session_id = str(uuid.uuid4())
        policy_hash = hashlib.sha256(
            f"{host}:{project_root}:{work_item}".encode()
        ).hexdigest()
        session_data = {
            "session_id": session_id,
            "host": host,
            "project_root": project_root,
            "work_item": work_item,
            "capability_report_hash": capability_report_hash,
            "expires_at": time.time() + ttl,
            "policy_hash": policy_hash,
            "revision": 1,
            "status": "active",
        }
        self.sessions[session_id] = session_data
        return {"session_id": session_id, "ttl": ttl, "policy_hash": policy_hash, "revision": 1}

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves an active, unexpired, unblocked session."""
        if not session_id or not isinstance(session_id, str):
            return None

        if self._manager is not None:
            sess = self._manager.get_session(session_id, fail_closed=False)
            if sess:
                self.sessions[session_id] = sess
                return sess
            # Clean up local cache if expired or missing
            self.sessions.pop(session_id, None)
            return None

        session = self.sessions.get(session_id)
        if (
            session
            and session["expires_at"] > time.time()
            and session["status"] != "blocked"
        ):
            return session
        return None

    def resume_session(self, session_id: str, last_revision: Any) -> Dict[str, Any]:
        """Resumes a session verifying revision integrity and extending TTL."""
        if self._manager is not None:
            try:
                res = self._manager.resume_session(
                    session_id=session_id,
                    last_revision=last_revision,
                )
                sess = self._manager.get_session(session_id)
                if sess:
                    self.sessions[session_id] = sess
                return res
            except Exception as err:
                # Wrap or propagate as ValueError for MCP compatibility
                if isinstance(err, ValueError):
                    raise
                raise ValueError(f"Invalid or expired session: {err}") from err

        session = self.get_session(session_id)
        if not session:
            raise ValueError("Invalid or expired session")

        curr_rev = session.get("revision", 1)
        try:
            req_rev = int(last_revision)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid revision: {last_revision}")

        if req_rev != curr_rev:
            raise ValueError(f"Session revision mismatch: {curr_rev} != {req_rev}")

        session["revision"] = curr_rev + 1
        session["expires_at"] = time.time() + 3600
        return {
            "session_id": session_id,
            "ttl": 3600,
            "status": session["status"],
            "revision": session["revision"],
        }

    def mark_blocked(self, session_id: str) -> None:
        """Marks a session as blocked."""
        if self._manager is not None:
            self._manager.mark_blocked(session_id)
        session = self.sessions.get(session_id)
        if session:
            session["status"] = "blocked"
