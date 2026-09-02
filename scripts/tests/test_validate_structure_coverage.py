from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest
import yaml

import scripts.validate_structure as subject


def write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_load_yaml_success_and_failure(tmp_path):
    errors = []
    good = write(tmp_path / "good.yaml", "answer: 42\n")
    assert subject._load_yaml(good, errors) == {"answer": 42}
    assert subject._load_yaml(tmp_path / "absent.yaml", errors) is None
    assert errors and errors[0].startswith("invalid yaml ")


def test_required_files_reports_only_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "REQUIRED_FILES", ["present", "absent"])
    write(tmp_path / "present")
    errors = []
    subject._validate_required_files(tmp_path, errors)
    assert errors == ["missing absent"]


def test_agents_all_validation_branches(tmp_path, monkeypatch):
    errors = []
    write(
        tmp_path / "config/agent-registry.yaml",
        yaml.safe_dump({"agents": [
            {"id": "same", "path": "agents/one", "manifest": "manifests/one.yaml"},
            {"id": "same", "path": "agents/two", "manifest": "manifests/two.yaml"},
        ]}),
    )
    write(tmp_path / "agents/one/PROMPT.md")
    write(tmp_path / "manifests/two.yaml")
    agents = subject._validate_agents(tmp_path, errors)
    assert agents[0]["id"] == "same"
    assert "expected 36 agents, found 2" in errors
    assert "duplicate agent ids" in errors
    assert "missing manifest for same" in errors
    assert "missing prompt for same" in errors

    monkeypatch.setattr(subject, "_load_yaml", lambda *_: {"agents": [
        {"id": str(i), "path": "ok", "manifest": "ok/PROMPT.md"} for i in range(36)
    ]})
    write(tmp_path / "ok/PROMPT.md")
    errors = []
    assert len(subject._validate_agents(tmp_path, errors)) == 36
    assert errors == []


def test_schemas_complete_incomplete_and_invalid(tmp_path):
    write(tmp_path / "contracts/good.schema.json", '{"$schema":"x","title":"T"}')
    write(tmp_path / "contracts/incomplete.schema.json", '{"title":"T"}')
    write(tmp_path / "contracts/bad.schema.json", "{")
    errors = []
    subject._validate_schemas(tmp_path, errors)
    assert any(e == "schema incomplete incomplete.schema.json" for e in errors)
    assert any(e.startswith("invalid schema bad.schema.json:") for e in errors)


def test_catalog_entries_supports_mapping_list_and_scalar_values():
    assert subject._catalog_entries({"catalog": {"a": {"x": 1}, "b": None}}) == {
        "a": {"path": "a", "x": 1}, "b": {"path": "b"}
    }
    assert subject._catalog_entries({"catalog": [{"path": "c"}]}) == {"c": {"path": "c"}}


def test_active_skill_paths_filters_non_active_content(tmp_path):
    write(tmp_path / "skills/active/SKILL.md")
    write(tmp_path / "skills/discovery/intake/no/SKILL.md")
    write(tmp_path / "skills/discovery/quarantine/no/SKILL.md")
    write(tmp_path / "skills/vendor/no/SKILL.md")
    write(tmp_path / "integrations/engine.py")
    write(tmp_path / "integrations/__init__.py")
    assert subject._active_skill_paths(tmp_path) == {"skills/active", "integrations/engine.py"}


