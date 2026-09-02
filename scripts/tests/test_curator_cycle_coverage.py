"""Cobertura total de scripts/curator_cycle.py — ciclo periodico de revisao de intake.

Cobre: intake vazio (com e sem diretorio), backup com conteudo correto
(path/mtime/sha256), item aprovado (promote-candidate), item com BLOCKER
(fix-findings), idade exatamente 7 dias vs >7 dias (expire-candidate),
injetabilidade de ``now``, chamada real aos gates de skill_curator,
unicidade do nome do backup, e os caminhos de sucesso/erro do CLI.
"""

from __future__ import annotations

import hashlib
import json
import runpy
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.curator_cycle as curator_cycle
from scripts.skill_curator import CuratorFinding, CuratorReport

DAY = 86400.0
T0 = 1_700_000_000  # epoch fixo, inteiro, para idade exata


def _runtime(tmp_path: Path, *, name: str = "runtime", with_intake: bool = True) -> Path:
    runtime = tmp_path / name
    runtime.mkdir(parents=True, exist_ok=True)
    if with_intake:
        (runtime / "skills" / "discovery" / "intake").mkdir(parents=True, exist_ok=True)
    return runtime


def _skill(
    runtime: Path,
    name: str,
    *,
    mtime: int | None = None,
    body: str = "",
) -> Path:
    skill_md = runtime / "skills" / "discovery" / "intake" / name / "SKILL.md"
    skill_md.parent.mkdir(parents=True, exist_ok=True)
    skill_md.write_text(
        f"---\nname: {name}\ndescription: Resumo de verificacao.\n---\n{body}",
        encoding="utf-8",
    )
    if mtime is not None:
        import os

        os.utime(skill_md, (mtime, mtime))
    return skill_md


def _report(approved: bool, findings: tuple[CuratorFinding, ...] = ()) -> CuratorReport:
    return CuratorReport(
        skill_name="fake",
        approved=approved,
        findings=findings,
        gates_run=("security-dangerous-patterns", "security-secrets",
                   "license-compliance", "anti-fabrication", "hermes-linter"),
    )


def _fake_review(reports_by_name: dict[str, CuratorReport], calls: list[Path]):
    def _review(skill_dir: Path, **kwargs):
        calls.append(skill_dir)
        return reports_by_name[skill_dir.name]
    return _review


# ── Intake vazio ────────────────────────────────────────────────────────────

def test_empty_intake_returns_empty_items_and_writes_backup(tmp_path: Path):
    # Sem o diretorio intake inteiro e com intake existente porem vazio:
    # items vazios, contadores zerados e backup ainda gravado.
    cases = (
        _runtime(tmp_path, name="rt-missing", with_intake=False),
        _runtime(tmp_path, name="rt-empty"),
    )
    for runtime in cases:
        backup_dir = tmp_path / f"backups-{runtime.name}"
        result = curator_cycle.run_cycle(runtime, backup_dir)
        assert result["items"] == []
        assert result["counts"] == {
            "promote-candidate": 0, "fix-findings": 0, "expire-candidate": 0,
        }
        backup = Path(result["backup_path"])
        assert backup.exists()
        assert backup.parent == backup_dir
        assert json.loads(backup.read_text(encoding="utf-8")) == {}


# ── Conteudo do backup ──────────────────────────────────────────────────────

def test_backup_snapshot_content_is_correct(tmp_path: Path):
    runtime = _runtime(tmp_path)
    a = _skill(runtime, "alpha", mtime=T0, body="Conteudo A.")
    b = _skill(runtime, "beta", mtime=T0 + 3600, body="Conteudo B.")
    # Ruidos que NAO sao skills: arquivo solto e pasta sem SKILL.md.
    (runtime / "skills" / "discovery" / "intake" / "README.md").write_text(
        "noop", encoding="utf-8")
    (runtime / "skills" / "discovery" / "intake" / "sem-skill").mkdir(parents=True)

    result = curator_cycle.run_cycle(runtime, tmp_path / "backups", now=float(T0))

    data = json.loads(Path(result["backup_path"]).read_text(encoding="utf-8"))
    assert set(data) == {"alpha", "beta"}
    for skill_md in (a, b):
        entry = data[skill_md.parent.name]
        assert entry["path"] == "skills/discovery/intake/" \
            f"{skill_md.parent.name}/SKILL.md"
        assert entry["sha256"] == hashlib.sha256(skill_md.read_bytes()).hexdigest()
        parsed = datetime.fromisoformat(entry["mtime"])
        assert parsed.timestamp() == pytest.approx(skill_md.stat().st_mtime)
        assert parsed.tzinfo is not None


