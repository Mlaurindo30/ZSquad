"""
R0 Red Diagnostic Suite — Test Integrity and False Coverage Verification.
Proves failures in R0-TEST-001 through R0-TEST-003 against current broken runtime behavior.
DO NOT FIX IN R0. These tests MUST FAIL (RED) to demonstrate the current defects.
"""

from pathlib import Path
import sys
import inspect
import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))



def test_r0_test_001_fake_full_lifecycle_coverage_early_return():
    """
    R0-TEST-001: Fake full lifecycle coverage.
    Invariant: test_full_governed_sdlc_lifecycle_g1_to_g6 must actually execute G1 through G6.
    Current defect: test_e2e_agent_workflow.py line 100 has an explicit 'return' statement right after G1,
    leaving G2, G3, G4, G5, and G6 as dead/unreachable code!
    """
    from scripts.tests import test_e2e_agent_workflow

    src = inspect.getsource(test_e2e_agent_workflow.AgentE2EWorkflowTests.test_full_governed_sdlc_lifecycle_g1_to_g6)

    # Invariant: The test must not have an early return statement terminating execution after G1.
    # Current behavior: line 100 has literal 'return'!
    lines = [line.strip() for line in src.splitlines()]
    has_early_return = False
    for i, line in enumerate(lines):
        if line == "return" and i < len(lines) - 5:
            has_early_return = True
            break

    assert not has_early_return, (
        "R0-TEST-001 CONFIRMED: test_full_governed_sdlc_lifecycle_g1_to_g6 contains an early 'return' "
        "at line 100, aborting before G2-G6 are ever tested!"
    )


def test_r0_test_002_sdlc_simulation_does_not_advance_state():
    """
    R0-TEST-002: SDLC simulation authenticity.
    Invariant: simulate_full_sdlc_project.py must exercise real state machine transitions (advance_state).
    Current defect: run_e2e_simulation() never calls advance_state(); the work item remains permanently
    in 'blueprint' while files and gate calls are handcrafted in isolation!
    """
    from scripts.tests import simulate_full_sdlc_project

    src = inspect.getsource(simulate_full_sdlc_project.run_e2e_simulation)

    # Invariant: run_e2e_simulation must call advance_state to progress through lifecycle.
    # Current behavior: advance_state is NEVER called!
    assert "advance_state" in src, (
        "R0-TEST-002 CONFIRMED: simulate_full_sdlc_project.py never calls advance_state(); "
        "it fakes lifecycle coverage by manually crafting JSON files without transitioning states."
    )


def test_r0_test_003_green_suite_masks_broken_workflow():
    """
    R0-TEST-003: Green suite masking broken workflow.
    Invariant: The test suite should test real gate execution end-to-end instead of skipping to achieve 100% green.
    Current defect: In test_e2e_agent_workflow.py, G2/G3/G4/G5/G6 assertions are unreachable due to line 100 'return'.
    The test suite reports green (1131 passed) while the actual G2-G6 workflow is unexecuted.
    """
    from scripts.tests import test_e2e_agent_workflow

    src = inspect.getsource(test_e2e_agent_workflow.AgentE2EWorkflowTests.test_full_governed_sdlc_lifecycle_g1_to_g6)
    
    # Invariant: The test method must execute all its statements without early exit
    lines = [line.strip() for line in src.splitlines()]
    unreachable_lines = 0
    return_found = False
    for line in lines:
        if return_found and line and not line.startswith("#"):
            unreachable_lines += 1
        if line == "return":
            return_found = True

    assert unreachable_lines == 0, (
        f"R0-TEST-003 CONFIRMED: Normal test suite hides broken lifecycle; "
        f"test_full_governed_sdlc_lifecycle_g1_to_g6 has {unreachable_lines} lines of dead code after early return!"
    )

