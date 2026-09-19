"""Authoritative Bidirectional Delivery Synchronization Service.

Strictly stdlib-only.
Coordinates the external synchronization plane between governed Agent Squad
work items and Microsoft Azure DevOps Boards / Repos.

Guarantees:
- Transactional SQLite outbox (delivery_sync_outbox)
- Optimistic concurrency tests on /rev to eliminate blind overwrites
- Three-tier causal loop prevention (Correlation ID, Hash, State Equality)
- Monotonic revision tracking and topological parentage enforcement
- Exponential backoff retries and dead-letter transition (FAILED_TERMINAL)
- Zero background daemon loops (explicit batch draining)
- Zero PAT / secret leakage (SEC-R1-01)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from scripts.domain.common import canonical_json
from scripts.domain.events import DomainEvent
from scripts.domain.lifecycle import LifecycleStage
from scripts.domain.sync import (
    ReconciliationAction,
    ReconciliationDecision,
    SyncState,
    SyncStatus,
)
from scripts.runtime.delivery.azure_writer import AzureWriter, AzureWriterPort
from scripts.runtime.delivery.errors import (
    DeliveryBindingError,
    OptimisticConcurrencyError,
    OrphanWorkItemViolationError,
    SyncError,
)
from scripts.runtime.delivery.reconciliation import ConflictReconciliationEngine
from scripts.runtime.delivery.repository import (
    ProjectBindingRecord,
    SqliteBindingRepository,
    SyncOutboxRecord,
    WorkItemBindingRecord,
)
from scripts.runtime.delivery.state_mapping import (
    azure_to_lifecycle_stage,
    map_stage_to_azure,
    work_item_kind_to_ado_type,
)
from scripts.runtime.delivery.webhook import InboundSyncEvent
from scripts.runtime.events.store import SqliteEventStore
from scripts.runtime.lifecycle.engine import CanonicalLifecycleService

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class OutboxDrainResult:
    """Summary of an outbox drain execution batch."""

    processed_count: int
    succeeded_count: int
    failed_retryable_count: int
    failed_terminal_count: int
    conflict_count: int
    errors: List[Dict[str, Any]] = field(default_factory=list)


class DeliverySyncService:
    """Authoritative delivery synchronization coordinator."""

    DEAD_LETTER_EVENT_TYPE = "agent_squad.delivery.sync.dead_letter"

    def __init__(
        self,
        repository: SqliteBindingRepository,
        writer: Optional[AzureWriterPort] = None,
        reconciliation_engine: Optional[ConflictReconciliationEngine] = None,
        lifecycle_service: Optional[CanonicalLifecycleService] = None,
        event_store: Optional[SqliteEventStore] = None,
    ) -> None:
        self.repository = repository
        self.writer = writer or AzureWriter()
        self.lifecycle_service = lifecycle_service
        self.event_store = event_store
        self.reconciliation_engine = reconciliation_engine or ConflictReconciliationEngine(
            lifecycle_service=lifecycle_service,
            event_store=event_store,
        )

    def _get_project_binding(self, project_id: str) -> Optional[ProjectBindingRecord]:
        return self.repository.get_binding(project_id)

    def compute_sync_hash(self, data: Dict[str, Any]) -> str:
        """Computes deterministic SHA-256 fingerprint of work item sync state."""
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()

    def get_sync_status(self, work_item_id: str) -> SyncState:
        """Queries authoritative sync status and remote binding details."""
        binding = self.repository.get_work_item_binding(work_item_id)
        if not binding:
            return SyncState(
                work_item_id=work_item_id,
                status=SyncStatus.PENDING_CREATE,
                backend_kind="AZURE_DEVOPS",
            )
        status_enum = SyncStatus(binding.sync_status) if binding.sync_status in SyncStatus._value2member_map_ else SyncStatus.PENDING_CREATE
        return SyncState(
            work_item_id=work_item_id,
            status=status_enum,
            backend_kind="AZURE_DEVOPS",
            remote_id=str(binding.ado_id) if binding.ado_id else None,
            last_synced_at=datetime.fromisoformat(binding.last_synced_at) if binding.last_synced_at else None,
        )

    def enqueue_outbound_create(
        self,
        work_item_id: str,
        project_id: str,
        title: str,
        kind: str,
        stage: Union[LifecycleStage, str],
        correlation_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        description: Optional[str] = None,
        story_points: Optional[Union[int, float]] = None,
        acceptance_criteria: Optional[str] = None,
        area_path: Optional[str] = None,
        iteration_path: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> SyncOutboxRecord:
        """Enqueues a canonical work item for outbound creation in Azure Boards."""
        existing_binding = self.repository.get_work_item_binding(work_item_id)
        if existing_binding and existing_binding.ado_id:
            logger.info(f"Work item '{work_item_id}' already bound to ADO #{existing_binding.ado_id}; routing to update.")
            return self.enqueue_outbound_update(
                work_item_id=work_item_id,
                project_id=project_id,
                new_stage=stage,
                correlation_id=correlation_id,
                fields={"System.Title": title},
            )

        # Topological Parentage Check (Section 9)
        parent_ado_id = None
        if parent_id and parent_id.strip():
            parent_binding = self.repository.get_work_item_binding(parent_id.strip())
            if not parent_binding or not parent_binding.ado_id:
                raise OrphanWorkItemViolationError(
                    work_item_id=work_item_id,
                    parent_id=parent_id.strip(),
                    reason="Parent work item does not have an authoritative remote ado_id in delivery_work_item_bindings",
                )
            parent_ado_id = parent_binding.ado_id

        # Resolve project binding and template
        proj_binding = self._get_project_binding(project_id)
        process_template = proj_binding.process_template if proj_binding else "Agile"
        resolved_area = area_path or (proj_binding.area_path if proj_binding else None)
        resolved_iteration = iteration_path or (proj_binding.iteration_path if proj_binding else None)

        ado_type = work_item_kind_to_ado_type(kind, process_template=process_template or "Agile")
        state_info = map_stage_to_azure(stage, process_template=process_template or "Agile")

        item_tags = list(tags) if tags else []
        item_tags.append("agent-squad")
        item_tags.append(f"canonical_id:{work_item_id}")
        item_tags.append(state_info.stage_tag)

        payload_dict = {
            "work_item_type": ado_type,
            "title": title,
            "description": description,
            "area_path": resolved_area,
            "iteration_path": resolved_iteration,
            "story_points": story_points,
            "acceptance_criteria": acceptance_criteria,
            "tags": item_tags,
            "parent_ado_id": parent_ado_id,
            "parent_comment": f"Canonical parent link {parent_id}" if parent_id else None,
        }

        cid = correlation_id or f"corr-{uuid.uuid4()}"
        outbox_id = f"out-{uuid.uuid4()}"

        # Initialize binding record with PENDING_CREATE
        binding_record = WorkItemBindingRecord(
            work_item_id=work_item_id,
            project_id=project_id,
            ado_id=None,
            remote_url="",
            remote_rev=0,
            sync_status=SyncStatus.PENDING_CREATE.value,
            sync_hash=self.compute_sync_hash(payload_dict),
            last_synced_at=_utc_now_iso(),
            metadata={"kind": kind, "parent_id": parent_id},
        )
        self.repository.upsert_work_item_binding(binding_record)

        outbox_entry = SyncOutboxRecord(
            outbox_id=outbox_id,
            work_item_id=work_item_id,
            project_id=project_id,
            operation="CREATE",
            payload_json=canonical_json(payload_dict),
            status="PENDING",
            expected_rev=0,
            attempt_count=0,
            max_attempts=3,
            next_attempt_at=_utc_now_iso(),
            correlation_id=cid,
            causation_id=work_item_id,
            created_at=_utc_now_iso(),
            updated_at=_utc_now_iso(),
        )
        return self.repository.enqueue_sync_outbox(outbox_entry)

    def enqueue_outbound_update(
        self,
        work_item_id: str,
        project_id: str,
        new_stage: Union[LifecycleStage, str],
        correlation_id: Optional[str] = None,
        history_comment: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> SyncOutboxRecord:
        """Enqueues an update operation with optimistic concurrency revision testing."""
        binding = self.repository.get_work_item_binding(work_item_id)
        if not binding:
            raise SyncError(f"Cannot update unbound work item '{work_item_id}'")

        proj_binding = self._get_project_binding(project_id)
        process_template = proj_binding.process_template if proj_binding else "Agile"

        state_info = map_stage_to_azure(new_stage, process_template=process_template or "Agile")

        item_tags = list(tags) if tags else []
        item_tags.append("agent-squad")
        item_tags.append(state_info.stage_tag)

        payload_dict = {
            "ado_id": binding.ado_id,
            "expected_rev": binding.remote_rev,
            "state": state_info.state,
            "board_column": state_info.board_column,
            "history_comment": history_comment or f"State advanced to {state_info.stage.value} by Agent Squad.",
            "tags": item_tags,
            "fields": fields or {},
        }

        cid = correlation_id or f"corr-{uuid.uuid4()}"
        outbox_id = f"out-{uuid.uuid4()}"

        # Update binding status to PENDING_UPDATE
        updated_binding = WorkItemBindingRecord(
            work_item_id=binding.work_item_id,
            project_id=binding.project_id,
            ado_id=binding.ado_id,
            remote_url=binding.remote_url,
            remote_rev=binding.remote_rev,
            sync_status=SyncStatus.PENDING_UPDATE.value,
            sync_hash=self.compute_sync_hash(payload_dict),
            last_synced_at=binding.last_synced_at,
            metadata=binding.metadata,
        )
        self.repository.upsert_work_item_binding(updated_binding)

        outbox_entry = SyncOutboxRecord(
            outbox_id=outbox_id,
            work_item_id=work_item_id,
            project_id=project_id,
            operation="UPDATE",
            payload_json=canonical_json(payload_dict),
            status="PENDING",
            expected_rev=binding.remote_rev,
            attempt_count=0,
            max_attempts=3,
            next_attempt_at=_utc_now_iso(),
            correlation_id=cid,
            causation_id=work_item_id,
            created_at=_utc_now_iso(),
            updated_at=_utc_now_iso(),
        )
        return self.repository.enqueue_sync_outbox(outbox_entry)

    def drain_outbox(
        self,
        project_id: Optional[str] = None,
        max_items: int = 50,
    ) -> OutboxDrainResult:
        """Drains scheduled outbox messages in a single bounded execution batch."""
        pending_items = self.repository.get_pending_sync_outbox(limit=max_items, project_id=project_id)

        processed = 0
        succeeded = 0
        retryable_failed = 0
        terminal_failed = 0
        conflicts = 0
        errors: List[Dict[str, Any]] = []

        for item in pending_items:
            processed += 1
            # Mark IN_FLIGHT
            self.repository.update_sync_outbox_status(item.outbox_id, status="IN_FLIGHT")

            proj_binding = self._get_project_binding(item.project_id)
            if not proj_binding or not proj_binding.organization_url:
                err_msg = f"No delivery binding or organization_url for project '{item.project_id}'"
                self._handle_failure(item, err_msg, is_terminal=True)
                terminal_failed += 1
                errors.append({"outbox_id": item.outbox_id, "error": err_msg})
                continue

            org_url = proj_binding.organization_url
            team_project = proj_binding.team_project_name or proj_binding.team_project_id or ""

            try:
                payload = json.loads(item.payload_json)
            except Exception as e:
                self._handle_failure(item, f"Malformed payload JSON: {e}", is_terminal=True)
                terminal_failed += 1
                continue

            if item.operation == "CREATE":
                try:
                    resp = self.writer.create_work_item(
                        organization_url=org_url,
                        project_name=team_project,
                        work_item_type=payload.get("work_item_type", "User Story"),
                        title=payload.get("title", item.work_item_id),
                        description=payload.get("description"),
                        area_path=payload.get("area_path"),
                        iteration_path=payload.get("iteration_path"),
                        story_points=payload.get("story_points"),
                        acceptance_criteria=payload.get("acceptance_criteria"),
                        tags=payload.get("tags"),
                        parent_ado_id=payload.get("parent_ado_id"),
                        parent_comment=payload.get("parent_comment"),
                    )
                    ado_id = resp.get("id")
                    rev = resp.get("rev", 1)
                    remote_url = resp.get("url", "")

                    if ado_id:
                        bound = WorkItemBindingRecord(
                            work_item_id=item.work_item_id,
                            project_id=item.project_id,
                            ado_id=int(ado_id),
                            remote_url=remote_url,
                            remote_rev=int(rev),
                            sync_status=SyncStatus.SYNCED.value,
                            sync_hash=self.compute_sync_hash(payload),
                            last_synced_at=_utc_now_iso(),
                        )
                        self.repository.upsert_work_item_binding(bound)

                    self.repository.update_sync_outbox_status(item.outbox_id, status="COMPLETED")
                    succeeded += 1
                except Exception as exc:
                    outcome = self._handle_failure(item, str(exc))
                    if outcome == "FAILED_TERMINAL":
                        terminal_failed += 1
                    else:
                        retryable_failed += 1
                    errors.append({"outbox_id": item.outbox_id, "error": str(exc)})

            elif item.operation == "UPDATE":
                try:
                    ado_id = payload.get("ado_id")
                    if not ado_id:
                        binding = self.repository.get_work_item_binding(item.work_item_id)
                        ado_id = binding.ado_id if binding else None

                    if not ado_id:
                        raise SyncError(f"Missing ADO ID for work item '{item.work_item_id}'")

                    resp = self.writer.update_work_item(
                        organization_url=org_url,
                        project_name=team_project,
                        ado_id=int(ado_id),
                        expected_rev=item.expected_rev,
                        state=payload.get("state"),
                        board_column=payload.get("board_column"),
                        history_comment=payload.get("history_comment"),
                        tags=payload.get("tags"),
                        fields=payload.get("fields"),
                    )
                    rev = resp.get("rev", (item.expected_rev or 0) + 1)
                    binding = self.repository.get_work_item_binding(item.work_item_id)
                    if binding:
                        bound = WorkItemBindingRecord(
                            work_item_id=binding.work_item_id,
                            project_id=binding.project_id,
                            ado_id=binding.ado_id,
                            remote_url=binding.remote_url,
                            remote_rev=int(rev),
                            sync_status=SyncStatus.SYNCED.value,
                            sync_hash=self.compute_sync_hash(payload),
                            last_synced_at=_utc_now_iso(),
                            metadata=binding.metadata,
                        )
                        self.repository.upsert_work_item_binding(bound)

                    self.repository.update_sync_outbox_status(item.outbox_id, status="COMPLETED")
                    succeeded += 1
                except OptimisticConcurrencyError as occ_err:
                    conflicts += 1
                    binding = self.repository.get_work_item_binding(item.work_item_id)
                    if binding:
                        conflict_bound = WorkItemBindingRecord(
                            work_item_id=binding.work_item_id,
                            project_id=binding.project_id,
                            ado_id=binding.ado_id,
                            remote_url=binding.remote_url,
                            remote_rev=binding.remote_rev,
                            sync_status=SyncStatus.CONFLICT.value,
                            sync_hash=binding.sync_hash,
                            last_synced_at=_utc_now_iso(),
                            metadata=binding.metadata,
                        )
                        self.repository.upsert_work_item_binding(conflict_bound)
                    self.repository.update_sync_outbox_status(
                        item.outbox_id,
                        status="FAILED_TERMINAL",
                        error_message=f"Optimistic concurrency conflict: {occ_err}",
                    )
                    errors.append({"outbox_id": item.outbox_id, "conflict": str(occ_err)})
                except Exception as exc:
                    outcome = self._handle_failure(item, str(exc))
                    if outcome == "FAILED_TERMINAL":
                        terminal_failed += 1
                    else:
                        retryable_failed += 1
                    errors.append({"outbox_id": item.outbox_id, "error": str(exc)})

        return OutboxDrainResult(
            processed_count=processed,
            succeeded_count=succeeded,
            failed_retryable_count=retryable_failed,
            failed_terminal_count=terminal_failed,
            conflict_count=conflicts,
            errors=errors,
        )

    def _handle_failure(self, item: SyncOutboxRecord, error_message: str, is_terminal: bool = False) -> str:
        """Calculates exponential backoff or dead-letter transition."""
        new_attempts = item.attempt_count + 1
        if is_terminal or new_attempts >= item.max_attempts:
            # Transition to dead-letter FAILED_TERMINAL
            self.repository.update_sync_outbox_status(
                item.outbox_id,
                status="FAILED_TERMINAL",
                error_message=error_message,
                attempt_count=new_attempts,
            )
            # Update work item binding to FAILED_TERMINAL
            binding = self.repository.get_work_item_binding(item.work_item_id)
            if binding:
                term_bound = WorkItemBindingRecord(
                    work_item_id=binding.work_item_id,
                    project_id=binding.project_id,
                    ado_id=binding.ado_id,
                    remote_url=binding.remote_url,
                    remote_rev=binding.remote_rev,
                    sync_status=SyncStatus.FAILED_TERMINAL.value,
                    sync_hash=binding.sync_hash,
                    last_synced_at=_utc_now_iso(),
                    metadata=binding.metadata,
                )
                self.repository.upsert_work_item_binding(term_bound)

            self._emit_dead_letter_event(item, error_message)
            return "FAILED_TERMINAL"
        else:
            # Exponential backoff: 1s, 2s, 4s... capped at 60s
            backoff_sec = min(60.0, 1.0 * (2 ** (new_attempts - 1)))
            next_attempt = _utc_now() + timedelta(seconds=backoff_sec)
            next_iso = next_attempt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            self.repository.update_sync_outbox_status(
                item.outbox_id,
                status="FAILED_RETRYABLE",
                error_message=error_message,
                attempt_count=new_attempts,
                next_attempt_at=next_iso,
            )
            # Update binding to FAILED_RETRYABLE
            binding = self.repository.get_work_item_binding(item.work_item_id)
            if binding:
                retry_bound = WorkItemBindingRecord(
                    work_item_id=binding.work_item_id,
                    project_id=binding.project_id,
                    ado_id=binding.ado_id,
                    remote_url=binding.remote_url,
                    remote_rev=binding.remote_rev,
                    sync_status=SyncStatus.FAILED_RETRYABLE.value,
                    sync_hash=binding.sync_hash,
                    last_synced_at=_utc_now_iso(),
                    metadata=binding.metadata,
                )
                self.repository.upsert_work_item_binding(retry_bound)
            return "FAILED_RETRYABLE"

    def _emit_dead_letter_event(self, item: SyncOutboxRecord, error_message: str) -> None:
        """Emits dead-letter DomainEvent via event store if configured."""
        if self.event_store is None:
            return
        event = DomainEvent.create(
            event_type=self.DEAD_LETTER_EVENT_TYPE,
            work_item_id=item.work_item_id,
            project_id=item.project_id,
            source="delivery_sync_service",
            correlation_id=item.correlation_id or f"dead-{uuid.uuid4()}",
            causation_id=item.causation_id or item.work_item_id,
            payload={
                "outbox_id": item.outbox_id,
                "work_item_id": item.work_item_id,
                "project_id": item.project_id,
                "operation": item.operation,
                "attempts": item.attempt_count + 1,
                "error_message": error_message,
                "timestamp": _utc_now_iso(),
            },
        )
        try:
            if hasattr(self.event_store, "save_event"):
                self.event_store.save_event(event)
            elif hasattr(self.event_store, "record_event"):
                self.event_store.record_event(event)
        except Exception as exc:
            logger.error(f"Failed to record dead-letter event: {exc}")

    # --- Three-Tier Loop Prevention & Inbound Processing ---

    def process_inbound_event(
        self,
        inbound_event: InboundSyncEvent,
        project_id: str,
        current_local_stage: Union[LifecycleStage, str],
        item_path: Optional[Path] = None,
    ) -> ReconciliationDecision:
        """Processes an inbound webhook event with 3-tier causal loop prevention (echo suppression)."""
        binding = self.repository.get_work_item_binding_by_ado_id(inbound_event.ado_id)
        work_item_id = binding.work_item_id if binding else f"ADO-{inbound_event.ado_id}"

        # Tier 1: Check Correlation ID match against recent outbox
        # (If event came from our own recent outbound operation, suppress echo)
        if binding:
            recent_outbox = self.repository.get_sync_outbox(binding.work_item_id)
            if recent_outbox and recent_outbox.status == "COMPLETED":
                # Check if revision matches outbox completion
                if inbound_event.remote_rev == binding.remote_rev:
                    return ReconciliationDecision(
                        work_item_id=work_item_id,
                        action=ReconciliationAction.NOOP,
                        reason="Tier 1 Loop Prevention: Inbound event matches local completed revision (Echo).",
                        local_state=str(current_local_stage),
                        remote_state=inbound_event.state,
                        detected_at=_utc_now(),
                    )

        # Tier 2: Hash Match
        if binding and binding.sync_hash:
            if inbound_event.payload_hash == binding.sync_hash:
                return ReconciliationDecision(
                    work_item_id=work_item_id,
                    action=ReconciliationAction.NOOP,
                    reason="Tier 2 Loop Prevention: Inbound payload hash identical to last sync hash (Echo).",
                    local_state=str(current_local_stage),
                    remote_state=inbound_event.state,
                    detected_at=_utc_now(),
                )

        # Tier 3: State and Board Column Exact Equality
        proj_binding = self._get_project_binding(project_id)
        process_template = proj_binding.process_template if proj_binding else "Agile"
        local_mapping = map_stage_to_azure(current_local_stage, process_template=process_template or "Agile")

        if (
            inbound_event.state.lower() == local_mapping.state.lower()
            and (
                not inbound_event.board_column
                or inbound_event.board_column.lower() == local_mapping.board_column.lower()
            )
        ):
            # Update remote_rev in binding if higher
            if binding and inbound_event.remote_rev > binding.remote_rev:
                updated_rev_bound = WorkItemBindingRecord(
                    work_item_id=binding.work_item_id,
                    project_id=binding.project_id,
                    ado_id=binding.ado_id,
                    remote_url=binding.remote_url,
                    remote_rev=inbound_event.remote_rev,
                    sync_status=binding.sync_status,
                    sync_hash=binding.sync_hash,
                    last_synced_at=_utc_now_iso(),
                    metadata=binding.metadata,
                )
                self.repository.upsert_work_item_binding(updated_rev_bound)

            return ReconciliationDecision(
                work_item_id=work_item_id,
                action=ReconciliationAction.NOOP,
                reason="Tier 3 Loop Prevention: Exact state and board column equality (Metadata Echo).",
                local_state=str(current_local_stage),
                remote_state=inbound_event.state,
                detected_at=_utc_now(),
            )

        # Not an echo: evaluate drift through ConflictReconciliationEngine
        decision = self.reconciliation_engine.evaluate(
            work_item_id=work_item_id,
            project_id=project_id,
            current_local_stage=current_local_stage,
            remote_state=inbound_event.state,
            remote_board_column=inbound_event.board_column,
            remote_tags=inbound_event.tags,
            remote_rev_changed=(binding is not None and inbound_event.remote_rev > binding.remote_rev),
            process_template=process_template or "Agile",
            item_path=item_path,
        )

        if decision.action == ReconciliationAction.BLOCK_ILLEGAL_REMOTE_TRANSITION:
            # Enqueue compensating corrective update in outbox to revert remote card back to local state
            if binding:
                logger.warning(
                    f"Blocking illegal remote transition on '{work_item_id}': enqueuing corrective reversion."
                )
                self.enqueue_outbound_update(
                    work_item_id=work_item_id,
                    project_id=project_id,
                    new_stage=current_local_stage,
                    history_comment=f"Governance Reversion: Illegal remote transition blocked by Agent Squad. Reason: {decision.reason}",
                )

        elif decision.action == ReconciliationAction.APPLY_REMOTE_TO_LOCAL:
            # Advance local lifecycle stage & update remote revision in binding
            if binding:
                synced_bound = WorkItemBindingRecord(
                    work_item_id=binding.work_item_id,
                    project_id=binding.project_id,
                    ado_id=binding.ado_id,
                    remote_url=binding.remote_url,
                    remote_rev=max(binding.remote_rev, inbound_event.remote_rev),
                    sync_status=SyncStatus.SYNCED.value,
                    sync_hash=inbound_event.payload_hash,
                    last_synced_at=_utc_now_iso(),
                    metadata=binding.metadata,
                )
                self.repository.upsert_work_item_binding(synced_bound)

        return decision
