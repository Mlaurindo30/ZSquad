"""
R0 Red Diagnostic Suite — MCP, Session, Preflight, and Delegation Contracts.
Proves failures in R0-DEL-001 through R0-DEL-007 against current broken runtime behavior.
DO NOT FIX IN R0. These tests MUST FAIL (RED) to demonstrate the current defects.
"""

from pathlib import Path
import sys
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))

from agent_squad import AgentSquad, read_yaml
from integrations.resolvers.session_manager import start_session
from integrations.resolvers.assignment_resolver import get_assignment, prepare_delegation, create_handoff
from integrations.resolvers.impact_analyzer import preflight
from integrations.mcp_session_store import SessionStore
from integrations.mcp_db_client import DBClient
from integrations.resolvers import ResolverContext



@pytest.fixture
def mcp_context():
    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "test.db"
    store = SessionStore()
    db = DBClient(str(db_path))
    ctx = ResolverContext(db_path=str(db_path), config_dir=str(ROOT / "config"))
    yield store, db, ctx, Path(temp_dir.name)
    try:
        if hasattr(db, "conn") and db.conn:
            db.conn.close()
    except Exception:
        pass
    try:
        temp_dir.cleanup()
    except Exception:
        pass


def test_r0_del_001_invalid_session_must_fail_closed(mcp_context):
    """
    R0-DEL-001: Invalid session fallback.
    Invariant: Operations referencing nonexistent sessions must fail closed (raise error).
    Current defect: create_handoff falls back to {'project_root': 'test_root', 'work_item': 'test_item'},
    and prepare_delegation falls back to os.getcwd() and 'UNSPECIFIED'.
    """
    store, db, ctx, _ = mcp_context

    # Invariant: create_handoff with nonexistent session must fail closed with error.
    # Current behavior: it quietly succeeds using fallback 'test_root' and 'test_item'!
    with pytest.raises((ValueError, KeyError), match="(?i)(invalid session|session not found|session missing)"):
        create_handoff({"session": "non-existent-session-123"}, ctx, store, db)


def test_r0_del_002_preflight_must_validate_real_conditions(mcp_context):
    """
    R0-DEL-002: Preflight is real?
    Invariant: preflight must validate that session exists and requested paths actually exist on disk.
    Current defect: preflight is a 1-line dummy returning hardcoded {'status': 'allow'} unconditionally.
    """
    store, _, ctx, _ = mcp_context

    # Pass completely nonexistent session and fake paths
    args = {
        "session": "ghost-session-999",
        "briefing_hash": "deadbeef",
        "paths": ["/this/path/does/not/exist/at/all/123456.txt"],
    }
    result = preflight(args, ctx, store)

    # Invariant: preflight must reject nonexistent session and paths.
    # Current behavior: returns {'status': 'allow', 'reason': 'Paths exist and session is active'}!
    assert result.get("status") != "allow", (
        f"R0-DEL-002 CONFIRMED: preflight returned 'allow' for nonexistent session and paths: {result}"
    )


def test_r0_del_003_prepare_delegation_must_not_swallow_render_failure(mcp_context):
    """
    R0-DEL-003: Render failure swallowed.
    Invariant: If prompt rendering fails during prepare_delegation, delegation must fail fast with an exception.
    Current defect: prepare_delegation wraps render_agent_prompt in try...except Exception: pass,
    swallowing all errors and returning empty rendered_prompt without warning.
    """
    store, _, ctx, temp_path = mcp_context
    session = store.create_session("host", str(temp_path), "US-TEST", "hash")

    # Pass an invalid role that causes render error
    args = {
        "session": session["session_id"],
        "target_role": "invalid-nonexistent-agent-role",
        "scope": "Testing render failure",
        "action": "Execution",
    }

    # Invariant: delegation must not swallow render failures and return an empty prompt.
    # Current behavior: prepare_delegation swallows exception and returns rendered_prompt=''!
    res = prepare_delegation(args, ctx, store)
    assert res.get("rendered_prompt"), (
        "R0-DEL-003 CONFIRMED: render failure was swallowed quietly and rendered_prompt was returned empty"
    )



