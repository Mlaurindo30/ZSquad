import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations" / "experimental"))

import clone_or_update_repos
from clone_or_update_repos import RepoSpec, clone_or_update
from code_health_analyzer import CodeHealthAnalyzer
from contextual_ast_chunker import ContextualASTChunker


class TestCloneOrUpdateRepos:
    repo = RepoSpec("example", "https://example.test/repo.git", "Example")

    def test_updates_existing_repository_successfully(self, tmp_path):
        target = tmp_path / self.repo.name
        (target / ".git").mkdir(parents=True)
        result = SimpleNamespace(returncode=0, stdout="Already current\n", stderr="")

        with mock.patch.object(clone_or_update_repos.subprocess, "run", return_value=result) as run:
            assert clone_or_update(self.repo, tmp_path) == (
                "example",
                True,
                "Atualizado com sucesso (Already current)",
            )

        run.assert_called_once_with(
            ["git", "pull", "--ff-only"],
            cwd=str(target),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_update_uses_fallback_message_for_empty_stdout(self, tmp_path):
        (tmp_path / self.repo.name / ".git").mkdir(parents=True)
        result = SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(clone_or_update_repos.subprocess, "run", return_value=result):
            assert clone_or_update(self.repo, tmp_path)[2] == "Atualizado com sucesso (up to date)"

    def test_reports_update_failure(self, tmp_path):
        (tmp_path / self.repo.name / ".git").mkdir(parents=True)
        result = SimpleNamespace(returncode=1, stdout="", stderr="cannot pull\n")

        with mock.patch.object(clone_or_update_repos.subprocess, "run", return_value=result):
            assert clone_or_update(self.repo, tmp_path) == (
                "example",
                False,
                "Falha no git pull: cannot pull",
            )

    def test_reports_update_exception(self, tmp_path):
        (tmp_path / self.repo.name / ".git").mkdir(parents=True)

        with mock.patch.object(clone_or_update_repos.subprocess, "run", side_effect=TimeoutError("late")):
            assert clone_or_update(self.repo, tmp_path) == (
                "example",
                False,
                "Exceção ao atualizar: late",
            )

    @pytest.mark.parametrize(
        ("shallow", "expected_command"),
        [
            (True, ["git", "clone", "--depth", "1"]),
            (False, ["git", "clone"]),
        ],
    )
    def test_clones_repository_with_requested_history(self, tmp_path, shallow, expected_command):
        result = SimpleNamespace(returncode=0, stdout="done", stderr="")

        with mock.patch.object(clone_or_update_repos.subprocess, "run", return_value=result) as run:
            assert clone_or_update(self.repo, tmp_path, shallow=shallow) == (
                "example",
                True,
                "Clonado com sucesso em 'example'",
            )

        run.assert_called_once_with(
            expected_command + [self.repo.url, str(tmp_path / "example")],
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_reports_clone_failure(self, tmp_path):
        result = SimpleNamespace(returncode=1, stdout="", stderr="cannot clone\n")

        with mock.patch.object(clone_or_update_repos.subprocess, "run", return_value=result):
            assert clone_or_update(self.repo, tmp_path) == (
                "example",
                False,
                "Falha no git clone: cannot clone",
            )

    def test_reports_clone_exception(self, tmp_path):
        with mock.patch.object(clone_or_update_repos.subprocess, "run", side_effect=OSError("offline")):
            assert clone_or_update(self.repo, tmp_path) == (
                "example",
                False,
                "Exceção ao clonar: offline",
            )

    def test_main_rejects_unknown_repository(self, capsys, tmp_path):
        assert clone_or_update_repos.main(["--vendor-dir", str(tmp_path), "--repo", "missing"]) == 1
        assert "não encontrado" in capsys.readouterr().out

    def test_main_syncs_selected_repository_without_shallow_clone(self, capsys, tmp_path):
        with mock.patch.object(
            clone_or_update_repos,
            "clone_or_update",
            return_value=("boostprompt", True, "done"),
        ) as sync:
            result = clone_or_update_repos.main(
                ["--vendor-dir", str(tmp_path), "--repo", "boostprompt", "--no-shallow"]
            )

        assert result == 0
        assert "Sincronizando 1 repositórios" in capsys.readouterr().out
        sync.assert_called_once_with(
            clone_or_update_repos.UPSTREAM_REPOSITORIES[0], tmp_path, shallow=False
        )

    def test_main_returns_failure_when_every_sync_fails(self, tmp_path):
        with mock.patch.object(
            clone_or_update_repos,
            "clone_or_update",
            return_value=("failed", False, "offline"),
        ) as sync:
            assert clone_or_update_repos.main(["--vendor-dir", str(tmp_path)]) == 1

        assert sync.call_count == len(clone_or_update_repos.UPSTREAM_REPOSITORIES)

    def test_script_entry_point_uses_sys_argv(self, tmp_path):
        result = SimpleNamespace(returncode=0, stdout="", stderr="")
        script = ROOT / "integrations" / "experimental" / "clone_or_update_repos.py"
        argv = [str(script), "--vendor-dir", str(tmp_path), "--repo", "boostprompt"]

        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(subprocess, "run", return_value=result),
            pytest.raises(SystemExit) as exit_info,
        ):
            runpy.run_path(str(script), run_name="__main__")

        assert exit_info.value.code == 0


class TestCodeHealthAnalyzer:
    @pytest.mark.parametrize(
        ("total_lines", "max_complexity", "docstring_cov", "has_contract", "expected"),
        [
            (20, 6, 1.0, False, 10.0),
            (21, 7, 0.5, False, 5.5),
            (201, 10, 1.0, True, 7.5),
            (501, 11, 1.0, True, 5.0),
            (501, 11, -10.0, False, 0.0),
            (1, 1, 2.0, True, 10.0),
        ],
    )
    def test_compute_health_score_covers_penalties_and_clamps(
        self, total_lines, max_complexity, docstring_cov, has_contract, expected
    ):
        assert CodeHealthAnalyzer().compute_health_score(
            total_lines, max_complexity, docstring_cov, has_contract
        ) == expected


class TestContextualASTChunker:
    def test_returns_raw_chunk_for_invalid_syntax(self):
        source = "def broken(:\n    pass"
        assert ContextualASTChunker().chunk_python_source(source, "broken.py") == [
            {
                "type": "raw",
                "start_line": 1,
                "end_line": 2,
                "content": source,
            }
        ]

    def test_chunks_function_async_function_and_class_but_ignores_other_nodes(self):
        source = (
            "VALUE = 1\n"
            "\n"
            "def regular():\n"
            "    return VALUE\n"
            "\n"
            "async def asynchronous():\n"
            "    return VALUE\n"
            "\n"
            "class Example:\n"
            "    pass\n"
        )

        chunks = ContextualASTChunker().chunk_python_source(source, "example.py")

        assert [chunk["name"] for chunk in chunks] == ["regular", "asynchronous", "Example"]
        assert [chunk["type"] for chunk in chunks] == ["function", "function", "class"]
        assert chunks[0] == {
            "name": "regular",
            "type": "function",
            "start_line": 3,
            "end_line": 4,
            "content": "def regular():\n    return VALUE",
        }

    def test_returns_module_chunk_when_no_semantic_nodes_exist(self):
        source = "VALUE = 1\n"
        assert ContextualASTChunker().chunk_python_source(source) == [
            {
                "type": "module",
                "start_line": 1,
                "end_line": 1,
                "content": source,
            }
        ]
