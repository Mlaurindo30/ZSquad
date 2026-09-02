"""Cobertura restante (statements e branches) de dois módulos do squad.

O que é: suíte dedicada às lacunas remanescentes de cobertura de
``scripts/evaluate_agent_trajectories.py`` e ``scripts/auto_correction.py``.
Responsabilidade: exercitar vias de violação de orçamento/artefato, o
entrypoint CLI (``main`` e bloco ``__main__``), serializadores de prompt,
regras de validação de edits, caminhos de erro de apply e os ramos de
rollback — sem tocar banco real ou arquivos de produção.
Pra que serve: levar os dois módulos a 100% de statements e branches.
Comportamento em falha: asserções falham se o comportamento observado
divergir do contrato dos módulos; nenhum estado persistente é criado.
Conexões: scripts/evaluate_agent_trajectories.py, scripts/auto_correction.py,
scripts/tests/test_evaluation_harness.py, scripts/tests/test_auto_correction.py.
Dependências & Imports: pytest (monkeypatch/capsys/raises), runpy, sys/pathlib
para resolução de caminhos, dados inteiramente em memória (FakeDB).
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
for _path in (str(ROOT), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import evaluate_agent_trajectories  # noqa: E402  (mesmo estilo do test_evaluation_harness)
import local_agent_db  # noqa: E402  (importado para permitir patch do símbolo reimportado via runpy)

from scripts.auto_correction import (  # noqa: E402
    AppliedRefinementEdit,
    HarnessState,
    RefinementEdit,
    RefinementEvent,
    RefinementHistoryEntry,
    RefinementPlan,
    RefinementProposal,
    RefinementResult,
    _parse_proposal,
    _slug,
    _validate_edit,
    apply_refinement_proposal,
    build_refinement_prompt,
    rollback,
    summarize_for_refinement,
)


# ---------------------------------------------------------------------------
# Fake do banco local (nada é persistido)
# ---------------------------------------------------------------------------


class FakeDB:
    """Substituto em memória de LocalAgentDB: registra instâncias e chamadas."""

    instances: List["FakeDB"] = []
    calls: List[Dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        FakeDB.instances.append(self)

    def log_trajectory(self, *args: Any, **kwargs: Any) -> None:
        FakeDB.calls.append({"args": args, "kwargs": kwargs})


@pytest.fixture(autouse=True)
def _reset_fake_db():
    FakeDB.instances.clear()
    FakeDB.calls.clear()
    yield
    FakeDB.instances.clear()
    FakeDB.calls.clear()


# ---------------------------------------------------------------------------
# evaluate_agent_trajectories.py — linhas 98, 117, 170-197, 201 e branches
# ---------------------------------------------------------------------------


class TestTrajectoryEvaluatorGaps:
    def test_step_budget_overflow_appends_violation(self):
        """Linha 98 / branch 97->98: estouro do orçamento de passos vira violação."""
        db = FakeDB()
        evaluator = evaluate_agent_trajectories.TrajectoryEvaluator(db)
        steps = [
            evaluate_agent_trajectories.TrajectoryStep(1, "agent-x", "read"),
            evaluate_agent_trajectories.TrajectoryStep(2, "agent-x", "write", status="retry"),
            evaluate_agent_trajectories.TrajectoryStep(3, "agent-x", "verify"),
        ]

        result = evaluator.evaluate_trajectory(
            benchmark_id="BM-OVF",
            agent_id="agent-x",
            allowed_scope=["src"],
            steps=steps,
            max_allowed_steps=2,
        )

        assert result.passed is False
        assert any("Estouro de orçamento de passos" in v for v in result.violations)
        assert db.calls, "log_trajectory deveria ter sido chamado no banco falso"
        assert db.calls[-1]["args"][3] == "fail"

    def test_expected_artifact_missing_appends_violation(self):
        """Linha 117 / branch 116->117: artefato esperado ausente vira violação."""
        evaluator = evaluate_agent_trajectories.TrajectoryEvaluator(FakeDB())
        steps = [
            evaluate_agent_trajectories.TrajectoryStep(
                1, "agent-y", "write_code", tool_called="write_to_file", files_touched=["src/ok.py"]
            )
        ]

        result = evaluator.evaluate_trajectory(
            benchmark_id="BM-ART",
            agent_id="agent-y",
            allowed_scope=["src", "docs"],
            steps=steps,
            max_allowed_steps=5,
            expected_artifacts=["src/ok.py", "docs/missing.md"],
        )

        assert result.passed is False
        assert any(
            "Artefato esperado não gerado" in v and "docs/missing.md" in v
            for v in result.violations
        )


class TestMainCli:
    def test_main_pass_returns_zero_and_logs_pass(self, monkeypatch, capsys):
        """Linhas 170-194 / branch 192->193: fluxo de sucesso do CLI."""
        monkeypatch.setattr(evaluate_agent_trajectories, "LocalAgentDB", FakeDB)

        code = evaluate_agent_trajectories.main(
            ["--benchmark", "bm-pass", "--agent", "agent-cli"]
        )

        out = capsys.readouterr().out
        assert code == 0
        assert "BENCHMARK_PASS: bm-pass" in out
        assert "Agent: agent-cli" in out
        assert FakeDB.instances, "o avaliador deveria usar o banco falso"
        assert FakeDB.calls[-1]["args"][3] == "pass"

    def test_main_fail_returns_one(self, monkeypatch, capsys):
        """Linhas 195-197 / branch 192->196: fluxo de falha do CLI."""
        monkeypatch.setattr(evaluate_agent_trajectories, "LocalAgentDB", FakeDB)
        failed = evaluate_agent_trajectories.TrajectoryEvaluationResult(
            benchmark_id="bm-fail",
            agent_id="agent-cli",
            passed=False,
            convergence_score=0.0,
            scope_compliance=False,
            clean_code_compliant=False,
            total_steps=3,
            total_tool_calls=3,
            duration_seconds=0.0,
            violations=["Violação de escopo: arquivo 'x' fora do escopo"],
        )
        monkeypatch.setattr(
            evaluate_agent_trajectories.TrajectoryEvaluator,
            "evaluate_trajectory",
            lambda self, **kwargs: failed,
        )

        code = evaluate_agent_trajectories.main(
            ["--benchmark", "bm-fail", "--agent", "agent-cli"]
        )

        assert code == 1
        assert "BENCHMARK_FAIL: bm-fail" in capsys.readouterr().out


class TestDunderMain:
    def test_dunder_main_runs_smoke_benchmark(self, monkeypatch, capsys):
        """Linha 201 / branch 200->201: bloco ``__main__`` executado via runpy."""
        monkeypatch.setattr(local_agent_db, "LocalAgentDB", FakeDB)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "evaluate_agent_trajectories.py",
                "--benchmark",
                "bm-runpy",
                "--agent",
                "agent-runpy",
            ],
        )

        with pytest.raises(SystemExit) as excinfo:
            runpy.run_path(
                str(SCRIPTS_DIR / "evaluate_agent_trajectories.py"),
                run_name="__main__",
            )

        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "BENCHMARK_PASS: bm-runpy" in out
        assert FakeDB.calls[-1]["args"][3] == "pass"


# ---------------------------------------------------------------------------
# auto_correction.py — helpers de slug e serialização
# ---------------------------------------------------------------------------


class TestSlug:
    def test_slug_normalizes_falls_back_and_truncates(self):
        """Linhas 122-124 (com o ramo ``or fallback`` do retorno)."""
        assert _slug("Hello World!!", "fb") == "hello_world"
        assert _slug("--", "fallback") == "fallback"
        assert _slug("a" * 100, "fb") == "a" * 80

    def test_create_without_id_uses_slug_as_edit_id(self):
        """Branch 363->122: create sem id deriva edit_id do título (ou do kind)."""
        state = HarnessState()
        plan = RefinementPlan(
            proposal=RefinementProposal(
                summary="create sem id",
                rationale="slug derivado de título/kind",
                expected_outcome="entradas criadas com id derivado",
                edits=[
                    RefinementEdit(
                        action="create", kind="decision", title="My Decision", content="decisão x"
                    ),
                    RefinementEdit(action="create", kind="file", content="conteúdo"),
                ],
            ),
            id="ref_slug",
            rollback_scope="local",
            baseline_state=None,
        )

        result = apply_refinement_proposal(plan, state, [])

        assert all(edit.applied for edit in result.applied_edits)
        assert state.entries["decision"]["my_decision"]["content"] == "decisão x"
        assert state.entries["file"]["file"]["content"] == "conteúdo"


class TestSerializationGaps:
    def test_message_blocks_non_text_dict_and_non_dict(self):
        """Linhas 189 e 191 / branches 186->189 e 185->191."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "olá"},
                    {"type": "image_url", "url": "https://example.com/img.png"},
                    "bloco cru como string",
                ],
            }
        ]

        out = summarize_for_refinement(messages)

        assert "olá" in out
        assert "image_url" in out
        assert "bloco cru como string" in out

    def test_state_serialization_handles_non_dict_entry_and_events(self):
        """Linhas 207 e 210 / branches 203->207 e 209->210."""
        state = HarnessState(
            entries={"file": {"alpha": {"title": "Alpha"}, "plain": "somente-uma-string"}},
            refinements=[
                RefinementEvent(
                    id="ev-1",
                    trigger="manual",
                    changes=["create file:x"],
                    evidence="evidência real",
                    outcome="aplicado",
                    created_at="2026-08-21T00:00:00+00:00",
                )
            ],
        )

        prompt = build_refinement_prompt(messages=[], state=state, history=[])

        assert "[file]" in prompt
        assert "alpha: Alpha" in prompt
        assert "\n  plain\n" in prompt
        assert "ev-1: manual" in prompt

    def test_history_serialization_lists_entries(self):
        """Linhas 220-223 / branches 218->220, 221->222 e 221->223."""
        history = [
            RefinementHistoryEntry(
                id="h-1",
                summary="primeiro refinamento",
                scope="local",
                applied_edits=[],
                created_at="2026-08-21T00:00:00+00:00",
                rolled_back=False,
            ),
            RefinementHistoryEntry(
                id="h-2",
                summary="segundo refinamento",
                scope="global",
                applied_edits=[],
                created_at="2026-08-21T01:00:00+00:00",
                rolled_back=True,
            ),
        ]

        prompt = build_refinement_prompt(messages=[], state=HarnessState(), history=history)

        assert "- h-1: primeiro refinamento (scope=local, rolled_back=False)" in prompt
        assert "- h-2: segundo refinamento (scope=global, rolled_back=True)" in prompt

    def test_prompt_includes_user_instructions(self):
        """Linha 266 / branch 265->266."""
        prompt = build_refinement_prompt(
            messages=[],
            state=HarnessState(),
            history=[],
            scope="local",
            instructions="Priorize decisões rastreáveis",
        )

        assert "<user_refine_instructions>" in prompt
        assert "Priorize decisões rastreáveis" in prompt
        assert "</user_refine_instructions>" in prompt


