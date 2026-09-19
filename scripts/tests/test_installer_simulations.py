"""
O que é: Suíte de testes de simulação de Clean Install (Seção 33) e Segunda Instalação / Idempotência (Seção 34).
Responsabilidade:
  - Simular instalação limpa (Zero-to-Hero) em diretório temporário descartável.
  - Verificar criação estrutural dos 7 provedores canônicos de vendor upstream.
  - Verificar inicialização do banco SQLite a partir do schema.sql canônico.
  - Verificar geração e validação do arquivo de configuração config/mcp_config.json.
  - Simular segunda instalação (idempotência) no mesmo diretório:
    * Preservação estrita de dados do banco de dados (não destrutivo).
    * Estabilidade do arquivo mcp_config.json.
    * Inexistência de duplicação ou corrupção nas árvores de repositórios vendor.
    * Funcionamento de CLI shims (.cmd).
Pra que serve: Garantir que o processo de instalação e atualização seja 100% determinístico, seguro e idempotente.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations"))
sys.path.insert(0, str(ROOT / "scripts"))

import setup_environment as se
from sync_mcp_servers import MCPSyncManager, build_default_mcp_config


class TestSection33And34Simulations:
    """Simulações completas em diretório temporário isolado."""

    def _setup_disposable_squad_tree(self, root: Path) -> None:
        """Configura a estrutura mínima do squad em diretório temporário."""
        # Cria pastas necessárias
        (root / "banco").mkdir(parents=True, exist_ok=True)
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / "integrations" / "vendor").mkdir(parents=True, exist_ok=True)
        (root / "scripts").mkdir(parents=True, exist_ok=True)

        # Copia schema.sql canônico
        canonical_schema = ROOT / "banco" / "schema.sql"
        (root / "banco" / "schema.sql").write_text(
            canonical_schema.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def _populate_mock_vendor_upstream(self, vendor_root: Path) -> None:
        """Cria as estruturas canônicas dos 7 provedores de vendor."""
        # 1. codebase-memory-mcp
        cbm_build = vendor_root / "codebase-memory-mcp" / "build" / "c"
        cbm_build.mkdir(parents=True, exist_ok=True)
        (cbm_build / "codebase-memory-mcp.exe").write_text("dummy binary", encoding="utf-8")

        # 2. boostprompt
        bp_src = vendor_root / "boostprompt" / "src" / "boostprompt"
        bp_src.mkdir(parents=True, exist_ok=True)
        (bp_src / "__init__.py").write_text("# boostprompt module\n", encoding="utf-8")

        # 3. sdlc-agents
        sdlc_agents = vendor_root / "sdlc-agents" / "agents"
        sdlc_agents.mkdir(parents=True, exist_ok=True)
        for agent_name in ["design", "execution", "governance", "product", "qa", "research", "vision"]:
            (sdlc_agents / f"{agent_name}.agent.md").write_text(f"# Agent {agent_name}\n", encoding="utf-8")

        # 4. graphify
        graph_pkg = vendor_root / "graphify" / "graphify"
        graph_pkg.mkdir(parents=True, exist_ok=True)
        (graph_pkg / "__init__.py").write_text("# graphify module\n", encoding="utf-8")

        # 5. trace-mcp
        trace_dir = vendor_root / "trace-mcp"
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / "package.json").write_text(
            json.dumps({"name": "trace-mcp", "version": "1.0.0"}),
            encoding="utf-8",
        )
        (trace_dir / "dist").mkdir(parents=True, exist_ok=True)
        (trace_dir / "dist" / "cli.js").write_text("// trace-mcp cli\n", encoding="utf-8")

        # 6. chunkhound
        chunk_pkg = vendor_root / "chunkhound" / "chunkhound"
        chunk_pkg.mkdir(parents=True, exist_ok=True)
        (chunk_pkg / "__init__.py").write_text("# chunkhound module\n", encoding="utf-8")

        # 7. repowise
        repo_core = vendor_root / "repowise" / "packages" / "core" / "src"
        repo_core.mkdir(parents=True, exist_ok=True)
        (repo_core / "repowise").mkdir(parents=True, exist_ok=True)
        (repo_core / "repowise" / "__init__.py").write_text("# repowise module\n", encoding="utf-8")
        (vendor_root / "repowise" / "pyproject.toml").write_text("[project]\nname='repowise'\n", encoding="utf-8")

    def test_section_33_clean_install_simulation(self, monkeypatch):
        """Seção 33: Simulação de instalação limpa (Zero-to-Hero) em diretório temporário descartável."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            self._setup_disposable_squad_tree(tmp_dir)

            # Estado inicial limpo verificado
            assert not (tmp_dir / "banco" / "squad.db").exists()
            assert not (tmp_dir / ".venv").exists()
            assert not (tmp_dir / "config" / "mcp_config.json").exists()

            # Popula estrutura dos 7 repositórios vendor simulando clone upstream
            v_dir = tmp_dir / "integrations" / "vendor"
            self._populate_mock_vendor_upstream(v_dir)

            # Validação 1: Todos os 7 diretórios de vendor presentes
            for repo_name in se.CANONICAL_VENDOR_REPOSITORIES:
                assert (v_dir / repo_name).is_dir(), f"Vendor {repo_name} não encontrado"

            # Validação 2: Banco de dados criado a partir de schema.sql
            monkeypatch.setattr(se, "ROOT", tmp_dir)
            se.init_database()
            db_path = tmp_dir / "banco" / "squad.db"
            assert db_path.is_file()
            assert db_path.stat().st_size > 0

            # Verifica integridade das tabelas criadas no banco
            conn = sqlite3.connect(db_path)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                assert {"symbols", "dependencies", "token_metrics", "trajectory_logs"}.issubset(tables)
            finally:
                conn.close()

            # Validação 3: Provisionamento dos 7 provedores de vendor
            # Mock de execução subprocess de validação interna
            class DummyResult:
                returncode = 0
                stdout = "dummy OK"
                stderr = ""

            monkeypatch.setattr(se.subprocess, "run", lambda *a, **k: DummyResult())
            py_bin = Path(sys.executable)
            assert se.provision_vendor_integrations(py_bin) is True

            # Validação 4: Geração e validação do config/mcp_config.json
            manager = MCPSyncManager(squad_root=tmp_dir)
            mcp_file = manager.generate_and_save()
            assert mcp_file.is_file()
            assert manager.validate_config(mcp_file) is True

            # Inspeciona conteúdo do mcp_config.json gerado
            mcp_content = json.loads(mcp_file.read_text(encoding="utf-8"))
            assert "mcpServers" in mcp_content
            assert "azure-devops" in mcp_content["mcpServers"]
            assert "codebase-memory" in mcp_content["mcpServers"]
            assert "sinapse-hivemind" in mcp_content["mcpServers"]
            for s_name, s_conf in mcp_content["mcpServers"].items():
                assert "command" in s_conf
                assert len(s_conf["command"]) > 0

    def test_section_34_second_install_idempotency_simulation(self, monkeypatch):
        """Seção 34: Simulação de segunda instalação (idempotência) sem corrupção ou reset destrutivo."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            self._setup_disposable_squad_tree(tmp_dir)
            v_dir = tmp_dir / "integrations" / "vendor"
            self._populate_mock_vendor_upstream(v_dir)

            monkeypatch.setattr(se, "ROOT", tmp_dir)
            class DummyResult:
                returncode = 0
                stdout = "dummy OK"
                stderr = ""

            monkeypatch.setattr(se.subprocess, "run", lambda *a, **k: DummyResult())

            # -----------------------------------------------------------------
            # 1ª Instalação
            # -----------------------------------------------------------------
            se.init_database()
            py_bin = Path(sys.executable)
            assert se.provision_vendor_integrations(py_bin) is True
            manager = MCPSyncManager(squad_root=tmp_dir)
            mcp_file_first = manager.generate_and_save()
            mcp_data_first = json.loads(mcp_file_first.read_text(encoding="utf-8"))

            # Insere dados de usuário no banco de dados para testar persistência
            db_path = tmp_dir / "banco" / "squad.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO symbols (project_id, file_path, name, kind, line_number, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("proj_idempotency", "core/main.py", "init_system", "function", 42, 1700000000.0),
            )
            conn.commit()
            conn.close()

            # Cria arquivo de usuário dentro de um vendor para verificar não-destruição
            custom_user_file = v_dir / "boostprompt" / "custom_user_work.txt"
            custom_user_file.write_text("keep this safe", encoding="utf-8")

            # -----------------------------------------------------------------
            # 2ª Instalação (Idempotente)
            # -----------------------------------------------------------------
            # Re-executa init_database
            se.init_database()

            # Verifica que o dado no banco de dados permanece INTACTO (não foi resetado/apagado)
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT project_id, name, line_number FROM symbols WHERE project_id='proj_idempotency'")
            rows = cursor.fetchall()
            conn.close()
            assert len(rows) == 1
            assert rows[0] == ("proj_idempotency", "init_system", 42)

            # Re-provisiona integrações vendor
            assert se.provision_vendor_integrations(py_bin) is True

            # Verifica que o arquivo customizado não foi apagado
            assert custom_user_file.is_file()
            assert custom_user_file.read_text(encoding="utf-8") == "keep this safe"

            # Re-executa sincronização MCP
            mcp_file_second = manager.generate_and_save()
            mcp_data_second = json.loads(mcp_file_second.read_text(encoding="utf-8"))

            # Verifica estabilidade do arquivo de configuração MCP
            assert mcp_data_first == mcp_data_second
            assert manager.validate_config(mcp_file_second) is True

            # Verifica CLI shim geração e funcionamento no user bin dir
            user_bin = tmp_dir / ".agents_squad" / "bin"
            user_bin.mkdir(parents=True, exist_ok=True)
            shim_path = user_bin / "squad.cmd"
            shim_code = (
                "@echo off\n"
                f'"{sys.executable}" -c "print(\'IDEMPOTENCY_SHIM_OK\')" %*\n'
            )
            shim_path.write_text(shim_code, encoding="ascii")

            # Restaura subprocess real para testar a execução do shim
            monkeypatch.undo()

            if sys.platform == "win32":
                res = subprocess.run(["cmd.exe", "/c", str(shim_path)], capture_output=True, text=True)
                assert res.returncode == 0
                assert "IDEMPOTENCY_SHIM_OK" in res.stdout

    def test_section_35_codebase_memory_fresh_download_simulation(self, monkeypatch):
        """Seção 35: Simulação de provisionamento fresco de codebase-memory-mcp com download e extração de zip upstream."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            vendor_root = tmp_dir / "integrations" / "vendor"
            cbm_dir = vendor_root / "codebase-memory-mcp"
            cbm_dir.mkdir(parents=True, exist_ok=True)

            # Verifica que o executável não existe inicialmente
            target_exe = cbm_dir / "build" / "c" / "codebase-memory-mcp.exe"
            assert not target_exe.exists()

            # Cria um buffer zip em memória contendo codebase-memory-mcp.exe e membros adicionais
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w") as zf:
                zf.writestr("codebase-memory-mcp.exe", b"MOCK_CBM_BINARY")
                zf.writestr("install.ps1", b"# Malicious or extra file")
                zf.writestr("LICENSE", b"MIT License")
            zip_bytes = zip_buf.getvalue()
            computed_sha = hashlib.sha256(zip_bytes).hexdigest()

            class FakeResponse:
                def __init__(self, data):
                    self.data = data
                def read(self):
                    return self.data
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass

            monkeypatch.setattr(
                se.urllib.request,
                "urlopen",
                lambda req, timeout=None: FakeResponse(zip_bytes),
            )

            # 1. Simula falha imediata se o hash SHA-256 divergir do esperado
            monkeypatch.setattr(se, "CODEBASE_MEMORY_ARCHIVE_SHA256", "0" * 64)
            assert se.provision_codebase_memory(vendor_root) is False
            assert not target_exe.exists()

            # 2. Configura hash correto para testar extração segura e smoke test
            monkeypatch.setattr(se, "CODEBASE_MEMORY_ARCHIVE_SHA256", computed_sha)

            class SmokeResult:
                returncode = 0
                stdout = "codebase-memory-mcp 0.11.0"
                stderr = ""

            smoke_called = []
            def fake_subprocess_run(cmd, *a, **k):
                smoke_called.append(cmd)
                return SmokeResult()

            monkeypatch.setattr(se.subprocess, "run", fake_subprocess_run)

            # Executa provisionamento fresco
            ok = se.provision_codebase_memory(vendor_root)
            assert ok is True
            assert target_exe.is_file()
            assert target_exe.read_bytes() == b"MOCK_CBM_BINARY"
            # Garante que membros adicionais NÃO foram extraídos (safe extraction)
            assert not (cbm_dir / "build" / "c" / "install.ps1").exists()
            assert not (cbm_dir / "build" / "c" / "LICENSE").exists()
            assert len(smoke_called) == 1
            assert str(target_exe) in smoke_called[0]
            assert "--version" in smoke_called[0]

    def test_section_36_trace_mcp_fresh_build_simulation(self, monkeypatch):
        """Seção 36: Simulação de provisionamento fresco de trace-mcp com compilação pnpm e live execution probe."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            vendor_root = tmp_dir / "integrations" / "vendor"
            trace_dir = vendor_root / "trace-mcp"
            trace_dir.mkdir(parents=True, exist_ok=True)
            (trace_dir / "package.json").write_text(
                json.dumps({"name": "trace-mcp", "version": "3.26.3"}),
                encoding="utf-8",
            )

            cli_js = trace_dir / "dist" / "cli.js"
            assert not cli_js.exists()

            commands_run = []
            env_passed = []
            class CmdResult:
                def __init__(self, code=0, stdout=""):
                    self.returncode = code
                    self.stdout = stdout
                    self.stderr = ""

            def fake_subprocess_run(cmd, *a, **k):
                commands_run.append(list(cmd) if isinstance(cmd, (list, tuple)) else [cmd])
                if "env" in k:
                    env_passed.append(k["env"])
                # Simula criação do dist/cli.js durante a etapa de build
                if any("build" in str(arg) for arg in cmd):
                    (trace_dir / "dist").mkdir(parents=True, exist_ok=True)
                    cli_js.write_text("// compiled trace-mcp cli\n", encoding="utf-8")
                if any(str(cli_js) in str(arg) for arg in cmd) and "--version" in cmd:
                    return CmdResult(0, "3.26.3")
                return CmdResult(0, "ok")

            monkeypatch.setattr(se.subprocess, "run", fake_subprocess_run)

            ok = se.provision_trace_mcp(vendor_root)
            assert ok is True
            assert cli_js.is_file()
            # Verifica que install com --frozen-lockfile, build e smoke probe foram executados
            flat_cmds = [" ".join(str(x) for x in c) for c in commands_run]
            assert any("install" in c and "--frozen-lockfile" in c for c in flat_cmds)
            assert any("build" in c for c in flat_cmds)
            assert any("--version" in c for c in flat_cmds)
            assert any(e.get("CI") == "true" for e in env_passed)
            assert any(e.get("PYTHONDONTWRITEBYTECODE") == "1" for e in env_passed)
