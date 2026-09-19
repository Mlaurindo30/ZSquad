"""Typed exception hierarchy for R5 Project and Delivery Backend Binding.

Strictly stdlib-only. Inherits from SquadError / common domain errors.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class SquadError(Exception):
    """Base Squad runtime exception."""
    pass


class DeliveryBindingError(SquadError):
    """Base exception for all Project and Delivery Backend Binding operations."""

    def __init__(self, message: str = "", code: str = "DEL_ERR_GENERAL", details: Optional[Dict[str, Any]] = None):
        self.code = code
        self.details = details or {}
        super().__init__(message or f"[{code}] Delivery binding error occurred")


# Alias per architectural specification section 21
BindingError = DeliveryBindingError


class ProjectNotResolvedError(DeliveryBindingError):
    """Raised when local project root or declarative project identity cannot be resolved."""

    def __init__(self, path_or_target: str, reason: str = "", details: Optional[Dict[str, Any]] = None):
        self.path_or_target = path_or_target
        self.reason = reason
        msg = f"[PROJ_001] Could not resolve project at '{path_or_target}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg, code="PROJ_001", details=details)


class ProjectConfigNotFoundError(ProjectNotResolvedError):
    """Raised when declarative project.yaml is missing from project root and ancestor search."""

    def __init__(self, search_path: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            path_or_target=search_path,
            reason="Declarative configuration '.agents_squad/config/project.yaml' not found",
            details=details,
        )


class PathContainmentViolationError(DeliveryBindingError):
    """Raised when a directory path attempts traversal or escapes governed runtime boundary."""

    def __init__(self, path: str, boundary: str, details: Optional[Dict[str, Any]] = None):
        self.path = path
        self.boundary = boundary
        msg = f"[SEC_001] PathContainmentViolation: Path '{path}' escapes governed boundary '{boundary}'"
        super().__init__(msg, code="SEC_001", details=details)


class DeliveryBackendNotConfiguredError(DeliveryBindingError):
    """Raised when project declares an external backend but configuration is missing or malformed."""

    def __init__(self, project_id: str, backend_kind: str, reason: str = "", details: Optional[Dict[str, Any]] = None):
        self.project_id = project_id
        self.backend_kind = backend_kind
        msg = f"[DEL_001] Delivery backend '{backend_kind}' not configured for project '{project_id}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg, code="DEL_001", details=details)


class AzureUnavailableError(DeliveryBindingError):
    """Raised when Azure DevOps remote endpoint is unreachable, timed out, or returns 5xx."""

    def __init__(self, endpoint: str, reason: str = "", details: Optional[Dict[str, Any]] = None):
        self.endpoint = endpoint
        self.reason = reason
        msg = f"[AZ_503] Azure DevOps endpoint '{endpoint}' is unavailable: {reason}"
        super().__init__(msg, code="AZ_503", details=details)


class AzureAuthenticationError(AzureUnavailableError):
    """Raised when Azure DevOps rejects credentials (HTTP 401 Unauthorized or 403 Forbidden)."""

    def __init__(self, endpoint: str, reason: str = "Unauthorized or Forbidden", details: Optional[Dict[str, Any]] = None):
        super().__init__(endpoint, reason=reason, details=details)
        self.code = "AZ_401_AUTH"


class AzureTimeoutError(AzureUnavailableError):
    """Raised when Azure DevOps request times out."""

    def __init__(self, endpoint: str, timeout_seconds: float, details: Optional[Dict[str, Any]] = None):
        super().__init__(endpoint, reason=f"Operation timed out after {timeout_seconds}s", details=details)
        self.code = "AZ_TIMEOUT"


class TeamProjectNotFoundError(DeliveryBindingError):
    """Raised when the specified Azure DevOps Team Project cannot be found in the organization."""

    def __init__(self, team_project_name: str, organization_url: str, details: Optional[Dict[str, Any]] = None):
        self.team_project_name = team_project_name
        self.organization_url = organization_url
        msg = f"[AZ_404_PROJ] Team Project '{team_project_name}' not found in organization '{organization_url}'"
        super().__init__(msg, code="AZ_404_PROJ", details=details)


class RepositoryNotFoundError(DeliveryBindingError):
    """Raised when the specified Git repository cannot be found within the Team Project."""

    def __init__(self, repo_name: str, team_project: str, details: Optional[Dict[str, Any]] = None):
        self.repo_name = repo_name
        self.team_project = team_project
        msg = f"[AZ_404_REPO] Git repository '{repo_name}' not found in Team Project '{team_project}'"
        super().__init__(msg, code="AZ_404_REPO", details=details)


class AmbiguousResourceError(DeliveryBindingError):
    """Raised when a resource discovery lookup matches multiple candidates and fails closed."""

    def __init__(self, resource_type: str, query: str, candidates: list[str], details: Optional[Dict[str, Any]] = None):
        self.resource_type = resource_type
        self.query = query
        self.candidates = candidates
        msg = f"[AZ_409_AMBIG] Ambiguous {resource_type} for query '{query}': candidates found: {candidates}"
        super().__init__(msg, code="AZ_409_AMBIG", details=details)


class AmbiguousRepositoryError(AmbiguousResourceError):
    """Raised when repository discovery matches multiple repositories."""

    def __init__(self, query: str, candidates: list[str], details: Optional[Dict[str, Any]] = None):
        super().__init__("Repository", query, candidates, details)


class AmbiguousTeamError(AmbiguousResourceError):
    """Raised when team discovery matches multiple teams."""

    def __init__(self, query: str, candidates: list[str], details: Optional[Dict[str, Any]] = None):
        super().__init__("Team", query, candidates, details)


class MissingResourceError(DeliveryBindingError):
    """Raised when a required delivery resource is missing on the remote backend."""

    def __init__(self, resource_type: str, identifier: str, details: Optional[Dict[str, Any]] = None):
        self.resource_type = resource_type
        self.identifier = identifier
        msg = f"[DEL_404_RES] Missing required resource '{resource_type}' with identifier '{identifier}'"
        super().__init__(msg, code="DEL_404_RES", details=details)


class BindingConflictError(DeliveryBindingError):
    """Raised on optimistic locking conflict or concurrent revision mismatch in binding store."""

    def __init__(self, project_id: str, expected_revision: int, current_revision: int, details: Optional[Dict[str, Any]] = None):
        self.project_id = project_id
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        msg = (
            f"[BIND_409] Binding revision conflict for project '{project_id}': "
            f"expected revision {expected_revision}, but found {current_revision}"
        )
        super().__init__(msg, code="BIND_409", details=details)


class BindingBlockedError(DeliveryBindingError):
    """Raised when project binding cannot proceed due to critical policy or backend violations."""

    def __init__(self, project_id: str, reason: str, details: Optional[Dict[str, Any]] = None):
        self.project_id = project_id
        self.reason = reason
        msg = f"[BIND_BLOCKED] Binding blocked for project '{project_id}': {reason}"
        super().__init__(msg, code="BIND_BLOCKED", details=details)


class InvalidBindingConfigurationError(DeliveryBindingError):
    """Raised when configuration contains syntax errors, forbidden legacy tokens, or invalid schema."""

    def __init__(self, project_id: str, reason: str, details: Optional[Dict[str, Any]] = None):
        self.project_id = project_id
        self.reason = reason
        msg = f"[BIND_INVALID_CONF] Invalid binding configuration for project '{project_id}': {reason}"
        super().__init__(msg, code="BIND_INVALID_CONF", details=details)


class ForbiddenResourceMutationError(DeliveryBindingError):
    """Raised when an operation attempts to mutate or create a forbidden resource, e.g. corporate Team Project."""

    def __init__(self, resource_type: str, action: str = "create", details: Optional[Dict[str, Any]] = None):
        self.resource_type = resource_type
        self.action = action
        msg = (
            f"[R0-ADO-002] ForbiddenResourceMutation: Mutation '{action}' on corporate resource '{resource_type}' "
            "is categorically prohibited. Enterprise Team Projects are shared multi-tenant containers managed externally."
        )
        super().__init__(msg, code="R0-ADO-002", details=details)


class OptimisticConcurrencyError(DeliveryBindingError):
    """Raised when an update to an Azure DevOps work item fails optimistic concurrency check (HTTP 412)."""

    def __init__(
        self,
        work_item_id: str,
        ado_id: int,
        expected_rev: int,
        actual_rev: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.work_item_id = work_item_id
        self.ado_id = ado_id
        self.expected_rev = expected_rev
        self.actual_rev = actual_rev
        msg = (
            f"[ADO_412_CONFLICT] Optimistic concurrency conflict for work item '{work_item_id}' (ADO #{ado_id}): "
            f"expected revision {expected_rev}, but remote was updated (actual: {actual_rev})"
        )
        super().__init__(msg, code="ADO_412_CONFLICT", details=details)


class OrphanWorkItemViolationError(DeliveryBindingError):
    """Raised when a child work item is dispatched to Azure DevOps before its canonical parent is bound."""

    def __init__(
        self,
        work_item_id: str,
        parent_id: str,
        reason: str = "Parent work item not yet synced or missing authoritative remote ado_id",
        details: Optional[Dict[str, Any]] = None,
    ):
        self.work_item_id = work_item_id
        self.parent_id = parent_id
        self.reason = reason
        msg = (
            f"[ADO_ORPHAN_ERR] OrphanWorkItemViolation: Cannot dispatch child '{work_item_id}' "
            f"with parent '{parent_id}': {reason}"
        )
        super().__init__(msg, code="ADO_ORPHAN_ERR", details=details)


class WebhookAuthenticationError(DeliveryBindingError):
    """Raised when an incoming webhook payload fails HMAC-SHA256 signature verification or secret validation."""

    def __init__(self, reason: str = "Invalid signature or missing secret header", details: Optional[Dict[str, Any]] = None):
        self.reason = reason
        msg = f"[AZ_401_WEBHOOK] Webhook authentication failed: {reason}"
        super().__init__(msg, code="AZ_401_WEBHOOK", details=details)


class IllegalRemoteTransitionError(DeliveryBindingError):
    """Raised when an external Azure DevOps transition violates the canonical R4 lifecycle state machine."""

    def __init__(
        self,
        work_item_id: str,
        attempted_remote_state: str,
        canonical_local_state: str,
        reason: str = "",
        details: Optional[Dict[str, Any]] = None,
    ):
        self.work_item_id = work_item_id
        self.attempted_remote_state = attempted_remote_state
        self.canonical_local_state = canonical_local_state
        self.reason = reason
        msg = (
            f"[R4_ILLEGAL_TRANSITION] Illegal remote transition for '{work_item_id}': "
            f"attempted state '{attempted_remote_state}' from local canonical stage '{canonical_local_state}'"
        )
        if reason:
            msg += f": {reason}"
        super().__init__(msg, code="R4_ILLEGAL_TRANSITION", details=details)


class SyncError(DeliveryBindingError):
    """General synchronization plane exception."""

    def __init__(self, message: str = "", code: str = "SYNC_ERR", details: Optional[Dict[str, Any]] = None):
        super().__init__(message or f"[{code}] Delivery synchronization error occurred", code=code, details=details)

