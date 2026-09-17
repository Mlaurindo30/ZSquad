#!/usr/bin/env python3
"""
O que é: Mapeador funcional de papéis e handoffs padronizados no ciclo de vida de desenvolvimento (SDLC).
Responsabilidade: Mapear personas especializadas do squad para papéis canônicos de engenharia de software e IDEs.
Pra que serve: Garantir interoperabilidade com extensões de editores e ferramentas de CI/CD.
Comportamento em falha: Retorna papel 'specialist' como fallback para personas não mapeadas.
Conexões: Mapeia agentes do squad para papéis SDLC canônicos.
Dependências & Imports:
  - pathlib, typing: Utilitários padrão.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2] if Path(__file__).resolve().parent.name == "experimental" else Path(__file__).resolve().parents[1]


class SDLCRoleMapper:
    """Mapeador de personas do squad para papéis SDLC."""

    def __init__(self, squad_root: Path | str | None = None):
        """Inicializa o mapeador de papéis.

        Args:
            squad_root: Raiz do squad.
        """
        self.squad_root = Path(squad_root) if squad_root else ROOT

    def get_canonical_sdlc_agents(self) -> list[str]:
        """Descobre os agentes SDLC canônicos definidos em integrations/vendor/sdlc-agents/agents/.

        Returns:
            list[str]: Lista de nomes de agentes (ex: ['design', 'execution', ...]).
        """
        vendor_agents_dir = self.squad_root / "integrations" / "vendor" / "sdlc-agents" / "agents"
        if not vendor_agents_dir.is_dir():
            return []
        return sorted(p.stem.replace(".agent", "") for p in vendor_agents_dir.glob("*.agent.md"))

    def map_squad_agents_to_roles(self, squad_agents: list[str]) -> list[dict[str, str]]:
        """Mapeia os agentes do squad para as definições de papéis padronizadas.

        Args:
            squad_agents: Lista de IDs dos agentes ativos.

        Returns:
            list[dict[str, str]]: Lista de definições de papéis mapeadas.
        """
        mapping = {
            "requirements-analyst": "analyst",
            "product-owner": "product-manager",
            "solution-architect": "architect",
            "software-engineer": "developer",
            "code-reviewer": "reviewer",
            "qa-engineer": "tester",
            "devops-release-engineer": "devops",
            "security-reviewer": "security-auditor",
            "skill-curator": "knowledge-manager",
            "delivery-orchestrator": "coordinator",
        }
        return [
            {"squad_agent": agent, "sdlc_role": mapping.get(agent, "specialist")}
            for agent in squad_agents
        ]

