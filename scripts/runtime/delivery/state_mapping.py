"""Deterministic Bidirectional State and Board Column Mapping.

Maps canonical R4 lifecycle stages (13 stages) to Azure Boards System.State
and System.BoardColumn across process templates (Agile, Scrum, Basic, CMMI).
Enforces tag disambiguation ('stage:<STAGE>') to resolve collapsing states.

Strictly stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from scripts.domain.lifecycle import LifecycleStage

# Canonical 7 Board Columns per Section 14 of R6 spec
CANONICAL_BOARD_COLUMNS = [
    "Blueprint",
    "Scaffolding",
    "Implementation",
    "Code Review",
    "Security Review",
    "Quality Validation",
    "Done",
]

# Mapping from canonical LifecycleStage to Board Column name
STAGE_TO_BOARD_COLUMN_MAP: Dict[LifecycleStage, str] = {
    LifecycleStage.INTAKE: "Blueprint",
    LifecycleStage.DISCOVERY: "Blueprint",
    LifecycleStage.REQUIREMENTS_PRODUCT: "Blueprint",
    LifecycleStage.PLANNING: "Blueprint",
    LifecycleStage.ARCHITECTURE_DESIGN: "Blueprint",
    LifecycleStage.READINESS_SCAFFOLDING: "Scaffolding",
    LifecycleStage.IMPLEMENTATION: "Implementation",
    LifecycleStage.CODE_REVIEW: "Code Review",
    LifecycleStage.SECURITY_REVIEW: "Security Review",
    LifecycleStage.TEST_VALIDATION: "Quality Validation",
    LifecycleStage.QA_VALIDATION: "Quality Validation",
    LifecycleStage.GOVERNANCE_RELEASE: "Quality Validation",
    LifecycleStage.DONE: "Done",
}

# Mapping from Board Column name to primary canonical LifecycleStage
BOARD_COLUMN_TO_STAGE_MAP: Dict[str, LifecycleStage] = {
    "blueprint": LifecycleStage.INTAKE,
    "scaffolding": LifecycleStage.READINESS_SCAFFOLDING,
    "implementation": LifecycleStage.IMPLEMENTATION,
    "code review": LifecycleStage.CODE_REVIEW,
    "security review": LifecycleStage.SECURITY_REVIEW,
    "quality validation": LifecycleStage.TEST_VALIDATION,
    "done": LifecycleStage.DONE,
}

# Board Column Types (incoming, inProgress, outgoing)
BOARD_COLUMN_TYPES: Dict[str, str] = {
    "Blueprint": "incoming",
    "Scaffolding": "inProgress",
    "Implementation": "inProgress",
    "Code Review": "inProgress",
    "Security Review": "inProgress",
    "Quality Validation": "inProgress",
    "Done": "outgoing",
}

# Template-specific System.State mappings per Section 14
AGILE_STATE_MAP: Dict[LifecycleStage, str] = {
    LifecycleStage.INTAKE: "New",
    LifecycleStage.DISCOVERY: "New",
    LifecycleStage.REQUIREMENTS_PRODUCT: "New",
    LifecycleStage.PLANNING: "New",
    LifecycleStage.ARCHITECTURE_DESIGN: "New",
    LifecycleStage.READINESS_SCAFFOLDING: "Active",
    LifecycleStage.IMPLEMENTATION: "Active",
    LifecycleStage.CODE_REVIEW: "Active",
    LifecycleStage.SECURITY_REVIEW: "Active",
    LifecycleStage.TEST_VALIDATION: "Resolved",
    LifecycleStage.QA_VALIDATION: "Resolved",
    LifecycleStage.GOVERNANCE_RELEASE: "Resolved",
    LifecycleStage.DONE: "Closed",
}

SCRUM_STATE_MAP: Dict[LifecycleStage, str] = {
    LifecycleStage.INTAKE: "To Do",
    LifecycleStage.DISCOVERY: "To Do",
    LifecycleStage.REQUIREMENTS_PRODUCT: "To Do",
    LifecycleStage.PLANNING: "To Do",
    LifecycleStage.ARCHITECTURE_DESIGN: "To Do",
    LifecycleStage.READINESS_SCAFFOLDING: "In Progress",
    LifecycleStage.IMPLEMENTATION: "In Progress",
    LifecycleStage.CODE_REVIEW: "In Progress",
    LifecycleStage.SECURITY_REVIEW: "In Progress",
    LifecycleStage.TEST_VALIDATION: "Done",
    LifecycleStage.QA_VALIDATION: "Done",
    LifecycleStage.GOVERNANCE_RELEASE: "Done",
    LifecycleStage.DONE: "Done",
}

BASIC_STATE_MAP: Dict[LifecycleStage, str] = {
    LifecycleStage.INTAKE: "To Do",
    LifecycleStage.DISCOVERY: "To Do",
    LifecycleStage.REQUIREMENTS_PRODUCT: "To Do",
    LifecycleStage.PLANNING: "To Do",
    LifecycleStage.ARCHITECTURE_DESIGN: "To Do",
    LifecycleStage.READINESS_SCAFFOLDING: "Doing",
    LifecycleStage.IMPLEMENTATION: "Doing",
    LifecycleStage.CODE_REVIEW: "Doing",
    LifecycleStage.SECURITY_REVIEW: "Doing",
    LifecycleStage.TEST_VALIDATION: "Done",
    LifecycleStage.QA_VALIDATION: "Done",
    LifecycleStage.GOVERNANCE_RELEASE: "Done",
    LifecycleStage.DONE: "Done",
}

CMMI_STATE_MAP: Dict[LifecycleStage, str] = {
    LifecycleStage.INTAKE: "Proposed",
    LifecycleStage.DISCOVERY: "Proposed",
    LifecycleStage.REQUIREMENTS_PRODUCT: "Proposed",
    LifecycleStage.PLANNING: "Proposed",
    LifecycleStage.ARCHITECTURE_DESIGN: "Proposed",
    LifecycleStage.READINESS_SCAFFOLDING: "Active",
    LifecycleStage.IMPLEMENTATION: "Active",
    LifecycleStage.CODE_REVIEW: "Active",
    LifecycleStage.SECURITY_REVIEW: "Active",
    LifecycleStage.TEST_VALIDATION: "Resolved",
    LifecycleStage.QA_VALIDATION: "Resolved",
    LifecycleStage.GOVERNANCE_RELEASE: "Resolved",
    LifecycleStage.DONE: "Closed",
}

PROCESS_TEMPLATE_STATE_MAPS = {
    "agile": AGILE_STATE_MAP,
    "scrum": SCRUM_STATE_MAP,
    "basic": BASIC_STATE_MAP,
    "cmmi": CMMI_STATE_MAP,
}

# WorkItemKind mapping across process templates (Section 6)
WORK_ITEM_TYPE_MAPS = {
    "agile": {
        "epic": "Epic",
        "feature": "Feature",
        "story": "User Story",
        "task": "Task",
        "bug": "Bug",
        "spike": "User Story",
    },
    "scrum": {
        "epic": "Epic",
        "feature": "Feature",
        "story": "Product Backlog Item",
        "task": "Task",
        "bug": "Bug",
        "spike": "Product Backlog Item",
    },
    "basic": {
        "epic": "Epic",
        "feature": "Issue",
        "story": "Issue",
        "task": "Task",
        "bug": "Issue",
        "spike": "Issue",
    },
    "cmmi": {
        "epic": "Epic",
        "feature": "Feature",
        "story": "Requirement",
        "task": "Task",
        "bug": "Bug",
        "spike": "Requirement",
    },
}

_STAGE_TAG_RE = re.compile(r"^stage:([a-zA-Z0-9_-]+)$", re.IGNORECASE)


@dataclass(frozen=True)
class AzureStateMapping:
    """Outbound representation for Azure DevOps card state, board column, and stage tag."""

    state: str
    board_column: str
    column_type: str
    stage_tag: str
    stage: LifecycleStage


def normalize_template_name(process_template: Optional[str]) -> str:
    """Normalizes process template name to lowercase standard key ('agile', 'scrum', 'basic', 'cmmi')."""
    if not process_template:
        return "agile"
    tmpl = process_template.strip().lower()
    if "scrum" in tmpl:
        return "scrum"
    if "basic" in tmpl:
        return "basic"
    if "cmmi" in tmpl:
        return "cmmi"
    return "agile"


def stage_to_board_column(stage: Union[LifecycleStage, str]) -> str:
    """Returns canonical Kanban board column name for a lifecycle stage."""
    if isinstance(stage, str):
        stage = LifecycleStage(stage.upper())
    return STAGE_TO_BOARD_COLUMN_MAP.get(stage, "Blueprint")


def stage_to_board_column_type(stage: Union[LifecycleStage, str]) -> str:
    """Returns Kanban column type ('incoming', 'inProgress', 'outgoing')."""
    col = stage_to_board_column(stage)
    return BOARD_COLUMN_TYPES.get(col, "inProgress")


def stage_to_azure_state(stage: Union[LifecycleStage, str], process_template: str = "Agile") -> str:
    """Maps a canonical LifecycleStage to the process template's System.State."""
    if isinstance(stage, str):
        stage = LifecycleStage(stage.upper())
    tmpl_key = normalize_template_name(process_template)
    state_map = PROCESS_TEMPLATE_STATE_MAPS.get(tmpl_key, AGILE_STATE_MAP)
    return state_map.get(stage, "New")


