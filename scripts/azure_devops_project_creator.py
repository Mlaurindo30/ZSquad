#!/usr/bin/env python3
"""
O que é: Criação de projetos Azure DevOps via REST API com polling.
Responsabilidade: Criar um projeto Azure DevOps esperando até o estado 'wellFormed',
lidando com idempotência (projeto já existente) e erros de validação.
Pra que serve: Orquestrador de delivery usa para provisionar projetos DevOps
antes de aplicar o processo (board, áreas, iterações, políticas).
Comportamento em falha: Retorna dict com state='error' ou 'UNVERIFIED' quando
a resposta não pode ser validada contra o contrato.
Conexões: integrations/devops_platform_connector.py (tenacity retry, SSRF,
_validate_org_url, _auth_header, _do_request).
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ProjectCreator")

API_VERSION = "7.1"
_VALID_STATES = {"creating", "waitingForAdmin", "validating", "wellFormed"}
_VALID_PROCESS_TYPES = {"Scrum", "Agile", "CMMI"}


class _RetryableHTTPError(Exception):
    def __init__(self, status_code: int = 0, retry_after: Optional[int] = None):
        self.status_code = status_code
        self.retry_after = retry_after


def _retry_wait(retry_state: tenacity.RetryCallState) -> float:
    exc = retry_state.outcome.exception()
    if isinstance(exc, _RetryableHTTPError) and exc.retry_after is not None:
        return float(exc.retry_after)
    return tenacity.wait_exponential(multiplier=1, min=2, max=30)(retry_state)


def _validate_org_url(org_url: str) -> None:
    parsed = urllib.parse.urlparse(org_url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"dev.azure.com", "visualstudio.com"} and not hostname.endswith(".visualstudio.com"):
        raise ValueError(
            f"URL de organização DevOps inválida (allowlist: dev.azure.com, visualstudio.com, <org>.visualstudio.com): {org_url}"
        )


def _auth_header(pat: str) -> str:
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


@tenacity.retry(
    retry=tenacity.retry_if_exception_type(_RetryableHTTPError),
    stop=tenacity.stop_after_attempt(3),
    wait=_retry_wait,
    reraise=True,
)
def _do_request(
    method: str,
    url: str,
    pat: str,
    body: Optional[dict[str, Any]] = None,
    content_type: str = "application/json",
) -> Optional[dict[str, Any]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", _auth_header(pat))
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
                logger.error(f"[ProjectCreator] Resposta não-JSON em {method} {url}")
                return None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logger.error(f"[ProjectCreator] HTTP {exc.code} em {method} {url}: {detail}")
        if exc.code in (429, 502, 503, 504):
            retry_after = exc.headers.get("Retry-After")
            try:
                retry_after = int(retry_after) if retry_after else None
            except (TypeError, ValueError):
                retry_after = None
            raise _RetryableHTTPError(exc.code, retry_after=retry_after) from exc
        raise _RetryableHTTPError(exc.code) from exc
    except urllib.error.URLError as exc:
        logger.error(f"[ProjectCreator] Falha de rede em {method} {url}: {exc.reason}")
        raise _RetryableHTTPError(0) from exc


def _build_org_url(org_url: str) -> str:
    if not org_url.startswith("http"):
        org_url = f"https://dev.azure.com/{org_url}"
    _validate_org_url(org_url)
    return org_url.rstrip("/")


def _wait_for_project(
    org_url: str,
    project_id: str,
    pat: str,
    timeout_seconds: int = 600,
    poll_interval: float = 5.0,
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    start = time.monotonic()
    poll = poll_interval
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout_seconds:
            logger.error(f"[ProjectCreator] Timeout ({timeout_seconds}s) aguardando projeto {project_id}")
            return None, {"error": "timeout", "elapsed_seconds": elapsed}
        url = f"{org_url}/_apis/projects/{urllib.parse.quote(project_id)}?api-version={API_VERSION}"
        result = _do_request("GET", url, pat)
        if result is None:
            return None, {"error": "request_failed"}
        state = result.get("state", "")
        if state == "wellFormed":
            return state, result
        if state and state not in _VALID_STATES:
            logger.warning(f"[ProjectCreator] Estado inesperado '{state}' para projeto {project_id}")
            return None, {"error": "invalid_state", "state": state, "response": result}
        remaining = timeout_seconds - elapsed
        sleep = min(poll, remaining)
        logger.info(
            f"[ProjectCreator] Projeto {project_id} em estado '{state}' "
            f"({elapsed:.0f}s decorridos, timeout em {remaining:.0f}s). "
            f"Aguardando {sleep:.0f}s..."
        )
        time.sleep(sleep)
        poll = min(poll * 1.5, 30)


def create_project(
    org_url: str,
    project_name: str,
    process_type: str = "Scrum",
    pat: Optional[str] = None,
    description: str = "",
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """
    Cria um projeto Azure DevOps via REST API com polling até 'wellFormed'.

    Args:
        org_url: URL base da organização (ex: 'https://dev.azure.com/cbvgas' ou 'cbvgas').
        project_name: Nome do projeto a criar.
        process_type: Modelo de processo — 'Scrum' (default), 'Agile', ou 'CMMI'.
        pat: Personal Access Token. Se None, tenta AZURE_DEVOPS_PAT do ambiente.
        description: Descrição opcional do projeto.
        timeout_seconds: Tempo máximo de polling (default 600s = 10 min).

    Returns:
        dict com:
          - project_id: str — ID do projeto (UUID)
          - project_url: str — URL do projeto no Azure DevOps
          - state: str — 'wellFormed' (sucesso), 'creating' (timeout em progresso),
                       'alreadyExists', 'error', ou 'UNVERIFIED' (resposta inesperada)
          - detail: dict — resposta completa ou informação de erro
    """
    if pat is None:
        pat = _get_env_pat()
    org = _build_org_url(org_url)

    if not project_name or not project_name.strip():
        return {"project_id": None, "project_url": None, "state": "error", "detail": {"error": "project_name não pode ser vazio"}}

    if process_type not in _VALID_PROCESS_TYPES:
        return {
            "project_id": None,
            "project_url": None,
            "state": "error",
            "detail": {"error": f"process_type inválido: '{process_type}'. Válidos: {sorted(_VALID_PROCESS_TYPES)}"},
        }

    logger.info(f"[ProjectCreator] Verificando se projeto '{project_name}' já existe em {org}...")

    project_id, detail = _find_project_by_name(org, project_name, pat)
    if project_id:
        logger.info(f"[ProjectCreator] Projeto '{project_name}' já existe com id={project_id}")
        project_url = f"{org}/{project_name}"
        return {
            "project_id": project_id,
            "project_url": project_url,
            "state": "alreadyExists",
            "detail": detail,
        }

    logger.info(f"[ProjectCreator] Criando projeto '{project_name}' (process={process_type}) em {org}...")

    create_url = f"{org}/_apis/projects?api-version={API_VERSION}"
    body: dict[str, Any] = {
        "name": project_name.strip(),
        "description": description,
        "capabilities": {
            "versionControl": {"sourceControlType": "Git"},
            "workItemTracking": {"processTemplate": {"type": process_type}},
        },
    }

    result = _do_request("POST", create_url, pat, body)

    if result is None:
        return {
            "project_id": None,
            "project_url": None,
            "state": "error",
            "detail": {"error": "requisição de criação falhou (HTTPError ou URLError)"},
        }

    if isinstance(result, dict):
        error_info = _extract_error(result)
        if error_info:
            return {
                "project_id": None,
                "project_url": None,
                "state": "error",
                "detail": error_info,
            }

    project_id = _safe_get(result, "id")
    if not project_id:
        logger.warning(f"[ProjectCreator] Resposta de criação sem 'id': {str(result)[:300]}")
        return {
            "project_id": None,
            "project_url": None,
            "state": "UNVERIFIED",
            "detail": {"raw": str(result)[:1000]},
        }

    logger.info(f"[ProjectCreator] Projeto criado com id={project_id}. Aguardando estado 'wellFormed'...")
    project_url = f"{org}/{project_name}"

    state, poll_result = _wait_for_project(org, project_id, pat, timeout_seconds=timeout_seconds)

    if state == "wellFormed":
        logger.info(f"[ProjectCreator] Projeto '{project_name}' ({project_id}) reached wellFormed")
        return {
            "project_id": project_id,
            "project_url": project_url,
            "state": "wellFormed",
            "detail": poll_result,
        }

    if state is None and poll_result.get("error") == "timeout":
        return {
            "project_id": project_id,
            "project_url": project_url,
            "state": "creating",
            "detail": poll_result,
        }

    if state is None:
        raw_state = (poll_result or {}).get("state", "unknown")
        if raw_state not in _VALID_STATES:
            return {
                "project_id": project_id,
                "project_url": project_url,
                "state": "UNVERIFIED",
                "detail": poll_result,
            }
        return {
            "project_id": project_id,
            "project_url": project_url,
            "state": raw_state,
            "detail": poll_result,
        }

    return {
        "project_id": project_id,
        "project_url": project_url,
        "state": state,
        "detail": poll_result,
    }


def _find_project_by_name(org: str, name: str, pat: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Busca projeto por nome via GET /_apis/projects?searchText=... Retorna (id, detail) ou (None, None)."""
    url = f"{org}/_apis/projects?api-version={API_VERSION}&searchText={urllib.parse.quote(name)}"
    result = _do_request("GET", url, pat)
    if not result or not isinstance(result, dict):
        return None, None
    for proj in result.get("value", []):
        if proj.get("name", "").lower() == name.lower():
            return proj.get("id"), proj
    return None, None


