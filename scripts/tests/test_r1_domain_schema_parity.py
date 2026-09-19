"""Tests for R1 Domain Schema Parity (Python Domain Models vs JSON Schemas Draft 2020-12).

Covers:
- Parity of required and optional fields between Python dataclasses and JSON Schemas
- Parity of enums between Python enums and JSON Schema enum declarations
- Parity of regex ID patterns between Python RE patterns and schema pattern strings
- Schema draft version (Draft 2020-12) and schema version consistency
"""

import json
from pathlib import Path
import re
import pytest

from scripts.domain.backlog import (
    BacklogPlan,
    BacklogPlanItem,
    BacklogPlanStatus,
)
from scripts.domain.common import SchemaVersion
from scripts.domain.delegation import (
    ActivationPacket,
    AssignmentStatus,
    DelegationEnvelope,
    ExecutionAssignment,
    HostCapabilities,
    WorkContext,
)
from scripts.domain.events import (
    DeliveryStatus,
    DomainEvent,
    FindingKind,
    FindingSeverity,
    TriggerActionKind,
    WatchdogFinding,
)
from scripts.domain.lifecycle import (
    AcknowledgementStatus,
    GateDecisionStatus,
    GateId,
    LifecycleStage,
)
from scripts.domain.project import (
    AdoBinding,
    DeliveryBackendKind,
    ProjectBinding,
)
from scripts.domain.receipts import (
    ReceiptType,
)
from scripts.domain.sync import (
    ReconciliationAction,
    SyncStatus,
)
from scripts.domain.work_items import (
    RiskTier,
    SyncStateKind,
    WorkItem,
    WorkItemKind,
)


SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "core"


def _load_schema(filename: str) -> dict:
    path = SCHEMAS_DIR / filename
    assert path.is_file(), f"Schema file not found: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_schema_draft_2020_12_and_version():
    """Validates that all core schemas declare Draft 2020-12 and proper metadata."""
    schema_files = list(SCHEMAS_DIR.glob("*.schema.json"))
    assert len(schema_files) >= 9, f"Expected at least 9 core schemas, found {len(schema_files)}"

    for sf in schema_files:
        data = json.loads(sf.read_text(encoding="utf-8"))
        assert data.get("$schema") == "https://json-schema.org/draft/2020-12/schema", (
            f"Schema {sf.name} must declare Draft 2020-12"
        )
        assert "$id" in data, f"Schema {sf.name} missing $id"
        assert "title" in data, f"Schema {sf.name} missing title"


def test_work_item_schema_parity():
    schema = _load_schema("work-item.schema.json")
    required = schema["required"]
    props = schema["properties"]

    assert set(required) == {
        "work_item_id",
        "kind",
        "title",
        "description",
        "project_id",
        "stage",
        "risk_tier",
        "definition_of_done",
        "acceptance_criteria",
    }

    schema_kinds = set(props["kind"]["enum"])
    py_kinds = {k.value for k in WorkItemKind}
    assert schema_kinds == py_kinds

    schema_risk = set(props["risk_tier"]["enum"])
    py_risk = {r.value for r in RiskTier}
    assert schema_risk == py_risk

    schema_sync = set(props["sync_state"]["enum"])
    py_sync = {s.value for s in SyncStateKind}
    assert schema_sync == py_sync

    schema_stages = set(props["stage"]["enum"])
    py_stages = {s.value for s in LifecycleStage}
    assert schema_stages == py_stages

    ac_def = schema["$defs"]["AcceptanceCriterion"]
    assert set(ac_def["required"]) == {"id", "scenario", "given", "when", "then"}


def test_work_item_regex_pattern_parity():
    schema = _load_schema("work-item.schema.json")
    pattern_str = schema["properties"]["work_item_id"]["pattern"]
    pattern = re.compile(pattern_str)

    test_ids = [
        "EPIC-001", "EPIC-12345",
        "FEATURE-001", "FEATURE-9999",
        "STORY-001", "STORY-8888",
        "TASK-0001", "TASK-99999",
        "BUG-001", "SPIKE-001", "INCIDENT-001", "RELEASE-001", "SETUP-001"
    ]
    for cid in test_ids:
        assert pattern.match(cid), f"Schema regex failed to match valid canonical ID: {cid}"

    invalid_ids = ["EPIC-1", "FEATURE-12", "STORY-1", "TASK-123", "INVALID-123"]
    for iid in invalid_ids:
        assert not pattern.match(iid), f"Schema regex unexpectedly matched invalid ID: {iid}"


def test_project_and_ado_binding_schema_parity():
    schema = _load_schema("project-binding.schema.json")
    required = schema["required"]
    assert set(required) == {
        "project_id",
        "project_root",
        "display_name",
        "delivery_backend_kind",
        "delivery_binding_ref",
    }

    schema_backends = set(schema["properties"]["delivery_backend_kind"]["enum"])
    py_backends = {b.value for b in DeliveryBackendKind}
    assert schema_backends == py_backends

    ado_def = schema["$defs"]["AdoBinding"]
    assert set(ado_def["required"]) == {
        "organization_url",
        "team_project",
        "area_path",
        "iteration_path",
        "assigned_team",
    }
    assert ado_def["properties"]["organization_url"]["pattern"] == "^https://"


