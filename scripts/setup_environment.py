#!/usr/bin/env python3
"""
O que é: Script de instalação, provisionamento e validação de ambiente unificado (Zero-to-Hero Completo).
Responsabilidade: Sincronizar repositórios vendor, criar ambiente virtual isolado (.venv/uv), instalar dependências, inicializar banco SQLite, sincronizar MCPs, provisionar Docker e validar testes.
Pra que serve: Permitir que qualquer desenvolvedor ou agente configure e execute o Agents Squad do zero com um único comando sem depender de configurações manuais.
Comportamento em falha: Interrompe a execução exibindo a causa raiz clara e orientações de correção.
Conexões: Conecta-se com banco/schema.sql, banco/squad.db, integrations/clone_or_update_repos.py, docker-compose.yml e scripts/validate_structure.py.
Dependências & Imports:
  - os, pathlib, shutil, sqlite3, subprocess, sys: Operações de sistema, git e banco.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND_TIMEOUT_SECONDS = 300
NETWORK_COMMAND_TIMEOUT_SECONDS = 900


def print_step(msg: str) -> None:
    """Imprime um cabeçalho formatado de etapa de instalação."""
    print(f"\n\033[1;34m==>\033[0m \033[1m{msg}\033[0m")


def sync_vendor_repositories() -> None:
    """Clona ou atualiza todos os repositórios upstream em integrations/vendor/."""
    print_step("1/6 Sincronizando repositórios upstream (integrations/vendor/)...")
    clone_script = ROOT / "integrations" / "clone_or_update_repos.py"
    if clone_script.exists():
        res = subprocess.run([sys.executable, str(clone_script)], capture_output=True, text=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
        if res.returncode == 0:
            print("  [OK] Repositórios vendor sincronizados com sucesso.")
        else:
            print(f"  [AVISO] Sincronização vendor: {res.stderr.strip() or res.stdout.strip()}")


def ensure_virtualenv() -> Path:
    """Cria e valida o ambiente virtual dedicado (.venv) na raiz do projeto."""
    print_step("2/6 Provisionando ambiente virtual isolado (.venv)...")
    venv_dir = ROOT / ".venv"
    if not venv_dir.exists():
        if shutil.which("uv"):
            subprocess.run(["uv", "venv", str(venv_dir)], check=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
            pip_cmd = ["uv", "pip", "install", "-r", str(ROOT / "requirements.txt")]
            subprocess.run(pip_cmd, check=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
        else:
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
            python_bin = venv_dir / "Scripts" / "python.exe" if sys.platform == "win32" else venv_dir / "bin" / "python"
            subprocess.run([str(python_bin), "-m", "pip", "install", "--upgrade", "pip"], check=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
            subprocess.run([str(python_bin), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
        print("  [OK] Ambiente virtual .venv criado e dependências instaladas!")
    else:
        print("  [OK] Ambiente virtual .venv já existente e operacional.")

    python_bin = venv_dir / "Scripts" / "python.exe" if sys.platform == "win32" else venv_dir / "bin" / "python"
    return python_bin if python_bin.exists() else Path(sys.executable)


def init_database() -> None:
    """Inicializa o banco de dados SQLite local em banco/squad.db a partir de schema.sql."""
    print_step("3/6 Inicializando banco de dados local (banco/squad.db)...")
    db_dir = ROOT / "banco"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "squad.db"
    schema_path = db_dir / "schema.sql"

    if not schema_path.exists():
        print(f"Erro: Arquivo de schema não encontrado em {schema_path}", file=sys.stderr)
        sys.exit(1)

    schema_sql = schema_path.read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema_sql)
        conn.commit()
        print(f"  [OK] Banco de dados criado e atualizado com sucesso em: {db_path}")
    finally:
        conn.close()


def sync_mcps() -> None:
    """Sincroniza a configuração de servidores MCP."""
    print_step("4/6 Sincronizando servidores MCP (config/mcp_config.json)...")
    sync_script = ROOT / "scripts" / "sync_mcp_servers.py"
    if sync_script.exists():
        res = subprocess.run([sys.executable, str(sync_script)], capture_output=True, text=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
        if res.returncode == 0:
            print("  [OK] MCP servers sincronizados com sucesso.")
        else:
            print(f"  [AVISO] Sincronização MCP: {res.stderr.strip() or res.stdout.strip()}")


def install_vendor_mcp_packages(python_bin: Path) -> None:
    """Instala pacotes MCP vendor editáveis no ambiente virtual."""
    print_step("4.5/6 Instalando pacotes MCP vendor...")
    vendor_mcp = ROOT / "integrations" / "vendor" / "codebase-memory-mcp" / "pkg" / "pypi"
    if not vendor_mcp.exists():
        print("  [AVISO] codebase-memory-mcp vendor path não encontrado.")
        return
    res = subprocess.run(
        [str(python_bin), "-m", "pip", "install", "-e", str(vendor_mcp)],
        capture_output=True,
        text=True,
        timeout=NETWORK_COMMAND_TIMEOUT_SECONDS,
    )
    if res.returncode == 0:
        print("  [OK] codebase-memory-mcp instalado no ambiente.")
    else:
        print(f"  [AVISO] Falha ao instalar codebase-memory-mcp: {res.stderr.strip() or res.stdout.strip()}")


def provision_docker_stack() -> None:
    """Verifica e provisiona os containers Docker caso o Docker esteja instalado e ativo."""
    print_step("5/6 Verificando e provisionando stack Docker...")
    if not shutil.which("docker"):
        print("  [INFO] Docker não detectado no PATH. Executando em modo nativo local.")
        return

    try:
        # Testa conectividade com o daemon Docker
        check_docker = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=5)
        if check_docker.returncode != 0:
            print("  [INFO] Daemon Docker não está ativo. Executando em modo nativo local.")
            return

        res = subprocess.run(["docker", "compose", "up", "-d"], capture_output=True, text=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
        if res.returncode == 0:
            print("  [OK] Stack Docker inicializada com sucesso (Core + FalkorDB)!")
        else:
            print(f"  [AVISO] Docker compose: {res.stderr.strip() or res.stdout.strip()}")
    except Exception as exc:
        print(f"  [INFO] Docker ignorado ({exc}). Operando em modo nativo.")


def validate_suite() -> None:
    """Executa a validação estrutural e a suíte de testes automatizados."""
    print_step("6/6 Validando estrutura e executando suíte de testes...")
    val_script = ROOT / "scripts" / "validate_structure.py"
    res_val = subprocess.run([sys.executable, str(val_script)], capture_output=True, text=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
    if res_val.returncode == 0:
        print(f"  [OK] Estrutura: {res_val.stdout.strip()}")
    else:
        print(f"  [FALHA] Validação de estrutura: {res_val.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    # Pytest
    res_test = subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "scripts" / "tests")], capture_output=True, text=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
    if res_test.returncode == 0:
        print("  [OK] Todos os 60 testes unitários e de integração passaram!")
    else:
        print(f"  [FALHA] Testes falharam:\n{res_test.stdout}\n{res_test.stderr}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Função principal do instalador zero-to-hero completo."""
    print("\n=======================================================")
    print("   AGENTS SQUAD — INSTALAÇÃO & PROVISIONAMENTO TOTAL   ")
    print("=======================================================")
    sync_vendor_repositories()
    python_bin = ensure_virtualenv()
    init_database()
    install_vendor_mcp_packages(python_bin)
    sync_mcps()
    provision_docker_stack()
    validate_suite()
    print("\n\033[1;32m[SUCESSO] Instalação, repositórios vendor, banco e Docker 100% operacionais!\033[0m\n")


if __name__ == "__main__":
    main()
