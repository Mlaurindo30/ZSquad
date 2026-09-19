"""
O que é: Suíte de testes automatizados para propagação de falhas do instalador (Seção 35).
Responsabilidade: Validar que todos os 5 cenários de falha canônicos falham de forma segura e controlada:
  - Caso A: Origem Git incorreta / divergente -> falha de forma segura sem corromper.
  - Caso B: Diretório alvo existe mas não é repositório Git -> falha de forma segura.
  - Caso C: Falha no clone canônico upstream -> instalador falha com código de saída 1.
  - Caso D: Falha no provisionamento do executável codebase-memory -> instalador falha com código 1.
  - Caso E: Comando ou servidor MCP gerado ausente/inválido -> validação falha com código 1.
Pra que serve: Garantir robustez e comportamento determinístico em falhas no ciclo Zero-to-Hero.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations"))
sys.path.insert(0, str(ROOT / "scripts"))

import clone_or_update_repos
from clone_or_update_repos import RepoSpec, clone_or_update
import setup_environment as se
from sync_mcp_servers import MCPSyncManager, build_default_mcp_config, main as sync_mcp_main


class TestSection35FailurePropagation:
    """Valida os 5 cenários de propagação de falhas exigidos na Seção 35."""

    # =========================================================================
    # CASO A: Wrong Git origin -> fails safely
    # =========================================================================
    def test_failure_case_a_wrong_git_origin_fails_safely(self, tmp_path):
        """Caso A: Se o remote origin divergir da URL canônica esperada, a atualização deve falhar com segurança."""
        repo = RepoSpec("boostprompt", "https://github.com/zen-quad/boostprompt.git", "BoostPrompt")
        target_dir = tmp_path / "boostprompt"
        (target_dir / ".git").mkdir(parents=True)
        (target_dir / "preserve_me.txt").write_text("critical data", encoding="utf-8")

        # Mock de git remote get-url origin retornando URL de outro repositório
        wrong_origin_res = SimpleNamespace(
            returncode=0,
            stdout="https://github.com/attacker/malicious-repo.git\n",
            stderr="",
        )

        with mock.patch.object(clone_or_update_repos.subprocess, "run", return_value=wrong_origin_res):
            name, success, msg = clone_or_update(repo, tmp_path)

        assert name == "boostprompt"
        assert success is False
        assert "Origin mismatch" in msg
        assert "attacker" in msg
        # Verifica que arquivos locais existentes não foram destruídos
        assert (target_dir / "preserve_me.txt").read_text(encoding="utf-8") == "critical data"

    # =========================================================================
    # CASO B: Target directory exists but is not Git -> fails safely
    # =========================================================================
    def test_failure_case_b_target_directory_exists_not_git_fails_safely(self, tmp_path):
        """Caso B: Se o diretório alvo já existir mas não for um repositório git, deve falhar com segurança."""
        repo = RepoSpec("sdlc-agents", "https://github.com/zen-quad/sdlc-agents.git", "SDLC Agents")
        target_dir = tmp_path / "sdlc-agents"
        target_dir.mkdir(parents=True)
        marker_file = target_dir / "existing_unrelated_content.txt"
        marker_file.write_text("important existing content", encoding="utf-8")

        name, success, msg = clone_or_update(repo, tmp_path)

        assert name == "sdlc-agents"
        assert success is False
        assert "não é um repositório git válido" in msg
        # Verifica integridade do conteúdo pré-existente
        assert marker_file.is_file()
        assert marker_file.read_text(encoding="utf-8") == "important existing content"

    # =========================================================================
    # CASO C: Canonical clone failure -> installer fails
    # =========================================================================
    def test_failure_case_c_canonical_clone_failure_aborts_installer(self, tmp_path, monkeypatch):
        """Caso C: Se a clonagem de qualquer repositório upstream canônico falhar, o instalador deve abortar."""
        monkeypatch.setattr(se, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv", ["setup_environment.py"])

        # Mock: sync_vendor_repositories retorna False (falha de clone)
        monkeypatch.setattr(se, "sync_vendor_repositories", lambda: False)

        with pytest.raises(SystemExit) as exc_info:
            se.main()

        assert exc_info.value.code == 1

    def test_failure_case_c_clone_cli_exit_code_propagation(self, tmp_path):
        """Caso C (CLI): clone_or_update_repos.main retorna código 1 se algum clone canônico falhar."""
        failed_res = SimpleNamespace(returncode=128, stdout="", stderr="fatal: repository not found\n")
        with mock.patch.object(clone_or_update_repos.subprocess, "run", return_value=failed_res):
            rc = clone_or_update_repos.main(["--vendor-dir", str(tmp_path)])
            assert rc == 1

    # =========================================================================
    # CASO D: Codebase-memory executable provisioning fails -> installer fails
    # =========================================================================
    def test_failure_case_d_codebase_memory_provisioning_failure_aborts_installer(self, tmp_path, monkeypatch):
        """Caso D: Se o executável do codebase-memory-mcp não estiver presente ou falhar no smoke test, o instalador falha."""
        monkeypatch.setattr(se, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv", ["setup_environment.py"])

        # Simula repositórios sincronizados
        monkeypatch.setattr(se, "sync_vendor_repositories", lambda: True)
        # Cria diretórios canônicos para evitar guarda de diretório ausente
        v_dir = tmp_path / "integrations" / "vendor"
        for r in se.CANONICAL_VENDOR_REPOSITORIES:
            (v_dir / r).mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(se, "ensure_virtualenv", lambda: Path(sys.executable))
        monkeypatch.setattr(se, "init_database", lambda: None)

        # Simula falha específica no provision_codebase_memory
        monkeypatch.setattr(se, "provision_codebase_memory", lambda *a: False)

        with pytest.raises(SystemExit) as exc_info:
            se.main()

        assert exc_info.value.code == 1

    def test_failure_case_d_codebase_memory_smoke_test_failure(self, tmp_path):
        """Caso D (Smoke Probe): Executável existe mas retorna exit code de erro no probe --version."""
        v_dir = tmp_path / "integrations" / "vendor"
        cbm_exe = v_dir / "codebase-memory-mcp" / "build" / "c" / "codebase-memory-mcp.exe"
        cbm_exe.parent.mkdir(parents=True)
        cbm_exe.write_text("dummy binary", encoding="utf-8")

        mock_smoke_failure = SimpleNamespace(returncode=1, stdout="", stderr="Segmentation fault")
        with mock.patch.object(se.subprocess, "run", return_value=mock_smoke_failure):
            assert se.provision_codebase_memory(v_dir) is False

    # =========================================================================
    # CASO E: Generated MCP command missing -> fails
    # =========================================================================
    def test_failure_case_e_generated_mcp_command_missing_fails_validation(self, tmp_path):
        """Caso E: Se a configuração MCP gerada contiver servidor sem campo 'command', a validação falha."""
        manager = MCPSyncManager(squad_root=tmp_path)
        config_file = tmp_path / "config" / "mcp_config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)

        # Configuração corrompida: servidor sem o campo obrigatório 'command'
        corrupted_data = {
            "mcpServers": {
                "broken-mcp": {
                    "args": ["foo"],
                    "description": "Invalid MCP without command"
                }
            }
        }
        config_file.write_text(json.dumps(corrupted_data), encoding="utf-8")

        assert manager.validate_config(config_file) is False

    def test_failure_case_e_mcp_sync_cli_returns_exit_code_1_on_invalid_config(self, tmp_path):
        """Caso E (CLI): sync_mcp_servers.main() retorna exit code 1 se a validação falhar."""
        bad_file = tmp_path / "config" / "bad_mcp.json"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text("{}", encoding="utf-8")

        with mock.patch.object(MCPSyncManager, "generate_and_save", return_value=bad_file):
            rc = sync_mcp_main(["--output", str(bad_file)])
            assert rc == 1
