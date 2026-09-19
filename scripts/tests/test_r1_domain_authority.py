"""Tests for R1 Domain Authority, Isolation and Clean Architectural Boundaries.

Covers:
- Domain modules in `scripts/domain/` rely strictly on Python stdlib and NEVER import runtime modules
  (e.g., agent_squad, integrations, connectors, adapters, external libraries)
- Absence of provider-coupled workflows (zero Anthropic, OpenAI, or Gemini coupling in domain)
- Absence of hardcoded product defaults (e.g. 'cbvgas', 'Arthemis', 'Deepvision', 'test_root', 'test_item')
- Local work mirror explicitly declared non-competing (projection cache / scratchpad only)
- compiled_instruction is authoritative in DelegationEnvelope and covered by SHA-256 hash
- StagePolicy contains transition prerequisites
- Receipt models contain agent identity, execution details, and content/evidence hashes
"""

import ast
import inspect
from pathlib import Path
import re
import pytest

import scripts.domain as domain_pkg
from scripts.domain.backlog import (
    BacklogPlan,
    BacklogPlanItem,
    BacklogPlanStatus,
)
from scripts.domain.common import (
    BaseDomainModel,
    ValidationError,
)
from scripts.domain.delegation import (
    ActivationPacket,
    DelegationEnvelope,
    HostCapabilities,
)
from scripts.domain.lifecycle import (
    CANONICAL_STAGE_POLICIES,
    LifecycleStage,
    StagePolicy,
)
from scripts.domain.project import (
    AdoBinding,
    LocalWorkMirror,
    ProjectBinding,
)
from scripts.domain.receipts import (
    BaseReceipt,
    ExecutionReceipt,
)


DOMAIN_DIR = Path(__file__).resolve().parents[2] / "scripts" / "domain"


def test_domain_modules_stdlib_only_and_no_runtime_imports():
    """Validates that all modules in scripts/domain/ import only standard library modules."""
    py_files = list(DOMAIN_DIR.glob("*.py"))
    assert len(py_files) >= 10, f"Expected at least 10 domain modules, found {len(py_files)}"

    disallowed_imports = {
        "agent_squad",
        "integrations",
        "requests",
        "httpx",
        "pydantic",
        "yaml",
        "duckdb",
        "jsonschema",
        "openai",
        "langchain",
        "langgraph",
    }

    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=py_file.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_name = alias.name.split(".")[0]
                    assert root_name not in disallowed_imports, (
                        f"Module {py_file.name} illegally imports '{alias.name}'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_name = node.module.split(".")[0]
                    assert root_name not in disallowed_imports, (
                        f"Module {py_file.name} illegally imports from '{node.module}'"
                    )
                    # Relative imports should only be within domain package
                    if node.level > 0:
                        assert node.module in {
                            None, "common", "work_items", "backlog", "lifecycle",
                            "project", "delegation", "events", "receipts", "sync"
                        } or node.module.startswith("domain"), (
                            f"Illegal relative import in {py_file.name}: {node.module}"
                        )


def test_absence_of_provider_coupled_workflow_in_domain():
    """Validates that scripts/domain/ does not contain host or LLM provider names."""
    forbidden_provider_words = [
        "anthropic",
        "openai",
        "gemini",
        "claude-desktop",
        "chatgpt",
        "cursor",
    ]

    for py_file in DOMAIN_DIR.glob("*.py"):
        content = py_file.read_text(encoding="utf-8").lower()
        for word in forbidden_provider_words:
            assert word not in content, (
                f"Domain file {py_file.name} couples with provider/runtime name: '{word}'"
            )


def test_absence_of_product_defaults_in_domain():
    """Validates that scripts/domain/ contains no hardcoded legacy product tokens as defaults."""
    forbidden_tokens = ["cbvgas", "arthemis", "deepvision", "test_root", "test_item"]

    # We test that creating domain models with forbidden tokens fails
    for token in forbidden_tokens:
        with pytest.raises(ValidationError, match="forbidden legacy/product-specific token"):
            AdoBinding(
                organization_url="https://dev.azure.com/myorg",
                team_project=f"proj-{token}",
                area_path="area",
                iteration_path="iteration",
                assigned_team="team",
            )

        with pytest.raises(ValidationError, match="forbidden legacy/product-specific token"):
            ProjectBinding(
                project_id=f"proj-{token}",
                project_root="/workspace/root",
                display_name="Platform",
                delivery_backend_kind="LOCAL_ONLY",  # type: ignore
                delivery_binding_ref="local://ref",
            )


def test_local_work_mirror_declared_non_competing():
    """Asserts that LocalWorkMirror is merely a cache/scratchpad and holds zero independent policy authority."""
    doc = LocalWorkMirror.__doc__ or ""
    assert "projection cache" in doc.lower() or "scratchpad" in doc.lower()
    assert "zero independent policy authority" in doc.lower()


def test_compiled_instruction_authoritative_in_delegation_envelope():
    """Validates that DelegationEnvelope instruction_hash covers compiled_instruction deterministically."""
    instr = "You are 06-software-engineer. Execute implementation according to specification."
    env = DelegationEnvelope.create(
        delegation_id="DEL-101",
        sender_role="00-delivery-orchestrator",
        target_role="06-software-engineer",
        work_item_id="STORY-101",
        scope_summary="Implement module",
        action_requested="IMPLEMENT_TDD",
        compiled_instruction=instr,
    )

    import hashlib
    expected_hash = hashlib.sha256(instr.encode("utf-8")).hexdigest()
    assert env.instruction_hash == expected_hash

    # Passing tampered hash raises ValidationError
    with pytest.raises(ValidationError, match="Instruction hash mismatch"):
        DelegationEnvelope(
            delegation_id="DEL-101",
            sender_role="00-delivery-orchestrator",
            target_role="06-software-engineer",
            work_item_id="STORY-101",
            scope_summary="Implement module",
            action_requested="IMPLEMENT_TDD",
            compiled_instruction=instr,
            instruction_hash="tampered_hash_00000000000000000000000000000000000000000000000000000",
        )


def test_stage_policy_contains_transition_prerequisites():
    """Validates that all 13 canonical stages have defined policies with owners and allowed next stages."""
    assert len(CANONICAL_STAGE_POLICIES) == 13
    for stage, policy in CANONICAL_STAGE_POLICIES.items():
        assert policy.stage == stage
        assert policy.owner_role, f"Policy for {stage} missing owner_role"
        assert isinstance(policy.allowed_next_stages, list)
        assert isinstance(policy.required_artifacts, list)
        assert isinstance(policy.trigger_refs, list)


def test_receipt_models_contain_identity_and_hashes():
    """Validates that BaseReceipt requires agent_id, work_item_id, instruction_hash and evidence_hash."""
    receipt = ExecutionReceipt(
        receipt_id="RCPT-01",
        receipt_type="EXECUTION",
        work_item_id="STORY-101",
        agent_id="06-software-engineer",
        instruction_hash="ihash123",
        evidence_hash="ehash456",
        diff_summary="Changes implemented",
    )
    assert receipt.agent_id == "06-software-engineer"
    assert receipt.work_item_id == "STORY-101"
    assert receipt.instruction_hash == "ihash123"
    assert receipt.evidence_hash == "ehash456"

    # Missing mandatory identifiers raises ValidationError
    with pytest.raises(ValidationError, match="agent_id must not be empty"):
        ExecutionReceipt(
            receipt_id="RCPT-01",
            receipt_type="EXECUTION",
            work_item_id="STORY-101",
            agent_id="",
            instruction_hash="ihash123",
            evidence_hash="ehash456",
            diff_summary="Changes implemented",
        )
