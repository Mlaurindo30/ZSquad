#!/usr/bin/env python3
"""
O que é: Sincronizador e gerador de configurações de servidores MCP (Model Context Protocol).
Responsabilidade: Gerar, validar e sincronizar mcp_config.json para Antigravity IDE, Claude Code e Codex CLI com base no catálogo de skills e ferramentas do squad.
Pra que serve: Prover interoperabilidade padronizada entre os agentes e as ferramentas de inteligência de código (Graphify, Trace-MCP, Codebase-Memory, Sinapse e Local DB).
Comportamento em falha: Valida os schemas JSON de saída; se inválido, emite erro descritivo e preserva o arquivo existente.
Conexões: Utilizado por devops-release-engineer, delivery-orchestrator e ferramentas de configuração de runtime.
Dependências & Imports:
  - json, pathlib, sys, argparse: Operações de serialização e manipulação de arquivos JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def build_default_mcp_config(squad_root: Path) -> dict[str, Any]:
    """Constrói a configuração padrão de servidores MCP para o ecossistema do squad.

    Args:
        squad_root: Diretório raiz do squad.

    Returns:
        dict[str, Any]: Dicionário com a estrutura de servidores MCP.
    """
    python_exe = sys.executable
    root_str = str(squad_root).replace("\\", "/")

    return {
        "mcpServers": {
            "azure-devops": {
                "command": "npx",
                # MCP Auth Fix (Lote 5.1): MCP v2.x usa PAT via env var
                # ADO_MCP_AUTH_TOKEN + --authentication envvar (headless/CI);
                # a organização é passada como NOME (não URL) como argumento.
                "args": ["-y", "@azure-devops/mcp@latest", "${AZURE_DEVOPS_ORG}", "--authentication", "envvar"],
                "env": {
                    "ADO_MCP_AUTH_TOKEN": "${AZURE_DEVOPS_PAT}"
                },
                "description": "Servidor MCP oficial do Azure DevOps (@azure-devops/mcp) para gestão de Boards, WIQL, Work Items e Pull Requests."
            },
            # squad-local-db foi removido: local_agent_db.py é biblioteca, não servidor MCP.
            # Um wrapper MCP real para o banco do squad entra como fase futura do
            # EVOL-LIVING-MEMORY-20260822 (ver plans/delivery-plan.md).
            "codebase-memory": {
                "command": python_exe,
                "args": ["-m", "codebase_memory_mcp"],
                "env": {
                    "PYTHONPATH": f"{root_str}/integrations/vendor/codebase-memory-mcp/pkg/pypi/src"
                },
                "description": "High-performance persistent structural symbol knowledge graph.",
                "tools": ["query_symbol", "find_callers", "find_dependencies"],
            },
            "sinapse-hivemind": {
                "command": python_exe,
                "args": ["D:/Hive-Mind/scripts/services/sinapse-mcp.py"],
                "description": "Global durable semantic memory bridge for Hive-Mind vault.",
                "tools": [
                    "sinapse_health",
                    "sinapse_query",
                    "sinapse_save_decision",
                    "sinapse_save_learning",
                    "sinapse_session_end",
                    "sinapse_temporal_search",
                    "sinapse_temporal_timeline",
                    "sinapse_temporal_get_observations",
                    "sinapse_temporal_save",
                    "sinapse_zettelkasten_split",
                    "sinapse_capture_screen",
                    "sinapse_plan_goal",
                    "sinapse_promote_knowledge",
                    "sinapse_temporal_graph_search",
                    "sinapse_rag_query",
                    "search_memories",
                ],
            },
        }
    }


class MCPSyncManager:
    """Gerenciador de sincronização de configurações de servidores MCP."""

    def __init__(self, squad_root: Path | str | None = None):
        """Inicializa o gerenciador com a raiz do squad.

        Args:
            squad_root: Caminho da raiz do squad.
        """
        self.root = Path(squad_root) if squad_root else ROOT

    def generate_and_save(self, output_path: Path | str | None = None) -> Path:
        """Gera e salva o arquivo de configuração mcp_config.json.

        Args:
            output_path: Caminho de saída opcional. Padrão: `config/mcp_config.json`.

        Returns:
            Path: Caminho do arquivo gravado.
        """
        out = Path(output_path) if output_path else self.root / "config" / "mcp_config.json"
        out.parent.mkdir(parents=True, exist_ok=True)

        config_data = build_default_mcp_config(self.root)
        out.write_text(json.dumps(config_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return out

    def validate_config(self, config_path: Path | str) -> bool:
        """Valida se o arquivo de configuração MCP segue a estrutura canônica esperada.

        Args:
            config_path: Caminho do arquivo a validar.

        Returns:
            bool: True se válido, False caso contrário.
        """
        path = Path(config_path)
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
                return False
            for s_name, s_conf in data["mcpServers"].items():
                if "command" not in s_conf:
                    return False
            return True
        except Exception:
            return False


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para sincronização de servidores MCP."""
    parser = argparse.ArgumentParser(description="Sincronizador de Servidores MCP do Squad")
    parser.add_argument("--output", help="Caminho do arquivo mcp_config.json de destino")
    args = parser.parse_args(argv or sys.argv[1:])

    manager = MCPSyncManager()
    target_file = manager.generate_and_save(args.output)
    is_valid = manager.validate_config(target_file)

    if is_valid:
        print(f"MCP_SYNC_SUCCESS: Configuração gerada e validada com sucesso em '{target_file}'.")
        return 0
    else:
        print(f"MCP_SYNC_ERROR: Falha na validação da configuração em '{target_file}'.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
