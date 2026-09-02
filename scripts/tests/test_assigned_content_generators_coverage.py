from __future__ import annotations

import importlib.util
import runpy
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_create_candidate_intakes(tmp_path, capsys):
    module = load("assigned_candidate_intakes", "create_candidate_intakes.py")
    module.main(tmp_path)
    assert "count=" + str(len(module.CANDIDATES)) in capsys.readouterr().out
    first = module.CANDIDATES[0]
    assert first["description"] in (tmp_path / "skills/discovery/intake" / first["name"] / "SKILL.md").read_text(encoding="utf-8")
    assert first["repo"] in (tmp_path / "skills/discovery/reviews" / f"SKILL-{first['id']}.md").read_text(encoding="utf-8")


def test_normalize_all_paths(tmp_path, monkeypatch, capsys):
    module = load("assigned_frontmatter", "normalize-skill-frontmatter.py")
    skill = tmp_path / "skills" / "active" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\ndescription: demo\nname: sample\nlegacy: yes\nmetadata: old\n---\nBody\n", encoding="utf-8")
    intake = tmp_path / "skills/discovery/intake/x/SKILL.md"
    quarantine = tmp_path / "skills/discovery/quarantine/x/SKILL.md"
    for excluded in (intake, quarantine):
        excluded.parent.mkdir(parents=True, exist_ok=True)
        excluded.write_text("not frontmatter", encoding="utf-8")

    assert module.active_skills(tmp_path) == [skill]
    assert module.normalize(skill, check=True) is True
    assert "legacy: yes" in skill.read_text(encoding="utf-8")
    assert module.normalize(skill, check=False) is True
    assert module.normalize(skill, check=False) is False

    scalar_metadata = tmp_path / "scalar.md"
    scalar_metadata.write_text("---\nname: x\nmetadata: value\n---", encoding="utf-8")
    assert module.normalize(scalar_metadata, check=False)
    assert "legacy_metadata: value" in scalar_metadata.read_text(encoding="utf-8")

    for text in ("Body", "---\n- item\n---"):
        bad = tmp_path / ("bad" + str(len(text)) + ".md")
        bad.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            module.normalize(bad, check=False)

    monkeypatch.setattr(sys, "argv", ["normalize", "--root", str(tmp_path), "--check"])
    skill.write_text("---\ndescription: changed\nname: sample\n---\n", encoding="utf-8")
    assert module.main() == 1
    assert "FRONTMATTER_NEEDS_NORMALIZATION" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["normalize", "--root", str(tmp_path)])
    assert module.main() == 0
    assert "FRONTMATTER_OK" in capsys.readouterr().out


def test_update_prompts_all_branches(tmp_path, capsys):
    module = load("assigned_prompt_updater", "update_prompts_heuristics.py")
    agents = []
    role_ids = iter(module.ROLE_DATA)
    cases = [
        ("missing", None),
        ("done", "## Heurísticas do papel\n## Quando carregar qual skill"),
        ("insert", "Before\n## Definição de saída\nAfter"),
        ("append", "Only body"),
    ]
    for folder, text in cases:
        agent_id = next(role_ids)
        agents.append({"id": agent_id, "path": f"agents/{folder}"})
        if text is not None:
            path = tmp_path / "agents" / folder / "PROMPT.md"
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "agent-registry.yaml").write_text(yaml.safe_dump({"agents": agents}), encoding="utf-8")

    module.main(tmp_path)
    out = capsys.readouterr().out
    assert "PROMPTS_UPDATED_ALL_30" in out
    inserted = (tmp_path / "agents/insert/PROMPT.md").read_text(encoding="utf-8")
    appended = (tmp_path / "agents/append/PROMPT.md").read_text(encoding="utf-8")
    assert inserted.index("## Heurísticas do papel") < inserted.index("## Definição de saída")
    assert appended.endswith("\n") and "## Quando carregar qual skill" in appended


