#!/usr/bin/env python3
"""
O que é: Motor funcional de observabilidade de trajetórias, rastreamento de episódios e destilação de auto-refinamento (/refine).
Responsabilidade: Coletar passos de execução de agentes, isolar falhas e erros em tool calls e destilar regras heurísticas para autorreparo de briefings.
Pra que serve: Prevenir que agentes cometam os mesmos erros em tarefas subsequentes, eliminando loops de repetição e acelerando a convergência.
Comportamento em falha: Classifica o erro de acordo com a taxonomia canônica e gera regras corretivas defensivas.
Conexões: Conecta-se com 00-delivery-orchestrator, banco/squad.db e scripts/evaluate_agent_trajectories.py.
Dependências & Imports:
  - json, pathlib, sys, time: Manipulação de dados e arquivos.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


class TrajectoryErrorCode(str, Enum):
    """Códigos de erro canônicos para classificação de falhas de trajetória."""
    INVALID_TOOL_CALL = "invalid_tool_call"
    FILE_NOT_FOUND = "file_not_found"
    SYNTAX_ERROR = "syntax_error"
    SCOPE_VIOLATION = "scope_violation"
    GATE_REJECTED = "gate_rejected"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class StepTrace:
    """Representa o registro de execução de um passo ou tool call de um agente."""
    step_index: int
    agent_id: str
    action: str
    input_params: dict[str, Any]
    output_result: str
    is_success: bool
    error_code: TrajectoryErrorCode | None = None
    duration_ms: float = 0.0


@dataclass
class EpisodeTrace:
    """Agrupamento de passos de execução de um work item completo."""
    episode_id: str
    work_item_id: str
    agent_id: str
    start_time: float = field(default_factory=time.time)
    steps: list[StepTrace] = field(default_factory=list)
    final_status: str = "in_progress"  # 'converged', 'failed', 'blocked'


class TrajectoryRefinementEngine:
    """Motor de coleta de trajetórias e destilação contínua de heurísticas (/refine)."""

    def __init__(self) -> None:
        """Inicializa o motor de refinamento de trajetórias."""

    def classify_error(self, error_message: str) -> TrajectoryErrorCode:
        """Classifica uma mensagem de erro em um código de erro canônico.

        Args:
            error_message: Mensagem textual de erro retornada pela ferramenta.

        Returns:
            TrajectoryErrorCode: Código classificado.
        """
        msg = error_message.lower()
        if "not found" in msg or "no such file" in msg:
            return TrajectoryErrorCode.FILE_NOT_FOUND
        elif "syntax" in msg or "parse" in msg or "indentation" in msg:
            return TrajectoryErrorCode.SYNTAX_ERROR
        elif "permission" in msg or "unauthorized" in msg or "outside" in msg:
            return TrajectoryErrorCode.SCOPE_VIOLATION
        elif "rejected" in msg or "gate" in msg:
            return TrajectoryErrorCode.GATE_REJECTED
        elif "rate limit" in msg or "429" in msg:
            return TrajectoryErrorCode.RATE_LIMITED
        elif "timeout" in msg:
            return TrajectoryErrorCode.TIMEOUT
        return TrajectoryErrorCode.UNKNOWN_ERROR

    def distill_refinement_rules(self, failed_steps: list[dict[str, Any]]) -> list[str]:
        """Destila regras heurísticas direcionadas a partir de passos que falharam.

        Args:
            failed_steps: Lista de dicionários contendo action e error.

        Returns:
            list[str]: Lista de regras corretivas destiladas para enriquecer o briefing.
        """
        rules: list[str] = []
        for step in failed_steps:
            action = step.get("action", "")
            error = step.get("error", "")
            code = self.classify_error(error)

            if code == TrajectoryErrorCode.FILE_NOT_FOUND:
                rules.append(f"Verifique a existência do caminho com `list_dir` antes de executar `{action}`.")
            elif code == TrajectoryErrorCode.SYNTAX_ERROR:
                rules.append("Execute verificação estática de sintaxe e compilação antes de salvar o arquivo definitivo.")
            elif code == TrajectoryErrorCode.SCOPE_VIOLATION:
                rules.append("Limite as edições estritamente aos diretórios autorizados no escopo do work item.")
            elif code == TrajectoryErrorCode.GATE_REJECTED:
                rules.append(f"Assegure evidência executada completa antes de solicitar aprovação de gate em `{action}`.")
            else:
                rules.append(f"Atenção ao passo `{action}`: {error[:60]}")

        return list(dict.fromkeys(rules))

    def format_trajectory_jsonl(self, episode: EpisodeTrace) -> str:
        """Formata um episódio em linhas JSONL serializadas de forma compacta e determinística.

        Args:
            episode: Objeto do episódio.

        Returns:
            str: Linhas JSONL formatadas.
        """
        record = {
            "episode_id": episode.episode_id,
            "work_item_id": episode.work_item_id,
            "agent_id": episode.agent_id,
            "steps_count": len(episode.steps),
            "final_status": episode.final_status,
            "steps": [
                {
                    "index": s.step_index,
                    "action": s.action,
                    "success": s.is_success,
                    "error_code": s.error_code.value if s.error_code else None,
                    "duration_ms": s.duration_ms,
                }
                for s in episode.steps
            ],
        }
        return json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
