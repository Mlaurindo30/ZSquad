"""R8 — Stage-Aware Specialist Routing Engine.

Deterministic routing from lifecycle stage demand to specialist assignment.
No silent fallback. No LLM. No host dispatch.

Strictly stdlib + yaml only.
"""

from scripts.runtime.routing.errors import (
    AmbiguousRoutingError,
    NoAgentFoundError,
    RegistryConfigError,
    RoutingError,
)
from scripts.runtime.routing.registry import AgentCapability, AgentRegistry
from scripts.runtime.routing.policies import (
    DEFAULT_ROUTING_POLICY,
    RoutingPolicy,
    StageDemand,
    resolve_stage_demand,
)
from scripts.runtime.routing.router import SpecialistRouter
from scripts.runtime.routing.repository import RoutingRepository

__all__ = [
    "AgentCapability",
    "AgentRegistry",
    "AmbiguousRoutingError",
    "DEFAULT_ROUTING_POLICY",
    "NoAgentFoundError",
    "RegistryConfigError",
    "RoutingError",
    "RoutingPolicy",
    "RoutingRepository",
    "SpecialistRouter",
    "StageDemand",
    "resolve_stage_demand",
]