def test_backup_filename_collision_gets_suffix(tmp_path: Path):
    runtime = _runtime(tmp_path)
    _skill(runtime, "alpha", mtime=T0)
    backup_dir = tmp_path / "backups"
    first = curator_cycle.run_cycle(runtime, backup_dir, now=float(T0))
    second = curator_cycle.run_cycle(runtime, backup_dir, now=float(T0))
    assert first["backup_path"] != second["backup_path"]
    assert Path(second["backup_path"]).name == (
        Path(first["backup_path"]).stem + "-1.json")
    # Mesmo estado -> mesmo conteudo.
    first_data = json.loads(Path(first["backup_path"]).read_text(encoding="utf-8"))
    second_data = json.loads(Path(second["backup_path"]).read_text(encoding="utf-8"))
    assert first_data == second_data


# ── Recomendacoes ───────────────────────────────────────────────────────────

def test_approved_skill_is_promote_candidate(tmp_path: Path, monkeypatch):
    runtime = _runtime(tmp_path)
    _skill(runtime, "good-one", mtime=T0)
    calls: list[Path] = []
    monkeypatch.setattr(
        curator_cycle.skill_curator, "review_intake_skill",
        _fake_review({"good-one": _report(approved=True)}, calls))

    result = curator_cycle.run_cycle(runtime, tmp_path / "backups", now=float(T0 + DAY))

    # O ciclo repassa o DIRETORIO da skill (skill_dir), nao o SKILL.md.
    assert calls == [runtime / "skills" / "discovery" / "intake" / "good-one"]
    item = result["items"][0]
    assert item["name"] == "good-one"
    assert item["approved"] is True
    assert item["age_days"] == 1.0
    assert item["recommendation"] == "promote-candidate"
    assert item["blockers"] == []
    assert result["counts"] == {
        "promote-candidate": 1, "fix-findings": 0, "expire-candidate": 0,
    }


def test_blocker_findings_feed_fix_findings(tmp_path: Path, monkeypatch):
    runtime = _runtime(tmp_path)
    _skill(runtime, "blocked", mtime=T0)
    report = _report(approved=False, findings=(
        CuratorFinding(gate="security-secrets", severity="BLOCKER",
                       message="Possivel segredo hardcoded: possible API key"),
        CuratorFinding(gate="license-compliance", severity="WARNING",
                       message="Licenca nao declarada."),
    ))
    monkeypatch.setattr(
        curator_cycle.skill_curator, "review_intake_skill",
        _fake_review({"blocked": report}, []))

    result = curator_cycle.run_cycle(runtime, tmp_path / "backups", now=float(T0 + DAY))

    item = result["items"][0]
    assert item["approved"] is False
    assert item["recommendation"] == "fix-findings"
    # Somente mensagens BLOCKER viram blockers.
    assert item["blockers"] == [
        "Possivel segredo hardcoded: possible API key"]
    assert result["counts"]["fix-findings"] == 1


def test_retention_boundary_exactly_seven_days_stays_fix(tmp_path: Path, monkeypatch):
    runtime = _runtime(tmp_path)
    _skill(runtime, "exactly-seven", mtime=T0)
    # Uma hora mais velha: com o mesmo now, passa de 7 dias estritamente.
    _skill(runtime, "seven-plus-hour", mtime=T0 - 3600)
    monkeypatch.setattr(
        curator_cycle.skill_curator, "review_intake_skill",
        _fake_review(
            {"exactly-seven": _report(False), "seven-plus-hour": _report(False)},
            []))

    result = curator_cycle.run_cycle(
        runtime, tmp_path / "backups", now=float(T0 + 7 * DAY))

    by_name = {i["name"]: i for i in result["items"]}
    # == 7 dias NAO expira (regra: estritamente maior que 7).
    assert by_name["exactly-seven"]["age_days"] == pytest.approx(7.0)
    assert by_name["exactly-seven"]["recommendation"] == "fix-findings"
    assert by_name["seven-plus-hour"]["recommendation"] == "expire-candidate"
    assert result["counts"] == {
        "promote-candidate": 0, "fix-findings": 1, "expire-candidate": 1,
    }


