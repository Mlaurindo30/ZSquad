"""Grafo de aprendizado visível do squad (porta do hermes-agent ``learning_graph``).

O que é: montador de um grafo não direcionado cujos nós são as skills do
catálogo (``config/skills-catalog.yaml``) e os deltas de memória do squad
(``work/**/memory/deltas/MEM-*.yaml``), com arestas derivadas de afinidade
declarada e sobreposição lexical.

Responsabilidade: ler o catálogo de skills e os deltas MEM-*.yaml, atribuir ids
estáveis (nome da skill; ``memory:<stem-do-caminho-relativo>``), derivar as
arestas (skill↔skill por domínio compartilhado ou agente ``assigned_to``
comum; memória→skill por sobreposição lexical dos statements; memória↔memória
por ``work_item_id`` comum) e calcular estatísticas de densidade espelhando o
hermes (node_count, edge_count, isolated, density).

Pra que serve: responder "o que o squad aprendeu e como isso se conecta?",
expondo um payload determinístico (nodes/edges/stats) consumível por painéis,
auditorias e mutações governadas (``scripts.learning_mutations``).

Comportamento em falha: tolerante por design, como o hermes — catálogo
ausente/ilegível/YAML inválido vira lista de skills vazia; ``work_root``
ausente vira lista de memórias vazia; entradas malformadas (não-dict, sem nome,
stem duplicado, entries não-lista) são puladas, nunca levantam. A função sempre
retorna um payload válido ``{"nodes": [...], "edges": [...], "stats": {...}}``.

Conexões: consumidor direto de ``config/skills-catalog.yaml`` e de
``work/<WORK-ID>/memory/deltas/MEM-*.yaml``; alimenta
``scripts/learning_mutations.py`` (ids estáveis são o contrato entre os dois
módulos) e pode ser usado por relatórios/dashboards do squad.

Dependências & Imports: apenas stdlib (``pathlib``, ``re``) + PyYAML
(``yaml.safe_load``). Sem rede, sem LLM, sem FalkorDB.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

# Separador de tokens lexical: mesma régua do hermes ([^a-z0-9]+, len >= 3).
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
# Máximo de skills ligadas por memória, espelhando o top-4 do hermes.
_MAX_MEMORY_SKILL_EDGES = 4


def _tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text.lower()) if len(t) >= 3}


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _load_catalog(catalog_path: str | Path) -> list[dict[str, Any]]:
    """Lê ``config/skills-catalog.yaml``; falhas de leitura/YAML viram lista vazia."""
    try:
        raw = yaml.safe_load(Path(catalog_path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []
    entries = raw.get("catalog")
    if not isinstance(entries, list):
        return []
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        skills.append(
            {
                "id": name,
                "label": name,
                "kind": "skill",
                "domain": str(entry.get("domain") or ""),
                "source": str(entry.get("source") or ""),
                "assigned_to": _as_str_list(entry.get("assigned_to")),
                "description": str(entry.get("description") or ""),
            }
        )
    return skills


def _load_memory_deltas(work_root: str | Path) -> list[dict[str, Any]]:
    """Carrega ``work/**/memory/deltas/MEM-*.yaml`` ordenados por caminho.

    Id estável: ``memory:<stem do caminho relativo ao work_root>`` (primeiro
    arquivo vence em caso de stem duplicado, como o hermes faz com nomes).
    """
    root = Path(work_root)
    if not root.is_dir():
        return []
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(root.glob("**/memory/deltas/MEM-*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(raw, dict):
            continue
        rel_stem = path.relative_to(root).stem
        node_id = f"memory:{rel_stem}"
        if node_id in seen:
            continue
        seen.add(node_id)
        statements: list[str] = []
        entries = raw.get("entries")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    statements.append(str(entry.get("statement") or ""))
        nodes.append(
            {
                "id": node_id,
                "label": rel_stem,
                "kind": "memory",
                "work_item_id": str(raw.get("work_item_id") or ""),
                "author": str(raw.get("author") or ""),
                "recorded_at": str(raw.get("recorded_at") or ""),
                "statements": statements,
            }
        )
    return nodes


def _skill_skill_edges(skills: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """skill↔skill quando compartilham domínio (não vazio) ou agente assigned_to."""
    edges: list[tuple[str, str]] = []
    for i, a in enumerate(skills):
        for j in range(i + 1, len(skills)):
            b = skills[j]
            same_domain = bool(a["domain"]) and a["domain"] == b["domain"]
            shared_agents = set(a["assigned_to"]) & set(b["assigned_to"])
            if same_domain or shared_agents:
                edges.append((a["id"], b["id"]))
    return edges


def _memory_skill_edges(
    memories: list[dict[str, Any]], skills: list[dict[str, Any]]
) -> list[tuple[str, str]]:
    """memória→skill por sobreposição lexical; top-4 por score (bônus de nome exato)."""
    skill_meta = [
        (s["id"], _tokenize(f"{s['id']} {s['description']}"), s["id"].lower()) for s in skills
    ]
    edges: list[tuple[str, str]] = []
    for mem in memories:
        text = "\n".join(mem["statements"]).lower()
        if not text.strip():
            continue
        text_tokens = _tokenize(text)
        scored: list[tuple[int, str]] = []
        for skill_id, skill_tokens, name_lower in skill_meta:
            score = 0
            if name_lower in text:
                score += 6
            score += len(skill_tokens & text_tokens)
            if score > 0:
                scored.append((score, skill_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        for _, skill_id in scored[:_MAX_MEMORY_SKILL_EDGES]:
            edges.append((mem["id"], skill_id))
    return edges


def _memory_memory_edges(memories: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """memória↔memória quando pertencem ao mesmo work_item_id (não vazio)."""
    edges: list[tuple[str, str]] = []
    for i, a in enumerate(memories):
        for j in range(i + 1, len(memories)):
            b = memories[j]
            if a["work_item_id"] and a["work_item_id"] == b["work_item_id"]:
                edges.append((a["id"], b["id"]))
    return edges


def density_stats(node_ids: list[str], edges: list[tuple[str, str]]) -> dict[str, Any]:
    """Estatísticas de densidade espelhando o hermes: total, arestas, isolados e densidade."""
    linked: set[str] = set()
    for source, target in edges:
        linked.add(source)
        linked.add(target)
    node_count = len(node_ids)
    edge_count = len(edges)
    possible = node_count * (node_count - 1) // 2
    density = round(edge_count / possible, 3) if possible > 0 else 0.0
    isolated = sum(1 for node_id in node_ids if node_id not in linked)
    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "isolated": isolated,
        "density": density,
    }


def build_learning_graph(catalog_path: str | Path, work_root: str | Path) -> dict[str, Any]:
    """Payload completo do grafo de aprendizado do squad.

    Nós: skills do catálogo (id = nome) + deltas de memória
    (id = ``memory:<stem relativo>``). Arestas: skill↔skill (domínio ou
    assigned_to), memória→skill (sobreposição lexical, top-4), memória↔memória
    (mesmo work_item_id). Stats: density_stats sobre o grafo montado.
    """
    skills = _load_catalog(catalog_path)
    memories = _load_memory_deltas(work_root)
    edges = (
        _skill_skill_edges(skills)
        + _memory_skill_edges(memories, skills)
        + _memory_memory_edges(memories)
    )
    all_ids = [n["id"] for n in skills] + [n["id"] for n in memories]
    return {
        "nodes": skills + memories,
        "edges": [{"source": a, "target": b} for a, b in edges],
        "stats": density_stats(all_ids, edges),
    }
