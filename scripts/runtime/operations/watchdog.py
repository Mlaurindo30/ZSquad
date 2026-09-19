"""Operational Watchdog Service (Milestone R13).

Strictly stdlib-only.
Actively scans for stale dispatches, timebox violations, unacknowledged handoffs, and outbox backlogs.
Emits canonical domain events and schedules reconciliation jobs WITHOUT bypassing domain authorities.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid
import yaml

from scripts.domain.events import DomainEvent
from scripts.runtime.events.store import SqliteEventStore
from scripts.runtime.lifecycle.policies import (
    get_stage_policy,
    load_workflow_config,
    stage_to_legacy_name,
)
from scripts.runtime.operations.clock import ClockPort, SystemClock
from scripts.runtime.operations.models import (
    JobKind,
    WatchdogFinding,
)
from scripts.runtime.operations.scheduler import SchedulerService

logger = logging.getLogger(__name__)


class WatchdogService:
    """Active condition scanner and event emitter."""

    EVENT_TIMEBOX_EXCEEDED = "agent_squad.lifecycle.timebox_exceeded"
    EVENT_HANDOFF_OVERDUE = "agent_squad.lifecycle.handoff_ack_overdue"
    EVENT_DISPATCH_TIMEOUT = "agent_squad.dispatch.execution_timeout"
    EVENT_OUTBOX_BACKLOG = "agent_squad.operations.outbox_backlog"
    EVENT_SESSION_EXPIRED = "agent_squad.session.expired"

    def __init__(
        self,
        scheduler: SchedulerService,
        event_store: Optional[SqliteEventStore] = None,
        clock: Optional[ClockPort] = None,
        dispatch_timeout_seconds: float = 1800.0,
        handoff_ack_timeout_seconds: float = 3600.0,
        work_dir: Optional[Path] = None,
        workflow_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.scheduler = scheduler
        self.event_store = event_store
        self.clock = clock or SystemClock()
        self.dispatch_timeout_seconds = dispatch_timeout_seconds
        self.handoff_ack_timeout_seconds = handoff_ack_timeout_seconds
        self.work_dir = Path(work_dir) if work_dir else Path("work")
        self.workflow_config = workflow_config or load_workflow_config()

    def _emit_event(self, event_type: str, work_item_id: str, payload: Dict[str, Any], correlation_id: Optional[str] = None) -> None:
        if not self.event_store:
            return
        event = DomainEvent.create(
            event_type=event_type,
            work_item_id=work_item_id,
            project_id=payload.get("project_id", "default"),
            source="runtime.watchdog",
            correlation_id=correlation_id or work_item_id,
            causation_id=f"wd-{uuid.uuid4()}",
            payload=payload,
        )
        try:
            if hasattr(self.event_store, "record"):
                self.event_store.record(event)
            elif hasattr(self.event_store, "save_event"):
                self.event_store.save_event(event)
        except Exception as exc:
            logger.error(f"Failed to record watchdog domain event: {exc}")

    def scan_stale_dispatches(self, now: Optional[datetime] = None) -> List[WatchdogFinding]:
        """Scans for dispatch attempts stuck in IN_FLIGHT state beyond dispatch_timeout_seconds."""
        current_time = now or self.clock.now()
        findings: List[WatchdogFinding] = []

        # Inspect dispatch_attempts table from squad.db
        with self.scheduler.repository.connection() as conn:
            # Check if dispatch_attempts table exists
            table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='dispatch_attempts'"
            ).fetchone()
            if not table_check:
                return findings

            cursor = conn.execute(
                "SELECT dispatch_id, delegation_id, host_kind, host_execution_id, status, created_at, updated_at "
                "FROM dispatch_attempts WHERE status = 'IN_FLIGHT'"
            )
            for row in cursor.fetchall():
                created_dt = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)

                elapsed = (current_time - created_dt).total_seconds()
                if elapsed >= self.dispatch_timeout_seconds:
                    dispatch_id = row["dispatch_id"]
                    finding = WatchdogFinding(
                        finding_id=f"wf-dispatch-{dispatch_id}",
                        component="dispatch",
                        finding_type="DISPATCH_TIMEOUT",
                        entity_id=dispatch_id,
                        severity="WARNING",
                        message=f"Dispatch '{dispatch_id}' has been in flight for {elapsed:.0f}s (timeout: {self.dispatch_timeout_seconds}s)",
                        detected_at=current_time,
                        suggested_action="DISPATCH_RECONCILE",
                        metadata={"elapsed_seconds": elapsed, "host_kind": row["host_kind"]},
                    )
                    findings.append(finding)

                    # Emit domain event
                    self._emit_event(
                        event_type=self.EVENT_DISPATCH_TIMEOUT,
                        work_item_id=row["delegation_id"],
                        payload={
                            "dispatch_id": dispatch_id,
                            "delegation_id": row["delegation_id"],
                            "host_kind": row["host_kind"],
                            "elapsed_seconds": elapsed,
                        },
                    )

                    # Schedule reconciliation job via SchedulerService
                    self.scheduler.schedule_job(
                        kind=JobKind.DISPATCH_RECONCILE,
                        entity_type="dispatch",
                        entity_id=dispatch_id,
                        payload={"dispatch_id": dispatch_id, "host_kind": row["host_kind"]},
                    )

        return findings

    def scan_lifecycle_timeboxes(self, now: Optional[datetime] = None) -> List[WatchdogFinding]:
        """Scans active work items to detect if stage duration exceeds configured phase_timeboxes."""
        current_time = now or self.clock.now()
        findings: List[WatchdogFinding] = []

        if not self.work_dir.is_dir():
            return findings

        timeboxes = self.workflow_config.get("flow", {}).get("phase_timeboxes", {})

        for item_path in self.work_dir.iterdir():
            if not item_path.is_dir():
                continue
            status_file = item_path / "status.yaml"
            if not status_file.is_file():
                continue

            try:
                data = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
                work_item_id = data.get("id", item_path.name)
                state = data.get("state", "intake")
                phase_started_str = data.get("phase_started_at")
                if not phase_started_str:
                    continue

                started_dt = datetime.fromisoformat(phase_started_str.replace("Z", "+00:00"))
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)

                elapsed = (current_time - started_dt).total_seconds()
                limit_val = timeboxes.get(state)
                if limit_val is not None:
                    max_seconds = float(limit_val) * 60.0 if isinstance(limit_val, (int, float)) else 86400.0
                    if elapsed > max_seconds:
                        finding = WatchdogFinding(
                            finding_id=f"wf-timebox-{work_item_id}-{state}",
                            component="lifecycle",
                            finding_type="TIMEBOX_EXCEEDED",
                            entity_id=work_item_id,
                            severity="WARNING",
                            message=f"Work item '{work_item_id}' in state '{state}' has elapsed {elapsed:.0f}s (limit: {max_seconds:.0f}s)",
                            detected_at=current_time,
                            suggested_action="LIFECYCLE_STALE_CHECK",
                            metadata={"state": state, "elapsed_seconds": elapsed, "limit_seconds": max_seconds},
                        )
                        findings.append(finding)

                        # Emit domain event
                        self._emit_event(
                            event_type=self.EVENT_TIMEBOX_EXCEEDED,
                            work_item_id=work_item_id,
                            payload={
                                "work_item_id": work_item_id,
                                "state": state,
                                "elapsed_seconds": elapsed,
                                "limit_seconds": max_seconds,
                            },
                        )

                        # Schedule lifecycle stale check job
                        self.scheduler.schedule_job(
                            kind=JobKind.LIFECYCLE_STALE_CHECK,
                            entity_type="work_item",
                            entity_id=work_item_id,
                            payload={"work_item_id": work_item_id, "state": state},
                        )
            except Exception as e:
                logger.error(f"Error scanning work item '{item_path.name}': {e}")

        return findings

    def scan_handoff_timeouts(self, now: Optional[datetime] = None) -> List[WatchdogFinding]:
        """Scans for unacknowledged handoffs whose age exceeds handoff_ack_timeout_seconds."""
        current_time = now or self.clock.now()
        findings: List[WatchdogFinding] = []

        if not self.work_dir.is_dir():
            return findings

        for item_path in self.work_dir.iterdir():
            if not item_path.is_dir():
                continue
            handoffs_dir = item_path / "handoffs"
            if not handoffs_dir.is_dir():
                continue

            for handoff_file in handoffs_dir.glob("HANDOFF-*.yaml"):
                try:
                    data = yaml.safe_load(handoff_file.read_text(encoding="utf-8")) or {}
                    ack = data.get("acknowledgement", {})
                    ack_status = str(ack.get("status", "")).lower()
                    if ack_status in {"pending", "awaiting", ""}:
                        created_str = data.get("created_at")
                        if not created_str:
                            continue
                        created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                        if created_dt.tzinfo is None:
                            created_dt = created_dt.replace(tzinfo=timezone.utc)

                        elapsed = (current_time - created_dt).total_seconds()
                        if elapsed > self.handoff_ack_timeout_seconds:
                            handoff_id = data.get("id", handoff_file.stem)
                            work_item_id = item_path.name
                            finding = WatchdogFinding(
                                finding_id=f"wf-handoff-{handoff_id}",
                                component="lifecycle",
                                finding_type="HANDOFF_ACK_OVERDUE",
                                entity_id=handoff_id,
                                severity="WARNING",
                                message=f"Handoff '{handoff_id}' in '{work_item_id}' unacknowledged for {elapsed:.0f}s (timeout: {self.handoff_ack_timeout_seconds}s)",
                                detected_at=current_time,
                                suggested_action="HANDOFF_ACK_TIMEOUT",
                                metadata={"handoff_id": handoff_id, "work_item_id": work_item_id, "elapsed_seconds": elapsed},
                            )
                            findings.append(finding)

                            # Emit domain event
                            self._emit_event(
                                event_type=self.EVENT_HANDOFF_OVERDUE,
                                work_item_id=work_item_id,
                                payload={
                                    "handoff_id": handoff_id,
                                    "work_item_id": work_item_id,
                                    "elapsed_seconds": elapsed,
                                },
                            )

                            # Schedule handoff timeout job
                            self.scheduler.schedule_job(
                                kind=JobKind.HANDOFF_ACK_TIMEOUT,
                                entity_type="handoff",
                                entity_id=handoff_id,
                                payload={"handoff_id": handoff_id, "work_item_id": work_item_id},
                            )
                except Exception as e:
                    logger.error(f"Error inspecting handoff '{handoff_file}': {e}")

        return findings

    def scan_session_expiries(self, now: Optional[datetime] = None) -> List[WatchdogFinding]:
        """Scans for active or pending delegations tied to expired MCP sessions."""
        current_time = now or self.clock.now()
        findings: List[WatchdogFinding] = []

        with self.scheduler.repository.connection() as conn:
            # Check if mcp_sessions table exists
            table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_sessions'"
            ).fetchone()
            if not table_check:
                return findings

            now_ts = current_time.timestamp()
            cursor = conn.execute(
                "SELECT session_id, project_id, work_item_id, host, status, expires_at "
                "FROM mcp_sessions WHERE (expires_at <= ? OR status = 'expired')",
                (now_ts,),
            )
            for row in cursor.fetchall():
                session_id = row["session_id"]
                work_item_id = row["work_item_id"] or "unbound"
                finding = WatchdogFinding(
                    finding_id=f"wf-session-{session_id}",
                    component="session",
                    finding_type="SESSION_EXPIRED",
                    entity_id=session_id,
                    severity="WARNING",
                    message=f"MCP Session '{session_id}' for work item '{work_item_id}' has expired.",
                    detected_at=current_time,
                    suggested_action="SESSION_EXPIRY_CHECK",
                    metadata={"work_item_id": work_item_id, "project_id": row["project_id"], "host": row["host"]},
                )
                findings.append(finding)

                # Emit domain event
                self._emit_event(
                    event_type=self.EVENT_SESSION_EXPIRED,
                    work_item_id=work_item_id,
                    payload={
                        "session_id": session_id,
                        "work_item_id": work_item_id,
                        "project_id": row["project_id"],
                        "host": row["host"],
                        "expired_at": row["expires_at"],
                    },
                )

                # Schedule session expiry check job
                self.scheduler.schedule_job(
                    kind=JobKind.SESSION_EXPIRY_CHECK,
                    entity_type="session",
                    entity_id=session_id,
                    payload={"session_id": session_id, "work_item_id": work_item_id},
                )

        return findings

    def scan_all(self, now: Optional[datetime] = None) -> List[WatchdogFinding]:
        """Runs all watchdog condition scanners."""
        current_time = now or self.clock.now()
        all_findings: List[WatchdogFinding] = []
        all_findings.extend(self.scan_stale_dispatches(current_time))
        all_findings.extend(self.scan_lifecycle_timeboxes(current_time))
        all_findings.extend(self.scan_handoff_timeouts(current_time))
        all_findings.extend(self.scan_session_expiries(current_time))
        return all_findings
