"""Domain models for Delegation, Assignments, Hierarchical Context, Host Capabilities and Routing.

Strictly stdlib-only.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional

from .common import BaseDomainModel, ValidationError
from .work_items import AcceptanceCriterion, WorkItemKind


class AssignmentStatus(str, Enum):
    ASSIGNED = "ASSIGNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class ExecutionAssignment(BaseDomainModel):
    """Assignment linking an individual agent to a work item stage."""

    assignment_id: str
    work_item_id: str
    agent_id: str
    assigned_role: str
    stage: str
    status: AssignmentStatus
    assigned_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if not self.assignment_id or not self.assignment_id.strip():
            raise ValidationError("assignment_id must not be empty")
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValidationError("work_item_id must not be empty")
        if not self.agent_id or not self.agent_id.strip():
            raise ValidationError("agent_id must not be empty")
        if not self.assigned_role or not self.assigned_role.strip():
            raise ValidationError("assigned_role must not be empty")
        if not self.stage or not self.stage.strip():
            raise ValidationError("stage must not be empty")


@dataclass(frozen=True)
class AncestorSnapshot(BaseDomainModel):
    """Snapshot of an ancestor work item providing hierarchical context (R0-DEL-007)."""

    work_item_id: str
    kind: WorkItemKind
    title: str
    stage: str
    spec_summary: str

    def __post_init__(self) -> None:
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValidationError("Ancestor work_item_id must not be empty")
        if not self.title or not self.title.strip():
            raise ValidationError("Ancestor title must not be empty")


@dataclass(frozen=True)
class WorkContext(BaseDomainModel):
    """Complete context package provided to subagents during execution."""

    work_item_id: str
    project_id: str
    current_stage: str
    title: str
    description: str
    definition_of_done: List[str]
    acceptance_criteria: List[AcceptanceCriterion]
    ancestors: List[AncestorSnapshot]
    ancestor_artifacts: Dict[str, str]
    active_receipts: List[str]
    filesystem_scope: List[str]

    def __post_init__(self) -> None:
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValidationError("work_item_id must not be empty")
        if not self.project_id or not self.project_id.strip():
            raise ValidationError("project_id must not be empty")
        if not self.current_stage or not self.current_stage.strip():
            raise ValidationError("current_stage must not be empty")


@dataclass(frozen=True)
class ActivationPacket(BaseDomainModel):
    """Immutable data package delivered to HostRuntime to activate a specialist subagent."""

    session_id: str
    agent_id: str
    role_name: str
    work_item_id: str
    work_context: WorkContext
    skill_manifest: Dict[str, Any]
    compiled_instruction: str
    instruction_hash: str
    created_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if not self.session_id or not self.session_id.strip():
            raise ValidationError("session_id must not be empty")
        if not self.agent_id or not self.agent_id.strip():
            raise ValidationError("agent_id must not be empty")
        if not self.role_name or not self.role_name.strip():
            raise ValidationError("role_name must not be empty")
        if not self.compiled_instruction or not self.compiled_instruction.strip():
            raise ValidationError("compiled_instruction must not be empty")

        expected_hash = hashlib.sha256(self.compiled_instruction.encode("utf-8")).hexdigest()
        if self.instruction_hash != expected_hash:
            raise ValidationError(
                f"Instruction hash mismatch in ActivationPacket: expected {expected_hash}, got {self.instruction_hash}"
            )


@dataclass(frozen=True)
class DelegationEnvelope(BaseDomainModel):
    """Authoritative instruction payload for specialist agent delegation (R0-DEL-003, R0-DEL-004)."""

    delegation_id: str
    sender_role: str
    target_role: str
    work_item_id: str
    scope_summary: str
    action_requested: str
    compiled_instruction: str
    instruction_hash: str

    def __post_init__(self) -> None:
        if not self.delegation_id or not self.delegation_id.strip():
            raise ValidationError("delegation_id must not be empty")
        if not self.sender_role or not self.sender_role.strip():
            raise ValidationError("sender_role must not be empty")
        if not self.target_role or not self.target_role.strip():
            raise ValidationError("target_role must not be empty")
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValidationError("work_item_id must not be empty")
        if not self.compiled_instruction or not self.compiled_instruction.strip():
            raise ValidationError("compiled_instruction must not be empty")

        expected_hash = hashlib.sha256(self.compiled_instruction.encode("utf-8")).hexdigest()
        if self.instruction_hash != expected_hash:
            raise ValidationError(
                f"Instruction hash mismatch: expected {expected_hash}, got {self.instruction_hash}"
            )

    @classmethod
    def create(
        cls,
        delegation_id: str,
        sender_role: str,
        target_role: str,
        work_item_id: str,
        scope_summary: str,
        action_requested: str,
        compiled_instruction: str,
    ) -> "DelegationEnvelope":
        """Creates delegation envelope computing authoritative instruction hash."""
        if not compiled_instruction or not compiled_instruction.strip():
            raise ValidationError("compiled_instruction must not be empty")
        computed_hash = hashlib.sha256(compiled_instruction.encode("utf-8")).hexdigest()
        return cls(
            delegation_id=delegation_id,
            sender_role=sender_role,
            target_role=target_role,
            work_item_id=work_item_id,
            scope_summary=scope_summary,
            action_requested=action_requested,
            compiled_instruction=compiled_instruction,
            instruction_hash=computed_hash,
        )


@dataclass(frozen=True)
class HostCapabilities(BaseDomainModel):
    """Abstract host capabilities matrix decoupling runtime logic from host provider names."""

    has_subagent_dispatch: bool
    has_filesystem_write: bool
    has_terminal_execution: bool
    has_mcp_client: bool
    has_background_tasks: bool
    max_token_context: int

    def __post_init__(self) -> None:
        if self.max_token_context <= 0:
            raise ValidationError("max_token_context must be a positive integer")


# ---------------------------------------------------------------------------
# R8 — Stage-Aware Specialist Routing Domain Models
# ---------------------------------------------------------------------------


class RoutingStatus(str, Enum):
    """Deterministic outcome of a routing decision. No silent fallback."""

    ASSIGNED = "ASSIGNED"
    NEEDS_ROUTING = "NEEDS_ROUTING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class RoutingRequest(BaseDomainModel):
    """Input contract for the routing engine.

    Captures the demand: which work item, at which stage, with which
    capability or role requirement needs a specialist assigned.
    """

    work_item_id: str
    project_id: str
    stage: str
    work_item_kind: str
    cycle_id: str
    trigger_type: Optional[str] = None
    required_role: Optional[str] = None
    required_capability: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValidationError("work_item_id must not be empty")
        if not self.project_id or not self.project_id.strip():
            raise ValidationError("project_id must not be empty")
        if not self.stage or not self.stage.strip():
            raise ValidationError("stage must not be empty")
        if not self.work_item_kind or not self.work_item_kind.strip():
            raise ValidationError("work_item_kind must not be empty")
        if not self.cycle_id or not self.cycle_id.strip():
            raise ValidationError("cycle_id must not be empty")


@dataclass(frozen=True)
class RoutingDecision(BaseDomainModel):
    """Output contract of the routing engine.

    Immutable, deterministic, auditable routing decision.
    No silent fallback to software-engineer. Zero candidates → BLOCKED.
    """

    decision_id: str
    work_item_id: str
    stage: str
    required_role: Optional[str]
    required_capability: Optional[str]
    candidates: List[str]
    selected_agent_id: Optional[str]
    status: RoutingStatus
    reason: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if not self.decision_id or not self.decision_id.strip():
            raise ValidationError("decision_id must not be empty")
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValidationError("work_item_id must not be empty")
        if not self.stage or not self.stage.strip():
            raise ValidationError("stage must not be empty")
        if not self.reason or not self.reason.strip():
            raise ValidationError("reason must not be empty")

        # Invariant: ASSIGNED requires exactly one selected agent
        if self.status == RoutingStatus.ASSIGNED:
            if not self.selected_agent_id or not self.selected_agent_id.strip():
                raise ValidationError(
                    "RoutingDecision with status ASSIGNED must have a selected_agent_id"
                )

        # Invariant: BLOCKED/NEEDS_ROUTING must NOT have selected agent
        if self.status in (RoutingStatus.BLOCKED, RoutingStatus.NEEDS_ROUTING):
            if self.selected_agent_id and self.selected_agent_id.strip():
                raise ValidationError(
                    f"RoutingDecision with status {self.status.value} must not have a selected_agent_id"
                )

