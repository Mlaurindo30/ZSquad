"""
O que é: serviço de descoberta, auditoria e instalação de skills externas.
Responsabilidade: consultar provedores confiáveis, validar conteúdo e confiná-lo ao destino local.
Pra que serve: disponibilizar skills.sh e skillsmp.com sem abrir fronteiras SSRF ou path traversal.
Comportamento em falha: rejeita URL, hash, caminho ou resposta inválida sem instalar conteúdo parcial.
Conexões: APIs skills.sh/skillsmp.com, diretório local de skills e scripts/skill_curator.py.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urljoin, urlparse

import requests


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Skill:
    """Representa uma skill retornada por um provedor de descoberta."""

    id: str
    slug: str
    name: str
    source: str
    installs: int
    source_type: str
    install_url: Optional[str]
    url: str
    is_duplicate: bool = False
    search_type: Optional[str] = None
    rank: Optional[int] = None
    provider: Optional[str] = None  # skillsmp: category/occupation/language

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Skill):
            return NotImplemented
        return self.id == other.id


@dataclass(frozen=True)
class SkillContent:
    """Agrupa arquivos remotos e o hash de integridade de uma skill."""

    skill_id: str
    files: list[dict[str, str]] = field(default_factory=list)
    hash: Optional[str] = None
    installs: Optional[int] = None


@dataclass(frozen=True)
class AuditEntry:
    """Registra o parecer de segurança emitido por um provedor."""

    provider: str
    slug: str
    status: str
    summary: str
    audited_at: str
    risk_level: str


@dataclass(frozen=True)
class AuditResult:
    skill_id: str
    audits: list[AuditEntry]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SkillDiscoveryService:
    """Serviço unificado de descoberta de skills externas.

    Consulta skills.sh e skillsmp.com, deduplica e ranqueia resultados.
    Nenhuma dependência externa é instalada automaticamente: o caller decide
    se instala, baseado em ``install_skill`` e ``audit_skill``.
    """

    SKILLS_SH_BASE = "https://skills.sh"
    SKILLSMP_BASE = "https://skillsmp.com"
    REQUEST_TIMEOUT = 10
    RANK_WEIGHTS = {
        "installs": 0.3,
        "audit_pass": 0.4,
        "recency": 0.2,
        "duplicate_penalty": 0.1,
    }
    TRUSTED_HOSTS = frozenset({"skills.sh", "skillsmp.com"})
    MAX_REDIRECTS = 3

    def __init__(
        self,
        skills_sh_token: Optional[str] = None,
        skillsmp_key: Optional[str] = None,
        skills_root: str = "skills",
    ) -> None:
        """Configura credenciais opcionais e a raiz do índice local."""
        self._skills_sh_token = skills_sh_token or os.getenv("SKILLS_SH_OIDC_TOKEN")
        self._skillsmp_key = skillsmp_key or os.getenv("SKILLSMP_API_KEY")
        self._skills_root = Path(skills_root)
        self._local_index: Optional[set[str]] = None

    # ------------------------------------------------------------------
    # Transporte de rede
    # ------------------------------------------------------------------

    @classmethod
    def _validate_remote_url(cls, url: str) -> None:
        """Aceita apenas HTTPS, hosts confiáveis e endereços IP públicos."""
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("URL remota inválida")
        if hostname not in cls.TRUSTED_HOSTS:
            raise ValueError("host remoto não confiável")
        addresses = {entry[4][0] for entry in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
        if not addresses:
            raise ValueError("host remoto sem endereço resolvido")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise ValueError("host remoto resolve para endereço não público")

    @classmethod
    def _request(cls, url: str, **kwargs: Any) -> requests.Response:
        """Executa GET validado e revalida cada redirecionamento manualmente."""
        current = url
        for _ in range(cls.MAX_REDIRECTS + 1):
            cls._validate_remote_url(current)
            response = requests.get(
                current,
                timeout=cls.REQUEST_TIMEOUT,
                allow_redirects=False,
                **kwargs,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("Location")
            if not location:
                raise requests.exceptions.TooManyRedirects("redirecionamento sem destino")
            current = urljoin(current, location)
        raise requests.exceptions.TooManyRedirects("limite de redirecionamentos excedido")

    # ------------------------------------------------------------------
    # Index local
    # ------------------------------------------------------------------

    def _build_local_index(self) -> set[str]:
        if self._local_index is not None:
            return self._local_index
        index: set[str] = set()
        if not self._skills_root.exists():
            self._local_index = index
            return index
        for skill_dir in self._skills_root.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                index.add(skill_dir.name.lower())
        self._local_index = index
        return index

    def refresh_local_index(self) -> None:
        """Reconstrói e retorna o índice de skills instaladas localmente."""
        self._local_index = None
        self._build_local_index()

    # ------------------------------------------------------------------
    # Busca
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        context: str = "",
        limit: int = 20,
        min_installs: int = 0,
    ) -> list[Skill]:
        """Busca skills externas relevantes para a competência/contexto.

        Executa busca paralela em skills.sh e skillsmp.com, deduplica por
        ``id`` e ranqueia por peso configurado.

        Args:
            query: termo de busca (ex: "react native").
            context: contexto adicional para filtrar (ex: "mobile app").
            limit: número máximo de resultados após ranqueamento.
            min_installs: filtro mínimo de instalações.

        Returns:
            Lista de ``Skill`` ranqueada, sem duplicatas.
        """
        results: list[Skill] = []
        seen: set[str] = set()

        candidates: list[Skill] = []

        # skills.sh (semantic para multi-word, fuzzy para single-word)
        try:
            sh_results = self._search_skills_sh(query, limit=limit)
            candidates.extend(sh_results)
        except Exception:
            pass

        # skillsmp.com
        try:
            mp_results = self._search_skillsmp(query, limit=limit)
            candidates.extend(mp_results)
        except Exception:
            pass

        for skill in candidates:
            if skill.id in seen:
                continue
            seen.add(skill.id)
            if min_installs > 0 and skill.installs < min_installs:
                continue
            results.append(skill)

        ranked = self._rank(results, context)
        return ranked[:limit]

    def _search_skills_sh(self, query: str, limit: int = 20) -> list[Skill]:
        if not self._skills_sh_token:
            return []

        params = urlencode({"q": query, "limit": limit})
        url = f"{self.SKILLS_SH_BASE}/api/v1/skills/search?{params}"
        headers = {"Authorization": f"Bearer {self._skills_sh_token}"}

        try:
            resp = self._request(url, headers=headers)
            if resp.status_code == 401:
                return []
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return []

        skills: list[Skill] = []
        for item in payload.get("data", []):
            skills.append(
                Skill(
                    id=item.get("id", ""),
                    slug=item.get("slug", ""),
                    name=item.get("name", ""),
                    source=item.get("source", ""),
                    installs=item.get("installs", 0),
                    source_type=item.get("sourceType", "github"),
                    install_url=item.get("installUrl"),
                    url=item.get("url", ""),
                    is_duplicate=item.get("isDuplicate", False),
                    search_type=payload.get("searchType"),
                )
            )
        return skills

    def _search_skillsmp(self, query: str, limit: int = 20) -> list[Skill]:
        params = {"q": query, "limit": limit, "sortBy": "stars"}
        if self._skillsmp_key:
            params["api_key"] = self._skillsmp_key

        url = f"{self.SKILLSMP_BASE}/api/v1/skills/search?{urlencode(params)}"

        resp = self._request(url)
        resp.raise_for_status()
        payload = resp.json()

        if not payload.get("success", True):
            return []

        skills: list[Skill] = []
        data = payload.get("data", [])
        if isinstance(data, dict):
            data = data.get("skills", [])
        if not isinstance(data, list):
            return skills
        for item in data:
            if not isinstance(item, dict):
                continue
            source = item.get("source", item.get("owner", ""))
            skills.append(
                Skill(
                    id=item.get("id", ""),
                    slug=item.get("slug", ""),
                    name=item.get("name", ""),
                    source=source,
                    installs=item.get("installs", 0),
                    source_type=item.get("sourceType", "github"),
                    install_url=item.get("installUrl"),
                    url=item.get("url", ""),
                    is_duplicate=item.get("isDuplicate", False),
                    provider="skillsmp",
                )
            )
        return skills

    # ------------------------------------------------------------------
    # Ranqueamento
    # ------------------------------------------------------------------

    def _rank(self, skills: list[Skill], context: str) -> list[Skill]:
        """Ranqueia skills por relevância, instalações, audit e frescor."""
        max_installs = max((s.installs for s in skills), default=1) or 1
        now = time.time()

        ranked: list[tuple[float, Skill]] = []
        for skill in skills:
            score = 0.0
            score += (skill.installs / max_installs) * self.RANK_WEIGHTS["installs"]

            if self.is_installed(skill.id):
                score += 0.1

            ranked.append((score, skill))

        ranked.sort(key=lambda x: (-x[0], x[1].id))
        return [s for _, s in ranked]

    # ------------------------------------------------------------------
    # Fetch / install / audit
    # ------------------------------------------------------------------

    def fetch_skill(self, skill_id: str) -> Optional[SkillContent]:
        """Busca conteúdo completo de uma skill pelo id ``source/slug``."""
        source, slug = self._split_id(skill_id)
        if not source or not slug:
            return None

        url = f"{self.SKILLS_SH_BASE}/api/v1/skills/{source}/{slug}"
        headers: dict[str, str] = {}
        if self._skills_sh_token:
            headers["Authorization"] = f"Bearer {self._skills_sh_token}"

        resp = self._request(url, headers=headers)
        if resp.status_code == 404:
            return None
        if resp.status_code == 401:
            return None
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError:
            return None
        payload = resp.json()

        return SkillContent(
            skill_id=payload.get("id", skill_id),
            files=payload.get("files", []),
            hash=payload.get("hash"),
            installs=payload.get("installs"),
        )

    @staticmethod
    def _content_digest(files: list[dict[str, str]]) -> str:
        """Calcula hash determinístico do conjunto de caminhos e conteúdos recebido."""
        digest = hashlib.sha256()
        for entry in sorted(files, key=lambda item: item.get("path", "")):
            digest.update(entry["path"].encode("utf-8"))
            digest.update(b"\0")
            digest.update(entry["contents"].encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    def install_skill(self, skill_id: str, target_dir: str = "skills") -> bool:
        """Instala skill externa no diretório local de skills.

        Apenas instala; não habilita automaticamente. Retorna ``False`` se
        skill já existe, id inválido, ou falha no download.

        Args:
            skill_id: ``source/slug`` (ex: ``vercel-labs/skills/find-skills``).
            target_dir: diretório base onde as skills são instaladas.

        Returns:
            ``True`` se instalada com sucesso.
        """
        source, slug = self._split_id(skill_id)
        if not source or not slug:
            return False

        if self.is_installed(skill_id):
            return False

        content = self.fetch_skill(skill_id)
        if content is None or not content.files or not content.hash:
            return False
        expected_hash = content.hash.removeprefix("sha256:").lower()
        if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
            return False
        try:
            if self._content_digest(content.files) != expected_hash:
                return False
        except (KeyError, TypeError, AttributeError):
            return False

        target = (Path(target_dir) / slug).resolve()
        if target.exists():
            return False

        files_to_write: list[tuple[Path, str]] = []
        for file_entry in content.files:
            relative_path = Path(file_entry["path"])
            if relative_path.is_absolute() or ".." in relative_path.parts:
                return False
            path = (target / relative_path).resolve()
            if target != path and target not in path.parents:
                return False
            files_to_write.append((path, file_entry["contents"]))

        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{slug}-", dir=target.parent)).resolve()
        try:
            for path, contents in files_to_write:
                staged_path = staging / path.relative_to(target)
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                if staged_path.parent.is_symlink():
                    return False
                staged_path.write_text(contents, encoding="utf-8")
            staging.replace(target)
        except OSError:
            return False
        finally:
            if staging.exists():
                import shutil
                shutil.rmtree(staging, ignore_errors=True)

        self.refresh_local_index()
        return True

    def audit_skill(self, skill_id: str) -> Optional[AuditResult]:
        """Consulta security audit de uma skill (Snyk, Socket, TrustHub).

        Retorna ``None`` se skill não tem audit ou falha na consulta.
        """
        source, slug = self._split_id(skill_id)
        if not source or not slug:
            return None

        url = f"{self.SKILLS_SH_BASE}/api/v1/skills/audit/{source}/{slug}"
        headers: dict[str, str] = {}
        if self._skills_sh_token:
            headers["Authorization"] = f"Bearer {self._skills_sh_token}"

        resp = self._request(url, headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()

        entries: list[AuditEntry] = []
        for audit in payload.get("audits", []):
            entries.append(
                AuditEntry(
                    provider=audit.get("provider", ""),
                    slug=audit.get("slug", ""),
                    status=audit.get("status", "unknown"),
                    summary=audit.get("summary", ""),
                    audited_at=audit.get("auditedAt", ""),
                    risk_level=audit.get("riskLevel", "UNKNOWN"),
                )
            )

        return AuditResult(skill_id=payload.get("id", skill_id), audits=entries)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_installed(self, skill_id: str) -> bool:
        """Informa se a skill já existe no índice local."""
        slug = skill_id.split("/")[-1]
        return slug.lower() in self._build_local_index()

    @staticmethod
    def _split_id(skill_id: str) -> tuple[str, str]:
        parts = skill_id.split("/")
        if len(parts) >= 2:
            return "/".join(parts[:-1]), parts[-1]
        return "", ""
