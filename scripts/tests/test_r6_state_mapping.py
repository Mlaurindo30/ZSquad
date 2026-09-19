"""Canonical Tests for R6 Bidirectional State and Board Column Mapping (Section 49).

Covers:
- every active R4 stage has explicit mapping policy
- unsupported mapping fails closed
- board/state distinction handled
- process variation handled across Agile, Scrum, Basic, CMMI
- no hardcoded one-process assumption
- tag disambiguation ('stage:<STAGE>') roundtrip fidelity
"""

from __future__ import annotations

import pytest

from scripts.domain.lifecycle import LifecycleStage
from scripts.runtime.delivery.state_mapping import (
    CANONICAL_BOARD_COLUMNS,
    AzureStateMapping,
    azure_to_lifecycle_stage,
    extract_stage_from_tags,
    map_stage_to_azure,
    normalize_template_name,
    stage_to_azure_state,
    stage_to_board_column,
    stage_to_board_column_type,
    work_item_kind_to_ado_type,
)


def test_every_active_r4_stage_has_explicit_mapping_policy():
    """Every canonical R4 lifecycle stage (13 stages) has an explicit, non-empty mapping policy."""
    all_stages = list(LifecycleStage)
    assert len(all_stages) == 13

    for stage in all_stages:
        mapping = map_stage_to_azure(stage, process_template="Agile")
        assert isinstance(mapping, AzureStateMapping)
        assert mapping.state in ("New", "Active", "Resolved", "Closed")
        assert mapping.board_column in CANONICAL_BOARD_COLUMNS
        assert mapping.column_type in ("incoming", "inProgress", "outgoing")
        assert mapping.stage_tag == f"stage:{stage.value}"
        assert mapping.stage == stage


def test_unsupported_stage_mapping_fails_closed():
    """Attempting to map an unknown stage string raises ValueError."""
    with pytest.raises(ValueError):
        map_stage_to_azure("INVALID_STAGE_XYZ")


def test_board_column_and_state_distinction():
    """Multiple stages share System.State='Active' in Agile but possess distinct Board Columns."""
    scaffolding_map = map_stage_to_azure(LifecycleStage.READINESS_SCAFFOLDING, "Agile")
    impl_map = map_stage_to_azure(LifecycleStage.IMPLEMENTATION, "Agile")
    code_review_map = map_stage_to_azure(LifecycleStage.CODE_REVIEW, "Agile")
    sec_review_map = map_stage_to_azure(LifecycleStage.SECURITY_REVIEW, "Agile")

    # All share 'Active' state in Azure Boards Agile template
    assert scaffolding_map.state == "Active"
    assert impl_map.state == "Active"
    assert code_review_map.state == "Active"
    assert sec_review_map.state == "Active"

    # Distinct Board Columns maintain granular SDLC progression
    assert scaffolding_map.board_column == "Scaffolding"
    assert impl_map.board_column == "Implementation"
    assert code_review_map.board_column == "Code Review"
    assert sec_review_map.board_column == "Security Review"


def test_process_variation_handled():
    """State mappings vary accurately across Agile, Scrum, Basic, and CMMI."""
    # Implementation stage across templates
    agile_impl = stage_to_azure_state(LifecycleStage.IMPLEMENTATION, "Agile")
    scrum_impl = stage_to_azure_state(LifecycleStage.IMPLEMENTATION, "Scrum")
    basic_impl = stage_to_azure_state(LifecycleStage.IMPLEMENTATION, "Basic")
    cmmi_impl = stage_to_azure_state(LifecycleStage.IMPLEMENTATION, "CMMI")

    assert agile_impl == "Active"
    assert scrum_impl == "In Progress"
    assert basic_impl == "Doing"
    assert cmmi_impl == "Active"

    # Terminal Done stage across templates
    assert stage_to_azure_state(LifecycleStage.DONE, "Agile") == "Closed"
    assert stage_to_azure_state(LifecycleStage.DONE, "Scrum") == "Done"
    assert stage_to_azure_state(LifecycleStage.DONE, "Basic") == "Done"
    assert stage_to_azure_state(LifecycleStage.DONE, "CMMI") == "Closed"


def test_no_hardcoded_one_process_assumption():
    """Work item types vary according to process template without hardcoding Agile."""
    templates = ["agile", "scrum", "basic", "cmmi"]
    story_types = {tmpl: work_item_kind_to_ado_type("STORY", tmpl) for tmpl in templates}

    assert story_types["agile"] == "User Story"
    assert story_types["scrum"] == "Product Backlog Item"
    assert story_types["basic"] == "Issue"
    assert story_types["cmmi"] == "Requirement"

    # Verify normalization handles mixed case and whitespace
    assert normalize_template_name("  SCRUM-v2 ") == "scrum"
    assert normalize_template_name("Basic-Process") == "basic"
    assert normalize_template_name("cmmi_enterprise") == "cmmi"
    assert normalize_template_name(None) == "agile"


def test_tag_disambiguation_lossless_roundtrip():
    """System.Tags 'stage:<STAGE>' enables exact canonical stage recovery from collapsed state."""
    # When remote state is 'Active', tag recovers specific canonical stage
    recovered_sec = azure_to_lifecycle_stage(
        state="Active",
        board_column="Implementation",  # divergent column
        tags=["agent-squad", "stage:SECURITY_REVIEW", "priority:high"],
    )
    assert recovered_sec == LifecycleStage.SECURITY_REVIEW

    recovered_code = azure_to_lifecycle_stage(
        state="Active",
        tags="agent-squad; stage:CODE_REVIEW; canonical_id:STORY-123",
    )
    assert recovered_code == LifecycleStage.CODE_REVIEW


def test_inbound_resolution_hierarchy():
    """Inbound stage resolution follows: 1. Tags -> 2. BoardColumn -> 3. State."""
    # 1. Tags take precedence over board column
    st1 = azure_to_lifecycle_stage(
        state="Active",
        board_column="Implementation",
        tags=["stage:PLANNING"],
    )
    assert st1 == LifecycleStage.PLANNING

    # 2. In absence of tag, Board Column takes precedence over State
    st2 = azure_to_lifecycle_stage(
        state="Active",
        board_column="Quality Validation",
        tags=[],
    )
    assert st2 == LifecycleStage.TEST_VALIDATION

    # 3. In absence of tag and board column, State is used
    st3 = azure_to_lifecycle_stage(
        state="Resolved",
        board_column=None,
        tags=[],
        process_template="Agile",
    )
    assert st3 == LifecycleStage.TEST_VALIDATION
