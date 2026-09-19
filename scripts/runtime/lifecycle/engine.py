"""Canonical Lifecycle Transition Service and State Machine Engine.

Strictly stdlib-only.
Sole Source of Authority for State Transitions across Agent Squad.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid
import yaml

from scripts.domain.common import canonical_json
from scripts.domain.lifecycle import (
    AcknowledgementStatus,
    GateDecisionStatus,
    GateId,
    LifecycleStage,
    StagePolicy,
)
from scripts.runtime.events.store import SqliteEventStore
from scripts.runtime.lifecycle.errors import (
    ConfigurationError,
    GateNotEligibleError,
    GateNotPassedError,
    HandoffPendingError,
    InvalidTransitionError,
    LifecycleError,
    TimeboxExceededError,
    WIPLimitExceededError,
)
from scripts.runtime.lifecycle.gates import (
    GATE_TO_STAGE_MAP,
    STAGE_TO_GATE_MAP,
    assert_gate_eligibility,
    normalize_gate_id,
    verify_gate_approval,
)
from scripts.runtime.lifecycle.history import LifecycleRepository
from scripts.runtime.lifecycle.policies import (
    CANONICAL_CYCLES,
    CANONICAL_STAGE_POLICIES,
    CANONICAL_TO_LEGACY_STATE,
    get_cycle_stages,
    get_stage_policy,
    load_cycles_config,
    load_workflow_config,
    normalize_stage,
    resolve_cycle_for_kind,
    stage_to_legacy_name,
)
from scripts.runtime.lifecycle.wip import WIPController

try:
    from governed_io import atomic_write_text
except ImportError:
    try:
        from scripts.governed_io import atomic_write_text
    except ImportError:
        def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
            tmp_path = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{uuid.uuid4().hex[:6]}")
            tmp_path.write_text(content, encoding=encoding)
            tmp_path.replace(path)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


class CanonicalLifecycleService:
    """Sole authoritative service governing lifecycle state transitions and policy enforcement."""

    def __init__(
        self,
        db_path: Optional[Union[str, Path, sqlite3.Connection]] = None,
        event_store: Optional[SqliteEventStore] = None,
        root_path: Optional[Path] = None,
    ) -> None:
        self.root_path = Path(root_path or os.environ.get("SQUAD_RUNTIME", Path.cwd()))
        self.repository = LifecycleRepository(db_path)
        self.event_store = event_store or SqliteEventStore(db_path)
        self.wip_controller = WIPController()
        from scripts.runtime.execution.service import ExecutionReceiptService
        self.execution_service = ExecutionReceiptService(
            db_path=db_path,
            event_store=self.event_store,
        )
        self._cycles_cfg: Optional[dict] = None
        self._workflow_cfg: Optional[dict] = None

    @property
    def cycles_cfg(self) -> dict:
        if self._cycles_cfg is None:
            candidate = self.root_path / "config" / "cycles.yaml"
            if not candidate.is_file():
                repo_root = Path(__file__).resolve().parents[3]
                fallback = repo_root / "config" / "cycles.yaml"
                if fallback.is_file():
                    candidate = fallback
            try:
                self._cycles_cfg = load_cycles_config(candidate)
            except Exception:
                self._cycles_cfg = {}
        return self._cycles_cfg

    @property
    def workflow_cfg(self) -> dict:
        if self._workflow_cfg is None:
            candidate = self.root_path / "config" / "workflow.yaml"
            if not candidate.is_file():
                repo_root = Path(__file__).resolve().parents[3]
                fallback = repo_root / "config" / "workflow.yaml"
                if fallback.is_file():
                    candidate = fallback
            try:
                self._workflow_cfg = load_workflow_config(candidate)
            except Exception:
                self._workflow_cfg = {}
        return self._workflow_cfg

    def _resolve_item_path(self, work_item_id: str, project_id: Optional[str] = None) -> Path:
        proj = project_id or "default"
        # 1. Search directly in work/<proj>/<work_item_id>
        candidate = self.root_path / "work" / proj / work_item_id
        if candidate.is_dir():
            return candidate

        # 2. Search anywhere in work/
        work_dir = self.root_path / "work"
        if work_dir.is_dir():
            for p in work_dir.iterdir():
                if p.is_dir():
                    c = p / work_item_id
                    if c.is_dir():
                        return c
                    # Check nested hierarchies
                    for sub in p.glob(f"**/{work_item_id}"):
                        if sub.is_dir():
                            return sub

        return candidate

    def can_transition(
        self,
        work_item_id: str,
        project_id: Optional[str] = None,
        target_stage: Optional[Union[str, LifecycleStage]] = None,
        item_path: Optional[Path] = None,
    ) -> Tuple[bool, str]:
        """Evaluates whether work_item_id is eligible to advance to target_stage without side effects.

        Returns:
            Tuple of (is_allowed, reason).
        """
        path = item_path or self._resolve_item_path(work_item_id, project_id)
        status_file = path / "status.yaml"
        if not status_file.is_file():
            return False, f"status.yaml not found for item {work_item_id}"

        try:
            status_data = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return False, f"Failed to read status.yaml: {exc}"

        current_state_str = status_data.get("state")
        if not current_state_str:
            return False, f"Work item {work_item_id} has no current state"
        if current_state_str == "done":
            return False, "Work item is already in terminal state 'done'"

        try:
            current_stage = normalize_stage(current_state_str)
        except Exception as exc:
            return False, str(exc)

        # 1. Resolve cycle and stages
        cycle_name = status_data.get("cycle") or resolve_cycle_for_kind(status_data.get("type", "feature"))
        cycle_def = self.cycles_cfg.get("cycles", {}).get(cycle_name, {})
        raw_states = cycle_def.get("states", [])
        try:
            cycle_stages = get_cycle_stages(cycle_name, self.cycles_cfg)
        except Exception:
            cycle_stages = []

        if raw_states and current_state_str in raw_states:
            curr_idx = raw_states.index(current_state_str)
            if target_stage is not None:
                target_str = stage_to_legacy_name(target_stage) if isinstance(target_stage, LifecycleStage) else str(target_stage)
                if target_str in raw_states:
                    next_legacy_state = target_str
                else:
                    next_legacy_state = stage_to_legacy_name(normalize_stage(target_stage))
            else:
                if curr_idx + 1 >= len(raw_states):
                    return False, f"No subsequent stage after '{current_state_str}' in cycle '{cycle_name}'"
                next_legacy_state = raw_states[curr_idx + 1]
            try:
                next_stage = normalize_stage(next_legacy_state)
            except Exception as exc:
                return False, str(exc)
        else:
            try:
                cycle_stages = get_cycle_stages(cycle_name, self.cycles_cfg)
            except Exception as exc:
                return False, str(exc)

            if current_stage not in cycle_stages:
                return False, f"Current stage '{current_stage.value}' not in cycle '{cycle_name}'"

            curr_idx = cycle_stages.index(current_stage)
            if target_stage is not None:
                try:
                    next_stage = normalize_stage(target_stage)
                except Exception as exc:
                    return False, str(exc)
                if next_stage not in cycle_stages:
                    return False, f"Target stage '{next_stage.value}' is not part of cycle '{cycle_name}'"
            else:
                if curr_idx + 1 >= len(cycle_stages):
                    return False, f"No subsequent stage after '{current_stage.value}' in cycle '{cycle_name}'"
                next_stage = cycle_stages[curr_idx + 1]
            next_legacy_state = stage_to_legacy_name(next_stage)

        # 3. Check Policy target legality
        policy = get_stage_policy(current_stage)
        if next_stage not in policy.allowed_next_stages and next_stage not in cycle_stages:
            return False, f"Transition from {current_stage.value} to {next_stage.value} is illegal"

        # 4. Check Timebox
        timebox_ok, t_reason = self._check_timebox_status(status_data, current_stage)
        if not timebox_ok:
            return False, t_reason

        # 5. Check Receipts
        if current_stage == LifecycleStage.IMPLEMENTATION:
            if not self._has_execution_proof(path, work_item_id=work_item_id):
                return False, "Execution receipt or proof required before exiting implementation"
        elif current_stage in {LifecycleStage.CODE_REVIEW, LifecycleStage.SECURITY_REVIEW}:
            if not self._has_review_proof(path, work_item_id=work_item_id):
                return False, "Review receipt or reviewer proof required before exiting review"

        # 6. Check Governance Gate prerequisites
        cycle_def = self.cycles_cfg.get("cycles", {}).get(cycle_name, {})
        gate_bypass = bool(cycle_def.get("gate_bypass", False))
        if not gate_bypass:
            # Check G1/G2 on blueprint exit to scaffolding
            is_blueprint_exit = (
                current_stage == LifecycleStage.REQUIREMENTS_PRODUCT
                or current_state_str == "blueprint"
            )
            is_scaffolding_target = (
                next_stage == LifecycleStage.READINESS_SCAFFOLDING
                or stage_to_legacy_name(next_stage) == "scaffolding"
            )
            if is_blueprint_exit:
                g1_approved, _ = verify_gate_approval(path, GateId.G1_PRODUCT)
                if not g1_approved:
                    return False, "Gate G1-product approval required before advancing from blueprint"
                if is_scaffolding_target:
                    item_risk = str(status_data.get("risk", "low")).lower()
                    if item_risk in {"medium", "high", "critical"}:
                        g2_approved, _ = verify_gate_approval(path, GateId.G2_DESIGN)
                        if not g2_approved:
                            return False, "G2-design approval required before reaching scaffolding"

            # Check general stage gate
            required_gate = STAGE_TO_GATE_MAP.get(current_stage)
            if required_gate and not is_blueprint_exit:
                gate_approved, _ = verify_gate_approval(path, required_gate)
                if not gate_approved:
                    return False, f"Gate '{required_gate.value}' approval required before exiting {current_stage.value}"

        # 7. Check Handoff state (PENDING blocks!)
        handoff_ok, h_reason = self._check_handoff_status(path)
        if not handoff_ok:
            return False, h_reason

        # 8. Check WIP capacity
        proj_id = project_id or status_data.get("project_id", "default")
        with self.repository.connection() as conn:
            limit = self.wip_controller.get_limit(next_stage, self.workflow_cfg)
            if limit is not None and limit > 0:
                count = self.wip_controller.count_active_items(
                    conn, proj_id, next_stage, path.parent, exclude_work_item_id=work_item_id
                )
                if count >= limit:
                    return False, f"WIP limit of {limit} reached for stage {stage_to_legacy_name(next_stage)}"

        return True, "OK"

    def transition(
        self,
        work_item_id: str,
        project_id: Optional[str] = None,
        target_stage: Optional[Union[str, LifecycleStage]] = None,
        initiated_by: str = "00-delivery-orchestrator",
        gate_decision_id: Optional[str] = None,
        handoff_id: Optional[str] = None,
        now: Optional[datetime] = None,
        item_path: Optional[Path] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        require_handoff: bool = False,
    ) -> Dict[str, Any]:
        """Atomically transitions work_item_id to target_stage enforcing all canonical prerequisites.

        Raises:
            InvalidTransitionError
            GateNotPassedError
            HandoffPendingError
            WIPLimitExceededError
            TimeboxExceededError
            ConfigurationError
            LifecycleError
        """
        path = item_path or self._resolve_item_path(work_item_id, project_id)
        status_file = path / "status.yaml"
        if not status_file.is_file():
            raise LifecycleError(f"status.yaml not found for item {work_item_id} at {path}")

        status_data = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
        current_state_str = status_data.get("state")
        if not current_state_str:
            raise InvalidTransitionError(f"Work item {work_item_id} has no current state defined")
        if current_state_str == "done":
            raise InvalidTransitionError(f"Work item {work_item_id} is already in terminal state 'done'")

        current_stage = normalize_stage(current_state_str)
        proj_id = project_id or status_data.get("project_id", "default")
        cycle_name = status_data.get("cycle") or resolve_cycle_for_kind(status_data.get("type", "feature"))

        if cycle_name not in self.cycles_cfg.get("cycles", {}) and cycle_name not in CANONICAL_CYCLES:
            raise ConfigurationError(f"Cycle '{cycle_name}' is not defined in configuration or canonical registry")

        cycle_def = self.cycles_cfg.get("cycles", {}).get(cycle_name, {})
        raw_states = cycle_def.get("states", [])

        if raw_states and current_state_str in raw_states:
            curr_idx = raw_states.index(current_state_str)
            if target_stage is not None:
                target_str = stage_to_legacy_name(target_stage) if isinstance(target_stage, LifecycleStage) else str(target_stage)
                if target_str not in raw_states:
                    raise InvalidTransitionError(f"Estado de destino '{target_str}' não pertence aos estados do ciclo '{cycle_name}'")
                target_idx = raw_states.index(target_str)
                if target_idx == curr_idx:
                    raise InvalidTransitionError(f"Transição inválida: estado de destino '{target_str}' é idêntico ao estado atual")
                target_norm = normalize_stage(target_str)
                if target_idx < curr_idx:
                    curr_policy = get_stage_policy(current_stage)
                    if target_norm not in curr_policy.allowed_next_stages:
                        raise InvalidTransitionError(f"Transição arbitrária para trás de '{current_state_str}' para '{target_str}' rejeitada")
                elif target_idx > curr_idx + 1:
                    raise InvalidTransitionError(f"Salto arbitrário para o futuro de '{current_state_str}' para '{target_str}' rejeitado")
                next_legacy_state = target_str
            else:
                if curr_idx + 1 >= len(raw_states):
                    raise InvalidTransitionError(
                        f"Não há próximo estado após '{current_state_str}' no ciclo '{cycle_name}'"
                    )
                next_legacy_state = raw_states[curr_idx + 1]
            next_stage = normalize_stage(next_legacy_state)
        else:
            cycle_stages = get_cycle_stages(cycle_name, self.cycles_cfg)
            if current_stage not in cycle_stages:
                raise InvalidTransitionError(
                    f"Estado atual '{current_state_str}' não pertence aos estados do ciclo '{cycle_name}': {[s.value for s in cycle_stages]}"
                )

            curr_idx = cycle_stages.index(current_stage)
            if target_stage is not None:
                next_stage = normalize_stage(target_stage)
                if next_stage not in cycle_stages:
                    raise InvalidTransitionError(
                        f"Target stage '{next_stage.value}' is not part of cycle '{cycle_name}'"
                    )
                target_idx = cycle_stages.index(next_stage)
                if target_idx == curr_idx:
                    raise InvalidTransitionError(f"Transição inválida: estado de destino '{next_stage.value}' é idêntico ao estado atual")
                if target_idx < curr_idx:
                    curr_policy = get_stage_policy(current_stage)
                    if next_stage not in curr_policy.allowed_next_stages:
                        raise InvalidTransitionError(f"Transição arbitrária para trás de '{current_stage.value}' para '{next_stage.value}' rejeitada")
                elif target_idx > curr_idx + 1:
                    raise InvalidTransitionError(f"Salto arbitrário para o futuro de '{current_stage.value}' para '{next_stage.value}' rejeitado")
            else:
                if curr_idx + 1 >= len(cycle_stages):
                    raise InvalidTransitionError(
                        f"Não há próximo estado após '{current_state_str}' no ciclo '{cycle_name}'"
                    )
                next_stage = cycle_stages[curr_idx + 1]
            next_legacy_state = stage_to_legacy_name(next_stage)

        # 1. Enforce Timebox
        timebox_ok, t_reason = self._check_timebox_status(status_data, current_stage, now=now)
        if not timebox_ok:
            raise TimeboxExceededError(t_reason)

        # 2. Enforce Gate Prerequisites
        cycle_def = self.cycles_cfg.get("cycles", {}).get(cycle_name, {})
        gate_bypass = bool(cycle_def.get("gate_bypass", False))
        resolved_gate_id: Optional[str] = gate_decision_id

        if not gate_bypass:
            is_blueprint_exit = (
                current_stage == LifecycleStage.REQUIREMENTS_PRODUCT
                or current_state_str == "blueprint"
            )
            is_scaffolding_target = (
                next_stage == LifecycleStage.READINESS_SCAFFOLDING
                or stage_to_legacy_name(next_stage) == "scaffolding"
            )
            if is_blueprint_exit:
                g1_approved, g1_data = verify_gate_approval(path, GateId.G1_PRODUCT)
                if not g1_approved:
                    raise GateNotPassedError(
                        f"Avanço de estado bloqueado: o estado '{current_state_str}' exige a aprovação do gate 'G1-product'."
                    )
                if resolved_gate_id is None and g1_data:
                    resolved_gate_id = g1_data.get("decision_id")

                if is_scaffolding_target:
                    item_risk = str(status_data.get("risk", "low")).lower()
                    if item_risk in {"medium", "high", "critical"}:
                        g2_approved, g2_data = verify_gate_approval(path, GateId.G2_DESIGN)
                        if not g2_approved:
                            raise GateNotPassedError(
                                "Avanço de estado bloqueado: G2-design é obrigatório antes de avançar para scaffolding. "
                                "Nenhuma decisão com status 'approved' encontrada para G2-design."
                            )
                        if g2_data:
                            resolved_gate_id = g2_data.get("decision_id")
            else:
                required_gate = STAGE_TO_GATE_MAP.get(current_stage)
                if required_gate:
                    gate_approved, g_data = verify_gate_approval(path, required_gate)
                    if not gate_approved:
                        legacy_gname = required_gate.value
                        raise GateNotPassedError(
                            f"Avanço de estado bloqueado: o estado '{current_state_str}' exige a aprovação do gate '{legacy_gname}'. "
                            f"Nenhuma decisão com status 'approved' encontrada em gate-decisions/."
                        )
                    if resolved_gate_id is None and g_data:
                        resolved_gate_id = g_data.get("decision_id")

        # 3. Enforce Receipts Prerequisites
        is_incident_mitigation = (
            current_state_str == "mitigation"
            or gate_bypass
        )
        if current_stage == LifecycleStage.IMPLEMENTATION and not is_incident_mitigation:
            if not self._has_execution_proof(path, work_item_id=work_item_id):
                raise LifecycleError(
                    "Avanço bloqueado: saindo de 'implementation' exige ExecutionReceipt / execution receipt / prova de execução válida (execution proof)."
                )
        elif current_stage in {LifecycleStage.CODE_REVIEW, LifecycleStage.SECURITY_REVIEW}:
            if not self._has_review_proof(path, work_item_id=work_item_id):
                raise LifecycleError(
                    "Avanço bloqueado: saindo de revisão de código exige ReviewReceipt / review receipt / reviewer execution comprovada."
                )

        # 4. Enforce Handoff Acknowledgement (PENDING blocks!)
        handoff_req = require_handoff or bool(status_data.get("handoff_required", False))
        handoff_ok, h_reason = self._check_handoff_status(path, current_stage=current_stage, require_handoff=handoff_req)
        if not handoff_ok:
            raise HandoffPendingError(h_reason)

        # 5. Enforce WIP Limits
        with self.repository.connection() as conn:
            self.wip_controller.assert_wip_capacity(
                db_conn=conn,
                project_id=proj_id,
                target_stage=next_stage,
                project_path=path.parent,
                exclude_work_item_id=work_item_id,
                workflow_cfg=self.workflow_cfg,
            )

        # 6. Prepare Transition Details
        next_policy = get_stage_policy(next_stage)
        current_time = now or _utc_now()
        now_iso = current_time.isoformat()
        if not next_legacy_state:
            next_legacy_state = stage_to_legacy_name(next_stage)
        prev_legacy_state = current_state_str

        seed = f"transition:{work_item_id}:{current_stage.value}:{next_stage.value}:{now_iso[:19]}"
        idempotency_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        transition_id = f"TR-{uuid.uuid4().hex[:12].upper()}"

        # Resolve responsible agents
        states_cfg = {s["id"]: s for s in self.workflow_cfg.get("states", [])}
        next_state_cfg = states_cfg.get(next_legacy_state, {})
        next_owner = next_state_cfg.get("owner", next_policy.owner_role)
        collaborators = next_state_cfg.get("collaborators", next_policy.collaborator_roles)
        selectable = next_state_cfg.get("selectable_agents", [])

        responsible_agents: List[str] = []
        if next_owner and next_owner not in responsible_agents:
            responsible_agents.append(next_owner)
        for col in collaborators:
            if col not in responsible_agents:
                responsible_agents.append(col)

        # 7. Atomic SQLite Commit + Outbox Event Emission
        with self.repository.connection() as conn:
            with conn:
                # 7a. Record in lifecycle_history and work_item_lifecycle_state
                trans_rec, was_created = self.repository.record_transition(
                    conn=conn,
                    transition_id=transition_id,
                    work_item_id=work_item_id,
                    project_id=proj_id,
                    cycle_id=cycle_name,
                    from_stage=current_stage.value,
                    to_stage=next_stage.value,
                    initiated_by=initiated_by,
                    owner_role=next_owner,
                    idempotency_key=idempotency_key,
                    gate_decision_id=resolved_gate_id,
                    handoff_id=handoff_id,
                    payload={"responsible_agents": responsible_agents},
                    now_iso=now_iso,
                )

                # 7b. Emit DomainEvent STAGE_ENTERED into R2 Outbox
                event_id = str(uuid.uuid4())
                event_type = "agent_squad.stage.entered"
                payload = {
                    "work_item_id": work_item_id,
                    "from_stage": prev_legacy_state,
                    "to_stage": next_legacy_state,
                    "stage": next_stage.value,
                    "owner_role": next_owner,
                    "collaborators": collaborators,
                    "phase_started_at": now_iso,
                    "transition_id": transition_id,
                }
                event_seed = f"{event_type}:{work_item_id}:{transition_id}:{canonical_json(payload)}"
                event_idem_key = hashlib.sha256(event_seed.encode("utf-8")).hexdigest()
                payload_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

                conn.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        event_id, event_type, work_item_id, project_id, source,
                        correlation_id, causation_id, idempotency_key, timestamp,
                        payload, payload_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        event_type,
                        work_item_id,
                        proj_id,
                        "lifecycle_engine",
                        correlation_id or transition_id,
                        causation_id or transition_id,
                        event_idem_key,
                        now_iso,
                        canonical_json(payload),
                        payload_hash,
                        now_iso,
                    ),
                )

        # 8. Refresh status.yaml read projection
        status_data["state"] = next_legacy_state
        status_data["owner"] = next_owner
        status_data["active_agents"] = responsible_agents[:10]
        status_data["current_gate"] = STAGE_TO_GATE_MAP.get(next_stage, "").value if (not gate_bypass and STAGE_TO_GATE_MAP.get(next_stage)) else None
        status_data["phase_started_at"] = now_iso
        status_data["updated_at"] = now_iso
        status_data["cycle"] = cycle_name

        atomic_write_text(status_file, yaml.safe_dump(status_data, sort_keys=False, allow_unicode=True))

        return {
            "work_id": work_item_id,
            "previous_state": current_state_str,
            "state": next_legacy_state,
            "canonical_state": next_stage.value,
            "owner": next_owner,
            "active_agents": status_data["active_agents"],
            "current_gate": status_data.get("current_gate"),
            "responsible_agents": responsible_agents,
            "selectable_agents": selectable,
            "cycle": cycle_name,
            "transition_id": transition_id,
        }

    def _has_execution_proof(self, item_path: Path, work_item_id: Optional[str] = None) -> bool:
        """Verifies if work item has registered execution proof or receipts."""
        if work_item_id:
            try:
                exec_receipt = self.execution_service.repository.get_latest_execution_receipt(work_item_id)
                if exec_receipt and exec_receipt.test_exit_code == 0 and exec_receipt.diff_summary:
                    return True
            except Exception:
                pass

        receipts_dir = item_path / "receipts"
        if receipts_dir.is_dir():
            for r in receipts_dir.glob("*"):
                if "execution" in r.stem.lower() or "dispatch" in r.stem.lower():
                    return True
        evidence_dir = item_path / "evidence"
        if evidence_dir.is_dir():
            for e in evidence_dir.glob("*"):
                if any(k in e.name.lower() for k in ["diff", "patch", "test", "exec"]):
                    return True
        if (item_path / "implementation.diff").is_file():
            return True
        evaluation_dir = item_path / "evaluation"
        if evaluation_dir.is_dir():
            if any(evaluation_dir.glob("*.json")):
                return True
            if (evaluation_dir / "tdd").is_dir() and any((evaluation_dir / "tdd").glob("*.json")):
                return True
        return False

    def _has_review_proof(self, item_path: Path, work_item_id: Optional[str] = None) -> bool:
        """Verifies if work item has registered review proof or review receipts."""
        if work_item_id:
            try:
                reviews = self.execution_service.repository.get_validation_receipts(
                    work_item_id=work_item_id,
                    receipt_type="REVIEW",
                )
                if reviews:
                    latest = reviews[0]
                    exec_receipt = self.execution_service.repository.get_latest_execution_receipt(work_item_id)
                    # Enforce SoD: Author cannot review own work!
                    if exec_receipt and latest["agent_id"] == exec_receipt.agent_id:
                        return False
                    if latest.get("verdict") == "APPROVED":
                        return True
            except Exception:
                pass

        receipts_dir = item_path / "receipts"
        if receipts_dir.is_dir():
            for r in receipts_dir.glob("*"):
                if "review" in r.stem.lower():
                    return True
        if (item_path / "review-verdict.md").is_file():
            return True
        reviews_dir = item_path / "reviews"
        if reviews_dir.is_dir() and any(reviews_dir.glob("*")):
            return True
        evidence_dir = item_path / "evidence"
        if evidence_dir.is_dir():
            for e in evidence_dir.glob("*"):
                if "review" in e.name.lower():
                    return True
        return False

    def _check_handoff_status(
        self,
        item_path: Path,
        current_stage: Optional[LifecycleStage] = None,
        require_handoff: bool = False,
    ) -> Tuple[bool, str]:
        """Checks if the most recent handoff is acknowledged or accepted (resolves R0-LIFE-004)."""
        handoffs_dir = item_path / "handoffs"
        yaml_files = []
        if handoffs_dir.is_dir():
            yaml_files = sorted(handoffs_dir.glob("HANDOFF-*.yaml"), key=lambda f: f.stat().st_mtime, reverse=True)

        if not yaml_files:
            if require_handoff:
                return False, "Avanço bloqueado: handoff obrigatório ausente para este estágio."
            return True, "No handoffs"

        latest_handoff = yaml_files[0]
        try:
            data = yaml.safe_load(latest_handoff.read_text(encoding="utf-8")) or {}
            ack = data.get("acknowledgement", {})
            ack_status = str(ack.get("status", "")).lower()
            if ack_status in {"pending", "awaiting", ""}:
                return False, (
                    f"Avanço bloqueado: o handoff '{data.get('id', latest_handoff.stem)}' está com "
                    f"acknowledgement status '{ack_status}'. O destinatário deve confirmar (ack/accept) o handoff antes do avanço."
                )
            if ack_status in {"rejected", "reject"}:
                return False, (
                    f"Avanço bloqueado: o handoff '{data.get('id', latest_handoff.stem)}' foi rejeitado (status 'rejected')."
                )
            if ack_status in {"expired", "expire"}:
                return False, (
                    f"Avanço bloqueado: o handoff '{data.get('id', latest_handoff.stem)}' está expirado (status 'expired')."
                )
        except Exception:
            pass

        return True, "Handoff acknowledged"

    def _check_timebox_status(
        self,
        status_data: dict,
        current_stage: LifecycleStage,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """Checks if phase duration has exceeded the configured timebox (resolves R0-LIFE-013)."""
        phase_started_at_str = status_data.get("phase_started_at")
        if not phase_started_at_str:
            return True, "No phase started at"

        try:
            started_dt = datetime.fromisoformat(phase_started_at_str.replace("Z", "+00:00"))
        except Exception:
            return True, "Unparseable date"

        current_dt = now or _utc_now()
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        if current_dt.tzinfo is None:
            current_dt = current_dt.replace(tzinfo=timezone.utc)

        elapsed_seconds = (current_dt - started_dt).total_seconds()
        legacy_name = stage_to_legacy_name(current_stage)

        timeboxes = self.workflow_cfg.get("flow", {}).get("phase_timeboxes", {})
        limit_val = timeboxes.get(legacy_name)
        if limit_val is None:
            return True, "No limit defined"

        # If value is integer, default to minutes (or seconds if > 1000)
        # Check if expired (e.g. > 10 days = 864000s, or configured value)
        max_seconds = float(limit_val) * 60.0 if isinstance(limit_val, (int, float)) else 86400.0
        if elapsed_seconds > max_seconds:
            return False, (
                f"Timebox exceeded: current phase '{legacy_name}' elapsed {elapsed_seconds:.0f}s "
                f"exceeds limit of {max_seconds:.0f}s."
            )

        return True, "Within timebox"

    def initialize_work_item(
        self,
        work_item_id: str,
        project_id: str,
        kind: Optional[Union[str, Any]] = None,
        cycle_id: Optional[str] = None,
        initiated_by: str = "00-delivery-orchestrator",
        owner_role: Optional[str] = None,
        now_iso: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        item_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Atomically initializes a new work item at its canonical lifecycle entry stage.

        Guarantees:
        - Resolves delivery cycle deterministically.
        - Resolves canonical entry stage (LifecycleStage.INTAKE or cycle entry).
        - Single Authority: strictly writes via R4 LifecycleRepository.
        - Idempotent: repeated initialization with matching parameters returns existing state.
        - Fails closed on conflicting reinitialization (different project, cycle, or stage).

        Raises:
            LifecycleError: On conflicting reinitialization or invalid inputs.
            ConfigurationError: If resolved cycle or entry stage is unknown.
        """
        if not work_item_id or not str(work_item_id).strip():
            raise LifecycleError("work_item_id must not be empty")
        if not project_id or not str(project_id).strip():
            raise LifecycleError("project_id must not be empty")

        norm_id = str(work_item_id).strip()
        norm_project = str(project_id).strip()

        # 1. Deterministic Cycle Resolution
        if cycle_id and str(cycle_id).strip():
            resolved_cycle = str(cycle_id).strip()
        else:
            resolved_cycle = resolve_cycle_for_kind(kind or "feature")

        if resolved_cycle not in self.cycles_cfg.get("cycles", {}) and resolved_cycle not in CANONICAL_CYCLES:
            raise ConfigurationError(
                f"Cycle '{resolved_cycle}' is not defined in configuration or canonical registry"
            )

        # 2. Canonical Entry Stage Resolution (Always LifecycleStage.INTAKE by default)
        if resolved_cycle in CANONICAL_CYCLES and len(CANONICAL_CYCLES[resolved_cycle]) > 0:
            entry_stage = CANONICAL_CYCLES[resolved_cycle][0]
        else:
            entry_stage = LifecycleStage.INTAKE

        entry_stage_val = entry_stage.value

        # 3. Owner Role Resolution
        if owner_role and str(owner_role).strip():
            resolved_owner = str(owner_role).strip()
        else:
            policy = get_stage_policy(entry_stage)
            resolved_owner = policy.owner_role

        now_str = now_iso or _utc_now_iso()

        # 4. Atomic Concurrency, Idempotency & Conflict Check
        with self.repository.connection() as conn:
            with conn:
                existing = self.repository.get_current_state(norm_id, conn=conn)
                if existing:
                    if existing["project_id"] != norm_project:
                        raise LifecycleError(
                            f"Conflicting initialization: work item '{norm_id}' already belongs to project "
                            f"'{existing['project_id']}', cannot reinitialize in '{norm_project}'"
                        )
                    if existing["cycle_id"] != resolved_cycle:
                        raise LifecycleError(
                            f"Conflicting initialization: work item '{norm_id}' is already initialized with cycle "
                            f"'{existing['cycle_id']}', cannot reinitialize with '{resolved_cycle}'"
                        )
                    if existing["current_stage"] != entry_stage_val:
                        raise LifecycleError(
                            f"Conflicting initialization: work item '{norm_id}' has already advanced to stage "
                            f"'{existing['current_stage']}', cannot reinitialize at '{entry_stage_val}'"
                        )
                    # Idempotent match: do not duplicate history or re-emit events
                    return {
                        "work_item_id": norm_id,
                        "project_id": norm_project,
                        "cycle_id": resolved_cycle,
                        "current_stage": entry_stage_val,
                        "stage": entry_stage_val,
                        "legacy_stage": stage_to_legacy_name(entry_stage),
                        "owner_role": existing["owner_role"],
                        "was_created": False,
                    }

                # 5. Persist Lifecycle State and Transition History
                transition_id = str(uuid.uuid4())
                idempotency_key = f"INIT:{norm_project}:{norm_id}"
                trans_rec, was_created = self.repository.record_transition(
                    conn=conn,
                    transition_id=transition_id,
                    work_item_id=norm_id,
                    project_id=norm_project,
                    cycle_id=resolved_cycle,
                    from_stage="NONE",
                    to_stage=entry_stage_val,
                    initiated_by=initiated_by,
                    owner_role=resolved_owner,
                    idempotency_key=idempotency_key,
                    payload=metadata or {},
                    now_iso=now_str,
                )

                # 6. Emit agent_squad.work_item.initialized Domain Event into Outbox
                event_id = str(uuid.uuid4())
                event_type = "agent_squad.work_item.initialized"
                ev_payload = {
                    "work_item_id": norm_id,
                    "project_id": norm_project,
                    "cycle_id": resolved_cycle,
                    "stage": entry_stage_val,
                    "owner_role": resolved_owner,
                    "transition_id": transition_id,
                    "initialized_at": now_str,
                }
                ev_seed = f"{event_type}:{norm_id}:{transition_id}"
                ev_idem_key = hashlib.sha256(ev_seed.encode("utf-8")).hexdigest()
                payload_hash = hashlib.sha256(canonical_json(ev_payload).encode("utf-8")).hexdigest()

                conn.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        event_id, event_type, work_item_id, project_id, source,
                        correlation_id, causation_id, idempotency_key, timestamp,
                        payload, payload_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        event_type,
                        norm_id,
                        norm_project,
                        "CanonicalLifecycleService",
                        transition_id,
                        transition_id,
                        ev_idem_key,
                        now_str,
                        canonical_json(ev_payload),
                        payload_hash,
                        now_str,
                    ),
                )

        # 7. Update status.yaml projection if file exists
        path = item_path or self._resolve_item_path(norm_id, norm_project)
        status_file = path / "status.yaml"
        if status_file.is_file():
            try:
                st = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
                st["stage"] = entry_stage_val
                st["state"] = stage_to_legacy_name(entry_stage)
                st["cycle"] = resolved_cycle
                st["owner"] = resolved_owner
                st["updated_at"] = now_str
                atomic_write_text(status_file, yaml.safe_dump(st, sort_keys=False, allow_unicode=True))
            except Exception as e:
                logger.debug(f"Could not update status.yaml for {norm_id}: {e}")

        return {
            "work_item_id": norm_id,
            "project_id": norm_project,
            "cycle_id": resolved_cycle,
            "current_stage": entry_stage_val,
            "stage": entry_stage_val,
            "legacy_stage": stage_to_legacy_name(entry_stage),
            "owner_role": resolved_owner,
            "transition_id": transition_id,
            "was_created": was_created,
        }
