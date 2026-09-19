"""Domain models for Backlog Planning, Breakdown Sizing and Artifact Materialization.

Strictly stdlib-only.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from .common import BaseDomainModel, ValidationError
from .work_items import FIBONACCI_SIZING_ALLOWED, WorkItemKind, WorkItemId


class BacklogPlanStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    MATERIALIZED = "MATERIALIZED"
    REJECTED = "REJECTED"


VALID_PLAN_TRANSITIONS: Dict[BacklogPlanStatus, Set[BacklogPlanStatus]] = {
    BacklogPlanStatus.DRAFT: {BacklogPlanStatus.VALIDATED, BacklogPlanStatus.REJECTED},
    BacklogPlanStatus.VALIDATED: {BacklogPlanStatus.APPROVED, BacklogPlanStatus.REJECTED},
    BacklogPlanStatus.APPROVED: {BacklogPlanStatus.MATERIALIZED, BacklogPlanStatus.REJECTED},
    BacklogPlanStatus.MATERIALIZED: set(),
    BacklogPlanStatus.REJECTED: set(),
}


@dataclass(frozen=True)
class BacklogPlanItem(BaseDomainModel):
    """A proposed work item within a BacklogPlan before physical materialization."""

    proposed_id: str
    kind: WorkItemKind
    title: str
    description: str
    story_points: Optional[int] = None
    parent_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        norm_id = WorkItemId.normalize(self.proposed_id)
        if norm_id != self.proposed_id:
            object.__setattr__(self, "proposed_id", norm_id)

        if not self.title or len(self.title.strip()) < 5:
            raise ValidationError("Plan item title must be at least 5 characters")
        if not self.description or len(self.description.strip()) < 10:
            raise ValidationError("Plan item description must be at least 10 characters")

        if self.story_points is not None:
            if self.story_points > 8:
                raise ValidationError(
                    f"Plan item '{self.proposed_id}' violates sizing: story_points ({self.story_points}) > 8 SP. "
                    "Must be sliced vertically before inclusion."
                )
            if self.story_points not in FIBONACCI_SIZING_ALLOWED:
                raise ValidationError(
                    f"Plan item '{self.proposed_id}' story_points ({self.story_points}) must be in {sorted(list(FIBONACCI_SIZING_ALLOWED))}"
                )


@dataclass(frozen=True)
class BacklogPlan(BaseDomainModel):
    """Backlog decomposition plan governing atomic review and materialization."""

    plan_id: str
    project_id: str
    status: BacklogPlanStatus
    items: List[BacklogPlanItem]
    created_by: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    approved_by: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.plan_id or not self.plan_id.strip():
            raise ValidationError("plan_id must not be empty")
        if not self.project_id or not self.project_id.strip():
            raise ValidationError("project_id must not be empty")
        if not self.created_by or not self.created_by.strip():
            raise ValidationError("created_by must not be empty")
        if not self.items:
            raise ValidationError("BacklogPlan must contain at least one BacklogPlanItem")

        # Invariant: APPROVED or MATERIALIZED plans must record approved_by
        if self.status in (BacklogPlanStatus.APPROVED, BacklogPlanStatus.MATERIALIZED):
            if not self.approved_by:
                raise ValidationError(f"Plan in status '{self.status.value}' must specify approved_by")

    def validate_transition(self, new_status: BacklogPlanStatus) -> None:
        """Validates that a transition from current status to new_status is legal."""
        allowed = VALID_PLAN_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValidationError(
                f"Illegal BacklogPlan transition from {self.status.value} to {new_status.value}. "
                f"Allowed targets: {[s.value for s in allowed]}"
            )

    def assert_can_materialize(self) -> None:
        """Enforces that only APPROVED plans can materialize into physical directories or cards."""
        if self.status != BacklogPlanStatus.APPROVED:
            raise ValidationError(
                f"Speculative materialization prohibited: BacklogPlan must be in status APPROVED, "
                f"but is currently in '{self.status.value}'"
            )

    @staticmethod
    def get_required_artifacts_for_kind(kind: WorkItemKind) -> List[str]:
        """Resolves mandatory level-specific artifacts.

        Strictly prevents Task from generating Epic-level artifacts (R0-WORK-003).
        """
        if kind == WorkItemKind.EPIC:
            return ["epic.md", "product-goal.md", "architecture-vision.md"]
        if kind == WorkItemKind.FEATURE:
            return ["feature-spec.md", "component-design.md"]
        if kind == WorkItemKind.STORY:
            return ["user-story.md", "acceptance-criteria.md"]
        if kind == WorkItemKind.TASK:
            return ["task-scope.md"]
        if kind == WorkItemKind.BUG:
            return ["bug-report.md", "reproduction-steps.md"]
        if kind == WorkItemKind.SPIKE:
            return ["spike-report.md", "findings.md"]
        if kind == WorkItemKind.INCIDENT:
            return ["incident-report.md", "post-mortem.md"]
        if kind == WorkItemKind.RELEASE:
            return ["release-notes.md", "deployment-plan.md"]
        if kind == WorkItemKind.PROJECT_SETUP:
            return ["project-setup.md"]
        return ["scope.md"]
