"""Canonical Lifecycle Engine package for Agent Squad.

Strictly stdlib-only.
Sole source of authority for state transitions across software delivery workflows.
"""

from scripts.domain.lifecycle import (
    Acknowledgement,
    AcknowledgementStatus,
    CANONICAL_STAGE_POLICIES,
    DeliveryCycle,
    GateDecision,
    GateDecisionStatus,
    GateId,
    Handoff,
    LifecycleStage,
    LifecycleTransition,
    StagePolicy,
)
from scripts.runtime.lifecycle.engine import CanonicalLifecycleService
from scripts.runtime.lifecycle.errors import (
    ConfigurationError,
    GateEligibilityViolationError,
    GateNotEligibleError,
    GateNotPassedError,
    GatePrerequisiteViolationError,
    HandoffNotAcknowledgedError,
    HandoffPendingError,
    InvalidTransitionError,
    LifecycleError,
    ReceiptPrerequisiteViolationError,
    StageTransitionIllegalError,
    TimeboxExceededError,
    WIPLimitExceededError,
)
from scripts.runtime.lifecycle.gates import (
    GATE_ALIASES,
    GATE_TO_STAGE_MAP,
    STAGE_TO_GATE_MAP,
    assert_gate_eligibility,
    is_gate_eligible,
    normalize_gate_id,
    verify_gate_approval,
)
from scripts.runtime.lifecycle.history import LifecycleRepository
from scripts.runtime.lifecycle.policies import (
    CANONICAL_CYCLES,
    CANONICAL_STAGES_ORDER,
    CANONICAL_TO_LEGACY_STATE,
    LEGACY_STATE_TO_CANONICAL,
    get_cycle_stages,
    get_stage_policy,
    load_cycles_config,
    load_workflow_config,
    normalize_stage,
    resolve_cycle_for_kind,
    stage_to_legacy_name,
)
from scripts.runtime.lifecycle.wip import DEFAULT_WIP_LIMITS, WIPController

__all__ = [
    # Engine & Services
    "CanonicalLifecycleService",
    "LifecycleRepository",
    "WIPController",
    # Domain models & enums
    "LifecycleStage",
    "GateId",
    "GateDecisionStatus",
    "AcknowledgementStatus",
    "StagePolicy",
    "GateDecision",
    "Acknowledgement",
    "Handoff",
    "LifecycleTransition",
    "DeliveryCycle",
    "CANONICAL_STAGE_POLICIES",
    # Mappings & helpers
    "CANONICAL_STAGES_ORDER",
    "CANONICAL_CYCLES",
    "LEGACY_STATE_TO_CANONICAL",
    "CANONICAL_TO_LEGACY_STATE",
    "GATE_TO_STAGE_MAP",
    "STAGE_TO_GATE_MAP",
    "GATE_ALIASES",
    "normalize_stage",
    "stage_to_legacy_name",
    "get_stage_policy",
    "resolve_cycle_for_kind",
    "get_cycle_stages",
    "load_cycles_config",
    "load_workflow_config",
    "assert_gate_eligibility",
    "is_gate_eligible",
    "normalize_gate_id",
    "verify_gate_approval",
    "DEFAULT_WIP_LIMITS",
    # Errors
    "LifecycleError",
    "InvalidTransitionError",
    "GateNotEligibleError",
    "GateNotPassedError",
    "HandoffPendingError",
    "WIPLimitExceededError",
    "TimeboxExceededError",
    "ConfigurationError",
    "StageTransitionIllegalError",
    "GatePrerequisiteViolationError",
    "GateEligibilityViolationError",
    "HandoffNotAcknowledgedError",
    "ReceiptPrerequisiteViolationError",
]
