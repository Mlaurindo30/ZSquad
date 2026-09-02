"""Cobertura determinística do orquestrador (Etapa 3).

Sem rede, sem LLM real, sem SQLite remoto. Usa `tmp_path` para isolar
estado entre testes e stub para `time.time` quando necessário.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.error_classifier import FailoverReason
from scripts.orchestration_controller import (
    ACTION_BY_REASON,
    FailureEvent,
    OrchestrationController,
    RecoveryAction,
    RecoveryDecision,
    decide_from_exception,
)


def _event(target_key: str = "agent-42/TASK-1", **overrides) -> FailureEvent:
    base = dict(
        project_id="alpha",
        work_item_id="TASK-1",
        target_key=target_key,
        phase="dispatch",
        agent_id="agent-42",
        provider="omniroute",
        model="agy/gemini-3.6-flash-low",
        error_message="boom",
    )
    base.update(overrides)
    return FailureEvent(**base)


def test_action_mapping_covers_every_failover_reason():
    # Toda entrada do enum FailoverReason deve ter uma decisão.
    for reason in FailoverReason:
        assert reason in ACTION_BY_REASON, f"missing mapping for {reason}"


MESSAGE_BY_REASON = {
    FailoverReason.rate_limit: "429 rate limit exceeded",
    FailoverReason.timeout: "ReadTimeoutError: timeout",
    FailoverReason.server_error: "500 server error",
    FailoverReason.auth: "401 unauthorized",
    FailoverReason.billing: "402 billing",
    FailoverReason.ssl_cert_verification: "ssl certificate verify failed",
    FailoverReason.context_overflow: "context length exceeded",
    FailoverReason.payload_too_large: "413 payload too large",
}


@pytest.mark.parametrize(
    "reason,expected",
    [
        (FailoverReason.rate_limit, RecoveryAction.RETRY_SAME),
        (FailoverReason.timeout, RecoveryAction.ROTATE_AGENT),
        (FailoverReason.server_error, RecoveryAction.ROTATE_AGENT),
        (FailoverReason.auth, RecoveryAction.ESCALATE),
        (FailoverReason.billing, RecoveryAction.ESCALATE),
        (FailoverReason.ssl_cert_verification, RecoveryAction.BLOCKED),
        (FailoverReason.context_overflow, RecoveryAction.RETRY_COMPRESSED),
        (FailoverReason.payload_too_large, RecoveryAction.RETRY_COMPRESSED),
    ],
)
def test_classify_to_action_is_deterministic(reason, expected):
    c = OrchestrationController()
    decision = c.decide(
        _event(error_message=MESSAGE_BY_REASON[reason], target_key=f"k-{reason.name}")
    )
    assert decision.action == expected
    if expected in (RecoveryAction.ESCALATE, RecoveryAction.BLOCKED):
        assert decision.backoff_seconds == 0.0
        assert decision.next_agent_id is None
        assert decision.next_provider is None


def test_backoff_grows_exponentially_and_caps_at_eight_seconds():
    c = OrchestrationController(max_retries=10)
    decisions = []
    target = "backoff-target"
    for _ in range(4):
        d = c.decide(_event(target_key=target, error_message="rate limit 429"))
        decisions.append(d.backoff_seconds)
    # Primeira tentativa: 1.0, segunda: 2.0, terceira: 4.0, quarta: cap 8.0.
    assert decisions == [1.0, 2.0, 4.0, 8.0]


def test_rotates_agent_when_server_error():
    c = OrchestrationController()
    d = c.decide(
        _event(target_key="server-error", error_message="500"),
        fallback_agent_id="agent-99",
    )
    assert d.action == RecoveryAction.ROTATE_AGENT
    assert d.next_agent_id == "agent-99"


def test_blocks_after_max_retries_for_transient_failures():
    c = OrchestrationController()
    target_key = "repeat"
    actions = []
    for _ in range(4):
        d = c.decide(_event(target_key=target_key, error_message="rate limit"))
        actions.append(d.action)
    # Espera: retry, retry, retry, blocked (após 2 retries permitidas, escalado a blocked).
    assert actions[-1] == RecoveryAction.BLOCKED


def test_reset_attempts_clears_counter():
    c = OrchestrationController()
    c.decide(_event(target_key="k", error_message="rate limit"))
    c.decide(_event(target_key="k", error_message="rate limit"))
    c.reset_attempts("alpha", "k")
    d = c.decide(_event(target_key="k", error_message="rate limit"))
    assert d.attempt == 1


def test_decide_from_exception_helper():
    exc = RuntimeError("boom-after-plan")
    d, event = decide_from_exception(
        exc,
        project_id="alpha",
        target_key="k",
        phase="apply",
        agent_id="agent-x",
    )
    assert event.phase == "apply"
    assert d.action in (RecoveryAction.ROTATE_AGENT, RecoveryAction.ESCALATE)
    assert d.attempt == 1


def test_record_event_persists_with_parameter_binds(tmp_path):
    c = OrchestrationController()
    db = tmp_path / "recovery.db"
    d = c.decide(_event(target_key="persisted", error_message="rate limit"))
    row_id = c.record_event(db, d, _event(target_key="persisted", error_message="rate limit"))
    assert row_id > 0
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT project_id, target_key, reason, action, attempt FROM ops_recovery WHERE id = ?",
        (row_id,),
    ).fetchall()
    assert rows, "event not persisted"
    row = rows[0]
    assert row[0] == "alpha"
    assert row[1] == "persisted"
    assert row[2] == "rate_limit"
    assert row[3] == "retry-same"
    assert row[4] >= 1
    conn.close()


def test_record_event_does_not_use_string_concatenation(tmp_path):
    """Garante que a inserção não faz SQL com concatenação de campos.

    Verificação indireta: injetar caracteres perigosos em `target_key`
    deve ser armazenado como literal, sem alterar a query.
    """
    c = OrchestrationController()
    target = "x'); DROP TABLE ops_recovery; --"
    db = tmp_path / "recovery.db"
    d = c.decide(_event(target_key=target, error_message="rate limit"))
    c.record_event(db, d, _event(target_key=target, error_message="rate limit"))
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT COUNT(*) FROM ops_recovery").fetchall()
    assert rows[0][0] == 1
    target_value = conn.execute("SELECT target_key FROM ops_recovery LIMIT 1").fetchone()[0]
    assert target_value == target
    conn.close()


def test_should_compress_flag_only_for_overflow_actions():
    c = OrchestrationController()
    d_rate = c.decide(_event(target_key="c1", error_message="rate limit"))
    d_overflow = c.decide(_event(target_key="c2", error_message="context overflow"))
    assert d_rate.should_compress is False
    assert d_overflow.should_compress is True
