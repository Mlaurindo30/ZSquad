"""Camada de acesso a LLMs do squad: OmniRoute (OpenAI-compatible) e Ollama (nativa).

O que é: módulo de acesso a LLMs com dois provedores — OmniRoute em
``http://localhost:20128`` (API compatível com OpenAI ``/v1/chat/completions``) e
Ollama em ``http://localhost:11434`` (API nativa ``/api/chat``) — mais um
``LLMRouter`` que encadeia os provedores em cascata de fallback.

Responsabilidade: encapsular endpoint/payload de cada provedor, garantir o
requisito crítico ``keep_alive: 0`` em TODA chamada Ollama (o modelo deve ser
descarregado da RAM imediatamente após cada chamada; a máquina nunca fica
carregada), medir latência e orquestrar retry único + fallback.

Pra que serve: oferecer ao squad uma única interface ``.complete(system, user)``
para consumir LLMs locais de forma resiliente, sem manter modelos residentes em
memória e sem acoplar o restante do código aos detalhes de cada API.

Comportamento em falha: erros de rede/HTTP (``requests.RequestException``) e
conteúdo inválido quando ``expect_json=True`` acionam 1 retry no mesmo provedor
e, persistindo a falha, o avanço para o próximo provedor; esgotados todos,
``LLMRouter`` levanta ``ProviderError`` listando os erros por provedor.

Conexões: consumido por scripts do squad e pela CLI ``python scripts/llm_providers.py check``
(ping por provedor; sai 0 se ao menos um responder OK, senão 1).

Dependências & Imports: ``requests`` (já é dependência do projeto) e biblioteca
padrão (``json``, ``sys``, ``time``, ``dataclasses``, ``typing``). Nenhuma
dependência nova.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

OMNIROUTE_BASE_URL = "http://localhost:20128"
OLLAMA_BASE_URL = "http://localhost:11434"

OMNIROUTE_MODEL = "agy/gemini-3.6-flash-low"
OLLAMA_PRIMARY_MODEL = "granite4.1:8b"
OLLAMA_VOLUME_MODEL = "granite4.1:3b"


@dataclass
class ProviderConfig:
    """Configuração imutável de um provedor LLM.

    O que é: dataclass com nome, URL base, modelo, sinal de API Ollama e timeout.

    Responsabilidade: carregar todos os dados necessários para montar a chamada HTTP
    do provedor, sem lógica própria.

    Pra que serve: separar configuração de comportamento em ``LLMProvider`` e
    permitir instanciar provedores idênticos com sessões diferentes (ex.: testes).

    Comportamento em falha: nenhum (objeto passivo); valores inválidos só
    aparecem como erro de requisição em ``LLMProvider.complete``.

    Conexões: consumido por ``LLMProvider``, ``OmniRoute``, ``Ollama`` e
    ``DEFAULT_PROVIDERS``.

    Dependências & Imports: ``dataclasses.dataclass``.
    """

    name: str
    base_url: str
    model: str
    is_ollama: bool = False
    timeout: float = 60.0


class ProviderError(RuntimeError):
    """Erro de esgotamento da cascata de provedores.

    O que é: exceção levantada por ``LLMRouter.complete`` quando todos os
    provedores falharam (após retry único de cada um).

    Responsabilidade: sinalizar de forma única e auditável que nenhuma chamada
    teve sucesso, carregando a lista de erros por provedor na mensagem.

    Pra que serve: permitir que chamadores tratem "nenhum LLM disponível" com um
    único tipo de exceção, mantendo o diagnóstico completo.

    Comportamento em falha: é o próprio canal de falha; não captura nada.

    Conexões: levantada por ``LLMRouter.complete``.

    Dependências & Imports: ``RuntimeError`` (biblioteca padrão).
    """


class LLMProvider:
    """Cliente de um único provedor LLM (OmniRoute ou Ollama).

    O que é: wrapper fino sobre ``requests`` que conhece o dialeto HTTP de cada
    provedor e devolve um resultado normalizado.

    Responsabilidade: montar endpoint/payload corretos por provedor (incluindo
    ``keep_alive: 0`` incondicional no caminho Ollama e ``format: "json"`` quando
    ``expect_json``), executar o POST com timeout, validar status e extrair o
    conteúdo da resposta com latência medida.

    Pra que serve: padronizar o consumo de LLMs locais — OmniRoute via
    ``POST {base}/v1/chat/completions`` (resposta em ``choices[0].message.content``)
    e Ollama via ``POST {base}/api/chat`` (resposta em ``message.content``).

    Comportamento em falha: propaga ``requests.RequestException`` (conexão,
    timeout, status HTTP via ``raise_for_status``) e erros de parsing/formato da
    resposta; não retenta por conta própria (o retry é política do ``LLMRouter``).

    Conexões: usado por ``LLMRouter``, ``DEFAULT_PROVIDERS`` e CLI ``main``.

    Dependências & Imports: ``requests``; ``time.perf_counter`` para latência.
    """

    def __init__(self, config: ProviderConfig, session: Any = None) -> None:
        self.config = config
        # Sessão injetável (duck-typed .post) permite testes sem HTTP real.
        self.session = session if session is not None else requests.Session()

    def _endpoint(self) -> str:
        if self.config.is_ollama:
            return f"{self.config.base_url}/api/chat"
        return f"{self.config.base_url}/v1/chat/completions"

    def _build_payload(self, messages: list[dict], expect_json: bool) -> dict:
        if self.config.is_ollama:
            payload: dict = {
                "model": self.config.model,
                "messages": messages,
                "stream": False,
                "options": {},
                # REQUISITO CRÍTICO (humano): keep_alive=0 em TODA chamada Ollama,
                # incondicional — o modelo é descarregado da RAM logo após a call.
                "keep_alive": 0,
            }
            if expect_json:
                payload["format"] = "json"
            return payload
        # stream=False é obrigatório: o OmniRoute local responde em SSE
        # (text/event-stream) quando o cliente não desativa o streaming, e o
        # corpo deixa de ser JSON parseável.
        return {"model": self.config.model, "messages": messages, "stream": False}

    def _extract_content(self, raw: dict) -> Any:
        if self.config.is_ollama:
            return raw["message"]["content"]
        return raw["choices"][0]["message"]["content"]

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        expect_json: bool = False,
    ) -> dict:
        """Executa uma completion e devolve o resultado normalizado.

        O que é: método principal do provedor; uma chamada, um resultado.

        Responsabilidade: montar mensagens (system+user), chamar o endpoint do
        provedor com timeout, medir ``elapsed_ms`` e extrair o conteúdo do JSON
        de resposta conforme o dialeto do provedor.

        Pra que serve: interface única para o squad; ``expect_json=True`` pede
        saída JSON ao provedor (no Ollama via ``format: "json"``).

        Comportamento em falha: propaga ``requests.RequestException`` (rede ou
        ``raise_for_status``) e ``KeyError``/``ValueError`` de resposta malformada.

        Conexões: chamado por ``LLMRouter.complete`` e pela CLI ``main("check")``.

        Dependências & Imports: ``time.perf_counter``, ``self.session.post``.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        url = self._endpoint()
        payload = self._build_payload(messages, expect_json)

        started = time.perf_counter()
        response = self.session.post(url, json=payload, timeout=self.config.timeout)
        response.raise_for_status()
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        raw = response.json()
        content = self._extract_content(raw)
        return {
            "provider": self.config.name,
            "model": self.config.model,
            "content": content,
            "elapsed_ms": round(elapsed_ms, 3),
            "raw": raw,
        }


