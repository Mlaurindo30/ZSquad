import copy
import json
from typing import Any, Dict, List, Optional

import pytest

from scripts.auto_correction import (
    HarnessState,
    LLMCallable,
    RefinementEdit,
    RefinementHistoryEntry,
    RefinementPlan,
    RefinementProposal,
    RefinementResult,
    apply_refinement_proposal,
    build_refinement_prompt,
    plan_refinement,
    rollback,
    summarize_for_refinement,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fake_llm_call(system_prompt: str, user_prompt: str, signal: Optional[str] = None) -> str:
    return json.dumps({
        "summary": "Add alpha entry",
        "rationale": "Conversation mentioned alpha",
        "expected_outcome": "alpha exists in state",
        "edits": [
            {
                "action": "create",
                "kind": "file",
                "id": "alpha",
                "title": "Alpha file",
                "content": "alpha content",
                "path": "alpha.txt",
                "reason": "mentioned in conversation",
            }
        ],
    })


def fake_llm_call_noop(system_prompt: str, user_prompt: str, signal: Optional[str] = None) -> str:
    return json.dumps({
        "summary": "no-op",
        "rationale": "nothing to improve",
        "expected_outcome": "no change",
        "edits": [],
    })


def fake_llm_call_invalid(system_prompt: str, user_prompt: str, signal: Optional[str] = None) -> str:
    return "not json"


def make_state(entries: Optional[Dict[str, Dict[str, Any]]] = None) -> HarnessState:
    return HarnessState(entries=entries or {}, refinements=[], metadata={})


def make_history() -> List[RefinementHistoryEntry]:
    return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlanRefinement:
    def test_returns_plan_without_mutating_state(self):
        state = make_state()
        history = make_history()
        plan = plan_refinement(
            messages=[{"role": "user", "content": "add alpha"}],
            state=state,
            history=history,
            llm_call=fake_llm_call,
            scope="local",
        )
        assert isinstance(plan, RefinementPlan)
        assert plan.proposal.summary == "Add alpha entry"
        assert len(plan.proposal.edits) == 1
        assert plan.baseline_state is not None
        assert state.entries == {}
        assert state.refinements == []

    def test_generates_unique_id(self):
        state = make_state()
        history = make_history()
        plan = plan_refinement(
            messages=[],
            state=state,
            history=history,
            llm_call=fake_llm_call,
            scope="global",
        )
        assert plan.id.startswith("refine_")
        assert plan.rollback_scope == "global"

    def test_invalid_json_raises(self):
        state = make_state()
        history = make_history()
        with pytest.raises(RuntimeError, match="Refinement LLM call failed"):
            plan_refinement(
                messages=[],
                state=state,
                history=history,
                llm_call=fake_llm_call_invalid,
                scope="local",
            )

    def test_llm_exception_raises(self):
        def failing_call(system_prompt, user_prompt, signal=None):
            raise RuntimeError("LLM down")

        state = make_state()
        history = make_history()
        with pytest.raises(RuntimeError, match="LLM down"):
            plan_refinement(
                messages=[],
                state=state,
                history=history,
                llm_call=failing_call,
                scope="local",
            )

    def test_build_prompt_contains_scope_policy(self):
        state = make_state()
        history = make_history()
        prompt = build_refinement_prompt(
            messages=[{"role": "user", "content": "test"}],
            state=state,
            history=history,
            scope="global",
        )
        assert "global" in prompt
        assert "scope_policy" in prompt


class TestApplyRefinementProposal:
    def test_create_edit_adds_entry(self):
        state = make_state()
        history = make_history()
        plan = plan_refinement(
            messages=[],
            state=state,
            history=history,
            llm_call=fake_llm_call,
            scope="local",
        )
        result = apply_refinement_proposal(plan, state, history)
        assert result.id == plan.id
        assert len(result.applied_edits) == 1
        assert result.applied_edits[0].applied is True
        assert "file" in state.entries
        assert "alpha" in state.entries["file"]

    def test_update_edit_modifies_existing(self):
        existing = {"file": {"alpha": {"title": "old", "content": "old content", "path": "old.txt"}}}
        state = make_state(entries=copy.deepcopy(existing))
        history = make_history()

        def update_llm(system_prompt, user_prompt, signal=None):
            return json.dumps({
                "summary": "update alpha",
                "rationale": "change content",
                "expected_outcome": "alpha updated",
                "edits": [
                    {
                        "action": "update",
                        "kind": "file",
                        "id": "alpha",
                        "title": "Alpha",
                        "content": "new content",
                        "path": "old.txt",
                        "reason": "update",
                    }
                ],
            })

        plan = plan_refinement(
            messages=[],
            state=state,
            history=history,
            llm_call=update_llm,
            scope="local",
        )
        result = apply_refinement_proposal(plan, state, history)
        assert result.applied_edits[0].applied is True
        assert state.entries["file"]["alpha"]["content"] == "new content"

    def test_delete_edit_removes_entry(self):
        existing = {"file": {"beta": {"title": "beta", "content": "x", "path": "beta.txt"}}}
        state = make_state(entries=copy.deepcopy(existing))
        history = make_history()

        def delete_llm(system_prompt, user_prompt, signal=None):
            return json.dumps({
                "summary": "delete beta",
                "rationale": "not needed",
                "expected_outcome": "beta removed",
                "edits": [{"action": "delete", "kind": "file", "id": "beta", "reason": "cleanup"}],
            })

        plan = plan_refinement(
            messages=[],
            state=state,
            history=history,
            llm_call=delete_llm,
            scope="local",
        )
        result = apply_refinement_proposal(plan, state, history)
        assert result.applied_edits[0].applied is True
        assert "beta" not in state.entries["file"]

    def test_validation_error_marks_not_applied(self):
        def invalid_llm(system_prompt, user_prompt, signal=None):
            return json.dumps({
                "summary": "bad edit",
                "rationale": "test",
                "expected_outcome": "none",
                "edits": [{"action": "invalid_action", "kind": "file", "id": "x"}],
            })

        state = make_state()
        history = make_history()
        plan = plan_refinement(
            messages=[],
            state=state,
            history=history,
            llm_call=invalid_llm,
            scope="local",
        )
        result = apply_refinement_proposal(plan, state, history)
        assert result.applied_edits[0].applied is False
        assert result.applied_edits[0].error is not None

    def test_baseline_conflict_rejects_edit(self):
        existing = {"file": {"gamma": {"title": "gamma", "content": "v1", "path": "g.txt"}}}
        state = make_state(entries=copy.deepcopy(existing))
        history = make_history()

        def update_gamma_llm(system_prompt, user_prompt, signal=None):
            return json.dumps({
                "summary": "update gamma",
                "rationale": "change gamma",
                "expected_outcome": "gamma updated",
                "edits": [
                    {
                        "action": "update",
                        "kind": "file",
                        "id": "gamma",
                        "title": "Gamma",
                        "content": "v2",
                        "path": "g.txt",
                        "reason": "update",
                    }
                ],
            })

        plan = plan_refinement(
            messages=[],
            state=state,
            history=history,
            llm_call=update_gamma_llm,
            scope="local",
        )
        state.entries["file"]["gamma"]["content"] = "changed during planning"
        result = apply_refinement_proposal(plan, state, history)
        applied = [e for e in result.applied_edits if e.edit_id == "gamma"]
        assert applied
        assert applied[0].applied is False
        assert "changed during refinement planning" in (applied[0].error or "")

    def test_records_refinement_event(self):
        state = make_state()
        history = make_history()
        plan = plan_refinement(
            messages=[],
            state=state,
            history=history,
            llm_call=fake_llm_call,
            scope="local",
        )
        result = apply_refinement_proposal(plan, state, history)
        assert len(state.refinements) == 1
        assert state.refinements[0].id == plan.id
        assert state.refinements[0].trigger == "Add alpha entry"

    def test_appends_to_history(self):
        state = make_state()
        history = make_history()
        plan = plan_refinement(
            messages=[],
            state=state,
            history=history,
            llm_call=fake_llm_call,
            scope="local",
        )
        result = apply_refinement_proposal(plan, state, history)
        assert len(history) == 1
        assert history[0].id == plan.id
        assert history[0].rolled_back is False


class TestRollback:
    def test_rollback_restores_previous_state(self):
        original = {"file": {"alpha": {"title": "original", "content": "v1", "path": "f.txt"}}}
        state = make_state(entries=copy.deepcopy(original))
        history: List[RefinementHistoryEntry] = []

        plan = RefinementPlan(
            proposal=RefinementProposal(
                summary="update alpha",
                rationale="change content",
                expected_outcome="alpha updated",
                edits=[
                    RefinementEdit(
                        action="update", kind="file", id="alpha",
                        title="Alpha", content="v2", path="f.txt", reason="update",
                    )
                ],
            ),
            id="ref_1",
            rollback_scope="local",
            baseline_state=state.clone(),
        )
        result = apply_refinement_proposal(plan, state, history)
        assert state.entries["file"]["alpha"]["content"] == "v2"

        rolled = rollback(result, state, history, baseline_state=plan.baseline_state)
        assert rolled is not None
        assert state.entries["file"]["alpha"]["content"] == "v1"

    def test_rollback_already_rolled_back_returns_none(self):
        state = make_state()
        history = make_history()
        plan = plan_refinement(
            messages=[],
            state=state,
            history=history,
            llm_call=fake_llm_call,
            scope="local",
        )
        result = apply_refinement_proposal(plan, state, history)
        history[0].rolled_back = True
        rolled = rollback(result, state, history, baseline_state=plan.baseline_state)
        assert rolled is None

    def test_rollback_marks_history(self):
        state = make_state()
        history = make_history()
        plan = plan_refinement(
            messages=[],
            state=state,
            history=history,
            llm_call=fake_llm_call,
            scope="local",
        )
        result = apply_refinement_proposal(plan, state, history)
        rollback(result, state, history, baseline_state=plan.baseline_state)
        assert history[0].rolled_back is True


class TestSummarizeForRefinement:
    def test_truncates_long_messages(self):
        messages = [{"role": "user", "content": "x" * 100_000}]
        summary = summarize_for_refinement(messages, max_chars=1000)
        assert len(summary) <= 1000

    def test_handles_list_content(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        summary = summarize_for_refinement(messages)
        assert "hello" in summary


class TestParseProposal:
    def test_valid_json(self):
        from scripts.auto_correction import _parse_proposal
        proposal = _parse_proposal('{"summary": "s", "rationale": "r", "expected_outcome": "e", "edits": []}')
        assert proposal.summary == "s"

    def test_json_with_code_fence(self):
        from scripts.auto_correction import _parse_proposal
        text = '```json\n{"summary": "s", "edits": []}\n```'
        proposal = _parse_proposal(text)
        assert proposal.summary == "s"

    def test_invalid_json_raises(self):
        from scripts.auto_correction import _parse_proposal
        with pytest.raises(ValueError):
            _parse_proposal("not json at all")
