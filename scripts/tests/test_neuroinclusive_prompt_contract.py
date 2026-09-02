from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROMPT_BUDGETS = {
    "AGENTS.md": 12_000,
    "CLAUDE.md": 40_000,
    "CODEX.md": 32 * 1024,
    "GEMINI.md": 12_000,
}
REQUIRED_MARKERS = (
    "neuroinclusive communication",
    "lead with the outcome",
    "empty preamble, recap, or closer",
    "numbered steps",
    "literal language",
    "state changes",
    "one concrete next action",
    "report errors directly",
    "evidence-based concrete units",
    "state uncertainty",
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


@pytest.mark.parametrize("prompt_name,budget", PROMPT_BUDGETS.items())
def test_provider_prompt_stays_within_documented_budget(prompt_name, budget):
    text = (ROOT / prompt_name).read_text(encoding="utf-8")

    assert len(text) <= budget, f"{prompt_name} has {len(text)} characters; budget is {budget}"


def test_prompt_line_endings_are_fixed_to_lf():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    for prompt_name in PROMPT_BUDGETS:
        assert f"{prompt_name} text eol=lf" in attributes


def test_upstream_project_has_attribution_notice():
    notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()

    assert "ayghri/i-have-adhd" in notice
    assert "ayoub ghriss" in notice
    assert "mit license" in notice
    assert "https://github.com/ayghri/i-have-adhd" in notice
    assert "b42a45a068e080294924bfba19a7a2e8944c48ff" in notice
