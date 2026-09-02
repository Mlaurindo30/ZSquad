from __future__ import annotations

import importlib.util
import runpy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("zcode_subagents", ROOT / "integrations/zcode_subagents.py")
SUBJECT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = SUBJECT
SPEC.loader.exec_module(SUBJECT)

CLI_SPEC = importlib.util.spec_from_file_location("install_zcode_subagents", ROOT / "scripts/install_zcode_subagents.py")
CLI = importlib.util.module_from_spec(CLI_SPEC)
assert CLI_SPEC.loader is not None
CLI_SPEC.loader.exec_module(CLI)

EXPECTED_IDS = {
    "delivery-orchestrator",
    "requirements-analyst",
    "solution-architect",
    "software-engineer",
    "qa-engineer",
}


def test_loads_exact_canonical_pilot():
    profiles = SUBJECT.load_pilot_profiles(ROOT)

    assert {profile.agent_id for profile in profiles} == EXPECTED_IDS
    assert "implementation-engineer" not in {profile.agent_id for profile in profiles}
    assert all((ROOT / profile.prompt_path).is_file() for profile in profiles)
    assert all((ROOT / profile.manifest_path).is_file() for profile in profiles)
    assert all((ROOT / profile.native_skill_path).is_file() for profile in profiles)


def test_render_matches_zcode_native_frontmatter_contract():
    profile = SUBJECT.load_pilot_profiles(ROOT)[0]
    rendered = SUBJECT.render_profile(profile, ROOT)
    frontmatter = yaml.safe_load(rendered.split("---", 2)[1])

    assert frontmatter["name"] == profile.agent_id
    assert frontmatter["injectAgentsMd"] is True
    assert frontmatter["memory"] == "project"
    assert frontmatter["tools"] == ["*"]
    assert frontmatter["x-managed-by"] == SUBJECT.MANAGED_BY
    assert "Assume this identity before acting" in rendered
    assert profile.prompt_path in rendered
    assert profile.manifest_path in rendered
    assert profile.native_skill_path in rendered


