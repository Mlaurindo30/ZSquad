"""Testes de contrato e cobertura (statements + branches) de scripts/llm_providers.py.

Contrato verificado aqui:
- OmniRoute (OpenAI-compatible): POST {base}/v1/chat/completions, payload {"model", "messages"},
  conteúdo em choices[0].message.content.
- Ollama (API nativa): POST {base}/api/chat com "stream": false e "keep_alive": 0 INCONDICIONAL
  (requisito crítico: o modelo deve ser descarregado da RAM imediatamente após cada chamada) e
  "format": "json" somente quando expect_json=True; conteúdo em message.content.
- LLMRouter: sucesso no primeiro provedor; 1 retry no mesmo provedor ante
  requests.RequestException ou JSON inválido (quando expect_json=True); fallback para o
  próximo provedor; esgotados todos -> ProviderError listando erros por provedor.
- CLI main(): "check" imprime OK/FAIL por provedor, sai 0 se ao menos um OK, senão 1;
  subcomando inválido/ausente -> 2.

Nenhum teste faz HTTP real: sessões fake (duck-typed .post) são injetadas em LLMProvider.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json

import scripts.llm_providers as llm_providers
from scripts.llm_providers import (
    DEFAULT_PROVIDERS,
    LLMProvider,
    LLMRouter,
    Ollama,
    ProviderConfig,
    ProviderError,
)


# ---------------------------------------------------------------------------
# Fakes duck-typed (nunca há HTTP real nestes testes)
# ---------------------------------------------------------------------------


class FakeResponse:
    """Resposta fake com a superfície usada por LLMProvider: raise_for_status/json."""

    def __init__(self, payload=None, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload


class FakeSession:
    """Sessão fake: .post devolve/lança itens do script em ordem; o último item repete."""

    def __init__(self, script):
        self.calls = []
        self._script = list(script)

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            return FakeResponse(payload=item)
        return item


OMNI_OK = {"choices": [{"message": {"content": "pong-omniroute"}}]}
OLLAMA_OK = {"message": {"content": "pong-ollama"}}
BAD_JSON_TEXT = "isto não é JSON {"


def omniroute_provider(script, timeout=60.0):
    config = ProviderConfig(
        name="omniroute-test",
        base_url="http://omni.test",
        model="agy/gemini-3.6-flash-low",
        timeout=timeout,
    )
    session = FakeSession(script)
    return LLMProvider(config, session=session), session


def ollama_provider(script, timeout=60.0):
    config = ProviderConfig(
        name="ollama-test",
        base_url="http://ollama.test",
        model="granite4.1:8b",
        is_ollama=True,
        timeout=timeout,
    )
    session = FakeSession(script)
    return LLMProvider(config, session=session), session


# ---------------------------------------------------------------------------
# OmniRoute: endpoint, payload e extração de conteúdo
# ---------------------------------------------------------------------------


def test_omniroute_payload_url_and_content():
    provider, session = omniroute_provider([OMNI_OK], timeout=12.5)
    result = provider.complete("sys-prompt", "user-prompt")

    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == "http://omni.test/v1/chat/completions"
    assert call["timeout"] == 12.5
    assert call["json"] == {
        "model": "agy/gemini-3.6-flash-low",
        "messages": [
            {"role": "system", "content": "sys-prompt"},
            {"role": "user", "content": "user-prompt"},
        ],
        # stream=False é obrigatório: sem ele o OmniRoute local responde em SSE.
        "stream": False,
    }
    # OmniRoute é OpenAI-compatible: nada de keep_alive/format no payload.
    assert "keep_alive" not in call["json"]
    assert "format" not in call["json"]

    assert result["provider"] == "omniroute-test"
    assert result["model"] == "agy/gemini-3.6-flash-low"
    assert result["content"] == "pong-omniroute"
    assert result["raw"] == OMNI_OK
    assert isinstance(result["elapsed_ms"], float)
    assert result["elapsed_ms"] >= 0


def test_omniroute_expect_json_keeps_openai_payload():
    provider, session = omniroute_provider([OMNI_OK])
    provider.complete("s", "u", expect_json=True)

    payload = session.calls[0]["json"]
    assert payload == {
        "model": "agy/gemini-3.6-flash-low",
        "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ],
        # stream=False é obrigatório: sem ele o OmniRoute local responde em SSE.
        "stream": False,
    }
    assert "format" not in payload
    assert "keep_alive" not in payload


# ---------------------------------------------------------------------------
# Ollama: keep_alive=0 incondicional, format=json só com expect_json
# ---------------------------------------------------------------------------


def test_ollama_payload_keep_alive_zero_stream_false_no_format():
    provider, session = ollama_provider([OLLAMA_OK], timeout=7.0)
    result = provider.complete("s", "u")

    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == "http://ollama.test/api/chat"
    assert call["timeout"] == 7.0
    payload = call["json"]
    assert payload["model"] == "granite4.1:8b"
    assert payload["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    assert payload["stream"] is False
    # REQUISITO CRÍTICO: keep_alive 0 em TODA chamada Ollama, mesmo sem expect_json.
    assert payload["keep_alive"] == 0
    assert "format" not in payload

    assert result["provider"] == "ollama-test"
    assert result["content"] == "pong-ollama"
    assert result["raw"] == OLLAMA_OK


def test_ollama_expect_json_adds_format_keeps_keep_alive_zero():
    provider, session = ollama_provider([{"message": {"content": '{"ok": true}'}}])
    provider.complete("s", "u", expect_json=True)

    payload = session.calls[0]["json"]
    assert payload["format"] == "json"
    # O keep_alive 0 permanece incondicional também no modo JSON.
    assert payload["keep_alive"] == 0
    assert payload["stream"] is False


# ---------------------------------------------------------------------------
# Sessão padrão e erros HTTP
# ---------------------------------------------------------------------------


def test_provider_creates_requests_session_when_none():
    provider = LLMProvider(ProviderConfig(name="x", base_url="http://x.test", model="m"))
    assert isinstance(provider.session, requests.Session)


def test_provider_surfaces_http_error_from_raise_for_status():
    bad = FakeResponse(payload={}, status_error=requests.HTTPError("500 Server Error"))
    provider, _session = omniroute_provider([bad])
    with pytest.raises(requests.HTTPError):
        provider.complete("s", "u")


# ---------------------------------------------------------------------------
# Fábricas
# ---------------------------------------------------------------------------


def test_default_providers_factory():
    providers = DEFAULT_PROVIDERS()

    assert len(providers) == 3
    assert all(isinstance(p, LLMProvider) for p in providers)
    omni, granite_8b, granite_3b = providers

    assert omni.config.name == "omniroute"
    assert omni.config.base_url == "http://localhost:20128"
    assert omni.config.model == "agy/gemini-3.6-flash-low"
    assert omni.config.is_ollama is False

    assert granite_8b.config.name == "ollama:granite4.1:8b"
    assert granite_8b.config.base_url == "http://localhost:11434"
    assert granite_8b.config.model == "granite4.1:8b"
    assert granite_8b.config.is_ollama is True

    assert granite_3b.config.name == "ollama:granite4.1:3b"
    assert granite_3b.config.base_url == "http://localhost:11434"
    assert granite_3b.config.model == "granite4.1:3b"
    assert granite_3b.config.is_ollama is True


def test_ollama_factory_custom_name():
    provider = Ollama(model="granite4.1:8b", name="ollama-volume")
    assert provider.config.name == "ollama-volume"
    assert provider.config.is_ollama is True
    assert provider.config.base_url == "http://localhost:11434"


# ---------------------------------------------------------------------------
# LLMRouter: sucesso, retry único, fallback e esgotamento
# ---------------------------------------------------------------------------


def test_router_success_on_first_provider():
    a, session_a = omniroute_provider([OMNI_OK])
    b, session_b = ollama_provider([OLLAMA_OK])
    router = LLMRouter([a, b])

    result = router.complete("s", "u")

    assert result["provider"] == "omniroute-test"
    assert result["content"] == "pong-omniroute"
    assert len(session_a.calls) == 1
    assert len(session_b.calls) == 0


def test_router_retries_once_same_provider_on_request_exception():
    a, session_a = omniroute_provider([requests.ConnectionError("boom"), OMNI_OK])
    b, session_b = ollama_provider([OLLAMA_OK])
    router = LLMRouter([a, b])

    result = router.complete("s", "u")

    assert result["provider"] == "omniroute-test"
    assert len(session_a.calls) == 2
    assert len(session_b.calls) == 0


def test_router_falls_back_to_next_provider():
    a, session_a = omniroute_provider([requests.ConnectionError("boom")])
    b, session_b = ollama_provider([OLLAMA_OK])
    router = LLMRouter([a, b])

    result = router.complete("s", "u")

    assert result["provider"] == "ollama-test"
    assert result["content"] == "pong-ollama"
    assert len(session_a.calls) == 2
    assert len(session_b.calls) == 1


def test_router_retries_once_on_invalid_json_then_succeeds():
    bad = {"choices": [{"message": {"content": BAD_JSON_TEXT}}]}
    good = {"choices": [{"message": {"content": '{"answer": 42}'}}]}
    a, session_a = omniroute_provider([bad, good])
    router = LLMRouter([a])

    result = router.complete("s", "u", expect_json=True)

    assert json.loads(result["content"]) == {"answer": 42}
    assert len(session_a.calls) == 2


def test_router_falls_back_after_invalid_json_on_both_attempts():
    bad = {"choices": [{"message": {"content": BAD_JSON_TEXT}}]}
    a, session_a = omniroute_provider([bad])
    good = {"message": {"content": '{"ok": true}'}}
    b, session_b = ollama_provider([good])
    router = LLMRouter([a, b])

    result = router.complete("s", "u", expect_json=True)

    assert result["provider"] == "ollama-test"
    assert json.loads(result["content"]) == {"ok": True}
    assert len(session_a.calls) == 2
    assert len(session_b.calls) == 1


def test_router_exhaustion_raises_provider_error_with_all_errors():
    a, session_a = omniroute_provider([requests.Timeout("t1")])
    b, session_b = ollama_provider([requests.ConnectionError("c1")])
    router = LLMRouter([a, b])

    with pytest.raises(ProviderError) as excinfo:
        router.complete("s", "u")

    message = str(excinfo.value)
    assert "omniroute-test" in message
    assert "ollama-test" in message
    assert "Timeout" in message
    assert "ConnectionError" in message
    assert len(session_a.calls) == 2
    assert len(session_b.calls) == 2


# ---------------------------------------------------------------------------
# CLI main(): check (OK/FAIL, códigos de saída) e caminhos de uso
# ---------------------------------------------------------------------------


def test_cli_check_all_ok_exit_0(capsys):
    a, _ = omniroute_provider([OMNI_OK])
    b, _ = ollama_provider([OLLAMA_OK])

    code = llm_providers.main(["check"], providers=[a, b])

    out = capsys.readouterr().out
    assert code == 0
    assert "OK" in out
    assert "FAIL" not in out
    assert "omniroute-test" in out
    assert "ollama-test" in out


def test_cli_check_partial_ok_exit_0(capsys):
    a, _ = omniroute_provider([requests.ConnectionError("down")])
    b, _ = ollama_provider([OLLAMA_OK])

    code = llm_providers.main(["check"], providers=[a, b])

    out = capsys.readouterr().out
    assert code == 0
    assert "FAIL" in out
    assert "OK" in out


def test_cli_check_all_fail_exit_1(capsys):
    a, _ = omniroute_provider([requests.ConnectionError("down")])
    b, _ = ollama_provider([requests.Timeout("t")])

    code = llm_providers.main(["check"], providers=[a, b])

    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL" in out
    assert "OK" not in out


def test_cli_unknown_or_empty_args_exit_2(capsys):
    assert llm_providers.main(["status"], providers=[]) == 2
    assert llm_providers.main([], providers=[]) == 2
    assert "Uso" in capsys.readouterr().out


def test_cli_argv_none_reads_sys_argv_exit_2(capsys):
    # sys.argv do pytest nunca começa com "check" -> cai na mensagem de uso, sem HTTP.
    assert llm_providers.main(None, providers=[]) == 2
    assert "Uso" in capsys.readouterr().out


def test_cli_check_uses_default_providers_factory(monkeypatch, capsys):
    a, _ = omniroute_provider([OMNI_OK])
    monkeypatch.setattr(llm_providers, "DEFAULT_PROVIDERS", lambda: [a])

    assert llm_providers.main(["check"]) == 0
    assert "OK" in capsys.readouterr().out


def test_module_entrypoint_guard(monkeypatch, capsys):
    import runpy
    import socket

    def offline_socket(*args, **kwargs):
        raise OSError("Hermetic test: network connections to local or remote ports are strictly forbidden")

    def offline_http(*args, **kwargs):
        raise requests.RequestException("offline")

    monkeypatch.setattr(socket, "create_connection", offline_socket)
    monkeypatch.setattr(socket.socket, "connect", offline_socket)
    monkeypatch.setattr(requests.Session, "post", offline_http)
    monkeypatch.setattr(requests.Session, "request", offline_http)
    monkeypatch.setattr(requests.Session, "send", offline_http)
    monkeypatch.setattr(requests, "post", offline_http)
    monkeypatch.setattr(requests, "get", offline_http)
    try:
        import httpx
        monkeypatch.setattr(httpx.Client, "send", offline_http)
        monkeypatch.setattr(httpx.Client, "request", offline_http)
    except ImportError:
        pass

    monkeypatch.setattr(sys, "argv", ["llm_providers.py", "check"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(llm_providers.__file__)), run_name="__main__")
    assert exc.value.code == 1
    assert "FAIL" in capsys.readouterr().out
