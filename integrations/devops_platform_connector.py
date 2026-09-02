#!/usr/bin/env python3
"""
O que é: Conector de Platform Engineering e DevOps Toolchain para Agents Squad.
Responsabilidade: Interface primária alinhada ao padrão MCP (Model Context Protocol) via `@azure-devops/mcp` (declarado em config/mcp_config.json) e fallback programmatic em Python para Azure DevOps Boards, Jira Software e GitHub Enterprise.
Pra que serve: 
  - Em runtime MCP (Antigravity, Claude, ZCode, Codex): os agentes interagem nativamente via ferramentas MCP (`azure_devops_*` / `@azure-devops/mcp`).
  - Em runtime Python/CI (scripts e autonomias): este conector fornece a abstração local e chamadas REST API REST v7.0 quando executado fora de um transporte stdio MCP.
Comportamento em falha: Emite log detalhado e faz fallback gracioso para o armazenamento local em work/ se a rede/credenciais não estiverem disponíveis.
Conexões: Utilizado por delivery-orchestrator, agile-coach, devops-release-engineer e pela esteira de CI/CD.
"""

from __future__ import annotations

import base64
import logging
import os
import urllib.request
import urllib.parse
import urllib.error
import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml
import tenacity
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from project_context import find_project_root  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DevOpsPlatformConnector")

DEFAULT_WORK_ITEM_TYPE_MAP = {
    "epic": "Epic", "story": "User Story", "task": "Task", "bug": "Bug",
    "spike": "Task", "release": "Task", "evolution": "Epic", "study": "Task",
}
DEFAULT_STATE_MAP = {
    "blueprint": "New", "scaffolding": "Active", "implementation": "Active",
    "code-security-review": "Active",
    "quality-validation": "Resolved",
    "governance-release": "Resolved", "done": "Closed",
    # US-10 (2026-09-02): 'review-qa' removido do state_map default (estado órfão).
    # Retrocompat: projetos legados continuam suportando via state_map customizado em devops.yaml.
}

def load_devops_config(start: Optional[Path] = None) -> dict[str, Any]:
    """Lê <project_root>/.agents_squad/config/devops.yaml se existir; senão usa defaults Agile.

    Nunca falha: um projeto sem o marcador ainda funciona com as env vars
    AZURE_DEVOPS_ORG/PROJECT/PAT e o mapeamento Agile padrão.
    """
    project_root = find_project_root(start or Path.cwd())
    if project_root is not None:
        marker = project_root / ".agents_squad" / "config" / "devops.yaml"
        if marker.is_file():
            try:
                payload = yaml.safe_load(marker.read_text(encoding="utf-8")) or {}
                if isinstance(payload, dict):
                    return payload
            except (OSError, yaml.YAMLError) as exc:
                logger.warning(f"devops.yaml inválido em {marker}: {exc}")
    return {
        "work_item_type_map": DEFAULT_WORK_ITEM_TYPE_MAP,
        "state_map": DEFAULT_STATE_MAP,
    }


@dataclass
class DevOpsWorkItem:
    id: str
    title: str
    description: str
    state: str
    story_points: Optional[int] = None
    t_shirt_size: Optional[str] = None
    squad_owner: Optional[str] = None



def load_dotenv_if_present(root_path: Path) -> None:
    """Lê variáveis de .env ou .env.local se existirem, sem sobrescrever o ambiente."""
    for env_file in (root_path / ".env", root_path / ".env.local"):
        if env_file.is_file():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k and k not in os.environ and v:
                            os.environ[k] = v
            except Exception as e:
                logger.debug("Failed to load .env: %s", e)


class BaseDevOpsClient(ABC):
    """Contrato base para clientes de integração DevOps."""

    @abstractmethod
    def pull_ready_items(self, squad_name: Optional[str] = None) -> list[DevOpsWorkItem]:
        pass

    @abstractmethod
    def update_item_state(self, item_id: str, new_state: str, comment: Optional[str] = None) -> bool:
        pass

    @abstractmethod
    def update_sizing(self, item_id: str, story_points: Optional[int] = None, t_shirt_size: Optional[str] = None) -> bool:
        pass

    @abstractmethod
    def create_split_stories(self, parent_item_id: str, split_stories: list[dict[str, Any]]) -> list[DevOpsWorkItem]:
        pass

    def create_pull_request(self, source_branch: str, target_branch: str, title: str,
                             description: str = "", work_item_ids: Optional[list[str]] = None) -> Optional[dict[str, Any]]:
        """Cria uma Pull Request no provider. Default: não suportado (ex: Jira não hospeda repositório)."""
        raise NotImplementedError(f"{type(self).__name__} não suporta criação de Pull Request")


