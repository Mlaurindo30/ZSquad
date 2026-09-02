"""Testes de contrato e cobertura (statements + branches) de scripts/background_review.py.

Contrato verificado aqui:
- review_artifact(kind, artifact_path, runtime_root, provider, *, dry_run=False):
  carrega o YAML do artefato (gate-decision GD-* ou handoff HANDOFF-*), monta resumo
  compacto (gate: id/gate/decisao/decisor/criterios/evidencias; handoff:
  id/de/para/resumo/memory_delta), chama provider.complete(..., expect_json=True)
  com instrucao estrita de JSON e, quando learning=true, deposita rascunho em
  skills/discovery/intake/ via AutoSkillLearner.deposit_in_intake (nunca promove).
- JSON invalido: 1 retry com lembrete mais estrito no user prompt; segunda falha ->
  {"status": "error", "error": "invalid-json"}.
- learning=false -> {"status": "no-learning", "rationale": ...}; dry-run ->
  {"status": "would-learn", ...} sem chamar deposit_in_intake.
- CLI main(): flags --kind/--artifact/--root/--dry-run/--model (default granite4.1:3b),
  provedor via llm_providers.Ollama(model=...); imprime REVIEW_LEARNED <skill> -> <path>
  | REVIEW_WOULD_LEARN <skill> | REVIEW_NO_LEARNING | REVIEW_ERROR: <msg>; sai 0 para
  learned/would-learn/no-learning, 1 para error e 2 para argumentos invalidos.

Nenhum teste faz rede ou LLM real: provider fake (duck .complete) injetado e
AutoSkillLearner substituido por classe fake via monkeypatch; na CLI,
llm_providers.Ollama e substituido por fabrica fake.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.background_review as bg


# ---------------------------------------------------------------------------
# Fakes duck-typed (nenhum LLM/rede real nestes testes)
# ---------------------------------------------------------------------------


class FakeProvider:
    """Provider fake: .complete registra a chamada e devolve itens do script em ordem."""

    def __init__(self, script):
        self.calls = []
        self._script = list(script)

    def complete(self, system_prompt, user_prompt, *, expect_json=False):
        self.calls.append(
            {"system": system_prompt, "user": user_prompt, "expect_json": expect_json}
        )
        item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, Exception):
            raise item
        return {
            "provider": "fake",
            "model": "fake-model",
            "content": item,
            "elapsed_ms": 0.0,
            "raw": {},
        }


def make_fake_learner(intake_root):
    """Cria classe fake de AutoSkillLearner que registra init/deposit e devolve path de intake."""
    calls = []

    class _FakeLearner:
        def __init__(self, squad_root=None):
            calls.append({"init": squad_root})

        def deposit_in_intake(self, draft):
            calls.append({"draft": draft})
            return Path(intake_root) / draft.skill_name / "SKILL.md"

    return _FakeLearner, calls


# ---------------------------------------------------------------------------
# Artefatos de entrada (YAML conforme contracts/gate-decision e handoff schemas)
# ---------------------------------------------------------------------------

GATE_YAML = """\
decision_id: GD-2026-0001
gate_id: G4-code-security
work_item_id: TASK-0042
decision: approved
decider: 09-quality-guardian
criteria:
  - name: dependencias-verificadas
    result: pass
  - name: segredos-ausentes
    result: fail
evidence:
  - traceability/verification-log.md#pip-audit
  - pytest scripts/tests/test_security_guardrails.py
human_approval:
  required: false
  status: not_required
  approved_by: null
  evidence: null
decided_at: "2026-08-22T10:00:00+00:00"
"""

HANDOFF_YAML = """\
id: HANDOFF-2026-0007
work_item_id: TASK-0042
from: 06-builder
to: 09-quality-guardian
created_at: "2026-08-22T10:05:00+00:00"
status: ready
summary: Implementacao concluida com testes verdes e cobertura total.
artifacts:
  - src/core/feature.py
decisions: []
open_questions: []
risks: []
evidence:
  - pytest scripts/tests/ -q
memory_delta: memory/deltas/MEM-2026-0003.yaml
next_gate: G5-quality
acceptance:
  criteria_checked:
    - cobertura-100-pct
  recipient_ack_required: true
acknowledgement:
  status: pending
  acknowledged_by: 09-quality-guardian
  acknowledged_at: null
  note: ""