# ---------------------------------------------------------------------------
# auto_correction.py — caminhos de erro do apply (395-403, 416-424, 427-435)
# ---------------------------------------------------------------------------


class TestApplyValidationEdgeCases:
    @staticmethod
    def _apply_single(edit: RefinementEdit, state: HarnessState) -> RefinementResult:
        plan = RefinementPlan(
            proposal=RefinementProposal(
                summary="um edit",
                rationale="exercitar ramo de erro",
                expected_outcome="edit rejeitado",
                edits=[edit],
            ),
            id=f"ref_{edit.action}_{edit.kind}",
            rollback_scope="local",
            baseline_state=None,
        )
        return apply_refinement_proposal(plan, state, [])

    def test_delete_missing_entry_reports_not_found(self):
        """Linhas 395-403 / branch 394->395: delete de entrada inexistente."""
        state = HarnessState()

        result = self._apply_single(
            RefinementEdit(action="delete", kind="file", id="ghost"), state
        )

        edit = result.applied_edits[0]
        assert edit.applied is False
        assert edit.error == "entry not found"
        assert state.entries.get("file") == {}

    def test_create_existing_entry_reports_already_exists(self):
        """Linhas 416-424 / branch 415->416: create de entrada existente."""
        state = HarnessState(
            entries={"file": {"dup": {"title": "original", "content": "v1"}}}
        )

        result = self._apply_single(
            RefinementEdit(action="create", kind="file", id="dup", title="Novo"), state
        )

        edit = result.applied_edits[0]
        assert edit.applied is False
        assert edit.error == "entry already exists"
        assert state.entries["file"]["dup"]["title"] == "original"

    def test_update_missing_entry_reports_not_found(self):
        """Linhas 427-435 / branch 426->427: update de entrada inexistente."""
        state = HarnessState()

        result = self._apply_single(
            RefinementEdit(action="update", kind="config", id="nope", content="x"), state
        )

        edit = result.applied_edits[0]
        assert edit.applied is False
        assert edit.error == "entry not found"
        assert state.entries.get("config") == {}

    def test_update_without_id_uses_empty_edit_id(self):
        """Ramo else do computed_id (ação != create sem id) + update inexistente."""
        state = HarnessState()

        result = self._apply_single(
            RefinementEdit(action="update", kind="file", content="x"), state
        )

        edit = result.applied_edits[0]
        assert edit.edit_id == ""
        assert edit.applied is False
        assert edit.error == "entry not found"

    def test_apply_valid_skill_edit_with_reference_and_arguments(self):
        """Skill válido (arguments + reference dict) passa na validação."""
        state = HarnessState()

        result = self._apply_single(
            RefinementEdit(
                action="create",
                kind="skill",
                id="my-skill",
                title="My Skill",
                arguments={"cmd": "run"},
                reference={"source": "skills/my-skill"},
            ),
            state,
        )

        edit = result.applied_edits[0]
        assert edit.applied is True
        assert edit.error is None
        assert state.entries["skill"]["my-skill"]["arguments"] == {"cmd": "run"}
        assert state.entries["skill"]["my-skill"]["reference"] == {
            "source": "skills/my-skill"
        }


