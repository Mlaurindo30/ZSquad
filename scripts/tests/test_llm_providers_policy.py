"""Cobertura determinística de ``LLMRouter.complete_with_policy`` e
``compress_messages`` (Etapa 4).

Não há rede real: provedores usam ``FakeSession`` que reproduz scripts
pré-definidos de respostas/erros. O ``sleeper`` é stubável para validar
o backoff sem bloquear.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.llm_providers as llm_providers
from scripts.llm_providers import (
    LLMProvider,
    LLMRouter,
    ProviderConfig,
    ProviderError,
    compress_messages,
)


class FakeResponse:
    def __init__(self, payload=None, status_error: Exception | None = None) -> None:
        self._payload = payload
        self._status_error = status_error
        self.calls: list[dict] = []

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def post(self, url: str, json=None, timeout=None) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if not self.script:
            raise IndexError(f"FakeSession exhausted for {url}")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return FakeResponse(payload=item)


def _provider(
    name: str,
    model: str,
    script: list[Any],
    is_ollama: bool = False,
    timeout: float = 5.0,
) -> LLMProvider:
    cfg = ProviderConfig(name=name, base_url="http://x", model=model, is_ollama=is_ollama, timeout=timeout)
    return LLMProvider(cfg, session=FakeSession(script))


def _sleep_recorder():
    sleeps: list[float] = []

    def _sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    return sleeps, _sleeper


def test_compress_messages_keeps_system_and_recent_tail():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "a" * 100},
        {"role": "assistant", "content": "b" * 100},
        {"role": "user", "content": "c" * 100},
        {"role": "user", "content": "d" * 100},
    ]
    out = compress_messages(msgs, keep_last=2, max_chars=10)
    assert out[0]["role"] == "system"
    assert out[0]["content"] == "s"
    assert out[-1]["content"] == "d" * 100
    assert out[-2]["content"] == "c" * 100
    # Mensagens intermediárias foram marcadas como comprimidas.
    assert "[compressed]" in out[1]["content"]
    assert "[compressed]" in out[2]["content"]


def test_complete_with_policy_returns_first_provider_result():
    a = _provider("p1", "m1", [{"choices": [{"message": {"content": "ok"}}]}])
    b = _provider("p2", "m2", [])
    router = LLMRouter([a, b])
    result = router.complete_with_policy(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
    )
    assert result["content"] == "ok"
    assert result["provider"] == "p1"


def test_aborts_provider_on_permanent_error():
    import requests
    a = _provider(
        "p1", "m1", [
            requests.HTTPError("401 unauthorized"),
            requests.HTTPError("401 unauthorized"),
        ],
    )
    b = _provider("p2", "m2", [{"choices": [{"message": {"content": "fallback"}}]}])
    router = LLMRouter([a, b])
    result = router.complete_with_policy(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )
    assert result["provider"] == "p2"
    # p1 fez UMA chamada; o retry foi suprimido por causa permanente.
    assert len(a.session.calls) == 1


def test_applies_backoff_when_rate_limited():
    import requests
    a = _provider(
        "p1", "m1", [
            requests.HTTPError("429 rate limit exceeded"),
            requests.HTTPError("429 rate limit exceeded"),
        ],
    )
    b = _provider("p2", "m2", [{"choices": [{"message": {"content": "recovered"}}]}])
    sleeps, sleeper = _sleep_recorder()
    router = LLMRouter([a, b], sleeper=sleeper)
    result = router.complete_with_policy(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )
    assert result["provider"] == "p2"
    # Pelo menos um backoff registrado entre as duas tentativas em p1.
    assert sleeps, "expected at least one backoff sleep"
    assert any(0 < s <= 4 for s in sleeps)


def test_complete_with_policy_compresses_then_succeeds():
    import requests
    a = _provider(
        "p1", "m1", [
            requests.HTTPError("context length exceeded"),
            {"choices": [{"message": {"content": "compressed-ok"}}]},
        ],
    )
    router = LLMRouter([a])
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "x" * 600},
        {"role": "assistant", "content": "y" * 600},
        {"role": "user", "content": "z" * 600},
        {"role": "user", "content": "q" * 600},
        {"role": "user", "content": "final"},
    ]
    result = router.complete_with_policy(msgs)
    assert result["content"] == "compressed-ok"


def test_complete_with_policy_raises_after_three_attempts():
    import requests
    a = _provider("p1", "m1", [requests.HTTPError("context length exceeded")] * 10)
    router = LLMRouter([a])
    with pytest.raises(ProviderError):
        router.complete_with_policy(
            [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        )