"""

LEARNED_GATE_PAYLOAD = {
    "learning": True,
    "skill_name": "Schema Review Checklist",
    "description": "Checklist de revisao de migracoes de schema antes de tocar producao.",
    "rationale": "O criterio 'segredos-ausentes' falhou por um padrao recorrente de revisao.",
    "markdown": (
        "---\n"
        "name: schema-review-checklist\n"
        "description: Checklist de revisao de migracoes de schema.\n"
        "---\n"
        "\n"
        "# Schema Review Checklist\n"
        "\n"
        "## Coletar impacto\n"
        "Mapear tabelas afetadas.\n"
        "## Validar rollback\n"
        "Exigir plano de rollback testado.\n"
    ),
}

NO_LEARNING_PAYLOAD = {
    "learning": False,
    "skill_name": None,
    "description": None,
    "rationale": "Aprovacao de rotina sem tecnica ou correcao reutilizavel.",
    "markdown": None,
}

FALLBACK_LEARNED_PAYLOAD = {
    "learning": True,
    "skill_name": None,
    "description": None,
    "rationale": "Aprendizado claro, mas campos opcionais ausentes na resposta.",
    "markdown": None,
}


def write_artifact(tmp_path, name, text):
    """Grava um artefato YAML em tmp_path e devolve o caminho."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# review_artifact: resumo, deposito, dry-run e falhas
# ---------------------------------------------------------------------------


def test_gate_summary_learning_true_deposits_intake(tmp_path, monkeypatch):
    artifact = write_artifact(tmp_path, "GD-2026-0001.yaml", GATE_YAML)
    provider = FakeProvider([json.dumps(LEARNED_GATE_PAYLOAD)])
    fake_cls, calls = make_fake_learner(tmp_path / "intake")
    monkeypatch.setattr(bg, "AutoSkillLearner", fake_cls)

    result = bg.review_artifact("gate", artifact, tmp_path, provider)

    expected_path = tmp_path / "intake" / "schema-review-checklist" / "SKILL.md"
    assert result == {
        "status": "learned",
        "intake_path": expected_path,
        "skill_name": "schema-review-checklist",
    }
    # learner instanciado com o runtime_root repassado e deposito executado uma vez
    assert calls[0] == {"init": tmp_path}
    assert isinstance(calls[1]["draft"], bg.LearnedSkillDraft)
    draft = calls[1]["draft"]
    assert draft.skill_name == "schema-review-checklist"
    assert draft.description == LEARNED_GATE_PAYLOAD["description"]
    assert draft.target_persona == "18-skill-curator"
    assert draft.source_work_item == "GD-2026-0001"
    assert draft.raw_markdown == LEARNED_GATE_PAYLOAD["markdown"]
    assert draft.steps == ["Coletar impacto", "Validar rollback"]
    assert draft.trigger_conditions == [
        "revisão pós-entrega de artefato gate (GD-2026-0001)"
    ]

    # chamada unica ao LLM com system prompt estrito e resumo do gate no user prompt
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["system"] == bg.SYSTEM_PROMPT
    assert call["expect_json"] is True
    for key in ('"learning"', '"skill_name"', '"description"', '"rationale"', '"markdown"'):
        assert key in call["system"]
    user = call["user"]
    assert "tipo: gate-decision" in user
    assert "id: GD-2026-0001" in user
    assert "gate: G4-code-security" in user
    assert "decisao: approved" in user
    assert "decisor: 09-quality-guardian" in user
    assert "dependencias-verificadas: pass" in user
    assert "segredos-ausentes: fail" in user
    assert "traceability/verification-log.md#pip-audit" in user
    assert "pytest scripts/tests/test_security_guardrails.py" in user


def test_handoff_summary_no_learning(tmp_path, monkeypatch):
    artifact = write_artifact(tmp_path, "HANDOFF-2026-0007.yaml", HANDOFF_YAML)
    provider = FakeProvider([json.dumps(NO_LEARNING_PAYLOAD)])
    fake_cls, calls = make_fake_learner(tmp_path / "intake")
    monkeypatch.setattr(bg, "AutoSkillLearner", fake_cls)

    result = bg.review_artifact("handoff", artifact, tmp_path, provider)

    assert result == {
        "status": "no-learning",
        "rationale": NO_LEARNING_PAYLOAD["rationale"],
    }
    assert calls == []  # sem learning nao ha learner nem deposito
    user = provider.calls[0]["user"]
    assert "tipo: handoff" in user
    assert "id: HANDOFF-2026-0007" in user
    assert "de: 06-builder" in user
    assert "para: 09-quality-guardian" in user
    assert "resumo: Implementacao concluida com testes verdes e cobertura total." in user
    assert "memory_delta: memory/deltas/MEM-2026-0003.yaml" in user


def test_dry_run_learning_true_does_not_deposit(tmp_path, monkeypatch):
    artifact = write_artifact(tmp_path, "GD-2026-0001.yaml", GATE_YAML)
    provider = FakeProvider([json.dumps(LEARNED_GATE_PAYLOAD)])
    fake_cls, calls = make_fake_learner(tmp_path / "intake")
    monkeypatch.setattr(bg, "AutoSkillLearner", fake_cls)

    result = bg.review_artifact("gate", artifact, tmp_path, provider, dry_run=True)

    assert result == {
        "status": "would-learn",
        "skill_name": "schema-review-checklist",
        "rationale": LEARNED_GATE_PAYLOAD["rationale"],
    }
    assert calls == []  # dry-run nunca instancia o learner nem deposita
    assert len(provider.calls) == 1


