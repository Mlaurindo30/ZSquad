"""Host Adapters for Host-Native Specialist Dispatch (Milestone R11)."""

from scripts.runtime.dispatch.adapters.antigravity import AntigravityDispatchAdapter
from scripts.runtime.dispatch.adapters.claude import ClaudeDispatchAdapter
from scripts.runtime.dispatch.adapters.codex import CodexDispatchAdapter
from scripts.runtime.dispatch.adapters.fake import FakeHostAdapter
from scripts.runtime.dispatch.adapters.gemini_cli import GeminiCliDispatchAdapter

__all__ = [
    "AntigravityDispatchAdapter",
    "ClaudeDispatchAdapter",
    "CodexDispatchAdapter",
    "FakeHostAdapter",
    "GeminiCliDispatchAdapter",
]
