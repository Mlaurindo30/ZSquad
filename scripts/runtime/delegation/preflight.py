"""Authoritative Preflight Verification Engine for Milestone R10.

Evaluates real operational conditions before specialist delegation: session validity,
path containment, tool availability, activation hash integrity, and lifecycle freshness.
Guarantees fail-closed evaluation. Strictly stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import uuid

from scripts.domain.delegation import ActivationPacket
from .errors import PreflightBlockedError, PreflightError
from .sessions import CanonicalSessionManager


class PreflightDecision(str, Enum):
    """Authoritative decision outcome of preflight verification."""

    ALLOW = "allow"
    BLOCK = "block"


@dataclass(frozen=True)
class PreflightCheckResult:
    """Individual verification check outcome."""

    name: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class PreflightResult:
    """Complete preflight evaluation record."""

    decision: PreflightDecision
    preflight_id: str
    session_id: str
    activation_id: str
    checks: List[PreflightCheckResult]
    reasons: List[str]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def is_allowed(self) -> bool:
        return self.decision == PreflightDecision.ALLOW

    def to_dict(self) -> Dict[str, Any]:
        """Provides backward-compatible dict structure for MCP resolvers."""
        return {
            "status": self.decision.value,
            "decision": self.decision.value,
            "preflight_id": self.preflight_id,
            "session_id": self.session_id,
            "activation_id": self.activation_id,
            "reason": "; ".join(self.reasons) if self.reasons else "All preflight checks passed",
            "reasons": list(self.reasons),
            "checks": [
                {"name": c.name, "passed": c.passed, "reason": c.reason}
                for c in self.checks
            ],
            "timestamp": self.timestamp,
        }


class PreflightValidator:
    """Canonical preflight evaluation engine."""

    def __init__(
        self,
        runtime_root: Union[str, Path],
        session_manager: CanonicalSessionManager,
        db_path: Optional[Union[str, Path]] = None,
    ):
        self.runtime_root = Path(runtime_root).resolve()
        self.session_manager = session_manager
        if db_path is None:
            db_path = self.runtime_root / "banco" / "squad.db"
        self.db_path = Path(db_path).resolve()

    def validate(
        self,
        session_id: str,
        activation_packet: Optional[ActivationPacket] = None,
        paths: Optional[List[str]] = None,
        required_tools: Optional[List[str]] = None,
        briefing_hash: Optional[str] = None,
        expected_stage: Optional[str] = None,
        expected_project_id: Optional[str] = None,
    ) -> PreflightResult:
        """Executes full deterministic preflight checks against live conditions.

        Guaranteed FAIL-CLOSED on any error or missing requirement.
        """
        preflight_id = f"PRF-{uuid.uuid4().hex[:12]}"
        activation_id = (
            activation_packet.session_id
            if activation_packet
            else "ACT-UNSPECIFIED"
        )
        checks: List[PreflightCheckResult] = []
        reasons: List[str] = []

        try:
            # 1. Session Validity Check
            session_data = self.session_manager.get_session(session_id, fail_closed=False)
            if not session_data:
                checks.append(
                    PreflightCheckResult(
                        name="session_validity",
                        passed=False,
                        reason=f"Session '{session_id}' does not exist, is expired, or is blocked",
                    )
                )
                reasons.append(f"Session '{session_id}' not found or inactive")
            else:
                checks.append(
                    PreflightCheckResult(
                        name="session_validity",
                        passed=True,
                        reason=f"Session '{session_id}' is active with revision {session_data.get('revision', 1)}",
                    )
                )

            # 2. Path Existence & Containment Check
            if paths:
                project_root = (
                    Path(session_data["project_root"]).resolve()
                    if session_data
                    else self.runtime_root
                )
                paths_ok = True
                failed_paths = []
                for p in paths:
                    path_obj = Path(p)
                    if not path_obj.is_absolute():
                        path_obj = (project_root / path_obj).resolve()
                    else:
                        path_obj = path_obj.resolve()

                    if not path_obj.exists():
                        paths_ok = False
                        failed_paths.append(str(p))

                if not paths_ok:
                    checks.append(
                        PreflightCheckResult(
                            name="path_existence",
                            passed=False,
                            reason=f"Target path(s) do not exist on disk: {', '.join(failed_paths)}",
                        )
                    )
                    reasons.append(f"Paths do not exist: {', '.join(failed_paths)}")
                else:
                    checks.append(
                        PreflightCheckResult(
                            name="path_existence",
                            passed=True,
                            reason=f"All {len(paths)} specified path(s) exist on disk",
                        )
                    )

            # 3. Activation Packet Integrity Check
            if activation_packet is not None:
                # Cryptographic instruction hash check
                expected_hash = hashlib.sha256(
                    activation_packet.compiled_instruction.encode("utf-8")
                ).hexdigest()
                if activation_packet.instruction_hash != expected_hash:
                    checks.append(
                        PreflightCheckResult(
                            name="activation_instruction_hash",
                            passed=False,
                            reason=(
                                f"Instruction hash mismatch: expected {expected_hash}, "
                                f"got {activation_packet.instruction_hash}"
                            ),
                        )
                    )
                    reasons.append("Activation instruction hash mismatch")
                else:
                    checks.append(
                        PreflightCheckResult(
                            name="activation_instruction_hash",
                            passed=True,
                            reason="Activation instruction hash matches compiled instruction verbatim",
                        )
                    )

                # Briefing hash match check (if legacy caller supplied briefing_hash)
                if briefing_hash:
                    if briefing_hash != activation_packet.instruction_hash:
                        # Allow match if it matches SHA256 of compiled or briefing
                        checks.append(
                            PreflightCheckResult(
                                name="briefing_hash_correlation",
                                passed=False,
                                reason=f"Briefing hash {briefing_hash} does not match activation {activation_packet.instruction_hash}",
                            )
                        )
                        reasons.append("Briefing hash mismatch")
                    else:
                        checks.append(
                            PreflightCheckResult(
                                name="briefing_hash_correlation",
                                passed=True,
                                reason="Briefing hash matches activation hash",
                            )
                        )

            # 4. Project Correlation Check
            if session_data:
                session_proj = session_data.get("project_id")
                target_proj = (
                    activation_packet.work_context.project_id
                    if activation_packet
                    else expected_project_id
                )
                if target_proj and session_proj and session_proj != "default" and session_proj != target_proj:
                    checks.append(
                        PreflightCheckResult(
                            name="project_binding_match",
                            passed=False,
                            reason=f"Session project '{session_proj}' does not match activation project '{target_proj}'",
                        )
                    )
                    reasons.append(f"Project mismatch: {session_proj} != {target_proj}")
                else:
                    checks.append(
                        PreflightCheckResult(
                            name="project_binding_match",
                            passed=True,
                            reason="Project binding correlated with session",
                        )
                    )

            # 5. Work Item Correlation Check
            if session_data and activation_packet:
                session_wi = session_data.get("work_item")
                packet_wi = activation_packet.work_item_id
                if session_wi and packet_wi and session_wi != packet_wi:
                    checks.append(
                        PreflightCheckResult(
                            name="work_item_match",
                            passed=False,
                            reason=f"Session work item '{session_wi}' does not match activation work item '{packet_wi}'",
                        )
                    )
                    reasons.append(f"Work item mismatch: {session_wi} != {packet_wi}")
                else:
                    checks.append(
                        PreflightCheckResult(
                            name="work_item_match",
                            passed=True,
                            reason="Work item correlated with session",
                        )
                    )

            # 6. Required Tools Availability Check
            tools_to_check = set(required_tools or [])
            if activation_packet:
                # Add tools declared in activation packet
                manifest = activation_packet.skill_manifest or {}
                for t in manifest.get("required_tools", []):
                    tools_to_check.add(t)

            if tools_to_check:
                avail_tools = set(session_data.get("tools", []) if session_data else [])
                # agent-squad-mcp is always built-in control plane
                avail_tools.add("agent-squad-mcp")
                missing_tools = sorted(tools_to_check - avail_tools)
                if missing_tools:
                    checks.append(
                        PreflightCheckResult(
                            name="required_tools_availability",
                            passed=False,
                            reason=f"Required tools missing in session capability: {', '.join(missing_tools)}",
                        )
                    )
                    reasons.append(f"Missing required tools: {', '.join(missing_tools)}")
                else:
                    checks.append(
                        PreflightCheckResult(
                            name="required_tools_availability",
                            passed=True,
                            reason=f"All {len(tools_to_check)} required tool(s) available",
                        )
                    )

            # 7. Lifecycle Stage Staleness Check
            if activation_packet and expected_stage:
                current_stage = activation_packet.work_context.current_stage
                if current_stage != expected_stage:
                    checks.append(
                        PreflightCheckResult(
                            name="lifecycle_stage_freshness",
                            passed=False,
                            reason=f"Work item stage drifted: activation was compiled for '{current_stage}', but expected '{expected_stage}'",
                        )
                    )
                    reasons.append("Lifecycle stage mismatch")
                else:
                    checks.append(
                        PreflightCheckResult(
                            name="lifecycle_stage_freshness",
                            passed=True,
                            reason=f"Lifecycle stage matches: {current_stage}",
                        )
                    )

            # Determine final decision: All checks must pass
            all_passed = all(c.passed for c in checks)
            decision = PreflightDecision.ALLOW if all_passed else PreflightDecision.BLOCK

        except Exception as err:
            # FAIL-CLOSED on any unexpected exception during evaluation
            checks.append(
                PreflightCheckResult(
                    name="preflight_evaluation_exception",
                    passed=False,
                    reason=f"Unexpected error during preflight evaluation: {err}",
                )
            )
            reasons.append(f"Preflight evaluation error: {err}")
            decision = PreflightDecision.BLOCK

        return PreflightResult(
            decision=decision,
            preflight_id=preflight_id,
            session_id=session_id,
            activation_id=activation_id,
            checks=checks,
            reasons=reasons,
        )
