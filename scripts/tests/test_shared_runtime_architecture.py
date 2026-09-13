import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PROMPTS = ("AGENTS.md", "CLAUDE.md", "CODEX.md", "GEMINI.md")
FORBIDDEN = (
    "bootstrap a copy",
    "copy source",
    "~1,000 files",
    "~17 mb",
    "the one inside the project",
    "copies `agents/",
    "copy it into the project",
    ".agents_squad/scripts/",
    "the copy is a fork",
    "global source",
)


@pytest.mark.parametrize("prompt_name", PROMPTS)
def test_provider_prompt_requires_shared_runtime_without_copy(prompt_name):
    text = (ROOT / prompt_name).read_text(encoding="utf-8")
    lowered = text.lower()

    assert "squad_runtime" in lowered
    assert "project_root" in lowered
    assert "project_id" in lowered
    assert "work/<project_id>" in lowered or "work/<work-id>" in lowered
    assert "banco/squad.db" in lowered
    assert all(phrase not in lowered for phrase in FORBIDDEN)


def test_bootstrap_creates_only_minimal_pointer(tmp_path):
    from bootstrap_project_squad import bootstrap

    project = tmp_path / "consumer"
    project.mkdir()
    destination = bootstrap(project, ROOT, project_name="consumer")

    files = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert files == {"PROVENANCE.yaml", "config/project.yaml"}
    assert not any((destination / name).exists() for name in ("agents", "skills", "contracts", "scripts", "work", "banco"))

    config = yaml.safe_load((destination / "config/project.yaml").read_text(encoding="utf-8"))
    assert config["project_id"] == "consumer"
    assert config["runtime"] == ROOT.as_posix()
    assert config["work_dir"] == (ROOT / "work/consumer").as_posix()
    assert config["db_path"] == (ROOT / "banco/squad.db").as_posix()


def test_bootstrap_refuses_legacy_local_state(tmp_path):
    from bootstrap_project_squad import bootstrap

    project = tmp_path / "consumer"
    (project / ".agents_squad/work").mkdir(parents=True)

    with pytest.raises(SystemExit, match="dados locais legados"):
        bootstrap(project, ROOT, project_name="consumer", force=True)


def test_bootstrap_writes_minimal_agents_md_and_never_overwrites_personal(tmp_path):
    from bootstrap_project_squad import bootstrap

    project = tmp_path / "consumer"
    project.mkdir()
    bootstrap(project, ROOT, project_name="consumer")

    agents_md = project / "AGENTS.md"
    text = agents_md.read_text(encoding="utf-8")
    assert "Agents Squad" in text
    assert ROOT.as_posix() in text
    assert "delivery-orchestrator" in text

    personal = tmp_path / "personal"
    personal.mkdir()
    (personal / "AGENTS.md").write_text("# my own rules\n", encoding="utf-8")
    bootstrap(personal, ROOT, project_name="personal")
    assert (personal / "AGENTS.md").read_text(encoding="utf-8") == "# my own rules\n"
