"""Mutações edit/delete para nós do grafo de aprendizado (porta do hermes ``learning_mutations``).

O que é: operações de edição e remoção de nós sobre o grafo construído por
``scripts/learning_graph.py``, com backup JSON obrigatório antes de qualquer
mutação.

Responsabilidade: mapear um id estável (nome da skill ou
``memory:<stem-do-caminho-relativo>``) ao nó correspondente no grafo em
memória, gravar um backup JSON do estado original em ``backup_path`` e só então
aplicar o patch (edição) ou remover o nó e todas as arestas que o tocam
(remoção — nunca deixa arestas órfãs). Ids estáveis são imutáveis: um patch que
tente trocar ``id`` é rejeitado.

Pra que serve: permitir correção e curadoria do grafo de aprendizado (renomear
rótulos, ajustar atributos, aposentar skills/deltas obsoletos) com
reversibilidade garantida pelo arquivo de backup — o equivalente squad do
"journey edit/delete" do hermes.

Comportamento em falha: espelha o hermes — nunca levanta para o chamador;
retorna ``{"ok": False, "message": <motivo>}`` quando o nó não existe, o patch
não é um mapping não vazio, o patch tenta mudar o id, ou o backup falha (nesse
caso o grafo permanece intacto, mutação não ocorre). Sucesso retorna
``{"ok": True, ...}`` com o caminho do backup.

Conexões: consome grafos produzidos por ``scripts/learning_graph.py``
(chaves ``nodes``/``edges``); o backup JSON permite auditoria/rollback em
conjunto com o ledger do squad (``work/<WORK-ID>/documentation/delivery-ledger.md``).

Dependências & Imports: apenas stdlib (``json``, ``pathlib``). Sem PyYAML, sem
rede, sem LLM.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def parse_node_kind(node_id: str) -> str:
    """Classifica o id estável: ``memory:*`` → memória; caso contrário, skill."""
    return "memory" if node_id.startswith("memory:") else "skill"


def _find_node(graph: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    for node in graph.get("nodes") or []:
        if node.get("id") == node_id:
            return node
    return None


def _write_backup(graph: dict[str, Any], backup_path: str | Path) -> bool:
    """Grava o snapshot JSON do grafo; retorna False em falha de escrita."""
    try:
        Path(backup_path).write_text(
            json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        return False
    return True


def edit_node(
    graph: dict[str, Any], node_id: str, patch: dict[str, Any], backup_path: str | Path
) -> dict[str, Any]:
    """Aplica ``patch`` (mapping de atributos) ao nó ``node_id`` após backup.

    O backup é gravado ANTES da mutação e captura o estado original. O campo
    ``id`` pode constar no patch apenas com o mesmo valor (ids estáveis).
    """
    node = _find_node(graph, node_id)
    if node is None:
        return {"ok": False, "message": f"node '{node_id}' not found"}
    if not isinstance(patch, dict) or not patch:
        return {"ok": False, "message": "patch must be a non-empty mapping"}
    if "id" in patch and patch["id"] != node_id:
        return {"ok": False, "message": "stable ids cannot be changed"}
    if not _write_backup(graph, backup_path):
        return {"ok": False, "message": f"backup failed at {backup_path}"}
    node.update(patch)
    return {"ok": True, "message": f"updated '{node_id}'", "backup": str(backup_path)}


def delete_node(graph: dict[str, Any], node_id: str, backup_path: str | Path) -> dict[str, Any]:
    """Remove o nó ``node_id`` e toda aresta que o toca, após backup.

    Garante ausência de arestas órfãs pós-remoção (sem link apontando para id
    inexistente). ``stats`` do grafo não é recomputado — re monte o grafo com
    ``build_learning_graph`` para estatísticas atualizadas.
    """
    node = _find_node(graph, node_id)
    if node is None:
        return {"ok": False, "message": f"node '{node_id}' not found"}
    if not _write_backup(graph, backup_path):
        return {"ok": False, "message": f"backup failed at {backup_path}"}
    graph["nodes"] = [n for n in graph.get("nodes") or [] if n.get("id") != node_id]
    kept_edges: list[dict[str, Any]] = []
    removed = 0
    for edge in graph.get("edges") or []:
        if node_id in (edge.get("source"), edge.get("target")):
            removed += 1
        else:
            kept_edges.append(edge)
    graph["edges"] = kept_edges
    return {
        "ok": True,
        "message": f"deleted '{node_id}' and {removed} edge(s)",
        "backup": str(backup_path),
        "edges_removed": removed,
    }
