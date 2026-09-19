"""Authoritative Inbound Webhook and Service Hook Ingestion Service.

Strictly stdlib-only.
Enforces Sections 17, 18, and 19 of R6 Specification:
- HMAC-SHA256 signature and token authentication with timing-safe comparison.
- Absolute credential isolation (SEC-R1-01).
- Event normalization for Azure DevOps Service Hooks.
- Deduplication by messageId/notificationId via SQLite ledger (delivery_inbound_events).
- Monotonic revision check: stale event discarding and gap detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import hmac
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from scripts.domain.common import canonical_json
from scripts.runtime.delivery.errors import WebhookAuthenticationError
from scripts.runtime.delivery.repository import (
    InboundEventRecord,
    SqliteBindingRepository,
    WorkItemBindingRecord,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class WebhookProcessStatus(str, Enum):
    """Processing outcomes for incoming Service Hook events."""

    ACCEPTED = "ACCEPTED"
    DUPLICATE_IGNORED = "DUPLICATE_IGNORED"
    STALE_IGNORED = "STALE_IGNORED"
    REDUNDANT_IGNORED = "REDUNDANT_IGNORED"
    GAP_DETECTED = "GAP_DETECTED"
    REJECTED_UNAUTHORIZED = "REJECTED_UNAUTHORIZED"


@dataclass(frozen=True)
class InboundSyncEvent:
    """Normalized internal representation of an Azure DevOps Service Hook event."""

    event_id: str
    subscription_id: str
    event_type: str
    ado_id: int
    remote_rev: int
    state: str
    board_column: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    title: Optional[str] = None
    area_path: Optional[str] = None
    iteration_path: Optional[str] = None
    fields: Dict[str, Any] = field(default_factory=dict)
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    payload_hash: str = ""


@dataclass(frozen=True)
class WebhookProcessResult:
    """Result of validating and ingesting an inbound webhook."""

    status: WebhookProcessStatus
    event: Optional[InboundSyncEvent] = None
    needs_full_refresh: bool = False
    message: str = ""


class InboundWebhookReceiver:
    """Receives, authenticates, deduplicates, and normalizes Azure DevOps Service Hook payloads."""

    def __init__(
        self,
        repository: SqliteBindingRepository,
        secret_token: Optional[str] = None,
        secret_env_var: Optional[str] = "AZURE_WEBHOOK_SECRET",
        expected_team_project: Optional[str] = None,
        allowed_subscription_ids: Optional[List[str]] = None,
    ) -> None:
        self.repository = repository
        self._secret_token = secret_token
        self._secret_env_var = secret_env_var
        self.expected_team_project = expected_team_project
        self.allowed_subscription_ids = allowed_subscription_ids

    def _resolve_secret(self, explicit_secret: Optional[str] = None) -> str:
        if explicit_secret and explicit_secret.strip():
            return explicit_secret.strip()
        if self._secret_token and self._secret_token.strip():
            return self._secret_token.strip()
        if self._secret_env_var:
            val = os.environ.get(self._secret_env_var)
            if val and val.strip():
                return val.strip()
        return ""

    def verify_authenticity(
        self,
        raw_body: bytes,
        headers: Dict[str, str],
        explicit_secret: Optional[str] = None,
    ) -> bool:
        """Validates payload authenticity using HMAC-SHA256 signature or shared secret header.

        Uses hmac.compare_digest for timing-safe comparison.
        """
        secret = self._resolve_secret(explicit_secret)
        if not secret:
            # If no secret is configured, reject for security unless running in explicit test mode
            raise WebhookAuthenticationError("No webhook secret configured for authentication")

        norm_headers = {k.lower(): v for k, v in headers.items()}

        # 1. Check HMAC-SHA256 signature header (e.g. X-Hub-Signature-256 or X-Squad-Signature)
        sig_header = (
            norm_headers.get("x-hub-signature-256")
            or norm_headers.get("x-squad-signature")
            or norm_headers.get("x-signature")
        )
        if sig_header:
            if sig_header.startswith("sha256="):
                sig_header = sig_header[7:]
            computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(computed.lower(), sig_header.lower()):
                return True

        # 2. Check secret reference header (e.g. X-Squad-Secret-Ref or X-Squad-Token)
        token_header = (
            norm_headers.get("x-squad-secret")
            or norm_headers.get("x-squad-token")
            or norm_headers.get("x-webhook-token")
        )
        if token_header:
            if hmac.compare_digest(token_header, secret):
                return True

        raise WebhookAuthenticationError("Invalid or missing webhook signature/token")

    def normalize_payload(self, raw_payload: Dict[str, Any]) -> InboundSyncEvent:
        """Parses and normalizes an Azure DevOps Service Hook JSON payload."""
        event_id = str(raw_payload.get("id") or raw_payload.get("notificationId") or "")
        subscription_id = str(raw_payload.get("subscriptionId") or "")
        event_type = str(raw_payload.get("eventType") or "workitem.updated")

        resource = raw_payload.get("resource") or {}
        ado_id_raw = resource.get("id") or resource.get("workItemId") or 0
        try:
            ado_id = int(ado_id_raw)
        except (ValueError, TypeError):
            ado_id = 0

        rev_raw = resource.get("rev") or 0
        try:
            remote_rev = int(rev_raw)
        except (ValueError, TypeError):
            remote_rev = 0

        fields = resource.get("fields") or {}
        # Support revision delta format: fields may contain {"System.State": {"newValue": "Active"}}
        parsed_fields: Dict[str, Any] = {}
        for k, v in fields.items():
            if isinstance(v, dict) and "newValue" in v:
                parsed_fields[k] = v["newValue"]
            else:
                parsed_fields[k] = v

        state = str(parsed_fields.get("System.State") or "")
        board_column = parsed_fields.get("System.BoardColumn")
        if board_column:
            board_column = str(board_column).strip()

        raw_tags = parsed_fields.get("System.Tags")
        tags: List[str] = []
        if isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.split(";") if t.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]

        title = parsed_fields.get("System.Title")
        area_path = parsed_fields.get("System.AreaPath")
        iteration_path = parsed_fields.get("System.IterationPath")

        # Deterministic payload hash for deduplication
        hash_dict = {
            "id": event_id,
            "eventType": event_type,
            "ado_id": ado_id,
            "rev": remote_rev,
            "state": state,
            "board_column": board_column,
            "tags": sorted(tags),
        }
        payload_hash = hashlib.sha256(canonical_json(hash_dict).encode("utf-8")).hexdigest()

        return InboundSyncEvent(
            event_id=event_id,
            subscription_id=subscription_id,
            event_type=event_type,
            ado_id=ado_id,
            remote_rev=remote_rev,
            state=state,
            board_column=board_column,
            tags=tags,
            title=title,
            area_path=area_path,
            iteration_path=iteration_path,
            fields=parsed_fields,
            raw_payload=raw_payload,
            payload_hash=payload_hash,
        )

    def process_webhook(
        self,
        raw_body: bytes,
        headers: Dict[str, str],
        explicit_secret: Optional[str] = None,
    ) -> WebhookProcessResult:
        """End-to-end ingestion pipeline: authentication, deduplication, and revision validation."""
        # 1. Authenticate request
        try:
            self.verify_authenticity(raw_body, headers, explicit_secret=explicit_secret)
        except WebhookAuthenticationError as auth_err:
            return WebhookProcessResult(
                status=WebhookProcessStatus.REJECTED_UNAUTHORIZED,
                message=str(auth_err),
            )

        # 2. Parse JSON
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            return WebhookProcessResult(
                status=WebhookProcessStatus.REJECTED_UNAUTHORIZED,
                message=f"Malformed JSON payload: {exc}",
            )

        if not isinstance(payload, dict):
            return WebhookProcessResult(
                status=WebhookProcessStatus.REJECTED_UNAUTHORIZED,
                message="Malformed payload: expected JSON object",
            )

        # Validate subscription ID if configured
        sub_id = payload.get("subscriptionId")
        if self.allowed_subscription_ids is not None:
            if not sub_id or sub_id not in self.allowed_subscription_ids:
                return WebhookProcessResult(
                    status=WebhookProcessStatus.REJECTED_UNAUTHORIZED,
                    message=f"Unknown or unauthorized subscription ID '{sub_id}'",
                )

        # Validate Team Project if configured
        if self.expected_team_project:
            resource = payload.get("resource") or {}
            fields = resource.get("fields") or {}
            event_tp = fields.get("System.TeamProject")
            if not event_tp:
                containers = payload.get("resourceContainers") or {}
                event_tp = (containers.get("project") or {}).get("id")
            if event_tp and event_tp != self.expected_team_project:
                return WebhookProcessResult(
                    status=WebhookProcessStatus.REJECTED_UNAUTHORIZED,
                    message=f"Team Project mismatch: expected '{self.expected_team_project}', got '{event_tp}'",
                )

        # Validate revision is integer
        resource = payload.get("resource") or {}
        rev_raw = resource.get("rev")
        if rev_raw is not None:
            try:
                int(rev_raw)
            except (ValueError, TypeError):
                return WebhookProcessResult(
                    status=WebhookProcessStatus.REJECTED_UNAUTHORIZED,
                    message=f"Malformed revision '{rev_raw}': must be integer",
                )

        event = self.normalize_payload(payload)

        # 3. Deduplication by event_id
        inbound_record = InboundEventRecord(
            event_id=event.event_id,
            subscription_id=event.subscription_id,
            event_type=event.event_type,
            ado_id=event.ado_id,
            remote_rev=event.remote_rev,
            received_at=_utc_now_iso(),
            payload_hash=event.payload_hash,
        )
        is_new = self.repository.record_inbound_event(inbound_record)
        if not is_new:
            return WebhookProcessResult(
                status=WebhookProcessStatus.DUPLICATE_IGNORED,
                event=event,
                message=f"Event '{event.event_id}' already processed (DUPLICATE_IGNORED).",
            )

        # 4. Out-of-order & stale check against work item binding
        if event.ado_id > 0:
            binding = self.repository.get_work_item_binding_by_ado_id(event.ado_id)
            if binding is not None:
                recorded_rev = binding.remote_rev
                if event.remote_rev < recorded_rev:
                    return WebhookProcessResult(
                        status=WebhookProcessStatus.STALE_IGNORED,
                        event=event,
                        message=f"Stale revision {event.remote_rev} < recorded {recorded_rev} (STALE_IGNORED).",
                    )
                elif event.remote_rev == recorded_rev:
                    return WebhookProcessResult(
                        status=WebhookProcessStatus.REDUNDANT_IGNORED,
                        event=event,
                        message=f"Redundant revision {event.remote_rev} == recorded {recorded_rev} (REDUNDANT_IGNORED).",
                    )
                elif event.remote_rev > recorded_rev + 1:
                    # Gap detected: missed intermediate updates -> needs full refresh
                    return WebhookProcessResult(
                        status=WebhookProcessStatus.GAP_DETECTED,
                        event=event,
                        needs_full_refresh=True,
                        message=f"Revision gap detected: event rev {event.remote_rev} > recorded {recorded_rev} + 1.",
                    )

        return WebhookProcessResult(
            status=WebhookProcessStatus.ACCEPTED,
            event=event,
            message="Webhook event verified and accepted.",
        )
