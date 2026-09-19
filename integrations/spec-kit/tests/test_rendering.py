"""Testes comportamentais de render_command e dos overlays governados (Tarefa T3).

Cobre o plano (secao 6/T3): comandos validos, contexto ausente fail-closed,
plan exige spec/clarifications/G1, implement exige plan/tasks/G1-G3,
renderizacao dos sete comandos, determinismo e ausencia de referencias
a diretorios temporarios de estudo. Os overlays devem conter as clausulas
de anti-bypass de checklist, Clarify obrigatorio e atribuicao MIT.

Este modulo entrega APENAS composicao de prompts; a autorizacao real
de gates pertence a T4 (policy.py) e T5 (CLI Squad).

O modulo e carregado via importlib porque o diretorio 'integrations/spec-kit'
contem hifen e nao e importavel pelo nome.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_DIR = REPO_ROOT / "integrations" / "spec-kit" / "adapter"
OVERLAYS_DIR = REPO_ROOT / "integrations" / "spec-kit" / "overlays" / "commands"

STAGES = ("constitution", "specify", "clarify", "plan", "tasks", "implement", "analyze")

FULL_CONTEXT = {
    "work_id": "TASK-SPECKIT-T3-20260911",
    "project_id": "agent_squad",
    "constitution_path": "work/agent_squad/TASK-SPECKIT-T3-20260911/sdd/constitution.md",
    "spec_path": "work/agent_squad/TASK-SPECKIT-T3-20260911/sdd/spec.md",
    "clarifications_path": "work/agent_squad/TASK-SPECKIT-T3-20260911/sdd/clarifications.yaml",
    "plan_path": "work/agent_squad/TASK-SPECKIT-T3-20260911/sdd/plan.md",
    "tasks_path": "work/agent_squad/TASK-SPECKIT-T3-20260911/sdd/tasks.yaml",
    "g1_evidence": "work/agent_squad/TASK-SPECKIT-T3-20260911/gate-decisions/GD-G1-20260911.yaml",
    "g2_evidence": "work/agent_squad/TASK-SPECKIT-T3-20260911/gate-decisions/GD-G2-20260911.yaml",
    "g3_evidence": "work/agent_squad/TASK-SPECKIT-T3-20260911/gate-decisions/GD-G3-20260911.yaml",
}


def _load_rendering():
    name = "sdd_adapter_t3.rendering"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ADAPTER_DIR / "rendering.py")
    if spec is None or spec.loader is None:  # pragma: no cover - só se arquivo faltar
        pytest.fail(f"rendering.py não encontrado em {ADAPTER_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rendering():
    return _load_rendering()


def _without(context: dict, key: str) -> dict:
    reduced = dict(context)
    reduced.pop(key)
    return reduced


# ---------------------------------------------------------------------------
# Estagio invalido e contexto invalido (fail-closed)
# ---------------------------------------------------------------------------


def test_unknown_stage_raises(rendering):
    with pytest.raises(ValueError, match="release"):
        rendering.render_command("release", dict(FULL_CONTEXT))


def test_non_dict_context_raises(rendering):
    with pytest.raises(ValueError):
        rendering.render_command("plan", ["não", "é", "dict"])


def test_minimal_context_for_plan_names_missing_requirements(rendering):
    with pytest.raises(ValueError) as excinfo:
        rendering.render_command("plan", {"work_id": "TASK-SPECKIT-T3-20260911"})
    message = str(excinfo.value)
    for required in ("project_id", "spec_path", "clarifications_path", "g1_evidence"):
        assert required in message, required


def test_empty_value_counts_as_missing(rendering):
    context = dict(FULL_CONTEXT)
    context["g1_evidence"] = ""
    with pytest.raises(ValueError, match="g1_evidence"):
        rendering.render_command("plan", context)


# ---------------------------------------------------------------------------
# Pre-condicoes por comando (secao 7-T3 do plano)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing", ["spec_path", "clarifications_path", "g1_evidence"]
)
def test_plan_requires_spec_clarifications_and_g1(rendering, missing):
    with pytest.raises(ValueError, match=missing):
        rendering.render_command("plan", _without(FULL_CONTEXT, missing))


@pytest.mark.parametrize(
    "missing",
    ["plan_path", "tasks_path", "g1_evidence", "g2_evidence", "g3_evidence"],
)
def test_implement_requires_plan_tasks_and_g1_to_g3(rendering, missing):
    with pytest.raises(ValueError, match=missing):
        rendering.render_command("implement", _without(FULL_CONTEXT, missing))


# ---------------------------------------------------------------------------
# Renderizacao dos sete comandos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", STAGES)
def test_all_seven_stages_render(rendering, stage):
    out = rendering.render_command(stage, dict(FULL_CONTEXT))
    assert isinstance(out, str) and out.strip()
    assert FULL_CONTEXT["work_id"] in out
    assert ".temp" not in out
    assert "{{" not in out, "placeholder nao substituído permaneceu na saida"


def test_rendered_briefing_embeds_context_values(rendering):
    out = rendering.render_command("plan", dict(FULL_CONTEXT))
    assert FULL_CONTEXT["spec_path"] in out
    assert FULL_CONTEXT["g1_evidence"] in out


def test_rendering_is_deterministic(rendering):
    first = rendering.render_command("plan", dict(FULL_CONTEXT))
    second = rendering.render_command("plan", dict(FULL_CONTEXT))
    assert first == second
    reordered = {k: FULL_CONTEXT[k] for k in reversed(list(FULL_CONTEXT))}
    assert rendering.render_command("plan", reordered) == first


# ---------------------------------------------------------------------------
# Revisao T3 (code-reviewer): REQUIRED_CONTEXT x placeholders, substituicao
# single-pass, persona no header e paths Windows (MINOR/MAJOR 1-4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", STAGES)
def test_render_with_exactly_declared_keys(rendering, stage):
    """MAJOR-1: contexto com EXATAMENTE as chaves declaradas nao pode falhar."""
    minimal = {key: f"valor-{key}" for key in rendering.REQUIRED_CONTEXT[stage]}
    out = rendering.render_command(stage, minimal)
    assert "{{" not in out, "placeholder declarado nao resolvido"


def test_value_containing_placeholder_is_not_resubstituted(rendering):
    """MAJOR-2: valor contendo {{...}} nao e re-substituido (passada unica).

    O valor literal deve aparecer exatamente duas vezes (bloco de contexto e
    corpo do overlay) e a interpretacao do placeholder interno (valor de
    work_id interpolado) NAO pode aparecer em lugar nenhum.
    """
    context = dict(FULL_CONTEXT)
    context["constitution_path"] = "{{work_id}}/constitution.md"
    out = rendering.render_command("constitution", context)
    assert out.count("{{work_id}}/constitution.md") == 2
    assert f"{context['work_id']}/constitution.md" not in out


def test_unreferenced_key_with_placeholder_value_raises(rendering):
    """MAJOR-2: chave nao referenciada com {{...}} no valor gera erro (fail-closed)."""
    context = dict(FULL_CONTEXT)
    context["extra_note"] = "veja {{plan_path}} depois"
    with pytest.raises(ValueError, match="plan_path"):
        rendering.render_command("constitution", context)


@pytest.mark.parametrize("stage, persona", [
    ("constitution", "requirements-analyst"),
    ("specify", "requirements-analyst"),
    ("clarify", "requirements-analyst"),
    ("plan", "solution-architect"),
    ("tasks", "delivery-orchestrator"),
    ("analyze", "delivery-orchestrator"),
    ("implement", "software-engineer"),
])
def test_briefing_header_names_responsible_persona(rendering, stage, persona):
    """MINOR-3: header do briefing declara a persona responsavel pelo comando."""
    out = rendering.render_command(stage, dict(FULL_CONTEXT))
    assert persona in out


def test_windows_backslash_path_preserved_verbatim(rendering):
    """MINOR-4: path Windows com backslash preservado verbatim na saida."""
    context = dict(FULL_CONTEXT)
    context["spec_path"] = "work\\projeto\\TASK-SPECKIT-T3-20260911\\sdd\\spec.md"
    out = rendering.render_command("specify", context)
    assert "work\\projeto\\TASK-SPECKIT-T3-20260911\\sdd\\spec.md" in out


# ---------------------------------------------------------------------------
# Overlays em disco: clausulas Squad e atribuicao
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", STAGES)
def test_overlay_file_has_squad_clauses(rendering, stage):
    path = OVERLAYS_DIR / f"{stage}.md"
    assert path.is_file(), f"overlay ausente: {path}"
    content = path.read_text(encoding="utf-8")
    assert ".temp" not in content
    assert "confirmação textual como override" in content
    assert "Clarify é obrigatório" in content
    assert "github/spec-kit (MIT)" in content
    assert "{{work_id}}" in content and "{{project_id}}" in content
