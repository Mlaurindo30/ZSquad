"""Tests for Milestone R11 — Dispatch Idempotency and Concurrency Controls.

Covers:
- Repeated dispatch of an already dispatched delegation returns cached DispatchReceipt
- Host adapter is NOT called a second time on duplicate dispatch
- In-flight dispatch (status PENDING) triggers DispatchIdempotencyConflictError
- Monotonic attempt counter increments on retry attempts
- Terminal failure once MAX_RETRIES (3) is exceeded
"""

import hashlib
import pytest

from scripts.domain.delegation import DelegationEnvelope
from scripts.runtime.delegation.repository import DelegationRepository
from scripts.runtime.dispatch.adapters.fake import FakeHostAdapter
from scripts.runtime.dispatch.errors import (
    DispatchIdempotencyConflictError,
    DispatchRetryableError,
    DispatchTerminalError,
)
from scripts.runtime.dispatch.host_registry import HostRegistry
from scripts.runtime.dispatch.receipts import DispatchStatus
from scripts.runtime.dispatch.repository import DispatchRepository
from scripts.runtime.dispatch.service import DispatchService


@pytest.fixture
def idempotency_setup(tmp_path):
    db_path = tmp_path / "squad.db"
    del_repo = DelegationRepository(db_path=db_path)
    disp_repo = DispatchRepository(db_path=db_path)

    registry = HostRegistry()
    fake_adapter = FakeHostAdapter()
    registry.register_adapter("fake", fake_adapter)

    service = DispatchService(
        dispatch_repository=disp_repo,
        delegation_repository=del_repo,
        host_registry=registry,
    )
    return del_repo, disp_repo, fake_adapter, service


def _seed(del_repo, delegation_id="DEL-IDEM-01"):
    instruction = "Implement feature X."
    envelope = DelegationEnvelope.create(
        delegation_id=delegation_id,
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        work_item_id="STORY-301",
        scope_summary="Implement X",
        action_requested="Code",
        compiled_instruction=instruction,
    )
    del_repo.save(
        envelope=envelope,
        activation_id="ACT-IDEM-01",
        assignment_id="ASN-IDEM-01",
        session_id="SESS-IDEM-01",
        session_revision=1,
        project_id="proj-idem",
        selected_agent_id="06-software-engineer",
        stage="IMPLEMENTATION",
        status="READY_FOR_DISPATCH",
        context_fingerprint="fp-123",
        preflight_id="PRE-01",
        preflight_decision="ALLOW",
    )
    return envelope


def test_idempotent_duplicate_dispatch(idempotency_setup):
    del_repo, disp_repo, adapter, service = idempotency_setup
    _seed(del_repo, "DEL-IDEM-01")

    # First dispatch
    receipt1 = service.dispatch_delegation("DEL-IDEM-01", host_override="fake")
    assert len(adapter.dispatches) == 1

    # Second dispatch for the same delegation
    receipt2 = service.dispatch_delegation("DEL-IDEM-01", host_override="fake")

    # Verify adapter was not called again
    assert len(adapter.dispatches) == 1
    # Verify exact same receipt is returned
    assert receipt1.receipt_id == receipt2.receipt_id
    assert receipt1.instruction_hash == receipt2.instruction_hash
    assert receipt1.evidence_hash == receipt2.evidence_hash


def test_concurrent_inflight_dispatch_conflict(idempotency_setup):
    del_repo, disp_repo, adapter, service = idempotency_setup
    _seed(del_repo, "DEL-IDEM-02")

    # Pre-record an in-flight attempt with status PENDING
    disp_repo.record_attempt(
        delegation_id="DEL-IDEM-02",
        session_id="SESS-IDEM-01",
        work_item_id="STORY-301",
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        host_kind="fake",
        instruction_hash=hashlib.sha256(b"Implement feature X.").hexdigest(),
        status=DispatchStatus.PENDING,
    )

    with pytest.raises(DispatchIdempotencyConflictError) as exc_info:
        service.dispatch_delegation("DEL-IDEM-02", host_override="fake")

    assert "in-flight" in str(exc_info.value)


def test_monotonic_attempt_counter_and_retry_budget(idempotency_setup):
    del_repo, disp_repo, adapter, service = idempotency_setup
    _seed(del_repo, "DEL-IDEM-03")

    # Configure adapter to fail with retryable error
    adapter.should_fail = True
    adapter.failure_status = DispatchStatus.FAILED_RETRYABLE
    adapter.failure_code = "ERR_HOST_SPAWN_TIMEOUT"
    adapter.failure_message = "Host spawn timeout simulated"

    # Attempt 1 -> fails retryable
    with pytest.raises(DispatchRetryableError):
        service.dispatch_delegation("DEL-IDEM-03", host_override="fake")

    # Attempt 2 -> fails retryable
    with pytest.raises(DispatchRetryableError):
        service.dispatch_delegation("DEL-IDEM-03", host_override="fake")

    # Attempt 3 -> fails retryable
    with pytest.raises(DispatchRetryableError):
        service.dispatch_delegation("DEL-IDEM-03", host_override="fake")

    # Check attempt numbers
    attempts = disp_repo.get_attempts_for_delegation("DEL-IDEM-03")
    assert len(attempts) == 3
    assert [a["attempt_number"] for a in attempts] == [1, 2, 3]

    # Attempt 4 -> exceeds MAX_RETRIES (3) -> terminal error before calling adapter
    with pytest.raises(DispatchTerminalError) as exc_info:
        service.dispatch_delegation("DEL-IDEM-03", host_override="fake")

    assert "Maximum dispatch retries (3) exceeded" in str(exc_info.value)
    assert exc_info.value.error_code == "ERR_MAX_RETRIES_EXCEEDED"