def test_invalid_json_retries_once_then_learns_with_fallbacks(tmp_path, monkeypatch):
    artifact = write_artifact(tmp_path, "GD-2026-0001.yaml", GATE_YAML)
    provider = FakeProvider(
        ["isto nao e JSON {", json.dumps(FALLBACK_LEARNED_PAYLOAD)]
    )
    fake_cls, calls = make_fake_learner(tmp_path / "intake")
    monkeypatch.setattr(bg, "AutoSkillLearner", fake_cls)

    result = bg.review_artifact("gate", artifact, tmp_path, provider)

    assert result["status"] == "learned"
    assert result["skill_name"] == "learned-from-gd-2026-0001"
    assert len(provider.calls) == 2
    assert bg.STRICTER_REMINDER.strip() in provider.calls[1]["user"]
    assert bg.STRICTER_REMINDER.strip() not in provider.calls[0]["user"]
    draft = calls[1]["draft"]
    # fallbacks: slug do id do artefato, descricao e markdown minimos, steps default
    assert draft.skill_name == "learned-from-gd-2026-0001"
    assert draft.description == "Aprendizado extraído do artefato GD-2026-0001."
    assert draft.raw_markdown == (
        "---\n"
        "name: learned-from-gd-2026-0001\n"
        "description: Aprendizado extraído do artefato GD-2026-0001.\n"
        "---\n"
        "\n"
        "# learned-from-gd-2026-0001\n"
        "\n"
        "Aprendizado extraído do artefato GD-2026-0001.\n"
    )
    assert draft.steps == ["Seguir o procedimento descrito no corpo da skill."]


def test_invalid_json_twice_returns_error(tmp_path):
    artifact = write_artifact(tmp_path, "GD-2026-0001.yaml", GATE_YAML)
    provider = FakeProvider(["garbage {{{", "ainda nao json"])

    result = bg.review_artifact("gate", artifact, tmp_path, provider)

    assert result == {"status": "error", "error": "invalid-json"}
    assert len(provider.calls) == 2  # retry unico, nunca uma terceira tentativa
    assert bg.STRICTER_REMINDER.strip() in provider.calls[1]["user"]


def test_provider_exception_returns_error(tmp_path):
    artifact = write_artifact(tmp_path, "HANDOFF-2026-0007.yaml", HANDOFF_YAML)
    provider = FakeProvider([RuntimeError("ollama fora do ar")])

    result = bg.review_artifact("handoff", artifact, tmp_path, provider)

    assert result["status"] == "error"
    assert result["error"].startswith("provider-failure:")
    assert "ollama fora do ar" in result["error"]
    assert len(provider.calls) == 1  # excecao de provedor nao aciona retry de JSON


def test_missing_artifact_file_returns_error(tmp_path):
    provider = FakeProvider([])

    result = bg.review_artifact("gate", tmp_path / "inexistente.yaml", tmp_path, provider)

    assert result["status"] == "error"
    assert result["error"].startswith("unreadable-artifact:")
    assert provider.calls == []


def test_non_mapping_yaml_returns_error(tmp_path):
    artifact = write_artifact(tmp_path, "scalar.yaml", "apenas uma string solta\n")
    provider = FakeProvider([])

    result = bg.review_artifact("gate", artifact, tmp_path, provider)

    assert result["status"] == "error"
    assert result["error"].startswith("unreadable-artifact:")
    assert provider.calls == []


def test_invalid_kind_raises_value_error(tmp_path):
    provider = FakeProvider([])

    with pytest.raises(ValueError):
        bg.review_artifact("epic", tmp_path / "x.yaml", tmp_path, provider)


def test_gate_summary_without_optional_lists(tmp_path):
    minimal = (
        "decision_id: GD-2026-0002\n"
        "gate_id: G5-quality\n"
        "decision: rejected\n"
        "decider: 11-vulnerability-hunter\n"
    )
    artifact = write_artifact(tmp_path, "GD-2026-0002.yaml", minimal)
    provider = FakeProvider([json.dumps(NO_LEARNING_PAYLOAD)])

    result = bg.review_artifact("gate", artifact, tmp_path, provider)

    assert result["status"] == "no-learning"
    user = provider.calls[0]["user"]
    assert "id: GD-2026-0002" in user
    assert "decisao: rejected" in user
    assert "decisor: 11-vulnerability-hunter" in user
    # secoes de listas aparecem vazias quando criteria/evidence estao ausentes
    assert "criterios:" in user
    assert "evidencias:" in user
    assert "dependencias-verificadas" not in user


