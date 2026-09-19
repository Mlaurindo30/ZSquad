"""Canonical Stage Policies, State Mappings, and Cycle Definitions.

Strictly stdlib-only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import yaml

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
from scripts.runtime.lifecycle.errors import ConfigurationError, InvalidTransitionError

# Canonical 13 delivery stages in progressive order
CANONICAL_STAGES_ORDER: List[LifecycleStage] = [
    LifecycleStage.INTAKE,
    LifecycleStage.DISCOVERY,
    LifecycleStage.REQUIREMENTS_PRODUCT,
    LifecycleStage.PLANNING,
    LifecycleStage.ARCHITECTURE_DESIGN,
    LifecycleStage.READINESS_SCAFFOLDING,
    LifecycleStage.IMPLEMENTATION,
    LifecycleStage.CODE_REVIEW,
    LifecycleStage.SECURITY_REVIEW,
    LifecycleStage.TEST_VALIDATION,
    LifecycleStage.QA_VALIDATION,
    LifecycleStage.GOVERNANCE_RELEASE,
    LifecycleStage.DONE,
]

# Legacy and alias mapping to canonical stages
LEGACY_STATE_TO_CANONICAL: Dict[str, LifecycleStage] = {
    "intake": LifecycleStage.INTAKE,
    "discovery": LifecycleStage.DISCOVERY,
    "blueprint": LifecycleStage.REQUIREMENTS_PRODUCT,
    "product-ready": LifecycleStage.REQUIREMENTS_PRODUCT,
    "requirements": LifecycleStage.REQUIREMENTS_PRODUCT,
    "requirements-product": LifecycleStage.REQUIREMENTS_PRODUCT,
    "planning": LifecycleStage.PLANNING,
    "design": LifecycleStage.ARCHITECTURE_DESIGN,
    "design-ready": LifecycleStage.ARCHITECTURE_DESIGN,
    "architecture": LifecycleStage.ARCHITECTURE_DESIGN,
    "architecture-design": LifecycleStage.ARCHITECTURE_DESIGN,
    "scaffolding": LifecycleStage.READINESS_SCAFFOLDING,
    "ready-for-build": LifecycleStage.READINESS_SCAFFOLDING,
    "readiness": LifecycleStage.READINESS_SCAFFOLDING,
    "readiness-scaffolding": LifecycleStage.READINESS_SCAFFOLDING,
    "implementation": LifecycleStage.IMPLEMENTATION,
    "review": LifecycleStage.CODE_REVIEW,
    "code-review": LifecycleStage.CODE_REVIEW,
    "code-security-review": LifecycleStage.SECURITY_REVIEW,
    "security-review": LifecycleStage.SECURITY_REVIEW,
    "test-validation": LifecycleStage.TEST_VALIDATION,
    "testing": LifecycleStage.TEST_VALIDATION,
    "validation": LifecycleStage.QA_VALIDATION,
    "quality-validation": LifecycleStage.QA_VALIDATION,
    "qa-validation": LifecycleStage.QA_VALIDATION,
    "governance-release": LifecycleStage.GOVERNANCE_RELEASE,
    "ready-for-release": LifecycleStage.GOVERNANCE_RELEASE,
    "governance": LifecycleStage.GOVERNANCE_RELEASE,
    "done": LifecycleStage.DONE,
    # Incident cycle states mapped cleanly
    "triage": LifecycleStage.INTAKE,
    "mitigation": LifecycleStage.IMPLEMENTATION,
    "postmortem": LifecycleStage.GOVERNANCE_RELEASE,
}

# Canonical stage to legacy directory / state string
CANONICAL_TO_LEGACY_STATE: Dict[LifecycleStage, str] = {
    LifecycleStage.INTAKE: "intake",
    LifecycleStage.DISCOVERY: "discovery",
    LifecycleStage.REQUIREMENTS_PRODUCT: "blueprint",
    LifecycleStage.PLANNING: "planning",
    LifecycleStage.ARCHITECTURE_DESIGN: "design",
    LifecycleStage.READINESS_SCAFFOLDING: "scaffolding",
    LifecycleStage.IMPLEMENTATION: "implementation",
    LifecycleStage.CODE_REVIEW: "review",
    LifecycleStage.SECURITY_REVIEW: "code-security-review",
    LifecycleStage.TEST_VALIDATION: "test-validation",
    LifecycleStage.QA_VALIDATION: "quality-validation",
    LifecycleStage.GOVERNANCE_RELEASE: "governance-release",
    LifecycleStage.DONE: "done",
}

# Default canonical definitions for the 8 delivery cycles
CANONICAL_CYCLES: Dict[str, List[LifecycleStage]] = {
    "development": [
        LifecycleStage.INTAKE,
        LifecycleStage.DISCOVERY,
        LifecycleStage.REQUIREMENTS_PRODUCT,
        LifecycleStage.PLANNING,
        LifecycleStage.ARCHITECTURE_DESIGN,
        LifecycleStage.READINESS_SCAFFOLDING,
        LifecycleStage.IMPLEMENTATION,
        LifecycleStage.CODE_REVIEW,
        LifecycleStage.SECURITY_REVIEW,
        LifecycleStage.TEST_VALIDATION,
        LifecycleStage.QA_VALIDATION,
        LifecycleStage.GOVERNANCE_RELEASE,
        LifecycleStage.DONE,
    ],
    "user-story": [
        LifecycleStage.INTAKE,
        LifecycleStage.DISCOVERY,
        LifecycleStage.REQUIREMENTS_PRODUCT,
        LifecycleStage.PLANNING,
        LifecycleStage.ARCHITECTURE_DESIGN,
        LifecycleStage.READINESS_SCAFFOLDING,
        LifecycleStage.IMPLEMENTATION,
        LifecycleStage.CODE_REVIEW,
        LifecycleStage.SECURITY_REVIEW,
        LifecycleStage.TEST_VALIDATION,
        LifecycleStage.QA_VALIDATION,
        LifecycleStage.GOVERNANCE_RELEASE,
        LifecycleStage.DONE,
    ],
    "new-project": [
        LifecycleStage.INTAKE,
        LifecycleStage.DISCOVERY,
        LifecycleStage.REQUIREMENTS_PRODUCT,
        LifecycleStage.PLANNING,
        LifecycleStage.ARCHITECTURE_DESIGN,
        LifecycleStage.READINESS_SCAFFOLDING,
        LifecycleStage.IMPLEMENTATION,
        LifecycleStage.GOVERNANCE_RELEASE,
        LifecycleStage.DONE,
    ],
    "bugfix": [
        LifecycleStage.INTAKE,
        LifecycleStage.DISCOVERY,
        LifecycleStage.ARCHITECTURE_DESIGN,
        LifecycleStage.IMPLEMENTATION,
        LifecycleStage.CODE_REVIEW,
        LifecycleStage.SECURITY_REVIEW,
        LifecycleStage.TEST_VALIDATION,
        LifecycleStage.GOVERNANCE_RELEASE,
        LifecycleStage.DONE,
    ],
    "spike": [
        LifecycleStage.INTAKE,
        LifecycleStage.DISCOVERY,
        LifecycleStage.REQUIREMENTS_PRODUCT,
        LifecycleStage.PLANNING,
        LifecycleStage.ARCHITECTURE_DESIGN,
        LifecycleStage.DONE,
    ],
    "release": [
        LifecycleStage.INTAKE,
        LifecycleStage.GOVERNANCE_RELEASE,
        LifecycleStage.DONE,
    ],
    "evolution": [
        LifecycleStage.INTAKE,
        LifecycleStage.DISCOVERY,
        LifecycleStage.REQUIREMENTS_PRODUCT,
        LifecycleStage.PLANNING,
        LifecycleStage.ARCHITECTURE_DESIGN,
        LifecycleStage.READINESS_SCAFFOLDING,
        LifecycleStage.IMPLEMENTATION,
        LifecycleStage.CODE_REVIEW,
        LifecycleStage.GOVERNANCE_RELEASE,
        LifecycleStage.DONE,
    ],
    "incident": [
        LifecycleStage.INTAKE,
        LifecycleStage.DISCOVERY,
        LifecycleStage.IMPLEMENTATION,
        LifecycleStage.SECURITY_REVIEW,
        LifecycleStage.GOVERNANCE_RELEASE,
        LifecycleStage.DONE,
    ],
}


def normalize_stage(stage: Union[str, LifecycleStage]) -> LifecycleStage:
    """Normalizes any stage representation (enum, string, legacy alias) into LifecycleStage.

    Raises:
        InvalidTransitionError: If the stage is unknown.
    """
    if isinstance(stage, LifecycleStage):
        return stage

    if not isinstance(stage, str) or not stage.strip():
        raise InvalidTransitionError(f"Stage value must not be empty, got {type(stage)}")

    cleaned = stage.strip().lower()

    # Direct match in legacy/alias mapping
    if cleaned in LEGACY_STATE_TO_CANONICAL:
        return LEGACY_STATE_TO_CANONICAL[cleaned]

    # Try matching upper enum name
    upper_name = stage.strip().upper().replace("-", "_")
    try:
        return LifecycleStage[upper_name]
    except KeyError:
        pass

    # Try matching enum values
    for member in LifecycleStage:
        if member.value == stage.strip() or member.value.lower() == cleaned:
            return member

    raise InvalidTransitionError(f"Unknown lifecycle stage: '{stage}'")


def stage_to_legacy_name(stage: Union[str, LifecycleStage]) -> str:
    """Maps a canonical stage to its legacy string name used in YAML status projections."""
    norm = normalize_stage(stage)
    return CANONICAL_TO_LEGACY_STATE.get(norm, norm.value.lower().replace("_", "-"))


def get_stage_policy(stage: Union[str, LifecycleStage]) -> StagePolicy:
    """Returns the immutable StagePolicy governing the given canonical stage."""
    norm = normalize_stage(stage)
    if norm not in CANONICAL_STAGE_POLICIES:
        raise ConfigurationError(f"No canonical stage policy defined for stage '{norm.value}'")
    return CANONICAL_STAGE_POLICIES[norm]


def resolve_cycle_for_kind(kind: Union[str, Any], risk_tier: Optional[str] = None) -> str:
    """Deterministically resolves the appropriate delivery cycle for a work item kind."""
    kind_str = getattr(kind, "value", str(kind)).lower().strip()
    if kind_str in {"project_setup", "setup", "new-project", "project-setup"}:
        return "new-project"
    if kind_str in {"bug", "bugfix"}:
        return "bugfix"
    if kind_str in {"spike", "study"}:
        return "spike"
    if kind_str in {"incident", "hotfix"}:
        return "incident"
    if kind_str in {"release", "rel"}:
        return "release"
    if kind_str in {"story", "us", "user-story"}:
        return "user-story"
    if kind_str in {"evolution", "evol"}:
        return "evolution"
    return "development"


def load_cycles_config(config_path: Optional[Path] = None) -> dict:
    """Loads and parses config/cycles.yaml from repository root or given path."""
    if config_path is None:
        runtime_root = Path(os.environ.get("SQUAD_RUNTIME", Path.cwd()))
        config_path = runtime_root / "config" / "cycles.yaml"
    if not config_path.is_file():
        raise ConfigurationError(f"cycles.yaml not found at: {config_path}")
    try:
        content = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        raise ConfigurationError(f"Failed to load cycles.yaml: {exc}") from exc


def load_workflow_config(config_path: Optional[Path] = None) -> dict:
    """Loads and parses config/workflow.yaml from repository root or given path."""
    if config_path is None:
        runtime_root = Path(os.environ.get("SQUAD_RUNTIME", Path.cwd()))
        config_path = runtime_root / "config" / "workflow.yaml"
    if not config_path.is_file():
        raise ConfigurationError(f"workflow.yaml not found at: {config_path}")
    try:
        content = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        raise ConfigurationError(f"Failed to load workflow.yaml: {exc}") from exc


def get_cycle_stages(cycle_name: str, cycles_cfg: Optional[dict] = None) -> List[LifecycleStage]:
    """Resolves the ordered list of canonical stages for a given cycle name.

    Prefers YAML-configured states if available, falling back to CANONICAL_CYCLES.
    """
    if cycles_cfg is None:
        try:
            cycles_cfg = load_cycles_config()
        except Exception:
            cycles_cfg = None

    if cycles_cfg and "cycles" in cycles_cfg and cycle_name in cycles_cfg["cycles"]:
        cycle_def = cycles_cfg["cycles"][cycle_name]
        raw_states = cycle_def.get("states", [])
        if raw_states:
            stages: List[LifecycleStage] = []
            for s in raw_states:
                try:
                    stages.append(normalize_stage(s))
                except InvalidTransitionError:
                    pass
            if stages:
                return stages

    if cycle_name in CANONICAL_CYCLES:
        return CANONICAL_CYCLES[cycle_name]

    raise ConfigurationError(f"Cycle '{cycle_name}' is not defined in configuration or canonical registry")