def test_r0_del_004_compiled_instruction_must_be_authoritative_payload(mcp_context):
    """
    R0-DEL-004: Compiled instruction not authoritative.
    Invariant: The returned briefing hash must be derived from the authoritative compiled prompt,
    not a synthetic detached 8-block briefing string.
    Current defect: hash is computed from synthetic 'briefing' while rendered_prompt is an optional afterthought.
    """
    store, _, ctx, temp_path = mcp_context
    session = store.create_session("host", str(temp_path), "US-TEST", "hash")

    args = {
        "session": session["session_id"],
        "target_role": "software-engineer",
        "scope": "Testing hash authority",
        "action": "Implementation",
    }
    result = prepare_delegation(args, ctx, store)


    import hashlib
    rendered = result.get("rendered_prompt", "")
    assert rendered, "rendered_prompt must not be empty"
    expected_prompt_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    # Invariant: result['hash'] must match the compiled rendered_prompt's hash.
    # Current behavior: result['hash'] is the hash of synthetic result['briefing'].
    assert result.get("hash") == expected_prompt_hash, (
        f"R0-DEL-004 CONFIRMED: Delegation hash {result.get('hash')} is derived from synthetic briefing, "
        f"not from authoritative compiled rendered_prompt ({expected_prompt_hash})"
    )


def test_r0_del_005_ambiguous_routing_must_not_silently_fallback_to_software_engineer(mcp_context):
    """
    R0-DEL-005: Routing fallback.
    Invariant: Ambiguous or unmatchable objective digest must result in NEEDS_ROUTING or error,
    never silent fallback to software-engineer.
    Current defect: get_assignment silently falls back to software-engineer if score == 0.
    """
    store, _, ctx, _ = mcp_context

    args = {
        "session": "session-test",
        "objective_digest": "xyz completely unknown objective without any keywords whatsoever 12345",
    }
    result = get_assignment(args, ctx, store)

    # Invariant: Must NOT silently assign to software-engineer.
    # Current behavior: returns persona_id='software-engineer'!
    assert result.get("persona_id") != "software-engineer", (
        f"R0-DEL-005 CONFIRMED: get_assignment silently fell back to software-engineer: {result}"
    )


def test_r0_del_006_skill_selection_budget_and_semantics(mcp_context):
    """
    R0-DEL-006: Skill-selection semantics.
    Invariant: Skill selection must not use an arbitrary truncation like skills[:7] without semantic budgeting.
    Current defect: assignment_resolver.py hardcodes 'skills[:7]' slice directly.
    """
    import inspect
    from integrations.resolvers import assignment_resolver

    src = inspect.getsource(assignment_resolver.get_assignment)

    # Invariant: get_assignment must not have raw hardcoded '[:7]' truncation.
    # Current behavior: contains '"skills": skills[:7]'
    assert "skills[:7]" not in src, (
        "R0-DEL-006 CONFIRMED: get_assignment contains hardcoded arbitrary slice 'skills[:7]'"
    )


def test_r0_del_007_compiled_context_must_include_all_ancestor_artifacts():
    """
    R0-DEL-007: Full work context missing.
    Invariant: When rendering context for a child Task, compiled context must include content from
    ancestor entities (Epic, Feature, Story).
    Current defect: _build_work_item_context only loads the item's own status.yaml, ignoring all ancestors.
    """
    import inspect
    from scripts import render_agent_prompt

    src = inspect.getsource(render_agent_prompt._build_work_item_context)

    # Invariant: _build_work_item_context must query and load ancestor hierarchy content.
    # Current behavior: only reads status_file.read_text() from the target item folder.
    assert "parent" in src or "ancestor" in src or "hierarchy" in src, (
        "R0-DEL-007 CONFIRMED: _build_work_item_context only loads local status.yaml without ancestor context"
    )