class LLMRouter:
    """Cascata de fallback sobre uma lista ordenada de ``LLMProvider``.

    O que é: orquestrador que tenta os provedores em ordem, com 1 retry por
    provedor antes de avançar.

    Responsabilidade: aplicar a política de resiliência — em
    ``requests.RequestException`` ou conteúdo não-JSON quando ``expect_json``,
    retenta UMA vez no mesmo provedor e então avança; esgotados todos, levanta
    ``ProviderError`` listando os erros por provedor.

    Pra que serve: dar ao squad uma chamada ``.complete`` resiliente (OmniRoute →
    Ollama 8b → Ollama 3b) sem que o chamador conheça a topologia.

    Comportamento em falha: ``ProviderError`` com o histórico completo; nunca
    engole silenciosamente um erro.

    Conexões: construído tipicamente com ``DEFAULT_PROVIDERS()``.

    Dependências & Imports: ``requests.RequestException``, ``json.loads``.
    """

    def __init__(self, providers: list[LLMProvider]) -> None:
        self.providers = list(providers)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        expect_json: bool = False,
    ) -> dict:
        """Tenta cada provedor em ordem (1 retry cada) e devolve o primeiro sucesso.

        O que é: ponto único de entrada resiliente da camada LLM.

        Responsabilidade: sequenciar tentativas, validar JSON quando pedido e
        acumular erros por provedor/tentativa para diagnóstico.

        Pra que serve: degradar com controle — provedor primário fora? O segundo
        assume automaticamente.

        Comportamento em falha: após esgotar todos os provedores, levanta
        ``ProviderError`` cuja mensagem lista cada erro (provedor, modelo e causa).

        Conexões: delega a cada ``LLMProvider.complete``.

        Dependências & Imports: ``json.loads``, ``requests.RequestException``.
        """
        errors: list[str] = []
        for provider in self.providers:
            for attempt in (1, 2):
                label = f"{provider.config.name}[{provider.config.model}] tentativa {attempt}"
                try:
                    result = provider.complete(
                        system_prompt, user_prompt, expect_json=expect_json
                    )
                    if expect_json:
                        json.loads(result["content"])
                    return result
                except requests.RequestException as exc:
                    errors.append(f"{label}: {type(exc).__name__}: {exc}")
                except json.JSONDecodeError as exc:
                    errors.append(f"{label}: conteúdo não é JSON válido: {exc}")
        raise ProviderError(
            "Todos os provedores falharam — " + " | ".join(errors)
        )


