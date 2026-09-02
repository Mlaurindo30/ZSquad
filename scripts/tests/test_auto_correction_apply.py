"""Cobertura determinística de scripts/auto_correction_apply.py.

Cria um repositório git descartável em ``tmp_path``, comita um arquivo
inicial e testa o apply em worktree. Verifica:
- apply bem-sucedido cria arquivo no worktree e retorna patched=True.
- apply com edição inválida (kind proibido) é rejeitado.
- apply com edição cuja path escapa do worktree é rejeitado.
- apply com apenas ``delete`` é ignorado (não há política de deletar).
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.auto_correction_apply import (
    WorktreeApplyResult,
    apply_in_worktree,
)


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "squad@example.com")
    _git(repo, "config", "user.name", "Squad Test")
    (repo / "README.md").write_text("init", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init", "-q")
    return repo


def _make_plan():
    from scripts.auto_correction import RefinementPlan, RefinementProposal

    proposal = RefinementProposal(
        summary="unit-test plan",
        rationale="synthetic",
        edits=[],
        expected_outcome="ok",
    )
    return RefinementPlan(
        proposal=proposal,
        id="refine_unit_test",
        rollback_of=None,
        rollback_scope="local",
        baseline_state=None,
    )


def _make_edit(action, kind, edit_id, path, content):
    from scripts.auto_correction import AppliedRefinementEdit

    return AppliedRefinementEdit(
        action=action,
        kind=kind,
        id=edit_id,
        title=edit_id,
        content=content,
        path=path,
        reference=None,
        arguments=None,
        metadata=None,
        reason="unit-test",
        edit_id=edit_id,
        before=None,
        after={"content": content, "path": path},
        applied=True,
    )


def test_apply_creates_file_in_worktree(tmp_path):
    repo = _init_repo(tmp_path)
    plan = _make_plan()
    edits = [
        _make_edit(
            action="create",
            kind="config",
            edit_id="cfg-1",
            path="config/extra.yaml",
            content="key: value\n",
        ),
    ]
    result = apply_in_worktree(repo, plan, applied_edits=edits, run_tests=False)
    assert isinstance(result, WorktreeApplyResult)
    assert result.patched is True
    assert result.applied_edits_count == 1
    assert result.before_sha != result.after_sha
    assert (result.worktree_path / "config" / "extra.yaml").exists()
    _git(
        tmp_path,
        "worktree",
        "remove",
        "--force",
        str(result.worktree_path),
        check=False,
    )


def test_apply_blocks_forbidden_kind(tmp_path):
    repo = _init_repo(tmp_path)
    plan = _make_plan()
    edits = [
        _make_edit(
            action="update",
            kind="file",
            edit_id="bad",
            path="README.md",
            content="tampered",
        ),
    ]
    result = apply_in_worktree(repo, plan, applied_edits=edits, run_tests=False)
    assert result.patched is False
    assert "fora da política" in (result.error or "")


def test_apply_blocks_path_escape(tmp_path):
    repo = _init_repo(tmp_path)
    outside = tmp_path / "outside.txt"
    plan = _make_plan()
    edits = [
        _make_edit(
            action="create",
            kind="config",
            edit_id="escape",
            path=str(outside),
            content="x",
        ),
    ]
    result = apply_in_worktree(repo, plan, applied_edits=edits, run_tests=False)
    assert result.patched is False
    assert "escapa do worktree" in (result.error or "")


def test_apply_skips_delete_actions(tmp_path):
    repo = _init_repo(tmp_path)
    plan = _make_plan()
    edits = [
        _make_edit(
            action="delete",
            kind="skill",
            edit_id="del",
            path="skills/never.md",
            content="",
        ),
    ]
    result = apply_in_worktree(repo, plan, applied_edits=edits, run_tests=False)
    assert result.applied_edits_count == 0
    assert result.patched is False
    assert "nenhuma edição elegível" in (result.error or "")
