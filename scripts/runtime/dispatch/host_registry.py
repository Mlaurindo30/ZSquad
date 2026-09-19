"""Host Registry and Dynamic Resolution for Specialist Dispatch (Milestone R11).

Strictly stdlib-only. Decouples host resolution from core orchestrator logic.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Callable, Dict, List, Optional, Union

from scripts.runtime.dispatch.adapters.antigravity import AntigravityDispatchAdapter
from scripts.runtime.dispatch.adapters.claude import ClaudeDispatchAdapter
from scripts.runtime.dispatch.adapters.codex import CodexDispatchAdapter
from scripts.runtime.dispatch.adapters.fake import FakeHostAdapter
from scripts.runtime.dispatch.adapters.gemini_cli import GeminiCliDispatchAdapter
from scripts.runtime.dispatch.errors import HostResolutionError, UnsupportedHostError
from scripts.runtime.dispatch.port import HostDispatchPort

AdapterFactory = Union[HostDispatchPort, Callable[[], HostDispatchPort]]


class HostRegistry:
    """Registry maintaining available HostDispatchPort adapters and resolution logic."""

    def __init__(self) -> None:
        self._adapters: Dict[str, AdapterFactory] = {}

    def register_adapter(self, host_kind: str, adapter_or_factory: AdapterFactory) -> None:
        """Registers a host adapter or factory for a given host kind."""
        cleaned_kind = host_kind.strip().lower()
        self._adapters[cleaned_kind] = adapter_or_factory

    def unregister_adapter(self, host_kind: str) -> None:
        """Removes a host adapter from registry."""
        cleaned_kind = host_kind.strip().lower()
        self._adapters.pop(cleaned_kind, None)

    def get_adapter(self, host_kind: str) -> HostDispatchPort:
        """Retrieves and instantiates adapter for specified host kind. Fails closed."""
        cleaned_kind = host_kind.strip().lower() if host_kind else ""
        if not cleaned_kind or cleaned_kind not in self._adapters:
            raise UnsupportedHostError(
                f"Unsupported host kind '{host_kind}'. Registered hosts: {self.list_registered_hosts()}",
                error_code="ERR_HOST_UNRESOLVED",
            )

        entry = self._adapters[cleaned_kind]
        if callable(entry) and not isinstance(entry, HostDispatchPort):
            return entry()
        return entry

    def list_registered_hosts(self) -> List[str]:
        """Returns sorted list of registered host kinds."""
        return sorted(list(self._adapters.keys()))

    def resolve_active_host(self, session_host: Optional[str] = None) -> HostDispatchPort:
        """Dynamically resolves the active host adapter based on governed precedence.

        Precedence:
        1. Explicit SQUAD_HOST_ADAPTER environment variable
        2. Session-specified host binding (if valid and not 'auto')
        3. Environment heuristics / probes (Antigravity, Codex, Gemini CLI, Claude)
        4. Test environment fallback (SQUAD_ENV == 'test' or pytest active) -> 'fake'
        5. Fail-closed: raise HostResolutionError
        """
        # 1. Explicit Environment Override
        env_override = os.environ.get("SQUAD_HOST_ADAPTER", "").strip().lower()
        if env_override:
            if env_override in self._adapters:
                return self.get_adapter(env_override)
            raise HostResolutionError(
                f"Host adapter '{env_override}' specified by SQUAD_HOST_ADAPTER is not registered",
                error_code="ERR_HOST_UNRESOLVED",
            )

        # 2. Session Host Binding
        if session_host and session_host.strip().lower() not in ("", "auto", "none"):
            req_host = session_host.strip().lower()
            if req_host in self._adapters:
                return self.get_adapter(req_host)
            raise UnsupportedHostError(
                f"Host '{session_host}' specified by MCP session is not registered",
                error_code="ERR_HOST_UNRESOLVED",
            )

        # 3. Environment Probes
        # Antigravity probe
        if (
            os.environ.get("ANTIGRAVITY_AGENT_ID")
            or os.environ.get("GEMINI_CLI_MODE")
            or "antigravity" in os.environ.get("AGENT_PLATFORM", "").lower()
        ):
            if "antigravity" in self._adapters:
                return self.get_adapter("antigravity")

        # Codex probe
        if os.environ.get("CODEX_CLI_PATH") or shutil.which("codex"):
            if "codex" in self._adapters:
                return self.get_adapter("codex")

        # Gemini CLI probe
        if os.environ.get("GEMINI_CLI_PATH") or shutil.which("agy") or shutil.which("gemini"):
            if "gemini_cli" in self._adapters:
                return self.get_adapter("gemini_cli")

        # Claude probe
        if os.environ.get("CLAUDE_CODE_ENTRYPOINT") or shutil.which("claude"):
            if "claude" in self._adapters:
                return self.get_adapter("claude")

        # 4. Test Environment Fallback
        is_test_env = (
            os.environ.get("SQUAD_ENV") == "test"
            or "pytest" in sys.modules
            or "unittest" in sys.modules
        )
        if is_test_env and "fake" in self._adapters:
            return self.get_adapter("fake")

        # Default to antigravity if running in user's default Antigravity environment
        if "antigravity" in self._adapters:
            return self.get_adapter("antigravity")

        # 5. Fail-Closed
        raise HostResolutionError(
            "Unable to resolve valid host adapter: environment did not match any registered host. "
            f"Available: {self.list_registered_hosts()}",
            error_code="ERR_HOST_UNRESOLVED",
        )


def create_default_registry() -> HostRegistry:
    """Initializes a canonical HostRegistry populated with default adapters."""
    registry = HostRegistry()
    registry.register_adapter("fake", FakeHostAdapter)
    registry.register_adapter("antigravity", AntigravityDispatchAdapter)
    registry.register_adapter("codex", CodexDispatchAdapter)
    registry.register_adapter("gemini_cli", GeminiCliDispatchAdapter)
    registry.register_adapter("claude", ClaudeDispatchAdapter)
    return registry


default_host_registry = create_default_registry()
