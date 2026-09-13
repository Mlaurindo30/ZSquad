"""RED contract tests for namespaced work-item resolution.

These tests intentionally target the resolver seam approved by ADR-001.  The
seam does not exist yet in the production runner, so the first execution must
fail with an explicit missing-contract assertion (or with the known CLI path
failure), before any production implementation is written.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.bdd_runner as bdd_runner
from scripts.project_context import ProjectContext


WORK_ITEM_ID = "BUG-NPR-BDD-RUNNER-PATH-20260913"


def _context(tmp_path: Path) -> tuple[ProjectContext, Path, Path, Path]:
    runtime = tmp_path / "runtime"
    project_root = tmp_path / "consumer"
    namespaced = runtime / "work" / "agent_squad"
    legacy_root = runtime / "work"
    namespaced.mkdir(parents=True)
    project_root.mkdir()
    (runtime / "scripts").mkdir()
    (runtime / "agents").mkdir()
    (runtime / "contracts").mkdir()
    return ProjectContext(runtime, project_root, "agent_squad"), namespaced, legacy_root, runtime


def _item(root: Path, item_id: str) -> Path:
    item = root / item_id
    (item / "specs" / "features").mkdir(parents=True)
    (item / "status.yaml").write_text(
        "id: %s\ntype: bug\nstate: blueprint\nrisk: medium\n" % item_id,
        encoding="utf-8",
    )
    (item / "specs" / "features" / "sample.feature").write_text(
        "Feature: path resolution\n"
        "  @AC_PATH\n"
        "  Scenario: valid feature\n"
        "    Given a valid work item\n"
        "    When the runner resolves it\n"
        "    Then it stays within the work root\n",
        encoding="utf-8",
    )
    return item


def _resolver():
    resolver = getattr(bdd_runner, "resolve_work_item_reference", None)
    if not callable(resolver):
        pytest.fail(
            "RED: resolve_work_item_reference seam is absent; implement the approved "
            "work-item resolution contract before this test can pass"
        )
    return resolver


def _resolution_error_type():
    error_type = getattr(bdd_runner, "WorkItemResolutionError", None)
    if not isinstance(error_type, type):
        pytest.fail(
            "RED: WorkItemResolutionError is absent; the resolver must expose "
            "a stable typed boundary error"
        )
    return error_type


def _resolve(raw: str, context: ProjectContext, legacy_root: Path | None = None) -> Path:
    return _resolver()(raw, context, legacy_root=legacy_root)


def test_bare_id_prefers_namespaced_item_when_legacy_also_exists(tmp_path: Path) -> None:
    context, namespaced_root, legacy_root, _ = _context(tmp_path)
    namespaced = _item(namespaced_root, "ITEM-001")
    _item(legacy_root, "ITEM-001")

    resolved = _resolve("ITEM-001", context, legacy_root=legacy_root)

    assert resolved == namespaced.resolve()


@pytest.mark.parametrize(
    ("reference", "item_id"),
    [
        ("work/agent_squad/ITEM-002", "ITEM-002"),
        ("agent_squad/ITEM-003", "ITEM-003"),
        (r"work\agent_squad\ITEM-004", "ITEM-004"),
    ],
)
def test_namespaced_references_resolve_once_with_both_separators(
    tmp_path: Path, reference: str, item_id: str
) -> None:
    context, namespaced_root, legacy_root, _ = _context(tmp_path)
    expected = _item(namespaced_root, item_id)

    resolved = _resolve(reference, context, legacy_root=legacy_root)

    assert resolved == expected.resolve()


def test_absolute_authorized_reference_is_accepted(tmp_path: Path) -> None:
    context, namespaced_root, legacy_root, _ = _context(tmp_path)
    expected = _item(namespaced_root, "ITEM-005")

    resolved = _resolve(str(expected), context, legacy_root=legacy_root)

    assert resolved == expected.resolve()


def test_explicit_legacy_reference_is_accepted_only_when_legacy_item_exists(tmp_path: Path) -> None:
    context, _, legacy_root, _ = _context(tmp_path)
    expected = _item(legacy_root, "ITEM-006")

    resolved = _resolve("work/ITEM-006", context, legacy_root=legacy_root)

    assert resolved == expected.resolve()


@pytest.mark.parametrize(
    "reference",
    [
        "MISSING-001",
        "work/agent_squad/MISSING-002",
        "work/agent_squad/NO-STATUS",
    ],
)
def test_missing_item_or_status_marker_is_rejected_without_creating_files(
    tmp_path: Path, reference: str
) -> None:
    context, namespaced_root, legacy_root, _ = _context(tmp_path)
    if reference.endswith("NO-STATUS"):
        missing_status = namespaced_root / "NO-STATUS"
        missing_status.mkdir()
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    with pytest.raises(_resolution_error_type()):
        _resolve(reference, context, legacy_root=legacy_root)

    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert after == before


@pytest.mark.parametrize(
    "reference",
    [
        "../ITEM-007",
        "work/agent_squad/../ITEM-008",
        r"C:foo",
        r"\foo",
        "/foo",
        r"\\other-host\share\ITEM-009",
    ],
)
def test_traversal_drive_rooted_relative_and_external_unc_are_rejected(
    tmp_path: Path, reference: str
) -> None:
    context, _, legacy_root, _ = _context(tmp_path)
    sentinel = tmp_path / "outside-sentinel.txt"
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    with pytest.raises(_resolution_error_type()):
        _resolve(reference, context, legacy_root=legacy_root)

    assert not sentinel.exists()
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert after == before


def test_injected_legacy_root_cannot_escape_runtime_work_root(tmp_path: Path) -> None:
    context, _, _, _ = _context(tmp_path)
    external_root = tmp_path / "external-legacy"
    _item(external_root, "ITEM-010")

    with pytest.raises(_resolution_error_type()):
        _resolve("work/ITEM-010", context, legacy_root=external_root)


def test_symlink_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    context, namespaced_root, legacy_root, _ = _context(tmp_path)
    external_root = tmp_path / "external"
    expected = _item(external_root, "ITEM-011")
    link = namespaced_root / "ITEM-011"
    try:
        link.symlink_to(expected, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"UNVERIFIED: symlink/junction creation unavailable: {exc}")

    with pytest.raises(_resolution_error_type()):
        _resolve("ITEM-011", context, legacy_root=legacy_root)


def test_cli_bare_id_uses_namespaced_runtime_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = tmp_path / "runtime"
    item = _item(runtime / "work" / "agent_squad", WORK_ITEM_ID)
    monkeypatch.setattr(bdd_runner, "ROOT", runtime)

    rc = bdd_runner.main(["--work-item", WORK_ITEM_ID])

    assert rc == 0, f"RED: current CLI did not resolve namespaced item: expected {item}"
    assert (item / "evaluation" / "bdd.json").is_file()


def test_cli_canonical_namespaced_reference_is_not_prefixed_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    item = _item(runtime / "work" / "agent_squad", "ITEM-012")
    monkeypatch.setattr(bdd_runner, "ROOT", runtime)

    rc = bdd_runner.main(["--work-item", "work/agent_squad/ITEM-012"])

    assert rc == 0, f"RED: current CLI duplicated work/ prefix; expected {item}"
    assert (item / "evaluation" / "bdd.json").is_file()


def test_cli_absolute_authorized_reference_writes_only_inside_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    item = _item(runtime / "work" / "agent_squad", "ITEM-013")
    monkeypatch.setattr(bdd_runner, "ROOT", runtime)

    rc = bdd_runner.main(["--work-item", str(item)])

    assert rc == 0
    assert (item / "evaluation" / "bdd.json").is_file()
    assert not (runtime / "evaluation" / "bdd.json").exists()
