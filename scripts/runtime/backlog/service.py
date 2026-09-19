"""Authoritative Backlog Plan Lifecycle Service.

Strictly stdlib-only.
Coordinates the end-to-end plan lifecycle:
DRAFT -> VALIDATED -> APPROVED -> MATERIALIZED (or REJECTED)
Enforces Segregation of Duties (SoD) between Author and Approver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Union
import uuid

from scripts.domain.backlog import BacklogPlan, BacklogPlanItem, BacklogPlanStatus
from scripts.domain.common import ValidationError
from scripts.domain.events import DomainEvent
from scripts.domain.work_items import WorkItemId, WorkItemKind
from scripts.runtime.backlog.id_allocator import CanonicalIdAllocator
from scripts.runtime.backlog.materializer import BacklogMaterializer, MaterializationReceipt
from scripts.runtime.backlog.qbc import SemanticQbcEngine
from scripts.runtime.backlog.repository import BacklogPlanRepository, QbcDecisionRecord
from scripts.runtime.backlog.validator import BacklogPlanValidator, ValidationResult
from scripts.runtime.delivery.repository import SqliteBindingRepository
from scripts.runtime.events.store import SqliteEventStore

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class BacklogPlanService:
    """Orchestrates BacklogPlan lifecycle transitions, validations and materializations."""

    def __init__(
        self,
        runtime_root: Optional[Union[str, Path]] = None,
        db_path: Optional[Union[str, Path, sqlite3.Connection]] = None,
        plan_repository: Optional[BacklogPlanRepository] = None,
        id_allocator: Optional[CanonicalIdAllocator] = None,
        qbc_engine: Optional[SemanticQbcEngine] = None,
        validator: Optional[BacklogPlanValidator] = None,
        materializer: Optional[BacklogMaterializer] = None,
        event_store: Optional[SqliteEventStore] = None,
        binding_repository: Optional[SqliteBindingRepository] = None,
    ) -> None:
        self.runtime_root = Path(runtime_root or os.environ.get("SQUAD_RUNTIME", Path.cwd()))
        self.repository = plan_repository or BacklogPlanRepository(db_path=db_path)
        self.id_allocator = id_allocator or CanonicalIdAllocator(db_path=db_path, runtime_root=self.runtime_root)
        self.qbc_engine = qbc_engine or SemanticQbcEngine(db_path=db_path, runtime_root=self.runtime_root)
        self.validator = validator or BacklogPlanValidator(qbc_engine=self.qbc_engine, repository=self.repository)
        self.binding_repo = binding_repository or SqliteBindingRepository(db_path=db_path)
        self.event_store = event_store or SqliteEventStore(db_path=db_path)
        self.materializer = materializer or BacklogMaterializer(
            runtime_root=self.runtime_root,
            db_path=db_path,
            plan_repository=self.repository,
            binding_repository=self.binding_repo,
            event_store=self.event_store,
        )

    def create_draft_plan(
        self,
        project_id: str,
        created_by: str,
        items: List[BacklogPlanItem],
        plan_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> BacklogPlan:
        """Creates and persists a new BacklogPlan in DRAFT status."""
        pid = plan_id or f"plan-{uuid.uuid4()}"
        plan = BacklogPlan(
            plan_id=pid,
            project_id=project_id,
            status=BacklogPlanStatus.DRAFT,
            items=items,
            created_by=created_by,
            metadata=metadata or {},
        )
        self.repository.save_plan(plan)

        event = DomainEvent.create(
            event_type="agent_squad.backlog.drafted",
            work_item_id=plan.plan_id,
            project_id=project_id,
            source="BacklogPlanService",
            causation_id=plan.plan_id,
            correlation_id=plan.plan_id,
            payload={
                "plan_id": plan.plan_id,
                "project_id": project_id,
                "created_by": created_by,
                "items_count": len(items),
            },
        )
        self.event_store.save_event(event)
        return plan

    def validate_plan(self, plan_id: str) -> Tuple[BacklogPlan, ValidationResult]:
        """Runs the 7-step validation on a draft plan and transitions to VALIDATED if clean."""
        plan = self.repository.get_plan(plan_id)
        if not plan:
            raise ValidationError(f"Plan '{plan_id}' not found.")

        if plan.status not in (BacklogPlanStatus.DRAFT, BacklogPlanStatus.VALIDATED):
            raise ValidationError(
                f"Cannot validate plan in status '{plan.status.value}'. Must be DRAFT or VALIDATED."
            )

        val_result = self.validator.validate(plan)

        if val_result.is_valid:
            self.repository.update_plan_status(plan_id, BacklogPlanStatus.VALIDATED)
            updated_plan = self.repository.get_plan(plan_id)

            event = DomainEvent.create(
                event_type="agent_squad.backlog.validated",
                work_item_id=plan_id,
                project_id=plan.project_id,
                source="BacklogPlanService",
                causation_id=plan_id,
                correlation_id=plan_id,
                payload={"plan_id": plan_id, "project_id": plan.project_id},
            )
            self.event_store.save_event(event)
            return updated_plan or plan, val_result

        if val_result.has_ambiguities:
            event = DomainEvent.create(
                event_type="agent_squad.backlog.ambiguity_detected",
                work_item_id=plan_id,
                project_id=plan.project_id,
                source="BacklogPlanService",
                causation_id=plan_id,
                correlation_id=plan_id,
                payload={
                    "plan_id": plan_id,
                    "project_id": plan.project_id,
                    "errors": val_result.errors,
                },
            )
            self.event_store.save_event(event)

        return plan, val_result

    def override_ambiguity(
        self,
        plan_id: str,
        decision_id: str,
        resolved_by: str,
        rationale: str,
    ) -> None:
        """Resolves an AMBIGUITY_DETECTED finding with explicit justification."""
        if not rationale or len(rationale.strip()) < 10:
            raise ValidationError("Override rationale must be at least 10 characters.")
        if not resolved_by or not resolved_by.strip():
            raise ValidationError("resolved_by must not be empty.")

        self.repository.resolve_qbc_decision(
            decision_id=decision_id,
            resolved_by=resolved_by,
            notes=rationale,
            new_status="OVERRIDDEN",
        )

    def approve_plan(
        self,
        plan_id: str,
        approved_by: str,
        enforce_sod: bool = True,
    ) -> BacklogPlan:
        """Approves a VALIDATED plan under Segregation of Duties (SoD) enforcement."""
        plan = self.repository.get_plan(plan_id)
        if not plan:
            raise ValidationError(f"Plan '{plan_id}' not found.")

        if plan.status != BacklogPlanStatus.VALIDATED:
            raise ValidationError(
                f"Cannot approve plan in status '{plan.status.value}'. Must be VALIDATED."
            )

        if not approved_by or not approved_by.strip():
            raise ValidationError("approved_by must be a non-empty identifier.")

        # Segregation of Duties: Author cannot approve own plan if items > 1 or risk >= MEDIUM
        if enforce_sod and plan.created_by == approved_by:
            plan_risk = plan.metadata.get("risk", "LOW").upper()
            has_medium_or_higher_item = any(
                item.metadata.get("risk", "LOW").upper() in ("MEDIUM", "HIGH", "CRITICAL")
                for item in plan.items
            )
            if len(plan.items) > 1 or plan_risk in ("MEDIUM", "HIGH", "CRITICAL") or has_medium_or_higher_item:
                raise ValidationError(
                    f"Segregation of Duties (SoD) violation: Author '{plan.created_by}' "
                    "cannot approve their own plan when risk >= MEDIUM or multi-item."
                )

        self.repository.update_plan_status(
            plan_id=plan_id,
            new_status=BacklogPlanStatus.APPROVED,
            approved_by=approved_by,
        )

        approved_plan = self.repository.get_plan(plan_id)
        if not approved_plan:
            raise ValidationError("Failed to reload plan after approval.")

        event = DomainEvent.create(
            event_type="agent_squad.backlog.approved",
            work_item_id=plan_id,
            project_id=plan.project_id,
            source="BacklogPlanService",
            causation_id=plan_id,
            correlation_id=plan_id,
            payload={
                "plan_id": plan_id,
                "project_id": plan.project_id,
                "approved_by": approved_by,
            },
        )
        self.event_store.save_event(event)
        return approved_plan

    def reject_plan(
        self,
        plan_id: str,
        rejected_by: str,
        reason: str,
    ) -> BacklogPlan:
        """Rejects a plan in DRAFT, VALIDATED, or APPROVED status."""
        plan = self.repository.get_plan(plan_id)
        if not plan:
            raise ValidationError(f"Plan '{plan_id}' not found.")

        if plan.status == BacklogPlanStatus.MATERIALIZED:
            raise ValidationError("Cannot reject an already MATERIALIZED plan.")

        self.repository.update_plan_status(
            plan_id=plan_id,
            new_status=BacklogPlanStatus.REJECTED,
        )

        event = DomainEvent.create(
            event_type="agent_squad.backlog.rejected",
            work_item_id=plan_id,
            project_id=plan.project_id,
            source="BacklogPlanService",
            causation_id=plan_id,
            correlation_id=plan_id,
            payload={
                "plan_id": plan_id,
                "project_id": plan.project_id,
                "rejected_by": rejected_by,
                "reason": reason,
            },
        )
        self.event_store.save_event(event)

        updated = self.repository.get_plan(plan_id)
        return updated or plan

    def materialize_plan(self, plan_id: str) -> MaterializationReceipt:
        """Executes the materialization saga for an APPROVED plan."""
        plan = self.repository.get_plan(plan_id)
        if not plan:
            raise ValidationError(f"Plan '{plan_id}' not found.")

        return self.materializer.materialize(plan)