def test_sync_is_idempotent_and_check_detects_drift(tmp_path):
    destination = tmp_path / "agents"

    first = SUBJECT.sync_profiles(ROOT, destination)
    second = SUBJECT.sync_profiles(ROOT, destination)

    assert set(first["changed"]) == {f"{agent_id}.md" for agent_id in EXPECTED_IDS}
    assert len(second["unchanged"]) == 5
    assert SUBJECT.sync_profiles(ROOT, destination, check=True)["changed"] == []
    target = destination / "qa-engineer.md"
    target.write_text(target.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8")
    with pytest.raises(SUBJECT.ZCodeProfileError, match="out of sync"):
        SUBJECT.sync_profiles(ROOT, destination, check=True)


def test_sync_refuses_unmanaged_conflict(tmp_path):
    destination = tmp_path / "agents"
    destination.mkdir()
    (destination / "qa-engineer.md").write_text("personal profile", encoding="utf-8")

    with pytest.raises(SUBJECT.ZCodeProfileError, match="unmanaged profile conflict"):
        SUBJECT.sync_profiles(ROOT, destination)


@pytest.mark.parametrize(
    "content",
    [
        "personal profile\nexample: x-managed-by: agents-squad-zcode-adapter\n",
        "---\nx-managed-by: agents-squad-zcode-adapter\nmissing closing delimiter\n",
        "---\nx-managed-by: [\n---\npersonal profile\n",
        "---\n- x-managed-by\n- agents-squad-zcode-adapter\n---\npersonal profile\n",
    ],
)
def test_body_marker_does_not_claim_an_unmanaged_profile(tmp_path, content):
    destination = tmp_path / "agents"
    destination.mkdir()
    target = destination / "qa-engineer.md"
    target.write_text(content, encoding="utf-8")

    with pytest.raises(SUBJECT.ZCodeProfileError, match="unmanaged profile conflict"):
        SUBJECT.sync_profiles(ROOT, destination)

    assert SUBJECT.remove_managed_profiles(destination) == []
    assert target.read_text(encoding="utf-8") == content


def test_sync_preflights_all_conflicts_before_writing(tmp_path):
    destination = tmp_path / "agents"
    destination.mkdir()
    conflict = destination / "qa-engineer.md"
    conflict.write_text("personal profile", encoding="utf-8")

    with pytest.raises(SUBJECT.ZCodeProfileError, match="unmanaged profile conflict"):
        SUBJECT.sync_profiles(ROOT, destination)

    assert list(destination.iterdir()) == [conflict]


def test_sync_rolls_back_when_a_later_write_fails(tmp_path, monkeypatch):
    destination = tmp_path / "agents"
    replace = SUBJECT.os.replace
    calls = 0

    def fail_second_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        replace(source, target)

    monkeypatch.setattr(SUBJECT.os, "replace", fail_second_replace)

    with pytest.raises(SUBJECT.ZCodeProfileError, match="profile sync failed: disk full"):
        SUBJECT.sync_profiles(ROOT, destination)

    assert not list(destination.glob("*.md"))


def test_sync_reports_rollback_failures_and_attempts_all_restorations(tmp_path, monkeypatch):
    destination = tmp_path / "agents"
    replace = SUBJECT.os.replace
    replace_calls = 0
    unlink_calls = 0

    def fail_second_replace(source, target):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("disk full")
        replace(source, target)

    def fail_first_unlink(path, *, missing_ok=False):
        nonlocal unlink_calls
        if path.suffix == ".md":
            unlink_calls += 1
            if unlink_calls == 1:
                raise OSError("rollback denied")
        return original_unlink(path, missing_ok=missing_ok)

    original_unlink = SUBJECT.Path.unlink
    monkeypatch.setattr(SUBJECT.os, "replace", fail_second_replace)
    monkeypatch.setattr(SUBJECT.Path, "unlink", fail_first_unlink)

    with pytest.raises(SUBJECT.ZCodeProfileError, match="disk full.*rollback denied"):
        SUBJECT.sync_profiles(ROOT, destination)

    assert unlink_calls >= 2


def test_sync_restores_managed_content_when_a_later_write_fails(tmp_path, monkeypatch):
    destination = tmp_path / "agents"
    SUBJECT.sync_profiles(ROOT, destination)
    targets = sorted(destination.glob("*.md"))
    original = targets[0].read_text(encoding="utf-8")
    targets[0].write_text(original + "\ndrift\n", encoding="utf-8")
    targets[1].write_text(targets[1].read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8")
    replace = SUBJECT.os.replace
    calls = 0

    def fail_second_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        replace(source, target)

    monkeypatch.setattr(SUBJECT.os, "replace", fail_second_replace)

    with pytest.raises(SUBJECT.ZCodeProfileError, match="profile sync failed: disk full"):
        SUBJECT.sync_profiles(ROOT, destination)

    assert targets[0].read_text(encoding="utf-8") == original + "\ndrift\n"


def test_check_rejects_stale_managed_profiles(tmp_path):
    destination = tmp_path / "agents"
    SUBJECT.sync_profiles(ROOT, destination)
    stale = destination / "retired-persona.md"
    stale.write_text(
        "---\nname: retired-persona\nx-managed-by: agents-squad-zcode-adapter\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(SUBJECT.ZCodeProfileError, match="stale managed profiles: retired-persona.md"):
        SUBJECT.sync_profiles(ROOT, destination, check=True)

    result = SUBJECT.sync_profiles(ROOT, destination)
    assert result["stale"] == ["retired-persona.md"]
    assert stale.is_file()


def test_remove_only_managed_profiles(tmp_path):
    destination = tmp_path / "agents"
    assert SUBJECT.remove_managed_profiles(destination) == []
    SUBJECT.sync_profiles(ROOT, destination)
    personal = destination / "personal.md"
    personal.write_text("not managed", encoding="utf-8")

    assert len(SUBJECT.remove_managed_profiles(destination, dry_run=True)) == 5
    assert len(SUBJECT.remove_managed_profiles(destination)) == 5
    assert personal.is_file()


def test_validation_rejects_unknown_duplicate_and_invalid_options(tmp_path):
    config = yaml.safe_load((ROOT / "config/zcode-pilot-agents.yaml").read_text(encoding="utf-8"))
    profile = config["profiles"][0]
    cases = [
        ({"profiles": []}, "invalid ZCode pilot configuration"),
        ({"managed_by": "other", "profiles": [profile]}, "invalid ZCode pilot configuration"),
        ({"managed_by": SUBJECT.MANAGED_BY, "profiles": [{}]}, "each profile requires an id"),
        ({"managed_by": SUBJECT.MANAGED_BY, "profiles": [dict(profile, id="unknown-agent")]}, "unknown canonical persona"),
        ({"managed_by": SUBJECT.MANAGED_BY, "profiles": [profile, profile]}, "duplicate profile id"),
        ({"managed_by": SUBJECT.MANAGED_BY, "profiles": [dict(profile, color="invisible")]}, "invalid ZCode options"),
        ({"managed_by": SUBJECT.MANAGED_BY, "profiles": [dict(profile, max_turns=0)]}, "invalid max_turns"),
    ]

    for index, (value, message) in enumerate(cases):
        path = tmp_path / f"case-{index}.yaml"
        path.write_text(yaml.safe_dump(value), encoding="utf-8")
        with pytest.raises(SUBJECT.ZCodeProfileError, match=message):
            SUBJECT.load_pilot_profiles(ROOT, path)


def test_validation_rejects_malformed_yaml_paths_and_missing_native_skill(tmp_path, monkeypatch):
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("profiles: [", encoding="utf-8")
    with pytest.raises(SUBJECT.ZCodeProfileError, match="invalid YAML"):
        SUBJECT.load_pilot_profiles(ROOT, malformed)
    with pytest.raises(SUBJECT.ZCodeProfileError, match="invalid YAML"):
        SUBJECT.load_pilot_profiles(ROOT, tmp_path / "missing.yaml")

    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("scalar", encoding="utf-8")
    with pytest.raises(SUBJECT.ZCodeProfileError, match="YAML must contain a mapping"):
        SUBJECT.load_pilot_profiles(ROOT, scalar)

    with pytest.raises(SUBJECT.ZCodeProfileError, match="path escapes runtime"):
        SUBJECT._relative_runtime_path(ROOT, "../outside")
    with pytest.raises(SUBJECT.ZCodeProfileError, match="missing canonical path"):
        SUBJECT._relative_runtime_path(ROOT, "missing")

    read_yaml = SUBJECT._read_yaml

    def read_without_native(path):
        value = read_yaml(path)
        if path.name == "manifest.yaml":
            return {**value, "native": []}
        return value

    monkeypatch.setattr(SUBJECT, "_read_yaml", read_without_native)
    with pytest.raises(SUBJECT.ZCodeProfileError, match="missing native skill"):
        SUBJECT.load_pilot_profiles(ROOT)


def test_cli_resolves_scopes_and_supports_check_and_remove(tmp_path, capsys):
    user_root = tmp_path / "user"
    assert CLI.resolve_destination("user", ROOT, user_root) == user_root.resolve() / "agents/agents-squad"
    assert CLI.resolve_destination("project", tmp_path) == tmp_path.resolve() / ".zcode/agents/agents-squad"

    assert CLI.main(["--user-root", str(user_root)]) == 0
    assert CLI.main(["--user-root", str(user_root), "--check"]) == 0
    assert CLI.main(["--user-root", str(user_root), "--remove-managed"]) == 0
    assert "delivery-orchestrator.md" in capsys.readouterr().out


def test_cli_reports_adapter_errors_and_entrypoint_exit(tmp_path, capsys, monkeypatch):
    destination = tmp_path / "agents"
    destination.mkdir()
    (destination / "qa-engineer.md").write_text("personal profile", encoding="utf-8")
    monkeypatch.setattr(CLI, "resolve_destination", lambda *_args, **_kwargs: destination)

    assert CLI.main([]) == 1
    assert "ZCODE_SUBAGENTS_ERROR" in capsys.readouterr().err

    monkeypatch.setattr(sys, "argv", [str(ROOT / "scripts/install_zcode_subagents.py"), "--dry-run"])
    with pytest.raises(SystemExit, match="0"):
        runpy.run_path(str(ROOT / "scripts/install_zcode_subagents.py"), run_name="__main__")


def test_dry_run_does_not_create_destination(tmp_path):
    destination = tmp_path / "missing"

    result = SUBJECT.sync_profiles(ROOT, destination, dry_run=True)

    assert len(result["changed"]) == 5
    assert not destination.exists()
