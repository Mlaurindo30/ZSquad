"""Routing error hierarchy.

Strictly stdlib-only.
"""


class RoutingError(Exception):
    """Base error for all routing engine failures."""


class NoAgentFoundError(RoutingError):
    """Zero candidates found for the routing demand. Must result in BLOCKED."""


class AmbiguousRoutingError(RoutingError):
    """Multiple candidates with no deterministic tiebreaker. Must result in NEEDS_ROUTING."""


class RegistryConfigError(RoutingError):
    """Agent registry is malformed, empty, or missing required fields."""
