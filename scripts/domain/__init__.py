"""Agent Squad Canonical Domain Contracts Package.

Pure Python standard library implementation of domain models, state machines,
receipts, events, and synchronization contracts.
"""

from .backlog import (
    BacklogPlan,
    BacklogPlanItem,
    BacklogPlanStatus,
)
from .common import (
    BaseDomainModel,
    SchemaVersion,
    ValidationError,
    canonical_hash,
    canonical_json,
)
from .delegation import (
    ActivationPacket,
    AncestorSnapshot,
    AssignmentStatus,
    DelegationEnvelope,
    ExecutionAssignment,
    HostCapabilities,
    WorkContext,
)
from .events import (
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
from .lifecycle import (
    CANONICAL_STAGE_POLICIES,
    Acknowledgement,
    AcknowledgementStatus,
    DeliveryCycle,
    GateDecision,
    GateDecisionStatus,
    GateId,
    Handoff,
    LifecycleStage,
    LifecycleTransition,
    StagePolicy,
)
from .project import (
    AdoBinding,
    DeliveryBackendKind,
    LocalWorkMirror,
    ProjectBinding,
)
from .receipts import (
    BaseReceipt,
    DispatchReceipt,
    ExecutionReceipt,
    GovernanceReceipt,
    QAReceipt,
    ReceiptType,
    ReviewReceipt,
    SecurityReceipt,
    TestReceipt,
    assert_sod_compliance,
)
from .sync import (
    AdoWorkItemBinding,
    ReconciliationAction,
    ReconciliationDecision,
    ReconciliationOutcome,
    SyncState,
    SyncStatus,
)
from .work_items import (
    AcceptanceCriterion,
    RiskTier,
    SyncStateKind,
    WorkHierarchy,
    WorkItem,
    WorkItemId,
    WorkItemKind,
    WorkItemType,
)

__all__ = [
    # Common
    "BaseDomainModel",
    "SchemaVersion",
    "ValidationError",
    "canonical_hash",
    "canonical_json",
    # Project
    "DeliveryBackendKind",
    "ProjectBinding",
    "AdoBinding",
    "LocalWorkMirror",
    # Work Items
    "WorkItemKind",
    "WorkItemType",
    "RiskTier",
    "SyncStateKind",
    "WorkItemId",
    "AcceptanceCriterion",
    "WorkHierarchy",
    "WorkItem",
    # Backlog
    "BacklogPlanStatus",
    "BacklogPlanItem",
    "BacklogPlan",
    # Lifecycle
    "LifecycleStage",
    "GateId",
    "GateDecisionStatus",
    "GateDecision",
    "StagePolicy",
    "AcknowledgementStatus",
    "Acknowledgement",
    "Handoff",
    "LifecycleTransition",
    "DeliveryCycle",
    "CANONICAL_STAGE_POLICIES",
    # Events
    "TriggerActionKind",
    "DeliveryStatus",
    "FindingKind",
    "FindingSeverity",
    "DomainEvent",
    "TriggerPolicy",
    "RetryPolicy",
    "EventDelivery",
    "SchedulePolicy",
    "WatchdogFinding",
    # Delegation
    "AssignmentStatus",
    "ExecutionAssignment",
    "AncestorSnapshot",
    "WorkContext",
    "ActivationPacket",
    "DelegationEnvelope",
    "HostCapabilities",
    # Receipts
    "ReceiptType",
    "BaseReceipt",
    "DispatchReceipt",
    "ExecutionReceipt",
    "ReviewReceipt",
    "SecurityReceipt",
    "TestReceipt",
    "QAReceipt",
    "GovernanceReceipt",
    "assert_sod_compliance",
    # Sync
    "SyncStatus",
    "ReconciliationAction",
    "AdoWorkItemBinding",
    "SyncState",
    "ReconciliationDecision",
    "ReconciliationOutcome",
]
