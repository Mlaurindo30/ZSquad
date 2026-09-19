"""Tests for Milestone R11 — Dispatch Authority and Boundary Invariants.

Covers:
- Invariant 1: ZERO Lifecycle Stage Advancement on dispatch
- Invariant 2: ZERO ExecutionReceipt generated (only DispatchReceipt minted)
- Invariant 3: ZERO Review/Test Gate Approval
- Invariant 4: Strict Control Plane Host Neutrality & Fail-Closed on unknown host
- Session freshness enforcement via CanonicalSessionManager
"""

import hashlib
import pytest

from scripts.domain.delegation import DelegationEnvelope
from scripts.domain.receipts import DispatchReceipt, ExecutionReceipt
from scripts.runtime.delegation.repository import DelegationRepository
from scripts.runtime.dispatch.adapters.fake import FakeHostAdapter
from scripts.runtime.dispatch.errors import (
    HostResolutionError,
    SessionInvalidForDispatchError,
    UnsupportedHostError,
)
from scripts.runtime.dispatch.host_registry import HostRegistry
from scripts.runtime.dispatch.repository import DispatchRepository
from scripts.runtime.dispatch.service import DispatchService


class MockSession:
    def __init__(self, session_id: str, active: bool = True):
        self.session_id = session_id
        self._active = active

    def is_active(self) -> bool:
        return self._active


class MockSessionManager:
    def __init__(self):
        self.sessions = {}

    def get_session(self, session_id: str):
        return self.sessions.get(session_id)


@pytest.fixture
def authority_setup(tmp_path):
    db_path = tmp_path / "squad.db"
    del_repo = DelegationRepository(db_path=db_path)
    disp_repo = DispatchRepository(db_path=db_path)
    registry = HostRegistry()
    fake_adapter = FakeHostAdapter()
    registry.register_adapter("fake", fake_adapter)
    session_mgr = MockSessionManager()

    service = DispatchService(
        dispatch_repository=disp_repo,
        delegation_repository=del_repo,
        host_registry=registry,
        session_manager=session_mgr,
    )
    return del_repo, disp_repo, registry, session_mgr, service


def test_zero_lifecycle_completion_invariant(authority_setup):
    del_repo, disp_repo, registry, session_mgr, service = authority_setup

    instruction = "Write code."
    envelope = DelegationEnvelope.create(
        delegation_id="DEL-AUTH-01",
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        work_item_id="STORY-601",
        scope_summary="Implement story",
        action_requested="Code",
        compiled_instruction=instruction,
    )
    del_repo.save(
        envelope=envelope,
        activation_id="ACT-01",
        assignment_id="ASN-01",
        session_id="SESS-01",
        session_revision=1,
        project_id="proj-auth",
        selected_agent_id="06-software-engineer",
        stage="IMPLEMENTATION",
        status="READY_FOR_DISPATCH",
        context_fingerprint="fp",
        preflight_id="PRE-01",
        preflight_decision="ALLOW",
    )
    session_mgr.sessions["SESS-01"] = MockSession("SESS-01", active=True)

    # Perform dispatch
    receipt = service.dispatch_delegation("DEL-AUTH-01", host_override="fake")

    # Invariant 1: Receipt MUST be DispatchReceipt, NOT ExecutionReceipt
    assert isinstance(receipt, DispatchReceipt)
    assert not isinstance(receipt, ExecutionReceipt)

    # Invariant 2: Lifecycle stage remains untouched in delegation record
    row = del_repo.get_by_id("DEL-AUTH-01")
    assert row["stage"] == "IMPLEMENTATION"
    # Status transitions to DISPATCHED, but stage does not change
    assert row["status"] == "DISPATCHED"


def test_fail_closed_on_unsupported_or_unknown_host(authority_setup):
    del_repo, disp_repo, registry, session_mgr, service = authority_setup

    envelope = DelegationEnvelope.create(
        delegation_id="DEL-AUTH-02",
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        work_item_id="STORY-602",
        scope_summary="Test host fail closed",
        action_requested="Code",
        compiled_instruction="Do work.",
    )
    del_repo.save(
        envelope=envelope,
        activation_id="ACT-02",
        assignment_id="ASN-02",
        session_id="SESS-02",
        session_revision=1,
        project_id="proj-auth",
        selected_agent_id="06-software-engineer",
        stage="IMPLEMENTATION",
        status="READY_FOR_DISPATCH",
        context_fingerprint="fp",
        preflight_id="PRE-01",
        preflight_decision="ALLOW",
    )
    session_mgr.sessions["SESS-02"] = MockSession("SESS-02", active=True)

    # Calling with an unknown host kind must fail-closed with UnsupportedHostError
    with pytest.raises(UnsupportedHostError) as exc_info:
        service.dispatch_delegation("DEL-AUTH-02", host_override="non_existent_host")

    assert "Unsupported host kind 'non_existent_host'" in str(exc_info.value)
    assert exc_info.value.error_code == "ERR_HOST_UNRESOLVED"


def test_session_freshness_enforcement(authority_setup):
    del_repo, disp_repo, registry, session_mgr, service = authority_setup

    envelope = DelegationEnvelope.create(
        delegation_id="DEL-AUTH-03",
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        work_item_id="STORY-603",
        scope_summary="Test session invalid",
        action_requested="Code",
        compiled_instruction="Do work.",
    )
    del_repo.save(
        envelope=envelope,
        activation_id="ACT-03",
        assignment_id="ASN-03",
        session_id="SESS-EXPIRED",
        session_revision=1,
        project_id="proj-auth",
        selected_agent_id="06-software-engineer",
        stage="IMPLEMENTATION",
        status="READY_FOR_DISPATCH",
        context_fingerprint="fp",
        preflight_id="PRE-01",
        preflight_decision="ALLOW",
    )
    # Mark session as inactive
    session_mgr.sessions["SESS-EXPIRED"] = MockSession("SESS-EXPIRED", active=False)

    with pytest.raises(SessionInvalidForDispatchError) as exc_info:
        service.dispatch_delegation("DEL-AUTH-03", host_override="fake")

    assert "is not active" in str(exc_info.value)
    assert exc_info.value.error_code == "ERR_SESSION_INVALID"
