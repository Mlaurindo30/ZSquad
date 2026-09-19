#!/usr/bin/env python3
"""
O que é: Script de instalação, provisionamento e validação de ambiente unificado (Zero-to-Hero Completo).
Responsabilidade: Sincronizar repositórios vendor, criar ambiente virtual isolado (.venv/uv), instalar dependências, inicializar banco SQLite, sincronizar MCPs, provisionar Docker e validar testes.
Pra que serve: Permitir que qualquer desenvolvedor ou agente configure e execute o Agents Squad do zero com um único comando sem depender de configurações manuais.
Comportamento em falha: Interrompe a execução exibindo a causa raiz clara e orientações de correção.
Conexões: Conecta-se com banco/schema.sql, banco/squad.db, integrations/clone_or_update_repos.py, docker-compose.yml e scripts/validate_structure.py.
Dependências & Imports:
  - json, os, pathlib, shutil, sqlite3, subprocess, sys: Operações de sistema, git e banco.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND_TIMEOUT_SECONDS = 300
NETWORK_COMMAND_TIMEOUT_SECONDS = 900
CODEBASE_MEMORY_VERSION = "0.11.0"
CODEBASE_MEMORY_ARCHIVE_SHA256 = "6eb6beaf261b19e419766e78baf93cbc3cf1c6338cff8fb7c0234859f96d1685"
CODEBASE_MEMORY_RELEASE_URL = (
    f"https://github.com/DeusData/codebase-memory-mcp/releases/download/v{CODEBASE_MEMORY_VERSION}/codebase-memory-mcp-windows-amd64.zip"
)
CBM_RELEASE_URL = CODEBASE_MEMORY_RELEASE_URL


def _python_env() -> dict[str, str]:
    """Retorna cópia das variáveis de ambiente com bloqueio de bytecode Python."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


CANONICAL_VENDOR_REPOSITORIES = [
    "boostprompt",
    "sdlc-agents",
    "graphify",
    "trace-mcp",
    "codebase-memory-mcp",
    "chunkhound",
    "repowise",
]


def print_step(msg: str) -> None:
    """Imprime um cabeçalho formatado de etapa de instalação."""
    print(f"\n\033[1;34m==>\033[0m \033[1m{msg}\033[0m")


