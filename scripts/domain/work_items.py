"""Domain models for Work Items, Canonical Identifiers, Hierarchy and Acceptance Criteria.

Strictly stdlib-only.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set

from .common import BaseDomainModel, ValidationError

FIBONACCI_SIZING_ALLOWED: Set[int] = {1, 2, 3, 5, 8}

RE_EPIC = re.compile(r"^EPIC-\d{3,}$")
RE_FEATURE = re.compile(r"^FEATURE-\d{3,}$")
RE_STORY = re.compile(r"^STORY-\d{3,}$")
RE_TASK = re.compile(r"^TASK-\d{4,}$")
RE_OPERATIONAL = re.compile(r"^(BUG|SPIKE|INCIDENT|RELEASE|SETUP)-\d{3,}$")


class WorkItemKind(str, Enum):
    EPIC = "EPIC"
    FEATURE = "FEATURE"
    STORY = "STORY"
    TASK = "TASK"
    # Operational specialized types
    BUG = "BUG"
    SPIKE = "SPIKE"
    INCIDENT = "INCIDENT"
    RELEASE = "RELEASE"
    PROJECT_SETUP = "PROJECT_SETUP"


# Alias for type parity
WorkItemType = WorkItemKind


class RiskTier(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SyncStateKind(str, Enum):
    SYNCED = "SYNCED"
    PENDING_CREATE = "PENDING_CREATE"
    PENDING_UPDATE = "PENDING_UPDATE"
    PENDING_DELETE = "PENDING_DELETE"
    CONFLICT = "CONFLICT"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"


class WorkItemId:
    """Canonical identifier helper and legacy alias normalizer."""

    @staticmethod
    def normalize(raw_id: str) -> str:
        """Normalizes legacy aliases (FEAT-, US-, TK-) to canonical prefixes."""
        if not raw_id or not isinstance(raw_id, str):
            raise ValidationError("WorkItem ID must be a non-empty string")

        trimmed = raw_id.strip()
        if trimmed.startswith("FEAT-"):
            return "FEATURE-" + trimmed[5:]
        if trimmed.startswith("US-"):
            return "STORY-" + trimmed[3:]
        if trimmed.startswith("TK-"):
            return "TASK-" + trimmed[3:]
        return trimmed

    @classmethod
    def validate(cls, canonical_id: str) -> bool:
        """Validates canonical ID grammar."""
        if not canonical_id or not isinstance(canonical_id, str):
            return False
        return bool(
            RE_EPIC.match(canonical_id)
            or RE_FEATURE.match(canonical_id)
            or RE_STORY.match(canonical_id)
            or RE_TASK.match(canonical_id)
            or RE_OPERATIONAL.match(canonical_id)
        )

    @classmethod
    def infer_kind(cls, canonical_id: str) -> WorkItemKind:
        """Infers WorkItemKind from canonical ID prefix."""
        norm_id = cls.normalize(canonical_id)
        if RE_EPIC.match(norm_id):
            return WorkItemKind.EPIC
        if RE_FEATURE.match(norm_id):
            return WorkItemKind.FEATURE
        if RE_STORY.match(norm_id):
            return WorkItemKind.STORY
        if RE_TASK.match(norm_id):
            return WorkItemKind.TASK
        if norm_id.startswith("BUG-"):
            return WorkItemKind.BUG
        if norm_id.startswith("SPIKE-"):
            return WorkItemKind.SPIKE
        if norm_id.startswith("INCIDENT-"):
            return WorkItemKind.INCIDENT
        if norm_id.startswith("RELEASE-"):
            return WorkItemKind.RELEASE
        if norm_id.startswith("SETUP-"):
            return WorkItemKind.PROJECT_SETUP
        raise ValidationError(f"Cannot infer WorkItemKind from non-canonical ID: '{canonical_id}'")


@dataclass(frozen=True)
class AcceptanceCriterion(BaseDomainModel):
    """Gherkin acceptance criterion structure."""

    id: str
    scenario: str
    given: str
    when: str
    then: str
    is_verified: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ValidationError("AcceptanceCriterion id must not be empty")
        if not self.scenario or not self.scenario.strip():
            raise ValidationError("AcceptanceCriterion scenario must not be empty")
        if not self.given or not self.given.strip():
            raise ValidationError("AcceptanceCriterion given must not be empty")
        if not self.when or not self.when.strip():
            raise ValidationError("AcceptanceCriterion when must not be empty")
        if not self.then or not self.then.strip():
            raise ValidationError("AcceptanceCriterion then must not be empty")


class WorkHierarchy:
    """Four-tier hierarchy and parent-child invariant rules."""

    ALLOWED_PARENTS: Dict[WorkItemKind, Set[Optional[WorkItemKind]]] = {
        WorkItemKind.EPIC: {None},
        WorkItemKind.FEATURE: {WorkItemKind.EPIC},
        WorkItemKind.STORY: {WorkItemKind.FEATURE},
        WorkItemKind.TASK: {WorkItemKind.STORY},
        WorkItemKind.BUG: {WorkItemKind.STORY, WorkItemKind.FEATURE},
        WorkItemKind.SPIKE: {WorkItemKind.FEATURE, WorkItemKind.EPIC},
        WorkItemKind.INCIDENT: {None, WorkItemKind.FEATURE, WorkItemKind.EPIC},
        WorkItemKind.RELEASE: {None},
        WorkItemKind.PROJECT_SETUP: {None},
    }

    @classmethod
    def validate_parent_child(
        cls,
        parent_kind: Optional[WorkItemKind],
        child_kind: WorkItemKind,
    ) -> None:
        allowed = cls.ALLOWED_PARENTS.get(child_kind, set())
        if parent_kind not in allowed:
            allowed_names = [k.value if k else "None" for k in allowed]
            raise ValidationError(
                f"Invalid parentage: {child_kind.value} cannot have parent {parent_kind.value if parent_kind else 'None'}. "
                f"Allowed parents: {allowed_names}"
            )


@dataclass(frozen=True)
class WorkItem(BaseDomainModel):
    """Canonical domain work item."""

    work_item_id: str
    kind: WorkItemKind
    title: str
    description: str
    project_id: str
    stage: str
    risk_tier: RiskTier
    definition_of_done: List[str]
    acceptance_criteria: List[AcceptanceCriterion]
    parent_id: Optional[str] = None
    story_points: Optional[int] = None
    delivery_backend_id: Optional[str] = None
    sync_state: SyncStateKind = SyncStateKind.PENDING_CREATE
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Canonical ID normalization check
        norm_id = WorkItemId.normalize(self.work_item_id)
        if norm_id != self.work_item_id:
            object.__setattr__(self, "work_item_id", norm_id)

        if not WorkItemId.validate(self.work_item_id):
            raise ValidationError(
                f"work_item_id '{self.work_item_id}' does not match canonical ID grammar"
            )

        if not self.title or len(self.title.strip()) < 5:
            raise ValidationError("title must be at least 5 characters")
        if not self.description or len(self.description.strip()) < 20:
            raise ValidationError("description must be at least 20 characters")
        if not self.project_id or not self.project_id.strip():
            raise ValidationError("project_id must not be empty")
        if not self.stage or not self.stage.strip():
            raise ValidationError("stage must not be empty")

        # Parent invariants
        if self.kind == WorkItemKind.EPIC and self.parent_id is not None:
            raise ValidationError("EPIC cannot have a parent (parent_id must be None)")
        if self.kind == WorkItemKind.PROJECT_SETUP and self.parent_id is not None:
            raise ValidationError("PROJECT_SETUP cannot have a parent (parent_id must be None)")

        # Sizing invariants
        if self.story_points is not None:
            if self.story_points > 8:
                raise ValidationError(
                    f"Sizing invariant violated: story_points ({self.story_points}) exceeds 8 SP. "
                    "Must be sliced vertically into smaller stories by 40-agile-coach."
                )
            if self.story_points not in FIBONACCI_SIZING_ALLOWED:
                raise ValidationError(
                    f"story_points ({self.story_points}) must belong to Fibonacci set {sorted(list(FIBONACCI_SIZING_ALLOWED))}"
                )

    def validate_exit_requirements(self, current_stage: str) -> None:
        """Validates DoD and acceptance criteria completeness when advancing past requirements."""
        if current_stage == "REQUIREMENTS_PRODUCT":
            if self.kind in (WorkItemKind.STORY, WorkItemKind.BUG, WorkItemKind.FEATURE):
                if not self.acceptance_criteria or len(self.acceptance_criteria) < 1:
                    raise ValidationError(
                        f"{self.kind.value} requires at least 1 AcceptanceCriterion before exiting REQUIREMENTS_PRODUCT"
                    )
                if not self.definition_of_done or len(self.definition_of_done) < 2:
                    raise ValidationError(
                        f"{self.kind.value} requires at least 2 Definition of Done statements before exiting REQUIREMENTS_PRODUCT"
                    )