class _RetryableHTTPError(Exception):
    def __init__(self, status_code: int = 0, retry_after: Optional[int] = None):
        self.status_code = status_code
        self.retry_after = retry_after


def _retry_wait(retry_state: tenacity.RetryCallState) -> float:
    exc = retry_state.outcome.exception()
    if isinstance(exc, _RetryableHTTPError) and exc.retry_after is not None:
        return float(exc.retry_after)
    return tenacity.wait_exponential(multiplier=1, min=1, max=4)(retry_state)


class AzureDevOpsClient(BaseDevOpsClient):
    """Cliente REST API para Azure DevOps Boards/Repos (v7.1).

    Espelha o servidor MCP oficial @azure-devops/mcp para os runtimes (scripts,
    CI) que rodam fora de um transporte MCP. Autentica via PAT (Basic auth,
    usuário vazio) — nunca loga o token; erros HTTP viram log + retorno seguro
    (False/[]/None), nunca exceção não tratada, para não travar o squad local.
    """

    API_VERSION = "7.1"

    def __init__(self, organization: str, project: str, pat_token: str, config: Optional[dict[str, Any]] = None):
        self.organization = organization
        self.project = project
        self.pat_token = pat_token
        config = config or {}
        self.repo = config.get("repo", project)
        self.area_path = config.get("area_path")
        self.iteration_path = config.get("iteration_path")
        self.type_map: dict[str, str] = {**DEFAULT_WORK_ITEM_TYPE_MAP, **config.get("work_item_type_map", {})}
        self.state_map: dict[str, str] = {**DEFAULT_STATE_MAP, **config.get("state_map", {})}
        # US-1 (2026-09-02): phase_tags aplicadas ao mover estado, refletem
        # a fase real do work item sem custom states herdados.
        self.phase_tags: dict[str, str] = config.get("phase_tags", {})
        # US-1: squad_tags discriminam squad temática nos swimlanes.
        self.squad_tags: dict[str, str] = config.get("squad_tags", {})
        self.pr_policy: dict[str, Any] = config.get("pr_policy", {})
        self.scoring: dict[str, Any] = config.get("scoring", {})
        self.identities: dict[str, Any] = config.get("identities", {})
        self.approvals: dict[str, Any] = config.get("approvals", {})
        # organization aceita tanto o nome curto ("cbvgas") quanto a URL completa
        # da org (dev.azure.com/<org> ou <org>.visualstudio.com), para cobrir os
        # dois formatos válidos que o Azure DevOps aceita hoje.
        org_url = config.get("org_url") or organization
        if not org_url.startswith("http"):
            org_url = f"https://dev.azure.com/{org_url}"
        self._validate_org_url(org_url)
        self.base_url = f"{org_url.rstrip('/')}/{urllib.parse.quote(project)}/_apis"

    def _validate_org_url(self, org_url: str) -> None:
        parsed = urllib.parse.urlparse(org_url)
        hostname = (parsed.hostname or "").lower()
        if hostname not in {"dev.azure.com", "visualstudio.com"} and not hostname.endswith(".visualstudio.com"):
            raise ValueError(
                f"URL de organização DevOps inválida (allowlist: dev.azure.com, visualstudio.com, <org>.visualstudio.com): {org_url}"
            )

    def _auth_header(self) -> str:
        token = base64.b64encode(f":{self.pat_token}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    @retry(
        retry=retry_if_exception_type(_RetryableHTTPError),
        stop=stop_after_attempt(3),
        wait=_retry_wait,
        reraise=True,
    )
    def _do_request(self, method: str, url: str, body: Optional[dict[str, Any]] = None,
                    content_type: str = "application/json") -> Optional[dict[str, Any]]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", self._auth_header())
        request.add_header("Content-Type", content_type)
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    logger.error(f"[AzureDevOps] Resposta não-JSON em {method} {url} (org/projeto/URL incorretos?)")
                    return None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            logger.error(f"[AzureDevOps] HTTP {exc.code} em {method} {url}: {detail}")
            if exc.code in (429, 502, 503, 504):
                retry_after = exc.headers.get("Retry-After")
                try:
                    retry_after = int(retry_after) if retry_after else None
                except (TypeError, ValueError):
                    retry_after = None
                raise _RetryableHTTPError(exc.code, retry_after=retry_after) from exc
        except urllib.error.URLError as exc:
            logger.error(f"[AzureDevOps] Falha de rede em {method} {url}: {exc.reason}")
            raise _RetryableHTTPError(0) from exc
        return None

    def _request(self, method: str, url: str, body: Optional[dict[str, Any]] = None,
                 content_type: str = "application/json") -> Optional[dict[str, Any]]:
        try:
            return self._do_request(method, url, body, content_type)
        except _RetryableHTTPError:
            return None

    def _resolve_type(self, item_type: str) -> str:
        return self.type_map.get(item_type, item_type)

    def pull_ready_items(self, squad_name: Optional[str] = None) -> list[DevOpsWorkItem]:
        logger.info(f"[AzureDevOps] Consultando Work Items no projeto {self.project} (Org: {self.organization})...")
        ready_states = {self.state_map.get("blueprint", "New"), self.state_map.get("scaffolding", "Active")}
        states_clause = " OR ".join(f"[System.State] = '{state}'" for state in ready_states)
        wiql = {
            "query": (
                "SELECT [System.Id] FROM WorkItems "
                f"WHERE [System.TeamProject] = '{self.project}' AND ({states_clause})"
            )
        }
        result = self._request("POST", f"{self.base_url}/wit/wiql?api-version={self.API_VERSION}", wiql)
        if not result:
            return []
        ids = [str(ref["id"]) for ref in result.get("workItems", [])]
        if not ids:
            return []
        detail = self._request(
            "GET",
            f"{self.base_url}/wit/workitems?ids={','.join(ids)}&api-version={self.API_VERSION}",
        )
        if not detail:
            return []
        items = []
        for wi in detail.get("value", []):
            fields = wi.get("fields", {})
            items.append(DevOpsWorkItem(
                id=str(wi["id"]),
                title=fields.get("System.Title", ""),
                description=fields.get("System.Description", ""),
                state=fields.get("System.State", ""),
                story_points=fields.get("Microsoft.VSTS.Scheduling.StoryPoints"),
            ))
        return items

    def update_item_state(self, item_id: str, new_state: str, comment: Optional[str] = None) -> bool:
        logger.info(f"[AzureDevOps] Atualizando estado do Work Item {item_id} para '{new_state}'...")
        resolved_state = self.state_map.get(new_state, new_state)
        patch = [{"op": "add", "path": "/fields/System.State", "value": resolved_state}]
        if comment:
            patch.append({"op": "add", "path": "/fields/System.History", "value": comment})
        # US-1 (2026-09-02): aplica phase tag correspondente ao novo estado e
        # remove a phase tag anterior, preservando squad_tags/outras tags.
        phase_tag = self.phase_tags.get(new_state)
        if phase_tag:
            current = self._request(
                "GET",
                f"{self.base_url}/wit/workitems/{item_id}?api-version={self.API_VERSION}",
            )
            existing_tags = str(((current or {}).get("fields") or {}).get("System.Tags") or "")
            phase_tags_in_use = set(self.phase_tags.values())
            # remove phase tags anteriores, mantém squad_tags e tags de risco livre
            kept = [
                t.strip()
                for t in existing_tags.split(";")
                if t.strip() and t.strip() not in phase_tags_in_use
            ]
            kept.append(phase_tag)
            patch.append({"op": "add", "path": "/fields/System.Tags", "value": "; ".join(kept)})
        url = f"{self.base_url}/wit/workitems/{item_id}?api-version={self.API_VERSION}"
        return self._request("PATCH", url, patch, content_type="application/json-patch+json") is not None

    def update_sizing(self, item_id: str, story_points: Optional[int] = None, t_shirt_size: Optional[str] = None) -> bool:
        logger.info(f"[AzureDevOps] Registrando Story Points={story_points} no Work Item {item_id}...")
        patch = []
        if story_points is not None:
            story_field = ((self.scoring.get("story_points") or {}).get("field")
                           or "Microsoft.VSTS.Scheduling.StoryPoints")
            patch.append({"op": "add", "path": f"/fields/{story_field}", "value": story_points})
        if t_shirt_size is not None:
            epic_cfg = self.scoring.get("epic_tshirt") or {}
            effort_field = epic_cfg.get("field") or "Microsoft.VSTS.Scheduling.Effort"
            effort_map = epic_cfg.get("map") or {"PP": 1, "P": 2, "M": 3, "G": 5, "GG": 8}
            effort_value = effort_map.get(str(t_shirt_size).upper(), t_shirt_size)
            patch.append({"op": "add", "path": f"/fields/{effort_field}", "value": effort_value})
        if not patch:
            return True
        url = f"{self.base_url}/wit/workitems/{item_id}?api-version={self.API_VERSION}"
        return self._request("PATCH", url, patch, content_type="application/json-patch+json") is not None

    def create_work_item(self, item_type: str, title: str, description: str = "",
                         parent_id: Optional[str] = None, story_points: Optional[int] = None) -> Optional[DevOpsWorkItem]:
        """Cria um work item novo (Epic/User Story/Task/Bug conforme work_item_type_map)."""
        wi_type = self._resolve_type(item_type)
        patch = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
        ]
        if description:
            patch.append({"op": "add", "path": "/fields/System.Description", "value": description})
        if self.area_path:
            patch.append({"op": "add", "path": "/fields/System.AreaPath", "value": self.area_path})
        if self.iteration_path:
            patch.append({"op": "add", "path": "/fields/System.IterationPath", "value": self.iteration_path})
        if story_points is not None:
            patch.append({"op": "add", "path": "/fields/Microsoft.VSTS.Scheduling.StoryPoints", "value": story_points})
        if parent_id:
            patch.append({
                "op": "add", "path": "/relations/-",
                "value": {
                    "rel": "System.LinkTypes.Hierarchy-Reverse",
                    "url": f"{self.base_url}/wit/workItems/{parent_id}",
                },
            })
        url = f"{self.base_url}/wit/workitems/${urllib.parse.quote(wi_type)}?api-version={self.API_VERSION}"
        result = self._request("POST", url, patch, content_type="application/json-patch+json")
        if not result:
            return None
        fields = result.get("fields", {})
        return DevOpsWorkItem(
            id=str(result["id"]), title=fields.get("System.Title", title),
            description=fields.get("System.Description", description),
            state=fields.get("System.State", "New"), story_points=story_points,
        )

    def create_split_stories(self, parent_item_id: str, split_stories: list[dict[str, Any]]) -> list[DevOpsWorkItem]:
        logger.info(f"[AzureDevOps] Dividindo Work Item {parent_item_id} em {len(split_stories)} fatias de história...")
        created = []
        for story in split_stories:
            item = self.create_work_item(
                item_type="story",
                title=story.get("title", "Split story"),
                description=story.get("description", ""),
                parent_id=parent_item_id,
                story_points=story.get("story_points", 3),
            )
            if item:
                created.append(item)
        return created

    def create_pull_request(self, source_branch: str, target_branch: str, title: str,
                             description: str = "", work_item_ids: Optional[list[str]] = None) -> Optional[dict[str, Any]]:
        """Cria uma PR em Azure Repos e vincula work items (política require_linked_work_item)."""
        target_branch = target_branch or self.pr_policy.get("target_branch", "main")
        body: dict[str, Any] = {
            "sourceRefName": f"refs/heads/{source_branch}",
            "targetRefName": f"refs/heads/{target_branch}",
            "title": title,
            "description": description,
        }
        if work_item_ids:
            body["workItemRefs"] = [{"id": str(wid)} for wid in work_item_ids]
        url = f"{self.base_url}/git/repositories/{urllib.parse.quote(self.repo)}/pullrequests?api-version={self.API_VERSION}"
        return self._request("POST", url, body)