def test_validate_skills_exercises_manifest_catalog_and_path_errors(tmp_path):
    write(tmp_path / "config/skills-catalog.yaml", yaml.safe_dump({"catalog": [
        {"path": "skills/assigned", "skill_id": "wrong-id"},
        {"path": "skills/stale", "skill_id": "stale"},
        {"path": "integrations/engine.py", "assigned_to": ["agent"]},
    ]}))
    write(tmp_path / "skills/assigned/SKILL.md", "---\nname: right-id\n---\n")
    write(tmp_path / "skills/uncatalogued/SKILL.md")
    write(tmp_path / "skills/active/SKILL.md")
    write(tmp_path / "integrations/engine.py")
    manifest = write(tmp_path / "manifest.yaml", yaml.safe_dump({
        "native": [{"path": "skills/missing"}],
        "assigned": [
            {"path": "skills/assigned"},
            {"path": "skills/uncatalogued"},
            {"path": "integrations/engine.py"},
        ],
    }))
    errors = []
    count = subject._validate_skills(
        tmp_path,
        [{"id": "agent", "manifest": manifest.relative_to(tmp_path).as_posix()}, {"id": "skip", "manifest": "absent"}],
        errors,
    )
    assert count == 4
    assert "missing skill path skills/missing" in errors
    assert "uncatalogued skill skills/uncatalogued in agent" in errors
    assert "catalog assignment mismatch skills/assigned -> agent" in errors
    assert "active skill not catalogued skills/active" in errors
    assert "catalogued skill missing on disk skills/stale" in errors


def test_validate_skills_handles_empty_catalog(tmp_path):
    write(tmp_path / "config/skills-catalog.yaml", "catalog: {}\n")
    write(tmp_path / "skills/plain/SKILL.md", "no frontmatter")
    write(tmp_path / "manifest.yaml", "assigned: []\n")
    errors = []
    assert subject._validate_skills(tmp_path, [{"manifest": "manifest.yaml"}], errors) == 1
    assert "active skill not catalogued skills/plain" in errors


def test_legacy_references_detected_and_catalog_skipped(tmp_path):
    write(tmp_path / "agents/a/PROMPT.md", "skills/legacy/thing")
    write(tmp_path / "config/a.yaml", "policy: sem aprovação")
    write(tmp_path / "config/skills-catalog.yaml", "legacy/ allowed here")
    errors = []
    subject._validate_legacy_references(tmp_path, errors)
    assert "legacy or permissive reference in agents/a/PROMPT.md" in errors
    assert "legacy or permissive reference in config/a.yaml" in errors
    assert len(errors) == 2

    write(tmp_path / "agents/a/PROMPT.md", "clean")
    write(tmp_path / "config/a.yaml", "clean")
    errors = []
    subject._validate_legacy_references(tmp_path, errors)
    assert errors == []


def test_prompt_copy_model_ignores_missing_and_reports_match(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "PROMPT_FILES", ("missing.md", "present.md"))
    write(tmp_path / "present.md", "Please COPY THE FULL RUNTIME now")
    errors = []

    subject._validate_prompt_copy_model(tmp_path, errors)

    assert errors == ["legacy copy-model reference in present.md: copy the full runtime"]


def test_prompt_budgets_ignore_missing_and_report_oversized(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "PROMPT_CHARACTER_BUDGETS", {"missing.md": 1, "present.md": 3})
    write(tmp_path / "present.md", "four")
    errors = []

    subject._validate_prompt_budgets(tmp_path, errors)

    assert errors == ["present.md exceeds 3 character budget: 4"]


def test_main_success_and_failure(monkeypatch, capsys):
    monkeypatch.setattr(subject, "_validate_required_files", lambda root, errors: None)
    monkeypatch.setattr(subject, "_validate_agents", lambda root, errors: [])
    monkeypatch.setattr(subject, "_validate_schemas", lambda root, errors: None)
    monkeypatch.setattr(subject, "_validate_skills", lambda root, agents, errors: 7)
    monkeypatch.setattr(subject, "_validate_legacy_references", lambda root, errors: None)
    assert subject.main() == 0
    assert "VALID structure agents=0 active_skills=7 schemas=" in capsys.readouterr().out

    monkeypatch.setattr(subject, "_validate_required_files", lambda root, errors: errors.extend(["first", "second"]))
    assert subject.main() == 1
    captured = capsys.readouterr()
    assert "ERROR: first" in captured.err and "ERROR: second" in captured.err


def test_script_entrypoint(monkeypatch):
    monkeypatch.setattr(Path, "resolve", lambda self: Path("/tmp/root/scripts/validate_structure.py"))
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("scripts.validate_structure", run_name="__main__")
    assert exc.value.code == 1
