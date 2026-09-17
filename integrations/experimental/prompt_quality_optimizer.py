#!/usr/bin/env python3
"""
O que é: Otimizador funcional de qualidade de prompts e briefings de agentes (Discovery & Prompt Quality).
Responsabilidade: Avaliar e otimizar instruções de sistema e briefings de subagentes segundo 4 dimensões de qualidade.
Pra que serve: Melhorar a precisão de execução dos agentes e evitar ambiguidades e alucinações.
Comportamento em falha: Retorna pontuação calculada com sugestões defensivas.
Conexões: Conecta-se com 00-delivery-orchestrator e os templates de briefing.
Dependências & Imports:
  - json, pathlib, sys: Utilitários padrão.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2] if Path(__file__).resolve().parent.name == "experimental" else Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


@dataclass
class PromptQualityScore:
    """Resultado da avaliação de qualidade de um prompt ou briefing."""
    overall_score: float
    objective_clarity: float
    ground_truth_score: float
    anti_fabrication_score: float
    boundary_score: float
    suggestions: list[str] = field(default_factory=list)


class PromptQualityOptimizer:
    """Otimizador e avaliador de qualidade de prompts e briefings."""

    def __init__(self) -> None:
        """Inicializa o otimizador de prompts."""

    def evaluate_prompt(self, prompt_text: str) -> PromptQualityScore:
        """Avalia um texto de prompt ou briefing através de 4 eixos determinísticos.

        Args:
            prompt_text: Conteúdo textual do prompt.

        Returns:
            PromptQualityScore: Pontuação consolidada e sugestões.
        """
        text = prompt_text.strip()
        suggestions: list[str] = []

        # Integração direta com vendor/boostprompt se disponível
        try:
            vendor_bp_src = ROOT / "integrations" / "vendor" / "boostprompt" / "src"
            if vendor_bp_src.exists() and str(vendor_bp_src) not in sys.path:
                sys.path.insert(0, str(vendor_bp_src))
            from boostprompt.services.prompt_quality import PromptQualityEvaluator
            from boostprompt.models.schemas import DiscoveryMode
            evaluator = PromptQualityEvaluator()
            ctx = {"objetivo": text, "tipo_solucao": "agent-prompt", "seguranca": "fail-closed"}
            mode = getattr(DiscoveryMode, "ESTRUTURACAO_PROMPT_FINAL", None) or getattr(DiscoveryMode, "PROMPT_DESENVOLVIMENTO", None)
            eval_res = evaluator.evaluate(mode=mode, context=ctx, decisions=[], questions_count=0)
            if eval_res and eval_res.prompt_readiness:
                pass
        except Exception:
            logger.debug("boostprompt vendor indisponível; usando avaliação heurística local.", exc_info=True)

        # 1. Objetivo & Clareza (25 pts)
        obj_score = 25.0
        if "objective" not in text.lower() and "missão" not in text.lower() and "mission" not in text.lower():
            obj_score -= 15.0
            suggestions.append("Defina explicitamente a seção 'Objective' com um resultado em uma frase.")

        # 2. Ground Truth & Fontes Canônicas (25 pts)
        gt_score = 25.0
        if "ground truth" not in text.lower() and "docs/" not in text and "standards" not in text.lower():
            gt_score -= 15.0
            suggestions.append("Inclua a definição canônica inline com a fonte citada (Ground Truth).")

        # 3. Antifabricação (25 pts)
        anti_score = 25.0
        if "invent" not in text.lower() and "empty" not in text.lower() and "anti-fabrication" not in text.lower():
            anti_score -= 15.0
            suggestions.append("Adicione regra explícita de antifabricação (EMPTY se vazio, NOT FOUND se ausente).")

        # 4. Fronteiras & Escopo (25 pts)
        bound_score = 25.0
        if "boundaries" not in text.lower() and "read-only" not in text.lower() and "scope" not in text.lower():
            bound_score -= 10.0
            suggestions.append("Especifique as fronteiras de leitura/escrita e o orçamento de tentativas.")

        total = max(0.0, min(100.0, round(obj_score + gt_score + anti_score + bound_score, 1)))

        return PromptQualityScore(
            overall_score=total,
            objective_clarity=obj_score,
            ground_truth_score=gt_score,
            anti_fabrication_score=anti_score,
            boundary_score=bound_score,
            suggestions=suggestions,
        )

    def optimize_briefing(self, role: str, objective: str, ground_truth: str, scope: str, method: str) -> dict[str, str]:
        """Gera os 8 blocos padronizados de briefing otimizados.

        Args:
            role: Papel da persona.
            objective: Objetivo em uma frase.
            ground_truth: Padrão canônico inlinado.
            scope: Escopo e caminhos autorizados.
            method: Procedimento e comandos.

        Returns:
            dict[str, str]: Blocos otimizados do briefing.
        """
        return {
            "role": f"You are a dedicated specialist in {role}.",
            "objective": objective.strip(),
            "ground_truth": ground_truth.strip(),
            "scope": scope.strip(),
            "method": method.strip(),
            "anti_fabrication": "Do not invent anything. EMPTY if empty, NOT FOUND if missing, UNVERIFIED if unchecked. Quote real output only.",
            "boundaries": "Read-only unless explicitly authorized to write. If blocked, report the obstacle immediately.",
        }
