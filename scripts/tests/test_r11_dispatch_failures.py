"""Tests for Milestone R11 — Dispatch Failure Modes and Error Taxonomy.

Covers:
- Capability mismatch rejection (HostCapabilityMismatchError and UNSUPPORTED status)
- Transient host failure (DispatchRetryableError)
- Terminal host failure (DispatchTerminalError)
- Unhandled host exception (DispatchExecutionFailedError)
- Missing or non-ready envelope (DelegationEnvelopeNotReadyError)
- Event emission on dispatch failure
"""

import pytest

from scripts.domain.delegation import DelegationEnvelope
from scripts.runtime.delegation.repository import DelegationRepository
from scripts.runtime.dispatch.adapters.fake import FakeHostAdapter
from scripts.runtime.dispatch.errors import (
    DelegationEnvelopeNotReadyError,
    DispatchExecutionFailedError,
    DispatchRetryableError,
    DispatchTerminalError,
    HostCapabilityMismatchError,
)
from scripts.runtime.dispatch.host_registry import HostRegistry
from scripts.runtime.dispatch.receipts import DispatchStatus
from scripts.runtime.dispatch.repository import DispatchRepository
from scripts.runtime.dispatch.service import DispatchService


class MockEventStore:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


@pytest.fixture
def failure_setup(tmp_path):
    db_path = tmp_path / "squad.db"
    del_repo = DelegationRepository(db_path=db_path)
    disp_repo = DispatchRepository(db_path=db_path)
    registry = HostRegistry()
    adapter = FakeHostAdapter()
    registry.register_adapter("fake", adapter)
    event_store = MockEventStore()

    service = DispatchService(
        dispatch_repository=disp_repo,
        delegation_repository=del_repo,
        host_registry=registry,
        event_store=event_store,
    )
    return del_repo, disp_repo, adapter, event_store, service


def _seed(del_repo, delegation_id="DEL-FAIL-01", status="READY_FOR_DISPATCH"):
    envelope = DelegationEnvelope.create(
        delegation_id=delegation_id,
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        work_item_id="STORY-701",
        scope_summary="Fail mode test",
        action_requested="Execute",
        compiled_instruction="Do work.",
    )
    del_repo.save(
        envelope=envelope,
        activation_id="ACT-01",
        assignment_id="ASN-01",
        session_id="SESS-01",
        session_revision=1,
        project_id="proj",
        selected_agent_id="06-software-engineer",
        stage="IMPLEMENTATION",
        status=status,
        context_fingerprint="fp",
        preflight_id="PRE-01",
        preflight_decision="ALLOW",
    )
    return envelope


def test_capability_mismatch_failure(failure_setup):
    del_repo, disp_repo, adapter, event_store, service = failure_setup
    _seed(del_repo, "DEL-FAIL-01")

    adapter.reject_dispatch = True
    adapter.rejection_reason = "Context size exceeded"

    with pytest.raises(HostCapabilityMismatchError) as exc_info:
        service.dispatch_delegation("DEL-FAIL-01", host_override="fake")

    assert "Context size exceeded" in str(exc_info.value)
    assert exc_info.value.error_code == "ERR_CAPABILITY_MISMATCH"

    # Verify attempt recorded as UNSUPPORTED
    attempts = disp_repo.get_attempts_for_delegation("DEL-FAIL-01")
    assert len(attempts) == 1
    assert attempts[0]["status"] == "UNSUPPORTED"
    assert attempts[0]["error_code"] == "ERR_CAPABILITY_MISMATCH"


def test_transient_retryable_failure(failure_setup):
    del_repo, disp_repo, adapter, event_store, service = failure_setup
    _seed(del_repo, "DEL-FAIL-02")

    adapter.should_fail = True
    adapter.failure_status = DispatchStatus.FAILED_RETRYABLE
    adapter.failure_code = "ERR_HOST_SPAWN_TIMEOUT"
    adapter.failure_message = "Spawn timeout"

    with pytest.raises(DispatchRetryableError) as exc_info:
        service.dispatch_delegation("DEL-FAIL-02", host_override="fake")

    assert "Spawn timeout" in str(exc_info.value)
    assert exc_info.value.retryable is True

    # Check failure event emitted
    assert len(event_store.events) == 1
    ev = event_store.events[0]
    assert ev.event_type == "agent_squad.specialist.dispatch_failed"
    assert ev.payload["retryable"] is True


def test_terminal_failure(failure_setup):
    del_repo, disp_repo, adapter, event_store, service = failure_setup
    _seed(del_repo, "DEL-FAIL-03")

    adapter.should_fail = True
    adapter.failure_status = DispatchStatus.FAILED_TERMINAL
    adapter.failure_code = "ERR_HOST_INVOCATION_FAILED"
    adapter.failure_message = "Binary missing"

    with pytest.raises(DispatchTerminalError) as exc_info:
        service.dispatch_delegation("DEL-FAIL-03", host_override="fake")

    assert "Binary missing" in str(exc_info.value)
    assert exc_info.value.retryable is False


def test_envelope_not_ready_failure(failure_setup):
    del_repo, disp_repo, adapter, event_store, service = failure_setup
    _seed(del_repo, "DEL-FAIL-04", status="DRAFT")

    with pytest.raises(DelegationEnvelopeNotReadyError) as exc_info:
        service.dispatch_delegation("DEL-FAIL-04", host_override="fake")

    assert "is in status 'DRAFT', expected 'READY_FOR_DISPATCH'" in str(exc_info.value)
