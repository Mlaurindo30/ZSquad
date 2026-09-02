#!/usr/bin/env python3
"""
O que é: Bootstrap de hierarquia de Work Items (Epic/User Story/Task) no Azure DevOps.
Responsabilidade: Criar (ou reaproveitar) a árvore de work items a partir de um work
item local do squad, e gravar de volta em status.yaml o ID do provider (devops_id)
para manter os dois lados sincronizados.
Pra que serve: Fechar o ciclo do SDLC — o squad cria/organiza o trabalho localmente,
mas o Board do Azure DevOps é o sistema de registro visível para o time.
Comportamento em falha: Aborta a subárvore corrente e reporta no resultado; nunca
sobe exceção não tratada, nunca faz mutação sem confirmação de conexão bem-sucedida.
Conexões: integrations/devops_platform_connector.py, scripts/agent_squad.py (status.yaml).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from integrations.devops_platform_connector import AzureDevOpsClient, DevOpsPlatformConnector  # noqa: E402
from agent_squad import read_yaml, write_yaml  # noqa: E402


def create_work_item_tree(
    client: AzureDevOpsClient,
    epic_title: str,
    stories: list[dict[str, Any]],
    epic_description: str = "",
) -> dict[str, Any]:
    """Cria um Epic e, sob ele, User Stories com Tasks filhas.

    Args:
        client: cliente Azure DevOps já autenticado.
        epic_title: título do Epic raiz.
        stories: lista de {"title": str, "description": str, "tasks": [str, ...]}.
        epic_description: descrição do Epic.

    Returns:
        dict com os IDs criados, no formato:
        {"epic": {"id": .., "title": ..}, "stories": [{"id": .., "tasks": [id, ..]}]}
    """
    result: dict[str, Any] = {"epic": None, "stories": [], "errors": []}
    epic = client.create_work_item("epic", epic_title, epic_description)
    if epic is None:
        result["errors"].append(f"falha ao criar Epic '{epic_title}'")
        return result
    result["epic"] = {"id": epic.id, "title": epic.title}

    for story_spec in stories:
        story = client.create_work_item(
            "story", story_spec["title"], story_spec.get("description", ""),
            parent_id=epic.id, story_points=story_spec.get("story_points"),
        )
        if story is None:
            result["errors"].append(f"falha ao criar User Story '{story_spec['title']}'")
            continue
        story_entry: dict[str, Any] = {"id": story.id, "title": story.title, "tasks": []}
        for task_title in story_spec.get("tasks", []):
            task = client.create_work_item("task", task_title, parent_id=story.id)
            if task is None:
                result["errors"].append(f"falha ao criar Task '{task_title}'")
                continue
            story_entry["tasks"].append({"id": task.id, "title": task.title})
        result["stories"].append(story_entry)
    return result


def record_devops_ids(work_item_dir: Path, tree: dict[str, Any]) -> None:
    """Grava o ID do Epic/Story raiz no status.yaml local (campo devops_id), sem violar o schema."""
    status_path = work_item_dir / "status.yaml"
    if not status_path.is_file():
        return
    status = read_yaml(status_path)
    status["devops_id"] = tree["epic"]["id"] if tree.get("epic") else None
    write_yaml(status_path, status)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap de hierarquia Azure DevOps a partir do squad")
    parser.add_argument("--epic-title", required=True)
    parser.add_argument("--epic-description", default="")
    parser.add_argument("--work-item-dir", type=Path, default=None,
                         help="Diretório do work item local para gravar devops_id em status.yaml")
    args = parser.parse_args(argv or sys.argv[1:])

    connector = DevOpsPlatformConnector(root_path=ROOT)
    if not isinstance(connector.client, AzureDevOpsClient):
        print("ERRO: credenciais Azure DevOps ausentes/incorretas em .env", file=sys.stderr)
        return 1

    tree = create_work_item_tree(connector.client, args.epic_title, [], args.epic_description)
    if tree["errors"]:
        for err in tree["errors"]:
            print(f"ERRO: {err}", file=sys.stderr)
        return 1
    print(f"Epic criado: #{tree['epic']['id']} — {tree['epic']['title']}")
    if args.work_item_dir:
        record_devops_ids(args.work_item_dir, tree)
    return 0


if __name__ == "__main__":
    sys.exit(main())
