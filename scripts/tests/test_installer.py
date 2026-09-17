"""
O que e: Suite de testes automatizados para o instalador e entrypoint (install.ps1 e CLI shims).
Responsabilidade: Validar deteccao de pre-requisitos (Git, Python >= 3.11 com launcher, Node >= 18, npm, npx),
geracao de shims squad.cmd e agent-squad.cmd, propagacao de exit code, idempotencia e clean install.
Pra que serve: Garantir que o entrypoint Zero-to-Hero funcione deterministicamente em sistemas Windows limpos.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL_PS1 = ROOT / "install.ps1"


def test_install_ps1_exists():
    """Valida presenca fisica do script install.ps1 na raiz do projeto."""
    assert INSTALL_PS1.is_file(), f"install.ps1 nao encontrado em {INSTALL_PS1}"


def test_install_ps1_syntax():
    """Valida que install.ps1 nao contem erros de sintaxe no parser PowerShell."""
    powershell_bin = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell_bin:
        pytest.skip("PowerShell nao disponivel no PATH deste ambiente.")

    # Script inline que usa o parser nativo da AST do PowerShell
    check_code = (
        "$tokens = $null; $errs = $null;\n"
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{INSTALL_PS1.as_posix()}', [ref]$tokens, [ref]$errs);\n"
        "if ($errs -and $errs.Count -gt 0) {\n"
        "    foreach ($e in $errs) { [Console]::Error.WriteLine($e.Message) }\n"
        "    exit 1\n"
        "}\n"
        "exit 0\n"
    )

    res = subprocess.run(
        [powershell_bin, "-NoProfile", "-NonInteractive", "-Command", check_code],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Erros de sintaxe em install.ps1:\n{res.stderr}\n{res.stdout}"


def test_install_ps1_contains_required_sections():
    """Valida presenca das secoes obrigatorias de pre-requisitos e shims no install.ps1."""
    content = INSTALL_PS1.read_text(encoding="utf-8")

    # 1. Checagem de Git
    assert "git --version" in content or "Get-Command git" in content
    # 2. Separacao estrita de launcher $pythonExe e $pythonArgs
    assert "$pythonExe" in content
    assert "$pythonArgs" in content
    # 3. Checagem de Node >= 18, npm e npx
    assert "Get-Command node" in content
    assert "Get-Command npm" in content
    assert "Get-Command npx" in content
    assert "18" in content
    # 4. Invocacao do setup_environment.py com splatting de args
    assert "setup_environment.py" in content
    assert "$setupArgs" in content
    # 5. Geracao de shims squad.cmd e agent-squad.cmd
    assert "squad.cmd" in content
    assert "agent-squad.cmd" in content
    assert "active_runtime.json" in content
    # 6. Propagacao de exit code
    assert "$setupExitCode" in content or "$LASTEXITCODE" in content
    assert "exit $setupExitCode" in content or "exit $LASTEXITCODE" in content


def test_node_version_validation_logic():
    """Valida a regra de compatibilidade de versoes do Node.js (>= 18 obrigatorio)."""
    def check_node_version(version_str: str) -> bool:
        match = re.search(r"v(\d+)\.", version_str)
        if not match:
            return False
        major = int(match.group(1))
        return major >= 18

    assert check_node_version("v18.0.0") is True
    assert check_node_version("v20.11.1") is True
    assert check_node_version("v22.0.0") is True
    assert check_node_version("v24.18.0") is True
    assert check_node_version("v16.20.2") is False
    assert check_node_version("v14.17.0") is False
    assert check_node_version("invalid") is False


def test_python_launcher_separation_logic():
    """Valida que o launcher py propaga argumentos e executavel separadamente."""
    def build_invocation(python_exe: str, python_args: list[str], script_path: str) -> list[str]:
        cmd = [python_exe]
        if python_args:
            cmd.extend(python_args)
        cmd.append(script_path)
        return cmd

    # Caso 1: python padrao
    cmd1 = build_invocation("python", [], "scripts/setup_environment.py")
    assert cmd1 == ["python", "scripts/setup_environment.py"]

    # Caso 2: py launcher com versao especifica
    cmd2 = build_invocation("py", ["-3.12"], "scripts/setup_environment.py")
    assert cmd2 == ["py", "-3.12", "scripts/setup_environment.py"]

    # Caso 3: py launcher com versao 3.11
    cmd3 = build_invocation("py", ["-3.11"], "scripts/setup_environment.py")
    assert cmd3 == ["py", "-3.11", "scripts/setup_environment.py"]


def test_database_init_on_clean_state(tmp_path):
    """Valida criacao limpa do banco SQLite a partir do schema.sql (Zero-to-Hero fresh install)."""
    schema_file = ROOT / "banco" / "schema.sql"
    assert schema_file.is_file(), f"schema.sql ausente em {schema_file}"

    db_dir = tmp_path / "banco"
    db_dir.mkdir()
    target_db = db_dir / "squad.db"

    assert not target_db.exists()

    schema_sql = schema_file.read_text(encoding="utf-8")
    conn = sqlite3.connect(target_db)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()

    assert target_db.is_file()
    assert target_db.stat().st_size > 0

    # Valida tabelas canonicas
    conn = sqlite3.connect(target_db)
    try:
        cursor = conn.cursor()
        tables = {row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "symbols" in tables
        assert "dependencies" in tables
        assert "token_metrics" in tables
        assert "trajectory_logs" in tables
    finally:
        conn.close()


def test_database_init_idempotency(tmp_path):
    """Valida que executar a inicializacao do banco multiplas vezes preserva dados e nao quebra."""
    schema_file = ROOT / "banco" / "schema.sql"
    db_dir = tmp_path / "banco"
    db_dir.mkdir()
    target_db = db_dir / "squad.db"
    schema_sql = schema_file.read_text(encoding="utf-8")

    # Primeira inicializacao
    conn = sqlite3.connect(target_db)
    conn.executescript(schema_sql)
    conn.execute(
        "INSERT INTO symbols (project_id, file_path, name, kind, line_number, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("p1", "test.py", "func_a", "function", 10, 123456.0),
    )
    conn.commit()
    conn.close()

    # Segunda inicializacao (idempotente)
    conn = sqlite3.connect(target_db)
    conn.executescript(schema_sql)
    conn.commit()

    # Verifica que o dado da primeira execucao permanece intacto
    cursor = conn.cursor()
    cursor.execute("SELECT name, kind, line_number FROM symbols WHERE project_id='p1'")
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0] == ("func_a", "function", 10)


def test_cli_shim_generation_and_execution(tmp_path):
    """Gera o shim batch squad.cmd e valida resolucao de SQUAD_RUNTIME e propagacao de saida."""
    if sys.platform != "win32":
        pytest.skip("Testes de shim .cmd aplicaveis apenas no Windows.")

    # Cria mock de runtime
    runtime_dir = tmp_path / "mock_runtime"
    runtime_dir.mkdir()
    scripts_dir = runtime_dir / "scripts"
    scripts_dir.mkdir()

    # Mock agent_squad.py que reflete o primeiro argumento e o exit code
    mock_agent_squad = scripts_dir / "agent_squad.py"
    mock_agent_squad.write_text(
        "import sys\n"
        "if len(sys.argv) > 1 and sys.argv[1] == '--fail':\n"
        "    print('Simulated failure', file=sys.stderr)\n"
        "    sys.exit(42)\n"
        "print(f'MOCK AGENT SQUAD OK: {sys.argv[1:]}')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    # Mock de venv com python.exe
    venv_scripts = runtime_dir / ".venv" / "Scripts"
    venv_scripts.mkdir(parents=True)
    mock_python_bat = venv_scripts / "python.exe"
    # No Windows, criar um link ou usar o python real
    real_py = Path(sys.executable)
    shutil.copy2(real_py, mock_python_bat)

    # Mock do active_runtime.json
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    active_runtime_json = config_dir / "active_runtime.json"
    active_runtime_json.write_text(f'{{"runtime": "{runtime_dir.as_posix()}"}}', encoding="utf-8")

    # Gera conteudo do squad.cmd apontando para esse runtime via SQUAD_RUNTIME
    shim_path = tmp_path / "squad.cmd"
    shim_code = (
        "@echo off\n"
        "setlocal\n"
        f'if "%SQUAD_RUNTIME%"=="" set "SQUAD_RUNTIME={runtime_dir}"\n'
        'if exist "%SQUAD_RUNTIME%\\.venv\\Scripts\\python.exe" (\n'
        '    "%SQUAD_RUNTIME%\\.venv\\Scripts\\python.exe" "%SQUAD_RUNTIME%\\scripts\\agent_squad.py" %*\n'
        ") else (\n"
        f'    "{sys.executable}" "%SQUAD_RUNTIME%\\scripts\\agent_squad.py" %*\n'
        ")\n"
        "endlocal & exit /b %ERRORLEVEL%\n"
    )
    shim_path.write_text(shim_code, encoding="ascii")

    # 1. Execucao de sucesso
    res_ok = subprocess.run(
        ["cmd.exe", "/c", str(shim_path), "test-command", "--flag"],
        capture_output=True,
        text=True,
    )
    assert res_ok.returncode == 0
    assert "MOCK AGENT SQUAD OK: ['test-command', '--flag']" in res_ok.stdout

    # 2. Execucao com erro e propagacao de exit code 42
    res_err = subprocess.run(
        ["cmd.exe", "/c", str(shim_path), "--fail"],
        capture_output=True,
        text=True,
    )
    assert res_err.returncode == 42
    assert "Simulated failure" in res_err.stderr


def test_real_squad_cmd_shim_executes_help():
    """Valida execucao real do squad.cmd na raiz do projeto com o python do ambiente."""
    if sys.platform != "win32":
        pytest.skip("Testes de squad.cmd aplicaveis apenas no Windows.")

    user_shim = Path.home() / ".agents_squad" / "bin" / "squad.cmd"
    squad_cmd = user_shim if user_shim.is_file() else ROOT / "squad.cmd"
    if not squad_cmd.is_file():
        pytest.skip("squad.cmd ainda nao foi gerado em ~/.agents_squad/bin ou na raiz.")

    env = os.environ.copy()
    env["SQUAD_RUNTIME"] = str(ROOT)

    res = subprocess.run(
        ["cmd.exe", "/c", str(squad_cmd), "-h"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    assert res.returncode == 0
    assert "Control plane local do squad de agentes" in res.stdout
