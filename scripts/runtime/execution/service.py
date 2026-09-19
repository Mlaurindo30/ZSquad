"""Canonical Execution and Validation Receipt Service.

Strictly stdlib-only. Sole authoritative service for execution receipts and SoD.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from scripts.domain.common import canonical_json
from scripts.domain.lifecycle import GateId, LifecycleStage
from scripts.domain.receipts import (
    BaseReceipt,
    ExecutionReceipt,
    GovernanceReceipt,
    QAReceipt,
    ReceiptType,
    ReviewReceipt,
    SecurityReceipt,
    TestReceipt,
)
from scripts.runtime.events.store import SqliteEventStore
from scripts.runtime.execution.errors import (
    ExecutionReceiptError,
    GatePrerequisiteError,
    InvalidEvidenceError,
    SoDViolationError,
    StageIneligibleError,
)
from scripts.runtime.execution.evidence import hash_evidence_payload
from scripts.runtime.execution.repository import ExecutionReceiptRepository
from scripts.runtime.execution.stage_policy import (
    STAGE_RECEIPT_REQUIREMENTS,
    StageReceiptRequirement,
    get_stage_receipt_requirement,
)
from scripts.runtime.execution.validation import (
    normalize_logical_agent_id,
    validate_execution_receipt,
    validate_governance_receipt,
    validate_qa_receipt,
    validate_review_receipt,
    validate_security_receipt,
    validate_sod,
    validate_test_receipt,
)
from scripts.runtime.lifecycle.gates import GATE_TO_STAGE_MAP, normalize_gate_id
from scripts.runtime.lifecycle.policies import normalize_stage


class ExecutionReceiptService:
    """Canonical service for recording, validating, and verifying execution receipts."""

    def __init__(
        self,
        repository: Optional[ExecutionReceiptRepository] = None,
        event_store: Optional[SqliteEventStore] = None,
        db_path: Optional[Union[str, Path, sqlite3.Connection]] = None,
    ) -> None:
        self.repository = repository or ExecutionReceiptRepository(db_path)
        self.event_store = event_store

    def _assert_stage_binding(
        self,
        work_item_id: str,
        expected_stage: LifecycleStage,
        passed_stage: LifecycleStage,
        receipt_name: str,
    ) -> None:
        if passed_stage != expected_stage:
            raise InvalidEvidenceError(
                f"{receipt_name} must be recorded for stage '{expected_stage.value}', got '{passed_stage.value}'"
            )
        current_stage_str = self.repository.get_work_item_current_stage(work_item_id)
        if current_stage_str:
            current_stage_norm = normalize_stage(current_stage_str)
            if current_stage_norm != expected_stage:
                raise StageIneligibleError(
                    f"Work item '{work_item_id}' is currently in stage '{current_stage_norm.value}'. "
                    f"Cannot record {receipt_name} which belongs exclusively to stage '{expected_stage.value}'."
                )

    def record_execution(
        self,
        work_item_id: str,
        project_id: str,
        agent_id: str,
        stage: Union[str, LifecycleStage],
        instruction_hash: str,
        evidence_hash: str,
        files_modified: List[str],
        tests_executed: List[str],
        test_exit_code: int,
        diff_summary: str,
        receipt_id: Optional[str] = None,
    ) -> ExecutionReceipt:
        canonical_stage = normalize_stage(stage)
        self._assert_stage_binding(work_item_id, LifecycleStage.IMPLEMENTATION, canonical_stage, "ExecutionReceipt")
        r_id = receipt_id or f"rcpt-exec-{uuid.uuid4().hex[:12]}"

        receipt = ExecutionReceipt(
            receipt_id=r_id,
            receipt_type=ReceiptType.EXECUTION.value,
            work_item_id=work_item_id,
            agent_id=agent_id,
            instruction_hash=instruction_hash,
            evidence_hash=evidence_hash,
            files_modified=files_modified,
            tests_executed=tests_executed,
            test_exit_code=test_exit_code,
            diff_summary=diff_summary,
        )

        validate_execution_receipt(receipt)
        self.repository.save_execution_receipt(receipt, project_id, canonical_stage.value)
        self._emit_event("agent_squad.execution.receipt_recorded", receipt, project_id)
        return receipt

    def record_review(
        self,
        work_item_id: str,
        project_id: str,
        agent_id: str,
        stage: Union[str, LifecycleStage],
        instruction_hash: str,
        evidence_hash: str,
        reviewer_role: str,
        verdict: str = "APPROVED",
        comments: Optional[List[str]] = None,
        reviewed_files: Optional[List[str]] = None,
        receipt_id: Optional[str] = None,
    ) -> ReviewReceipt:
        canonical_stage = normalize_stage(stage)
        self._assert_stage_binding(work_item_id, LifecycleStage.CODE_REVIEW, canonical_stage, "ReviewReceipt")
        r_id = receipt_id or f"rcpt-rev-{uuid.uuid4().hex[:12]}"

        receipt = ReviewReceipt(
            receipt_id=r_id,
            receipt_type=ReceiptType.REVIEW.value,
            work_item_id=work_item_id,
            agent_id=agent_id,
            instruction_hash=instruction_hash,
            evidence_hash=evidence_hash,
            reviewer_role=reviewer_role,
            verdict=verdict,
            comments=comments or [],
            reviewed_files=reviewed_files or [],
        )

        exec_receipt = self.repository.get_latest_execution_receipt(work_item_id)
        validate_review_receipt(receipt, exec_receipt)

        details = {
            "reviewer_role": reviewer_role,
            "verdict": verdict,
            "comments": comments or [],
            "reviewed_files": reviewed_files or [],
        }
        self.repository.save_validation_receipt(
            receipt=receipt,
            project_id=project_id,
            stage=canonical_stage.value,
            role=reviewer_role,
            verdict=verdict,
            details=details,
        )
        self._emit_event("agent_squad.review.receipt_recorded", receipt, project_id)
        return receipt

    def record_security(
        self,
        work_item_id: str,
        project_id: str,
        agent_id: str,
        stage: Union[str, LifecycleStage],
        instruction_hash: str,
        evidence_hash: str,
        security_role: str,
        vulnerabilities_detected: int = 0,
        critical_count: int = 0,
        sast_tool_output: str = "",
        verdict: str = "APPROVED",
        receipt_id: Optional[str] = None,
    ) -> SecurityReceipt:
        canonical_stage = normalize_stage(stage)
        self._assert_stage_binding(work_item_id, LifecycleStage.SECURITY_REVIEW, canonical_stage, "SecurityReceipt")
        r_id = receipt_id or f"rcpt-sec-{uuid.uuid4().hex[:12]}"

        receipt = SecurityReceipt(
            receipt_id=r_id,
            receipt_type=ReceiptType.SECURITY.value,
            work_item_id=work_item_id,
            agent_id=agent_id,
            instruction_hash=instruction_hash,
            evidence_hash=evidence_hash,
            security_role=security_role,
            vulnerabilities_detected=vulnerabilities_detected,
            critical_count=critical_count,
            sast_tool_output=sast_tool_output,
            verdict=verdict,
        )

        exec_receipt = self.repository.get_latest_execution_receipt(work_item_id)
        validate_security_receipt(receipt, exec_receipt)

        details = {
            "security_role": security_role,
            "vulnerabilities_detected": vulnerabilities_detected,
            "critical_count": critical_count,
            "sast_tool_output": sast_tool_output,
            "verdict": verdict,
        }
        self.repository.save_validation_receipt(
            receipt=receipt,
            project_id=project_id,
            stage=canonical_stage.value,
            role=security_role,
            verdict=verdict,
            details=details,
        )
        self._emit_event("agent_squad.security.receipt_recorded", receipt, project_id)
        return receipt

    def record_test(
        self,
        work_item_id: str,
        project_id: str,
        agent_id: str,
        stage: Union[str, LifecycleStage],
        instruction_hash: str,
        evidence_hash: str,
        tester_role: str,
        total_tests: int,
        passed_tests: int,
        failed_tests: int = 0,
        coverage_percentage: float = 0.0,
        receipt_id: Optional[str] = None,
    ) -> TestReceipt:
        canonical_stage = normalize_stage(stage)
        self._assert_stage_binding(work_item_id, LifecycleStage.TEST_VALIDATION, canonical_stage, "TestReceipt")
        r_id = receipt_id or f"rcpt-tst-{uuid.uuid4().hex[:12]}"

        receipt = TestReceipt(
            receipt_id=r_id,
            receipt_type=ReceiptType.TEST.value,
            work_item_id=work_item_id,
            agent_id=agent_id,
            instruction_hash=instruction_hash,
            evidence_hash=evidence_hash,
            tester_role=tester_role,
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            coverage_percentage=coverage_percentage,
        )

        exec_receipt = self.repository.get_latest_execution_receipt(work_item_id)
        validate_test_receipt(receipt, exec_receipt)

        details = {
            "tester_role": tester_role,
            "total_tests": total_tests,
            "passed_tests": passed_tests,
            "failed_tests": failed_tests,
            "coverage_percentage": coverage_percentage,
        }
        self.repository.save_validation_receipt(
            receipt=receipt,
            project_id=project_id,
            stage=canonical_stage.value,
            role=tester_role,
            verdict="APPROVED" if failed_tests == 0 else "REJECTED",
            details=details,
        )
        self._emit_event("agent_squad.test.receipt_recorded", receipt, project_id)
        return receipt

    def record_qa(
        self,
        work_item_id: str,
        project_id: str,
        agent_id: str,
        stage: Union[str, LifecycleStage],
        instruction_hash: str,
        evidence_hash: str,
        qa_role: str,
        scenarios_verified: int,
        bdd_exit_code: int = 0,
        verdict: str = "APPROVED",
        receipt_id: Optional[str] = None,
    ) -> QAReceipt:
        canonical_stage = normalize_stage(stage)
        self._assert_stage_binding(work_item_id, LifecycleStage.QA_VALIDATION, canonical_stage, "QAReceipt")
        r_id = receipt_id or f"rcpt-qa-{uuid.uuid4().hex[:12]}"

        receipt = QAReceipt(
            receipt_id=r_id,
            receipt_type=ReceiptType.QA.value,
            work_item_id=work_item_id,
            agent_id=agent_id,
            instruction_hash=instruction_hash,
            evidence_hash=evidence_hash,
            qa_role=qa_role,
            scenarios_verified=scenarios_verified,
            bdd_exit_code=bdd_exit_code,
            verdict=verdict,
        )

        exec_receipt = self.repository.get_latest_execution_receipt(work_item_id)
        validate_qa_receipt(receipt, exec_receipt)

        details = {
            "qa_role": qa_role,
            "scenarios_verified": scenarios_verified,
            "bdd_exit_code": bdd_exit_code,
            "verdict": verdict,
        }
        self.repository.save_validation_receipt(
            receipt=receipt,
            project_id=project_id,
            stage=canonical_stage.value,
            role=qa_role,
            verdict=verdict,
            details=details,
        )
        self._emit_event("agent_squad.qa.receipt_recorded", receipt, project_id)
        return receipt

    def record_governance(
        self,
        work_item_id: str,
        project_id: str,
        agent_id: str,
        stage: Union[str, LifecycleStage],
        instruction_hash: str,
        evidence_hash: str,
        auditor_role: str,
        ledger_entry_id: str,
        gate_approvals: Optional[List[str]] = None,
        compliance_verdict: str = "COMPLIANT",
        receipt_id: Optional[str] = None,
    ) -> GovernanceReceipt:
        canonical_stage = normalize_stage(stage)
        self._assert_stage_binding(work_item_id, LifecycleStage.GOVERNANCE_RELEASE, canonical_stage, "GovernanceReceipt")
        r_id = receipt_id or f"rcpt-gov-{uuid.uuid4().hex[:12]}"

        receipt = GovernanceReceipt(
            receipt_id=r_id,
            receipt_type=ReceiptType.GOVERNANCE.value,
            work_item_id=work_item_id,
            agent_id=agent_id,
            instruction_hash=instruction_hash,
            evidence_hash=evidence_hash,
            auditor_role=auditor_role,
            gate_approvals=gate_approvals or [],
            ledger_entry_id=ledger_entry_id,
            compliance_verdict=compliance_verdict,
        )

        exec_receipt = self.repository.get_latest_execution_receipt(work_item_id)
        validate_governance_receipt(receipt, exec_receipt)

        details = {
            "auditor_role": auditor_role,
            "gate_approvals": gate_approvals or [],
            "ledger_entry_id": ledger_entry_id,
            "compliance_verdict": compliance_verdict,
        }
        self.repository.save_validation_receipt(
            receipt=receipt,
            project_id=project_id,
            stage=canonical_stage.value,
            role=auditor_role,
            verdict=compliance_verdict,
            details=details,
        )
        self._emit_event("agent_squad.governance.receipt_recorded", receipt, project_id)
        return receipt

    def is_stage_satisfied(
        self,
        work_item_id: str,
        stage: Union[str, LifecycleStage],
        security_required: bool = True,
    ) -> Tuple[bool, str]:
        """Checks whether all required execution/validation receipts for stage exist and are valid."""
        canonical_stage = normalize_stage(stage)
        if canonical_stage == LifecycleStage.SECURITY_REVIEW and not security_required:
            return True, "Security review explicitly not required by policy"

        req = get_stage_receipt_requirement(canonical_stage)
        if not req:
            return True, "No receipt requirements configured for stage"

        exec_receipt = self.repository.get_latest_execution_receipt(work_item_id)

        for r_type in req.required_receipt_types:
            if r_type == ReceiptType.EXECUTION:
                if not exec_receipt:
                    return False, f"Missing ExecutionReceipt for work item {work_item_id}"
                if exec_receipt.test_exit_code != 0:
                    return False, f"Execution tests failed with exit code {exec_receipt.test_exit_code}"
            else:
                receipts = self.repository.get_validation_receipts(
                    work_item_id=work_item_id,
                    receipt_type=r_type,
                )
                if not receipts:
                    if r_type == ReceiptType.SECURITY and not security_required:
                        continue
                    return False, f"Missing {r_type.value} receipt for work item {work_item_id}"
                # Check that latest validation is approved
                latest = receipts[0]
                allowed_verdicts = ("APPROVED", "COMPLIANT")
                if r_type == ReceiptType.SECURITY and not security_required:
                    allowed_verdicts = ("APPROVED", "COMPLIANT", "NOT_REQUIRED")
                if latest["verdict"] not in allowed_verdicts:
                    return False, f"{r_type.value} receipt not approved: verdict '{latest['verdict']}'"
                # Check SoD using normalized logical agent ID
                if exec_receipt:
                    exec_logical = normalize_logical_agent_id(exec_receipt.agent_id)
                    val_logical = normalize_logical_agent_id(latest["agent_id"])
                    if exec_logical == val_logical:
                        return False, f"SoD violation: {latest['agent_id']} (logical: {val_logical}) cannot validate their own implementation"

        return True, "All receipt requirements satisfied"

    def verify_governance_chain(
        self,
        work_item_id: str,
        is_security_required: bool = True,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Verifies that the entire required governance evidence chain is complete, valid, and SoD compliant."""
        chain: Dict[str, Any] = {
            "work_item_id": work_item_id,
            "implementation": None,
            "code_review": None,
            "security_review": None,
            "test_validation": None,
            "qa_validation": None,
            "status": "INCOMPLETE",
        }

        # 1. Implementation
        exec_receipt = self.repository.get_latest_execution_receipt(work_item_id)
        if not exec_receipt:
            return False, f"Governance chain incomplete: Missing ExecutionReceipt for {work_item_id}", chain
        if exec_receipt.test_exit_code != 0:
            return False, f"Governance chain incomplete: Implementation test exit code is {exec_receipt.test_exit_code}", chain
        chain["implementation"] = {
            "receipt_id": exec_receipt.receipt_id,
            "agent_id": exec_receipt.agent_id,
            "status": "VALID",
        }

        exec_logical = normalize_logical_agent_id(exec_receipt.agent_id)

        # 2. Code Review
        reviews = self.repository.get_validation_receipts(work_item_id, ReceiptType.REVIEW)
        if not reviews:
            return False, f"Governance chain incomplete: Missing ReviewReceipt for {work_item_id}", chain
        latest_rev = reviews[0]
        if latest_rev["verdict"] != "APPROVED":
            return False, f"Governance chain incomplete: Review not APPROVED (verdict: {latest_rev['verdict']})", chain
        if normalize_logical_agent_id(latest_rev["agent_id"]) == exec_logical:
            return False, f"Governance chain incomplete: SoD violation on review by {latest_rev['agent_id']}", chain
        chain["code_review"] = {
            "receipt_id": latest_rev["receipt_id"],
            "agent_id": latest_rev["agent_id"],
            "status": "APPROVED",
        }

        # 3. Security Review (conditional)
        if is_security_required:
            secs = self.repository.get_validation_receipts(work_item_id, ReceiptType.SECURITY)
            if not secs:
                return False, f"Governance chain incomplete: Missing required SecurityReceipt for {work_item_id}", chain
            latest_sec = secs[0]
            if latest_sec["verdict"] != "APPROVED":
                return False, f"Governance chain incomplete: Security not APPROVED (verdict: {latest_sec['verdict']})", chain
            if latest_sec.get("details", {}).get("critical_count", 0) > 0:
                return False, f"Governance chain incomplete: Security contains critical vulnerabilities", chain
            if normalize_logical_agent_id(latest_sec["agent_id"]) == exec_logical:
                return False, f"Governance chain incomplete: SoD violation on security by {latest_sec['agent_id']}", chain
            chain["security_review"] = {
                "receipt_id": latest_sec["receipt_id"],
                "agent_id": latest_sec["agent_id"],
                "status": "APPROVED",
            }
        else:
            chain["security_review"] = {
                "status": "NOT_REQUIRED",
            }

        # 4. Test Validation (Independent)
        tests = self.repository.get_validation_receipts(work_item_id, ReceiptType.TEST)
        if not tests:
            return False, f"Governance chain incomplete: Missing TestReceipt for {work_item_id}", chain
        latest_tst = tests[0]
        details_tst = latest_tst.get("details", {})
        if details_tst.get("failed_tests", 0) > 0 or latest_tst["verdict"] != "APPROVED":
            return False, f"Governance chain incomplete: Test validation has failing tests or not approved", chain
        if normalize_logical_agent_id(latest_tst["agent_id"]) == exec_logical:
            return False, f"Governance chain incomplete: SoD violation on test validation by {latest_tst['agent_id']}", chain
        chain["test_validation"] = {
            "receipt_id": latest_tst["receipt_id"],
            "agent_id": latest_tst["agent_id"],
            "status": "APPROVED",
        }

        # 5. QA Validation (Independent)
        qas = self.repository.get_validation_receipts(work_item_id, ReceiptType.QA)
        if not qas:
            return False, f"Governance chain incomplete: Missing QAReceipt for {work_item_id}", chain
        latest_qa = qas[0]
        details_qa = latest_qa.get("details", {})
        if details_qa.get("bdd_exit_code", 0) != 0 or latest_qa["verdict"] != "APPROVED":
            return False, f"Governance chain incomplete: QA validation has failing BDD exit code or not approved", chain
        if normalize_logical_agent_id(latest_qa["agent_id"]) == exec_logical:
            return False, f"Governance chain incomplete: SoD violation on QA validation by {latest_qa['agent_id']}", chain
        chain["qa_validation"] = {
            "receipt_id": latest_qa["receipt_id"],
            "agent_id": latest_qa["agent_id"],
            "status": "APPROVED",
        }

        chain["status"] = "COMPLETE"
        return True, "Governance evidence chain complete and verified", chain

    def verify_gate_eligibility(
        self,
        work_item_id: str,
        gate_id: Union[str, GateId],
        security_required: bool = True,
    ) -> Tuple[bool, str]:
        """Verifies if work item is eligible to pass gate_id based on receipts and SoD."""
        canonical_gate = normalize_gate_id(gate_id)
        expected_stage = GATE_TO_STAGE_MAP.get(canonical_gate)
        if not expected_stage:
            return False, f"Gate '{canonical_gate.value}' has no registered stage mapping"

        # Check stage receipt satisfaction
        satisfied, reason = self.is_stage_satisfied(
            work_item_id=work_item_id,
            stage=expected_stage,
            security_required=security_required,
        )
        if not satisfied:
            return False, f"Gate '{canonical_gate.value}' blocked: {reason}"

        return True, f"Gate '{canonical_gate.value}' eligible"

    def _emit_event(self, event_type: str, receipt: BaseReceipt, project_id: str) -> None:
        if not self.event_store:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "receipt_id": receipt.receipt_id,
            "receipt_type": receipt.receipt_type,
            "work_item_id": receipt.work_item_id,
            "agent_id": receipt.agent_id,
            "instruction_hash": receipt.instruction_hash,
            "evidence_hash": receipt.evidence_hash,
            "created_at": receipt.created_at.isoformat(),
        }
        event_seed = f"{event_type}:{receipt.work_item_id}:{receipt.receipt_id}:{canonical_json(payload)}"
        event_idem_key = hashlib.sha256(event_seed.encode("utf-8")).hexdigest()
        payload_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

        try:
            with self.event_store.connection() as conn:
                with conn:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO events (
                            event_id, event_type, work_item_id, project_id, source,
                            correlation_id, causation_id, idempotency_key, timestamp,
                            payload, payload_hash, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            event_type,
                            receipt.work_item_id,
                            project_id,
                            "execution_service",
                            receipt.receipt_id,
                            receipt.receipt_id,
                            event_idem_key,
                            now_iso,
                            canonical_json(payload),
                            payload_hash,
                            now_iso,
                        ),
                    )
        except Exception:
            pass