def test_now_is_injectable_and_drives_age(tmp_path: Path, monkeypatch):
    runtime = _runtime(tmp_path)
    _skill(runtime, "aged", mtime=T0)
    monkeypatch.setattr(
        curator_cycle.skill_curator, "review_intake_skill",
        _fake_review({"aged": _report(False)}, []))

    result = curator_cycle.run_cycle(
        runtime, tmp_path / "backups", now=float(T0 + 8 * DAY))

    assert result["items"][0]["age_days"] == 8.0
    assert result["items"][0]["recommendation"] == "expire-candidate"


# ── Gates reais (sem mock): prova da assinatura real ────────────────────────

def test_real_gates_secret_makes_blocker(tmp_path: Path):
    runtime = _runtime(tmp_path)
    _skill(runtime, "leaky", mtime=T0,
           body='config com api_key = "supersecretvalue123" no texto.')

    result = curator_cycle.run_cycle(runtime, tmp_path / "backups", now=float(T0))

    item = result["items"][0]
    assert item["approved"] is False
    assert item["recommendation"] == "fix-findings"
    assert any("segredo" in b.lower() for b in item["blockers"])


def test_real_gates_clean_frontmatter_skill_approves(tmp_path: Path):
    runtime = _runtime(tmp_path)
    _skill(runtime, "clean-demo", mtime=T0,
           body="Instrucoes de resumo de evidencias, sem comandos.")

    result = curator_cycle.run_cycle(runtime, tmp_path / "backups", now=float(T0))

    item = result["items"][0]
    assert item["approved"] is True
    assert item["blockers"] == []
    assert item["recommendation"] == "promote-candidate"


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_cli_success_prints_summary(tmp_path: Path, monkeypatch, capsys):
    runtime = _runtime(tmp_path)
    _skill(runtime, "good-one", mtime=T0)
    monkeypatch.setattr(
        curator_cycle.skill_curator, "review_intake_skill",
        _fake_review({"good-one": _report(True)}, []))
    backup_dir = tmp_path / "cli-backups"

    code = curator_cycle.main(
        ["--root", str(runtime), "--backup-dir", str(backup_dir)])

    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith("CURATOR_OK promote=1 fix=0 expire=0 backup=")
    printed_path = out.split("backup=", 1)[1].strip()
    assert Path(printed_path).exists()
    assert backup_dir in Path(printed_path).parents


def test_cli_oserror_prints_error_and_exits_1(tmp_path: Path, capsys):
    runtime = _runtime(tmp_path)
    # backup-dir aponta para um ARQUIVO regular: mkdir levanta OSError.
    blocker_file = tmp_path / "not-a-dir"
    blocker_file.write_text("sou um arquivo", encoding="utf-8")

    code = curator_cycle.main(
        ["--root", str(runtime), "--backup-dir", str(blocker_file)])

    assert code == 1
    assert capsys.readouterr().out.startswith("CURATOR_ERROR")


def test_module_main_guard_via_runpy(tmp_path: Path, monkeypatch, capsys):
    runtime = _runtime(tmp_path)
    _skill(runtime, "good-one", mtime=T0)
    monkeypatch.setattr(
        curator_cycle.skill_curator, "review_intake_skill",
        _fake_review({"good-one": _report(True)}, []))
    monkeypatch.setattr(sys, "argv", [
        "curator_cycle.py",
        "--root", str(runtime),
        "--backup-dir", str(tmp_path / "runpy-backups"),
    ])
    # Remove ROOT de sys.path para exercitar o bootstrap de import direto
    # (o modulo re-insere ROOT antes de ``from scripts import skill_curator``).
    monkeypatch.setattr(
        sys, "path", [p for p in sys.path if p not in (str(ROOT), "")])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(ROOT / "scripts" / "curator_cycle.py"),
                       run_name="__main__")

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.startswith("CURATOR_OK")