def map_stage_to_azure(stage: Union[LifecycleStage, str], process_template: str = "Agile") -> AzureStateMapping:
    """Computes comprehensive outbound state, column, and tag mapping for a lifecycle stage."""
    if isinstance(stage, str):
        stage = LifecycleStage(stage.upper())
    state = stage_to_azure_state(stage, process_template)
    column = stage_to_board_column(stage)
    col_type = stage_to_board_column_type(stage)
    tag = f"stage:{stage.value}"
    return AzureStateMapping(
        state=state,
        board_column=column,
        column_type=col_type,
        stage_tag=tag,
        stage=stage,
    )


def extract_stage_from_tags(tags: Union[List[str], str, None]) -> Optional[LifecycleStage]:
    """Inspects System.Tags for 'stage:<STAGE>' tag, enabling lossless stage recovery."""
    if not tags:
        return None
    tag_list: List[str] = []
    if isinstance(tags, str):
        tag_list = [t.strip() for t in tags.split(";") if t.strip()]
    elif isinstance(tags, list):
        tag_list = [str(t).strip() for t in tags if str(t).strip()]

    for t in tag_list:
        m = _STAGE_TAG_RE.match(t)
        if m:
            raw_stage = m.group(1).upper()
            try:
                return LifecycleStage(raw_stage)
            except ValueError:
                try:
                    return LifecycleStage(raw_stage.replace("-", "_"))
                except ValueError:
                    pass
    return None


