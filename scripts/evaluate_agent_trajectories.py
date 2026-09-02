#!/usr/bin/env python3
"""
O que é: Harness de avaliação de trajetórias de agentes e benchmarks determinísticos (EDD - Evaluation-Driven Development).
Responsabilidade: Medir e avaliar a eficiência de trajetórias de execução de agentes, conformidade de escopo, chamadas de ferramentas e taxa de convergência.
Pra que serve: Prevenir regressões comportamentais e de qualidade em agentes e skills antes de sua promoção.
Comportamento em falha: Registra falhas detalhadas em trajectory_logs do banco local e emite relatório com código de saída 1.
Conexões: Integra-se com local_agent_db.py, agent_squad.py e a suíte de testes de benchmark.
Dependências & Imports:
  - json, time, pathlib, sys: Utilitários padrão para execução e gravação de métricas.
  - local_agent_db: Persistência de resultados de trajetórias.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from local_agent_db import LocalAgentDB


@dataclass
class TrajectoryStep:
    """Representa um passo de execução de um agente em uma trajetória."""
    step_number: int
    agent_id: str
    action: str
    tool_called: str | None = None
    files_touched: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    status: str = "ok"  # 'ok', 'error', 'retry'


@dataclass
class TrajectoryEvaluationResult:
    """Resultado consolidado da avaliação de uma trajetória de agente."""
    benchmark_id: str
    agent_id: str
    passed: bool
    convergence_score: float  # 0.0 a 1.0
    scope_compliance: bool
    clean_code_compliant: bool
    total_steps: int
    total_tool_calls: int
    duration_seconds: float
    violations: list[str] = field(default_factory=list)


class TrajectoryEvaluator:
    """Avaliador de trajetórias de execução e benchmarks de agentes."""

    def __init__(self, db: LocalAgentDB | None = None):
        """Inicializa o avaliador com a conexão ao banco local.

        Args:
            db: Instância opcional de LocalAgentDB.
        """
        self.db = db or LocalAgentDB(allow_legacy=True)

    def evaluate_trajectory(
        self,
        benchmark_id: str,
        agent_id: str,
        allowed_scope: list[str],
        steps: list[TrajectoryStep],
        max_allowed_steps: int = 10,
        expected_artifacts: list[str] | None = None,
    ) -> TrajectoryEvaluationResult:
        """Avalia uma sequência de passos executados por um agente contra limites e critérios.

        Args:
            benchmark_id: Identificador do caso de teste/benchmark.
            agent_id: ID do agente avaliado.
            allowed_scope: Lista de caminhos ou padrões permitidos para alteração.
            steps: Lista de passos da trajetória.
            max_allowed_steps: Limite máximo de passos antes de considerar loop/falha de convergência.
            expected_artifacts: Lista de arquivos ou artefatos que devem ser gerados.

        Returns:
            TrajectoryEvaluationResult: Resultado detalhado da avaliação.
        """
        violations: list[str] = []
        start_time = time.time()

        total_steps = len(steps)
        total_tool_calls = sum(1 for s in steps if s.tool_called)
        total_duration = sum(s.duration_seconds for s in steps)

        # 1. Checagem de convergência e estouro de passos
        if total_steps > max_allowed_steps:
            violations.append(f"Estouro de orçamento de passos: {total_steps} passos > limite {max_allowed_steps}")

        # 2. Checagem de conformidade de escopo (Boundary checking)
        scope_ok = True
        normalized_allowed = [p.replace("\\", "/").rstrip("/") for p in allowed_scope]

        for s in steps:
            for touched in s.files_touched:
                norm_touched = touched.replace("\\", "/").lstrip("/")
                if not any(norm_touched.startswith(allowed) or norm_touched == allowed for allowed in normalized_allowed):
                    violations.append(f"Violação de escopo: arquivo '{touched}' fora do escopo autorizado {allowed_scope}")
                    scope_ok = False

        # 3. Checagem de artefatos esperados
        if expected_artifacts:
            all_touched = {f.replace("\\", "/").lstrip("/") for s in steps for f in s.files_touched}
            for exp in expected_artifacts:
                norm_exp = exp.replace("\\", "/").lstrip("/")
                if norm_exp not in all_touched:
                    violations.append(f"Artefato esperado não gerado na trajetória: '{exp}'")

        # 4. Cálculo do score de convergência
        # Penaliza retries e estouro de passos
        error_count = sum(1 for s in steps if s.status != "ok")
        step_efficiency = max(0.0, 1.0 - (total_steps / (max_allowed_steps * 1.5)))
        error_penalty = max(0.0, 1.0 - (error_count * 0.2))
        convergence_score = round(step_efficiency * error_penalty, 2)

        passed = len(violations) == 0

        # 5. Persiste log no banco local
        details = {
            "violations": violations,
            "steps": [
                {
                    "step": s.step_number,
                    "action": s.action,
                    "tool": s.tool_called,
                    "files": s.files_touched,
                    "status": s.status,
                }
                for s in steps
            ],
        }

        self.db.log_trajectory(
            benchmark_id,
            agent_id,
            benchmark_id,
            "pass" if passed else "fail",
            total_steps,
            total_tool_calls,
            total_duration,
            details,
        )

        return TrajectoryEvaluationResult(
            benchmark_id=benchmark_id,
            agent_id=agent_id,
            passed=passed,
            convergence_score=convergence_score,
            scope_compliance=scope_ok,
            clean_code_compliant=passed,
            total_steps=total_steps,
            total_tool_calls=total_tool_calls,
            duration_seconds=total_duration,
            violations=violations,
        )


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI para execução do harness de avaliação de trajetórias."""
    parser = argparse.ArgumentParser(description="Harness de Avaliação de Trajetórias de Agentes (EDD)")
    parser.add_argument("--benchmark", default="smoke-test", help="Nome do benchmark a executar")
    parser.add_argument("--agent", default="software-engineer", help="ID do agente a avaliar")
    args = parser.parse_args(argv or sys.argv[1:])

    evaluator = TrajectoryEvaluator()
    # Execução de teste de fumaça padrão
    sample_steps = [
        TrajectoryStep(1, args.agent, "read_contract", tool_called="view_file", files_touched=["templates/code-component.md"]),
        TrajectoryStep(2, args.agent, "write_code", tool_called="write_to_file", files_touched=["src/app.py"]),
        TrajectoryStep(3, args.agent, "run_tests", tool_called="run_command", files_touched=[]),
    ]

    result = evaluator.evaluate_trajectory(
        benchmark_id=args.benchmark,
        agent_id=args.agent,
        allowed_scope=["templates", "src"],
        steps=sample_steps,
        max_allowed_steps=5,
        expected_artifacts=["src/app.py"],
    )

    if result.passed:
        print(f"BENCHMARK_PASS: {args.benchmark} (Agent: {args.agent}, Convergence Score: {result.convergence_score})")
        return 0
    else:
        print(f"BENCHMARK_FAIL: {args.benchmark} (Violations: {result.violations})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