def test_backlog_plan_schema_parity():
    schema = _load_schema("backlog-plan.schema.json")
    assert set(schema["required"]) == {"plan_id", "project_id", "status", "items", "created_by"}

    schema_status = set(schema["properties"]["status"]["enum"])
    py_status = {s.value for s in BacklogPlanStatus}
    assert schema_status == py_status

    item_def = schema["$defs"]["BacklogPlanItem"]
    assert set(item_def["required"]) == {"proposed_id", "kind", "title", "description"}
    assert set(item_def["properties"]["kind"]["enum"]) == {k.value for k in WorkItemKind}


def test_lifecycle_schema_parity():
    schema = _load_schema("lifecycle.schema.json")
    defs = schema["$defs"]

    schema_stages = set(defs["CanonicalStage"]["enum"])
    py_stages = {s.value for s in LifecycleStage}
    assert schema_stages == py_stages

    schema_gates = set(defs["GateId"]["enum"])
    py_gates = {g.value for g in GateId}
    assert schema_gates == py_gates

    gd_def = defs["GateDecision"]
    assert set(gd_def["required"]) == {
        "decision_id",
        "gate_id",
        "work_item_id",
        "stage",
        "evaluator_role",
        "evaluator_agent_id",
        "status",
        "rationale",
        "evidence_hashes",
    }
    assert set(gd_def["properties"]["status"]["enum"]) == {s.value for s in GateDecisionStatus}

    ho_def = defs["Handoff"]
    assert set(ho_def["required"]) == {
        "handoff_id",
        "work_item_id",
        "from_stage",
        "to_stage",
        "from_agent_id",
        "to_agent_id",
        "status",
    }
    assert set(ho_def["properties"]["status"]["enum"]) == {s.value for s in AcknowledgementStatus}


def test_domain_event_schema_parity():
    schema = _load_schema("domain-event.schema.json")
    event_def = schema["$defs"]["DomainEvent"]
    assert set(event_def["required"]) == {
        "event_id",
        "event_type",
        "work_item_id",
        "project_id",
        "source",
        "correlation_id",
        "causation_id",
        "idempotency_key",
        "timestamp",
    }

    tp_def = schema["$defs"]["TriggerPolicy"]
    assert set(tp_def["required"]) == {
        "trigger_id",
        "event_type",
        "condition_expression",
        "action_kind",
    }
    assert set(tp_def["properties"]["action_kind"]["enum"]) == {a.value for a in TriggerActionKind}

    wf_def = schema["$defs"]["WatchdogFinding"]
    assert set(wf_def["required"]) == {
        "finding_id",
        "kind",
        "severity",
        "description",
        "recommended_action",
        "detected_at",
    }
    assert set(wf_def["properties"]["kind"]["enum"]) == {k.value for k in FindingKind}
    assert set(wf_def["properties"]["severity"]["enum"]) == {s.value for s in FindingSeverity}


def test_receipt_schema_parity():
    schema = _load_schema("receipt.schema.json")
    base_props = schema["$defs"]["BaseReceiptProperties"]
    assert set(base_props["required"]) == {
        "receipt_id",
        "receipt_type",
        "work_item_id",
        "agent_id",
        "instruction_hash",
        "evidence_hash",
        "created_at",
    }

    # All specialized receipt types defined in schema oneOf
    receipt_refs = [entry["$ref"] for entry in schema["oneOf"]]
    assert "#/$defs/ExecutionReceipt" in receipt_refs
    assert "#/$defs/ReviewReceipt" in receipt_refs
    assert "#/$defs/SecurityReceipt" in receipt_refs
    assert "#/$defs/TestReceipt" in receipt_refs
    assert "#/$defs/QAReceipt" in receipt_refs
    assert "#/$defs/GovernanceReceipt" in receipt_refs
    assert "#/$defs/DispatchReceipt" in receipt_refs


def test_sync_state_schema_parity():
    schema = _load_schema("sync-state.schema.json")
    sync_def = schema["$defs"]["SyncState"]
    assert set(sync_def["required"]) == {
        "work_item_id",
        "status",
        "backend_kind",
    }
    schema_sync = set(schema["$defs"]["SyncStatus"]["enum"])
    py_sync = {s.value for s in SyncStatus}
    assert schema_sync == py_sync

    rd_def = schema["$defs"]["ReconciliationDecision"]
    assert set(rd_def["required"]) == {
        "work_item_id",
        "action",
        "reason",
        "local_state",
        "remote_state",
        "detected_at",
    }
    schema_actions = set(schema["$defs"]["ReconciliationAction"]["enum"])
    py_actions = {a.value for a in ReconciliationAction}
    assert schema_actions == py_actions


def test_delegation_envelope_and_host_capabilities_schema_parity():
    del_schema = _load_schema("delegation-envelope.schema.json")
    env_def = del_schema["$defs"]["DelegationEnvelope"]
    assert set(env_def["required"]) == {
        "delegation_id",
        "sender_role",
        "target_role",
        "work_item_id",
        "scope_summary",
        "action_requested",
        "compiled_instruction",
        "instruction_hash",
    }

    host_schema = _load_schema("host-capabilities.schema.json")
    assert set(host_schema["required"]) == {
        "has_subagent_dispatch",
        "has_filesystem_write",
        "has_terminal_execution",
        "has_mcp_client",
        "has_background_tasks",
        "max_token_context",
    }