class TestValidateEditRules:
    def test_invalid_kind(self):
        """Linha 572 / branch 571->572."""
        assert (
            _validate_edit(RefinementEdit(action="create", kind="bogus"), "x")
            == "invalid kind: bogus"
        )

    def test_create_skill_without_arguments(self):
        """Linha 574 / branch 573->574."""
        assert (
            _validate_edit(RefinementEdit(action="create", kind="skill", id="s"), "s")
            == "create skill requires arguments"
        )

    def test_update_skill_without_arguments(self):
        """Ramo 573->574 também para update."""
        assert (
            _validate_edit(RefinementEdit(action="update", kind="skill", id="s"), "s")
            == "update skill requires arguments"
        )

    def test_skill_without_reference(self):
        """Linha 577 / branches 575->576 e 576->577."""
        assert (
            _validate_edit(
                RefinementEdit(action="create", kind="skill", arguments={}), ""
            )
            == "create skill requires reference"
        )

    def test_skill_reference_not_dict(self):
        """Linha 579 / branches 576->578 e 578->579."""
        assert (
            _validate_edit(
                RefinementEdit(
                    action="create", kind="skill", arguments={}, reference="skills/x"
                ),
                "",
            )
            == "create skill reference must be dict"
        )

    def test_skill_valid_returns_none(self):
        """Branch 578->580: reference dict válida libera o edit."""
        assert (
            _validate_edit(
                RefinementEdit(
                    action="create",
                    kind="skill",
                    arguments={"a": 1},
                    reference={"source": "s"},
                ),
                "",
            )
            is None
        )

    def test_delete_skill_without_arguments_returns_none(self):
        """Delete de skill dispensa arguments/reference."""
        assert (
            _validate_edit(RefinementEdit(action="delete", kind="skill", id="s"), "s")
            is None
        )


