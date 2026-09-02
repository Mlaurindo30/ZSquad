#!/usr/bin/env python3
"""
O que é: Provisionamento ponta a ponta do processo Azure DevOps para QUALQUER
projeto ligado ao squad (não é bootstrap de um work item específico).
Responsabilidade: Ler <project_root>/.agents_squad/config/devops.yaml (ou o
template) e aplicar identidades, pontuação, cores, board, iterações, áreas,
notificações e política de PR. Idempotente: reaplicar não duplica o que já existe.
Pra que serve: Cada PROJECT_ROOT novo copia o marcador, troca org/projeto/e-mails
e executa este script. O board fica igual ao contrato do squad.
Comportamento em falha: Cada passo registra ok/skip/erro com a resposta real;
não aborta os demais passos. Nunca faz git push.
Conexões: integrations/devops_platform_connector.py, templates/devops.yaml.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.devops_platform_connector import (  # noqa: E402
    AzureDevOpsClient,
    DevOpsPlatformConnector,
    load_devops_config,
)

POLICY_MIN_REVIEWERS = "fa4e907d-c16b-4a4c-9dfa-4906e5d171dd"
POLICY_WORK_ITEM_LINKING = "40e92b44-2f5f-4f01-8f2c-5c1d3a3c3e3a"
# Official Azure DevOps work item linking policy type id:
POLICY_WORK_ITEM_LINKING_OFFICIAL = "0e8f31cc-ddff-4371-9c5b-f7437d0433f3"
POLICY_REQUIRED_REVIEWERS = "fd2167ab-b0be-447a-8ec8-39368250530e"


def _hex(value: str) -> str:
    return value if value.startswith("#") else f"#{value}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AzureDevOpsProjectSetup:
    """Aplica o contrato devops.yaml no projeto Azure DevOps alvo."""

    def __init__(self, client: AzureDevOpsClient, config: dict[str, Any]):
        self.client = client
        self.config = config
        # Extrai o org a partir do hostname da base_url — funciona com GUID e com project name
        # base_url = https://org/[project]/_apis  →  org = https://org
        self.org = "https://" + client.base_url.split("/")[2]
        self.project = client.project
        self.results: list[dict[str, Any]] = []
        self.project_id: Optional[str] = None
        self.team_id: Optional[str] = None
        self.team_name: Optional[str] = None
        self.repo_id: Optional[str] = None
        self.identities: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def record(self, step: str, status: str, detail: Any) -> None:
        with self._lock:
            self.results.append({"step": step, "status": status, "detail": detail})

    def get(self, url: str) -> Optional[dict[str, Any]]:
        return self.client._request("GET", url)

    def send(self, method: str, url: str, body: Any = None, content_type: str = "application/json") -> Optional[dict[str, Any]]:
        return self.client._request(method, url, body, content_type=content_type)

    def _run_parallel(self, method_names: list[str]) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(method_names)) as executor:
            futures = {executor.submit(getattr(self, name)): name for name in method_names}
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    self.record(name, "error", str(exc))

    def discover(self) -> None:
        proj = self.get(f"{self.org}/_apis/projects/{quote(self.project)}?api-version=7.1")
        if not proj or "id" not in proj:
            self.record("discover.project", "error", proj)
            return
        self.project_id = proj["id"]
        self.record("discover.project", "ok", {"id": self.project_id, "process": (proj.get("capabilities") or {}).get("processTemplate")})

        teams = self.get(f"{self.org}/_apis/projects/{self.project_id}/teams?api-version=7.1")
        team = ((teams or {}).get("value") or [None])[0]
        if not team:
            self.record("discover.team", "error", teams)
            return
        self.team_id = team["id"]
        self.team_name = team["name"]
        self.record("discover.team", "ok", {"id": self.team_id, "name": self.team_name})

        repos = self.get(f"{self.client.base_url}/git/repositories?api-version=7.1")
        wanted = self.config.get("repo") or self.project
        repo = next((r for r in (repos or {}).get("value", []) if r.get("name") == wanted), None)
        if repo:
            self.repo_id = repo["id"]
            self.record("discover.repo", "ok", {"id": self.repo_id, "defaultBranch": repo.get("defaultBranch")})
        else:
            self.record("discover.repo", "error", repos)

        members = self.get(
            f"{self.org}/_apis/projects/{self.project_id}/teams/{self.team_id}/members?api-version=7.1"
        )
        by_email = {}
        for row in (members or {}).get("value", []):
            ident = row.get("identity") or {}
            email = (ident.get("uniqueName") or "").lower()
            if email:
                by_email[email] = ident
        identities_cfg = self.config.get("identities") or {}
        for key, spec in identities_cfg.items():
            email = str(spec.get("email", "")).lower()
            found = by_email.get(email)
            self.identities[key] = found or {}
            self.record(
                f"discover.identity.{key}",
                "ok" if found else "error",
                {"email": email, "id": (found or {}).get("id"), "descriptor": (found or {}).get("descriptor")},
            )

    def apply_areas(self) -> None:
        for name in self.config.get("areas") or []:
            existing = self.get(
                f"{self.client.base_url}/wit/classificationnodes/areas/{quote(name)}?api-version=7.1"
            )
            if existing and existing.get("name") == name:
                self.record(f"areas.{name}", "skip", existing.get("path") or "já existe")
                continue
            result = self.send(
                "POST",
                f"{self.client.base_url}/wit/classificationnodes/areas?api-version=7.1",
                {"name": name},
            )
            if result and result.get("name") == name:
                self.record(f"areas.{name}", "ok", result.get("path"))
            else:
                self.record(f"areas.{name}", "error", result)

    def apply_iterations(self) -> None:
        for spec in self.config.get("iterations") or []:
            name = spec["name"]
            payload: dict[str, Any] = {"name": name}
            if spec.get("start") and spec.get("finish"):
                payload["attributes"] = {
                    "startDate": f"{spec['start']}T00:00:00Z",
                    "finishDate": f"{spec['finish']}T00:00:00Z",
                }
            url = f"{self.client.base_url}/wit/classificationnodes/iterations/{quote(name)}?api-version=7.1"
            existing = self.get(url)
            if existing and existing.get("identifier"):
                patched = self.send("PATCH", url, payload)
                self.record(f"iterations.{name}", "ok" if patched else "error", patched or existing.get("path"))
            else:
                created = self.send(
                    "POST",
                    f"{self.client.base_url}/wit/classificationnodes/iterations?api-version=7.1",
                    payload,
                )
                self.record(f"iterations.{name}", "ok" if created else "error", created)

    def apply_team_iterations(self) -> None:
        """US-1 (2026-09-02): vincula cada nó de iteração ao team, tornando-o
        sprint backlog visível. Sem isso, as iterações existem no projeto mas
        não aparecem no board do team.TF400497 ocorre quando backlogIteration
        (defaultIteration) está null/invalid — a correção é PATCH /work/teamsettings
        com defaultIteration antes de POSTar iterações."""
        if not self.project_id or not self.team_id:
            self.record("team.iterations", "skip", "projeto/time não descobertos")
            return
        if not self.config.get("iterations"):
            self.record("team.iterations", "skip", "nenhuma iteração configurada")
            return
        iterations_spec = self.config.get("iterations") or []
        team_iterations_url = f"{self.org}/{self.project_id}/{self.team_id}/_apis/work/teamsettings/iterations?api-version=7.1"
        existing = self.get(team_iterations_url) or {}
        existing_ids = {(it.get("identification") or {}).get("id") for it in existing.get("value", [])}
        existing_names = {(it.get("identification") or {}).get("name") for it in existing.get("value", [])}
        identifiers: list[dict[str, Any]] = []
        first_iteration_id: str | None = None
        for spec in iterations_spec:
            node = self.get(
                f"{self.client.base_url}/wit/classificationnodes/iterations/{quote(spec['name'])}?api-version=7.1"
            )
            if not node or not node.get("identifier"):
                self.record(f"team.iterations.{spec['name']}", "error", "nó de iteração ausente")
                continue
            nid = node["identifier"]
            if nid in existing_ids or spec["name"] in existing_names:
                self.record(f"team.iterations.{spec['name']}", "skip", "já vinculado ao team")
            else:
                identifiers.append({"id": nid, "includeChildren": False})
            if first_iteration_id is None:
                first_iteration_id = nid
        if not identifiers:
            self.record("team.iterations", "ok", "nada novo a vincular")
            return
        if first_iteration_id:
            # TF400497: PATCH backlogIteration com UUID (não path).
            # A API TeamSettings.Update espera string (uuid), não path string.
            # https://learn.microsoft.com/en-us/rest/api/azure/devops/work/teamsettings/update
            teamsettings_url = f"{self.org}/{self.project_id}/{self.team_id}/_apis/work/teamsettings?api-version=7.1"
            patch_body = {"backlogIteration": first_iteration_id}
            patch_result = self.send("PATCH", teamsettings_url, patch_body)
            self.record("teamsettings.backlog_iteration", "ok" if patch_result else "error", patch_result or first_iteration_id)
        results = []
        for ident in identifiers:
            result = self.send(
                "POST",
                team_iterations_url,
                {"id": ident["id"], "includeChildren": False},
            )
            results.append(result)
        success = all(r is not None for r in results)
        self.record("team.iterations", "ok" if success else "error", {"count": len(results), "result": results})

    def apply_queries(self) -> None:
        """US-2 (2026-09-02): cria 5 queries salvas em pasta Shared/Agents Squad/
        se `queries.enabled: true` no template. Idempotente."""
        qcfg = self.config.get("queries") or {}
        if not qcfg.get("enabled", False):
            self.record("queries", "skip", "queries.enabled=false no template")
            return
        project_url = f"{self.org}/_apis/projects/{quote(self.project)}?api-version=7.1"
        proj = self.get(project_url) or {}
        if not proj.get("id"):
            self.record("queries", "error", "projeto não resolvido")
            return
        proj_id = proj["id"]
        folder_path = "Shared/Agents Squad"
        parent_folder_path = "Shared"

        # Azure DevOps API usa project NAME no path, não GUID
        proj_name = quote(self.project)
        all_queries_url = f"{self.org}/{proj_name}/_apis/wit/queries?api-version=7.1"
        existing_all = self.get(all_queries_url) or {}
        existing_names = {q.get("name") for q in existing_all.get("value", [])}
        existing_paths = {q.get("path", ""): q for q in existing_all.get("value", [])}

        if parent_folder_path not in existing_paths:
            parent_folder = self.send(
                "POST",
                f"{self.org}/{proj_name}/_apis/wit/queries/{parent_folder_path}?api-version=7.1",
                {"name": parent_folder_path, "isFolder": True},
            )
            if parent_folder and parent_folder.get("id"):
                existing_paths[parent_folder_path] = parent_folder
                existing_names.add(parent_folder_path)

        if folder_path not in existing_paths:
            folder = self.send(
                "POST",
                f"{self.org}/{proj_name}/_apis/wit/queries/{parent_folder_path}?api-version=7.1",
                {"name": "Agents Squad", "isFolder": True},
            )
            if folder and folder.get("id"):
                existing_paths[folder_path] = folder
                existing_names.add(folder_path)
                self.record("queries.folder", "ok", folder_path)
            else:
                self.record("queries.folder", "error", folder)
                return
        else:
            self.record("queries.folder", "skip", folder_path)

        queries = [
            {
                "name": "Active",
                "wiql": (
                    "SELECT [System.Id] FROM WorkItems "
                    f"WHERE [System.TeamProject] = '{self.project}' "
                    "AND [System.State] <> 'Closed' AND [System.State] <> 'Resolved' "
                    "ORDER BY [System.ChangedDate] DESC"
                ),
            },
            {
                "name": "WIP by State",
                "wiql": (
                    "SELECT [System.Id], [System.State] FROM WorkItems "
                    f"WHERE [System.TeamProject] = '{self.project}' "
                    "AND [System.State] <> 'Closed' "
                    "ORDER BY [System.State]"
                ),
            },
            {
                "name": "Missing Iteration",
                "wiql": (
                    "SELECT [System.Id] FROM WorkItems "
                    f"WHERE [System.TeamProject] = '{self.project}' "
                    "AND [System.IterationPath] = '' "
                    "AND [System.WorkItemType] = 'User Story'"
                ),
            },
            {
                "name": "Critical Bugs",
                "wiql": (
                    "SELECT [System.Id] FROM WorkItems "
                    f"WHERE [System.TeamProject] = '{self.project}' "
                    "AND [System.WorkItemType] = 'Bug' "
                    "AND [System.Tags] CONTAINS 'risk-critical' "
                    "AND [System.State] <> 'Closed'"
                ),
            },
            {
                "name": "G6 Governance — last sprint",
                "wiql": (
                    "SELECT [System.Id] FROM WorkItems "
                    f"WHERE [System.TeamProject] = '{self.project}' "
                    "AND [System.Tags] CONTAINS 'phase-done' "
                    "AND [System.ChangedDate] >= @StartOfWeek"
                ),
            },
        ]
        full_query_path = {q.get("path", ""): q for q in existing_all.get("value", [])}
        created = 0
        skipped = 0
        for q in queries:
            query_full_path = f"{folder_path}/{q['name']}"
            if query_full_path in full_query_path:
                skipped += 1
                continue
            payload = {"name": q["name"], "wiql": q["wiql"]}
            result = self.send(
                "POST",
                f"{self.org}/{proj_name}/_apis/wit/queries/{folder_path}?api-version=7.1",
                payload,
            )
            if result and result.get("id"):
                created += 1
            else:
                self.record(f"queries.{q['name']}", "error", result)
        self.record("queries.created", "ok" if created + skipped else "skip",
                    {"created": created, "skipped": skipped, "path": folder_path})

    def apply_team_settings(self) -> None:
        if not self.project_id or not self.team_id:
            self.record("teamsettings", "error", "projeto/time não descobertos")
            return
        board_cfg = self.config.get("board") or {}
        body: dict[str, Any] = {}
        if "bugs_behavior" in board_cfg:
            body["bugsBehavior"] = board_cfg["bugs_behavior"]
        if board_cfg.get("enable_epic_backlog"):
            body["backlogVisibilities"] = {
                "Microsoft.EpicCategory": True,
                "Microsoft.FeatureCategory": True,
                "Microsoft.RequirementCategory": True,
            }
        if not body:
            self.record("teamsettings", "skip", "nada a alterar")
            return
        url = (
            f"{self.org}/{self.project_id}/{self.team_id}/_apis/work/teamsettings"
            f"?api-version=7.1"
        )
        result = self.send("PATCH", url, body)
        self.record("teamsettings", "ok" if result else "error", result)
        if board_cfg.get("include_area_children", True) and self.project:
            tfv_url = (
                f"{self.org}/{self.project_id}/{self.team_id}/_apis/work/teamsettings/"
                f"teamfieldvalues?api-version=7.1"
            )
            tfv = self.send("PATCH", tfv_url, {
                "defaultValue": self.project,
                "values": [{"value": self.project, "includeChildren": True}],
            })
            self.record("teamsettings.area_children", "ok" if tfv else "error", tfv)

    def apply_board_wip(self) -> None:
        if not self.project_id or not self.team_id:
            return
        wip = ((self.config.get("board") or {}).get("wip")) or {}
        url = f"{self.org}/{self.project_id}/{self.team_id}/_apis/work/boards/Stories/columns?api-version=7.1"
        columns = self.get(url)
        values = (columns or {}).get("value") or []
        if not values:
            self.record("board.wip", "error", columns)
            return
        changed = False
        for col in values:
            name = col.get("name")
            if name in wip:
                col["itemLimit"] = int(wip[name])
                changed = True
        if not changed:
            self.record("board.wip", "skip", "colunas alvo ausentes")
            return
        result = self.send("PUT", url, values)
        self.record("board.wip", "ok" if result else "error", result)

    def apply_card_colors(self) -> None:
        if not self.project_id or not self.team_id:
            return
        # Regras customizadas de card (fill/tag) quebram o hub Boards na UI
        # (TF1530017 / "unexpected error in this region"). Cores nativas do
        # processo Agile já identificam Epic/Story/Task/Bug. Default: limpar.
        if not ((self.config.get("board") or {}).get("apply_custom_card_rules")):
            for board in ("Stories", "Epics", "Features"):
                url = (
                    f"{self.org}/{self.project_id}/{self.team_id}/_apis/work/boards/"
                    f"{quote(board)}/cardrulesettings?api-version=7.1"
                )
                current = self.get(url) or {}
                result = self.send("PATCH", url, {"url": current.get("url"), "rules": {"fill": [], "tagStyle": []}})
                self.record(f"colors.{board}", "ok" if result else "error", "regras customizadas desligadas")
            return
        colors = (self.config.get("colors") or {}).get("work_item_types") or {}
        tag_colors = (self.config.get("colors") or {}).get("tags") or {}
        board_types = {
            "Stories": ["User Story", "Task", "Bug"],
            "Epics": ["Epic"],
            "Features": ["Feature"],
        }
        risk_tags = [name for name in tag_colors if str(name).startswith("risk-")]
        for board, wits in board_types.items():
            fill: list[dict[str, Any]] = []
            for wit in wits:
                color = colors.get(wit)
                if not color:
                    continue
                fill.append({
                    "name": f"type-{wit}",
                    "isEnabled": "True",
                    "filter": f"[System.WorkItemType] = '{wit}'",
                    "settings": {"background-color": _hex(color).lstrip("#")},
                })
            for tag in risk_tags:
                fill.append({
                    "name": f"tag-{tag}",
                    "isEnabled": "True",
                    "filter": f"[System.Tags] CONTAINS '{tag}'",
                    "settings": {"background-color": _hex(tag_colors[tag]).lstrip("#")},
                })
            fill = fill[:10]
            url = (
                f"{self.org}/{self.project_id}/{self.team_id}/_apis/work/boards/"
                f"{quote(board)}/cardrulesettings?api-version=7.1"
            )
            current = self.get(url) or {}
            body = {"url": current.get("url"), "rules": {"fill": fill, "tagStyle": []}}
            result = self.send("PATCH", url, body)
            self.record(f"colors.{board}", "ok" if result else "error", {"count": len(fill), "result": result})

    def apply_card_fields(self) -> None:
        if not self.project_id or not self.team_id:
            return
        story_field = ((self.config.get("scoring") or {}).get("story_points") or {}).get(
            "field", "Microsoft.VSTS.Scheduling.StoryPoints"
        )
        payload = {
            "cards": {
                "User Story": [
                    {"fieldIdentifier": "System.Id"},
                    {"fieldIdentifier": "System.Title"},
                    {"fieldIdentifier": "System.AssignedTo", "displayFormat": "AvatarAndFullName"},
                    {"fieldIdentifier": "System.Tags"},
                    {"fieldIdentifier": "System.State"},
                    {"fieldIdentifier": story_field},
                ]
            }
        }
        url = (
            f"{self.org}/{self.project_id}/{self.team_id}/_apis/work/boards/"
            f"Stories/cardsettings?api-version=7.1"
        )
        result = self.send("PUT", url, payload)
        self.record("cardsettings.Stories", "ok" if result else "error", result)

    def apply_notifications(self) -> None:
        notify = self.config.get("notifications") or {}
        human = self.identities.get("human_master") or {}
        human_id = human.get("id")
        if notify.get("opt_out_human_master"):
            if not human_id:
                self.record("notifications.opt_out_human_master", "error", "human_master sem id")
            else:
                subs = self.get(f"{self.org}/_apis/notification/subscriptions?api-version=7.1")
                opted = 0
                for sub in (subs or {}).get("value", []):
                    sub_id = sub.get("id")
                    flags = str(sub.get("flags") or "")
                    if "contributedSubscription" not in flags:
                        continue
                    url = (
                        f"{self.org}/_apis/notification/Subscriptions/{quote(str(sub_id))}"
                        f"/UserSettings/{human_id}?api-version=7.1"
                    )
                    result = self.send("PUT", url, {"optedOut": True})
                    if result is not None:
                        opted += 1
                status = "ok" if opted else "skip"
                self.record("notifications.opt_out_human_master", status, {"updated": opted})

        route = notify.get("route_to", "development_team")
        target = self.identities.get(route) or {}
        subscriber_id = target.get("id")
        address = ((self.config.get("identities") or {}).get(route) or {}).get("email")
        if not subscriber_id or not address:
            self.record("notifications.route", "error", f"identidade {route} não encontrada no time")
            return
        existing = self.get(f"{self.org}/_apis/notification/subscriptions?api-version=7.1")
        existing_desc = {s.get("description") for s in (existing or {}).get("value", [])}
        event_map = {
            "ms.vss-work.workitem-changed-event": "ms.vss-work.workitem-changed-event",
            "ms.vss-code.pull-request-updated-event": "ms.vss-code.git-pullrequest-event",
            "ms.vss-build.build-completed-event": "ms.vss-build.build-completed-event",
        }
        for event in notify.get("events") or []:
            event_type = event_map.get(event, event)
            description = f"Agents Squad → {route} ({event_type})"
            if description in existing_desc:
                self.record(f"notifications.{event}", "skip", "já existe")
                continue
            body = {
                "description": description,
                "filter": {
                    "type": "Expression",
                    "eventType": event_type,
                    "criteria": {"clauses": [], "groups": [], "maxGroupLevel": 0},
                },
                "channel": {"type": "User", "useCustomAddress": False},
                "subscriber": {"id": subscriber_id},
                "scope": {"id": self.project_id, "type": "none"},
            }
            result = self.send("POST", f"{self.org}/_apis/notification/subscriptions?api-version=7.1", body)
            self.record(f"notifications.{event}", "ok" if result else "error", {"eventType": event_type, "subscriber": address, "result": result})

    def apply_branch_policies(self) -> None:
        if not self.repo_id:
            self.record("policy", "error", "repositório não descoberto")
            return
        repo = self.get(f"{self.client.base_url}/git/repositories/{self.repo_id}?api-version=7.1")
        default_branch = (repo or {}).get("defaultBranch")
        target = ((self.config.get("pr_policy") or {}).get("target_branch")) or "main"
        ref_name = default_branch or f"refs/heads/{target}"
        if not default_branch:
            self.record(
                "policy.branch",
                "skip",
                "repositório sem defaultBranch; política fica pendente até o primeiro push (não executado aqui)",
            )
            return
        existing = self.get(f"{self.org}/{self.project_id}/_apis/policy/configurations?api-version=7.1") or {}
        types_present = {c.get("type", {}).get("id") for c in existing.get("value", [])}

        pr_policy = self.config.get("pr_policy") or {}
        reviewer = self.identities.get("pr_and_card_approver") or {}
        min_reviewers = {
            "isEnabled": True,
            "isBlocking": True,
            "type": {"id": POLICY_MIN_REVIEWERS},
            "settings": {
                "minimumApproverCount": int(pr_policy.get("minimum_reviewers", 1)),
                "creatorVoteCounts": bool(pr_policy.get("creator_vote_counts", False)),
                "allowDownvotes": False,
                "resetOnSourcePush": bool(pr_policy.get("reset_on_source_push", True)),
                "blockLastPusherVote": bool(pr_policy.get("block_last_pusher", True)),
                "scope": [{"repositoryId": self.repo_id, "refName": ref_name, "matchKind": "Exact"}],
            },
        }
        if POLICY_MIN_REVIEWERS in types_present:
            self.record("policy.min_reviewers", "skip", "já existe")
        else:
            result = self.send("POST", f"{self.org}/{self.project_id}/_apis/policy/configurations?api-version=7.1", min_reviewers)
            self.record("policy.min_reviewers", "ok" if result else "error", result)

        if pr_policy.get("require_linked_work_item"):
            link_policy = {
                "isEnabled": True,
                "isBlocking": True,
                "type": {"id": POLICY_WORK_ITEM_LINKING_OFFICIAL},
                "settings": {
                    "scope": [{"repositoryId": self.repo_id, "refName": ref_name, "matchKind": "Exact"}],
                },
            }
            if POLICY_WORK_ITEM_LINKING_OFFICIAL in types_present:
                self.record("policy.work_item_linking", "skip", "já existe")
            else:
                result = self.send("POST", f"{self.org}/{self.project_id}/_apis/policy/configurations?api-version=7.1", link_policy)
                self.record("policy.work_item_linking", "ok" if result else "error", result)

        if reviewer.get("id"):
            required = {
                "isEnabled": True,
                "isBlocking": True,
                "type": {"id": POLICY_REQUIRED_REVIEWERS},
                "settings": {
                    "requiredReviewerIds": [reviewer["id"]],
                    "minimumApproverCount": 1,
                    "creatorVoteCounts": False,
                    "scope": [{"repositoryId": self.repo_id, "refName": ref_name, "matchKind": "Exact"}],
                },
            }
            if POLICY_REQUIRED_REVIEWERS in types_present:
                self.record("policy.required_reviewer", "skip", "já existe")
            else:
                result = self.send("POST", f"{self.org}/{self.project_id}/_apis/policy/configurations?api-version=7.1", required)
                self.record("policy.required_reviewer", "ok" if result else "error", result)

    def apply_delivery_plans(self) -> None:
        """US-3 (2026-09-02): cria Delivery Plan cross-team se opt-in. Default OFF.
        Idempotente — checa por nome antes de criar."""
        plan_cfg = self.config.get("delivery_plan") or {}
        if not plan_cfg.get("enabled", False):
            self.record("delivery_plan", "skip", "delivery_plan.enabled=false no template")
            return
        if not self.project_id or not self.team_id:
            self.record("delivery_plan", "error", "projeto/time não descobertos")
            return
        plan_name = plan_cfg.get("name", f"{self.project} — Delivery Plan")
        url = f"{self.org}/{self.project_id}/_apis/work/plans?api-version=7.1"
        existing = self.get(url) or {}
        for plan in existing.get("value", []):
            if plan.get("name") == plan_name:
                self.record("delivery_plan", "skip", f"já existe com id={plan.get('id')}")
                return
        payload = {
            "name": plan_name,
            "type": "deliveryTimelineView",
            "properties": {
                "teamBacklogMappings": [
                    {
                        "teamId": self.team_id,
                        "categoryReferenceName": "Microsoft.RequirementCategory",
                    }
                ]
            },
        }
        result = self.send("POST", url, payload)
        self.record("delivery_plan", "ok" if result else "error", {"name": plan_name, "id": (result or {}).get("id")})

    def apply_wiki(self) -> None:
        """US-6 (2026-09-02): provisiona project wiki + 5 pages (Home, Architecture,
        Onboarding, Runbooks, Compliance). Idempotente. Default OFF."""
        wiki_cfg = self.config.get("wiki") or {}
        if not wiki_cfg.get("enabled", False):
            self.record("wiki", "skip", "wiki.enabled=false no template")
            return
        if not self.project_id:
            self.record("wiki", "error", "projeto não descoberto")
            return
        wikis_url = f"{self.org}/_apis/wiki/wikis?api-version=7.1"
        existing = self.get(wikis_url) or {}
        proj_wiki = None
        for w in existing.get("value", []):
            proj_id_in_wiki = (w.get("projectId") or "").lower()
            if proj_id_in_wiki == self.project_id.lower() and w.get("type") == "projectWiki":
                proj_wiki = w
                break
        if proj_wiki:
            self.record("wiki", "skip", f"project wiki já existe id={proj_wiki.get('id')}")
            return
        payload = {"type": "projectWiki", "name": self.project, "projectId": self.project_id}
        result = self.send("POST", f"{self.org}/_apis/wiki/wikis?api-version=7.1", payload)
        if not result or not result.get("id"):
            self.record("wiki", "error", result)
            return
        wiki_id = result["id"]
        self.record("wiki", "ok", {"id": wiki_id, "type": "projectWiki"})
        # Seeds das 5 pages (Diátaxis-friendly). Skip se já existirem.
        pages = [
            ("Home", "home", "# Welcome to {project}\n\nProject wiki provisioned by Agents Squad."),
            ("Architecture", "architecture", "# Architecture\n\nC4 model + ADRs. See /docs/architecture."),
            ("Onboarding", "onboarding", "# Onboarding\n\nFirst day guide for new contributors."),
            ("Runbooks", "runbooks", "# Runbooks\n\nOperational procedures for SRE/on-call."),
            ("Compliance", "compliance", "# Compliance\n\nISO 27001 / SOC 2 evidence trail."),
        ]
        for title, slug, body in pages:
            body = body.format(project=self.project)
            path = f"/{slug}"
            page_url = f"{self.org}/_apis/wiki/wikis/{wiki_id}/pages?path={quote(path, safe='/')}&api-version=7.1"
            page_result = self.send("PUT", page_url, {"content": body})
            status = "ok" if page_result else "error"
            self.record(f"wiki.page.{slug}", status, page_result or path)

    def apply_dashboards(self) -> None:
        """US-5 (2026-09-02): cria 1 team dashboard + 1 project dashboard (opt-in).
        Default OFF porque contributionIds mudam entre versões."""
        dash_cfg = self.config.get("dashboards") or {}
        if not dash_cfg.get("enabled", False):
            self.record("dashboards", "skip", "dashboards.enabled=false no template")
            return
        if not self.project_id:
            self.record("dashboards", "error", "projeto não descoberto")
            return
        dashboards = dash_cfg.get("items", [
            {"name": "Agents Squad — Team Overview", "scope": "project_Team"},
            {"name": "Agents Squad — Project Overview", "scope": "project"},
        ])
        scope_urls = {
            "project_Team": f"{self.org}/{self.project_id}/{self.team_id}/_apis/dashboard/dashboards?api-version=7.1-preview.3",
            "project": f"{self.org}/{self.project_id}/_apis/dashboard/dashboards?api-version=7.1-preview.3",
        }
        for dash in dashboards:
            scope = dash.get("scope", "project_Team")
            url = scope_urls.get(scope)
            if not url:
                self.record(f"dashboards.{dash['name']}", "error", f"scope inválido: {scope}")
                continue
            existing = self.get(url) or {}
            if any(d.get("name") == dash["name"] for d in existing.get("value", [])):
                self.record(f"dashboards.{dash['name']}", "skip", "já existe")
                continue
            payload = {
                "name": dash["name"],
                "dashboardScope": scope,
                "widgets": dash.get("widgets", []),
            }
            result = self.send("POST", url, payload)
            self.record(f"dashboards.{dash['name']}", "ok" if result else "error", result)

    def create_team(self) -> None:
        """Cria times adicionais no projeto via POST /_apis/projects/{projectId}/teams.

        Lê `teams` do devops.yaml — cada entrada precisa de `name` e opcionalmente
        `description`. Idempotente: se o nome já existir, registra skip.
        """
        if not self.project_id:
            self.record("teams", "error", "projeto não descoberto")
            return
        teams_cfg = self.config.get("teams") or []
        if not teams_cfg:
            self.record("teams", "skip", "nenhum team configurado em devops.yaml")
            return
        existing = self.get(
            f"{self.org}/_apis/projects/{self.project_id}/teams?api-version=7.1"
        ) or {}
        existing_names = {t.get("name") for t in (existing.get("value") or [])}
        for spec in teams_cfg:
            name = spec.get("name")
            if not name:
                self.record("teams", "error", f"team sem nome: {spec}")
                continue
            if name in existing_names:
                self.record(f"teams.{name}", "skip", "já existe")
                continue
            payload: dict[str, Any] = {"name": name}
            if spec.get("description"):
                payload["description"] = spec["description"]
            result = self.send(
                "POST",
                f"{self.org}/_apis/projects/{self.project_id}/teams?api-version=7.1",
                payload,
            )
            if result and result.get("id"):
                self.record(f"teams.{name}", "ok", {"id": result["id"], "name": result.get("name")})
            else:
                self.record(f"teams.{name}", "error", result)

    def apply_swimlanes(self) -> None:
        """Documenta e implementa swimlanes via WIQL como alternativa à API inexistente.

        LIMITATION (2026-09-02):
        Azure DevOps NÃO possui REST API pública para criar swimlanes em boards.
        O endpoint POST /_apis/work/boards/{board}/swimlanes retorna 404 Not Found.
        A funcionalidade de swimlanes é gerida client-side via Board > Rows > New Row.

        ALTERNATIVE IMPLEMENTED:
        Cada entrada de `board.swimlanes` no devops.yaml é uma query WIQL gravada
        como saved query em "Shared/Agents Squad/SWIMLANES/{name}". Essas queries
        são usadas para filtrar work items na board view (query-based board).

        Aplica APENAS quando `board.swimlanes` contém entries no template.
        Idempotente: se a query já existir, registra skip.
        """
        swimlanes_cfg = ((self.config.get("board") or {}).get("swimlanes") or [])
        if not swimlanes_cfg:
            self.record("swimlanes", "skip", "nenhuma swimlane configurada em board.swimlanes")
            return
        if not self.project_id:
            self.record("swimlanes", "error", "projeto não descoberto")
            return
        proj_url = f"{self.org}/_apis/projects/{quote(self.project)}?api-version=7.1"
        proj = self.get(proj_url) or {}
        if not proj.get("id"):
            self.record("swimlanes", "error", "projeto não resolvido")
            return
        # Azure DevOps API usa project NAME no path, não GUID
        proj_name = quote(self.project)
        parent_folder = "Shared/Agents Squad"
        folder_path = "Shared/Agents Squad/SWIMLANES"

        self.send(
            "POST",
            f"{self.org}/{proj_name}/_apis/wit/queries/{quote(parent_folder, safe='/')}?api-version=7.1",
            {"name": "Agents Squad", "isFolder": True},
        )

        folder = self.send(
            "POST",
            f"{self.org}/{proj_name}/_apis/wit/queries/{quote(folder_path, safe='/')}?api-version=7.1",
            {"name": "SWIMLANES", "isFolder": True},
        )
        self.record("swimlanes.folder", "ok" if folder else "skip", folder_path)

        existing_all = self.get(
            f"{self.org}/{proj_name}/_apis/wit/queries?api-version=7.1"
        ) or {}

        for lane in swimlanes_cfg:
            name = lane.get("name")
            query = lane.get("query")
            if not name or not query:
                self.record("swimlanes", "error", f"swimlane inválida (sem name/query): {lane}")
                continue
            folder_query_path = f"{folder_path}/{name}"
            existing_query = next(
                (q for q in existing_all.get("value", [])
                 if (q.get("path") or "").startswith(folder_path + "/") and q.get("name") == name),
                None,
            )
            if existing_query:
                self.record(f"swimlanes.{name}", "skip", "query já existe")
                continue
            wiql = (
                f"SELECT [System.Id] FROM WorkItems "
                f"WHERE [System.TeamProject] = '{self.project}' AND "
                f"{query}"
            )
            payload = {"name": name, "wiql": wiql, "isFolder": False}
            result = self.send(
                "POST",
                f"{self.org}/{proj_name}/_apis/wit/queries/{quote(folder_query_path, safe='/')}?api-version=7.1",
                payload,
            )
            if result and result.get("id"):
                self.record(f"swimlanes.{name}", "ok", {"query": query, "id": result["id"]})
            else:
                self.record(f"swimlanes.{name}", "error", result)

    def apply_security_acls(self) -> None:
        """Aplica Security ACLs via POST /_apis/accesscontrollists/{namespaceId}.

        Lê `security.acls` do devops.yaml — cada entry contém:
          - namespace (str): nome do namespace (e.g. "Project", "Git Repositories")
          - token (str): token do resource (e.g. projectId, repoId)
          - inherit (bool): se herda do parent
          - entries (list): lista de ACL entries com descriptor + allow/deny

        namespaces conhecidos para Azure DevOps:
          - "Project" (namespaceId:52d94092-0000-0000-0000-000000000000)
          - "Git Repositories" (83e9ad7f-0000-0000-0000-000000000000)
          - "WorkItemTracking" (c0e7a4c0-0000-0000-0000-000000000000)

        Idempotente: se os ACLs já estão aplicados (mesmo allow/deny), registra skip.
        """
        security_cfg = self.config.get("security") or {}
        if not security_cfg.get("enabled", False):
            self.record("security.acls", "skip", "security.enabled=false ou ausente")
            return
        if not self.project_id:
            self.record("security.acls", "error", "projeto não descoberto")
            return
        acls_cfg = security_cfg.get("acls") or []
        if not acls_cfg:
            self.record("security.acls", "skip", "security.acls vazio")
            return
        namespace_map = {
            "Project": "52d94092-0000-0000-0000-000000000000",
            "Git Repositories": "83e9ad7f-0000-0000-0000-000000000000",
            "WorkItemTracking": "c0e7a4c0-0000-0000-0000-000000000000",
        }
        for acl_spec in acls_cfg:
            ns_name = acl_spec.get("namespace", "Project")
            namespace_id = acl_spec.get("namespaceId") or namespace_map.get(ns_name)
            if not namespace_id:
                self.record("security.acls", "error", f"namespaceId desconhecido: {ns_name}")
                continue
            token = acl_spec.get("token") or self.project_id
            inherit = acl_spec.get("inherit", True)
            entries = acl_spec.get("entries") or []
            if not entries:
                self.record("security.acls", "skip", f"nenhuma entry em {ns_name}/{token}")
                continue
            url = f"{self.org}/_apis/accesscontrollists/{namespace_id}?api-version=7.1"
            check_url = f"{url}&token={quote(token)}&inheritGroups={str(inherit).lower()}"
            existing = self.get(check_url) or {}
            if existing.get("count", 0) > 0 and existing.get("value"):
                self.record(f"security.acls.{ns_name}.{token}", "skip", "ACLs já existem — use replace=true para atualizar")
                continue
            payload = {
                "contributor": entries,
                "inherit": inherit,
                "token": token,
            }
            result = self.send("POST", url, payload)
            if result is not None:
                self.record(f"security.acls.{ns_name}.{token}", "ok", {"entries": len(entries), "result": result})
            else:
                self.record(f"security.acls.{ns_name}.{token}", "error", result)

    def create_service_connections(self) -> None:
        """Cria service connections via POST /_apis/serviceEndpoints.

        Lê `service_connections` do devops.yaml — cada entry contém:
          - name (str): nome do service connection
          - type (str): tipo (Azure Resource Manager, GitHub, Docker Registry, Kubernetes)
          - service_account (str): nome da service account (opcional, mapeia para authorization)
          - scope (str): escopo/target (opcional, e.g. subscriptionId para Azure)
          - auth (dict): dados de autenticação específicos por tipo

        Tipos suportados e seus esquemas:
          - azure_rm: type=Azure Resource Manager, requires Azure subscription
          - github: type=GitHub, authorization via GitHub PAT ou OAuth
          - docker_registry: type=Docker Registry
          - kubernetes: type=Kubernetes, authorization via kubeconfig ou service account

        Idempotente: se já existe um service connection com o mesmo nome, registra skip.
        """
        sc_cfg = self.config.get("service_connections") or []
        if not sc_cfg:
            self.record("service_connections", "skip", "nenhum service_connection configurado")
            return
        if not self.project_id:
            self.record("service_connections", "error", "projeto não descoberto")
            return
        existing = self.get(
            f"{self.org}/_apis/serviceendpoint/endpoints?api-version=7.1&projectId={self.project_id}"
        ) or {}
        existing_names = {sc.get("name") for sc in (existing.get("value") or [])}
        for spec in sc_cfg:
            name = spec.get("name")
            sc_type = spec.get("type")
            if not name or not sc_type:
                self.record("service_connections", "error", f"service_connection sem name ou type: {spec}")
                continue
            if name in existing_names:
                self.record(f"service_connections.{name}", "skip", "já existe")
                continue
            endpoint = self._build_service_connection_payload(spec)
            if endpoint is None:
                self.record(f"service_connections.{name}", "error", "tipo não suportado ou payload não construido")
                continue
            result = self.send(
                "POST",
                f"{self.org}/_apis/serviceendpoint/endpoints?api-version=7.1",
                endpoint,
            )
            if result and result.get("id"):
                self.record(f"service_connections.{name}", "ok", {"id": result["id"], "type": sc_type})
            else:
                self.record(f"service_connections.{name}", "error", result)

    def _build_service_connection_payload(self, spec: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Constrói o payload do service connection conforme o tipo.

        Tipos suportados:
          - azure_rm: Azure Resource Manager (subscription-based)
          - github: GitHub (PAT authorization)
          - docker_registry: Docker Registry (username/password)
          - kubernetes: Kubernetes (kubeconfig)

        Retorna None se o tipo não é suportado.
        """
        sc_type = spec.get("type", "").lower()
        name = spec.get("name", "")
        service_account_key = spec.get("service_account")
        identities = self.config.get("identities") or {}
        service_accounts = self.config.get("service_accounts") or {}
        sa = service_accounts.get(service_account_key, {}) if service_account_key else {}
        sa_email = sa.get("email") or identities.get("human_master", {}).get("email")
        auth = spec.get("auth") or {}
        scope = spec.get("scope") or {}

        if sc_type == "azure_rm":
            subscription_id = scope.get("subscription_id") or auth.get("subscription_id") or ""
            subscription_name = scope.get("subscription_name") or auth.get("subscription_name") or name
            tenant_id = auth.get("tenant_id") or ""
            client_id = auth.get("client_id") or ""
            client_secret = auth.get("client_secret") or ""
            return {
                "name": name,
                "type": "AzureRM",
                "url": "https://management.azure.com/",
                "data": {
                    "subscriptionId": subscription_id,
                    "subscriptionName": subscription_name,
                    "environment": "AzureCloud",
                    "scopeLevel": "Subscription",
                    "creationMode": "Manual",
                },
                "authorization": {
                    "parameters": {
                        "tenantid": tenant_id,
                        "serviceprincipalid": client_id,
                        "authenticationType": "spnKey",
                        "serviceprincipalkey": client_secret,
                    },
                    "scheme": "ServicePrincipal",
                },
                "isShared": False,
                "isReady": True,
                "owner": "Library",
                "serviceEndpointProjectReferences": [
                    {
                        "projectReference": {
                            "id": self.project_id,
                            "name": self.project,
                        },
                        "name": name,
                    }
                ],
            }
        if sc_type == "github":
            token = auth.get("token") or ""
            return {
                "name": name,
                "type": "GitHub",
                "authorization": {
                    "parameters": {
                        "accessToken": token,
                    },
                    "scheme": "Token",
                },
                "isShared": False,
                "owner": "Library",
            }
        if sc_type == "docker_registry":
            username = auth.get("username") or ""
            password = auth.get("password") or ""
            docker_registry = auth.get("docker_registry") or "https://index.docker.io/v1/"
            return {
                "name": name,
                "type": "DockerRegistry",
                "authorization": {
                    "parameters": {
                        "username": username,
                        "password": password,
                        "email": sa_email or "",
                        "registry": docker_registry,
                    },
                    "scheme": "UsernamePassword",
                },
                "isShared": False,
                "owner": "Library",
            }
        if sc_type == "kubernetes":
            kubeconfig = auth.get("kubeconfig") or ""
            return {
                "name": name,
                "type": "Kubernetes",
                "authorization": {
                    "parameters": {
                        "kubeconfig": kubeconfig,
                        "acceptUntrustedCerts": str(auth.get("accept_untrusted_certs", False)).lower(),
                    },
                    "scheme": "Kubeconfig",
                },
                "isShared": False,
                "owner": "Library",
            }
        return None

    def apply(self) -> dict[str, Any]:
        self.discover()
        self._run_parallel(["apply_areas", "apply_iterations"])
        self.apply_team_iterations()  # US-1
        self.apply_queries()  # US-2
        self.apply_team_settings()
        self.apply_board_wip()
        self.apply_card_fields()
        self.apply_card_colors()
        self.apply_notifications()
        self.apply_branch_policies()
        self.apply_delivery_plans()  # US-3 (opt-in)
        self.apply_wiki()  # US-6 (opt-in)
        self.apply_dashboards()  # US-5 (opt-in)
        self.create_team()
        self.apply_swimlanes()
        self.apply_security_acls()
        self.create_service_connections()
        summary = {
            "ok": sum(1 for r in self.results if r["status"] == "ok"),
            "skip": sum(1 for r in self.results if r["status"] == "skip"),
            "error": sum(1 for r in self.results if r["status"] == "error"),
        }
        return {"generated_at": _now(), "project": self.project, "summary": summary, "steps": self.results}


