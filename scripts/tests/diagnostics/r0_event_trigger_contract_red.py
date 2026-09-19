"""
R0 Red Diagnostic Suite — Event Engine, Triggers, and Watchdog Scheduler.
Proves failures in R0-EVT-* and Watchdog/Scheduler invariants against current broken runtime behavior.
DO NOT FIX IN R0. These tests MUST FAIL (RED) to demonstrate the current defects.
"""

from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))

from continuous_trigger_engine import EngineEvent, ContinuousTriggerEngine



def test_r0_evt_001_canonical_domain_event_model():
    """
    R0-EVT-001: Canonical domain event model.
    Invariant: Domain event model must include source, correlation_id, and causation_id.
    Current defect: EngineEvent only defines event_id, event_type, work_item_id, payload, timestamp.
    """
    event = EngineEvent(
        event_id="evt-001",
        event_type="TEST_EVENT",
        work_item_id="US-001",
        payload={},
        timestamp="2026-09-17T00:00:00Z",
    )

    # Invariant: EngineEvent must have source, correlation_id, causation_id attributes.
    # Current behavior: AttributeError!
    assert hasattr(event, "source"), "R0-EVT-001 CONFIRMED: EngineEvent missing 'source' attribute"
    assert hasattr(event, "correlation_id"), "R0-EVT-001 CONFIRMED: EngineEvent missing 'correlation_id' attribute"
    assert hasattr(event, "causation_id"), "R0-EVT-001 CONFIRMED: EngineEvent missing 'causation_id' attribute"


def test_r0_evt_002_trigger_registry_executable_policy():
    """
    R0-EVT-002: Trigger registry/policy.
    Invariant: Runtime must provide an executable mapping: event -> required action -> required role.
    Current defect: There is no declarative or executable trigger registry; only hardcoded callbacks for handoff/gate.
    """
    import inspect
    from scripts import continuous_trigger_engine

    src = inspect.getsource(continuous_trigger_engine.ContinuousTriggerEngine)

    # Invariant: ContinuousTriggerEngine must have a trigger policy/registry engine.
    assert "trigger_policy" in src or "trigger_registry" in src, (
        "R0-EVT-002 CONFIRMED: ContinuousTriggerEngine lacks executable trigger policy/registry mapping"
    )


def test_r0_evt_004_review_trigger_enforcement():
    """
    R0-EVT-004: Review trigger.
    Invariant: Completion of implementation must mechanically emit an event triggering code review.
    Current defect: No IMPLEMENTATION_COMPLETED event or code-review trigger exists in continuous engine.
    """
    from scripts import continuous_trigger_engine

    # Invariant: EVENT_IMPLEMENTATION_COMPLETED must exist
    assert hasattr(continuous_trigger_engine, "EVENT_IMPLEMENTATION_COMPLETED"), (
        "R0-EVT-004 CONFIRMED: EVENT_IMPLEMENTATION_COMPLETED is absent from event definitions"
    )


def test_r0_evt_010_persistent_event_outbox():
    """
    R0-EVT-010: Persistent event storage.
    Invariant: Events must be persisted in an outbox/event store in squad.db to survive process crashes.
    Current defect: Events are only written via unindexed append to a local text file events.jsonl.
    """
    import inspect
    from scripts import continuous_trigger_engine

    src = inspect.getsource(continuous_trigger_engine.ContinuousTriggerEngine._save_event_log)

    # Invariant: Events must be saved into a transactional database or outbox table.
    # Current behavior: writes directly to text file events.jsonl!
    assert "sqlite" in src or "db." in src or "outbox" in src, (
        "R0-EVT-010 CONFIRMED: Events are appended to JSONL file without durable transactional outbox"
    )


def test_r0_watchdog_scheduler_presence():
    """
    Area G: Watchdog / Scheduler.
    Invariant: A watchdog/scheduler service must exist to periodically detect stalled states,
    timebox breaches, and pending syncs.
    Current defect: Watchdog/scheduler is completely ABSENT from the runtime.
    """
    # Invariant: A scheduler module must exist in scripts or integrations.
    scheduler_found = False
    for p in ROOT.rglob("*scheduler*.py"):
        if "site-packages" not in str(p) and "vendor" not in str(p):
            scheduler_found = True
            break

    assert scheduler_found, (
        "Area G CONFIRMED: Watchdog/scheduler module is ABSENT from Agent Squad runtime"
    )
