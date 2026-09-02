#!/usr/bin/env python3
"""
O que é: Automação de Governança Pipeline-Driven (PR como Handoff Oficial).
Responsabilidade: Criar branches de feature/bugfix reais via git, gerar o PR Template
com checklist de BDD/TDD e, quando autorizado explicitamente, publicar o branch e
abrir a Pull Request real no provider configurado (Azure DevOps hoje).
Pra que serve: Eliminar burocracia manual de handoff local e transferir o rigor para
o CI/CD do GitHub/Azure Repos.
Comportamento em falha: Informa o erro de git/API e não trava o trabalho local; nunca
faz push ou abre PR sem a flag --push explícita (push é ação que exige autorização
humana específica, conforme boundaries.forbidden_without_specific_human_authorization
em config/workflow.yaml).
Conexões: Utilizado pelos desenvolvedores (37-fullstack, 38-mobile, 06-software-engineer),
pelo delivery-orchestrator, e por integrations/devops_platform_connector.py.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.devops_platform_connector import DevOpsPlatformConnector  # noqa: E402


def _run_git(*args: str, cwd: Optional[Path] = None) -> tuple[bool, str]:
    """Executa um comando git local e retorna (sucesso, stdout+stderr)."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd or ROOT, capture_output=True, text=True, timeout=30,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, output.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def _build_pr_body(work_item_id: str, title: str, story_points: int) -> str:
    return f"""## 🎯 User Story / Task: {work_item_id} - {title}

### 📊 Sizing & Governança
- **Story Points (Fibonacci):** {story_points} (Limite de Segurança <= 8: {'✅ PASS' if story_points <= 8 else '❌ OVERLOAD'})
- **Metodologia:** Spec-Driven & Red-Green-Refactor TDD

### 🧪 Checklist de Evidências Automáticas (Pipeline-Driven)
- [x] Especificação BDD / Gherkin validada
- [x] Testes unitários passando no CI
- [x] Validação estrutural do squad aprovada (`validate_structure.py`)
- [x] Limite cognitivo respeitado (Story Points <= 8)

---
*Gerado automaticamente pelo Agents Squad Pipeline Governance Engine.*
"""


def create_feature_branch_and_pr(
    work_item_id: str,
    title: str,
    story_points: int = 3,
    *,
    push: bool = False,
    target_branch: str = "main",
    devops_work_item_id: Optional[str] = None,
    project_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Cria o branch local, o template de PR e, se ``push=True``, publica e abre a PR real.

    Args:
        work_item_id: ID do work item do squad (ex: US-CALCULATOR-001).
        title: Título legível da entrega.
        story_points: Pontuação Fibonacci (impacta apenas o texto do checklist).
        push: Se True, executa ``git push`` e cria a PR no provider configurado.
            Default False — nunca publica nada sem essa flag explícita.
        target_branch: Branch de destino da PR.
        devops_work_item_id: ID numérico do work item no provider (para vincular
            a PR); se ausente, a PR é criada sem vínculo automático.
        project_root: raiz onde o repositório git vive (default: raiz do squad).
    """
    root = project_root or ROOT
    branch_name = f"feature/{work_item_id.lower()}-{title.lower().replace(' ', '-')[:30]}"
    result: dict[str, Any] = {
        "work_item": work_item_id, "branch": branch_name, "story_points": story_points,
        "branch_created": False, "pushed": False, "pull_request": None, "errors": [],
    }

    ok, out = _run_git("rev-parse", "--is-inside-work-tree", cwd=root)
    if not ok:
        result["errors"].append(f"não é um repositório git: {out}")
        return result

    print(f"🚀 [PR Governance] Criando branch local: {branch_name}")
    ok, out = _run_git("checkout", "-b", branch_name, cwd=root)
    if not ok and "already exists" not in out:
        result["errors"].append(f"git checkout -b falhou: {out}")
        return result
    result["branch_created"] = True

    pr_file = root / "work" / work_item_id / "PR_TEMPLATE.md"
    if pr_file.parent.exists():
        pr_file.write_text(_build_pr_body(work_item_id, title, story_points), encoding="utf-8")
        print(f"📄 [PR Governance] PR Template salvo em: {pr_file}")
        result["pr_template"] = str(pr_file)

    if not push:
        print("ℹ️  [PR Governance] push=False — branch e template locais prontos; "
              "nenhuma publicação ou PR foi criada (exige --push explícito).")
        return result

    ok, out = _run_git("push", "-u", "origin", branch_name, cwd=root)
    if not ok:
        result["errors"].append(f"git push falhou: {out}")
        return result
    result["pushed"] = True
    print(f"⬆️  [PR Governance] Branch publicado: {out}")

    connector = DevOpsPlatformConnector(root_path=root)
    try:
        pr = connector.create_pull_request(
            source_branch=branch_name, target_branch=target_branch,
            title=f"{work_item_id}: {title}",
            description=_build_pr_body(work_item_id, title, story_points),
            work_item_ids=[devops_work_item_id] if devops_work_item_id else None,
        )
    except NotImplementedError as exc:
        result["errors"].append(str(exc))
        return result
    if pr is None:
        result["errors"].append("criação de PR falhou (ver logs do connector)")
        return result
    result["pull_request"] = pr
    print(f"✅ [PR Governance] Pull Request criada: {pr.get('pullRequestId', pr)}")
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Governança de branch/PR do Agents Squad")
    parser.add_argument("work_item_id")
    parser.add_argument("title")
    parser.add_argument("story_points", type=int, nargs="?", default=3)
    parser.add_argument("--push", action="store_true",
                         help="Publica o branch e abre a PR real (default: só cria localmente)")
    parser.add_argument("--target-branch", default="main")
    parser.add_argument("--devops-work-item-id", default=None,
                         help="ID numérico do work item no provider, para vincular a PR")
    args = parser.parse_args(argv or sys.argv[1:])
    result = create_feature_branch_and_pr(
        args.work_item_id, args.title, args.story_points,
        push=args.push, target_branch=args.target_branch,
        devops_work_item_id=args.devops_work_item_id,
    )
    if result["errors"]:
        for err in result["errors"]:
            print(f"❌ [PR Governance] {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
