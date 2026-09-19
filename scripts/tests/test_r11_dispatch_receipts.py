"""Tests for Milestone R11 — Dispatch Receipts and Evidence Hashing.

Covers:
- Deterministic SHA-256 evidence hashing
- Minting canonical DispatchReceipt bound to DelegationEnvelope and HostDispatchResult
- Receipt integrity validation (tampering detection)
- Retrieval of persisted receipts from SQLite
"""

import hashlib
from pathlib import Path
import pytest

from scripts.domain.delegation import DelegationEnvelope
from scripts.runtime.delegation.repository import DelegationRepository
from scripts.runtime.dispatch.adapters.fake import FakeHostAdapter
from scripts.runtime.dispatch.errors import DispatchReceiptIntegrityError
from scripts.runtime.dispatch.host_registry import HostRegistry
from scripts.runtime.dispatch.receipts import (
    DispatchStatus,
    HostDispatchResult,
    compute_evidence_hash,
    mint_dispatch_receipt,
)
from scripts.runtime.dispatch.repository import DispatchRepository
from scripts.runtime.dispatch.service import DispatchService


def test_compute_evidence_hash_determinism():
    payload1 = {"b": 2, "a": 1, "c": [3, 4]}
    payload2 = {"a": 1, "c": [3, 4], "b": 2}
    # canonical_json sorts keys, ensuring deterministic hash regardless of dict insertion order
    h1 = compute_evidence_hash(payload1)
    h2 = compute_evidence_hash(payload2)
    assert h1 == h2
    assert len(h1) == 64


def test_mint_dispatch_receipt_invariants():
    instruction = "Write tests"
    envelope = DelegationEnvelope.create(
        delegation_id="DEL-RCP-01",
        sender_role="00-delivery-orchestrator",
        target_role="11-test-engineer",
        work_item_id="STORY-501",
        scope_summary="Testing",
        action_requested="Test suite",
        compiled_instruction=instruction,
    )

    result = HostDispatchResult(
        status=DispatchStatus.DISPATCHED,
        host_execution_id="exec-123",
        evidence_payload={"pid": 9999, "status": "running"},
    )

    receipt = mint_dispatch_receipt(envelope, result)
    assert receipt.receipt_type == "DISPATCH"
    assert receipt.work_item_id == "STORY-501"
    assert receipt.agent_id == "00-delivery-orchestrator"
    assert receipt.target_agent_id == "11-test-engineer"
    assert receipt.delegation_id == "DEL-RCP-01"
    assert receipt.instruction_hash == envelope.instruction_hash
    assert receipt.evidence_hash == compute_evidence_hash(result.evidence_payload)


def test_instruction_hash_tampering_rejection(tmp_path):
    db_path = tmp_path / "squad.db"
    del_repo = DelegationRepository(db_path=db_path)
    disp_repo = DispatchRepository(db_path=db_path)
    registry = HostRegistry()
    registry.register_adapter("fake", FakeHostAdapter())

    service = DispatchService(
        dispatch_repository=disp_repo,
        delegation_repository=del_repo,
        host_registry=registry,
    )

    # Construct envelope with tampered instruction hash (or tampered instruction string)
    instruction = "Original instruction"
    valid_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()

    envelope = DelegationEnvelope.create(
        delegation_id="DEL-TAMP-01",
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        work_item_id="STORY-502",
        scope_summary="Test tampering",
        action_requested="Code",
        compiled_instruction=instruction,
    )

    # Save to repo
    del_repo.save(
        envelope=envelope,
        activation_id="ACT-TAMP-01",
        assignment_id="ASN-TAMP-01",
        session_id="SESS-01",
        session_revision=1,
        project_id="proj",
        selected_agent_id="06-software-engineer",
        stage="IMPLEMENTATION",
        status="READY_FOR_DISPATCH",
        context_fingerprint="fp",
        preflight_id="PRE-01",
        preflight_decision="ALLOW",
    )

    # Simulate out-of-band tampering in database row: alter envelope_payload instruction
    with del_repo._get_connection() as conn:
        import json
        cur = conn.execute("SELECT envelope_payload FROM delegation_envelopes WHERE delegation_id = 'DEL-TAMP-01'")
        data = json.loads(cur.fetchone()["envelope_payload"])
        data["compiled_instruction"] = "Tampered malicious instruction!"
        conn.execute(
            "UPDATE delegation_envelopes SET envelope_payload = ? WHERE delegation_id = 'DEL-TAMP-01'",
            (json.dumps(data),),
        )
        conn.commit()

    with pytest.raises(DispatchReceiptIntegrityError) as exc_info:
        service.dispatch_delegation("DEL-TAMP-01", host_override="fake")

    assert "Instruction hash tampering" in str(exc_info.value)
    assert exc_info.value.error_code == "ERR_INSTRUCTION_HASH_MISMATCH"
