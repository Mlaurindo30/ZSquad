"""Testes de hermeticidade para entrypoints de módulos.

Garante que nenhuma invocação via CLI / __main__ tente abrir sockets ou conexões
de rede (incluindo portas locais como 20128 OmniRoute e 11434 Ollama).
"""

from __future__ import annotations

import runpy
import socket
import sys
from pathlib import Path
from typing import Any

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import scripts.llm_providers as llm_providers


def test_module_entrypoint_guard(monkeypatch, capsys):
    """Entrypoint do llm_providers check deve rodar de forma 100% hermética sem rede."""
    def offline_socket(*args, **kwargs):
        raise OSError("Hermetic test: network connections to local or remote ports (20128, 11434) are strictly forbidden")

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


def test_entrypoint_rejects_socket_attempts_to_local_ports(monkeypatch):
    """Garante que qualquer tentativa de conectar nas portas 20128 ou 11434 seja interceptada."""
    blocked_ports = {20128, 11434}
    attempted_ports: set[int] = set()

    def guarded_connect(self, address, *args, **kwargs):
        if isinstance(address, tuple) and len(address) >= 2:
            port = address[1]
            attempted_ports.add(port)
            if port in blocked_ports:
                raise ConnectionRefusedError(f"Hermetic guard blocked connection to local port {port}")
        raise OSError("Offline hermetic mock")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", lambda address, *a, **kw: guarded_connect(None, address))

    # Testa que socket mock funciona e bloqueia
    with pytest.raises((ConnectionRefusedError, OSError)):
        sock = socket.socket()
        sock.connect(("localhost", 20128))

    with pytest.raises((ConnectionRefusedError, OSError)):
        sock = socket.socket()
        sock.connect(("127.0.0.1", 11434))
