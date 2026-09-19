from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROMPT_BUDGETS = {
    "AGENTS.md": 12_000,
    "CLAUDE.md": 40_000,
    "GEMINI.md": 12_000,
}
REQUIRED_MARKERS = (
    "neuroinclusive communication",
    "outcome/action first",
    "empty preamble, recap or closer",
    "literal, unambiguous language",
    "short structured sections",
    "report errors with concrete evidence",
    "state uncertainty explicitly",
    "what changed · what remains · next concrete action",
)


@pytest.mark.parametrize("prompt_name", PROMPT_BUDGETS)
def test_provider_prompt_has_neuroinclusive_contract(prompt_name):
    text = (ROOT / prompt_name).read_text(encoding="utf-8").lower()

    for marker in REQUIRED_MARKERS:
        assert marker in text, f"{prompt_name} missing neuroinclusive marker: {marker}"


def test_agents_prompt_assigns_orchestrator_imperatively():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8").lower()

    assert "you are the `delivery-orchestrator` (`00`)" in text
    assert "assume this role at session start" in text


def test_agents_prompt_has_subagent_dispatch_reflection_rule():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8").lower()

    assert "dispatch only when specialist evidence or segregation changes the outcome" in text


@pytest.mark.parametrize("prompt_name", PROMPT_BUDGETS)
def test_provider_prompt_mandates_payload_injection_dispatch(prompt_name):
    text = (ROOT / prompt_name).read_text(encoding="utf-8").lower()

    assert "render-prompt" in text, (
        f"{prompt_name} missing mechanical persona compilation command"
    )
    assert "rendered output as the subagent's primary system/instruction payload" in text, (
        f"{prompt_name} missing mandatory payload injection rule"
    )


@pytest.mark.parametrize("prompt_name,budget", PROMPT_BUDGETS.items())
def test_provider_prompt_stays_within_documented_budget(prompt_name, budget):
    text = (ROOT / prompt_name).read_text(encoding="utf-8")

    assert len(text) <= budget, f"{prompt_name} has {len(text)} characters; budget is {budget}"


@pytest.mark.parametrize("prompt_name", PROMPT_BUDGETS)
def test_provider_prompt_has_dispatch_triage_and_synthesis(prompt_name):
    text = (ROOT / prompt_name).read_text(encoding="utf-8").lower()

    assert "dispatch only when" in text, (
        f"{prompt_name} missing triage gate for subagent dispatch"
    )
    assert "synthesize" in text, f"{prompt_name} missing mandatory post-return synthesis"


@pytest.mark.parametrize("prompt_name", PROMPT_BUDGETS)
def test_provider_prompt_documents_gate_cli_inputs(prompt_name):
    text = (ROOT / prompt_name).read_text(encoding="utf-8").lower()

    assert "decide-gate" in text or "decider" in text, f"{prompt_name} missing gate decider rule"
    assert "gates" in text, f"{prompt_name} missing gates rule"


@pytest.mark.parametrize("prompt_name", PROMPT_BUDGETS)
def test_provider_prompt_prefers_native_persona_catalog(prompt_name):
    text = (ROOT / prompt_name).read_text(encoding="utf-8").lower()

    assert "native" in text, (
        f"{prompt_name} missing native persona catalog preference"
    )


@pytest.mark.parametrize("prompt_name", PROMPT_BUDGETS)
def test_provider_prompt_references_work_cycles_registry(prompt_name):
    text = (ROOT / prompt_name).read_text(encoding="utf-8").lower().replace("\\", "/")

    assert "cycles.yaml" in text, f"{prompt_name} missing work cycles registry reference"
    assert "active cycle" in text, f"{prompt_name} missing work cycle identification rule"
    assert "tdd" in text and "bdd" in text, (
        f"{prompt_name} missing TDD/BDD practice anchors for the development cycle"
    )


def test_prompt_line_endings_are_fixed_to_lf():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    for prompt_name in PROMPT_BUDGETS:
        assert f"{prompt_name} text eol=lf" in attributes


def test_upstream_project_has_attribution_notice():
    notice_path = ROOT / "docs" / "THIRD_PARTY_NOTICES.md"
    if not notice_path.exists():
        notice_path = ROOT / "THIRD_PARTY_NOTICES.md"
    notice = notice_path.read_text(encoding="utf-8").lower()

    assert "ayghri/i-have-adhd" in notice
    assert "ayoub ghriss" in notice
    assert "mit license" in notice
    assert "https://github.com/ayghri/i-have-adhd" in notice
    assert "b42a45a068e080294924bfba19a7a2e8944c48ff" in notice
