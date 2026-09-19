"""Canonical Tests for R6 Inbound Sync & Webhook Ingestion (Section 50).

Covers:
- legal remote transition
- illegal remote transition
- local state preserved on illegal remote change
- workflow drift emitted
- duplicate webhook idempotent
- stale revision ignored
- newer revision processed
- own outbound webhook becomes NOOP (Tier 1/2/3 loop suppression)
- unknown external work item blocked
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict
import pytest

from scripts.domain.lifecycle import LifecycleStage
from scripts.domain.sync import ReconciliationAction, SyncStatus
from scripts.runtime.delivery.azure_writer import AzureWriterPort
from scripts.runtime.delivery.reconciliation import ConflictReconciliationEngine
from scripts.runtime.delivery.repository import (
    ProjectBindingRecord,
    SqliteBindingRepository,
    SyncOutboxRecord,
    WorkItemBindingRecord,
)
from scripts.runtime.delivery.sync import DeliverySyncService
from scripts.runtime.delivery.webhook import (
    InboundSyncEvent,
    InboundWebhookReceiver,
    WebhookProcessStatus,
)
from scripts.runtime.events.store import SqliteEventStore


class NullWriter(AzureWriterPort):
    def create_work_item(self, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def update_work_item(self, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def create_repository(self, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def create_team(self, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def create_area(self, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def create_iteration(self, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def create_service_hook(self, **kwargs: Any) -> Dict[str, Any]:
        return {}


@pytest.fixture
def store() -> SqliteEventStore:
    return SqliteEventStore(":memory:")


@pytest.fixture
def repo() -> SqliteBindingRepository:
    repository = SqliteBindingRepository(":memory:")
    proj = ProjectBindingRecord(
        project_id="PROJ-SYNC",
        project_root="/workspace/proj",
        display_name="Sync Project",
        delivery_backend_kind="AZURE_DEVOPS",
        delivery_binding_ref="https://dev.azure.com/enterprise/CoreProject",
        binding_status="COMPLETE",
        organization_url="https://dev.azure.com/enterprise",
        team_project_name="CoreProject",
        process_template="Agile",
    )
    repository.upsert_binding(proj, changed_by="test-setup", action="INIT")
    return repository


def make_signed_request(payload: Dict[str, Any], secret: str = "test-secret") -> tuple[bytes, Dict[str, str]]:
    raw_body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={sig}",
    }
    return raw_body, headers


def test_legal_remote_transition_applied(repo: SqliteBindingRepository, store: SqliteEventStore):
    """Legal remote transition (IMPLEMENTATION -> CODE_REVIEW) evaluates to APPLY_REMOTE_TO_LOCAL."""
    # Seed work item in IMPLEMENTATION
    bound = WorkItemBindingRecord(
        work_item_id="STORY-101",
        project_id="PROJ-SYNC",
        ado_id=3001,
        remote_url="",
        remote_rev=2,
        sync_status=SyncStatus.SYNCED.value,
        sync_hash="initial-hash",
    )
    repo.upsert_work_item_binding(bound)

    sync_svc = DeliverySyncService(repository=repo, writer=NullWriter(), event_store=store)

    inbound_event = InboundSyncEvent(
        event_id="evt-legal-1",
        subscription_id="sub-1",
        event_type="workitem.updated",
        ado_id=3001,
        remote_rev=3,
        state="Active",
        board_column="Code Review",
        tags=["stage:CODE_REVIEW"],
        payload_hash="new-hash-code-review",
    )

    decision = sync_svc.process_inbound_event(
        inbound_event=inbound_event,
        project_id="PROJ-SYNC",
        current_local_stage=LifecycleStage.IMPLEMENTATION,
    )

    assert decision.action == ReconciliationAction.APPLY_REMOTE_TO_LOCAL
    # Remote revision updated in binding
    updated_bound = repo.get_work_item_binding("STORY-101")
    assert updated_bound.remote_rev == 3


def test_illegal_remote_transition_blocked(repo: SqliteBindingRepository, store: SqliteEventStore):
    """Illegal jump (IMPLEMENTATION -> DONE) is blocked fail-closed with BLOCK_ILLEGAL_REMOTE_TRANSITION."""
    bound = WorkItemBindingRecord(
        work_item_id="STORY-102",
        project_id="PROJ-SYNC",
        ado_id=3002,
        remote_url="",
        remote_rev=2,
        sync_status=SyncStatus.SYNCED.value,
        sync_hash="hash-102",
    )
    repo.upsert_work_item_binding(bound)

    sync_svc = DeliverySyncService(repository=repo, writer=NullWriter(), event_store=store)

    # Remote attempts jumping straight to Closed (DONE) without reviews/gates
    inbound_event = InboundSyncEvent(
        event_id="evt-illegal-1",
        subscription_id="sub-1",
        event_type="workitem.updated",
        ado_id=3002,
        remote_rev=3,
        state="Closed",
        board_column="Done",
        tags=["stage:DONE"],
        payload_hash="illegal-hash-done",
    )

    decision = sync_svc.process_inbound_event(
        inbound_event=inbound_event,
        project_id="PROJ-SYNC",
        current_local_stage=LifecycleStage.IMPLEMENTATION,
    )

    assert decision.action == ReconciliationAction.BLOCK_ILLEGAL_REMOTE_TRANSITION


def test_local_state_preserved_on_illegal_remote_change(repo: SqliteBindingRepository, store: SqliteEventStore):
    """Local binding remains in canonical stage when illegal remote mutation is blocked."""
    bound = WorkItemBindingRecord(
        work_item_id="STORY-103",
        project_id="PROJ-SYNC",
        ado_id=3003,
        remote_url="",
        remote_rev=2,
        sync_status=SyncStatus.SYNCED.value,
        sync_hash="hash-103",
    )
    repo.upsert_work_item_binding(bound)

    sync_svc = DeliverySyncService(repository=repo, writer=NullWriter(), event_store=store)

    inbound_event = InboundSyncEvent(
        event_id="evt-illegal-2",
        subscription_id="sub-1",
        event_type="workitem.updated",
        ado_id=3003,
        remote_rev=3,
        state="Closed",
        tags=[],
    )

    sync_svc.process_inbound_event(
        inbound_event=inbound_event,
        project_id="PROJ-SYNC",
        current_local_stage=LifecycleStage.IMPLEMENTATION,
    )

    binding = repo.get_work_item_binding("STORY-103")
    # Binding state NOT overwritten by illegal remote Closed state
    assert binding.sync_status != SyncStatus.FAILED_TERMINAL.value
    # A corrective reversion update was queued in outbox
    pending_outbox = repo.get_sync_outbox("STORY-103")
    assert pending_outbox is not None
    assert pending_outbox.operation == "UPDATE"
    payload = json.loads(pending_outbox.payload_json)
    assert "Governance Reversion" in payload.get("history_comment", "")


def test_workflow_drift_event_emitted(repo: SqliteBindingRepository, store: SqliteEventStore):
    """Illegal transition emits 'agent_squad.delivery.sync.workflow_drift_detected' DomainEvent."""
    engine = ConflictReconciliationEngine(event_store=store)

    decision = engine.evaluate(
        work_item_id="STORY-104",
        project_id="PROJ-SYNC",
        current_local_stage=LifecycleStage.INTAKE,
        remote_state="Closed",
    )

    assert decision.action == ReconciliationAction.BLOCK_ILLEGAL_REMOTE_TRANSITION

    # Check event store
    events = store.list_events_by_work_item("STORY-104")
    drift_events = [e for e in events if e.event_type == "agent_squad.delivery.sync.workflow_drift_detected"]
    assert len(drift_events) == 1
    evt = drift_events[0]
    assert evt.payload["attempted_remote_state"] == "Closed"
    assert evt.payload["canonical_local_state"] == "INTAKE"


def test_duplicate_webhook_idempotent(repo: SqliteBindingRepository):
    """Duplicate delivery of same event_id is recognized and ignored (DUPLICATE_IGNORED)."""
    receiver = InboundWebhookReceiver(repository=repo, secret_token="test-sec")

    payload = {
        "id": "evt-dup-999",
        "eventType": "workitem.updated",
        "resource": {"id": 4001, "rev": 1, "fields": {"System.State": "Active"}},
    }
    raw_body, headers = make_signed_request(payload, secret="test-sec")

    # Delivery 1: ACCEPTED
    res1 = receiver.process_webhook(raw_body, headers)
    assert res1.status == WebhookProcessStatus.ACCEPTED

    # Delivery 2: DUPLICATE_IGNORED
    res2 = receiver.process_webhook(raw_body, headers)
    assert res2.status == WebhookProcessStatus.DUPLICATE_IGNORED


def test_stale_revision_ignored(repo: SqliteBindingRepository):
    """Incoming event with rev < recorded rev is discarded as STALE_IGNORED."""
    # Seed binding at rev 5
    bound = WorkItemBindingRecord(
        work_item_id="STORY-STALE",
        project_id="PROJ-SYNC",
        ado_id=4002,
        remote_url="",
        remote_rev=5,
        sync_status=SyncStatus.SYNCED.value,
    )
    repo.upsert_work_item_binding(bound)

    receiver = InboundWebhookReceiver(repository=repo, secret_token="test-sec")

    # Event arrives with stale rev 3
    payload = {
        "id": "evt-stale-1",
        "eventType": "workitem.updated",
        "resource": {"id": 4002, "rev": 3, "fields": {"System.State": "Active"}},
    }
    raw_body, headers = make_signed_request(payload, secret="test-sec")

    res = receiver.process_webhook(raw_body, headers)
    assert res.status == WebhookProcessStatus.STALE_IGNORED


def test_newer_revision_processed(repo: SqliteBindingRepository):
    """Incoming event with rev > recorded rev is accepted for processing."""
    bound = WorkItemBindingRecord(
        work_item_id="STORY-NEWER",
        project_id="PROJ-SYNC",
        ado_id=4003,
        remote_url="",
        remote_rev=2,
        sync_status=SyncStatus.SYNCED.value,
    )
    repo.upsert_work_item_binding(bound)

    receiver = InboundWebhookReceiver(repository=repo, secret_token="test-sec")

    payload = {
        "id": "evt-newer-1",
        "eventType": "workitem.updated",
        "resource": {"id": 4003, "rev": 3, "fields": {"System.State": "Active"}},
    }
    raw_body, headers = make_signed_request(payload, secret="test-sec")

    res = receiver.process_webhook(raw_body, headers)
    assert res.status == WebhookProcessStatus.ACCEPTED


def test_own_outbound_webhook_becomes_noop(repo: SqliteBindingRepository, store: SqliteEventStore):
    """Tier 1/2/3 loop suppression guards discard our own outbound reflection as NOOP."""
    # Seed item at IMPLEMENTATION with hash and completed outbox
    sync_svc = DeliverySyncService(repository=repo, writer=NullWriter(), event_store=store)

    bound = WorkItemBindingRecord(
        work_item_id="STORY-ECHO",
        project_id="PROJ-SYNC",
        ado_id=4004,
        remote_url="",
        remote_rev=4,
        sync_status=SyncStatus.SYNCED.value,
        sync_hash="echo-hash-4004",
    )
    repo.upsert_work_item_binding(bound)

    # Simulate completed outbox record at rev 4
    outbox = SyncOutboxRecord(
        outbox_id="out-echo-1",
        work_item_id="STORY-ECHO",
        project_id="PROJ-SYNC",
        operation="UPDATE",
        payload_json="{}",
        status="COMPLETED",
        expected_rev=4,
        correlation_id="corr-echo-test",
    )
    repo.enqueue_sync_outbox(outbox)

    # 1. Tier 1 Echo: Remote rev matches recently completed outbox
    event_tier1 = InboundSyncEvent(
        event_id="evt-echo-1",
        subscription_id="sub-1",
        event_type="workitem.updated",
        ado_id=4004,
        remote_rev=4,
        state="Active",
        board_column="Implementation",
    )
    decision1 = sync_svc.process_inbound_event(
        inbound_event=event_tier1,
        project_id="PROJ-SYNC",
        current_local_stage=LifecycleStage.IMPLEMENTATION,
    )
    assert decision1.action == ReconciliationAction.NOOP
    assert "Echo" in decision1.reason

    # 2. Tier 2 Echo: Payload hash matches last sync hash
    event_tier2 = InboundSyncEvent(
        event_id="evt-echo-2",
        subscription_id="sub-1",
        event_type="workitem.updated",
        ado_id=4004,
        remote_rev=5,
        state="Active",
        board_column="Implementation",
        payload_hash="echo-hash-4004",
    )
    decision2 = sync_svc.process_inbound_event(
        inbound_event=event_tier2,
        project_id="PROJ-SYNC",
        current_local_stage=LifecycleStage.IMPLEMENTATION,
    )
    assert decision2.action == ReconciliationAction.NOOP
    assert "Tier 2" in decision2.reason


def test_unknown_external_work_item_blocked(repo: SqliteBindingRepository, store: SqliteEventStore):
    """Incoming event for an unbound external ADO ID does not mutate any local work items."""
    sync_svc = DeliverySyncService(repository=repo, writer=NullWriter(), event_store=store)

    inbound_event = InboundSyncEvent(
        event_id="evt-unknown-1",
        subscription_id="sub-1",
        event_type="workitem.updated",
        ado_id=99999,  # Unbound ID
        remote_rev=1,
        state="Active",
    )

    decision = sync_svc.process_inbound_event(
        inbound_event=inbound_event,
        project_id="PROJ-SYNC",
        current_local_stage=LifecycleStage.INTAKE,
    )

    # Treated safely without crashing or corrupting local entities
    assert decision.work_item_id == "ADO-99999"
    assert repo.get_work_item_binding_by_ado_id(99999) is None