def _extract_error(result: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Extrai erro estruturado da resposta API ou retorna None se não houver erro."""
    if result.get("status"):
        code = result.get("status", 0)
        if code in {400, 401, 403, 404}:
            msg = result.get("message") or result.get("errorDescription") or str(result)
            return {"error": "validation", "http_status": code, "message": msg}
    if "error" in result:
        inner = result["error"]
        if isinstance(inner, dict):
            msg = inner.get("message") or inner.get("value") or str(inner)
            return {"error": "api_error", "code": inner.get("code"), "message": msg}
        return {"error": "api_error", "raw": str(inner)}
    if not result.get("id"):
        keys = list(result.keys())
        if any(k in ("message", "errors", "validationErrors") for k in keys):
            return {"error": "validation", "raw": {k: result[k] for k in keys if k in ("message", "errors", "validationErrors")}}
    return None


def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    return d.get(key, default) if isinstance(d, dict) else default


def _get_env_pat() -> str:
    import os
    pat = os.environ.get("AZURE_DEVOPS_PAT", "")
    if not pat:
        raise ValueError(
            "AZURE_DEVOPS_PAT não encontrado no ambiente. "
            "Informe o argumento 'pat' ou defina a variável AZURE_DEVOPS_PAT."
        )
    return pat


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cria projeto Azure DevOps via REST API com polling")
    parser.add_argument("--org", required=True, help="URL ou nome da organização Azure DevOps")
    parser.add_argument("--name", required=True, help="Nome do projeto a criar")
    parser.add_argument("--process", default="Scrum", choices=["Scrum", "Agile", "CMMI"], help="Process template (default: Scrum)")
    parser.add_argument("--pat", help="Personal Access Token (ou defina AZURE_DEVOPS_PAT)")
    parser.add_argument("--description", default="", help="Descrição do projeto")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout de polling em segundos (default: 600)")
    parser.add_argument("--json", action="store_true", help="Saída em JSON")
    args = parser.parse_args()

    result = create_project(
        org_url=args.org,
        project_name=args.name,
        process_type=args.process,
        pat=args.pat,
        description=args.description,
        timeout_seconds=args.timeout,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        state = result.get("state", "unknown")
        pid = result.get("project_id")
        purl = result.get("project_url")
        print(f"state={state} project_id={pid} project_url={purl}")
        if state == "error":
            print(f"ERROR detail={result.get('detail')}")
        elif state == "UNVERIFIED":
            print(f"UNVERIFIED — raw_response={result.get('detail')}")
