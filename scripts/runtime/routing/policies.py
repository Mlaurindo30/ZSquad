"""Routing Policies — stage demand resolution from CANONICAL_STAGE_POLICIES.

Strictly stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from scripts.domain.lifecycle import (
    CANONICAL_STAGE_POLICIES,
    GateId,
    LifecycleStage,
    StagePolicy,
)
from scripts.runtime.lifecycle.policies import normalize_stage
from scripts.runtime.routing.errors import RoutingError


@dataclass(frozen=True)
class RoutingPolicy:
    """Configurable routing behavior policy."""

    require_stage_owner_first: bool = True
    allow_collaborators: bool = True
    enforce_wip: bool = False
    enforce_sod: bool = True
    max_candidates: int = 10


DEFAULT_ROUTING_POLICY = RoutingPolicy()


@dataclass(frozen=True)
class StageDemand:
    """Resolved demand for a lifecycle stage: who owns it, who collaborates."""

    stage: LifecycleStage
    owner_role: str
    collaborator_roles: List[str]
    required_gate: Optional[GateId]
    wip_policy_ref: str


def resolve_stage_demand(
    stage: str,
    cycle_id: Optional[str] = None,
) -> StageDemand:
    """Resolves the demand (owner, collaborators, gate) for a canonical stage.

    Uses CANONICAL_STAGE_POLICIES as the authoritative source.

    Args:
        stage: Stage name (canonical enum value, legacy alias, or string).
        cycle_id: Optional cycle identifier (currently used for logging/context only).

    Returns:
        StageDemand with owner_role, collaborator_roles, required_gate, wip_policy_ref.

    Raises:
        RoutingError: If the stage is unknown or has no policy defined.
    """
    try:
        normalized = normalize_stage(stage)
    except Exception as exc:
        raise RoutingError(f"Cannot resolve stage demand: {exc}") from exc

    policy: Optional[StagePolicy] = CANONICAL_STAGE_POLICIES.get(normalized)
    if policy is None:
        raise RoutingError(
            f"No CANONICAL_STAGE_POLICIES entry for stage '{normalized.value}'"
        )

    return StageDemand(
        stage=normalized,
        owner_role=policy.owner_role,
        collaborator_roles=list(policy.collaborator_roles),
        required_gate=policy.required_gate,
        wip_policy_ref=policy.wip_policy_ref,
    )
