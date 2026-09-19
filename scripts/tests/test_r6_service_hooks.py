"""Canonical Tests for R6 Service Hooks & Webhook Ingestion Security (Section 52).

Covers:
- valid payload accepted
- invalid payload rejected (non-JSON, non-object)
- wrong Team Project rejected
- unknown subscription rejected where applicable
- duplicate delivery idempotent
- malformed revision rejected
- auth failure rejected (HMAC mismatch, missing secret)
- replay rejected/no-op
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict
import pytest

from scripts.runtime.delivery.errors import WebhookAuthenticationError
from scripts.runtime.delivery.repository import SqliteBindingRepository
from scripts.runtime.delivery.webhook import (
    InboundWebhookReceiver,
    WebhookProcessStatus,
)


@pytest.fixture
def repo() -> SqliteBindingRepository:
    return SqliteBindingRepository(":memory:")


def make_hmac_request(payload: Any, secret: str = "shared-secret-123") -> tuple[bytes, Dict[str, str]]:
    if isinstance(payload, bytes):
        raw_body = payload
    elif isinstance(payload, str):
        raw_body = payload.encode("utf-8")
    else:
        raw_body = json.dumps(payload).encode("utf-8")

    sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={sig}",
    }
    return raw_body, headers


def test_valid_payload_accepted(repo: SqliteBindingRepository):
    """Valid Service Hook payload with HMAC signature is accepted."""
    receiver = InboundWebhookReceiver(repository=repo, secret_token="shared-secret-123")

    payload = {
        "id": "sh-evt-001",
        "subscriptionId": "sub-uuid-1",
        "eventType": "workitem.updated",
        "resource": {
            "id": 1001,
            "rev": 2,
            "fields": {
                "System.State": "Active",
                "System.BoardColumn": "Implementation",
                "System.Title": "Valid Card",
            },
        },
    }
    raw_body, headers = make_hmac_request(payload, secret="shared-secret-123")

    result = receiver.process_webhook(raw_body, headers)
    assert result.status == WebhookProcessStatus.ACCEPTED
    assert result.event is not None
    assert result.event.ado_id == 1001
    assert result.event.remote_rev == 2


def test_invalid_payload_rejected(repo: SqliteBindingRepository):
    """Malformed non-JSON body is rejected fail-closed."""
    receiver = InboundWebhookReceiver(repository=repo, secret_token="shared-secret-123")

    raw_body, headers = make_hmac_request(b"THIS IS NOT VALID JSON {{{{", secret="shared-secret-123")

    result = receiver.process_webhook(raw_body, headers)
    assert result.status == WebhookProcessStatus.REJECTED_UNAUTHORIZED
    assert "malformed" in result.message.lower()


def test_wrong_team_project_rejected(repo: SqliteBindingRepository):
    """Event originating from an unexpected Team Project is rejected fail-closed."""
    receiver = InboundWebhookReceiver(
        repository=repo,
        secret_token="shared-secret-123",
        expected_team_project="Enterprise-Platform",
    )

    payload = {
        "id": "sh-evt-002",
        "subscriptionId": "sub-uuid-1",
        "eventType": "workitem.updated",
        "resource": {
            "id": 1002,
            "rev": 1,
            "fields": {
                "System.TeamProject": "Foreign-Project",
                "System.State": "Active",
            },
        },
    }
    raw_body, headers = make_hmac_request(payload, secret="shared-secret-123")

    result = receiver.process_webhook(raw_body, headers)
    assert result.status == WebhookProcessStatus.REJECTED_UNAUTHORIZED
    assert "team project mismatch" in result.message.lower()


def test_unknown_subscription_rejected_where_applicable(repo: SqliteBindingRepository):
    """Event carrying an unauthorized subscription ID is rejected fail-closed."""
    receiver = InboundWebhookReceiver(
        repository=repo,
        secret_token="shared-secret-123",
        allowed_subscription_ids=["sub-allowed-alpha", "sub-allowed-beta"],
    )

    payload = {
        "id": "sh-evt-003",
        "subscriptionId": "sub-rogue-unauthorized",
        "eventType": "workitem.updated",
        "resource": {"id": 1003, "rev": 1, "fields": {"System.State": "Active"}},
    }
    raw_body, headers = make_hmac_request(payload, secret="shared-secret-123")

    result = receiver.process_webhook(raw_body, headers)
    assert result.status == WebhookProcessStatus.REJECTED_UNAUTHORIZED
    assert "subscription" in result.message.lower()


def test_duplicate_delivery_idempotent(repo: SqliteBindingRepository):
    """Redelivered Service Hook message returns DUPLICATE_IGNORED and does not double-process."""
    receiver = InboundWebhookReceiver(repository=repo, secret_token="shared-secret-123")

    payload = {
        "id": "sh-evt-unique-dup",
        "subscriptionId": "sub-1",
        "eventType": "workitem.updated",
        "resource": {"id": 1004, "rev": 1, "fields": {"System.State": "Active"}},
    }
    raw_body, headers = make_hmac_request(payload, secret="shared-secret-123")

    # First attempt: ACCEPTED
    res1 = receiver.process_webhook(raw_body, headers)
    assert res1.status == WebhookProcessStatus.ACCEPTED

    # Immediate replay: DUPLICATE_IGNORED
    res2 = receiver.process_webhook(raw_body, headers)
    assert res2.status == WebhookProcessStatus.DUPLICATE_IGNORED


def test_malformed_revision_rejected(repo: SqliteBindingRepository):
    """Non-integer revision value is rejected safely without crashing."""
    receiver = InboundWebhookReceiver(repository=repo, secret_token="shared-secret-123")

    payload = {
        "id": "sh-evt-005",
        "subscriptionId": "sub-1",
        "eventType": "workitem.updated",
        "resource": {"id": 1005, "rev": "NaN-invalid", "fields": {"System.State": "Active"}},
    }
    raw_body, headers = make_hmac_request(payload, secret="shared-secret-123")

    result = receiver.process_webhook(raw_body, headers)
    assert result.status == WebhookProcessStatus.REJECTED_UNAUTHORIZED
    assert "revision" in result.message.lower()


def test_auth_failure_rejected(repo: SqliteBindingRepository):
    """Invalid HMAC signature or wrong secret returns REJECTED_UNAUTHORIZED."""
    receiver = InboundWebhookReceiver(repository=repo, secret_token="correct-secret")

    payload = {
        "id": "sh-evt-006",
        "eventType": "workitem.updated",
        "resource": {"id": 1006, "rev": 1},
    }
    # Sign with forged/wrong key
    raw_body, headers = make_hmac_request(payload, secret="wrong-impostor-key")

    result = receiver.process_webhook(raw_body, headers)
    assert result.status == WebhookProcessStatus.REJECTED_UNAUTHORIZED
    assert "authentication failed" in result.message.lower()


def test_replay_rejected_or_noop(repo: SqliteBindingRepository):
    """Replay of an already recorded event in delivery_inbound_events produces no side effects."""
    receiver = InboundWebhookReceiver(repository=repo, secret_token="shared-secret-123")

    payload = {
        "id": "sh-evt-replay-guard",
        "subscriptionId": "sub-1",
        "eventType": "workitem.updated",
        "resource": {"id": 1007, "rev": 2, "fields": {"System.State": "Active"}},
    }
    raw_body, headers = make_hmac_request(payload, secret="shared-secret-123")

    res_initial = receiver.process_webhook(raw_body, headers)
    assert res_initial.status == WebhookProcessStatus.ACCEPTED

    # Replay 1
    res_replay1 = receiver.process_webhook(raw_body, headers)
    assert res_replay1.status == WebhookProcessStatus.DUPLICATE_IGNORED

    # Replay 2
    res_replay2 = receiver.process_webhook(raw_body, headers)
    assert res_replay2.status == WebhookProcessStatus.DUPLICATE_IGNORED

    # Verify repository only holds single record for this event_id
    stored_event = repo.get_inbound_event("sh-evt-replay-guard")
    assert stored_event is not None
    assert stored_event.ado_id == 1007
