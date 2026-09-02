from unittest.mock import patch

import pytest

from scripts.error_classifier import (
    ErrorClassifier,
    FailoverReason,
    classify_error,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_exc(message: str) -> Exception:
    return RuntimeError(message)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestErrorClassifier:
    def test_auth_401(self):
        result = classify_error(status_code=401, response_text="unauthorized")
        assert result.reason == FailoverReason.auth
        assert result.retryable is False
        assert result.should_backoff is False
        assert result.should_fallback is True

    def test_auth_403(self):
        result = classify_error(status_code=403, response_text="forbidden")
        assert result.reason == FailoverReason.auth
        assert result.retryable is False

    def test_auth_text(self):
        result = classify_error(response_text="Authentication failed")
        assert result.reason == FailoverReason.auth

    def test_billing_402(self):
        result = classify_error(status_code=402, response_text="billing error")
        assert result.reason == FailoverReason.billing
        assert result.retryable is False
        assert result.should_fallback is True

    def test_billing_text(self):
        result = classify_error(response_text="credit exhausted")
        assert result.reason == FailoverReason.billing

    def test_rate_limit_429(self):
        result = classify_error(status_code=429, response_text="rate limit exceeded")
        assert result.reason == FailoverReason.rate_limit
        assert result.retryable is True
        assert result.should_backoff is True
        assert result.should_fallback is True

    def test_rate_limit_text(self):
        result = classify_error(response_text="Too Many Requests")
        assert result.reason == FailoverReason.rate_limit

    def test_overloaded_503(self):
        result = classify_error(status_code=503, response_text="service unavailable")
        assert result.reason == FailoverReason.overloaded
        assert result.retryable is True
        assert result.should_backoff is True

    def test_overloaded_529(self):
        result = classify_error(status_code=529, response_text="overloaded")
        assert result.reason == FailoverReason.overloaded

    def test_server_error_500(self):
        result = classify_error(status_code=500, response_text="internal server error")
        assert result.reason == FailoverReason.server_error
        assert result.retryable is True
        assert result.should_backoff is True

    def test_server_error_502(self):
        result = classify_error(status_code=502, response_text="bad gateway")
        assert result.reason == FailoverReason.server_error

    def test_timeout_text(self):
        result = classify_error(response_text="connection timed out")
        assert result.reason == FailoverReason.timeout
        assert result.retryable is True
        assert result.should_backoff is True

    def test_timeout_connection_reset(self):
        result = classify_error(response_text="connection reset")
        assert result.reason == FailoverReason.timeout

    def test_ssl_cert_verification(self):
        result = classify_error(response_text="SSL certificate verification failed")
        assert result.reason == FailoverReason.ssl_cert_verification
        assert result.retryable is False

    def test_context_overflow(self):
        result = classify_error(response_text="maximum context length exceeded")
        assert result.reason == FailoverReason.context_overflow
        assert result.should_compress is True
        assert result.retryable is False

    def test_payload_too_large(self):
        result = classify_error(status_code=413, response_text="payload too large")
        assert result.reason == FailoverReason.payload_too_large
        assert result.should_compress is True

    def test_image_too_large(self):
        result = classify_error(response_text="image too large")
        assert result.reason == FailoverReason.image_too_large
        assert result.should_compress is True

    def test_unknown_fallback(self):
        result = classify_error(response_text="something weird happened")
        assert result.reason == FailoverReason.unknown
        # Por padrão, erros de rede/causa desconhecida são retentáveis para
        # preservar o comportamento histórico do LLMRouter.
        assert result.retryable is True

    def test_exception_preferred_over_text(self):
        result = classify_error(exc=make_exc("authentication failed"), status_code=500, response_text="ignore this")
        assert result.reason == FailoverReason.auth

    def test_provider_hint_propagated(self):
        result = classify_error(status_code=429, response_text="rate limit", provider="openai")
        assert result.provider_hint == "openai"

    def test_bound_error_truncates_long_text(self):
        long_text = "x" * 3000
        result = classify_error(response_text=long_text)
        assert len(result.bound_error) <= 2048 + len("... [truncated]")

    def test_bound_error_preserves_short_text(self):
        text = "short error"
        result = classify_error(response_text=text)
        assert result.bound_error == text
