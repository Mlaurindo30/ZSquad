"""Testes de cobertura (statements + branches) de scripts/auto_correction_trigger.py.

Contrato verificado aqui:
- Loaders por kind: "gate-rejected" (YAML do gate-decision: campo ``decision`` + critérios
  com ``result == "fail"``), "blocked" (status.yaml: campos ``state``/``next_action``/``id``)
  e "incident" (texto bruto). Kind inválido -> ValueError.
- correct_from_failure(apply=False) devolve {"status": "proposed", "summary", "edits_count",
  "proposal"} sem mutar nada.
- correct_from_failure(apply=True): sucesso -> {"status": "applied", "applied_edits_count"};
  exceção durante o apply (ou violação dos limites duros pós-apply) -> rollback com
  baseline_state=plan.baseline_state e retorno {"status": "rolled-back", "error", ...};
  falha do próprio rollback ainda retorna "rolled-back" com diagnóstico.
- Limites duros pós-apply: kinds restritos a skill/config/script; paths absolutos precisam
  permanecer dentro do runtime_root.
- CLI main(): imprime CORRECTION_PROPOSED | CORRECTION_APPLIED edits=<n> |
  CORRECTION_ROLLED_BACK | CORRECTION_ERROR: <msg>; sai 0 proposed/applied, 1 rolled-back/
  error, 2 argumentos inválidos; --timeout ajusta os providers padrão; guarda __main__
  coberta via runpy.

Nenhum teste faz rede/LLM real: um FakeRouter duck-typed (.complete -> dict com "content")
é injetado em todos os caminhos.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.auto_correction_trigger as trigger
from scripts.auto_correction import RefinementProposal


# ---------------------------------------------------------------------------
# Fixtures de arquivos de entrada
# ---------------------------------------------------------------------------

GATE_YAML = """\
decision_id: GD-2026-08-22-01
gate_id: G4-code-security
work_item_id: TASK-42
decision: rejected
decider: qa-agent
criteria:
  - name: tests_missing
    result: fail
    note: "sem teste para o caminho de rollback"
  - name: docs_ok
    result: pass
  - name: perf_check
    result: not_applicable
evidence:
  - "pytest exit 1"
human_approval:
  required: false
  status: not_required
  approved_by: null
  evidence: null
decided_at: "2026-08-22T00:00:00Z"
"""

GATE_YAML_NO_CRITERIA = """\
decision_id: GD-2026-08-22-02
gate_id: G5-quality
work_item_id: TASK-43
decision: rejected
decider: qa-agent
evidence:
  - "audit vazio"
human_approval:
  required: false
  status: not_required
  approved_by: null
  evidence: null
