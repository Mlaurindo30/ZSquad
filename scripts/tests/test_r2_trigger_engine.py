"""Canonical Test Suite for R2 Trigger Engine (Section 40).

Covers:
- matching event type creates expected delivery
- nonmatching event creates zero deliveries
- declarative conditions respected (wildcard, payload equality, comparators, membership, conjunctions)
- disabled trigger ignored (via registry.disable and false condition)
- duplicate trigger ID rejected (TriggerAlreadyExistsError)
- replay of same event/policy does not duplicate deliveries
- multiple matching policies generate one delivery each
- zero eval/exec paths (prohibited AST constructs rejected safely)
- zero LLM dependency/imports
- zero agent IDs hardcoded in event engine
"""

import ast
from pathlib import Path
import pytest

from scripts.domain.events import (
    DeliveryStatus,
    DomainEvent,
    TriggerActionKind,
    TriggerPolicy,
)
from scripts.runtime.events.engine import EventEngine
from scripts.runtime.events.errors import (
    TriggerAlreadyExistsError,
    TriggerConditionError,
)
from scripts.runtime.events.store import SqliteEventStore
from scripts.runtime.events.triggers import TriggerRegistry, evaluate_condition


@pytest.fixture
def memory_store():
    """Provides an isolated in-memory SqliteEventStore."""
    store = SqliteEventStore(":memory:")
    yield store
    store.close()


@pytest.fixture
def sample_event():
    """Provides a valid canonical DomainEvent."""
    return DomainEvent.create(
        event_type="squad.stage.transitioned",
        work_item_id="US-R2-100",
        project_id="agent_squad",
        source="cli",
        correlation_id="corr-r2-trig-1",
        causation_id="cause-r2-trig-1",
        payload={
            "stage": "implementation",
            "risk": "medium",
            "story_points": 5,
            "target": "backend",
        },
    )