def test_build_specialized_agents_generation(tmp_path, monkeypatch, capsys):
    module = load("assigned_build_agents", "build_specialized_agents.py")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "config").mkdir()
    skills = {
        "skills/named": "---\nname: agent-memory\n---\n",
        "skills/data-tool": "no declared name",
        "skills/security-tool": "no declared name",
        "skills/ui-design-tool": "no declared name",
        "skills/ai-tool": "no declared name",
        "skills/general-tool": "no declared name",
        "skills/discovery/intake/ignored": "name: ignored",
        "skills/discovery/quarantine/ignored": "name: ignored",
    }
    for rel, text in skills.items():
        path = tmp_path / rel / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    unresolved = next(
        skill
        for spec in module.AGENTS_SPEC.values()
        for skill in spec["assigned_skills"]
        if skill not in {"agent-memory", *(path.rsplit("/", 1)[-1] for path in skills)}
    )
    catalog = {
        "catalog": [
            {"path": unresolved, "assigned_to": []},
            {"path": "skills/catalog-only", "assigned_to": ["delivery-orchestrator"]},
            {"path": "skills/existing", "assigned_to": ["delivery-orchestrator"]},
        ]
    }
    (tmp_path / "config/skills-catalog.yaml").write_text(yaml.safe_dump(catalog), encoding="utf-8")
    (tmp_path / "config/agent-registry.yaml").write_text(": invalid: yaml", encoding="utf-8")

    # Exercise formatting branches independently as well as through main.
    sample = next(iter(module.AGENTS_SPEC.values())).copy()
    sample["operates"] = ["Colon: rule", "Plain rule"]
    assert "Colon" in module.generate_prompt_markdown("x", sample)
    assert sample["id"] in module.generate_native_skill_markdown("x", sample)
    assert module.indent_text("a\n\nb", 2) == "  a\n\n  b"

    module.main()
    assert "SUCCESS" in capsys.readouterr().out
    registry = yaml.safe_load((tmp_path / "config/agent-registry.yaml").read_text(encoding="utf-8"))
    assert len(registry["agents"]) == len(module.AGENTS_SPEC)
    assert (tmp_path / registry["agents"][0]["path"] / "PROMPT.md").exists()
    generated_catalog = yaml.safe_load((tmp_path / "config/skills-catalog.yaml").read_text(encoding="utf-8"))
    assert generated_catalog["active_skill_count"] == 3

    # Cover absent catalog/registry inputs and successful existing-registry parsing.
    second = tmp_path / "second"
    (second / "skills").mkdir(parents=True)
    (second / "config").mkdir()
    (second / "config/agent-registry.yaml").write_text("custom: preserved\n", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", second)
    module.main()
    assert yaml.safe_load((second / "config/agent-registry.yaml").read_text(encoding="utf-8"))["custom"] == "preserved"


def test_script_entrypoints(tmp_path, monkeypatch):
    candidate = load("entry_candidate", "create_candidate_intakes.py")
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(SCRIPTS / "create_candidate_intakes.py"), run_name="__main__")

    skill = tmp_path / "skills/active/SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("---\nname: active\ndescription: demo\n---\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["normalize", "--root", str(tmp_path)])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(SCRIPTS / "normalize-skill-frontmatter.py"), run_name="__main__")
    assert exit_info.value.code == 0

    updater_root = tmp_path / "updater"
    (updater_root / "config").mkdir(parents=True)
    (updater_root / "config/agent-registry.yaml").write_text("agents: []\n", encoding="utf-8")
    monkeypatch.chdir(updater_root)
    runpy.run_path(str(SCRIPTS / "update_prompts_heuristics.py"), run_name="__main__")

    build_root = tmp_path / "builder"
    (build_root / "skills").mkdir(parents=True)
    (build_root / "config").mkdir()
    monkeypatch.setenv("AGENT_SQUAD_ROOT", str(build_root))
    runpy.run_path(str(SCRIPTS / "build_specialized_agents.py"), run_name="__main__")
    assert len(candidate.CANDIDATES) > 0