def OmniRoute(  # noqa: N802 — nome de fábrica no estilo do domínio.
    model: str,
    base_url: str = OMNIROUTE_BASE_URL,
    name: str = "omniroute",
    timeout: float = 60.0,
) -> LLMProvider:
    """Fábrica de provedor OmniRoute (OpenAI-compatible, memória server-side).

    O que é: construtor auxiliar com defaults da instância local
    (``http://localhost:20128``).

    Responsabilidade: montar ``ProviderConfig``/``LLMProvider`` com
    ``is_ollama=False``.

    Pra que serve: tornar a montagem da cascata declarativa em
    ``DEFAULT_PROVIDERS``.

    Comportamento em falha: nenhum aqui; falhas surgem em ``complete``.

    Conexões: usado por ``DEFAULT_PROVIDERS``.

    Dependências & Imports: ``ProviderConfig``, ``LLMProvider``.
    """
    return LLMProvider(
        ProviderConfig(
            name=name, base_url=base_url, model=model, timeout=timeout
        )
    )


def Ollama(  # noqa: N802 — nome de fábrica no estilo do domínio.
    model: str,
    base_url: str = OLLAMA_BASE_URL,
    name: Optional[str] = None,
    # 300s acomoda o load frio do modelo (granite4.1:8b medido em ~152s na
    # máquina local); keep_alive: 0 descarrega após cada chamada, então toda
    # chamada paga o load frio.
    timeout: float = 300.0,
) -> LLMProvider:
    """Fábrica de provedor Ollama (API nativa; descarrega da RAM a cada call).

    O que é: construtor auxiliar com defaults da instância local
    (``http://localhost:11434``).

    Responsabilidade: montar ``ProviderConfig``/``LLMProvider`` com
    ``is_ollama=True`` (o que ativa ``keep_alive: 0`` incondicional).

    Pra que serve: instanciar os fallbacks locais do squad com uma linha.

    Comportamento em falha: nenhum aqui; falhas surgem em ``complete``.

    Conexões: usado por ``DEFAULT_PROVIDERS``.

    Dependências & Imports: ``ProviderConfig``, ``LLMProvider``.
    """
    provider_name = name if name is not None else f"ollama:{model}"
    return LLMProvider(
        ProviderConfig(
            name=provider_name,
            base_url=base_url,
            model=model,
            is_ollama=True,
            timeout=timeout,
        )
    )


def DEFAULT_PROVIDERS() -> list[LLMProvider]:
    """Cascata padrão do squad: OmniRoute → Ollama 8b → Ollama 3b.

    O que é: função-fábrica do conjunto ordenado de provedores aprovados.

    Responsabilidade: materializar a ordem de preferência — OmniRoute
    ``agy/gemini-3.6-flash-low`` (server-side), Ollama ``granite4.1:8b``
    (primário local) e Ollama ``granite4.1:3b`` (volume).

    Pra que serve: fonte única de verdade da topologia de fallback, consumida
    pela CLI e por quem montar ``LLMRouter``.

    Comportamento em falha: nenhuma em si; indisponibilidades aparecem em
    runtime via ``ProviderError``.

    Conexões: usada por ``main("check")`` e por chamadores do ``LLMRouter``.

    Dependências & Imports: ``OmniRoute``, ``Ollama``.
    """
    return [
        OmniRoute(model=OMNIROUTE_MODEL),
        Ollama(model=OLLAMA_PRIMARY_MODEL),
        Ollama(model=OLLAMA_VOLUME_MODEL),
    ]


def main(
    argv: Optional[list[str]] = None,
    providers: Optional[list[LLMProvider]] = None,
) -> int:
    """CLI de diagnóstico: ``python scripts/llm_providers.py check``.

    O que é: subcomando ``check`` que faz um ping mínimo ("ping"/"ping",
    ``expect_json=False``) em cada provedor.

    Responsabilidade: imprimir ``OK``/``FAIL`` por provedor e devolver código de
    saída 0 se ao menos um provedor respondeu, 1 se todos falharam e 2 para uso
    inválido (subcomando ausente/desconhecido).

    Pra que serve: verificar rapidamente a saúde da camada LLM local.

    Comportamento em falha: falhas individuais viram linhas ``FAIL`` com a causa;
    a CLI não explode por exceção de provedor.

    Conexões: usa ``DEFAULT_PROVIDERS()`` (injetável via ``providers`` para testes).

    Dependências & Imports: ``sys``, ``requests`` (via exceções do provider).
    """
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] != "check":
        print("Uso: python scripts/llm_providers.py check")
        return 2

    active_providers = DEFAULT_PROVIDERS() if providers is None else providers
    any_ok = False
    for provider in active_providers:
        label = f"{provider.config.name} ({provider.config.model})"
        try:
            provider.complete("ping", "ping")
            print(f"OK   {label}")
            any_ok = True
        except Exception as exc:  # diagnóstico robusto: nenhum provider derruba a CLI
            print(f"FAIL {label}: {type(exc).__name__}: {exc}")
    return 0 if any_ok else 1


if __name__ == "__main__":
    sys.exit(main())