class TestR2TriggerEngine:
    """Rigorous verification of the Trigger Engine and Declarative Registry."""

    def test_matching_event_type_creates_expected_delivery(self, memory_store, sample_event):
        """Validates that a registered policy matching event_type and condition enqueues an outbox delivery."""
        registry = TriggerRegistry()
        policy = TriggerPolicy(
            trigger_id="trig-impl-start",
            event_type="squad.stage.transitioned",
            condition_expression="payload.stage == 'implementation'",
            action_kind=TriggerActionKind.ACTIVATE_AGENT,
            target_role="06-software-engineer",
        )
        registry.register(policy)

        engine = EventEngine(store=memory_store, triggers=registry)
        engine.emit(sample_event)

        pending = engine.list_pending_deliveries()
        assert len(pending) == 1
        delivery = pending[0]
        assert delivery.subscriber == "agent:06-software-engineer"
        assert delivery.status == DeliveryStatus.PENDING
        assert delivery.event_id == sample_event.event_id

    def test_nonmatching_event_creates_zero_deliveries(self, memory_store, sample_event):
        """Validates that non-matching event_type or unmet condition creates zero deliveries."""
        registry = TriggerRegistry()
        # Different event type
        registry.register(
            TriggerPolicy(
                trigger_id="trig-other-type",
                event_type="squad.gate.evaluated",
                condition_expression="*",
                action_kind=TriggerActionKind.EVALUATE_GATE,
            )
        )
        # Matching event type but failing condition
        registry.register(
            TriggerPolicy(
                trigger_id="trig-high-risk-only",
                event_type="squad.stage.transitioned",
                condition_expression="payload.risk == 'high'",
                action_kind=TriggerActionKind.ACTIVATE_AGENT,
                target_role="11-security-architect",
            )
        )

        engine = EventEngine(store=memory_store, triggers=registry)
        engine.emit(sample_event)

        pending = engine.list_pending_deliveries()
        assert len(pending) == 0

    def test_declarative_conditions_respected(self, sample_event):
        """Validates wildcards, equality, comparisons, membership, boolean logic, and safe missing keys."""
        # Wildcards
        assert evaluate_condition("*", sample_event) is True
        assert evaluate_condition("", sample_event) is True
        assert evaluate_condition("true", sample_event) is True
        assert evaluate_condition("ALL", sample_event) is True
        assert evaluate_condition("1", sample_event) is True

        # Equality & inequality
        assert evaluate_condition("source == 'cli'", sample_event) is True
        assert evaluate_condition("source == 'mcp'", sample_event) is False
        assert evaluate_condition("payload.risk == 'medium'", sample_event) is True
        assert evaluate_condition("payload.risk != 'high'", sample_event) is True

        # Relational comparators
        assert evaluate_condition("payload.story_points >= 5", sample_event) is True
        assert evaluate_condition("payload.story_points > 5", sample_event) is False
        assert evaluate_condition("payload.story_points < 8", sample_event) is True
        assert evaluate_condition("payload.story_points <= 5", sample_event) is True

        # Membership
        assert evaluate_condition("payload.risk in ['low', 'medium']", sample_event) is True
        assert evaluate_condition("payload.risk not in ['high', 'critical']", sample_event) is True

        # Boolean conjunctions
        expr_and = "payload.risk == 'medium' and payload.story_points == 5"
        assert evaluate_condition(expr_and, sample_event) is True
        expr_or = "payload.risk == 'high' or payload.story_points == 5"
        assert evaluate_condition(expr_or, sample_event) is True
        expr_not = "not (payload.risk == 'high')"
        assert evaluate_condition(expr_not, sample_event) is True

        # Missing attributes resolve to None without crashing
        assert evaluate_condition("payload.missing_key is None", sample_event) is True
        assert evaluate_condition("payload.missing_key == 'foo'", sample_event) is False

    def test_disabled_trigger_ignored(self, memory_store, sample_event):
        """Validates that a disabled trigger policy produces no matches and no deliveries."""
        registry = TriggerRegistry()
        policy = TriggerPolicy(
            trigger_id="trig-maintenance-mode",
            event_type="squad.stage.transitioned",
            condition_expression="*",
            action_kind=TriggerActionKind.ACTIVATE_AGENT,
            target_role="06-software-engineer",
        )
        registry.register(policy)

        # Disable the trigger
        registry.disable("trig-maintenance-mode")
        assert registry.is_enabled("trig-maintenance-mode") is False

        # Should match nothing when disabled
        matches = registry.match_triggers(sample_event)
        assert len(matches) == 0

        engine = EventEngine(store=memory_store, triggers=registry)
        engine.emit(sample_event)
        assert len(engine.list_pending_deliveries()) == 0

        # Re-enable the trigger
        registry.enable("trig-maintenance-mode")
        assert registry.is_enabled("trig-maintenance-mode") is True
        matches_reenabled = registry.match_triggers(sample_event)
        assert len(matches_reenabled) == 1

        # Also test declarative disabled condition
        policy_false = TriggerPolicy(
            trigger_id="trig-always-false",
            event_type="squad.stage.transitioned",
            condition_expression="false",
            action_kind=TriggerActionKind.ACTIVATE_AGENT,
        )
        assert evaluate_condition("false", sample_event) is False
        assert evaluate_condition("0", sample_event) is False

    def test_duplicate_trigger_id_rejected(self):
        """Validates that registering a trigger with an existing trigger_id raises TriggerAlreadyExistsError."""
        registry = TriggerRegistry()
        policy1 = TriggerPolicy(
            trigger_id="trig-unique-id",
            event_type="squad.stage.transitioned",
            condition_expression="*",
            action_kind=TriggerActionKind.ACTIVATE_AGENT,
        )
        registry.register(policy1)

        policy2 = TriggerPolicy(
            trigger_id="trig-unique-id",  # Colliding ID
            event_type="squad.stage.transitioned",
            condition_expression="payload.risk == 'high'",
            action_kind=TriggerActionKind.EVALUATE_GATE,
        )
        with pytest.raises(TriggerAlreadyExistsError, match="already registered"):
            registry.register(policy2, overwrite=False)

        # Overwrite=True should allow updating
        registry.register(policy2, overwrite=True)
        assert registry.get_policy("trig-unique-id").action_kind == TriggerActionKind.EVALUATE_GATE

    def test_replay_of_same_event_policy_does_not_duplicate_deliveries(self, memory_store, sample_event):
        """Validates that replaying/re-emitting an identical event does not duplicate outbox deliveries."""
        registry = TriggerRegistry()
        registry.register(
            TriggerPolicy(
                trigger_id="trig-stage",
                event_type="squad.stage.transitioned",
                condition_expression="*",
                action_kind=TriggerActionKind.ACTIVATE_AGENT,
                target_role="06-software-engineer",
            )
        )
        engine = EventEngine(store=memory_store, triggers=registry)

        # First emission
        engine.emit(sample_event)
        assert len(engine.list_pending_deliveries()) == 1

        # Replay same event
        engine.emit(sample_event)
        # Outbox must still contain only 1 delivery
        assert len(engine.list_pending_deliveries()) == 1

    def test_multiple_matching_policies_generate_one_delivery_each(self, memory_store, sample_event):
        """Validates that multiple matched trigger policies fan out to distinct deliveries atomically."""
        registry = TriggerRegistry()
        registry.register(
            TriggerPolicy(
                trigger_id="trig-1",
                event_type="squad.stage.transitioned",
                condition_expression="payload.stage == 'implementation'",
                action_kind=TriggerActionKind.ACTIVATE_AGENT,
                target_role="06-software-engineer",
            )
        )
        registry.register(
            TriggerPolicy(
                trigger_id="trig-2",
                event_type="squad.stage.transitioned",
                condition_expression="payload.risk == 'medium'",
                action_kind=TriggerActionKind.ACTIVATE_AGENT,
                target_role="11-security-architect",
            )
        )
        registry.register(
            TriggerPolicy(
                trigger_id="trig-3",
                event_type="squad.stage.transitioned",
                condition_expression="*",
                action_kind=TriggerActionKind.EVALUATE_GATE,
                target_role=None,  # Will fallback to trigger:trig-3
            )
        )

        engine = EventEngine(store=memory_store, triggers=registry)
        engine.emit(sample_event)

        pending = engine.list_pending_deliveries()
        assert len(pending) == 3
        subscribers = {d.subscriber for d in pending}
        assert subscribers == {
            "agent:06-software-engineer",
            "agent:11-security-architect",
            "trigger:trig-3",
        }

    def test_zero_eval_exec_paths_and_prohibited_nodes(self, sample_event):
        """Validates that unsafe Python constructs (eval, exec, __class__, function calls) are strictly blocked."""
        malicious_expressions = [
            "__import__('os').system('echo pwned')",
            "eval('1 + 1')",
            "exec('x = 1')",
            "open('/etc/passwd')",
            "payload.__class__",
            "payload.get('stage')",
            "exit(0)",
            "[x for x in (1, 2)]",  # Comprehensions are not in whitelist
        ]
        for expr in malicious_expressions:
            with pytest.raises(TriggerConditionError):
                evaluate_condition(expr, sample_event)

    def test_zero_llm_dependency_and_imports(self):
        """Static AST analysis verifying zero LLM library imports in the trigger and event engine modules."""
        events_dir = Path(__file__).resolve().parents[2] / "scripts" / "runtime" / "events"
        forbidden_llm_modules = {
            "openai",
            "anthropic",
            "google",
            "gemini",
            "langchain",
            "langgraph",
            "litellm",
            "ollama",
        }

        for py_file in events_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=py_file.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        assert root not in forbidden_llm_modules, (
                            f"{py_file.name} illegally imports LLM module '{alias.name}'"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        root = node.module.split(".")[0]
                        assert root not in forbidden_llm_modules, (
                            f"{py_file.name} illegally imports from LLM module '{node.module}'"
                        )

    def test_zero_agent_id_hardcoded_in_event_engine(self):
        """Static analysis verifying that no specific Agent Squad IDs are hardcoded in the engine logic."""
        events_dir = Path(__file__).resolve().parents[2] / "scripts" / "runtime" / "events"
        # Known specialist agent prefixes
        specific_agent_ids = [
            "00-delivery-orchestrator",
            "01-product-owner",
            "04-solution-architect",
            "06-software-engineer",
            "11-test-engineer",
            "14-governance-auditor",
        ]

        for py_file in events_dir.glob("*.py"):
            code = py_file.read_text(encoding="utf-8")
            for agent_id in specific_agent_ids:
                # Should not appear in runtime event engine source
                assert agent_id not in code, (
                    f"Found hardcoded Agent ID '{agent_id}' in engine file {py_file.name}"
                )
