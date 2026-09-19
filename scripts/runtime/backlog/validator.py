"""Authoritative Backlog Plan Validation Engine.

Strictly stdlib-only.
Executes the deterministic 7-step validation pipeline:
1. Structural Checks (plan_id, project_id, created_by non-empty, >=1 item)
2. Item Integrity (title >= 5, description >= 10, prohibited tokens check)
3. Sizing Checks (Fibonacci SP in {1, 2, 3, 5, 8}, SP <= 8, Epics/Tasks SP=None)
4. Hierarchy Checks (conformance to WorkHierarchy.ALLOWED_PARENTS, multi-epic support)
5. DAG Acyclicity & No Unresolved Orphans
6. QBC Tripartite Verification (SQLite, Filesystem, Azure WIQL, Semantic Matcher)
7. Ambiguity Gate (fail-closed block on unreviewed AMBIGUITY_DETECTED)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import sqlite3
from typing import Any, Dict, List, Optional, Set

from scripts.domain.backlog import BacklogPlan, BacklogPlanItem
from scripts.domain.common import ValidationError
from scripts.domain.work_items import (
    FIBONACCI_SIZING_ALLOWED,
    WorkHierarchy,
    WorkItemId,
    WorkItemKind,
)
from scripts.runtime.backlog.qbc import QbcMatchResult, SemanticQbcEngine
from scripts.runtime.backlog.repository import BacklogPlanRepository, QbcDecisionRecord

logger = logging.getLogger(__name__)

PROHIBITED_TOKENS = {"tbd", "todo", "lorem ipsum", "implement later"}


@dataclass(frozen=True)
class ValidationResult:
    """Result of plan validation execution."""

    is_valid: bool
    errors: List[str] = field(default_factory=list)
    qbc_decisions: List[QbcDecisionRecord] = field(default_factory=list)
    has_ambiguities: bool = False


class BacklogPlanValidator:
    """Deterministic 7-step validator for BacklogPlan entities."""

    def __init__(
        self,
        qbc_engine: SemanticQbcEngine,
        repository: Optional[BacklogPlanRepository] = None,
    ) -> None:
        self.qbc_engine = qbc_engine
        self.repository = repository

    def validate(
        self,
        plan: BacklogPlan,
        existing_parent_ids: Optional[Set[str]] = None,
    ) -> ValidationResult:
        """Runs all 7 validation steps on the provided BacklogPlan."""
        errors: List[str] = []

        # 1. Structural Checks
        if not plan.plan_id or not plan.plan_id.strip():
            errors.append("StructuralError: plan_id must not be empty.")
        if not plan.project_id or not plan.project_id.strip():
            errors.append("StructuralError: project_id must not be empty.")
        if not plan.created_by or not plan.created_by.strip():
            errors.append("StructuralError: created_by must not be empty.")
        if not plan.items or len(plan.items) == 0:
            errors.append("StructuralError: BacklogPlan must contain at least one item.")
            return ValidationResult(is_valid=False, errors=errors)

        # Build plan-internal ID set
        plan_item_ids = {WorkItemId.normalize(i.proposed_id) for i in plan.items}
        if len(plan_item_ids) != len(plan.items):
            errors.append("DuplicateIdError: Plan contains duplicate proposed_id across items.")

        # 2. Item Integrity & 3. Sizing Checks
        for item in plan.items:
            # Check title & description length
            if not item.title or len(item.title.strip()) < 5:
                errors.append(f"ItemIntegrityError: Item '{item.proposed_id}' title must be >= 5 chars.")
            if not item.description or len(item.description.strip()) < 10:
                errors.append(f"ItemIntegrityError: Item '{item.proposed_id}' description must be >= 10 chars.")

            # Prohibited tokens
            norm_title = item.title.lower()
            norm_desc = item.description.lower()
            for token in PROHIBITED_TOKENS:
                if token in norm_title or token in norm_desc:
                    errors.append(
                        f"ItemIntegrityError: Item '{item.proposed_id}' contains prohibited token '{token}'."
                    )

            # Sizing rules
            if item.kind == WorkItemKind.STORY:
                if item.story_points is not None:
                    if item.story_points not in FIBONACCI_SIZING_ALLOWED:
                        errors.append(
                            f"SizingError: Story '{item.proposed_id}' story_points ({item.story_points}) "
                            f"must be in {sorted(list(FIBONACCI_SIZING_ALLOWED))}."
                        )
                    if item.story_points > 8:
                        errors.append(
                            f"SizingError: Story '{item.proposed_id}' SP ({item.story_points}) exceeds 8 SP limit. "
                            "Vertical slicing required."
                        )
            elif item.kind in (WorkItemKind.EPIC, WorkItemKind.TASK):
                if item.story_points is not None:
                    errors.append(
                        f"SizingError: {item.kind.value} '{item.proposed_id}' must not declare story_points (SP=None)."
                    )

        # 4. Hierarchy Checks & Multi-Epic Support
        # Discover known parent IDs in SQLite and Filesystem if not supplied
        known_parent_ids = set(existing_parent_ids or set())
        known_parent_ids.update(plan_item_ids)
        if hasattr(self.qbc_engine, "discover_sqlite_items"):
            for existing in self.qbc_engine.discover_sqlite_items(plan.project_id):
                known_parent_ids.add(WorkItemId.normalize(existing.canonical_id))
        if hasattr(self.qbc_engine, "discover_filesystem_items"):
            for existing in self.qbc_engine.discover_filesystem_items(plan.project_id):
                known_parent_ids.add(WorkItemId.normalize(existing.canonical_id))

        for item in plan.items:
            norm_parent = WorkItemId.normalize(item.parent_id) if item.parent_id else None

            # Epics must not have parents
            if item.kind == WorkItemKind.EPIC and norm_parent is not None:
                errors.append(f"HierarchyError: EPIC '{item.proposed_id}' must not have a parent_id.")

            # Features, Stories, Tasks must have parents
            if item.kind in (WorkItemKind.FEATURE, WorkItemKind.STORY, WorkItemKind.TASK):
                if not norm_parent:
                    errors.append(f"HierarchyError: {item.kind.value} '{item.proposed_id}' must declare a parent_id.")
                elif norm_parent not in known_parent_ids:
                    errors.append(
                        f"HierarchyError: {item.kind.value} '{item.proposed_id}' references unknown parent '{norm_parent}'."
                    )

        # 5. DAG Acyclicity Check
        parent_map: Dict[str, Optional[str]] = {}
        for item in plan.items:
            norm_id = WorkItemId.normalize(item.proposed_id)
            norm_parent = WorkItemId.normalize(item.parent_id) if item.parent_id else None
            parent_map[norm_id] = norm_parent

        for start_id in parent_map:
            visited = set()
            curr = start_id
            while curr in parent_map and parent_map[curr] is not None:
                visited.add(curr)
                parent = parent_map[curr]
                if parent in visited:
                    errors.append(f"CycleError: Circular hierarchy detected involving '{parent}'.")
                    break
                curr = parent

        # 6. QBC Tripartite Verification & 7. Ambiguity Gate
        qbc_decisions: List[QbcDecisionRecord] = []
        has_ambiguities = False

        # Load existing overridden decisions if repository is available
        overridden_decisions: Dict[str, QbcDecisionRecord] = {}
        if self.repository:
            for d in self.repository.get_qbc_decisions(plan.plan_id):
                if d.decision_status == "OVERRIDDEN":
                    overridden_decisions[WorkItemId.normalize(d.proposed_id)] = d

        for item in plan.items:
            norm_id = WorkItemId.normalize(item.proposed_id)
            match_res: QbcMatchResult = self.qbc_engine.evaluate_item(
                project_id=plan.project_id,
                item=item,
                in_flight_items=plan.items,
            )

            decision_status = match_res.decision_status
            notes = None
            resolved_by = None

            # Check if previously overridden
            if decision_status == "AMBIGUITY_DETECTED" and norm_id in overridden_decisions:
                prev = overridden_decisions[norm_id]
                decision_status = "OVERRIDDEN"
                notes = prev.resolution_notes
                resolved_by = prev.resolved_by

            record = QbcDecisionRecord(
                decision_id=f"qbc:{plan.plan_id}:{norm_id}",
                plan_id=plan.plan_id,
                proposed_id=item.proposed_id,
                kind=item.kind.value,
                title=item.title,
                matched_id=match_res.matched_id,
                matched_source=match_res.matched_source,
                similarity_score=match_res.similarity_score,
                decision_status=decision_status,
                resolution_notes=notes,
                resolved_by=resolved_by,
            )
            qbc_decisions.append(record)

            if self.repository:
                try:
                    self.repository.record_qbc_decision(record)
                except sqlite3.IntegrityError:
                    pass

            if decision_status == "DUPLICATE_REJECTED":
                errors.append(
                    f"QbcDuplicateError: Item '{item.proposed_id}' rejected as duplicate of "
                    f"'{match_res.matched_id}' ({match_res.matched_source}): {match_res.rationale}"
                )
            elif decision_status == "AMBIGUITY_DETECTED":
                has_ambiguities = True
                errors.append(
                    f"QbcAmbiguityError: Item '{item.proposed_id}' flagged with AMBIGUITY_DETECTED "
                    f"against '{match_res.matched_id}': {match_res.rationale}"
                )

        is_valid = (len(errors) == 0)
        return ValidationResult(
            is_valid=is_valid,
            errors=errors,
            qbc_decisions=qbc_decisions,
            has_ambiguities=has_ambiguities,
        )
