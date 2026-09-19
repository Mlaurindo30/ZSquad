"""Domain models for Events, Triggers, Outbox Delivery, Policies and Watchdog Findings.

Strictly stdlib-only.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional
import uuid

from .common import BaseDomainModel, ValidationError, canonical_json


class TriggerActionKind(str, Enum):
    ACTIVATE_AGENT = "ACTIVATE_AGENT"
    EVALUATE_GATE = "EVALUATE_GATE"
    ADVANCE_STAGE = "ADVANCE_STAGE"
    REQUEST_HANDOFF_ACK = "REQUEST_HANDOFF_ACK"
    DISPATCH_EXTERNAL_SYNC = "DISPATCH_EXTERNAL_SYNC"
    ESCALATE_ALERT = "ESCALATE_ALERT"
    SCHEDULE_RETRY = "SCHEDULE_RETRY"


class DeliveryStatus(str, Enum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class FindingKind(str, Enum):
    STALE_LIFECYCLE = "STALE_LIFECYCLE"
    HANDOFF_AWAITING_ACK = "HANDOFF_AWAITING_ACK"
    PENDING_SYNC_OVERDUE = "PENDING_SYNC_OVERDUE"
    WIP_LIMIT_BREACH = "WIP_LIMIT_BREACH"
    TIMEBOX_EXPIRED = "TIMEBOX_EXPIRED"
    UNPROCESSED_EVENT_SPIKE = "UNPROCESSED_EVENT_SPIKE"


class FindingSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class DomainEvent(BaseDomainModel):
    """Canonical domain event record for state mutations and outbox delivery."""

    event_id: str
    event_type: str
    work_item_id: str
    project_id: str
    source: str
    correlation_id: str
    causation_id: str
    idempotency_key: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id or not self.event_id.strip():
            raise ValidationError("event_id must not be empty")
        if not self.event_type or not self.event_type.strip():
            raise ValidationError("event_type must not be empty")
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValidationError("work_item_id must not be empty")
        if not self.project_id or not self.project_id.strip():
            raise ValidationError("project_id must not be empty")
        if not self.source or not self.source.strip():
            raise ValidationError("source must not be empty")
        if not self.correlation_id or not self.correlation_id.strip():
            raise ValidationError("correlation_id must not be empty")
        if not self.causation_id or not self.causation_id.strip():
            raise ValidationError("causation_id must not be empty")
        if not self.idempotency_key or not self.idempotency_key.strip():
            raise ValidationError("idempotency_key must not be empty")

    @classmethod
    def create(
        cls,
        event_type: str,
        work_item_id: str,
        project_id: str,
        source: str,
        correlation_id: str,
        causation_id: str,
        payload: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> "DomainEvent":
        """Factory method computing deterministic idempotency key and populating identifiers."""
        payload_data = payload or {}
        assigned_id = event_id or str(uuid.uuid4())
        assigned_ts = timestamp or datetime.utcnow()

        seed = f"{event_type}:{work_item_id}:{causation_id}:{canonical_json(payload_data)}"
        idempotency_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()

        return cls(
            event_id=assigned_id,
            event_type=event_type,
            work_item_id=work_item_id,
            project_id=project_id,
            source=source,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
            timestamp=assigned_ts,
            payload=payload_data,
        )


@dataclass(frozen=True)
class TriggerPolicy(BaseDomainModel):
    """Declarative trigger mapping event occurrences to autonomous action requests."""

    trigger_id: str
    event_type: str
    condition_expression: str
    action_kind: TriggerActionKind
    target_role: Optional[str] = None
    payload_mapping: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.trigger_id or not self.trigger_id.strip():
            raise ValidationError("trigger_id must not be empty")
        if not self.event_type or not self.event_type.strip():
            raise ValidationError("event_type must not be empty")
        if not self.condition_expression or not self.condition_expression.strip():
            raise ValidationError("condition_expression must not be empty")


@dataclass(frozen=True)
class RetryPolicy(BaseDomainModel):
    """Backoff and retry configuration for asynchronous delivery."""

    max_attempts: int = 3
    initial_backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_attempts < 0:
            raise ValidationError("max_attempts must be non-negative")
        if self.initial_backoff_seconds <= 0:
            raise ValidationError("initial_backoff_seconds must be positive")


@dataclass(frozen=True)
class EventDelivery(BaseDomainModel):
    """Tracking entity for event outbox distribution to an individual subscriber."""

    delivery_id: str
    event_id: str
    subscriber: str
    status: DeliveryStatus
    attempt_count: int = 0
    last_attempt_at: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class SchedulePolicy(BaseDomainModel):
    """Periodic maintenance policy for watchdog and background workers."""

    policy_id: str
    cron_expression: str
    action_type: str
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if not self.policy_id or not self.policy_id.strip():
            raise ValidationError("policy_id must not be empty")
        if not self.cron_expression or not self.cron_expression.strip():
            raise ValidationError("cron_expression must not be empty")
        if not self.action_type or not self.action_type.strip():
            raise ValidationError("action_type must not be empty")


@dataclass(frozen=True)
class WatchdogFinding(BaseDomainModel):
    """Anomaly or policy breach discovered by background supervision."""

    finding_id: str
    kind: FindingKind
    severity: FindingSeverity
    work_item_id: Optional[str]
    description: str
    recommended_action: str
    detected_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if not self.finding_id or not self.finding_id.strip():
            raise ValidationError("finding_id must not be empty")
        if not self.description or not self.description.strip():
            raise ValidationError("description must not be empty")
        if not self.recommended_action or not self.recommended_action.strip():
            raise ValidationError("recommended_action must not be empty")


# ---------------------------------------------------------------------------
# R11 — Host-Native Specialist Dispatch Domain Events
# ---------------------------------------------------------------------------

EVENT_SPECIALIST_DISPATCHED = "agent_squad.specialist.dispatched"
EVENT_SPECIALIST_DISPATCH_FAILED = "agent_squad.specialist.dispatch_failed"


@dataclass(frozen=True)
class SpecialistDispatchedEvent(DomainEvent):
    """Event emitted when a specialist agent is successfully dispatched to a host substrate."""

    @classmethod
    def create_dispatched(
        cls,
        work_item_id: str,
        project_id: str,
        delegation_id: str,
        dispatch_id: str,
        sender_role: str,
        target_role: str,
        host_kind: str,
        host_execution_id: str,
        receipt_id: str,
        instruction_hash: str,
        dispatched_at: datetime,
        source: str = "runtime.dispatch_service",
    ) -> "SpecialistDispatchedEvent":
        dispatched_at_iso = (
            dispatched_at.isoformat()
            if isinstance(dispatched_at, datetime)
            else str(dispatched_at)
        )
        payload = {
            "dispatch_id": dispatch_id,
            "delegation_id": delegation_id,
            "work_item_id": work_item_id,
            "sender_role": sender_role,
            "target_role": target_role,
            "host_kind": host_kind,
            "host_execution_id": host_execution_id,
            "receipt_id": receipt_id,
            "instruction_hash": instruction_hash,
            "dispatched_at": dispatched_at_iso,
        }
        base_event = DomainEvent.create(
            event_type=EVENT_SPECIALIST_DISPATCHED,
            work_item_id=work_item_id,
            project_id=project_id,
            source=source,
            correlation_id=work_item_id,
            causation_id=delegation_id,
            payload=payload,
        )
        return cls(
            event_id=base_event.event_id,
            event_type=base_event.event_type,
            work_item_id=base_event.work_item_id,
            project_id=base_event.project_id,
            source=base_event.source,
            correlation_id=base_event.correlation_id,
            causation_id=base_event.causation_id,
            idempotency_key=base_event.idempotency_key,
            timestamp=base_event.timestamp,
            payload=base_event.payload,
        )


@dataclass(frozen=True)
class SpecialistDispatchFailedEvent(DomainEvent):
    """Event emitted when a specialist agent dispatch attempt fails."""

    @classmethod
    def create_failed(
        cls,
        work_item_id: str,
        project_id: str,
        delegation_id: str,
        dispatch_id: str,
        target_role: str,
        host_kind: str,
        error_code: str,
        error_message: str,
        retryable: bool,
        attempt_number: int,
        source: str = "runtime.dispatch_service",
    ) -> "SpecialistDispatchFailedEvent":
        payload = {
            "dispatch_id": dispatch_id,
            "delegation_id": delegation_id,
            "work_item_id": work_item_id,
            "target_role": target_role,
            "host_kind": host_kind,
            "error_code": error_code,
            "error_message": error_message,
            "retryable": retryable,
            "attempt_number": attempt_number,
        }
        base_event = DomainEvent.create(
            event_type=EVENT_SPECIALIST_DISPATCH_FAILED,
            work_item_id=work_item_id,
            project_id=project_id,
            source=source,
            correlation_id=work_item_id,
            causation_id=delegation_id,
            payload=payload,
        )
        return cls(
            event_id=base_event.event_id,
            event_type=base_event.event_type,
            work_item_id=base_event.work_item_id,
            project_id=base_event.project_id,
            source=base_event.source,
            correlation_id=base_event.correlation_id,
            causation_id=base_event.causation_id,
            idempotency_key=base_event.idempotency_key,
            timestamp=base_event.timestamp,
            payload=base_event.payload,
        )

