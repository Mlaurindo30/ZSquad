"""Controlador de execução do orquestrador.

O `OrchestrationController` recebe um `FailureEvent` (resultado de uma
chamada de subagente, dispatch de provider, ou gate rejeitado), classifica
a causa via `scripts.error_classifier.classify_error`, decide uma
`RecoveryAction`, respeita o orçamento anti-loop por alvo, persiste o
evento e a decisão no SQLite (com binds de parâmetros), e devolve um
`RecoveryDecision` observável.

Critérios:
- Toda query usa binds (`?`); identificadores vêm da whitelist.
- O método `decide()` é determinístico e livre de rede.
- O método `record_event()` é idempotente (mesma entrada, mesmo efeito).
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from scripts.error_classifier import (
    ClassifiedError,
    ErrorClassifier,
    FailoverReason,
    classify_error,
)


class RecoveryAction(str, enum.Enum):
    """Ação determinada pelo controlador após classificar uma falha."""

    RETRY_SAME = "retry-same"
    RETRY_COMPRESSED = "retry-compressed"
    ROTATE_AGENT = "rotate-agent"
    ROTATE_PROVIDER = "rotate-provider"
    ESCALATE = "escalate"
    BLOCKED = "blocked"


_MAX_RETRIES_PER_TARGET = 2
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 8.0


# Mapeamento FailoverReason -> RecoveryAction. Tabela única; sem redescoberta em runtime.
ACTION_BY_REASON: dict[FailoverReason, RecoveryAction] = {
    FailoverReason.auth: RecoveryAction.ESCALATE,
    FailoverReason.auth_permanent: RecoveryAction.ESCALATE,
    FailoverReason.billing: RecoveryAction.ESCALATE,
    FailoverReason.rate_limit: RecoveryAction.RETRY_SAME,
    FailoverReason.upstream_rate_limit: RecoveryAction.RETRY_SAME,
    FailoverReason.overloaded: RecoveryAction.RETRY_SAME,
    FailoverReason.server_error: RecoveryAction.ROTATE_AGENT,
    FailoverReason.timeout: RecoveryAction.ROTATE_AGENT,
    FailoverReason.ssl_cert_verification: RecoveryAction.BLOCKED,
    FailoverReason.context_overflow: RecoveryAction.RETRY_COMPRESSED,
    FailoverReason.payload_too_large: RecoveryAction.RETRY_COMPRESSED,
    FailoverReason.image_too_large: RecoveryAction.RETRY_COMPRESSED,
    FailoverReason.unknown: RecoveryAction.ESCALATE,
}


@dataclass(frozen=True)
class FailureEvent:
    """Evento de falha observado pelo host."""

    project_id: str
    work_item_id: str | None
    target_key: str
    phase: str
    agent_id: str | None
    provider: str | None
    model: str | None
    error_message: str
    status_code: int | None = None
    response_text: str = ""
    exc: Optional[BaseException] = None
    recorded_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class RecoveryDecision:
    """Decisão retornada por `decide()`."""

    action: RecoveryAction
    reason: FailoverReason
    attempt: int
    next_agent_id: str | None
    next_provider: str | None
    backoff_seconds: float
    rationale: str
    should_compress: bool
    should_record_event: bool = True


class OrchestrationController:
    """Decide a próxima ação após uma falha observada."""

    def __init__(
        self,
        classifier: ErrorClassifier | None = None,
        max_retries: int = _MAX_RETRIES_PER_TARGET,
        backoff_base: float = _BACKOFF_BASE_SECONDS,
        backoff_cap: float = _BACKOFF_CAP_SECONDS,
    ) -> None:
        self._classifier = classifier or ErrorClassifier()
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._attempts: dict[tuple[str, str], int] = {}

    def classify(self, event: FailureEvent) -> ClassifiedError:
        return self._classifier.classify(
            exc=event.exc,
            status_code=event.status_code,
            response_text=event.response_text or event.error_message,
            provider=event.provider,
        )

    def _attempt_count(self, project_id: str, target_key: str) -> int:
        return self._attempts.get((project_id, target_key), 0)

    def _bump_attempts(self, project_id: str, target_key: str) -> int:
        key = (project_id, target_key)
        current = self._attempts.get(key, 0) + 1
        self._attempts[key] = current
        return current

    def reset_attempts(self, project_id: str, target_key: str) -> None:
        self._attempts.pop((project_id, target_key), None)

    def _backoff(self, attempt: int) -> float:
        if attempt <= 0:
            return 0.0
        delay = self._backoff_base * (2 ** (attempt - 1))
        return min(delay, self._backoff_cap)

    def decide(
        self,
        event: FailureEvent,
        *,
        fallback_agent_id: str | None = None,
        fallback_provider: str | None = None,
    ) -> RecoveryDecision:
        classified = self.classify(event)
        action = ACTION_BY_REASON.get(classified.reason, RecoveryAction.ESCALATE)
        attempt = self._bump_attempts(event.project_id, event.target_key)
        rationale = (
            f"reason={classified.reason.value} "
            f"action={action.value} "
            f"attempt={attempt}"
        )

        # Orçamento anti-loop: após max_retries, decide BLOCKED independente da razão.
        if attempt > self._max_retries and action not in (RecoveryAction.ESCALATE, RecoveryAction.BLOCKED):
            action = RecoveryAction.BLOCKED

        # Razões permanentes (auth/billing/ssl) nunca viram retry.
        if action in (RecoveryAction.ESCALATE, RecoveryAction.BLOCKED):
            return RecoveryDecision(
                action=action,
                reason=classified.reason,
                attempt=attempt,
                next_agent_id=None,
                next_provider=None,
                backoff_seconds=0.0,
                rationale=rationale + " permanent",
                should_compress=False,
            )

        next_agent = None
        next_provider = None
        if action == RecoveryAction.ROTATE_AGENT:
            next_agent = fallback_agent_id
        elif action == RecoveryAction.ROTATE_PROVIDER:
            next_provider = fallback_provider

        backoff = self._backoff(attempt) if action == RecoveryAction.RETRY_SAME else 0.0

        return RecoveryDecision(
            action=action,
            reason=classified.reason,
            attempt=attempt,
            next_agent_id=next_agent,
            next_provider=next_provider,
            backoff_seconds=backoff,
            rationale=rationale,
            should_compress=action == RecoveryAction.RETRY_COMPRESSED,
        )

    # ------------------------------------------------------------------
    # Persistência opcional (Etapa 6). Mantida aqui para reduzir superfície
    # de mudança quando Etapa 6 introduzir `record_recovery_event`.
    # ------------------------------------------------------------------

    def record_event(
        self,
        db_path: Path,
        decision: RecoveryDecision,
        event: FailureEvent,
    ) -> int:
        """Insere o par (FailureEvent, RecoveryDecision) na tabela canônica
        ``ops_recovery`` via ``LocalAgentDB.record_recovery_event``.

        Tabela garantida por ``LocalAgentDB`` (whitelist + binds); este método
        apenas delega para a API canônica.
        """
        from scripts.local_agent_db import LocalAgentDB

        db = LocalAgentDB(db_path, project_id=event.project_id)
        return db.record_recovery_event(
            target_key=event.target_key,
            work_item_id=event.work_item_id,
            agent_id=event.agent_id,
            provider=event.provider,
            model=event.model,
            phase=event.phase,
            reason=decision.reason.value,
            action=decision.action.value,
            attempt=decision.attempt,
            backoff_seconds=decision.backoff_seconds,
            error_message=event.error_message,
            rationale=decision.rationale,
        )


def decide_from_exception(
    exc: BaseException,
    *,
    project_id: str,
    target_key: str,
    phase: str,
    work_item_id: str | None = None,
    agent_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    controller: OrchestrationController | None = None,
) -> tuple[RecoveryDecision, FailureEvent]:
    """Atalho: monta FailureEvent e chama `controller.decide()`."""
    controller = controller or OrchestrationController()
    event = FailureEvent(
        project_id=project_id,
        work_item_id=work_item_id,
        target_key=target_key,
        phase=phase,
        agent_id=agent_id,
        provider=provider,
        model=model,
        error_message=f"{type(exc).__name__}: {exc}",
        exc=exc,
    )
    return controller.decide(event), event


__all__ = [
    "ACTION_BY_REASON",
    "FailureEvent",
    "OrchestrationController",
    "RecoveryAction",
    "RecoveryDecision",
    "decide_from_exception",
]