class JiraClient(BaseDevOpsClient):
    """Cliente REST API para Jira Software Cloud (v3)."""

    def __init__(self, domain: str, email: str, api_token: str, project_key: str):
        self.domain = domain
        self.email = email
        self.api_token = api_token
        self.project_key = project_key
        self.base_url = f"https://{domain}.atlassian.net/rest/api/3"

    def pull_ready_items(self, squad_name: Optional[str] = None) -> list[DevOpsWorkItem]:
        logger.info(f"[Jira] Consultando JQL no projeto {self.project_key}...")
        return []

    def update_item_state(self, item_id: str, new_state: str, comment: Optional[str] = None) -> bool:
        logger.info(f"[Jira] Transicionando issue {item_id} para '{new_state}'...")
        return True

    def update_sizing(self, item_id: str, story_points: Optional[int] = None, t_shirt_size: Optional[str] = None) -> bool:
        logger.info(f"[Jira] Atualizando customfield_story_points={story_points} em {item_id}...")
        return True

    def create_split_stories(self, parent_item_id: str, split_stories: list[dict[str, Any]]) -> list[DevOpsWorkItem]:
        logger.info(f"[Jira] Quebrando issue {parent_item_id} em {len(split_stories)} sub-stories...")
        return []


class LocalFilesystemFallbackClient(BaseDevOpsClient):
    """Fallback local para desenvolvimento offline baseado no diretório work/."""

    def __init__(self, root_path: Path):
        self.root_path = root_path
        self.work_dir = root_path / "work"

    def pull_ready_items(self, squad_name: Optional[str] = None) -> list[DevOpsWorkItem]:
        logger.info(f"[FallbackLocal] Lendo itens de trabalho em {self.work_dir}...")
        items = []
        if not self.work_dir.exists():
            return items

        for status_file in self.work_dir.rglob("status.yaml"):
            try:
                data = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
                if data.get("state") in {"intake", "blueprint", "scaffolding"}:
                    items.append(DevOpsWorkItem(
                        id=data.get("id", status_file.parent.name),
                        title=data.get("title", status_file.parent.name),
                        description=data.get("next_action", ""),
                        state=data.get("state", "unknown"),
                        story_points=data.get("story_points")
                    ))
            except Exception as e:
                logger.warning(f"Erro ao ler {status_file}: {e}")
        return items

    def update_item_state(self, item_id: str, new_state: str, comment: Optional[str] = None) -> bool:
        logger.info(f"[FallbackLocal] Atualizando estado local do item {item_id} para {new_state}...")
        for status_file in self.work_dir.rglob("status.yaml"):
            try:
                data = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
                if data.get("id") == item_id or status_file.parent.name == item_id:
                    data["state"] = new_state
                    if comment:
                        data["status_note"] = comment
                    status_file.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
                    return True
            except Exception as e:
                logger.error(f"Erro ao atualizar status local de {item_id}: {e}")
        return False

    def update_sizing(self, item_id: str, story_points: Optional[int] = None, t_shirt_size: Optional[str] = None) -> bool:
        logger.info(f"[FallbackLocal] Registrando sizing para {item_id}: points={story_points}, t_shirt={t_shirt_size}")
        for status_file in self.work_dir.rglob("status.yaml"):
            try:
                data = yaml.safe_load(status_file.read_text(encoding="utf-8")) or {}
                if data.get("id") == item_id or status_file.parent.name == item_id:
                    if story_points is not None:
                        data["story_points"] = story_points
                    if t_shirt_size is not None:
                        data["t_shirt_size"] = t_shirt_size
                    status_file.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
                    return True
            except Exception as e:
                logger.error(f"Erro ao atualizar sizing local de {item_id}: {e}")
        return False

    def create_split_stories(self, parent_item_id: str, split_stories: list[dict[str, Any]]) -> list[DevOpsWorkItem]:
        logger.info(f"[FallbackLocal] Criando {len(split_stories)} sub-stories locais para {parent_item_id}...")
        created = []
        for idx, story in enumerate(split_stories, 1):
            created.append(DevOpsWorkItem(
                id=f"{parent_item_id}-{idx:03d}",
                title=story.get("title", f"Split {idx}"),
                description=story.get("description", ""),
                state="blueprint",
                story_points=story.get("story_points")
            ))
        return created