def sync_vendor_repositories(python_bin: Path | None = None) -> bool:
    """Clona ou atualiza todos os repositórios upstream em integrations/vendor/."""
    print_step("1/6 Sincronizando repositórios upstream (integrations/vendor/)...")
    py_cmd = str(python_bin) if python_bin else sys.executable
    clone_script = ROOT / "integrations" / "clone_or_update_repos.py"
    if clone_script.exists():
        res = subprocess.run([py_cmd, str(clone_script)], env=_python_env(), capture_output=True, text=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
        if res.returncode == 0:
            vendor_dir = ROOT / "integrations" / "vendor"
            missing = [repo for repo in CANONICAL_VENDOR_REPOSITORIES if not (vendor_dir / repo).is_dir()]
            if missing and vendor_dir.exists():
                print(f"  [FALHA] Diretórios upstream obrigatórios ausentes após sincronização: {', '.join(missing)}")
                return False
            print("  [OK] Repositórios vendor sincronizados com sucesso.")
            return True
        else:
            print(f"  [FALHA] Sincronização vendor: {res.stderr.strip() or res.stdout.strip()}")
            return False
    else:
        print("  [FALHA] Script de sincronização de repositórios não encontrado.")
        return False




def ensure_virtualenv() -> Path:
    """Cria e valida o ambiente virtual dedicado (.venv) na raiz do projeto."""
    print_step("2/6 Provisionando ambiente virtual isolado (.venv)...")
    venv_dir = ROOT / ".venv"
    py_env = _python_env()
    if not venv_dir.exists():
        if shutil.which("uv"):
            subprocess.run(["uv", "venv", str(venv_dir)], env=py_env, check=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
        else:
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], env=py_env, check=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)

    python_bin = venv_dir / "Scripts" / "python.exe" if sys.platform == "win32" else venv_dir / "bin" / "python"
    if not python_bin.exists():
        python_bin = Path(sys.executable)

    req_file = ROOT / "requirements.txt"
    if req_file.exists():
        if shutil.which("uv"):
            subprocess.run(["uv", "pip", "install", "--python", str(python_bin), "-r", str(req_file)], env=py_env, check=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
        else:
            subprocess.run([str(python_bin), "-m", "pip", "install", "-r", str(req_file)], env=py_env, check=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)

    print("  [OK] Ambiente virtual .venv operacional e dependências sincronizadas!")
    return python_bin


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


def sync_mcps(python_bin: Path | None = None) -> None:
    """Sincroniza a configuração de servidores MCP."""
    print_step("4/6 Sincronizando servidores MCP (config/mcp_config.json)...")
    if python_bin is None:
        python_bin = Path(sys.executable)
    sync_script = ROOT / "scripts" / "sync_mcp_servers.py"
    if sync_script.exists():
        res = subprocess.run([str(python_bin), str(sync_script)], env=_python_env(), capture_output=True, text=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
        if res.returncode == 0:
            print("  [OK] MCP servers sincronizados com sucesso.")
        else:
            print(f"  [AVISO] Sincronização MCP: {res.stderr.strip() or res.stdout.strip()}")


def provision_codebase_memory(vendor_dir: Path) -> bool:
    """Valida a presença e execução do binário nativo do codebase-memory-mcp.

    Caso o binário esteja ausente em build/c/codebase-memory-mcp.exe, realiza o download
    do release canônico upstream em zip, valida a integridade estrita via hash SHA-256,
    extrai com segurança exclusivamente o executável e executa smoke test --version.
    """
    bin_name = "codebase-memory-mcp.exe" if sys.platform == "win32" else "codebase-memory-mcp"
    cbm_dir = vendor_dir / "codebase-memory-mcp"
    bin_path = cbm_dir / "build" / "c" / bin_name

    if not bin_path.is_file():
        alt_path = cbm_dir / "build" / "c" / "codebase-memory-mcp.exe"
        if alt_path.is_file():
            bin_path = alt_path
        else:
            print("  [*] codebase-memory-mcp: executável ausente em build/c, baixando release canônico upstream...")
            try:
                bin_path.parent.mkdir(parents=True, exist_ok=True)
                req = urllib.request.Request(
                    CODEBASE_MEMORY_RELEASE_URL,
                    headers={"User-Agent": "AgentSquad-Installer/1.0"},
                )
                with urllib.request.urlopen(req, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS) as resp:
                    zip_bytes = resp.read()

                actual_sha256 = hashlib.sha256(zip_bytes).hexdigest()
                if actual_sha256 != CODEBASE_MEMORY_ARCHIVE_SHA256:
                    print(
                        f"  [FALHA] INSTALLATION_FAILED: integridade SHA-256 de codebase-memory-mcp falhou!\n"
                        f"          Esperado: {CODEBASE_MEMORY_ARCHIVE_SHA256}\n"
                        f"          Obtido:   {actual_sha256}"
                    )
                    return False

                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                    target_member = "codebase-memory-mcp.exe"
                    if target_member not in zf.namelist():
                        if "codebase-memory-mcp" in zf.namelist():
                            target_member = "codebase-memory-mcp"
                        else:
                            print(
                                f"  [FALHA] codebase-memory-mcp: executável '{target_member}' não encontrado no zip do release upstream."
                            )
                            return False
                    bin_path.write_bytes(zf.read(target_member))
                    if sys.platform != "win32":
                        bin_path.chmod(0o755)
                print(f"  [OK] codebase-memory-mcp: executável extraído com sucesso em '{bin_path}'.")
            except Exception as exc:
                print(f"  [FALHA] codebase-memory-mcp: falha ao baixar ou extrair release upstream: {exc}")
                return False

    try:
        res = subprocess.run([str(bin_path), "--version"], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
        if res.returncode == 0:
            version_str = res.stdout.strip() or CODEBASE_MEMORY_VERSION
            print(f"  [OK] codebase-memory-mcp: binário validado ({version_str}).")
            return True
        else:
            err = res.stderr.strip() or res.stdout.strip()
            print(f"  [FALHA] codebase-memory-mcp: teste smoke --version falhou: {err}")
            return False
    except Exception as exc:
        print(f"  [FALHA] codebase-memory-mcp: exceção ao executar smoke probe: {exc}")
        return False


def provision_boostprompt(vendor_dir: Path, python_bin: Path) -> bool:
    """Valida o pacote Python / importação via src de boostprompt."""
    src_dir = vendor_dir / "boostprompt" / "src"
    pkg_dir = src_dir / "boostprompt"
    if not pkg_dir.is_dir():
        print(f"  [FALHA] boostprompt: diretório de código-fonte ausente em '{src_dir}'.")
        return False

    try:
        cmd = [
            str(python_bin),
            "-c",
            f"import sys; sys.path.insert(0, r'{src_dir}'); import boostprompt; print('boostprompt OK')",
        ]
        res = subprocess.run(cmd, env=_python_env(), capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
        if res.returncode == 0:
            print("  [OK] boostprompt: pacote Python validado via src.")
            return True
        else:
            err = res.stderr.strip() or res.stdout.strip()
            print(f"  [FALHA] boostprompt: falha ao importar pacote: {err}")
            return False
    except Exception as exc:
        print(f"  [FALHA] boostprompt: exceção ao validar pacote: {exc}")
        return False


def provision_sdlc_agents(vendor_dir: Path) -> bool:
    """Valida as 7 especificações de agentes SDLC em markdown."""
    agents_dir = vendor_dir / "sdlc-agents" / "agents"
    if not agents_dir.is_dir():
        print(f"  [FALHA] sdlc-agents: diretório de agentes ausente em '{agents_dir}'.")
        return False

    required_agents = ["design", "execution", "governance", "product", "qa", "research", "vision"]
    missing = [f"{name}.agent.md" for name in required_agents if not (agents_dir / f"{name}.agent.md").is_file()]
    if missing:
        print(f"  [FALHA] sdlc-agents: especificações obrigatórias ausentes: {', '.join(missing)}")
        return False

    print(f"  [OK] sdlc-agents: {len(required_agents)} especificações de agentes validadas.")
    return True


def provision_graphify(vendor_dir: Path, python_bin: Path) -> bool:
    """Valida o pacote de AST / grafo de conhecimento graphify."""
    pkg_dir = vendor_dir / "graphify"
    if not (pkg_dir / "graphify").is_dir() and not (pkg_dir / "pyproject.toml").is_file():
        print(f"  [FALHA] graphify: diretório de pacote ausente em '{pkg_dir}'.")
        return False

    try:
        cmd = [
            str(python_bin),
            "-c",
            f"import sys; sys.path.insert(0, r'{pkg_dir}'); import graphify; print('graphify OK')",
        ]
        res = subprocess.run(cmd, env=_python_env(), capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
        if res.returncode == 0:
            print("  [OK] graphify: pacote Python validado.")
            return True
        else:
            err = res.stderr.strip() or res.stdout.strip()
            print(f"  [FALHA] graphify: falha ao validar pacote: {err}")
            return False
    except Exception as exc:
        print(f"  [FALHA] graphify: exceção ao validar pacote: {exc}")
        return False


def provision_trace_mcp(vendor_dir: Path) -> bool:
    """Valida o pacote Node MCP trace-mcp, compilando via pnpm caso dist/cli.js esteja ausente e executando smoke test."""
    pkg_dir = vendor_dir / "trace-mcp"
    pkg_json = pkg_dir / "package.json"
    if not pkg_json.is_file():
        print(f"  [FALHA] trace-mcp: package.json ausente em '{pkg_dir}'.")
        return False

    try:
        manifest = json.loads(pkg_json.read_text(encoding="utf-8"))
        if not manifest.get("name"):
            print("  [FALHA] trace-mcp: package.json inválido (sem campo 'name').")
            return False
    except Exception as exc:
        print(f"  [FALHA] trace-mcp: exceção ao ler package.json: {exc}")
        return False

    cli_js = pkg_dir / "dist" / "cli.js"
    if not cli_js.is_file():
        print("  [*] trace-mcp: dist/cli.js ausente, executando pnpm install && pnpm run build...")
        pnpm_bin = shutil.which("pnpm")
        if pnpm_bin:
            install_cmd = [pnpm_bin, "install", "--frozen-lockfile"]
            build_cmd = [pnpm_bin, "run", "build"]
        else:
            npx_bin = shutil.which("npx") or "npx"
            install_cmd = [npx_bin, "pnpm", "install", "--frozen-lockfile"]
            build_cmd = [npx_bin, "pnpm", "run", "build"]

        try:
            pnpm_env = os.environ.copy()
            pnpm_env["CI"] = "true"
            pnpm_env["PYTHONDONTWRITEBYTECODE"] = "1"
            res_install = subprocess.run(
                install_cmd,
                cwd=str(pkg_dir),
                env=pnpm_env,
                capture_output=True,
                text=True,
                timeout=NETWORK_COMMAND_TIMEOUT_SECONDS,
            )
            if res_install.returncode != 0:
                err = res_install.stderr.strip() or res_install.stdout.strip()
                print(f"  [FALHA] trace-mcp: falha no pnpm install: {err}")
                return False

            res_build = subprocess.run(
                build_cmd,
                cwd=str(pkg_dir),
                env=pnpm_env,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
            if res_build.returncode != 0:
                err = res_build.stderr.strip() or res_build.stdout.strip()
                print(f"  [FALHA] trace-mcp: falha no pnpm run build: {err}")
                return False
        except Exception as exc:
            print(f"  [FALHA] trace-mcp: exceção durante build: {exc}")
            return False

        if not cli_js.is_file():
            print(f"  [FALHA] trace-mcp: dist/cli.js não foi gerado após build em '{cli_js}'.")
            return False

    node_bin = shutil.which("node") or "node"
    try:
        node_env = os.environ.copy()
        node_env["PYTHONDONTWRITEBYTECODE"] = "1"
        res_smoke = subprocess.run(
            [node_bin, str(cli_js), "--version"],
            cwd=str(pkg_dir),
            env=node_env,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        if res_smoke.returncode == 0:
            version_str = res_smoke.stdout.strip() or manifest.get("version", "0.0.0")
            print(f"  [OK] trace-mcp: pacote Node MCP validado ({manifest.get('name')} v{version_str}).")
            return True
        else:
            err = res_smoke.stderr.strip() or res_smoke.stdout.strip()
            print(f"  [FALHA] trace-mcp: teste smoke node dist/cli.js --version falhou: {err}")
            return False
    except Exception as exc:
        print(f"  [FALHA] trace-mcp: exceção ao executar smoke probe: {exc}")
        return False


def provision_chunkhound(vendor_dir: Path, python_bin: Path) -> bool:
    """Valida o pacote Rust/Python chunkhound."""
    pkg_dir = vendor_dir / "chunkhound"
    if not (pkg_dir / "chunkhound").is_dir() and not (pkg_dir / "pyproject.toml").is_file():
        print(f"  [FALHA] chunkhound: pacote ausente em '{pkg_dir}'.")
        return False

    try:
        cmd = [
            str(python_bin),
            "-c",
            f"import sys; sys.path.insert(0, r'{pkg_dir}'); import chunkhound; print('chunkhound OK')",
        ]
        res = subprocess.run(cmd, env=_python_env(), capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
        if res.returncode == 0:
            print("  [OK] chunkhound: pacote Python/Rust validado.")
            return True
        else:
            err = res.stderr.strip() or res.stdout.strip()
            print(f"  [FALHA] chunkhound: falha ao validar pacote: {err}")
            return False
    except Exception as exc:
        print(f"  [FALHA] chunkhound: exceção ao validar pacote: {exc}")
        return False


def provision_repowise(vendor_dir: Path, python_bin: Path) -> bool:
    """Valida o pacote Python repowise."""
    pkg_dir = vendor_dir / "repowise"
    core_src = pkg_dir / "packages" / "core" / "src"
    if not core_src.is_dir() and not (pkg_dir / "pyproject.toml").is_file():
        print(f"  [FALHA] repowise: pacote ausente em '{pkg_dir}'.")
        return False

    try:
        cmd = [
            str(python_bin),
            "-c",
            f"import sys; sys.path.insert(0, r'{core_src}'); import repowise.core; print('repowise OK')",
        ]
        res = subprocess.run(cmd, env=_python_env(), capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
        if res.returncode == 0:
            print("  [OK] repowise: pacote Python core validado.")
            return True
        else:
            err = res.stderr.strip() or res.stdout.strip()
            print(f"  [FALHA] repowise: falha ao validar pacote: {err}")
            return False
    except Exception as exc:
        print(f"  [FALHA] repowise: exceção ao validar pacote: {exc}")
        return False


def provision_vendor_integrations(python_bin: Path | None = None) -> bool:
    """Provisiona e valida deterministicamente os 7 provedores de vendor upstream."""
    print_step("4.5/6 Provisionando integrações vendor (7 provedores canônicos)...")
    if python_bin is None:
        python_bin = Path(sys.executable)
    vendor_dir = ROOT / "integrations" / "vendor"

    results = {
        "codebase-memory-mcp": provision_codebase_memory(vendor_dir),
        "boostprompt": provision_boostprompt(vendor_dir, python_bin),
        "sdlc-agents": provision_sdlc_agents(vendor_dir),
        "graphify": provision_graphify(vendor_dir, python_bin),
        "trace-mcp": provision_trace_mcp(vendor_dir),
        "chunkhound": provision_chunkhound(vendor_dir, python_bin),
        "repowise": provision_repowise(vendor_dir, python_bin),
    }

    failed = [name for name, ok in results.items() if not ok]
    if failed:
        print(f"  [FALHA] Provisionamento falhou para os seguintes provedores: {', '.join(failed)}")
        return False

    print("  [OK] Todos os 7 provedores vendor provisionados e validados com sucesso.")
    return True


def install_vendor_mcp_packages(python_bin: Path | None = None) -> bool:
    """Compatibilidade legada: executa provision_vendor_integrations."""
    return provision_vendor_integrations(python_bin)


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


def validate_suite(python_bin: Path | None = None, test_target: str | None = None) -> None:
    """Executa a validação estrutural e a suíte de testes automatizados."""
    print_step("6/6 Validando estrutura e executando suíte de testes...")
    if python_bin is None:
        python_bin = Path(sys.executable)
    val_script = ROOT / "scripts" / "validate_structure.py"
    py_env = _python_env()
    res_val = subprocess.run([str(python_bin), str(val_script)], env=py_env, capture_output=True, text=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
    if res_val.returncode == 0:
        print(f"  [OK] Estrutura: {res_val.stdout.strip()}")
    else:
        print(f"  [FALHA] Validação de estrutura: {res_val.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    # Pytest
    target = test_target or os.environ.get("SQUAD_TEST_TARGET") or str(ROOT / "scripts" / "tests")
    res_test = subprocess.run([str(python_bin), "-m", "pytest", str(target)], env=py_env, capture_output=True, text=True, timeout=NETWORK_COMMAND_TIMEOUT_SECONDS)
    if res_test.returncode == 0:
        print("  [OK] Validação da suíte concluída com sucesso.")
    else:
        print(f"  [FALHA] Testes falharam:\n{res_test.stdout}\n{res_test.stderr}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Função principal do instalador zero-to-hero completo."""
    import argparse
    parser = argparse.ArgumentParser(description="Instalação e provisionamento total do Agents Squad.")
    parser.add_argument("--skip-docker", action="store_true", help="Ignora provisionamento Docker.")
    parser.add_argument("--skip-tests", action="store_true", help="Ignora execução de testes.")
    parser.add_argument("--test-target", default=None, help="Alvo de teste para pytest.")
    args = parser.parse_args()

    print("\n=======================================================")
    print("   AGENTS SQUAD — INSTALAÇÃO & PROVISIONAMENTO TOTAL   ")
    print("=======================================================")
    vendor_ok = sync_vendor_repositories()
    if vendor_ok is False:
        print("\n\033[1;31m[FALHA] INSTALLATION_FAILED: Falha na sincronização dos repositórios upstream obrigatórios em integrations/vendor/.\033[0m\n", file=sys.stderr)
        sys.exit(1)

    missing_vendor = [repo for repo in CANONICAL_VENDOR_REPOSITORIES if not (ROOT / "integrations" / "vendor" / repo).is_dir()]
    if missing_vendor and vendor_ok is not None:
        print(f"\n\033[1;31m[FALHA] INSTALLER_NOT_READY: Repositórios upstream obrigatórios ausentes em integrations/vendor/: {', '.join(missing_vendor)}\033[0m\n", file=sys.stderr)
        sys.exit(1)

    python_bin = ensure_virtualenv()
    init_database()
    vendor_prov_ok = provision_vendor_integrations(python_bin)
    if vendor_prov_ok is False:
        print("\n\033[1;31m[FALHA] INSTALLATION_FAILED: Falha no provisionamento dos 7 provedores vendor obrigatórios.\033[0m\n", file=sys.stderr)
        sys.exit(1)

    sync_mcps(python_bin)
    if not args.skip_docker:
        provision_docker_stack()
    if not args.skip_tests:
        validate_suite(python_bin, test_target=args.test_target)

    print("\n\033[1;32m[SUCESSO] Instalação, repositórios vendor, banco e Docker 100% operacionais!\033[0m\n")


if __name__ == "__main__":
    main()

