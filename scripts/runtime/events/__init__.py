"""Runtime events package providing durable storage, triggers, and outbox delivery engine.

Strictly stdlib-only.
"""

from scripts.domain.events import (
    DeliveryStatus,
    DomainEvent,
    EventDelivery,
    FindingKind,
    FindingSeverity,
    RetryPolicy,
    SchedulePolicy,
    TriggerActionKind,
    TriggerPolicy,
    WatchdogFinding,
)
from scripts.runtime.events.engine import EventEngine, sanitize_error_message
from scripts.runtime.events.errors import (
    DeliveryNotFoundError,
    EventAlreadyExistsError,
    EventEngineError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    TriggerAlreadyExistsError,
    TriggerConditionError,
)
from scripts.runtime.events.store import SqliteEventStore, StoredEventDelivery
from scripts.runtime.events.triggers import TriggerRegistry, evaluate_condition

__all__ = [
    # Domain Models re-exported
    "DomainEvent",
    "EventDelivery",
    "TriggerPolicy",
    "RetryPolicy",
    "SchedulePolicy",
    "WatchdogFinding",
    "TriggerActionKind",
    "DeliveryStatus",
    "FindingKind",
    "FindingSeverity",
    # Store & Deliveries
    "SqliteEventStore",
    "StoredEventDelivery",
    # Triggers
    "TriggerRegistry",
    "evaluate_condition",
    # Engine
    "EventEngine",
    "sanitize_error_message",
    # Exceptions
    "EventEngineError",
    "EventAlreadyExistsError",
    "IdempotencyConflictError",
    "DeliveryNotFoundError",
    "InvalidStateTransitionError",
    "TriggerAlreadyExistsError",
    "TriggerConditionError",
]