# ---------------------------------------------------------------------------
# auto_correction.py — rollback (500, 508, 524, 536->506, 558->560)
# ---------------------------------------------------------------------------


class TestRollbackGaps:
    def test_rollback_of_rollback_returns_none(self):
        """Linha 500 / branch 499->500: reverter uma reversão é no-op."""
        target = RefinementResult(
            id="rollback_ref_1",
            summary="s",
            rationale="r",
            expected_outcome="o",
            applied_edits=[],
            rollback_of="ref_1",
            scope="local",
        )

        assert rollback(target, HarnessState(), []) is None

    def test_rollback_skips_unapplied_and_inert_edits_without_history(self):
        """Linhas 508 e branches 507->508, 536->506 e 558->560."""
        state = HarnessState(
            entries={
                "file": {"keep": {"title": "Keep", "content": "v1", "path": "k.txt"}}
            }
        )
        applied = [
            AppliedRefinementEdit(
                action="create",
                kind="file",
                id="skip",
                edit_id="skip",
                applied=False,
                error="entry not found",
            ),
            AppliedRefinementEdit(
                action="update",
                kind="file",
                id="keep",
                edit_id="keep",
                before={
                    "title": "Keep",
                    "content": "v1",
                    "path": "k.txt",
                    "reference": None,
                    "arguments": None,
                    "metadata": None,
                },
                after={
                    "title": "Keep2",
                    "content": "v2",
                    "path": "k.txt",
                    "reference": None,
                    "arguments": None,
                    "metadata": None,
                },
                applied=True,
            ),
            AppliedRefinementEdit(
                action="update", kind="file", id="ghost", edit_id="ghost", applied=True
            ),
        ]
        target = RefinementResult(
            id="rr-mix",
            summary="s",
            rationale="r",
            expected_outcome="o",
            applied_edits=applied,
        )

        rolled = rollback(target, state, [])

        assert rolled is not None
        assert state.entries["file"]["keep"]["title"] == "Keep"
        assert state.entries["file"]["keep"]["content"] == "v1"
        assert "skip" not in state.entries["file"]
        assert "ghost" not in state.entries["file"]
        assert rolled.applied_edits[0].applied is True

    def test_rollback_delete_restores_entry(self):
        """Linha 524 / branch 510->524: delete aplicado reverte como create."""
        state = HarnessState(
            entries={
                "file": {"beta": {"title": "Beta", "content": "x", "path": "beta.txt"}}
            }
        )
        history: List[RefinementHistoryEntry] = []
        plan = RefinementPlan(
            proposal=RefinementProposal(
                summary="remove beta",
                rationale="limpeza",
                expected_outcome="beta removido",
                edits=[RefinementEdit(action="delete", kind="file", id="beta")],
            ),
            id="ref_del_beta",
            rollback_scope="local",
            baseline_state=state.clone(),
        )

        result = apply_refinement_proposal(plan, state, history)
        assert "beta" not in state.entries["file"]

        rolled = rollback(result, state, history)

        assert rolled is not None
        assert state.entries["file"]["beta"]["title"] == "Beta"
        assert state.entries["file"]["beta"]["content"] == "x"
        assert history[0].rolled_back is True


