"""State-aware governance gate eligibility and verification.

Strictly stdlib-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Set, Union
import yaml

from scripts.domain.lifecycle import GateDecisionStatus, GateId, LifecycleStage
from scripts.runtime.lifecycle.errors import GateNotEligibleError, GateNotPassedError
from scripts.runtime.lifecycle.policies import (
    CANONICAL_TO_LEGACY_STATE,
    LEGACY_STATE_TO_CANONICAL,
    normalize_stage,
)

# Canonical mapping from Gate to the strictly legal LifecycleStage where it may be evaluated
GATE_TO_STAGE_MAP: Dict[GateId, LifecycleStage] = {
    GateId.G1_PRODUCT: LifecycleStage.REQUIREMENTS_PRODUCT,
    GateId.G2_DESIGN: LifecycleStage.ARCHITECTURE_DESIGN,
    GateId.G3_READINESS: LifecycleStage.READINESS_SCAFFOLDING,
    GateId.G4_CODE_SECURITY: LifecycleStage.SECURITY_REVIEW,
    GateId.G5_QUALITY: LifecycleStage.QA_VALIDATION,
    GateId.G6_GOVERNANCE_RELEASE: LifecycleStage.GOVERNANCE_RELEASE,
}

# Reverse mapping: which gate is evaluated to exit from each stage
STAGE_TO_GATE_MAP: Dict[LifecycleStage, GateId] = {
    LifecycleStage.REQUIREMENTS_PRODUCT: GateId.G1_PRODUCT,
    LifecycleStage.ARCHITECTURE_DESIGN: GateId.G2_DESIGN,
    LifecycleStage.READINESS_SCAFFOLDING: GateId.G3_READINESS,
    LifecycleStage.SECURITY_REVIEW: GateId.G4_CODE_SECURITY,
    LifecycleStage.QA_VALIDATION: GateId.G5_QUALITY,
    LifecycleStage.GOVERNANCE_RELEASE: GateId.G6_GOVERNANCE_RELEASE,
}

# Aliases and historic names for gates
GATE_ALIASES: Dict[str, GateId] = {
    "g1": GateId.G1_PRODUCT,
    "g1-product": GateId.G1_PRODUCT,
    "gt-entry": GateId.G1_PRODUCT,
    "g2": GateId.G2_DESIGN,
    "g2-design": GateId.G2_DESIGN,
    "g3": GateId.G3_READINESS,
    "g3-readiness": GateId.G3_READINESS,
    "gt-design-review": GateId.G3_READINESS,
    "g4": GateId.G4_CODE_SECURITY,
    "g4-code-security": GateId.G4_CODE_SECURITY,
    "g5": GateId.G5_QUALITY,
    "g5-quality": GateId.G5_QUALITY,
    "g6": GateId.G6_GOVERNANCE_RELEASE,
    "g6-governance-release": GateId.G6_GOVERNANCE_RELEASE,
    "gt-done": GateId.G6_GOVERNANCE_RELEASE,
}


def normalize_gate_id(gate_id: Union[str, GateId]) -> GateId:
    """Normalizes string or enum gate identifier into canonical GateId."""
    if isinstance(gate_id, GateId):
        return gate_id

    cleaned = str(gate_id).strip().lower().replace("_", "-")
    if cleaned in GATE_ALIASES:
        return GATE_ALIASES[cleaned]

    for member in GateId:
        if member.value.lower() == cleaned:
            return member

    raise GateNotEligibleError(f"Unknown gate identifier: '{gate_id}'")


def is_gate_eligible(
    gate_id: Union[str, GateId],
    current_stage: Union[str, LifecycleStage],
) -> bool:
    """Checks whether the gate is legally eligible for evaluation in current_stage."""
    try:
        assert_gate_eligibility(gate_id, current_stage)
        return True
    except GateNotEligibleError:
        return False


def assert_gate_eligibility(
    gate_id: Union[str, GateId],
    current_stage: Union[str, LifecycleStage],
) -> None:
    """Enforces state eligibility for gate evaluation (resolves R0-LIFE-003).

    Evaluating a gate prematurely or out of order is strictly prohibited.
    For example, evaluating G5-quality or G6 while the item is in 'blueprint' raises GateNotEligibleError.

    Raises:
        GateNotEligibleError: If gate cannot legally be evaluated in current_stage.
    """
    canonical_gate = normalize_gate_id(gate_id)
    norm_stage = normalize_stage(current_stage)

    expected_stage = GATE_TO_STAGE_MAP.get(canonical_gate)
    if expected_stage is None:
        raise GateNotEligibleError(
            f"Gate '{canonical_gate.value}' has no registered stage mapping and is not eligible for state '{norm_stage.value}'"
        )

    # Allow compatibility where legacy 'blueprint' may evaluate G1, G2, or G3 (bundled planning/readiness)
    raw_stage_str = str(current_stage).strip().lower()
    is_blueprint_bundle = (
        raw_stage_str in {"blueprint", "requirements_product"}
        and canonical_gate in {GateId.G1_PRODUCT, GateId.G2_DESIGN, GateId.G3_READINESS}
    )

    if norm_stage != expected_stage and not is_blueprint_bundle:
        legacy_cur = CANONICAL_TO_LEGACY_STATE.get(norm_stage, norm_stage.value.lower())
        legacy_exp = CANONICAL_TO_LEGACY_STATE.get(expected_stage, expected_stage.value.lower())
        raise GateNotEligibleError(
            f"Gate '{canonical_gate.value}' is not eligible for state '{legacy_cur}'. "
            f"Legal evaluation stage is strictly '{legacy_exp}' ({expected_stage.value})."
        )


def verify_gate_approval(
    item_path: Path,
    gate_id: Union[str, GateId],
) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Inspects item's gate-decisions directory for an approved decision for gate_id.

    Returns:
        (True, decision_data) if approved, (False, None) otherwise.
    """
    canonical_gate = normalize_gate_id(gate_id)
    decisions_dir = item_path / "gate-decisions"
    if not decisions_dir.is_dir():
        return False, None

    valid_names = {
        canonical_gate.value.lower(),
        canonical_gate.value.lower().replace("-", "_"),
        canonical_gate.name.lower(),
    }
    # Check aliases
    for alias_name, gid in GATE_ALIASES.items():
        if gid == canonical_gate:
            valid_names.add(alias_name.lower())
            valid_names.add(alias_name.lower().replace("-", "_"))

    for dec_file in decisions_dir.glob("*.yaml"):
        try:
            content = dec_file.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                continue
            dec_gate = str(data.get("gate_id", "")).lower().replace("_", "-")
            file_stem = dec_file.stem.lower().replace("_", "-")

            matches = (
                dec_gate in valid_names
                or any(v in dec_gate for v in valid_names)
                or any(v in file_stem for v in valid_names)
            )
            if matches:
                status_val = str(data.get("decision", data.get("status", ""))).lower()
                if status_val in {"approved", "pass", "waived"}:
                    return True, data
        except Exception:
            continue

    return False, None