def azure_to_lifecycle_stage(
    state: str,
    board_column: Optional[str] = None,
    tags: Union[List[str], str, None] = None,
    process_template: str = "Agile",
) -> LifecycleStage:
    """Deterministically resolves canonical LifecycleStage from Azure DevOps fields.

    Priority:
    1. Explicit 'stage:<STAGE>' tag in System.Tags (highest fidelity).
    2. Canonical System.BoardColumn mapping.
    3. Fallback to System.State mapping.
    """
    tagged_stage = extract_stage_from_tags(tags)
    if tagged_stage is not None:
        return tagged_stage

    if board_column:
        col_norm = board_column.strip().lower()
        if col_norm in BOARD_COLUMN_TO_STAGE_MAP:
            return BOARD_COLUMN_TO_STAGE_MAP[col_norm]

    st_clean = state.strip().lower()
    tmpl = normalize_template_name(process_template)

    if tmpl == "scrum":
        if st_clean == "to do":
            return LifecycleStage.INTAKE
        elif st_clean == "in progress":
            return LifecycleStage.IMPLEMENTATION
        elif st_clean == "done":
            return LifecycleStage.DONE
    elif tmpl == "basic":
        if st_clean == "to do":
            return LifecycleStage.INTAKE
        elif st_clean == "doing":
            return LifecycleStage.IMPLEMENTATION
        elif st_clean == "done":
            return LifecycleStage.DONE
    else:
        if st_clean in ("new", "proposed"):
            return LifecycleStage.INTAKE
        elif st_clean == "active":
            return LifecycleStage.IMPLEMENTATION
        elif st_clean == "resolved":
            return LifecycleStage.TEST_VALIDATION
        elif st_clean in ("closed", "done"):
            return LifecycleStage.DONE

    return LifecycleStage.INTAKE


def work_item_kind_to_ado_type(kind: str, process_template: str = "Agile") -> str:
    """Maps canonical WorkItemKind (EPIC, FEATURE, STORY, TASK, BUG, SPIKE) to ADO WIT type."""
    tmpl = normalize_template_name(process_template)
    tmpl_map = WORK_ITEM_TYPE_MAPS.get(tmpl, WORK_ITEM_TYPE_MAPS["agile"])
    k_norm = kind.strip().lower()
    return tmpl_map.get(k_norm, "User Story")
