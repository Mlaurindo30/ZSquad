"""Skill selector — consome o catálogo descoberto e rankeia skills relevantes
para uma tarefa/contexto de agente.

Uso:
    from scripts.skill_selector import select_skills_for_task, RankedSkill

    ranked = select_skills_for_task(
        task="build a React Native app with auth",
        available_skills=discovered_skills,
        persona="mobile-developer",
        max_results=3,
    )
    for r in ranked:
        print(f"{r.skill.name} — score {r.score:.2f} ({r.match_reasons})")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Union


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveredSkill:
    name: str
    path: str
    source: str = "local"
    description: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RankedSkill:
    skill: DiscoveredSkill
    score: float
    match_reasons: tuple[str, ...]


# ---------------------------------------------------------------------------
# Stopwords mínimas para matching textual
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "about", "o", "a",
    "os", "as", "de", "do", "da", "no", "na", "em", "um", "uma", "uns",
    "umas", "se", "que", "e", "ou", "com", "para", "por", "entre",
    "como", "mais", "menos", "sua", "seu", "seus", "suas",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_\-]+", text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 2]


def _boost_for_persona(skill: DiscoveredSkill, persona: Optional[str]) -> float:
    if not persona:
        return 0.0
    persona_lower = persona.lower()
    haystack = f"{skill.name} {skill.description} {skill.metadata.get('persona', '')}"
    haystack_lower = haystack.lower()
    if persona_lower in haystack_lower:
        return 2.0
    # partial match
    tokens = _tokenize(persona_lower)
    hits = sum(1 for t in tokens if t in haystack_lower)
    return hits * 0.3


# ---------------------------------------------------------------------------
# Seleção principal
# ---------------------------------------------------------------------------

def select_skills_for_task(
    task: str,
    available_skills: Sequence[DiscoveredSkill],
    persona: Optional[str] = None,
    max_results: int = 3,
    min_score: float = 0.1,
) -> List[RankedSkill]:
    """Rankeia skills mais relevantes para uma tarefa.

    Score combina:
      - token overlap entre task e `name` (peso 3x)
      - token overlap entre task e `description` (peso 2x)
      - token overlap entre task e `metadata` serializado (peso 1x)
      - boost se `persona` bater com skill.name/description/metadata.persona

    Args:
        task: descrição da tarefa em linguagem natural.
        available_skills: catálogo vindo de `discover_skills()`.
        persona: persona atribuída ao agente (ex: "mobile-developer").
        max_results: máximo de skills a retornar.
        min_score: corte mínimo de relevância.

    Returns:
        Lista de `RankedSkill` ordenada por score desc, já filtrada por
        ``min_score`` e limitada a ``max_results``.
    """
    task_tokens = _tokenize(task)
    if not task_tokens:
        return []

    ranked: list[RankedSkill] = []

    for skill in available_skills:
        reasons: list[str] = []
        score = 0.0

        name_tokens = _tokenize(skill.name)
        desc_tokens = _tokenize(skill.description)
        meta_tokens = _tokenize(str(skill.metadata))

        name_hits = len(set(task_tokens) & set(name_tokens))
        desc_hits = len(set(task_tokens) & set(desc_tokens))
        meta_hits = len(set(task_tokens) & set(meta_tokens))

        score += name_hits * 3.0
        if name_hits:
            reasons.append(f"name={skill.name}")

        score += desc_hits * 2.0
        if desc_hits:
            reasons.append(f"desc keywords")

        score += meta_hits * 1.0
        if meta_hits:
            reasons.append("metadata match")

        persona_boost = _boost_for_persona(skill, persona)
        score += persona_boost
        if persona_boost:
            reasons.append(f"persona={persona}")

        if score >= min_score:
            ranked.append(RankedSkill(
                skill=skill,
                score=score,
                match_reasons=tuple(reasons),
            ))

    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:max_results]


# ---------------------------------------------------------------------------
# Autoload — sugere skills e retorna conteúdo dos SKILL.md
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillSuggestion:
    ranked: RankedSkill
    loaded: bool
    content: Optional[str]
    error: Optional[str]


def suggest_and_load(
    task: str,
    skills_dir: Union[str, Path],
    persona: Optional[str] = None,
    max_results: int = 3,
    min_score: float = 0.1,
    cache: bool = True,
) -> List[SkillSuggestion]:
    """Convenience: descobre skills locais, rankeia e carrega conteúdo.

    Args:
        task: descrição da tarefa.
        skills_dir: diretório base de skills (ex: ``Path("skills")``).
        persona: persona atribuída ao agente.
        max_results: máximo de sugestões.
        min_score: score mínimo para considerar.
        cache: usar cache disco do AST scanner.

    Returns:
        Lista de ``SkillSuggestion`` com conteúdo carregado quando possível.
    """
    from scripts.skill_discovery_ast import discover_skills  # import local p/ evitar ciclos

    discovered = discover_skills(Path(skills_dir), cache=cache)
    ranked = select_skills_for_task(
        task=task,
        available_skills=discovered,
        persona=persona,
        max_results=max_results,
        min_score=min_score,
    )

    suggestions: list[SkillSuggestion] = []
    for r in ranked:
        skill_md = Path(r.skill.path)
        content: Optional[str] = None
        error: Optional[str] = None
        loaded = False
        if skill_md.is_file():
            try:
                content = skill_md.read_text(encoding="utf-8")
                loaded = True
            except OSError as exc:
                error = str(exc)
        suggestions.append(SkillSuggestion(
            ranked=r,
            loaded=loaded,
            content=content,
            error=error,
        ))
    return suggestions
