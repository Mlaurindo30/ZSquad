#!/usr/bin/env python3
"""
O que é: Orquestrador do ciclo de vida completo Azure DevOps — create → import → configure.
Responsabilidade: Encadear azure_devops_project_creator, azure_devops_repo_importer e
azure_devops_project_setup.apply_all() num fluxo ordenado, com rollback seletivo em caso
de falha após a criação do projeto.
Pra que serve: Provisionar um projeto Azure DevOps novo com repositório importado e
configuração completa (áreas, iterações, políticas de PR, identidades, board, etc.)
a partir de um único comando.
Comportamento em falha: Captura o erro exato antes de acionar rollback; nãofabrica
cenários. Rollback só é executado se step 1 (criação) já tiver succeed.
Dry-run: Imprime o plano de execução sem realizar chamadas de API.
Conexões: scripts/azure_devops_project_creator.py, scripts/azure_devops_repo_importer.py,
scripts/azure_devops_project_setup.py, integrations/devops_platform_connector.py,
templates/devops.yaml.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import yaml

from integrations.devops_platform_connector import (
    AzureDevOpsClient,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"falha ao ler config {path}: {exc}") from exc


class AzureDevOpsLifecycle:
    """Orquestra o ciclo de vida completo de um projeto Azure DevOps."""

    def __init__(
        self,
        project_name: str,
        org_url: str,
        repo_url: str,
        config_path: Optional[Path],
        pat: str,
        dry_run: bool = False,
    ):
        self.project_name = project_name
        self.org_url = org_url.rstrip("/")
        self.repo_url = repo_url
        self.pat = pat
        self.dry_run = dry_run
        self.config_path = config_path or (ROOT / "templates" / "devops.yaml")
        self.config: dict[str, Any] = {}
        self.results: list[dict[str, Any]] = []
        self._created_project_id: Optional[str] = None
        self._project_url: Optional[str] = None
        self._repo_url: Optional[str] = None
        self._default_branch: Optional[str] = None
        self._client: Optional[AzureDevOpsClient] = None

    def _record(self, step: str, status: str, detail: Any) -> None:
        self.results.append({"step": step, "status": status, "detail": detail})

    def _load_config(self) -> None:
        if not self.config_path.is_file():
            raise FileNotFoundError(f"arquivo de config não encontrado: {self.config_path}")
        self.config = _load_yaml(self.config_path)

    def _build_client(self) -> AzureDevOpsClient:
        self._client = AzureDevOpsClient(
            organization=self.org_url,
            project=self.project_name,
            pat_token=self.pat,
            config=self.config,
        )
        return self._client

    def _resolve_org_base(self) -> str:
        org = self.org_url
        if not org.startswith("http"):
            org = f"https://dev.azure.com/{org}"
        return org.rstrip("/")

    # -------------------------------------------------------------------------
    # Phase 1 — create project
    # -------------------------------------------------------------------------

    def _phase1_create_project(self) -> Optional[dict[str, Any]]:
        self._record("phase1.create_project", "running", {"project": self.project_name})
        if self.dry_run:
            self._record(
                "phase1.create_project",
                "dry-run",
                f"criaria projeto '{self.project_name}' em {self.org_url}",
            )
            self._created_project_id = "dry-run-project-id"
            self._project_url = f"{self._resolve_org_base()}/{self.project_name}"
            return {"project_id": "dry-run-project-id", "project_url": self._project_url}

        try:
            from azure_devops_project_creator import create_project

            result = create_project(
                org_url=self.org_url,
                project_name=self.project_name,
                process_type=self.config.get("process_template", "Scrum"),
                pat=self.pat,
            )
            if not result or not result.get("project_id"):
                raise RuntimeError(f"resposta inválida do creator: {result}")
            self._created_project_id = result["project_id"]
            self._project_url = result.get("project_url") or (
                f"{self._resolve_org_base()}/{self.project_name}"
            )
            self._record(
                "phase1.create_project",
                "ok",
                {"project_id": self._created_project_id, "project_url": self._project_url},
            )
            return result
        except Exception as exc:
            self._record("phase1.create_project", "error", str(exc))
            raise

    # -------------------------------------------------------------------------
    # Phase 2 — wait for project to be ready
    # -------------------------------------------------------------------------

    def _phase2_wait_ready(self, project_id: str) -> bool:
        self._record("phase2.wait_ready", "running", {"project_id": project_id})
        if self.dry_run:
            self._record("phase2.wait_ready", "dry-run", "aguarda 3 ciclos de poll")
            return True

        org_base = self._resolve_org_base()
        max_attempts = 10
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self._client._request(
                    "GET",
                    f"{org_base}/_apis/projects/{project_id}?api-version=7.1",
                )
                if resp and resp.get("state") == "wellFormed":
                    self._record(
                        "phase2.wait_ready",
                        "ok",
                        {"attempt": attempt, "state": resp.get("state")},
                    )
                    return True
            except Exception as exc:
                self._record(
                    "phase2.wait_ready",
                    "warning",
                    {"attempt": attempt, "error": str(exc)},
                )
            time.sleep(5)

        self._record(
            "phase2.wait_ready",
            "error",
            f"projeto não ficou 'wellFormed' após {max_attempts} tentativas",
        )
        return False

    # -------------------------------------------------------------------------
    # Phase 3 — import repository
    # -------------------------------------------------------------------------

    def _phase3_import_repo(self) -> Optional[dict[str, Any]]:
        self._record(
            "phase3.import_repo",
            "running",
            {"repo_url": self.repo_url, "project": self.project_name},
        )
        if self.dry_run:
            self._record(
                "phase3.import_repo",
                "dry-run",
                f"importaria repositório de '{self.repo_url}' para '{self.project_name}'",
            )
            self._repo_url = f"{self._resolve_org_base()}/{self.project_name}/_git/{self.project_name}"
            self._default_branch = "refs/heads/main"
            return {"repo_url": self._repo_url, "default_branch": self._default_branch}

        try:
            from azure_devops_repo_importer import import_repo

            repo_import_cfg = self.config.get("repo_import", {})
            repo_name = repo_import_cfg.get("repo_name", self.project_name)
            credentials = repo_import_cfg.get("credentials", {"type": "none"})

            result = import_repo(
                org_url=self.org_url,
                project_id=self.project_name,
                repo_name=repo_name,
                remote_url=self.repo_url,
                credentials=credentials,
                pat=self.pat,
            )
            if not result or not result.get("repo_id"):
                raise RuntimeError(f"resposta inválida do importer: {result}")
            self._repo_url = result.get("repo_url") or (
                f"{self._resolve_org_base()}/{self.project_name}/_git/"
                f"{result.get('repo_name', self.project_name)}"
            )
            self._default_branch = result.get("default_branch", "refs/heads/main")
            self._record(
                "phase3.import_repo",
                "ok",
                {
                    "repo_id": result.get("repo_id"),
                    "repo_url": self._repo_url,
                    "default_branch": self._default_branch,
                },
            )
            return result
        except Exception as exc:
            self._record("phase3.import_repo", "error", str(exc))
            raise

    # -------------------------------------------------------------------------
    # Phase 4 — apply full configuration
    # -------------------------------------------------------------------------

    def _phase4_configure(self) -> dict[str, Any]:
        self._record("phase4.configure", "running", {"project": self.project_name})
        if self.dry_run:
            self._record(
                "phase4.configure",
                "dry-run",
                "aplicaria azure_devops_project_setup.apply_all()",
            )
            return {"generated_at": _now(), "mode": "dry-run", "steps": []}

        try:
            from azure_devops_project_setup import (
                AzureDevOpsProjectSetup,
                load_setup_config,
            )

            self._build_client()
            setup_config = load_setup_config(ROOT)
            merged = {**setup_config, **self.config}
            setup = AzureDevOpsProjectSetup(self._client, merged)
            report = setup.apply()
            for step in report.get("steps", []):
                self._record(f"configure.{step['step']}", step["status"], step["detail"])
            return report
        except Exception as exc:
            self._record("phase4.configure", "error", str(exc))
            raise

    # -------------------------------------------------------------------------
    # Rollback
    # -------------------------------------------------------------------------

    def _rollback(self, failure_step: str) -> None:
        self._record("rollback", "running", {"triggered_by": failure_step})
        if not self._created_project_id:
            self._record("rollback", "skip", "nenhum projeto criado para reverter")
            return
        if self.dry_run:
            self._record(
                "rollback",
                "dry-run",
                f"deletaria projeto '{self._created_project_id}'",
            )
            return

        try:
            org_base = self._resolve_org_base()
            result = self._client._request(
                "DELETE",
                f"{org_base}/_apis/projects/{self._created_project_id}?api-version=7.1",
            )
            if result is None and self._client._request(
                "GET",
                f"{org_base}/_apis/projects/{self._created_project_id}?api-version=7.1",
            ):
                self._record("rollback", "error", "projeto ainda existe após delete")
            else:
                self._record(
                    "rollback",
                    "ok",
                    {"deleted_project_id": self._created_project_id},
                )
        except Exception as exc:
            self._record("rollback", "error", str(exc))

    # -------------------------------------------------------------------------
    # Public run()
    # -------------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Executa o ciclo completo: create → wait → import → configure.

        Returns:
            dict com work_item_ids, repo_url, default_branch, project_url e metadata.
        """
        self._load_config()
        project_id: Optional[str] = None

        try:
            # Phase 1
            phase1_result = self._phase1_create_project()
            project_id = phase1_result.get("project_id") if phase1_result else None

            # Phase 2 (only if phase1 succeeded and not dry-run)
            if project_id and not self.dry_run:
                if not self._phase2_wait_ready(project_id):
                    raise RuntimeError(
                        f"projeto {project_id} não ficou pronto após espera"
                    )

            # Phase 3
            phase3_result = self._phase3_import_repo()

            # Phase 4
            self._phase4_configure()

            ok_count = sum(1 for r in self.results if r["status"] == "ok")
            err_count = sum(1 for r in self.results if r["status"] == "error")
            summary = {"ok": ok_count, "skip": sum(1 for r in self.results if r["status"] in {"skip", "dry-run"}), "error": err_count}

            return {
                "generated_at": _now(),
                "mode": "dry-run" if self.dry_run else "apply",
                "project_name": self.project_name,
                "project_url": self._project_url,
                "repo_url": self._repo_url,
                "default_branch": self._default_branch,
                "work_item_ids": [],  # populated by setup phase if applicable
                "summary": summary,
                "steps": self.results,
            }

        except Exception as exc:
            self._record("lifecycle", "error", str(exc))
            step_that_failed = next(
                (r["step"] for r in reversed(self.results) if r["status"] == "error"),
                "unknown",
            )
            if self._created_project_id and (
                step_that_failed.startswith("phase2")
                or step_that_failed.startswith("phase3")
                or step_that_failed.startswith("phase4")
            ):
                self._rollback(step_that_failed)
            return {
                "generated_at": _now(),
                "mode": "dry-run" if self.dry_run else "apply",
                "project_name": self.project_name,
                "project_url": self._project_url,
                "error": str(exc),
                "rollback_triggered": bool(self._created_project_id),
                "steps": self.results,
            }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ciclo de vida completo Azure DevOps: create → import → configure"
    )
    parser.add_argument("--project-name", required=True, help="Nome do projeto Azure DevOps")
    parser.add_argument(
        "--org-url", required=True, help="URL da organização (ex: https://dev.azure.com/cbvgas)"
    )
    parser.add_argument(
        "--repo-url", required=True, help="URL do repositório Git de origem para import"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Caminho para devops.yaml customizado (default: templates/devops.yaml)",
    )
    parser.add_argument(
        "--pat", required=True, help="Personal Access Token do Azure DevOps"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Imprime plano sem realizar chamadas de API"
    )
    parser.add_argument("--json", action="store_true", help="Imprime relatório em JSON")

    args = parser.parse_args(argv or sys.argv[1:])

    lifecycle = AzureDevOpsLifecycle(
        project_name=args.project_name,
        org_url=args.org_url,
        repo_url=args.repo_url,
        config_path=args.config,
        pat=args.pat,
        dry_run=args.dry_run,
    )

    report = lifecycle.run()

    if args.json:
        import json as _json

        print(_json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"mode={report.get('mode')} project={report.get('project_name')}")
        for step in report.get("steps", []):
            print(f"[{step['status']}] {step['step']}: {step['detail']}")
        if "summary" in report:
            print(report["summary"])
        if "error" in report:
            print(f"ERRO: {report['error']}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