# ---------------------------------------------------------------------------
# CLI main()
# ---------------------------------------------------------------------------


def test_cli_learned_prints_and_exits_zero(tmp_path, monkeypatch, capsys):
    artifact = write_artifact(tmp_path, "GD-2026-0001.yaml", GATE_YAML)
    provider = FakeProvider([json.dumps(LEARNED_GATE_PAYLOAD)])
    made = {}

    def fake_ollama(model, **kwargs):
        made["model"] = model
        return provider

    monkeypatch.setattr(bg.llm_providers, "Ollama", fake_ollama)
    fake_cls, calls = make_fake_learner(tmp_path / "intake")
    monkeypatch.setattr(bg, "AutoSkillLearner", fake_cls)

    code = bg.main(
        ["--kind", "gate", "--artifact", str(artifact), "--root", str(tmp_path)]
    )

    assert code == 0
    assert made["model"] == "granite4.1:3b"  # default do CLI
    expected_path = tmp_path / "intake" / "schema-review-checklist" / "SKILL.md"
    assert capsys.readouterr().out == (
        f"REVIEW_LEARNED schema-review-checklist -> {expected_path}\n"
    )
    assert len(calls) == 2  # init + deposit


def test_cli_no_learning_exits_zero(tmp_path, monkeypatch, capsys):
    artifact = write_artifact(tmp_path, "HANDOFF-2026-0007.yaml", HANDOFF_YAML)
    provider = FakeProvider([json.dumps(NO_LEARNING_PAYLOAD)])
    monkeypatch.setattr(bg.llm_providers, "Ollama", lambda model, **kw: provider)

    code = bg.main(
        ["--kind", "handoff", "--artifact", str(artifact), "--root", str(tmp_path)]
    )

    assert code == 0
    assert capsys.readouterr().out == "REVIEW_NO_LEARNING\n"


def test_cli_dry_run_would_learn_exits_zero(tmp_path, monkeypatch, capsys):
    artifact = write_artifact(tmp_path, "HANDOFF-2026-0007.yaml", HANDOFF_YAML)
    provider = FakeProvider([json.dumps(LEARNED_GATE_PAYLOAD)])
    monkeypatch.setattr(bg.llm_providers, "Ollama", lambda model, **kw: provider)
    fake_cls, calls = make_fake_learner(tmp_path / "intake")
    monkeypatch.setattr(bg, "AutoSkillLearner", fake_cls)

    code = bg.main(
        [
            "--kind", "handoff",
            "--artifact", str(artifact),
            "--root", str(tmp_path),
            "--dry-run",
            "--model", "granite4.1:8b",
        ]
    )

    assert code == 0
    assert capsys.readouterr().out == "REVIEW_WOULD_LEARN schema-review-checklist\n"
    assert calls == []  # dry-run nao deposita
    assert "tipo: handoff" in provider.calls[0]["user"]


def test_cli_error_exits_one(tmp_path, monkeypatch, capsys):
    artifact = write_artifact(tmp_path, "GD-2026-0001.yaml", GATE_YAML)
    provider = FakeProvider(["nope", "tambem nao"])
    monkeypatch.setattr(bg.llm_providers, "Ollama", lambda model, **kw: provider)

    code = bg.main(
        ["--kind", "gate", "--artifact", str(artifact), "--root", str(tmp_path)]
    )

    assert code == 1
    assert capsys.readouterr().out == "REVIEW_ERROR: invalid-json\n"


def test_cli_invalid_kind_exits_two(tmp_path):
    code = bg.main(
        ["--kind", "bogus", "--artifact", "x.yaml", "--root", str(tmp_path)]
    )

    assert code == 2


# ---------------------------------------------------------------------------
# Normalizacao de sys.path no import do modulo
# ---------------------------------------------------------------------------


def test_sys_path_guard_inserts_root_when_missing(monkeypatch):
    """Remove a raiz de sys.path, recarrega o modulo e verifica a reinsercao do guard."""
    monkeypatch.setattr(
        sys, "path", [p for p in sys.path if p != str(bg.ROOT)]
    )
    importlib.reload(bg)
    assert str(bg.ROOT) in sys.path


def test_main_guard_executes_cli(tmp_path, monkeypatch, capsys):
    """Executa o modulo como __main__ (runpy) e verifica o caminho CLI de saida 0."""
    import runpy

    artifact = write_artifact(tmp_path, "GD-2026-0001.yaml", GATE_YAML)
    provider = FakeProvider([json.dumps(NO_LEARNING_PAYLOAD)])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "background_review.py",
            "--kind", "gate",
            "--artifact", str(artifact),
            "--root", str(tmp_path),
        ],
    )
    monkeypatch.setattr(bg.llm_providers, "Ollama", lambda model, **kw: provider)

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("scripts.background_review", run_name="__main__")

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == "REVIEW_NO_LEARNING\n"
