"""Specialist Router — deterministic stage-aware routing engine.

Primary invariant: ZERO silent fallback to software-engineer.
Zero candidates → BLOCKED. Ambiguity → NEEDS_ROUTING.

Strictly stdlib + yaml only.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import List, Optional

from scripts.domain.delegation import (
    RoutingDecision,
    RoutingRequest,
    RoutingStatus,
)
from scripts.runtime.routing.errors import NoAgentFoundError, RoutingError
from scripts.runtime.routing.policies import (
    DEFAULT_ROUTING_POLICY,
    RoutingPolicy,
    StageDemand,
    resolve_stage_demand,
)
from scripts.runtime.routing.registry import AgentCapability, AgentRegistry


class SpecialistRouter:
    """Deterministic specialist routing engine.

    Given a RoutingRequest, produces a RoutingDecision by:
    1. Resolving StageDemand from CANONICAL_STAGE_POLICIES
    2. If required_role specified → direct registry lookup
    3. If required_capability specified → capability search
    4. Default: use stage owner_role from StageDemand
    5. Zero candidates → BLOCKED (never fallback)
    6. Ambiguity → NEEDS_ROUTING
    7. Single match → ASSIGNED
    """

    def __init__(
        self,
        registry: AgentRegistry,
        policy: Optional[RoutingPolicy] = None,
    ) -> None:
        self._registry = registry
        self._policy = policy or DEFAULT_ROUTING_POLICY

    def route(self, request: RoutingRequest) -> RoutingDecision:
        """Routes a request to a specialist deterministically.

        Returns:
            RoutingDecision with status ASSIGNED, NEEDS_ROUTING, or BLOCKED.
            Never raises on routing failure — always returns a decision.
        """
        decision_id = self._generate_decision_id(request)

        # 1. Resolve stage demand
        try:
            demand = resolve_stage_demand(request.stage, request.cycle_id)
        except RoutingError as exc:
            return RoutingDecision(
                decision_id=decision_id,
                work_item_id=request.work_item_id,
                stage=request.stage,
                required_role=request.required_role,
                required_capability=request.required_capability,
                candidates=[],
                selected_agent_id=None,
                status=RoutingStatus.BLOCKED,
                reason=f"Stage demand resolution failed: {exc}",
            )

        # 2. Resolve candidates
        candidates = self._resolve_candidates(demand, request)

        # 3. Evaluate result
        if not candidates:
            return RoutingDecision(
                decision_id=decision_id,
                work_item_id=request.work_item_id,
                stage=request.stage,
                required_role=request.required_role or demand.owner_role,
                required_capability=request.required_capability,
                candidates=[],
                selected_agent_id=None,
                status=RoutingStatus.BLOCKED,
                reason=(
                    f"No agent found for stage {demand.stage.value}, "
                    f"role '{request.required_role or demand.owner_role}', "
                    f"capability '{request.required_capability or 'none'}'"
                ),
            )

        if len(candidates) == 1:
            selected = candidates[0]
            return RoutingDecision(
                decision_id=decision_id,
                work_item_id=request.work_item_id,
                stage=request.stage,
                required_role=request.required_role or demand.owner_role,
                required_capability=request.required_capability,
                candidates=[c.agent_id for c in candidates],
                selected_agent_id=selected.agent_id,
                status=RoutingStatus.ASSIGNED,
                reason=(
                    f"Single candidate '{selected.agent_id}' matched for "
                    f"stage {demand.stage.value}"
                ),
            )

        # Multiple candidates — try deterministic tiebreak
        selected = self._select_best(candidates, demand)
        if selected is not None:
            return RoutingDecision(
                decision_id=decision_id,
                work_item_id=request.work_item_id,
                stage=request.stage,
                required_role=request.required_role or demand.owner_role,
                required_capability=request.required_capability,
                candidates=[c.agent_id for c in candidates],
                selected_agent_id=selected.agent_id,
                status=RoutingStatus.ASSIGNED,
                reason=(
                    f"Selected '{selected.agent_id}' from {len(candidates)} candidates "
                    f"for stage {demand.stage.value} (owner priority)"
                ),
            )

        # Ambiguity — no deterministic tiebreak
        return RoutingDecision(
            decision_id=decision_id,
            work_item_id=request.work_item_id,
            stage=request.stage,
            required_role=request.required_role or demand.owner_role,
            required_capability=request.required_capability,
            candidates=[c.agent_id for c in candidates],
            selected_agent_id=None,
            status=RoutingStatus.NEEDS_ROUTING,
            reason=(
                f"Ambiguous: {len(candidates)} candidates "
                f"({', '.join(c.agent_id for c in candidates)}) "
                f"for stage {demand.stage.value}, no deterministic tiebreak"
            ),
        )

    def _resolve_candidates(
        self,
        demand: StageDemand,
        request: RoutingRequest,
    ) -> List[AgentCapability]:
        """Resolves candidate agents for the routing request."""

        # Path A: explicit required_role
        if request.required_role:
            agent = self._registry.resolve_role(request.required_role)
            if agent is not None and agent.dispatchable:
                return [agent]
            # Role specified but not found — return empty (will become BLOCKED)
            return []

        # Path B: explicit required_capability
        if request.required_capability:
            caps = self._registry.find_by_capability(request.required_capability)
            dispatchable = [c for c in caps if c.dispatchable]
            return dispatchable[:self._policy.max_candidates]

        # Path C: stage-driven routing (default)
        candidates: List[AgentCapability] = []

        # C1: Stage owner (primary)
        if self._policy.require_stage_owner_first:
            owner = self._registry.resolve_role(demand.owner_role)
            if owner is not None and owner.dispatchable:
                return [owner]

        # C2: Collaborators (fallback, not silent — explicit path)
        if self._policy.allow_collaborators:
            for collab_role in demand.collaborator_roles:
                collab = self._registry.resolve_role(collab_role)
                if collab is not None and collab.dispatchable:
                    candidates.append(collab)

        return candidates[:self._policy.max_candidates]

    def _select_best(
        self,
        candidates: List[AgentCapability],
        demand: StageDemand,
    ) -> Optional[AgentCapability]:
        """Deterministic tiebreak: prefer stage owner if present in candidates."""
        owner = self._registry.resolve_role(demand.owner_role)
        if owner is not None:
            for c in candidates:
                if c.agent_id == owner.agent_id:
                    return c

        # If owner not in candidates, return first core agent
        for c in candidates:
            if c.mode == "core":
                return c

        # No deterministic tiebreak possible
        return None

    @staticmethod
    def _generate_decision_id(request: RoutingRequest) -> str:
        """Generates a deterministic decision ID from request parameters."""
        content = f"{request.work_item_id}:{request.stage}:{request.required_role}:{request.required_capability}"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        return f"RD-{digest}"
