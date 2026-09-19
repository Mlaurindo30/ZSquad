"""Canonical exports for R5/R6 Project Delivery Binding & Bidirectional Sync subsystem.

Strictly stdlib-only.
"""

from scripts.runtime.delivery.azure_discovery import (
    AzureDiscoveryPort,
    BoardInfo,
    ClassificationNodeInfo,
    ProcessTemplateInfo,
    ReadOnlyAzureDiscovery,
    RepositoryInfo,
    TeamInfo,
    TeamProjectInfo,
)
from scripts.runtime.delivery.azure_writer import (
    AzureWriter,
    AzureWriterPort,
    TransportCallable as WriterTransportCallable,
)
from scripts.runtime.delivery.binding import (
    BindingResolutionResult,
    DeliveryBindingManager,
    ProjectBindingStatus,
    ProjectDeliveryBindingService,
    ResourceBindingStatus,
    ResourceStatusReport,
)
from scripts.runtime.delivery.errors import (
    AmbiguousRepositoryError,
    AmbiguousResourceError,
    AmbiguousTeamError,
    AzureAuthenticationError,
    AzureTimeoutError,
    AzureUnavailableError,
    BindingBlockedError,
    BindingConflictError,
    BindingError,
    DeliveryBackendNotConfiguredError,
    DeliveryBindingError,
    ForbiddenResourceMutationError,
    IllegalRemoteTransitionError,
    InvalidBindingConfigurationError,
    MissingResourceError,
    OptimisticConcurrencyError,
    OrphanWorkItemViolationError,
    PathContainmentViolationError,
    ProjectConfigNotFoundError,
    ProjectNotResolvedError,
    SquadError,
    SyncError,
    TeamProjectNotFoundError,
    WebhookAuthenticationError,
)
from scripts.runtime.delivery.reconciliation import (
    ConflictReconciliationEngine,
    ResourceReconciliationResult,
    ResourceReconciliationService,
)
from scripts.runtime.delivery.repository import (
    BindingHistoryRecord,
    InboundEventRecord,
    ProjectBindingRecord,
    SqliteBindingRepository,
    SyncOutboxRecord,
    WorkItemBindingRecord,
    compute_binding_fingerprint,
    sanitize_credentials,
)
from scripts.runtime.delivery.state_mapping import (
    CANONICAL_BOARD_COLUMNS,
    AzureStateMapping,
    azure_to_lifecycle_stage,
    extract_stage_from_tags,
    map_stage_to_azure,
    normalize_template_name,
    stage_to_azure_state,
    stage_to_board_column,
    stage_to_board_column_type,
    work_item_kind_to_ado_type,
)
from scripts.runtime.delivery.sync import (
    DeliverySyncService,
    OutboxDrainResult,
)
from scripts.runtime.delivery.webhook import (
    InboundSyncEvent,
    InboundWebhookReceiver,
    WebhookProcessResult,
    WebhookProcessStatus,
)

__all__ = [
    # Errors
    "SquadError",
    "DeliveryBindingError",
    "BindingError",
    "ProjectNotResolvedError",
    "ProjectConfigNotFoundError",
    "PathContainmentViolationError",
    "DeliveryBackendNotConfiguredError",
    "AzureUnavailableError",
    "AzureAuthenticationError",
    "AzureTimeoutError",
    "TeamProjectNotFoundError",
    "MissingResourceError",
    "AmbiguousResourceError",
    "AmbiguousRepositoryError",
    "AmbiguousTeamError",
    "BindingConflictError",
    "BindingBlockedError",
    "InvalidBindingConfigurationError",
    "ForbiddenResourceMutationError",
    "OptimisticConcurrencyError",
    "OrphanWorkItemViolationError",
    "WebhookAuthenticationError",
    "IllegalRemoteTransitionError",
    "SyncError",
    # Repository & Records
    "ProjectBindingRecord",
    "BindingHistoryRecord",
    "WorkItemBindingRecord",
    "SyncOutboxRecord",
    "InboundEventRecord",
    "SqliteBindingRepository",
    "compute_binding_fingerprint",
    "sanitize_credentials",
    # Azure Discovery
    "AzureDiscoveryPort",
    "ReadOnlyAzureDiscovery",
    "TeamProjectInfo",
    "RepositoryInfo",
    "TeamInfo",
    "ClassificationNodeInfo",
    "BoardInfo",
    "ProcessTemplateInfo",
    # Azure Writer
    "AzureWriterPort",
    "AzureWriter",
    "WriterTransportCallable",
    # Service & Status
    "ResourceBindingStatus",
    "ProjectBindingStatus",
    "ResourceStatusReport",
    "BindingResolutionResult",
    "ProjectDeliveryBindingService",
    "DeliveryBindingManager",
    # State Mapping
    "CANONICAL_BOARD_COLUMNS",
    "AzureStateMapping",
    "normalize_template_name",
    "stage_to_board_column",
    "stage_to_board_column_type",
    "stage_to_azure_state",
    "map_stage_to_azure",
    "extract_stage_from_tags",
    "azure_to_lifecycle_stage",
    "work_item_kind_to_ado_type",
    # Reconciliation
    "ConflictReconciliationEngine",
    "ResourceReconciliationResult",
    "ResourceReconciliationService",
    # Webhook
    "InboundWebhookReceiver",
    "InboundSyncEvent",
    "WebhookProcessStatus",
    "WebhookProcessResult",
    # Sync Service
    "DeliverySyncService",
    "OutboxDrainResult",
]
