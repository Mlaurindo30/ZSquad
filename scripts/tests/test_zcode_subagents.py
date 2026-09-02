from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import runpy
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("zcode_subagents", ROOT / "integrations/experimental/zcode_subagents.py")
SUBJECT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = SUBJECT
SPEC.loader.exec_module(SUBJECT)

CLI_SPEC = importlib.util.spec_from_file_location("install_zcode_subagents", ROOT / "scripts/install_zcode_subagents.py")
CLI = importlib.util.module_from_spec(CLI_SPEC)
assert CLI_SPEC.loader is not None
CLI_SPEC.loader.exec_module(CLI)

REGISTRY = yaml.safe_load((ROOT / "config/agent-registry.yaml").read_text(encoding="utf-8"))
EXPECTED_IDS = tuple(agent["id"] for agent in REGISTRY["agents"])
LEGACY_IDS = {
    "delivery-orchestrator",
    "requirements-analyst",
    "solution-architect",
    "software-engineer",
    "qa-engineer",
}


def _config() -> dict:
    return yaml.safe_load((ROOT / "config/zcode-agents.yaml").read_text(encoding="utf-8"))


def _write_config(tmp_path: Path, value: object) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "zcode-agents.yaml"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


def _frontmatter(rendered: str) -> dict:
    return yaml.safe_load(rendered.split("---", 2)[1])


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _authorization(tmp_path: Path, scope: str, destination: Path, operation: str = "sync") -> Path:
    path = tmp_path / f"authorization-{scope}-{operation}.json"
    approvals = {
        "G1": "product-owner",
        "G2": "solution-architect",
        "G3": "delivery-orchestrator",
        "G4": "independent-reviewer",
        "G5": "qa-engineer",
        "human_authorization": "human-authorizer",
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "work_item": "EVOL-ZCODE-ALL-AGENTS-20260824",
                "scope": scope,
                "destination": str(destination),
                "operation": operation,
                "expires_at": "2999-01-01T00:00:00Z",
                "rollback_verified": True,
                "approvals": {
                    name: {"approved": True, "role": role, "authorizer": f"test-{name}"}
                    for name, role in approvals.items()
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_loads_exact_registry_order_and_canonical_sources():
    profiles = SUBJECT.load_profiles(ROOT)

    assert len(profiles) == 41
    assert tuple(profile.agent_id for profile in profiles) == EXPECTED_IDS
    assert SUBJECT.load_pilot_profiles(ROOT) == profiles
    for profile in profiles:
        assert (ROOT / profile.prompt_path).is_file()
        assert (ROOT / profile.manifest_path).is_file()
        assert (ROOT / profile.native_skill_path).is_file()
        if profile.openai_path is not None:
            assert (ROOT / profile.openai_path).is_file()


def test_host_config_has_exact_coverage_without_canonical_identity_fields():
    config = _config()
    forbidden = {"title", "purpose", "path", "prompt", "manifest", "native", "native_skill"}

    assert config["schema_version"] == 2
    assert config["managed_by"] == SUBJECT.MANAGED_BY
    assert len(config["profiles"]) == 41
    assert tuple(item["id"] for item in config["profiles"]) == EXPECTED_IDS
    assert all(not (forbidden & set(item)) for item in config["profiles"])
    assert set(config) <= {"schema_version", "version", "managed_by", "toolsets", "profiles"}


def test_exact_coverage_rejects_missing_unknown_and_duplicate_mappings(tmp_path):
    config = _config()
    cases = [
        ({**config, "profiles": config["profiles"][:-1]}, "missing profile mappings"),
        ({**config, "profiles": [*config["profiles"], {**config["profiles"][0], "id": "unknown"}]}, "unknown profile mappings"),
        ({**config, "profiles": [*config["profiles"], config["profiles"][0]]}, "duplicate profile id"),
    ]

    for index, (value, message) in enumerate(cases):
        with pytest.raises(SUBJECT.ZCodeProfileError, match=message):
            SUBJECT.load_profiles(ROOT, _write_config(tmp_path / str(index), value))


def test_exact_coverage_rejects_noncanonical_mapping_order(tmp_path):
    config = _config()
    profiles = list(config["profiles"])
    profiles[0], profiles[1] = profiles[1], profiles[0]

    with pytest.raises(SUBJECT.ZCodeProfileError, match="preserve canonical registry order"):
        SUBJECT.load_profiles(ROOT, _write_config(tmp_path, {**config, "profiles": profiles}))


def test_validation_rejects_invalid_host_schema_and_options(tmp_path):
    config = _config()
    first = config["profiles"][0]
    cases = [
        ({**config, "schema_version": 1}, "unsupported ZCode config schema"),
        ({**config, "managed_by": "other"}, "invalid ZCode configuration"),
        ({**config, "toolsets": {**config["toolsets"], "bad": []}}, "invalid toolset"),
        ({**config, "profiles": [{**first, "color": "invisible"}, *config["profiles"][1:]]}, "invalid ZCode options"),
        ({**config, "profiles": [{**first, "maxTurns": 0}, *config["profiles"][1:]]}, "invalid max_turns"),
        ({**config, "profiles": [{**first, "toolset": "missing"}, *config["profiles"][1:]]}, "unknown toolset"),
    ]

    for index, (value, message) in enumerate(cases):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        with pytest.raises(SUBJECT.ZCodeProfileError, match=message):
            SUBJECT.load_profiles(ROOT, _write_config(case_dir, value))


def test_validation_rejects_malformed_yaml_unsafe_paths_and_missing_native_metadata(tmp_path, monkeypatch):
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("profiles: [", encoding="utf-8")
    with pytest.raises(SUBJECT.ZCodeProfileError, match="invalid YAML"):
        SUBJECT.load_profiles(ROOT, malformed)

    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("scalar", encoding="utf-8")
    with pytest.raises(SUBJECT.ZCodeProfileError, match="YAML must contain a mapping"):
        SUBJECT.load_profiles(ROOT, scalar)

    with pytest.raises(SUBJECT.ZCodeProfileError, match="path escapes runtime"):
        SUBJECT._relative_runtime_path(ROOT, "../outside")
    with pytest.raises(SUBJECT.ZCodeProfileError, match="unsafe runtime path"):
        SUBJECT._relative_runtime_path(ROOT, str((ROOT / "README.md").resolve()))
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
        SUBJECT.load_profiles(ROOT)


def test_rejects_missing_openai_metadata_when_native_agents_directory_exists(monkeypatch):
    real_is_file = Path.is_file

    def hide_openai(path):
        if path.as_posix().endswith("delivery-orchestrator-native/agents/openai.yaml"):
            return False
        return real_is_file(path)

    monkeypatch.setattr(Path, "is_file", hide_openai)
    with pytest.raises(SUBJECT.ZCodeProfileError, match="missing native interface metadata"):
        SUBJECT.load_profiles(ROOT)


def test_render_has_native_frontmatter_fingerprint_and_exact_probe_contract():
    profile = SUBJECT.load_profiles(ROOT)[0]
    rendered = SUBJECT.render_profile(profile, ROOT)
    frontmatter = _frontmatter(rendered)

    assert frontmatter["name"] == profile.agent_id
    assert frontmatter["injectAgentsMd"] is True
    assert frontmatter["memory"] == "project"
    assert frontmatter["x-managed-by"] == SUBJECT.MANAGED_BY
    assert frontmatter["x-activation-fingerprint"] == profile.activation_fingerprint
    assert frontmatter["x-activation-probe"] == profile.activation_probe
    assert profile.activation_fingerprint in rendered
    assert f"Respond with exactly `{profile.activation_probe}`" in rendered
    assert "This probe verifies rendered contract data, not host execution." in rendered
    assert profile.prompt_path in rendered
    assert profile.manifest_path in rendered
    assert profile.native_skill_path in rendered


def test_activation_fingerprint_is_deterministic_and_covers_host_options():
    profile = SUBJECT.load_profiles(ROOT)[0]
    same = SUBJECT.load_profiles(ROOT)[0]
    changed = replace(profile, max_turns=profile.max_turns + 1, activation_fingerprint="", activation_probe="")

    assert profile.activation_fingerprint == same.activation_fingerprint
    assert SUBJECT.activation_fingerprint(changed, ROOT) != profile.activation_fingerprint
    assert SUBJECT.activation_probe(profile) == profile.activation_probe


def test_sync_is_idempotent_and_check_detects_drift(tmp_path):
    destination = tmp_path / "agents"

    first = SUBJECT.sync_profiles(ROOT, destination)
    second = SUBJECT.sync_profiles(ROOT, destination)

    assert len(first["changed"]) == 41
    assert first["fingerprints"] == {profile.agent_id: profile.activation_fingerprint for profile in SUBJECT.load_profiles(ROOT)}
    assert len(second["unchanged"]) == 41
    assert SUBJECT.sync_profiles(ROOT, destination, check=True)["changed"] == []
    target = destination / "qa-engineer.md"
    target.write_bytes(target.read_bytes() + b"\ndrift\n")
    with pytest.raises(SUBJECT.ZCodeProfileError, match="out of sync"):
        SUBJECT.sync_profiles(ROOT, destination, check=True)


def test_dry_run_does_not_create_destination(tmp_path):
    destination = tmp_path / "missing"

    result = SUBJECT.sync_profiles(ROOT, destination, dry_run=True)

    assert len(result["changed"]) == 41
    assert not destination.exists()


def test_global_preflight_refuses_all_unmanaged_conflicts_before_writing(tmp_path):
    destination = tmp_path / "agents"
    destination.mkdir()
    conflict = destination / "qa-engineer.md"
    conflict.write_text("personal profile", encoding="utf-8")

    with pytest.raises(SUBJECT.ZCodeProfileError, match="unmanaged profile conflict"):
        SUBJECT.sync_profiles(ROOT, destination)

    assert list(destination.iterdir()) == [conflict]


@pytest.mark.parametrize(
    "content",
    [
        b"personal profile\nexample: x-managed-by: agents-squad-zcode-adapter\n",
        b"---\nx-managed-by: agents-squad-zcode-adapter\nmissing closing delimiter\n",
        b"---\nx-managed-by: [\n---\npersonal profile\n",
        b"---\n- x-managed-by\n- agents-squad-zcode-adapter\n---\npersonal profile\n",
    ],
)
def test_bounded_frontmatter_parser_does_not_claim_unmanaged_files(tmp_path, content):
    destination = tmp_path / "agents"
    destination.mkdir()
    target = destination / "qa-engineer.md"
    target.write_bytes(content)

    with pytest.raises(SUBJECT.ZCodeProfileError, match="unmanaged profile conflict"):
        SUBJECT.sync_profiles(ROOT, destination)
    assert SUBJECT.remove_managed_profiles(destination) == []
    assert target.read_bytes() == content


def test_oversized_frontmatter_is_bounded_and_unmanaged(tmp_path):
    destination = tmp_path / "agents"
    destination.mkdir()
    target = destination / "qa-engineer.md"
    content = b"---\n" + b"x" * (64 * 1024 + 1) + b"\n---\n"
    target.write_bytes(content)

    with pytest.raises(SUBJECT.ZCodeProfileError, match="unmanaged profile conflict"):
        SUBJECT.sync_profiles(ROOT, destination)
    assert SUBJECT.remove_managed_profiles(destination) == []
    assert target.read_bytes() == content


def test_sync_rolls_back_every_original_byte_when_later_write_fails(tmp_path, monkeypatch):
    destination = tmp_path / "agents"
    SUBJECT.sync_profiles(ROOT, destination)
    originals = {path.name: path.read_bytes() for path in destination.iterdir()}
    for name in ("delivery-orchestrator.md", "requirements-analyst.md"):
        (destination / name).write_bytes(originals[name] + b"\r\nbyte-drift\x00")
    originals = {path.name: path.read_bytes() for path in destination.iterdir()}
    real_replace = SUBJECT.os.replace
    calls = 0

    def fail_second_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        real_replace(source, target)

    monkeypatch.setattr(SUBJECT.os, "replace", fail_second_replace)
    with pytest.raises(SUBJECT.ZCodeProfileError, match="profile sync failed: disk full"):
        SUBJECT.sync_profiles(ROOT, destination)

    assert {path.name: path.read_bytes() for path in destination.iterdir()} == originals


def test_check_rejects_stale_managed_profiles_without_deleting_them(tmp_path):
    destination = tmp_path / "agents"
    SUBJECT.sync_profiles(ROOT, destination)
    stale = destination / "retired-persona.md"
    stale.write_text("---\nname: retired-persona\nx-managed-by: agents-squad-zcode-adapter\n---\n", encoding="utf-8")

    with pytest.raises(SUBJECT.ZCodeProfileError, match="stale managed profiles: retired-persona.md"):
        SUBJECT.sync_profiles(ROOT, destination, check=True)

    result = SUBJECT.sync_profiles(ROOT, destination)
    assert result["stale"] == ["retired-persona.md"]
    assert stale.is_file()


def test_remove_deletes_only_managed_profiles_after_global_preflight(tmp_path):
    destination = tmp_path / "agents"
    SUBJECT.sync_profiles(ROOT, destination)
    personal = destination / "personal.md"
    personal.write_text("not managed", encoding="utf-8")

    assert len(SUBJECT.remove_managed_profiles(destination, dry_run=True)) == 41
    assert len(SUBJECT.remove_managed_profiles(destination)) == 41
    assert personal.read_text(encoding="utf-8") == "not managed"


def test_destination_and_profile_symlinks_are_rejected_without_following(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unsupported")
    destination = tmp_path / "agents"
    destination.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("personal", encoding="utf-8")
    link = destination / "qa-engineer.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation not permitted")

    with pytest.raises(SUBJECT.ZCodeProfileError, match="symlink"):
        SUBJECT.sync_profiles(ROOT, destination)
    with pytest.raises(SUBJECT.ZCodeProfileError, match="symlink"):
        SUBJECT.remove_managed_profiles(destination)
    assert outside.read_text(encoding="utf-8") == "personal"


def test_migration_backs_up_existing_five_with_hash_manifest(tmp_path):
    destination = tmp_path / "agents"
    expected = SUBJECT.expected_profiles(ROOT)
    destination.mkdir()
    legacy_bytes = {}
    for agent_id in LEGACY_IDS:
        data = expected[f"{agent_id}.md"].encode("utf-8") + b"\r\nlegacy-byte\x00"
        (destination / f"{agent_id}.md").write_bytes(data)
        legacy_bytes[f"{agent_id}.md"] = data
    backup_root = tmp_path / "external-backups"

    result = SUBJECT.sync_profiles(ROOT, destination, migrate=True, backup_root=backup_root)

    backup = Path(result["backup"])
    assert backup.parent == backup_root.resolve()
    assert not backup.is_relative_to(destination)
    manifest = json.loads((backup / SUBJECT.BACKUP_MANIFEST).read_text(encoding="utf-8"))
    assert set(manifest["files"]) == set(legacy_bytes)
    for name, original in legacy_bytes.items():
        assert (backup / "files" / name).read_bytes() == original
        assert manifest["files"][name]["sha256"] == _sha256(original)
        assert manifest["files"][name]["size"] == len(original)
    assert len(list(destination.glob("*.md"))) == 41


def test_migration_failure_auto_restores_backup_and_removes_new_profiles(tmp_path, monkeypatch):
    destination = tmp_path / "agents"
    expected = SUBJECT.expected_profiles(ROOT)
    destination.mkdir()
    originals = {}
    for agent_id in LEGACY_IDS:
        data = expected[f"{agent_id}.md"].encode("utf-8") + b"\nlegacy"
        (destination / f"{agent_id}.md").write_bytes(data)
        originals[f"{agent_id}.md"] = data
    real_replace = SUBJECT.os.replace
    calls = 0

    def fail_after_backup(source, target):
        nonlocal calls
        if Path(target).parent == destination:
            calls += 1
            if calls == 2:
                raise OSError("migration write failed")
        real_replace(source, target)

    monkeypatch.setattr(SUBJECT.os, "replace", fail_after_backup)
    with pytest.raises(SUBJECT.ZCodeProfileError, match="migration write failed"):
        SUBJECT.sync_profiles(ROOT, destination, migrate=True, backup_root=tmp_path / "backups")

    assert {path.name: path.read_bytes() for path in destination.glob("*.md")} == originals


def test_migration_does_not_backup_fresh_install(tmp_path):
    destination = tmp_path / "agents"
    backup_root = tmp_path / "backups"

    result = SUBJECT.sync_profiles(ROOT, destination, migrate=True, backup_root=backup_root)

    assert result["backup"] is None
    assert not backup_root.exists()


def test_idempotent_migration_does_not_create_another_backup(tmp_path):
    destination = tmp_path / "agents"
    backup_root = tmp_path / "backups"
    SUBJECT.sync_profiles(ROOT, destination)

    result = SUBJECT.sync_profiles(ROOT, destination, migrate=True, backup_root=backup_root)

    assert result["changed"] == []
    assert result["backup"] is None
    assert not backup_root.exists()


def test_backup_root_must_be_external_and_not_a_symlink(tmp_path):
    destination = tmp_path / "agents"
    SUBJECT.sync_profiles(ROOT, destination)

    with pytest.raises(SUBJECT.ZCodeProfileError, match="outside managed destination"):
        SUBJECT.create_migration_backup(destination, destination / "backups")


def test_cli_resolves_scopes_defaults_to_new_config_and_emits_probe_evidence(tmp_path, capsys):
    user_root = tmp_path / "user"
    assert CLI.DEFAULT_CONFIG == ROOT / "config/zcode-agents.yaml"
    assert CLI.resolve_destination("user", ROOT, user_root) == user_root.absolute() / "agents/agents-squad"
    assert CLI.resolve_destination("project", tmp_path) == tmp_path.absolute() / ".zcode/agents/agents-squad"

    user_destination = CLI.resolve_destination("user", ROOT, user_root)
    user_auth = _authorization(tmp_path, "user", user_destination)
    assert CLI.main(["--user-root", str(user_root), "--authorization", str(user_auth)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert len(output["changed"]) == 41
    assert len(output["fingerprints"]) == 41
    assert len(output["probes"]) == 41
    assert output["host_execution_verified"] is False
    assert CLI.main(["--user-root", str(user_root), "--check"]) == 0
    capsys.readouterr()
    user_remove_auth = _authorization(tmp_path, "user", user_destination, "remove-managed")
    assert CLI.main(["--user-root", str(user_root), "--remove-managed", "--authorization", str(user_remove_auth)]) == 0
    capsys.readouterr()

    project_root = tmp_path / "project"
    project_root.mkdir()
    project_args = ["--scope", "project", "--project-root", str(project_root)]
    project_destination = CLI.resolve_destination("project", project_root)
    project_auth = _authorization(tmp_path, "project", project_destination)
    assert CLI.main([*project_args, "--authorization", str(project_auth)]) == 0
    project_output = json.loads(capsys.readouterr().out)
    assert len(project_output["changed"]) == 41
    assert CLI.main([*project_args, "--check"]) == 0
    capsys.readouterr()
    project_remove_auth = _authorization(tmp_path, "project", project_destination, "remove-managed")
    assert CLI.main([*project_args, "--remove-managed", "--authorization", str(project_remove_auth)]) == 0


def test_cli_blocks_mutations_without_complete_target_bound_authorization(tmp_path, capsys):
    user_root = tmp_path / "user"
    destination = CLI.resolve_destination("user", ROOT, user_root)

    assert CLI.main(["--user-root", str(user_root)]) == 1
    assert "missing mutation authorization" in capsys.readouterr().err
    assert not destination.exists()
    assert CLI.main(["--user-root", str(user_root), "--dry-run"]) == 0
    capsys.readouterr()
    assert not destination.exists()
    assert CLI.main(["--user-root", str(user_root), "--check"]) == 1
    assert "profiles out of sync" in capsys.readouterr().err
    assert not destination.exists()

    authorization = _authorization(tmp_path, "user", destination)
    record = json.loads(authorization.read_text(encoding="utf-8"))
    del record["approvals"]["G4"]
    authorization.write_text(json.dumps(record), encoding="utf-8")
    assert CLI.main(["--user-root", str(user_root), "--authorization", str(authorization)]) == 1
    assert "G4" in capsys.readouterr().err
    assert not destination.exists()


def test_filesystem_failures_are_normalized_as_domain_errors(tmp_path, monkeypatch):
    blocked_parent = tmp_path / "blocked-parent"
    blocked_parent.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SUBJECT.ZCodeProfileError, match="profile sync failed"):
        SUBJECT.sync_profiles(ROOT, blocked_parent / "agents")

    destination = tmp_path / "agents"
    SUBJECT.sync_profiles(ROOT, destination)
    target = destination / "qa-engineer.md"
    real_read_bytes = SUBJECT.Path.read_bytes

    def deny_profile_read(path):
        if path == target:
            raise PermissionError("read denied")
        return real_read_bytes(path)

    monkeypatch.setattr(SUBJECT.Path, "read_bytes", deny_profile_read)
    with pytest.raises(SUBJECT.ZCodeProfileError, match="cannot inspect profile destination.*read denied"):
        SUBJECT.sync_profiles(ROOT, destination)


def test_backup_and_restore_failures_are_normalized_as_domain_errors(tmp_path):
    destination = tmp_path / "agents"
    SUBJECT.sync_profiles(ROOT, destination)
    blocked_backup_root = tmp_path / "blocked-backup-root"
    blocked_backup_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SUBJECT.ZCodeProfileError, match="migration backup failed"):
        SUBJECT.create_migration_backup(destination, blocked_backup_root)

    malformed = tmp_path / "malformed-backup"
    malformed.mkdir()
    (malformed / "manifest.json").write_text("[not-an-object]", encoding="utf-8")
    with pytest.raises(SUBJECT.ZCodeProfileError, match="invalid migration backup"):
        SUBJECT.restore_migration_backup(destination, malformed)


def test_cli_normalizes_operational_filesystem_errors(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(CLI, "sync_profiles", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")))
    destination = CLI.resolve_destination("user", ROOT, tmp_path)
    authorization = _authorization(tmp_path, "user", destination)

    assert CLI.main(["--user-root", str(tmp_path), "--authorization", str(authorization)]) == 1
    assert "ZCODE_SUBAGENTS_ERROR: denied" in capsys.readouterr().err


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