# ---------------------------------------------------------------------------
# auto_correction.py — _parse_proposal (597, 615, 619-620, 626)
# ---------------------------------------------------------------------------


class TestParseProposalGaps:
    def test_plain_code_fence_without_json_tag(self):
        """Linha 597 / branch 596->597: cerca ``` sem o sufixo json."""
        proposal = _parse_proposal('```\n{"summary": "fence", "edits": []}\n```')
        assert proposal.summary == "fence"
        assert proposal.edits == []

    def test_unbalanced_json_raises(self):
        """Linha 615 / branch 606->615: objeto nunca fechado."""
        with pytest.raises(ValueError, match="Unbalanced JSON"):
            _parse_proposal('prelúdio {"summary": "sem fechamento"')

    def test_balanced_but_invalid_json_raises(self):
        """Linhas 619-620: JSON balanceado, porém inválido."""
        with pytest.raises(ValueError, match="Invalid JSON from LLM"):
            _parse_proposal('{"summary": valor-invalido}')

    def test_non_dict_edit_entries_are_skipped(self):
        """Linha 626 / branch 625->626: itens de edits que não são dict."""
        payload = (
            '{"summary": "mixed", '
            '"edits": ["ignorar", 42, {"action": "create", "kind": "file", "id": "mantido"}]}'
        )

        proposal = _parse_proposal(payload)

        assert [edit.id for edit in proposal.edits] == ["mantido"]
        assert proposal.edits[0].action == "create"
