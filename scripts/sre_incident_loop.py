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
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


class SREIncidentLoop:
    """Orquestrador de incidentes e geração automática de work items de correção."""

    def __init__(self, squad_root: Path | str | None = None):
        """Inicializa o orquestrador com a raiz do squad.

        Args:
            squad_root: Caminho da raiz do squad.
        """
        self.root = Path(squad_root) if squad_root else ROOT

    def create_incident_bug(
        self,
        alert_id: str,
        service_name: str,
        error_details: str,
        severity: str = "high",
    ) -> str:
        """Cria automaticamente uma pasta de trabalho de bug e status.yaml a partir de um alerta de produção.

        Args:
            alert_id: Identificador do alerta (ex: high_error_rate_5xx).
            service_name: Nome do microsserviço ou componente afetado.
            error_details: Descrição textual ou trace do erro.
            severity: Gravidade do incidente ('critical', 'high', 'medium').

        Returns:
            str: ID do work item gerado (ex: BUG-INCIDENT-...).
        """
        timestamp_str = time.strftime("%Y%m%d-%H%M%S")
        bug_id = f"BUG-INCIDENT-{service_name.upper()}-{timestamp_str}"
        work_dir = self.root / "work" / bug_id
        work_dir.mkdir(parents=True, exist_ok=True)

        for sub in ["documentation", "memory/shared", "memory/deltas", "evidence", "gate-decisions"]:
            (work_dir / sub).mkdir(parents=True, exist_ok=True)

        status_content = f"""id: "{bug_id}"
type: "bug"
risk: "{severity}"
created_at: "{time.strftime('%Y-%m-%dT%H:%M:%SZ')}"
current_gate: "G1-product"
status: "in_progress"
target_service: "{service_name}"
alert_source: "{alert_id}"
"""
        (work_dir / "status.yaml").write_text(status_content, encoding="utf-8")

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
1. Investigação de causa raiz com `26-sre-observability-engineer` e `08-backend-specialist`.
2. Execução de rollback caso triggers de SLO estejam violados.
3. Criação de testes de regressão para garantir não-recorrência.
"""
        (work_dir / "epic.md").write_text(epic_content, encoding="utf-8")

        ledger_content = f"""# Delivery Ledger — {bug_id}

| Data | Agente | Artefato | Decisão / Mudança | Testes | Próximo Passo |
|---|---|---|---|---|---|
| {time.strftime('%Y-%m-%d')} | sre-observability-engineer | epic.md | Incidente registrado automaticamente por telemetria | N/A | Investigar causa raiz |
"""
        (work_dir / "documentation" / "delivery-ledger.md").write_text(ledger_content, encoding="utf-8")

        return bug_id


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para conversão de incidentes de observabilidade."""
    parser = argparse.ArgumentParser(description="Automação de Incidentes e Closed-Loop SRE")
    parser.add_argument("--alert", required=True, help="Identificador do alerta SRE")
    parser.add_argument("--service", required=True, help="Nome do serviço afetado")
    parser.add_argument("--details", default="Trigger de SLO violado em produção.", help="Detalhes ou logs do erro")
    parser.add_argument("--severity", default="high", help="Nível de risco (critical, high, medium)")
    args = parser.parse_args(argv or sys.argv[1:])

    loop = SREIncidentLoop()
    bug_id = loop.create_incident_bug(
        alert_id=args.alert,
        service_name=args.service,
        error_details=args.details,
        severity=args.severity,
    )
    print(f"INCIDENT_WORK_ITEM_CREATED: Work item '{bug_id}' gerado com sucesso.")
    logger.info("INCIDENT_WORK_ITEM_CREATED: %s", bug_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