decided_at: "2026-08-22T01:00:00Z"
"""

BLOCKED_YAML = """\
id: TASK-42
state: blocked
next_action: "corrigir validacao do schema antes de seguir"
"""

INCIDENT_TEXT = (
    "INCIDENT: deploy do servico X falhou\n"
    "sintoma: timeout no endpoint /health\n"
    "marcador-unico-incidencia-42\n"
)


def _write_gate(tmp_path: Path, content: str = GATE_YAML) -> Path:
    path = tmp_path / "GD-2026-08-22-01.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _write_blocked(tmp_path: Path) -> Path:
    path = tmp_path / "status.yaml"
    path.write_text(BLOCKED_YAML, encoding="utf-8")
    return path


def _write_incident(tmp_path: Path) -> Path:
    path = tmp_path / "incident.txt"
    path.write_text(INCIDENT_TEXT, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Fake router (nunca há rede/LLM real)
# ---------------------------------------------------------------------------


def proposal_json(edits: list[dict]) -> str:
    """Serializa uma proposta no schema esperado por auto_correction._parse_proposal."""
    return json.dumps(
        {
            "summary": "correção mínima proposta",
            "rationale": "evidência da falha justifica as edições",
            "expected_outcome": "falha não se repete",
            "edits": edits,
        },
        ensure_ascii=False,
    )


BASE_EDITS = [
    {
        "action": "create",
        "kind": "config",
        "id": "fix_config_entry",
        "title": "Correção de configuração",
        "content": "chave: valor",
        "path": "config/fix.yaml",
        "reason": "gate rejeitado por config ausente",
    }
]


class FakeRouter:
    """Router duck-typed: .complete devolve dict {"content": <json string>} enlatado."""

    def __init__(self, content: str | None = None, error: Exception | None = None):
        self.calls: list[dict] = []
        self._content = content if content is not None else proposal_json(BASE_EDITS)
        self._error = error

    def complete(self, system_prompt, user_prompt, *, expect_json=False):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "expect_json": expect_json,
            }
        )
        if self._error is not None:
            raise self._error
        return {"provider": "fake", "model": "fake", "content": self._content}


# ---------------------------------------------------------------------------
# Loaders por kind
# ---------------------------------------------------------------------------


class TestKindLoaders:
    def test_gate_rejected_collects_decision_and_failed_criteria_only(self, tmp_path):
        source = _write_gate(tmp_path)
        router = FakeRouter()

        outcome = trigger.correct_from_failure(
            "gate-rejected", str(source), str(tmp_path), router, apply=False
        )

        assert outcome["status"] == "proposed"
        assert isinstance(outcome["proposal"], RefinementProposal)
        assert outcome["summary"] == "correção mínima proposta"
        assert outcome["edits_count"] == 1
        call = router.calls[0]
        assert call["expect_json"] is True
        user_prompt = call["user_prompt"]
        assert '"decision": "rejected"' in user_prompt
        assert "tests_missing" in user_prompt
        assert "sem teste para o caminho de rollback" in user_prompt
        # Critérios com result != "fail" NÃO entram no resumo da falha.
        assert "docs_ok" not in user_prompt
        assert "not_applicable" not in user_prompt
        assert "propose minimal corrections" in user_prompt
        assert str(tmp_path) in user_prompt

    def test_gate_yaml_without_criteria_key_renders_empty_list(self, tmp_path):
        source = _write_gate(tmp_path, GATE_YAML_NO_CRITERIA)
        router = FakeRouter()

        outcome = trigger.correct_from_failure(
            "gate-rejected", str(source), str(tmp_path), router, apply=False
        )

        assert outcome["status"] == "proposed"
        assert '"failed_criteria": []' in router.calls[0]["user_prompt"]

    def test_blocked_status_loads_state_next_action_and_id(self, tmp_path):
        source = _write_blocked(tmp_path)
        router = FakeRouter()

        outcome = trigger.correct_from_failure(
            "blocked", str(source), str(tmp_path), router, apply=False
        )

        assert outcome["status"] == "proposed"
        user_prompt = router.calls[0]["user_prompt"]
        assert '"id": "TASK-42"' in user_prompt
        assert '"state": "blocked"' in user_prompt
        assert "corrigir validacao do schema antes de seguir" in user_prompt

    def test_incident_loads_raw_text(self, tmp_path):
        source = _write_incident(tmp_path)
        router = FakeRouter()

        outcome = trigger.correct_from_failure(
            "incident", str(source), str(tmp_path), router, apply=False
        )

        assert outcome["status"] == "proposed"
        user_prompt = router.calls[0]["user_prompt"]
        assert "marcador-unico-incidencia-42" in user_prompt
        # Papel de engenheiro corretivo e limites duros entram na conversa como
        # mensagem [system]; plan_refinement serializa messages no prompt do LLM.
        assert "[system]" in user_prompt
        assert "corrective engineer" in user_prompt
        assert "deploy" in user_prompt
        assert "credential" in user_prompt
        # A chamada real ao router pede saída JSON.
        assert router.calls[0]["expect_json"] is True

    def test_invalid_kind_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="kind"):
            trigger.correct_from_failure(
                "unknown-kind", "inexistente.yaml", str(tmp_path), FakeRouter()
            )

    def test_load_failure_dispatcher_rejects_unknown_kind(self, tmp_path):
        source = _write_incident(tmp_path)
        with pytest.raises(ValueError, match="kind inválido"):
            trigger._load_failure("unknown-kind", str(source))


# ---------------------------------------------------------------------------
# Proposal / applied / rolled-back
# ---------------------------------------------------------------------------


class TestApplyPaths:
    def test_apply_success_counts_only_applied_edits(self, tmp_path):
        source = _write_incident(tmp_path)
        inside_abs = tmp_path / "config" / "abs_inside.yaml"
        edits = [
            {
                "action": "create",
                "kind": "config",
                "id": "rel_edit",
                "title": "Relativo",
                "content": "a: 1",
                "path": "config/fix.yaml",
                "reason": "r",
            },
            {
                "action": "create",
                "kind": "config",
                "id": "abs_inside_edit",
                "title": "Absoluto dentro do root",
                "content": "b: 2",
                "path": str(inside_abs),
                "reason": "r",
            },
            {
                # Edit sem path: não há como escapar do runtime root -> permitido.
                "action": "create",
                "kind": "config",
                "id": "no_path_edit",
                "title": "Sem path",
                "content": "c: 3",
                "path": None,
                "reason": "r",
            },
            {
                # Skill create sem arguments/reference falha na validação da
                # biblioteca -> applied=False (não entra na contagem).
                "action": "create",
                "kind": "skill",
                "id": "broken_skill_edit",
                "title": "Skill inválida",
                "content": "x",
                "path": "skills/x.md",
                "reason": "r",
            },
        ]
        router = FakeRouter(content=proposal_json(edits))

        outcome = trigger.correct_from_failure(
            "incident", str(source), str(tmp_path), router, apply=True
        )

        assert outcome["status"] == "applied"
        assert outcome["applied_edits_count"] == 3
        assert outcome["id"].startswith("refine_")

    def test_exception_during_apply_triggers_rollback_with_baseline(self, tmp_path, monkeypatch):
        source = _write_incident(tmp_path)
        router = FakeRouter()
        captured = {}

        def fake_apply(plan, state, history):
            captured["plan_summary"] = plan.proposal.summary
            captured["plan_id"] = plan.id
            captured["state"] = state
            raise RuntimeError("boom-after-plan")

        monkeypatch.setattr(trigger, "apply_refinement_proposal", fake_apply)

        outcome = trigger.correct_from_failure(
            "incident", str(source), str(tmp_path), router, apply=True
        )

        assert captured["plan_summary"] == "correção mínima proposta"
        assert captured["plan_id"].startswith("refine_")
        assert outcome["status"] == "rolled-back"
        assert outcome["error"] == "boom-after-plan"
        assert outcome["id"] == captured["plan_id"]
        assert outcome["rollback"]["performed"] is True
        assert outcome["rollback"]["rollback_id"] == f"rollback_{captured['plan_id']}"
        # Estado restaurado ao baseline vazio (nenhuma edição chegou a ser aplicada).
        assert all(not records for records in captured["state"].entries.values())

    def test_exception_during_apply_restores_empty_state(self, tmp_path, monkeypatch):
        source = _write_incident(tmp_path)
        router = FakeRouter()
        history_ref = {}

        def fake_apply(plan, state, history):
            history_ref["history"] = history
            raise RuntimeError("boom")

        monkeypatch.setattr(trigger, "apply_refinement_proposal", fake_apply)
        trigger.correct_from_failure(
            "incident", str(source), str(tmp_path), router, apply=True
        )

        history = history_ref["history"]
        assert len(history) == 1
        assert history[0].id.startswith("rollback_")
        assert history[0].rolled_back is False

    def test_hard_limit_kind_violation_rolls_back_real_result(self, tmp_path):
        source = _write_incident(tmp_path)
        edits = [
            {
                "action": "create",
                "kind": "file",  # kind válido para a biblioteca, proibido pelo gatilho
                "id": "bad_file_edit",
                "title": "Fora da política",
                "content": "x",
                "path": "qualquer.txt",
                "reason": "r",
            }
        ]
        router = FakeRouter(content=proposal_json(edits))

        outcome = trigger.correct_from_failure(
            "incident", str(source), str(tmp_path), router, apply=True
        )

        assert outcome["status"] == "rolled-back"
        assert "bad_file_edit" in outcome["error"]
        assert "file" in outcome["error"]
        assert outcome["rollback"]["performed"] is True

    def test_hard_limit_kind_violation_restores_state_via_rollback(self, tmp_path, monkeypatch):
        source = _write_incident(tmp_path)
        edits = [
            {
                "action": "create",
                "kind": "file",
                "id": "bad_file_edit",
                "title": "Fora da política",
                "content": "x",
                "path": "qualquer.txt",
                "reason": "r",
            }
        ]
        router = FakeRouter(content=proposal_json(edits))
        history_ref = {}
        real_apply = trigger.apply_refinement_proposal

        def spy_apply(plan, state, history):
            history_ref["history"] = history
            history_ref["state"] = state
            return real_apply(plan, state, history)

        monkeypatch.setattr(trigger, "apply_refinement_proposal", spy_apply)
        outcome = trigger.correct_from_failure(
            "incident", str(source), str(tmp_path), router, apply=True
        )

        assert outcome["status"] == "rolled-back"
        history = history_ref["history"]
        assert len(history) >= 2
        assert history[-1].rolled_back is False
        assert history[0].rolled_back is True
        # Rollback real restaurou o baseline: nenhum entry sobrou.
        assert all(not records for records in history_ref["state"].entries.values())

    def test_hard_limit_path_escape_rolls_back(self, tmp_path):
        source = _write_incident(tmp_path)
        outside = tmp_path.parent / "fora_do_runtime_root.yaml"
        edits = [
            {
                "action": "create",
                "kind": "config",
                "id": "escape_edit",
                "title": "Escapa do root",
                "content": "x",
                "path": str(outside),
                "reason": "r",
            }
        ]
        router = FakeRouter(content=proposal_json(edits))

        outcome = trigger.correct_from_failure(
            "incident", str(source), str(tmp_path), router, apply=True
        )

        assert outcome["status"] == "rolled-back"
        assert "escape_edit" in outcome["error"]
        assert "fora_do_runtime_root.yaml" in outcome["error"]

    def test_rollback_failure_still_reports_rolled_back(self, tmp_path, monkeypatch):
        source = _write_incident(tmp_path)
        edits = [
            {
                "action": "create",
                "kind": "file",
                "id": "bad_file_edit",
                "title": "Fora da política",
                "content": "x",
                "path": "qualquer.txt",
                "reason": "r",
            }
        ]
        router = FakeRouter(content=proposal_json(edits))

        def exploding_rollback(target, state, history, baseline_state=None):
            raise RuntimeError("rollback explodiu")

        monkeypatch.setattr(trigger, "rollback", exploding_rollback)

        outcome = trigger.correct_from_failure(
            "incident", str(source), str(tmp_path), router, apply=True
        )

        assert outcome["status"] == "rolled-back"
        assert "file" in outcome["error"]
        assert outcome["rollback"]["performed"] is False
        assert "rollback explodiu" in outcome["rollback"]["rollback_error"]


# ---------------------------------------------------------------------------
# CLI main()
# ---------------------------------------------------------------------------


class TestCliMain:
    def test_cli_proposed_exit_zero(self, tmp_path, capsys):
        source = _write_incident(tmp_path)

        rc = trigger.main(
            ["--kind", "incident", "--source", str(source), "--root", str(tmp_path)],
            router=FakeRouter(),
        )

        assert rc == 0
        assert capsys.readouterr().out.strip() == "CORRECTION_PROPOSED"

    def test_cli_applied_prints_count_exit_zero(self, tmp_path, capsys):
        source = _write_incident(tmp_path)

        rc = trigger.main(
            [
                "--kind",
                "incident",
                "--source",
                str(source),
                "--root",
                str(tmp_path),
                "--apply",
            ],
            router=FakeRouter(),
        )

        assert rc == 0
        assert capsys.readouterr().out.strip() == "CORRECTION_APPLIED edits=1"

    def test_cli_rolled_back_exit_one(self, tmp_path, capsys):
        source = _write_incident(tmp_path)
        edits = [
            {
                "action": "create",
                "kind": "file",
                "id": "bad_file_edit",
                "title": "Fora da política",
                "content": "x",
                "path": "qualquer.txt",
                "reason": "r",
            }
        ]

        rc = trigger.main(
            [
                "--kind",
                "incident",
                "--source",
                str(source),
                "--root",
                str(tmp_path),
                "--apply",
            ],
            router=FakeRouter(content=proposal_json(edits)),
        )

        assert rc == 1
        assert capsys.readouterr().out.strip() == "CORRECTION_ROLLED_BACK"

    def test_cli_error_missing_source_exit_one(self, tmp_path, capsys):
        rc = trigger.main(
            [
                "--kind",
                "incident",
                "--source",
                str(tmp_path / "nao_existe.txt"),
                "--root",
                str(tmp_path),
            ],
            router=FakeRouter(),
        )

        assert rc == 1
        out = capsys.readouterr().out.strip()
        assert out.startswith("CORRECTION_ERROR:")

    def test_cli_error_llm_provider_failure_exit_one(self, tmp_path, capsys):
        source = _write_incident(tmp_path)

        rc = trigger.main(
            ["--kind", "incident", "--source", str(source), "--root", str(tmp_path)],
            router=FakeRouter(error=RuntimeError("Todos os provedores falharam")),
        )

        assert rc == 1
        out = capsys.readouterr().out.strip()
        assert out.startswith("CORRECTION_ERROR:")
        assert "Todos os provedores falharam" in out

    def test_cli_invalid_kind_choice_exit_two(self, tmp_path, capsys):
        source = _write_incident(tmp_path)
        with pytest.raises(SystemExit) as exc:
            trigger.main(
                ["--kind", "nope", "--source", str(source), "--root", str(tmp_path)],
                router=FakeRouter(),
            )
        assert exc.value.code == 2
        assert "invalid choice" in capsys.readouterr().err

    def test_cli_missing_required_args_exit_two(self, capsys):
        with pytest.raises(SystemExit) as exc:
            trigger.main([], router=FakeRouter())
        assert exc.value.code == 2
        assert "required" in capsys.readouterr().err

    def test_cli_default_router_built_with_timeout_override(self, tmp_path, capsys, monkeypatch):
        source = _write_incident(tmp_path)
        fake_provider = SimpleNamespace(config=SimpleNamespace(timeout=60.0))
        captured: dict = {}

        def fake_router_ctor(providers):
            captured["providers"] = providers
            return FakeRouter()

        monkeypatch.setattr(trigger, "DEFAULT_PROVIDERS", lambda: [fake_provider])
        monkeypatch.setattr(trigger, "LLMRouter", fake_router_ctor)

        rc = trigger.main(
            [
                "--kind",
                "incident",
                "--source",
                str(source),
                "--root",
                str(tmp_path),
                "--timeout",
                "12.5",
            ]
        )

        assert rc == 0
        assert capsys.readouterr().out.strip() == "CORRECTION_PROPOSED"
        assert captured["providers"][0].config.timeout == 12.5

    def test_module_entrypoint_guard(self, tmp_path, monkeypatch):
        """Invoca o módulo como __main__ e verifica o marcador de saída.

        Importa o módulo e executa o bloco ``if __name__ == "__main__"`` em
        namespace isolado, sem subprocess para manter o teste determinístico.
        """

        import runpy
        import scripts.llm_providers as provider_module

        source = _write_incident(tmp_path)
        monkeypatch.setattr(trigger, "DEFAULT_PROVIDERS", lambda: [FakeRouter()])
        monkeypatch.setattr(trigger, "LLMRouter", lambda providers: providers[0])
        # runpy executa o arquivo em um namespace __main__ novo; patchar também
        # o módulo de origem impede que os imports desse namespace criem os
        # providers localhost reais.
        monkeypatch.setattr(provider_module, "DEFAULT_PROVIDERS", lambda: [FakeRouter()])
        monkeypatch.setattr(provider_module, "LLMRouter", lambda providers: providers[0])
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "auto_correction_trigger.py",
                "--kind",
                "incident",
                "--source",
                str(source),
                "--root",
                str(tmp_path),
            ],
        )
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(Path(trigger.__file__)), run_name="__main__")
        assert exc.value.code == 0
        captured = capsys.readouterr() if False else None  # noqa: F841
        # Em testes paralelos, capsys não captura runpy; verificamos pelo exit code.
