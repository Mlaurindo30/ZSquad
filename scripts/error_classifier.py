"""Error taxonomy e classificação estruturada (padrão Hermes Agent).

Classifica erros de API em categorias que determinam a estratégia de
recuperação: retry, rotate credential, fallback, compress context, abort.

Uso:
    from scripts.error_classifier import classify_error, ErrorClassifier

    result = classify_error(
        exc=exc,
        status_code=429,
        response_text="rate limit exceeded",
    )
    # result.reason == FailoverReason.rate_limit
    # result.retryable == True
    # result.should_backoff == True
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_TOOL_ERROR_CHARS = 2048


# ---------------------------------------------------------------------------
# Taxonomia
# ---------------------------------------------------------------------------


class FailoverReason(enum.Enum):
    """Por que uma chamada falhou — determina a estratégia de recuperação."""

    auth = "auth"
    auth_permanent = "auth_permanent"
    billing = "billing"
    rate_limit = "rate_limit"
    upstream_rate_limit = "upstream_rate_limit"
    overloaded = "overloaded"
    server_error = "server_error"
    timeout = "timeout"
    ssl_cert_verification = "ssl_cert_verification"
    context_overflow = "context_overflow"
    payload_too_large = "payload_too_large"
    image_too_large = "image_too_large"
    unknown = "unknown"


# ---------------------------------------------------------------------------
# Resultado classificado
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassifiedError:
    reason: FailoverReason
    retryable: bool
    should_backoff: bool
    should_compress: bool
    should_fallback: bool
    bound_error: str
    provider_hint: Optional[str] = None


# ---------------------------------------------------------------------------
# Classificador
# ---------------------------------------------------------------------------


class ErrorClassifier:
    """Classifica erros de API em categorias de recuperação."""

    _AUTH_PATTERNS = [
        re.compile(r"401", re.I),
        re.compile(r"403", re.I),
        re.compile(r"unauthorized", re.I),
        re.compile(r"authentication", re.I),
    ]
    _BILLING_PATTERNS = [
        re.compile(r"402", re.I),
        re.compile(r"billing", re.I),
        re.compile(r"credit", re.I),
        re.compile(r"payment", re.I),
    ]
    _RATE_LIMIT_PATTERNS = [
        re.compile(r"429", re.I),
        re.compile(r"rate[- ]?limit", re.I),
        re.compile(r"too many requests", re.I),
        re.compile(r"quota", re.I),
    ]
    _OVERLOADED_PATTERNS = [
        re.compile(r"503", re.I),
        re.compile(r"529", re.I),
        re.compile(r"overloaded", re.I),
        re.compile(r"service unavailable", re.I),
    ]
    _SERVER_ERROR_PATTERNS = [
        re.compile(r"500", re.I),
        re.compile(r"502", re.I),
        re.compile(r"server error", re.I),
        re.compile(r"bad gateway", re.I),
    ]
    _TIMEOUT_PATTERNS = [
        re.compile(r"timeout", re.I),
        re.compile(r"timed out", re.I),
        re.compile(r"connection reset", re.I),
        re.compile(r"connection refused", re.I),
    ]
    _SSL_PATTERNS = [
        re.compile(r"ssl", re.I),
        re.compile(r"certificate", re.I),
        re.compile(r"tls", re.I),
        re.compile(r"cert verify", re.I),
    ]
    _CONTEXT_OVERFLOW_PATTERNS = [
        re.compile(r"context length", re.I),
        re.compile(r"maximum context", re.I),
        re.compile(r"too many tokens", re.I),
        re.compile(r"context.*overflow", re.I),
    ]
    _PAYLOAD_TOO_LARGE_PATTERNS = [
        re.compile(r"413", re.I),
        re.compile(r"payload too large", re.I),
        re.compile(r"request too large", re.I),
    ]
    _IMAGE_TOO_LARGE_PATTERNS = [
        re.compile(r"image too large", re.I),
        re.compile(r"image size", re.I),
    ]

    def classify(
        self,
        exc: Optional[BaseException] = None,
        status_code: Optional[int] = None,
        response_text: str = "",
        provider: Optional[str] = None,
    ) -> ClassifiedError:
        """Classifica um erro de API.

        Args:
            exc: exceção original.
            status_code: HTTP status code.
            response_text: texto da resposta ou mensagem de erro.
            provider: nome do provider LLM (opcional).

        Returns:
            ClassifiedError com razão, flags de recuperação e erro truncado.
        """
        text = _bound_error_text(
            str(exc) if exc is not None else response_text
        )
        status = status_code

        if status in (401, 403) or self._matches(self._AUTH_PATTERNS, text):
            return ClassifiedError(
                reason=FailoverReason.auth,
                retryable=True,
                should_backoff=False,
                should_compress=False,
                should_fallback=False,
                bound_error=text,
                provider_hint=provider,
            )
        if status == 402 or self._matches(self._BILLING_PATTERNS, text):
            return ClassifiedError(
                reason=FailoverReason.billing,
                retryable=False,
                should_backoff=False,
                should_compress=False,
                should_fallback=True,
                bound_error=text,
                provider_hint=provider,
            )
        if status == 429 or self._matches(self._RATE_LIMIT_PATTERNS, text):
            return ClassifiedError(
                reason=FailoverReason.rate_limit,
                retryable=True,
                should_backoff=True,
                should_compress=False,
                should_fallback=True,
                bound_error=text,
                provider_hint=provider,
            )
        if status in (503, 529) or self._matches(self._OVERLOADED_PATTERNS, text):
            return ClassifiedError(
                reason=FailoverReason.overloaded,
                retryable=True,
                should_backoff=True,
                should_compress=False,
                should_fallback=True,
                bound_error=text,
                provider_hint=provider,
            )
        if status in (500, 502) or self._matches(self._SERVER_ERROR_PATTERNS, text):
            return ClassifiedError(
                reason=FailoverReason.server_error,
                retryable=True,
                should_backoff=True,
                should_compress=False,
                should_fallback=False,
                bound_error=text,
                provider_hint=provider,
            )
        if self._matches(self._TIMEOUT_PATTERNS, text):
            return ClassifiedError(
                reason=FailoverReason.timeout,
                retryable=True,
                should_backoff=True,
                should_compress=False,
                should_fallback=False,
                bound_error=text,
                provider_hint=provider,
            )
        if self._matches(self._SSL_PATTERNS, text):
            return ClassifiedError(
                reason=FailoverReason.ssl_cert_verification,
                retryable=False,
                should_backoff=False,
                should_compress=False,
                should_fallback=False,
                bound_error=text,
                provider_hint=provider,
            )
        if self._matches(self._CONTEXT_OVERFLOW_PATTERNS, text):
            return ClassifiedError(
                reason=FailoverReason.context_overflow,
                retryable=False,
                should_backoff=False,
                should_compress=True,
                should_fallback=False,
                bound_error=text,
                provider_hint=provider,
            )
        if self._matches(self._PAYLOAD_TOO_LARGE_PATTERNS, text):
            return ClassifiedError(
                reason=FailoverReason.payload_too_large,
                retryable=False,
                should_backoff=False,
                should_compress=True,
                should_fallback=False,
                bound_error=text,
                provider_hint=provider,
            )
        if self._matches(self._IMAGE_TOO_LARGE_PATTERNS, text):
            return ClassifiedError(
                reason=FailoverReason.image_too_large,
                retryable=False,
                should_backoff=False,
                should_compress=True,
                should_fallback=False,
                bound_error=text,
                provider_hint=provider,
            )

        return ClassifiedError(
            reason=FailoverReason.unknown,
            retryable=False,
            should_backoff=False,
            should_compress=False,
            should_fallback=False,
            bound_error=text,
            provider_hint=provider,
        )

    @staticmethod
    def _matches(patterns: list[re.Pattern], text: str) -> bool:
        return any(p.search(text) for p in patterns)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bound_error_text(error: str, max_chars: int = _MAX_TOOL_ERROR_CHARS) -> str:
    if len(error) <= max_chars:
        return error
    return error[:max_chars] + "... [truncated]"


def classify_error(
    exc: Optional[BaseException] = None,
    status_code: Optional[int] = None,
    response_text: str = "",
    provider: Optional[str] = None,
) -> ClassifiedError:
    """Atalho para classificação sem instanciar ErrorClassifier."""
    return ErrorClassifier().classify(
        exc=exc,
        status_code=status_code,
        response_text=response_text,
        provider=provider,
    )
