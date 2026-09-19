"""
O que é: Motor Contínuo de Orquestração Reativo do Agent Squad.
Responsabilidade: Processar eventos de ciclo de vida (handoffs, gates, transições),
gerenciar FSM reativa, aplicar Circuit Breaker de 2 retries e interceptar pontos de injeção de governança.
Pra que serve: Permitir transições autônomas seguras no SDLC para Golden Paths sem quebra de SoD.
Comportamento em falha: Incrementa contador de retries; após 2 falhas, abre o Circuit Breaker
(HALTED_CIRCUIT_BREAKER) e emite log contextual sem corromper status.yaml.
Conexões: Acionado por CLI run-continuous ou create_handoff; consulta status.yaml, workflow.yaml,
gate-decisions/ e interage com AgentSquad e LocalAgentDB.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path
from typing import Any, Callable
import uuid

from agent_squad import AgentSquad, now, read_yaml
from governed_io import atomic_write_text

logger = logging.getLogger(__name__)

EVENT_HANDOFF_CREATED = "EVENT_HANDOFF_CREATED"
EVENT_GATE_EVALUATED = "EVENT_GATE_EVALUATED"
EVENT_STATE_ADVANCED = "EVENT_STATE_ADVANCED"
EVENT_CYCLE_HALTED = "EVENT_CYCLE_HALTED"
EVENT_CIRCUIT_BREAKER_TRIPPED = "EVENT_CIRCUIT_BREAKER_TRIPPED"
EVENT_CIRCUIT_BREAKER_RESET = "EVENT_CIRCUIT_BREAKER_RESET"


@dataclass
class EngineEvent:
    """Evento emitido e processado pelo ContinuousTriggerEngine."""

    event_id: str
    event_type: str
    work_item_id: str
    payload: dict[str, Any]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        """Converte a instância para dicionário."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineEvent:
        """Reconstrói uma instância de EngineEvent a partir de um dicionário."""
        return cls(
            event_id=str(data["event_id"]),
            event_type=str(data["event_type"]),
            work_item_id=str(data["work_item_id"]),
            payload=dict(data.get("payload", {})),
            timestamp=str(data.get("timestamp", now())),
        )


