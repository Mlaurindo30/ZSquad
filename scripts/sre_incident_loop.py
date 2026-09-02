#!/usr/bin/env python3
"""
O que é: Automação de incidentes de observabilidade e ciclo fechado SRE (Closed-Loop Incident Orchestrator).
Responsabilidade: Processar alertas de telemetria e falhas em produção, gerando automaticamente work items do tipo BUG-* com contexto e evidências de erro.
Pra que serve: Fechar o ciclo entre telemetria de produção (SRE) e correção assistida por agentes de engenharia.
Comportamento em falha: Cria o work item com fallback de status de diagnóstico parcial e notifica o orquestrador.
Conexões: Utilizado por 26-sre-observability-engineer, 13-devops-release-engineer e agent_squad.py.
Dependências & Imports:
  - argparse, json, sys, time, pathlib: Manipulação de CLI, sistema de arquivos e formatação de datas.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_squad import AgentSquad, SquadError, read_yaml, write_yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


class SREIncidentLoop:
    """Orquestrador de incidentes e geração automática de work items de correção."""

    def __init__(self, squad_root: Path | str | None = None, project_name: str | None = None):
        """Inicializa o orquestrador com a raiz do squad.

        Args:
            squad_root: Caminho da raiz do squad.
            project_name: Projeto consumidor onde o work item é criado. ``None`` usa
                o modo legado (``work/<BUG-ID>``), mantido apenas para compatibilidade.
        """
        self.root = Path(squad_root) if squad_root else ROOT
        self.project_name = project_name

    def create_incident_bug(
        self,
        alert_id: str,
        service_name: str,
        error_details: str,
        severity: str = "high",
    ) -> str:
        """Cria automaticamente um work item de bug governado a partir de um alerta de produção.

        Delega a criação da árvore de artefatos ao ``AgentSquad.init_work_item`` para
        que o ``status.yaml`` resultante conforme ao ``work-item.schema.json`` — o
        mesmo contrato validado por ``validate-work-item`` e ``audit``.

        Args:
            alert_id: Identificador do alerta (ex: high_error_rate_5xx).
            service_name: Nome do microsserviço ou componente afetado.
            error_details: Descrição textual ou trace do erro.
            severity: Gravidade do incidente ('critical', 'high', 'medium', 'low').

        Returns:
            str: ID do work item gerado (ex: BUG-INCIDENT-...).
        """
        timestamp_str = time.strftime("%Y%m%d-%H%M%S")
        bug_id = f"BUG-INCIDENT-{service_name.upper()}-{timestamp_str}"
        risk = severity if severity in {"low", "medium", "high", "critical"} else "high"

        squad = AgentSquad(self.root, project_name=self.project_name, allow_legacy=self.project_name is None)
        work_dir = squad.init_work_item(bug_id, risk)

        status_path = work_dir / "status.yaml"
        status = read_yaml(status_path)
        status["next_action"] = f"Investigar causa raiz do alerta '{alert_id}' em '{service_name}' e validar GT-entry."
        status["status_note"] = f"target_service={service_name}; alert_source={alert_id}"
        write_yaml(status_path, status)

        epic_content = f"""# Incidente de Produção: {alert_id} no serviço `{service_name}`

## Contexto do Incidente
- **Origem do Alerta**: `{alert_id}`
- **Serviço Afetado**: `{service_name}`
- **Gravidade**: `{severity}`
- **Data/Hora**: `{time.strftime('%Y-%m-%d %H:%M:%S UTC')}`

## Evidência e Traces Coletados
```
{error_details}
```

## Plano de Remediação Imediato
1. Investigação de causa raiz com `26-sre-observability-engineer` e `22-backend-engineer`.
2. Execução de rollback caso triggers de SLO estejam violados.
3. Criação de testes de regressão para garantir não-recorrência.
"""
        (work_dir / "epic.md").write_text(epic_content, encoding="utf-8")

        ledger_path = work_dir / "documentation" / "delivery-ledger.md"
        ledger_line = (
            f"| {time.strftime('%Y-%m-%d')} | sre-observability-engineer | epic.md | "
            "Incidente registrado automaticamente por telemetria | N/A | Investigar causa raiz |\n"
        )
        with ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(ledger_line)

        return bug_id


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para conversão de incidentes de observabilidade."""
    parser = argparse.ArgumentParser(description="Automação de Incidentes e Closed-Loop SRE")
    parser.add_argument("--alert", required=True, help="Identificador do alerta SRE")
    parser.add_argument("--service", required=True, help="Nome do serviço afetado")
    parser.add_argument("--details", default="Trigger de SLO violado em produção.", help="Detalhes ou logs do erro")
    parser.add_argument("--severity", default="high", help="Nível de risco (critical, high, medium, low)")
    parser.add_argument("--project-name", default=None, help="Projeto consumidor (work/<projeto>/<BUG-ID>)")
    args = parser.parse_args(argv or sys.argv[1:])

    loop = SREIncidentLoop(project_name=args.project_name)
    try:
        bug_id = loop.create_incident_bug(
            alert_id=args.alert,
            service_name=args.service,
            error_details=args.details,
            severity=args.severity,
        )
    except SquadError as exc:
        print(f"INCIDENT_WORK_ITEM_FAILED: {exc}", file=sys.stderr)
        logger.error("INCIDENT_WORK_ITEM_FAILED: %s", exc)
        return 1
    print(f"INCIDENT_WORK_ITEM_CREATED: Work item '{bug_id}' gerado com sucesso.")
    logger.info("INCIDENT_WORK_ITEM_CREATED: %s", bug_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())

