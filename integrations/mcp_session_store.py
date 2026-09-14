import time
import hashlib
import uuid
from typing import Dict, Any, Optional


class SessionStore:
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def create_session(
        self, host: str, project_root: str, work_item: str, capability_report_hash: str
    ) -> Dict[str, Any]:
        session_id = str(uuid.uuid4())
        ttl = 3600
        # simple hash for policy
        policy_hash = hashlib.sha256(
            f"{host}:{project_root}:{work_item}".encode()
        ).hexdigest()

        self.sessions[session_id] = {
            "session_id": session_id,
            "host": host,
            "project_root": project_root,
            "work_item": work_item,
            "capability_report_hash": capability_report_hash,
            "expires_at": time.time() + ttl,
            "policy_hash": policy_hash,
            "status": "active",
        }
        return {"session_id": session_id, "ttl": ttl, "policy_hash": policy_hash}

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = self.sessions.get(session_id)
        if (
            session
            and session["expires_at"] > time.time()
            and session["status"] != "blocked"
        ):
            return session
        return None

    def resume_session(self, session_id: str, last_revision: str) -> Dict[str, Any]:
        session = self.get_session(session_id)
        if not session:
            raise ValueError("Invalid or expired session")
        # For simplicity, we just extend the TTL
        session["expires_at"] = time.time() + 3600
        return {"session_id": session_id, "ttl": 3600, "status": session["status"]}

    def mark_blocked(self, session_id: str):
        session = self.sessions.get(session_id)
        if session:
            session["status"] = "blocked"
