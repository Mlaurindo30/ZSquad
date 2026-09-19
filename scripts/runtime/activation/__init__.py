"""R9 — Work Context, Canonical Skill Resolution & Specialist Activation Packet.

Turns an R8 ExecutionAssignment into a deterministic, immutable ActivationPacket
with full hierarchical ancestor context and cognitive budget enforcement.
Zero MCP session authority. Zero host dispatch. Zero specialist execution.

Strictly stdlib + yaml only.
"""

from .errors import (
    ActivationError,
    ActivationPersistenceError,
    CompilationError,
    IncompleteContextError,
    SkillBudgetExceededError,
    SkillNotFoundError,
    SkillResolutionError,
)
from .context import WorkContextBuilder
from .skills import ResolvedSkills, SkillResolver
from .compiler import SpecialistInstructionCompiler
from .repository import ActivationRepository
from .service import ActivationService

__all__ = [
    "ActivationError",
    "ActivationPersistenceError",
    "ActivationRepository",
    "ActivationService",
    "CompilationError",
    "IncompleteContextError",
    "ResolvedSkills",
    "SkillBudgetExceededError",
    "SkillNotFoundError",
    "SkillResolutionError",
    "SkillResolver",
    "SpecialistInstructionCompiler",
    "WorkContextBuilder",
]
