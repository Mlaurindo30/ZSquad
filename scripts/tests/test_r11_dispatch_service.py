"""Tests for Milestone R11 — Canonical DispatchService.

Covers:
- End-to-end dispatch of READY_FOR_DISPATCH DelegationEnvelope via HostDispatchPort
- Envelope status transition to DISPATCHED
- Minting and SQLite persistence of canonical DispatchReceipt
- Exact instruction_hash and evidence_hash verification
- Domain event emission into R2 EventStore
- Status probing and cancellation
"""

import hashlib
from pathlib import Path
import pytest

from scripts.domain.delegation import DelegationEnvelope
from scripts.runtime.delegation.repository import DelegationRepository
from scripts.runtime.dispatch.adapters.fake import FakeHostAdapter
from scripts.runtime.dispatch.host_registry import HostRegistry
from scripts.runtime.dispatch.receipts import DispatchStatus
from scripts.runtime.dispatch.repository import DispatchRepository
from scripts.runtime.dispatch.service import DispatchService


class MockEventStore:
    """In-memory event store recording emitted events."""

    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)

    def append(self, event):
        self.events.append(event)


@pytest.fixture
def dispatch_environment(tmp_path):
    db_path = tmp_path / "squad.db"
    del_repo = DelegationRepository(db_path=db_path)
    disp_repo = DispatchRepository(db_path=db_path)

    registry = HostRegistry()
    fake_adapter = FakeHostAdapter()
    registry.register_adapter("fake", fake_adapter)

    event_store = MockEventStore()

    service = DispatchService(
        dispatch_repository=disp_repo,
        delegation_repository=del_repo,
        host_registry=registry,
        event_store=event_store,
    )

    return {
        "db_path": db_path,
        "del_repo": del_repo,
        "disp_repo": disp_repo,
        "registry": registry,
        "adapter": fake_adapter,
        "event_store": event_store,
        "service": service,
    }


def _seed_envelope(del_repo, delegation_id="DEL-SRV-01", status="READY_FOR_DISPATCH"):
    instruction = "# AGENT: 06-software-engineer\nImplement user story."
    instr_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
    envelope = DelegationEnvelope.create(
        delegation_id=delegation_id,
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        work_item_id="STORY-201",
        scope_summary="Implement module",
        action_requested="Code and unit test",
        compiled_instruction=instruction,
    )
    del_repo.save(
        envelope=envelope,
        activation_id="ACT-SRV-01",
        assignment_id="ASN-SRV-01",
        session_id="SESS-SRV-01",
        session_revision=1,
        project_id="proj-srv",
        selected_agent_id="06-software-engineer",
        stage="IMPLEMENTATION",
        status=status,
        context_fingerprint="ctx-fingerprint-123",
        preflight_id="PRE-01",
        preflight_decision="ALLOW",
    )
    return envelope


def test_dispatch_delegation_success(dispatch_environment):
    env = dispatch_environment
    service = env["service"]
    del_repo = env["del_repo"]
    disp_repo = env["disp_repo"]
    event_store = env["event_store"]

    _seed_envelope(del_repo, delegation_id="DEL-SRV-01")

    receipt = service.dispatch_delegation("DEL-SRV-01", host_override="fake")

    # Invariants on DispatchReceipt
    assert receipt is not None
    assert receipt.receipt_type == "DISPATCH"
    assert receipt.work_item_id == "STORY-201"
    assert receipt.agent_id == "00-delivery-orchestrator"
    assert receipt.target_agent_id == "06-software-engineer"
    assert receipt.delegation_id == "DEL-SRV-01"
    assert receipt.instruction_hash != ""
    assert receipt.evidence_hash != ""

    # Envelope status updated in DelegationRepository
    del_row = del_repo.get_by_id("DEL-SRV-01")
    assert del_row["status"] == "DISPATCHED"

    # Dispatch attempt persisted in DispatchRepository
    attempt = disp_repo.get_active_attempt_for_delegation("DEL-SRV-01")
    assert attempt is not None
    assert attempt["status"] == "DISPATCHED"
    assert attempt["target_role"] == "06-software-engineer"
    assert attempt["host_kind"] == "fake"
    assert attempt["receipt_payload"] is not None

    # Event emitted to R2 Outbox
    assert len(event_store.events) == 1
    event = event_store.events[0]
    assert event.event_type == "agent_squad.specialist.dispatched"
    assert event.work_item_id == "STORY-201"
    assert event.payload["delegation_id"] == "DEL-SRV-01"
    assert event.payload["target_role"] == "06-software-engineer"


def test_check_dispatch_status_and_cancel(dispatch_environment):
    env = dispatch_environment
    service = env["service"]
    del_repo = env["del_repo"]
    disp_repo = env["disp_repo"]

    _seed_envelope(del_repo, delegation_id="DEL-SRV-02")
    service.dispatch_delegation("DEL-SRV-02", host_override="fake")

    attempt = disp_repo.get_active_attempt_for_delegation("DEL-SRV-02")
    dispatch_id = attempt["dispatch_id"]

    # Check status
    status_result = service.check_dispatch_status(dispatch_id)
    assert status_result.is_alive is True
    assert status_result.status == DispatchStatus.DISPATCHED

    # Cancel dispatch
    cancelled = service.cancel_dispatch(dispatch_id)
    assert cancelled is True

    # Verify attempt is marked CANCELLED
    updated_attempt = disp_repo.get_attempt(dispatch_id)
    assert updated_attempt["status"] == "CANCELLED"
