"""Canonical Dispatch Service for Specialist Agents (Milestone R11).

Strictly stdlib-only. Orchestrates host-native specialist dispatch, validates envelope
integrity and session freshness, ensures idempotency, and records durable receipts.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import uuid

from scripts.domain.delegation import DelegationEnvelope
from scripts.domain.events import DomainEvent
from scripts.domain.receipts import DispatchReceipt
from scripts.runtime.delegation.repository import DelegationRepository
from scripts.runtime.dispatch.errors import (
    DelegationEnvelopeNotReadyError,
    DispatchError,
    DispatchExecutionFailedError,
    DispatchIdempotencyConflictError,
    DispatchReceiptIntegrityError,
    DispatchRetryableError,
    DispatchTerminalError,
    HostCapabilityMismatchError,
    HostResolutionError,
    SessionInvalidForDispatchError,
    UnsupportedHostError,
)
from scripts.runtime.dispatch.host_registry import HostRegistry, default_host_registry
from scripts.runtime.dispatch.port import HostDispatchPort
from scripts.runtime.dispatch.receipts import (
    DispatchStatus,
    HostDispatchResult,
    HostExecutionBinding,
    HostStatusResult,
    compute_evidence_hash,
    mint_dispatch_receipt,
)
from scripts.runtime.dispatch.repository import DispatchRepository


class DispatchService:
    """Canonical service executing specialist dispatch across host adapters."""

    MAX_RETRIES: int = 3

    def __init__(
        self,
        dispatch_repository: DispatchRepository,
        delegation_repository: Optional[DelegationRepository] = None,
        host_registry: Optional[HostRegistry] = None,
        session_manager: Optional[Any] = None,
        event_store: Optional[Any] = None,
    ) -> None:
        self.dispatch_repository = dispatch_repository
        self.delegation_repository = delegation_repository
        self.host_registry = host_registry or default_host_registry
        self.session_manager = session_manager
        self.event_store = event_store

    def dispatch_delegation(
        self,
        delegation_id: str,
        host_override: Optional[str] = None,
    ) -> DispatchReceipt:
        """Dispatches an authoritative DelegationEnvelope to the resolved host substrate.

        Steps:
        1. Fetch DelegationEnvelope from delegation_repository.
        2. Validate envelope status is READY_FOR_DISPATCH and instruction hash matches verbatim.
        3. Validate MCP session freshness if session_manager is configured.
        4. Enforce idempotency: return existing DispatchReceipt if already DISPATCHED/ACKNOWLEDGED.
        5. Check retry budget: fail terminally if MAX_RETRIES exceeded.
        6. Resolve concrete HostDispatchPort adapter and check capabilities.
        7. Record PENDING attempt in SQLite.
        8. Execute host dispatch through adapter.
        9. Mint canonical DispatchReceipt and update persistence to DISPATCHED.
        10. Update DelegationEnvelope status to DISPATCHED in delegation_repository.
        11. Emit SpecialistDispatchedEvent to EventStore.
        12. Return DispatchReceipt.
        """
        if not self.delegation_repository:
            raise DispatchError(
                "DelegationRepository is not configured on DispatchService",
                error_code="ERR_SERVICE_MISCONFIGURED",
            )

        try:
            envelope = self.delegation_repository.get_envelope(delegation_id)
        except Exception as exc:
            raise DispatchReceiptIntegrityError(
                f"Instruction hash tampering or corruption detected for delegation '{delegation_id}': {exc}",
                error_code="ERR_INSTRUCTION_HASH_MISMATCH",
            ) from exc

        if not envelope:
            raise DelegationEnvelopeNotReadyError(
                f"Delegation envelope '{delegation_id}' not found",
                error_code="ERR_ENVELOPE_NOT_FOUND",
            )

        row = self.delegation_repository.get_by_id(delegation_id) or {}
        envelope_status = row.get("status", "")
        project_id = row.get("project_id", "default-project")
        session_id = row.get("session_id", "default-session")

        # 1. Status and idempotency check
        if envelope_status == "DISPATCHED":
            cached_receipt = self.dispatch_repository.get_receipt_for_delegation(delegation_id)
            if cached_receipt:
                return cached_receipt

        if envelope_status != "READY_FOR_DISPATCH":
            raise DelegationEnvelopeNotReadyError(
                f"Delegation envelope '{delegation_id}' is in status '{envelope_status}', expected 'READY_FOR_DISPATCH'",
                error_code="ERR_ENVELOPE_NOT_READY",
            )

        # 2. Instruction hash integrity verification
        expected_hash = hashlib.sha256(envelope.compiled_instruction.encode("utf-8")).hexdigest()
        if envelope.instruction_hash != expected_hash:
            raise DispatchReceiptIntegrityError(
                f"Instruction hash tampering detected for delegation '{delegation_id}': "
                f"expected {envelope.instruction_hash}, got {expected_hash}",
                error_code="ERR_INSTRUCTION_HASH_MISMATCH",
            )

        # 3. Session freshness verification
        if self.session_manager and session_id:
            try:
                session = self.session_manager.get_session(session_id)
                if not session:
                    raise SessionInvalidForDispatchError(
                        f"MCP session '{session_id}' not found",
                        error_code="ERR_SESSION_INVALID",
                    )
                # Session must be active
                is_active = False
                if hasattr(session, "is_active"):
                    is_active = session.is_active()
                elif isinstance(session, dict):
                    is_active = (session.get("status") == "active")
                elif hasattr(session, "status"):
                    is_active = (session.status == "active")
                if not is_active:
                    raise SessionInvalidForDispatchError(
                        f"MCP session '{session_id}' is not active",
                        error_code="ERR_SESSION_INVALID",
                    )
            except SessionInvalidForDispatchError:
                raise
            except Exception as e:
                raise SessionInvalidForDispatchError(
                    f"Session validation failed for '{session_id}': {e}",
                    error_code="ERR_SESSION_INVALID",
                ) from e

        # 4. Check active attempts & idempotency
        active_attempt = self.dispatch_repository.get_active_attempt_for_delegation(delegation_id)
        if active_attempt:
            act_status = active_attempt.get("status")
            if act_status in ("DISPATCHED", "ACKNOWLEDGED"):
                cached_receipt = self.dispatch_repository.get_receipt_for_delegation(delegation_id)
                if cached_receipt:
                    return cached_receipt
            elif act_status == "PENDING":
                raise DispatchIdempotencyConflictError(
                    f"A dispatch attempt is currently in-flight for delegation '{delegation_id}'",
                    error_code="ERR_CONCURRENT_DISPATCH_CONFLICT",
                )

        # 5. Retry budget enforcement
        prior_attempts = self.dispatch_repository.get_attempts_for_delegation(delegation_id)
        failed_count = sum(1 for a in prior_attempts if a.get("status") in ("FAILED_RETRYABLE", "FAILED_TERMINAL"))
        if failed_count >= self.MAX_RETRIES:
            raise DispatchTerminalError(
                f"Maximum dispatch retries ({self.MAX_RETRIES}) exceeded for delegation '{delegation_id}'",
                error_code="ERR_MAX_RETRIES_EXCEEDED",
            )

        # 6. Host adapter resolution
        session_host = None
        if isinstance(row.get("metadata"), dict):
            session_host = row["metadata"].get("host")

        if host_override:
            adapter = self.host_registry.get_adapter(host_override)
        else:
            adapter = self.host_registry.resolve_active_host(session_host=session_host)

        # Capability validation
        can_run, reason = adapter.can_dispatch(envelope)
        if not can_run:
            unsupported_id = self.dispatch_repository.record_attempt(
                delegation_id=delegation_id,
                session_id=session_id,
                work_item_id=envelope.work_item_id,
                sender_role=envelope.sender_role,
                target_role=envelope.target_role,
                host_kind=adapter.host_kind,
                instruction_hash=envelope.instruction_hash,
                status=DispatchStatus.UNSUPPORTED,
                evidence_hash="",
                metadata={"rejection_reason": reason},
            )
            self.dispatch_repository.update_attempt_failure(
                dispatch_id=unsupported_id,
                error_code="ERR_CAPABILITY_MISMATCH",
                error_message=reason or "Host lacks required capabilities for envelope",
                status=DispatchStatus.UNSUPPORTED,
            )
            raise HostCapabilityMismatchError(
                f"Host '{adapter.host_kind}' cannot dispatch delegation '{delegation_id}': {reason}",
                error_code="ERR_CAPABILITY_MISMATCH",
            )

        # 7. Create Execution Binding & Record PENDING Attempt
        binding = HostExecutionBinding(
            host_kind=adapter.host_kind,
            host_version="1.0.0",
            session_id=session_id,
            execution_handle="",
            dispatch_mode="native_subagent" if adapter.get_capabilities().has_subagent_dispatch else "cli",
            capabilities=adapter.get_capabilities(),
            metadata={"work_item_id": envelope.work_item_id, "project_id": project_id},
        )

        dispatch_id = self.dispatch_repository.record_attempt(
            delegation_id=delegation_id,
            session_id=session_id,
            work_item_id=envelope.work_item_id,
            sender_role=envelope.sender_role,
            target_role=envelope.target_role,
            host_kind=adapter.host_kind,
            instruction_hash=envelope.instruction_hash,
            status=DispatchStatus.PENDING,
            metadata=binding.to_dict(),
        )

        # 8. Dispatch via Host Adapter
        try:
            dispatch_result = adapter.dispatch(envelope, binding)
        except Exception as exc:
            err_msg = str(exc)
            self.dispatch_repository.update_attempt_failure(
                dispatch_id=dispatch_id,
                error_code="ERR_HOST_INVOCATION_FAILED",
                error_message=err_msg,
                status=DispatchStatus.FAILED_TERMINAL,
            )
            self._emit_dispatch_failed_event(
                delegation_id=delegation_id,
                work_item_id=envelope.work_item_id,
                project_id=project_id,
                target_role=envelope.target_role,
                host_kind=adapter.host_kind,
                error_code="ERR_HOST_INVOCATION_FAILED",
                error_message=err_msg,
                retryable=False,
            )
            raise DispatchExecutionFailedError(
                f"Underlying host invocation threw an exception: {err_msg}",
                error_code="ERR_HOST_INVOCATION_FAILED",
                retryable=False,
            ) from exc

        # 9. Handle Dispatch Result
        if dispatch_result.status not in (DispatchStatus.DISPATCHED, DispatchStatus.ACKNOWLEDGED):
            is_retry = (dispatch_result.status == DispatchStatus.FAILED_RETRYABLE)
            err_code = dispatch_result.error_code or (
                "ERR_HOST_SPAWN_TIMEOUT" if is_retry else "ERR_HOST_INVOCATION_FAILED"
            )
            err_msg = dispatch_result.error_message or f"Host returned failure status {dispatch_result.status.value}"

            self.dispatch_repository.update_attempt_failure(
                dispatch_id=dispatch_id,
                error_code=err_code,
                error_message=err_msg,
                status=dispatch_result.status,
            )
            self._emit_dispatch_failed_event(
                delegation_id=delegation_id,
                work_item_id=envelope.work_item_id,
                project_id=project_id,
                target_role=envelope.target_role,
                host_kind=adapter.host_kind,
                error_code=err_code,
                error_message=err_msg,
                retryable=is_retry,
            )

            if is_retry:
                raise DispatchRetryableError(err_msg, error_code=err_code)
            raise DispatchTerminalError(err_msg, error_code=err_code)

        # 10. Mint canonical DispatchReceipt and update persistence
        receipt = mint_dispatch_receipt(
            envelope=envelope,
            dispatch_result=dispatch_result,
        )
        evidence_hash = compute_evidence_hash(dispatch_result.evidence_payload)

        self.dispatch_repository.update_attempt_success(
            dispatch_id=dispatch_id,
            host_execution_id=dispatch_result.host_execution_id,
            evidence_hash=evidence_hash,
            receipt=receipt,
            status=dispatch_result.status,
            metadata={"binding": binding.to_dict()},
        )

        # 11. Update DelegationEnvelope status in DelegationRepository
        self.delegation_repository.update_status(delegation_id, "DISPATCHED")

        # 12. Emit domain event
        self._emit_dispatched_event(
            delegation_id=delegation_id,
            work_item_id=envelope.work_item_id,
            project_id=project_id,
            sender_role=envelope.sender_role,
            target_role=envelope.target_role,
            host_kind=adapter.host_kind,
            host_execution_id=dispatch_result.host_execution_id,
            receipt_id=receipt.receipt_id,
            instruction_hash=envelope.instruction_hash,
            dispatch_id=dispatch_id,
        )

        return receipt

    def dispatch(
        self,
        delegation_id: str,
        host_override: Optional[str] = None,
    ) -> DispatchReceipt:
        """Alias for dispatch_delegation."""
        return self.dispatch_delegation(delegation_id, host_override=host_override)

    def check_dispatch_status(self, dispatch_id: str) -> HostStatusResult:
        """Queries the current status of a dispatch attempt via its host adapter."""
        attempt = self.dispatch_repository.get_attempt(dispatch_id)
        if not attempt:
            return HostStatusResult(
                host_execution_id="",
                status=DispatchStatus.FAILED_TERMINAL,
                is_alive=False,
                details={"error": f"Dispatch attempt '{dispatch_id}' not found"},
            )

        host_kind = attempt.get("host_kind", "")
        host_exec_id = attempt.get("host_execution_id") or ""
        try:
            adapter = self.host_registry.get_adapter(host_kind)
            return adapter.check_status(host_exec_id)
        except Exception as e:
            return HostStatusResult(
                host_execution_id=host_exec_id,
                status=DispatchStatus(attempt.get("status", "FAILED_TERMINAL")),
                is_alive=False,
                details={"error": str(e)},
            )

    def cancel_dispatch(self, dispatch_id: str) -> bool:
        """Cancels a dispatch attempt via its host adapter and updates SQLite."""
        attempt = self.dispatch_repository.get_attempt(dispatch_id)
        if not attempt:
            return False

        host_kind = attempt.get("host_kind", "")
        host_exec_id = attempt.get("host_execution_id") or ""
        cancelled = True
        if host_exec_id:
            try:
                adapter = self.host_registry.get_adapter(host_kind)
                cancelled = adapter.cancel(host_exec_id)
            except Exception:
                cancelled = False

        if cancelled:
            self.dispatch_repository.update_attempt_status(
                dispatch_id=dispatch_id,
                status=DispatchStatus.CANCELLED,
            )
        return cancelled

    def _emit_dispatched_event(
        self,
        delegation_id: str,
        work_item_id: str,
        project_id: str,
        sender_role: str,
        target_role: str,
        host_kind: str,
        host_execution_id: str,
        receipt_id: str,
        instruction_hash: str,
        dispatch_id: str,
    ) -> None:
        """Emits SpecialistDispatchedEvent to R2 EventStore if configured."""
        if not self.event_store:
            return

        payload = {
            "dispatch_id": dispatch_id,
            "delegation_id": delegation_id,
            "work_item_id": work_item_id,
            "sender_role": sender_role,
            "target_role": target_role,
            "host_kind": host_kind,
            "host_execution_id": host_execution_id,
            "receipt_id": receipt_id,
            "instruction_hash": instruction_hash,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            event = DomainEvent.create(
                event_type="agent_squad.specialist.dispatched",
                work_item_id=work_item_id,
                project_id=project_id,
                source="runtime.dispatch_service",
                correlation_id=work_item_id,
                causation_id=delegation_id,
                payload=payload,
            )
            if hasattr(self.event_store, "record"):
                self.event_store.record(event)
            elif hasattr(self.event_store, "append"):
                self.event_store.append(event)
        except Exception:
            # Event emission failures should not block dispatch execution
            pass

    def _emit_dispatch_failed_event(
        self,
        delegation_id: str,
        work_item_id: str,
        project_id: str,
        target_role: str,
        host_kind: str,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> None:
        """Emits SpecialistDispatchFailedEvent to R2 EventStore if configured."""
        if not self.event_store:
            return

        payload = {
            "delegation_id": delegation_id,
            "work_item_id": work_item_id,
            "target_role": target_role,
            "host_kind": host_kind,
            "error_code": error_code,
            "error_message": error_message,
            "retryable": retryable,
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            event = DomainEvent.create(
                event_type="agent_squad.specialist.dispatch_failed",
                work_item_id=work_item_id,
                project_id=project_id,
                source="runtime.dispatch_service",
                correlation_id=work_item_id,
                causation_id=delegation_id,
                payload=payload,
            )
            if hasattr(self.event_store, "record"):
                self.event_store.record(event)
            elif hasattr(self.event_store, "append"):
                self.event_store.append(event)
        except Exception:
            pass
