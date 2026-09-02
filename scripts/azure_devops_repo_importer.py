#!/usr/bin/env python3
"""
Azure DevOps Git Repository Importer.

Importa repositórios Git externos (GitHub, GitLab, Bitbucket, generic) para Azure DevOps
usando a API de import via POST /git/repositories?api-version=7.1.

Referência: integrations/devops_platform_connector.py (tenacity retry, SSRF, _do_request).
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

import tenacity
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

_SCRIPTS_DIR = Path(__file__).resolve().parents[0]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from project_context import find_project_root  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RepoImporter")

_API_VERSION = "7.1"
_DEFAULT_TIMEOUT_SECS = 15 * 60


class _RetryableHTTPError(Exception):
    def __init__(self, status_code: int = 0, retry_after: Optional[int] = None):
        self.status_code = status_code
        self.retry_after = retry_after


def _retry_wait(retry_state: tenacity.RetryCallState) -> float:
    exc = retry_state.outcome.exception()
    if isinstance(exc, _RetryableHTTPError) and exc.retry_after is not None:
        return float(exc.retry_after)
    return wait_exponential(multiplier=1, min=1, max=4)(retry_state)


def _validate_remote_url(remote_url: str) -> None:
    parsed = urllib.parse.urlparse(remote_url)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError(f"URL de remote inválida: {remote_url}")
    blocked = {"localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254"}
    if hostname in blocked or parsed.netloc.startswith("169.254."):
        raise ValueError(f"SSRF bloqueado: remote URL não pode ser {remote_url}")


class RepoImportClient:
    """Cliente REST para importação de repositórios Git no Azure DevOps."""

    def __init__(self, organization: str, project: str, pat_token: str):
        self.organization = organization
        self.project = project
        self.pat_token = pat_token
        # Normalize: aceita nome curto ("cbvgas") ou URL completa (dev.azure.com/cbvgas, org.visualstudio.com)
        if organization.startswith("http"):
            org_url = organization.rstrip("/")
        else:
            org_url = f"https://dev.azure.com/{organization}"
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
    def _do_request(
        self,
        method: str,
        url: str,
        body: Optional[dict[str, Any]] = None,
        content_type: str = "application/json",
    ) -> Optional[dict[str, Any]]:
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
                    logger.error(f"[RepoImporter] Resposta não-JSON em {method} {url}")
                    return None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            logger.error(f"[RepoImporter] HTTP {exc.code} em {method} {url}: {detail}")
            if exc.code in (429, 502, 503, 504):
                retry_after = exc.headers.get("Retry-After")
                try:
                    retry_after = int(retry_after) if retry_after else None
                except (TypeError, ValueError):
                    retry_after = None
                raise _RetryableHTTPError(exc.code, retry_after=retry_after) from exc
        except urllib.error.URLError as exc:
            logger.error(f"[RepoImporter] Falha de rede em {method} {url}: {exc.reason}")
            raise _RetryableHTTPError(0) from exc
        return None

    def _request(
        self,
        method: str,
        url: str,
        body: Optional[dict[str, Any]] = None,
        content_type: str = "application/json",
    ) -> Optional[dict[str, Any]]:
        try:
            return self._do_request(method, url, body, content_type)
        except _RetryableHTTPError:
            return None

    def _build_import_body(
        self,
        repo_name: str,
        remote_url: str,
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        credential_type = credentials.get("type", "none")
        body: dict[str, Any] = {
            "name": repo_name,
            "isImport": True,
            "remoteUrl": remote_url,
        }
        if credential_type == "none":
            body["hasRemoteCredentials"] = False
            body["credentialsSource"] = {"type": "none"}
        elif credential_type == "usernamePassword":
            body["hasRemoteCredentials"] = True
            body["credentialsSource"] = {
                "type": "usernamePassword",
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
            }
        elif credential_type == "personalAccessToken":
            body["hasRemoteCredentials"] = True
            body["credentialsSource"] = {
                "type": "personalAccessToken",
                "token": credentials.get("token", ""),
            }
        elif credential_type == "serviceConnection":
            body["hasRemoteCredentials"] = True
            body["credentialsSource"] = {
                "type": "serviceConnection",
                "id": str(credentials.get("id", "")),
            }
        else:
            logger.warning(f"[RepoImporter] Tipo de credencial desconhecido: {credential_type}; usando none")
            body["hasRemoteCredentials"] = False
            body["credentialsSource"] = {"type": "none"}
        return body

    def _check_existing_repo(self, repo_name: str) -> Optional[dict[str, Any]]:
        url = f"{self.base_url}/git/repositories?api-version={_API_VERSION}"
        result = self._request("GET", url)
        if not result:
            return None
        for repo in result.get("value", []):
            if repo.get("name") == repo_name:
                return repo
        return None

    def _wait_for_import(
        self,
        repo_id: str,
        timeout_secs: int = _DEFAULT_TIMEOUT_SECS,
    ) -> tuple[str, Optional[str]]:
        poll_url = f"{self.base_url}/git/repositories/{urllib.parse.quote(repo_id)}?api-version={_API_VERSION}"
        start = time.monotonic()
        interval = 10
        while time.monotonic() - start < timeout_secs:
            repo = self._request("GET", poll_url)
            if not repo:
                time.sleep(interval)
                continue
            is_import = repo.get("isImport", False)
            if not is_import:
                return "completed", None
            logger.info(f"[RepoImporter] Importação de {repo_id} em progresso...")
            time.sleep(interval)
        return "failed", "timeout"

    def import_repo(
        self,
        repo_name: str,
        remote_url: str,
        credentials: dict[str, Any],
        timeout_secs: int = _DEFAULT_TIMEOUT_SECS,
    ) -> dict[str, Any]:
        _validate_remote_url(remote_url)
        existing = self._check_existing_repo(repo_name)
        if existing:
            logger.info(f"[RepoImporter] Repositório '{repo_name}' já existe (id={existing['id']})")
            return {
                "repo_id": existing["id"],
                "repo_url": existing.get("webUrl", ""),
                "default_branch": existing.get("defaultBranch", "refs/heads/main"),
                "import_state": "already_exists",
            }
        body = self._build_import_body(repo_name, remote_url, credentials)
        create_url = f"{self.base_url}/git/repositories?api-version={_API_VERSION}"
        result = self._request("POST", create_url, body)
        if not result or "id" not in result:
            error_detail = (result or {}).get("message", "") or ""
            if error_detail:
                return {
                    "repo_id": None,
                    "repo_url": None,
                    "default_branch": None,
                    "import_state": f"error: {error_detail}",
                }
            return {
                "repo_id": None,
                "repo_url": None,
                "default_branch": None,
                "import_state": "UNVERIFIED",
            }
        repo_id = result["id"]
        import_state, error_reason = self._wait_for_import(repo_id, timeout_secs)
        if import_state == "completed":
            final_repo = self._request("GET", f"{self.base_url}/git/repositories/{urllib.parse.quote(repo_id)}?api-version={_API_VERSION}")
            if final_repo:
                return {
                    "repo_id": repo_id,
                    "repo_url": final_repo.get("webUrl", ""),
                    "default_branch": final_repo.get("defaultBranch", "refs/heads/main"),
                    "import_state": import_state,
                }
        return {
            "repo_id": repo_id,
            "repo_url": result.get("webUrl", ""),
            "default_branch": result.get("defaultBranch"),
            "import_state": import_state,
        }


def import_repo(
    org_url: str,
    project_id: str,
    repo_name: str,
    remote_url: str,
    credentials: dict[str, Any],
    pat: str,
    timeout_secs: int = _DEFAULT_TIMEOUT_SECS,
) -> dict[str, Any]:
    """Importa um repositório Git externo para Azure DevOps.

    Args:
        org_url: URL da organização Azure DevOps (e.g. https://dev.azure.com/myorg ou myorg).
        project_id: Nome ou ID do projeto Azure DevOps.
        repo_name: Nome do repositório a ser criado no Azure DevOps.
        remote_url: URL do repositório Git externo (GitHub, GitLab, Bitbucket, etc.).
        credentials: Dict com tipo e dados de credencial:
            - none: {}
            - usernamePassword: {"type": "usernamePassword", "username": "...", "password": "..."}
            - personalAccessToken: {"type": "personalAccessToken", "token": "..."}
            - serviceConnection: {"type": "serviceConnection", "id": "..."}
        pat: Personal Access Token do Azure DevOps.
        timeout_secs: Timeout em segundos para polling do import (default 900s = 15 min).

    Returns:
        Dict com:
            - repo_id: ID do repositório (None se falhou)
            - repo_url: URL web do repositório (None se falhou)
            - default_branch: Branch padrão (None se falhou)
            - import_state: "completed", "inProgress", "failed", "already_exists", ou "error: ...".
                          "UNVERIFIED" se a resposta for vazia/inválida.
    """
    parsed = urllib.parse.urlparse(org_url)
    org = parsed.path.strip("/").split("/")[0] if parsed.path else org_url
    if not org:
        org = parsed.hostname or org_url
    client = RepoImportClient(org, project_id, pat)
    return client.import_repo(repo_name, remote_url, credentials, timeout_secs)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Importa repositório Git externo para Azure DevOps")
    parser.add_argument("--org", required=True, help="URL ou nome da organização Azure DevOps")
    parser.add_argument("--project", required=True, help="Nome do projeto Azure DevOps")
    parser.add_argument("--repo", required=True, help="Nome do repositório a criar")
    parser.add_argument("--remote", required=True, help="URL do repositório Git externo")
    parser.add_argument("--pat", required=True, help="Personal Access Token do Azure DevOps")
    parser.add_argument("--cred-type", default="none", choices=["none", "usernamePassword", "personalAccessToken", "serviceConnection"])
    parser.add_argument("--cred-username", help="Username para credencial usernamePassword")
    parser.add_argument("--cred-password", help="Password para credencial usernamePassword")
    parser.add_argument("--cred-token", help="Token para credencial personalAccessToken")
    parser.add_argument("--cred-id", help="ID da service connection")
    args = parser.parse_args()

    creds: dict[str, Any] = {"type": args.cred_type}
    if args.cred_type == "usernamePassword":
        creds["username"] = args.cred_username or ""
        creds["password"] = args.cred_password or ""
    elif args.cred_type == "personalAccessToken":
        creds["token"] = args.cred_token or ""
    elif args.cred_type == "serviceConnection":
        creds["id"] = args.cred_id or ""

    result = import_repo(args.org, args.project, args.repo, args.remote, creds, args.pat)
    print(result)
