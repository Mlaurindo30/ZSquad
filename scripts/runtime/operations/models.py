"""Operational Domain Models and Data Structures (Milestone R13).

Strictly stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class JobKind(str, Enum):
    """Authoritative taxonomy of operations scheduled jobs."""

    OUTBOX_DRAIN = "OUTBOX_DRAIN"
    DISPATCH_RECONCILE = "DISPATCH_RECONCILE"
    EXECUTION_RECONCILE = "EXECUTION_RECONCILE"
    AZURE_RECONCILE = "AZURE_RECONCILE"
    LIFECYCLE_STALE_CHECK = "LIFECYCLE_STALE_CHECK"
    HANDOFF_ACK_TIMEOUT = "HANDOFF_ACK_TIMEOUT"
    SESSION_EXPIRY_CHECK = "SESSION_EXPIRY_CHECK"


class JobStatus(str, Enum):
    """Operational lifecycle status of a scheduled job."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ScheduledJob:
    """Authoritative scheduled job representation."""

    job_id: str
    kind: JobKind
    entity_type: str
    entity_id: str
    due_at: datetime
    attempt: int
    max_attempts: int
    status: JobStatus
    payload: Dict[str, Any]
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    last_error: Optional[str] = None
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class OperationalLease:
    """Durable multi-process mutual exclusion lease."""

    resource_key: str
    lease_id: str
    owner: str
    expires_at: datetime
    acquired_at: datetime


@dataclass(frozen=True)
class WatchdogFinding:
    """Condition detected by an operational watchdog scanner."""

    finding_id: str
    component: str
    finding_type: str
    entity_id: str
    severity: str  # "INFO", "WARNING", "ERROR"
    message: str
    detected_at: datetime
    suggested_action: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReconciliationResult:
    """Summary of a single scheduled job reconciliation execution."""

    job_id: str
    kind: JobKind
    success: bool
    status: JobStatus
    error: Optional[str] = None
    action_taken: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationsRunReport:
    """Comprehensive execution report returned by OperationsControlService.run_once()."""

    run_id: str
    started_at: datetime
    completed_at: datetime
    findings: List[WatchdogFinding] = field(default_factory=list)
    jobs_scheduled: List[str] = field(default_factory=list)
    jobs_claimed: List[str] = field(default_factory=list)
    jobs_completed: List[str] = field(default_factory=list)
    jobs_failed: List[str] = field(default_factory=list)
    dead_letters: List[str] = field(default_factory=list)
    events_emitted: List[str] = field(default_factory=list)