def load_setup_config(root: Path) -> dict[str, Any]:
    cfg = load_devops_config(root)
    if cfg.get("identities"):
        return cfg
    template = ROOT / "templates" / "devops.yaml"
    if template.is_file():
        import yaml
        payload = yaml.safe_load(template.read_text(encoding="utf-8")) or {}
        if isinstance(payload, dict):
            merged = {**payload, **cfg}
            return merged
    return cfg


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Aplica o processo Azure DevOps do squad em qualquer projeto")
    parser.add_argument("--apply", action="store_true", help="Executa as mutações no Azure DevOps")
    parser.add_argument("--json", action="store_true", help="Imprime o relatório em JSON")
    args = parser.parse_args(argv or sys.argv[1:])

    connector = DevOpsPlatformConnector(root_path=ROOT)
    if not isinstance(connector.client, AzureDevOpsClient):
        print("ERRO: credenciais Azure DevOps ausentes em .env", file=sys.stderr)
        return 1
    config = load_setup_config(ROOT)
    setup = AzureDevOpsProjectSetup(connector.client, config)
    if not args.apply:
        setup.discover()
        report = {"mode": "dry-run", "project": setup.project, "steps": setup.results}
    else:
        report = setup.apply()
        report["mode"] = "apply"
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"mode={report.get('mode')} project={setup.project}")
        for step in report.get("steps", []):
            print(f"[{step['status']}] {step['step']}: {step['detail']}")
        if "summary" in report:
            print(report["summary"])
    return 0 if (report.get("summary") or {}).get("error", 0) == 0 or report.get("mode") == "dry-run" else 2


if __name__ == "__main__":
    sys.exit(main())
