"""Canonical domain and engine exceptions for the Event and Trigger Engine.

Strictly stdlib-only.
"""


class EventEngineError(Exception):
    """Base exception for all Event and Trigger Engine operational errors."""
    pass


class EventAlreadyExistsError(EventEngineError):
    """Raised when attempting to insert an event whose event_id already exists."""

    def __init__(self, event_id: str, message: str = ""):
        self.event_id = event_id
        super().__init__(message or f"DomainEvent with id '{event_id}' already exists in store")


class IdempotencyConflictError(EventEngineError):
    """Raised when an idempotency key already exists but the payload hash differs."""

    def __init__(self, idempotency_key: str, message: str = ""):
        self.idempotency_key = idempotency_key
        super().__init__(
            message or f"Idempotency conflict for key '{idempotency_key}': payload hash mismatch"
        )


class DeliveryNotFoundError(EventEngineError):
    """Raised when a requested delivery record is not found."""

    def __init__(self, delivery_id: str, message: str = ""):
        self.delivery_id = delivery_id
        super().__init__(message or f"EventDelivery with id '{delivery_id}' not found")


class InvalidStateTransitionError(EventEngineError):
    """Raised when an invalid state transition is attempted on an EventDelivery."""

    def __init__(self, delivery_id: str, current_status: str, target_status: str, message: str = ""):
        self.delivery_id = delivery_id
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            message
            or f"Invalid delivery transition for '{delivery_id}': cannot move from '{current_status}' to '{target_status}'"
        )


class TriggerConditionError(EventEngineError):
    """Raised when a declarative trigger condition expression is invalid or cannot be evaluated."""

    def __init__(self, expression: str, reason: str = ""):
        self.expression = expression
        self.reason = reason
        super().__init__(
            f"Failed to evaluate trigger condition expression '{expression}': {reason}"
            if reason
            else f"Invalid trigger condition expression '{expression}'"
        )


class TriggerAlreadyExistsError(EventEngineError):
    """Raised when registering a trigger policy with an ID that is already registered."""

    def __init__(self, trigger_id: str, message: str = ""):
        self.trigger_id = trigger_id
        super().__init__(
            message or f"TriggerPolicy with id '{trigger_id}' already registered"
        )