class CircuitBreakerState:
    """Gerencia o estado de proteção e retries do Circuit Breaker para um work item."""

    def __init__(
        self,
        work_item_id: str,
        failure_count: int = 0,
        max_retries: int = 2,
        state: str = "CLOSED",
        last_failure_reason: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        self.work_item_id = work_item_id
        self.failure_count = failure_count
        self.max_retries = max_retries
        self.state = state
        self.last_failure_reason = last_failure_reason
        self.updated_at = updated_at or now()

    def record_failure(self, reason: str) -> bool:
        """Registra uma falha consecutiva. Retorna True se o circuito desarmou (OPEN)."""
        self.failure_count += 1
        self.last_failure_reason = reason
        self.updated_at = now()
        if self.failure_count >= self.max_retries:
            self.state = "OPEN"
            return True
        return False

    def record_success(self) -> None:
        """Registra sucesso na transição, rearmando o contador para zero e estado CLOSED."""
        self.failure_count = 0
        self.state = "CLOSED"
        self.last_failure_reason = None
        self.updated_at = now()

    def is_open(self) -> bool:
        """Verifica se o circuito está aberto (bloqueado para execuções automáticas)."""
        return self.state == "OPEN"

    def reset(self) -> None:
        """Reseta o circuit breaker para o estado CLOSED com contador zerado."""
        self.state = "CLOSED"
        self.failure_count = 0
        self.last_failure_reason = None
        self.updated_at = now()

    def to_dict(self) -> dict[str, Any]:
        """Serializa o estado do Circuit Breaker para dicionário."""
        return {
            "work_item_id": self.work_item_id,
            "failure_count": self.failure_count,
            "max_retries": self.max_retries,
            "state": self.state,
            "last_failure_reason": self.last_failure_reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CircuitBreakerState:
        """Desserializa um dicionário para CircuitBreakerState."""
        return cls(
            work_item_id=str(data["work_item_id"]),
            failure_count=int(data.get("failure_count", 0)),
            max_retries=int(data.get("max_retries", 2)),
            state=str(data.get("state", "CLOSED")),
            last_failure_reason=data.get("last_failure_reason"),
            updated_at=data.get("updated_at"),
        )


class POInjectionGuard:
    """Guarda de injeção para o Product Owner (G1-Product)."""

    def validate_g1_clearance(self, squad: AgentSquad, work_item_id: str) -> dict[str, Any]:
        """Verifica se itens com risco medium/high/critical possuem G1 aprovado com human approval."""
        item_path = squad._item(work_item_id)
        status_path = item_path / "status.yaml"
        if not status_path.is_file():
            return {
                "allowed": False,
                "status": "STATUS_NOT_FOUND",
                "reason": f"Arquivo status.yaml não encontrado em {item_path}",
            }

        status = read_yaml(status_path)
        risk = str(status.get("risk", "low")).lower()

        if risk not in {"medium", "high", "critical"}:
            return {
                "allowed": True,
                "status": "LOW_RISK_BYPASS",
                "reason": "Risco low não exige G1 humano bloqueante",
            }

        decisions_dir = item_path / "gate-decisions"
        valid_g1_names = {"g1-product", "gt-entry", "g1_product", "gt_entry"}

        if decisions_dir.is_dir():
            for dec_file in decisions_dir.glob("*.yaml"):
                try:
                    dec_data = read_yaml(dec_file)
                    dec_gate_id = str(dec_data.get("gate_id", "")).lower()
                    file_matches = any(g in dec_file.stem.lower() for g in valid_g1_names)
                    if dec_gate_id in valid_g1_names or file_matches:
                        decision_val = str(dec_data.get("decision", "")).lower()
                        if decision_val in {"approved", "pass"}:
                            human_app = dec_data.get("human_approval", {})
                            if isinstance(human_app, dict) and human_app.get("status") == "approved":
                                return {
                                    "allowed": True,
                                    "status": "G1_APPROVED",
                                    "reason": "Gate G1-product aprovado formalmente com aprovação humana",
                                }
                except Exception as exc:
                    logger.debug("Erro ao ler decisão %s: %s", dec_file, exc)

        return {
            "allowed": False,
            "status": "AWAITING_PO_APPROVAL",
            "reason": "Aprovação do Product Owner e humana necessária para avanço do portão G1-product",
        }


class AgileCoachSizingGuard:
    """Guarda de proteção cognitiva (Max 8 Story Points na regra de Fibonacci)."""

    def validate_sizing(self, status_data: dict[str, Any]) -> dict[str, Any]:
        """Bloqueia itens com story_points > 8 exigindo divisão vertical pelo agile-coach."""
        points = status_data.get("story_points")
        if points is not None and isinstance(points, (int, float)) and points > 8:
            return {
                "allowed": False,
                "status": "BLOCKED_SIZING_EXCEEDED",
                "story_points": points,
                "action_required": (
                    f"Fatiamento vertical necessário pelo agile-coach: item possui {points} Story Points, "
                    "violando a regra de proteção cognitiva de no máximo 8 pontos."
                ),
            }
        return {
            "allowed": True,
            "status": "SIZING_ALLOWED",
            "story_points": points,
        }


class ContinuousTriggerEngine:
    """Motor contínuo de gatilhos e orquestração reativa do SDLC."""

    def __init__(self, squad: AgentSquad, max_retries: int | None = None) -> None:
        self.squad = squad
        if max_retries is not None:
            self.max_retries = max_retries
        else:
            cfg = squad.workflow.get("continuous_engine", {})
            self.max_retries = int(cfg.get("max_retries_per_check", 2))

        self.handlers: dict[str, list[Callable[[EngineEvent], dict[str, Any]]]] = {}
        self.circuit_breakers: dict[str, CircuitBreakerState] = {}
        self.po_guard = POInjectionGuard()
        self.sizing_guard = AgileCoachSizingGuard()
        self._register_default_handlers()

    def _register_default_handlers(self) -> None:
        """Registra handlers padrão para eventos centrais do SDLC."""
        self.register_handler(EVENT_HANDOFF_CREATED, self.handle_handoff_created)
        self.register_handler(EVENT_GATE_EVALUATED, self.handle_gate_evaluated)

    def register_handler(
        self, event_type: str, handler: Callable[[EngineEvent], dict[str, Any]]
    ) -> None:
        """Registra um callback listener para um tipo de evento."""
        if event_type not in self.handlers:
            self.handlers[event_type] = []
        self.handlers[event_type].append(handler)

    def dispatch(self, event: EngineEvent) -> dict[str, Any]:
        """Despacha um evento para todos os ouvintes registrados."""
        self._save_event_log(event)
        handlers = self.handlers.get(event.event_type, [])
        if not handlers:
            return {
                "status": "NO_HANDLERS",
                "event_type": event.event_type,
                "message": f"Nenhum handler registrado para {event.event_type}",
            }

        last_result: dict[str, Any] = {}
        for handler in handlers:
            last_result = handler(event)
        return last_result

    def _cb_file(self, work_item_id: str) -> Path:
        """Retorna o caminho do arquivo persistido do Circuit Breaker."""
        item_path = self.squad._item(work_item_id)
        return item_path / ".circuit_breaker.json"

    def get_circuit_breaker(self, work_item_id: str) -> CircuitBreakerState:
        """Carrega ou instancia o CircuitBreakerState do work item."""
        if work_item_id in self.circuit_breakers:
            return self.circuit_breakers[work_item_id]

        cb_file = self._cb_file(work_item_id)
        if cb_file.is_file():
            try:
                data = json.loads(cb_file.read_text(encoding="utf-8"))
                cb = CircuitBreakerState.from_dict(data)
                self.circuit_breakers[work_item_id] = cb
                return cb
            except Exception as exc:
                logger.warning("Falha ao ler circuit breaker de %s: %s", cb_file, exc)

        cb = CircuitBreakerState(work_item_id=work_item_id, max_retries=self.max_retries)
        self.circuit_breakers[work_item_id] = cb
        return cb

    def _save_circuit_breaker(self, cb: CircuitBreakerState) -> None:
        """Persiste o estado do Circuit Breaker em disco atomicamente."""
        self.circuit_breakers[cb.work_item_id] = cb
        try:
            cb_file = self._cb_file(cb.work_item_id)
            atomic_write_text(cb_file, json.dumps(cb.to_dict(), indent=2))
        except Exception as exc:
            logger.warning("Erro ao persistir circuit breaker para %s: %s", cb.work_item_id, exc)

    def trip_circuit_breaker(self, work_item_id: str, reason: str) -> None:
        """Abre manualmente o circuit breaker para um work item."""
        cb = self.get_circuit_breaker(work_item_id)
        cb.state = "OPEN"
        cb.last_failure_reason = reason
        cb.updated_at = now()
        self._save_circuit_breaker(cb)

    def reset_circuit_breaker(self, work_item_id: str) -> None:
        """Rearma o circuit breaker para um work item."""
        cb = self.get_circuit_breaker(work_item_id)
        cb.reset()
        self._save_circuit_breaker(cb)

    def _save_event_log(self, event: EngineEvent) -> None:
        """Persiste o log do evento em events/events.jsonl do work item."""
        try:
            item_path = self.squad._item(event.work_item_id)
            events_dir = item_path / "events"
            events_dir.mkdir(parents=True, exist_ok=True)
            log_file = events_dir / "events.jsonl"
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict()) + "\n")
        except Exception as exc:
            logger.debug("Não foi possível persistir evento %s: %s", event.event_id, exc)

    def emit_event(
        self, event_type: str, work_item_id: str, payload: dict[str, Any]
    ) -> EngineEvent:
        """Cria, registra e despacha um novo evento."""
        event = EngineEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            work_item_id=work_item_id,
            payload=payload,
            timestamp=now(),
        )
        self.dispatch(event)
        return event

    def handle_handoff_created(self, event: EngineEvent) -> dict[str, Any]:
        """Processa evento de handoff criado tentando avançar o estado se elegível."""
        work_item_id = event.work_item_id
        cb = self.get_circuit_breaker(work_item_id)

        if cb.is_open():
            return {
                "status": "HALTED_CIRCUIT_BREAKER",
                "message": f"Circuit breaker aberto para {work_item_id}: {cb.last_failure_reason}",
            }

        item_path = self.squad._item(work_item_id)
        status_path = item_path / "status.yaml"
        if not status_path.is_file():
            return {"status": "ERROR", "message": f"status.yaml não encontrado em {item_path}"}

        status = read_yaml(status_path)
        current_state = status.get("state")
        if current_state == "done":
            return {"status": "ALREADY_DONE", "message": "Work item já concluído"}

        # Guarda de Sizing
        sizing_check = self.sizing_guard.validate_sizing(status)
        if not sizing_check["allowed"]:
            return {
                "status": "BLOCKED_SIZING_EXCEEDED",
                "message": sizing_check["action_required"],
            }

        # Guarda de PO para avanço além de blueprint
        if current_state == "blueprint":
            po_check = self.po_guard.validate_g1_clearance(self.squad, work_item_id)
            if not po_check["allowed"]:
                return {
                    "status": "AWAITING_PO_APPROVAL",
                    "message": po_check["reason"],
                }

        # Guarda de Handoff Acknowledgement (R0-LIFE-004)
        handoff_id = event.payload.get("handoff_id")
        handoff_file = None
        if handoff_id:
            candidate = item_path / "handoffs" / f"{handoff_id}.yaml"
            if candidate.is_file():
                handoff_file = candidate

        if handoff_file is None:
            h_dir = item_path / "handoffs"
            if h_dir.is_dir():
                h_files = sorted(h_dir.glob("HANDOFF-*.yaml"), key=lambda f: f.stat().st_mtime, reverse=True)
                if h_files:
                    handoff_file = h_files[0]

        if handoff_file and handoff_file.is_file():
            try:
                h_data = read_yaml(handoff_file)
                ack = h_data.get("acknowledgement", {})
                ack_status = str(ack.get("status", "")).lower()
                target_recipient = event.payload.get("to") or h_data.get("to")
                if ack_status in {"pending", "awaiting", ""} and target_recipient in {
                    "delivery-orchestrator",
                    "00-delivery-orchestrator",
                }:
                    try:
                        self.squad.ack_handoff(
                            work_item_id, h_data.get("id", handoff_file.stem), target_recipient
                        )
                        ack_status = "accepted"
                    except Exception as ack_err:
                        logger.debug("Falha no auto-ack pelo delivery-orchestrator: %s", ack_err)

                if ack_status in {"pending", "awaiting", ""}:
                    return {
                        "status": "AWAITING_HANDOFF_ACK",
                        "message": (
                            f"Handoff '{h_data.get('id', handoff_file.stem)}' pendente de acknowledgement. "
                            "Avanço não permitido até aceitação formal do destinatário."
                        ),
                    }
            except Exception as exc:
                logger.warning("Falha ao verificar handoff acknowledgement: %s", exc)

        try:
            advance_result = self.squad.advance_state(work_item_id)
            cb.record_success()
            self._save_circuit_breaker(cb)
            return {
                "status": "STATE_ADVANCED",
                "new_state": advance_result.get("state", ""),
                "details": advance_result,
            }
        except Exception as exc:
            tripped = cb.record_failure(str(exc))
            self._save_circuit_breaker(cb)
            if tripped:
                return {
                    "status": "HALTED_CIRCUIT_BREAKER",
                    "message": f"Circuit breaker desarmou após 2 falhas consecutivas: {exc}",
                }
            return {
                "status": "STEP_FAILED",
                "message": f"Falha ao avançar estado: {exc}",
            }

    def handle_gate_evaluated(self, event: EngineEvent) -> dict[str, Any]:
        """Processa evento de gate avaliado."""
        work_item_id = event.work_item_id
        decision = event.payload.get("decision", "").lower()
        if decision in {"approved", "pass"}:
            return self.handle_handoff_created(event)
        elif decision in {"rejected", "changes_requested", "fail"}:
            cb = self.get_circuit_breaker(work_item_id)
            reason = event.payload.get("reason", f"Gate rejeitado com decisão: {decision}")
            tripped = cb.record_failure(reason)
            self._save_circuit_breaker(cb)
            if tripped:
                return {
                    "status": "HALTED_CIRCUIT_BREAKER",
                    "message": f"Circuit breaker desarmou após falha em avaliação de gate: {reason}",
                }
            return {
                "status": "GATE_FAILED",
                "message": reason,
            }
        return {"status": "IGNORED", "message": f"Decisão de gate ignorada: {decision}"}

    def run_continuous(
        self, work_item_id: str, max_steps: int = 10, dry_run: bool = False
    ) -> dict[str, Any]:
        """Executa o ciclo contínuo de avanço de estados respeitando Circuit Breaker e Guards."""
        cb = self.get_circuit_breaker(work_item_id)

        if cb.is_open():
            return {
                "status": "HALTED_CIRCUIT_BREAKER",
                "work_item_id": work_item_id,
                "steps_executed": 0,
                "message": f"Circuit breaker está aberto para {work_item_id}: {cb.last_failure_reason}",
            }

        item_path = self.squad._item(work_item_id)
        status_path = item_path / "status.yaml"
        if not status_path.is_file():
            return {
                "status": "ERROR",
                "work_item_id": work_item_id,
                "steps_executed": 0,
                "message": f"status.yaml ausente em {item_path}",
            }

        steps = 0
        history: list[dict[str, Any]] = []

        while steps < max_steps:
            status = read_yaml(status_path)
            current_state = status.get("state")

            if current_state == "done":
                status_code = "ALREADY_DONE" if steps == 0 else "COMPLETED_IDEMPOTENT"
                return {
                    "status": status_code,
                    "work_item_id": work_item_id,
                    "current_state": "done",
                    "steps_executed": steps,
                    "history": history,
                    "message": "Work item concluído no estado terminal 'done'.",
                }

            # Avalia Sizing Guard
            sizing_check = self.sizing_guard.validate_sizing(status)
            if not sizing_check["allowed"]:
                return {
                    "status": "BLOCKED_SIZING_EXCEEDED",
                    "work_item_id": work_item_id,
                    "current_state": current_state,
                    "steps_executed": steps,
                    "message": sizing_check["action_required"],
                }

            # Avalia PO Guard
            if current_state == "blueprint":
                po_check = self.po_guard.validate_g1_clearance(self.squad, work_item_id)
                if not po_check["allowed"]:
                    return {
                        "status": "AWAITING_PO_APPROVAL",
                        "work_item_id": work_item_id,
                        "current_state": current_state,
                        "steps_executed": steps,
                        "message": po_check["reason"],
                    }

            if dry_run:
                history.append({"step": steps + 1, "action": f"simulated_advance from {current_state}"})
                steps += 1
                break

            # Validação determinística de pré-requisitos canônicos (R0-LIFE-011)
            from runtime.lifecycle import CanonicalLifecycleService
            lifecycle_service = CanonicalLifecycleService(root_path=self.squad.root)
            can_trans, trans_reason = lifecycle_service.can_transition(work_item_id, item_path=item_path)
            if not can_trans:
                tripped = cb.record_failure(trans_reason)
                self._save_circuit_breaker(cb)
                if tripped:
                    return {
                        "status": "HALTED_CIRCUIT_BREAKER",
                        "work_item_id": work_item_id,
                        "current_state": current_state,
                        "steps_executed": steps,
                        "history": history,
                        "agent_dispatches": [],
                        "message": f"Circuit breaker abriu após falhas consecutivas: {trans_reason}",
                    }
                return {
                    "status": "STEP_FAILED",
                    "work_item_id": work_item_id,
                    "current_state": current_state,
                    "steps_executed": steps,
                    "history": history,
                    "agent_dispatches": [],
                    "message": f"Falha ao executar avanço de estado: {trans_reason}",
                }

            try:
                advance_result = self.squad.advance_state(work_item_id)
                cb.record_success()
                self._save_circuit_breaker(cb)
                steps += 1
                new_state = advance_result.get("state") or advance_result.get("new_state", "")
                history.append({
                    "step": steps,
                    "from_state": current_state,
                    "to_state": new_state,
                })
            except Exception as exc:
                tripped = cb.record_failure(str(exc))
                self._save_circuit_breaker(cb)
                if tripped:
                    return {
                        "status": "HALTED_CIRCUIT_BREAKER",
                        "work_item_id": work_item_id,
                        "current_state": current_state,
                        "steps_executed": steps,
                        "history": history,
                        "agent_dispatches": [],
                        "message": f"Circuit breaker abriu após falhas consecutivas: {exc}",
                    }
                return {
                    "status": "STEP_FAILED",
                    "work_item_id": work_item_id,
                    "current_state": current_state,
                    "steps_executed": steps,
                    "history": history,
                    "agent_dispatches": [],
                    "message": f"Falha ao executar avanço de estado: {exc}",
                }

        status = read_yaml(status_path)
        return {
            "status": "MAX_STEPS_REACHED",
            "work_item_id": work_item_id,
            "current_state": status.get("state"),
            "steps_executed": steps,
            "history": history,
            "agent_dispatches": [],
            "message": f"Limite de {max_steps} passos atingido.",
        }
