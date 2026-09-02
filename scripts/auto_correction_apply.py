"""Aplica uma proposta de autocorreção em worktree isolado e descarta em falha.

Política de segurança:
- Operações destrutivas (delete) são bloqueadas — apenas ``create`` e ``update``.
- O apply acontece sempre em worktree descartável (`git worktree add --detach`).
- Após o apply, a suíte focada do projeto é executada dentro do worktree.
- Sucesso: devolve patch com digests antes/depois; o worktree é mantido para
  revisão humana.
- Falha (teste ou exceção): o worktree é removido com ``git worktree remove
  --force`` e o caller recebe ``patched=False`` com diagnóstico.

Por design, este módulo NÃO muta o branch principal diretamente.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from scripts.auto_correction import (
    AppliedRefinementEdit,
    RefinementPlan,
)


@dataclass
class WorktreeApplyResult:
    """Resumo do apply em worktree."""

    patched: bool
    worktree_path: Path
    branch: str
    before_sha: Optional[str]
    after_sha: Optional[str]
    applied_edits_count: int
    test_summary: dict
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "patched": self.patched,
            "worktree_path": str(self.worktree_path),
            "branch": self.branch,
            "before_sha": self.before_sha,
            "after_sha": self.after_sha,
            "applied_edits_count": self.applied_edits_count,
            "test_summary": self.test_summary,
            "error": self.error,
        }


def _run(cmd: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git_head(repo: Path) -> str:
    proc = _run(["git", "rev-parse", "HEAD"], repo)
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse falhou em {repo}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _make_worktree(repo: Path, branch: str, base_commit: str) -> Path:
    """Cria worktree isolado em ``<repo>/.squad-worktrees/<branch>``."""
    worktree_root = repo / ".squad-worktrees" / branch
    if worktree_root.exists():
        shutil.rmtree(worktree_root, ignore_errors=True)
    proc = _run(
        ["git", "worktree", "add", "--detach", str(worktree_root), base_commit],
        repo,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git worktree add falhou em {repo}: {proc.stderr.strip()}"
        )
    return worktree_root


def _remove_worktree(repo: Path, worktree_path: Path) -> None:
    _run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        repo,
        timeout=30,
    )


def _validate_edit_kind(kind: str) -> None:
    if kind == "decision":
        return  # deliberações de governança são registradas, não editam arquivos.
    if kind not in {"skill", "config", "script"}:
        raise ValueError(f"edit kind fora da política: {kind!r}")


def _apply_edits(worktree: Path, edits: list[AppliedRefinementEdit]) -> int:
    """Aplica edições permitidas (create/update apenas) e devolve a contagem.

    Edições com ``kind`` fora de ``skill``/``config``/``script`` ou ``action``
    ``delete`` são puladas, mantendo a política do ``auto_correction_trigger``.
    """
    applied = 0
    for edit in edits:
        if not edit.applied:
            continue
        if edit.action == "delete":
            continue
        _validate_edit_kind(edit.kind)
        if not edit.path or not edit.content:
            continue
        target = worktree / edit.path
        if not target.resolve().is_relative_to(worktree.resolve()):
            raise ValueError(f"path escapa do worktree: {edit.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(edit.content, encoding="utf-8")
        applied += 1
    return applied


def _run_focused_tests(worktree: Path) -> dict:
    """Executa a suíte focada do squad dentro do worktree.

    O caminho do pytest é descoberto via sys.executable. Marcadores limitam a
    execução aos módulos próprios (exclui vendor e tests lentos).
    """
    cmd = [
        sys_executable(),
        "-m",
        "pytest",
        "-q",
        "scripts/tests/test_llm_providers_policy.py",
        "scripts/tests/test_orchestration_controller.py",
        "scripts/tests/test_sql_safety_lint.py",
        "--no-cov",
    ]
    proc = _run(cmd, worktree, timeout=180)
    return {
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-400:],
        "stderr_tail": proc.stderr[-400:],
    }


def sys_executable() -> str:
    import sys
    return sys.executable


def _commit_worktree(worktree: Path, message: str) -> str:
    """Cria commit no worktree com tudo que foi alterado; devolve SHA do commit."""
    _run(["git", "add", "-A"], worktree)
    proc = _run(
        ["git", "commit", "--allow-empty", "-m", message, "-q"],
        worktree,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git commit falhou: {proc.stderr.strip()}")
    return _git_head(worktree)


def apply_in_worktree(
    repo_path: Path,
    plan: RefinementPlan,
    *,
    applied_edits: Optional[list] = None,
    branch_prefix: str = "squad-auto",
    run_tests: bool = True,
) -> WorktreeApplyResult:
    """Aplica uma proposta em worktree descartável com rollback real.

    ``repo_path`` deve estar dentro de um repositório git funcional; ``plan`` é
    a estrutura devolvida por ``plan_refinement``. ``applied_edits`` é a lista
    (tipicamente ``RefinementResult.applied_edits``) que de fato será gravada;
    quando omitido, usa ``plan.proposal.edits`` como rascunho (limitado).

    O retorno contém os digests antes/depois e o resumo da suíte focada.
    """
    repo = Path(repo_path).resolve()
    if not (repo / ".git").exists():
        raise RuntimeError(f"{repo} não é um repositório git")
    before_sha = _git_head(repo)
    branch = f"{branch_prefix}-{int(time.time())}-{plan.id[:8]}"
    worktree = _make_worktree(repo, branch, before_sha)
    try:
        edits_source = applied_edits if applied_edits is not None else plan.proposal.edits
        applied = _apply_edits(worktree, edits_source)
        if applied == 0:
            _remove_worktree(repo, worktree)
            return WorktreeApplyResult(
                patched=False,
                worktree_path=worktree,
                branch=branch,
                before_sha=before_sha,
                after_sha=before_sha,
                applied_edits_count=0,
                test_summary={},
                error="nenhuma edição elegível aplicada",
            )
        if run_tests:
            summary = _run_focused_tests(worktree)
            if summary["returncode"] != 0:
                _remove_worktree(repo, worktree)
                return WorktreeApplyResult(
                    patched=False,
                    worktree_path=worktree,
                    branch=branch,
                    before_sha=before_sha,
                    after_sha=before_sha,
                    applied_edits_count=applied,
                    test_summary=summary,
                    error="testes focados falharam",
                )
        else:
            summary = {"skipped": True}
        after_sha = _commit_worktree(
            worktree, f"auto-correction: {plan.id}"
        )
        return WorktreeApplyResult(
            patched=True,
            worktree_path=worktree,
            branch=branch,
            before_sha=before_sha,
            after_sha=after_sha,
            applied_edits_count=applied,
            test_summary=summary,
        )
    except Exception as exc:
        _remove_worktree(repo, worktree)
        return WorktreeApplyResult(
            patched=False,
            worktree_path=worktree,
            branch=branch,
            before_sha=before_sha,
            after_sha=None,
            applied_edits_count=0,
            test_summary={},
            error=f"{type(exc).__name__}: {exc}",
        )


__all__ = ["WorktreeApplyResult", "apply_in_worktree"]
