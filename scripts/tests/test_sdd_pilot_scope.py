"""RED contract tests for the work-item-scoped SDD pilot (T0).

These tests intentionally target the proposed public resolver.  They use only
temporary project roots and on-disk pilot records; no provider, credential, or
shared work directory is involved.  The resolver is not implemented yet, so
the suite is expected to fail until the T0 implementation supplies it.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import project_context


PILOT_ID = "TASK-SDD-EXECUTOR-T1-20260912"
LEGACY_ID = "TASK-LEGACY-01"
INJECTED_ID = "TASK-INJECTED-02"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_project(root: Path, *, global_required: bool | None = None) -> Path:
    config = root / ".agents_squad" / "config"
    config.mkdir(parents=True)
    (config / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "runtime": str(ROOT),
                "project_id": "test-sdd-pilot-t0",
                "project_name": "test-sdd-pilot-t0",
                "project_root": str(root),
                "work_dir": str(ROOT / "work" / "test-sdd-pilot-t0"),
                "db_path": str(ROOT / "banco" / "squad.db"),
                "overrides": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    if global_required is not None:
        (config / "sdd-policy.yaml").write_text(
            yaml.safe_dump({"policy_version": 1, "sdd": {"required": global_required}}, sort_keys=False),
            encoding="utf-8",
        )
    return config


def _write_active_pilot(root: Path, *, work_items: list[str] | None = None) -> dict[str, Path]:
    """Materialize the canonical activated snapshot defined by the T0 design."""
    config = _write_project(root)
    work_items = work_items or [PILOT_ID]
    scope_path = config / "sdd-pilot-scope.yaml"
    scope_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "scope_mode": "allowlist",
                "policy": {"policy_version": 1, "sdd": {"required": True}},
                "work_items": work_items,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    activation_id = "11111111-1111-1111-1111-111111111111"
    activation_path = config / "sdd-pilot-activation.yaml"
    activation_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "activation_id": activation_id,
                "activated_at": "2026-09-13T00:00:00Z",
                "scope_sha256": _sha256(scope_path.read_bytes()),
                "scope_policy_version": 1,
                "work_items": work_items,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    journal = config / "sdd-pilot-journal"
    events = journal / "events"
    events.mkdir(parents=True)
    genesis = _sha256(_canonical_json({"schema_version": 1, "kind": "sdd-pilot-genesis"}))
    event = {
        "schema_version": 1,
        "event_id": activation_id,
        "event": "activated",
        "activation_id": activation_id,
        "occurred_at": "2026-09-13T00:00:00Z",
        "scope_sha256": _sha256(scope_path.read_bytes()),
        "scope_policy_version": 1,
        "work_items": work_items,
        "sequence": 1,
        "prev_event_sha256": genesis,
    }
    event["event_sha256"] = _sha256(_canonical_json(event))
    event_path = events / f"00000001-{activation_id}.json"
    event_path.write_bytes(_canonical_json(event))
    head_path = journal / "HEAD.json"
    head_path.write_bytes(
        _canonical_json(
            {
                "schema_version": 1,
                "genesis_sha256": genesis,
                "sequence": 1,
                "event_id": activation_id,
                "event_sha256": event["event_sha256"],
            }
        )
    )
    return {"config": config, "scope": scope_path, "activation": activation_path, "journal": journal, "event": event_path, "head": head_path}


def _resolve(root: Path, work_id: str):
    resolver = getattr(project_context, "resolve_sdd_requirement", None)
    assert callable(resolver), "RED: resolve_sdd_requirement is not implemented in scripts/project_context.py"
    return resolver(root, work_id)


def test_allowlisted_required_and_legacy_unchanged(tmp_path: Path):
    root = tmp_path / "pilot"
    _write_active_pilot(root)

    assert _resolve(root, PILOT_ID).state == "pilot-required"
    assert _resolve(root, LEGACY_ID).state == "legacy"


def test_missing_scope_after_activation_blocks_snapshot_only(tmp_path: Path):
    root = tmp_path / "missing-scope"
    records = _write_active_pilot(root)
    records["scope"].unlink()

    resolution = _resolve(root, PILOT_ID)
    assert resolution.state == "pilot-invalid"
    assert "SDD_PILOT_SCOPE_INVALID" in resolution.errors
    assert _resolve(root, LEGACY_ID).state == "legacy"


def test_missing_projection_after_activation_blocks_snapshot_only(tmp_path: Path):
    root = tmp_path / "missing-projection"
    records = _write_active_pilot(root)
    records["activation"].unlink()

    resolution = _resolve(root, PILOT_ID)
    assert resolution.state == "pilot-invalid"
    assert "SDD_PILOT_SCOPE_INVALID" in resolution.errors
    assert _resolve(root, LEGACY_ID).state == "legacy"


def test_tampered_scope_never_expands_blast_radius(tmp_path: Path):
    root = tmp_path / "tampered-scope"
    records = _write_active_pilot(root)
    scope = yaml.safe_load(records["scope"].read_text(encoding="utf-8"))
    scope["work_items"].append(INJECTED_ID)
    records["scope"].write_text(yaml.safe_dump(scope, sort_keys=False), encoding="utf-8")

    original = _resolve(root, PILOT_ID)
    assert original.state == "pilot-invalid"
    assert "SDD_PILOT_SCOPE_INVALID" in original.errors
    assert _resolve(root, INJECTED_ID).state == "legacy"
    assert _resolve(root, LEGACY_ID).state == "legacy"


def test_invalid_journal_blocks_snapshot_only(tmp_path: Path):
    root = tmp_path / "invalid-journal"
    records = _write_active_pilot(root)
    records["head"].write_bytes(b'{"sequence":999}')

    resolution = _resolve(root, PILOT_ID)
    assert resolution.state == "pilot-invalid"
    assert "SDD_PILOT_JOURNAL_INVALID" in resolution.errors
    assert _resolve(root, LEGACY_ID).state == "legacy"


def test_global_policy_precedence_unchanged(tmp_path: Path):
    root = tmp_path / "global-precedence"
    _write_active_pilot(root)
    config = root / ".agents_squad" / "config"
    (config / "sdd-policy.yaml").write_text(
        yaml.safe_dump({"policy_version": 1, "sdd": {"required": True}}, sort_keys=False),
        encoding="utf-8",
    )

    assert _resolve(root, PILOT_ID).state == "global-required"
    assert _resolve(root, LEGACY_ID).state == "global-required"


def test_pilot_revoke_is_unavailable_and_non_mutating(tmp_path: Path):
    root = tmp_path / "revoke-unavailable"
    records = _write_active_pilot(root)
    before = {name: path.read_bytes() for name, path in records.items() if name not in {"config", "journal"}}

    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "agent_squad.py"), "--project-root", str(root), "sdd", "pilot-revoke"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "SDD_PILOT_REVOKE_UNAVAILABLE" in (completed.stdout + completed.stderr)
    assert before == {name: path.read_bytes() for name, path in records.items() if name not in {"config", "journal"}}