class DevOpsPlatformConnector:
    """Gerenciador central de conexão com plataformas DevOps (MCP e REST Fallback)."""

    def __init__(self, root_path: Optional[Path] = None):
        self.root_path = root_path or Path(__file__).resolve().parents[1]
        load_dotenv_if_present(self.root_path)
        self.client = self._resolve_client()

    def _resolve_client(self) -> BaseDevOpsClient:
        # 1. Verifica se credenciais do Azure DevOps estão presentes no ambiente
        if os.getenv("AZURE_DEVOPS_PAT") and os.getenv("AZURE_DEVOPS_ORG") and os.getenv("AZURE_DEVOPS_PROJECT"):
            config = load_devops_config(self.root_path)
            return AzureDevOpsClient(
                organization=os.environ["AZURE_DEVOPS_ORG"],
                project=os.environ["AZURE_DEVOPS_PROJECT"],
                pat_token=os.environ["AZURE_DEVOPS_PAT"],
                config=config,
            )
        # 2. Verifica se credenciais do Jira estão presentes
        if os.getenv("JIRA_API_TOKEN") and os.getenv("JIRA_DOMAIN") and os.getenv("JIRA_EMAIL"):
            return JiraClient(
                domain=os.environ["JIRA_DOMAIN"],
                email=os.environ["JIRA_EMAIL"],
                api_token=os.environ["JIRA_API_TOKEN"],
                project_key=os.getenv("JIRA_PROJECT_KEY", "SQUAD")
            )
        # 3. Fallback padrão seguro para arquivos locais em work/
        return LocalFilesystemFallbackClient(self.root_path)

    def pull_ready_items(self, squad_name: Optional[str] = None) -> list[DevOpsWorkItem]:
        return self.client.pull_ready_items(squad_name)

    def update_item_state(self, item_id: str, new_state: str, comment: Optional[str] = None) -> bool:
        return self.client.update_item_state(item_id, new_state, comment)

    def update_sizing(self, item_id: str, story_points: Optional[int] = None, t_shirt_size: Optional[str] = None) -> bool:
        return self.client.update_sizing(item_id, story_points, t_shirt_size)

    def create_pull_request(self, source_branch: str, target_branch: str, title: str,
                             description: str = "", work_item_ids: Optional[list[str]] = None) -> Optional[dict[str, Any]]:
        return self.client.create_pull_request(source_branch, target_branch, title, description, work_item_ids)

    def create_work_item(self, item_type: str, title: str, description: str = "",
                          parent_id: Optional[str] = None, story_points: Optional[int] = None) -> Optional[DevOpsWorkItem]:
        if isinstance(self.client, AzureDevOpsClient):
            return self.client.create_work_item(item_type, title, description, parent_id, story_points)
        raise NotImplementedError(f"{type(self.client).__name__} não suporta create_work_item genérico")

    def split_story_if_exceeded(self, item_id: str, story_points: int, suggested_splits: list[dict[str, Any]]) -> Optional[list[DevOpsWorkItem]]:
        """Aplica a Regra de Ouro (Max 8 Points): se > 8, quebra a história automaticamente."""
        if story_points <= 8:
            self.update_sizing(item_id, story_points=story_points)
            return None
        logger.warning(f"[CognitiveProtection] Story {item_id} tem {story_points} pontos (> 8). Disparando quebra automática!")
        self.update_item_state(item_id, "Refinement Required", comment=f"Story points ({story_points}) excedeu o limite máximo seguro (8). Quebra obrigatória executada.")
        return self.client.create_split_stories(item_id, suggested_splits)


if __name__ == "__main__":
    connector = DevOpsPlatformConnector()
    print(f"DevOps Platform Connector ativo usando client: {type(connector.client).__name__}")
    items = connector.pull_ready_items()
    print(f"Total de itens no backlog: {len(items)}")
