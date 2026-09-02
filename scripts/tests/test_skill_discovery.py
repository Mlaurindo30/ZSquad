import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from scripts.skill_discovery import Skill, SkillContent, SkillDiscoveryService


FIXTURES = Path(__file__).parent / "fixtures"
FIXTURES.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------


def _write_fixture(name: str, payload: Any) -> Path:
    path = FIXTURES / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _content_hash(files: list[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda item: item["path"]):
        digest.update(entry["path"].encode())
        digest.update(b"\0")
        digest.update(entry["contents"].encode())
        digest.update(b"\0")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkillDiscoveryService:
    def test_split_id(self):
        svc = SkillDiscoveryService()
        assert svc._split_id("owner/repo/skill-name") == ("owner/repo", "skill-name")
        assert svc._split_id("short") == ("", "")

    def test_build_local_index_empty(self, tmp_path):
        svc = SkillDiscoveryService(skills_root=str(tmp_path))
        assert svc._build_local_index() == set()

    def test_build_local_index_scans_dirs(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "alpha").mkdir()
        (skills_dir / "alpha" / "SKILL.md").write_text("name: alpha", encoding="utf-8")
        (skills_dir / "bravo").mkdir()
        (skills_dir / "bravo" / "SKILL.md").write_text("name: bravo", encoding="utf-8")
        (skills_dir / "charlie").mkdir()

        svc = SkillDiscoveryService(skills_root=str(skills_dir))
        assert svc._build_local_index() == {"alpha", "bravo"}

    def test_is_installed(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "find-skills").mkdir()
        (skills_dir / "find-skills" / "SKILL.md").write_text("name: find-skills", encoding="utf-8")

        svc = SkillDiscoveryService(skills_root=str(skills_dir))
        assert svc.is_installed("vercel-labs/skills/find-skills") is True
        assert svc.is_installed("unknown/missing-skill") is False

    def test_refresh_local_index(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "x").mkdir()
        (skills_dir / "x" / "SKILL.md").write_text("name: x", encoding="utf-8")

        svc = SkillDiscoveryService(skills_root=str(skills_dir))
        assert svc._build_local_index() == {"x"}

        (skills_dir / "y").mkdir()
        (skills_dir / "y" / "SKILL.md").write_text("name: y", encoding="utf-8")
        svc.refresh_local_index()
        assert svc._build_local_index() == {"x", "y"}

    @patch("scripts.skill_discovery.requests.get")
    def test_search_skills_sh_success(self, mock_get):
        payload = {
            "data": [
                {
                    "id": "vercel-labs/skills/find-skills",
                    "slug": "find-skills",
                    "name": "find-skills",
                    "source": "vercel-labs/skills",
                    "installs": 100,
                    "sourceType": "github",
                    "installUrl": "https://github.com/vercel-labs/skills",
                    "url": "https://skills.sh/vercel-labs/skills/find-skills",
                }
            ],
            "searchType": "fuzzy",
        }
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = payload
        mock_get.return_value.raise_for_status = lambda: None

        svc = SkillDiscoveryService(skills_sh_token="fake-token")
        results = svc.search("find skills", limit=5)

        assert len(results) == 1
        assert results[0].id == "vercel-labs/skills/find-skills"
        assert results[0].installs == 100
        assert results[0].provider is None

    @patch("scripts.skill_discovery.requests.get")
    def test_search_skillsmp_success(self, mock_get):
        payload = {
            "success": True,
            "data": {
                "skills": [
                    {
                        "id": "skillsmp/react-native-skill",
                        "slug": "react-native-skill",
                        "name": "React Native",
                        "source": "skillsmp",
                        "installs": 50,
                        "sourceType": "github",
                        "installUrl": "https://skillsmp.com/react-native",
                        "url": "https://skillsmp.com/react-native",
                    }
                ]
            },
        }
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = payload
        mock_get.return_value.raise_for_status = lambda: None

        svc = SkillDiscoveryService(skillsmp_key="fake-key")
        results = svc.search("react native", limit=5)

        assert len(results) == 1
        assert results[0].id == "skillsmp/react-native-skill"
        assert results[0].provider == "skillsmp"

    @patch("scripts.skill_discovery.requests.get")
    def test_search_dedup_across_sources(self, mock_get):
        sh_payload = {
            "data": [
                {
                    "id": "shared/skill",
                    "slug": "shared-skill",
                    "name": "Shared",
                    "source": "shared/skill",
                    "installs": 10,
                    "sourceType": "github",
                    "installUrl": "",
                    "url": "",
                }
            ],
            "searchType": "fuzzy",
        }
        mp_payload = {
            "success": True,
            "data": {
                "skills": [
                    {
                        "id": "shared/skill",
                        "slug": "shared-skill",
                        "name": "Shared",
                        "source": "shared/skill",
                        "installs": 10,
                        "sourceType": "github",
                        "installUrl": "",
                        "url": "",
                    }
                ]
            },
        }

        def side_effect(url, **kwargs):
            if "skillsmp.com" in url:
                r = mock_get.return_value
                r.status_code = 200
                r.json.return_value = mp_payload
            else:
                r = mock_get.return_value
                r.status_code = 200
                r.json.return_value = sh_payload
            return r

        mock_get.side_effect = side_effect

        svc = SkillDiscoveryService(skills_sh_token="t", skillsmp_key="k")
        results = svc.search("shared", limit=20)
        assert len(results) == 1

    @patch("scripts.skill_discovery.requests.get")
    def test_fetch_skill(self, mock_get):
        payload = {
            "id": "vercel-labs/skills/find-skills",
            "hash": "abc123",
            "installs": 100,
            "files": [
                {"path": "SKILL.md", "contents": "name: find-skills\ndescription: Find skills."},
                {"path": "scripts/helper.py", "contents": "print('hello')"},
            ],
        }
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = payload
        mock_get.return_value.raise_for_status = lambda: None

        svc = SkillDiscoveryService(skills_sh_token="fake-token")
        content = svc.fetch_skill("vercel-labs/skills/find-skills")
        assert content is not None
        assert content.skill_id == "vercel-labs/skills/find-skills"
        assert content.hash == "abc123"
        assert len(content.files) == 2

    @patch("scripts.skill_discovery.requests.get")
    def test_fetch_skill_not_found(self, mock_get):
        mock_get.return_value.status_code = 404
        mock_get.return_value.raise_for_status = lambda: None

        svc = SkillDiscoveryService(skills_sh_token="fake-token")
        assert svc.fetch_skill("missing/skill") is None

    def test_install_skill(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        svc = SkillDiscoveryService(skills_root=str(skills_dir))
        files = [
            {"path": "SKILL.md", "contents": "name: my-skill\ndescription: Test."},
            {"path": "scripts/run.py", "contents": "print('ok')"},
        ]
        fake_content = SkillContent(
            skill_id="owner/my-skill",
            files=files,
            hash=_content_hash(files),
        )
        with patch.object(svc, "fetch_skill", return_value=fake_content):
            result = svc.install_skill("owner/my-skill", target_dir=str(skills_dir))

        assert result is True
        target = skills_dir / "my-skill"
        assert target.exists()
        assert (target / "SKILL.md").exists()
        assert (target / "scripts" / "run.py").exists()

    def test_install_skill_rejects_missing_or_mismatched_integrity_hash(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        svc = SkillDiscoveryService(skills_root=str(skills_dir))
        files = [{"path": "SKILL.md", "contents": "name: my-skill"}]
        for value in (None, "0" * 64):
            content = SkillContent(skill_id="owner/my-skill", files=files, hash=value)
            with patch.object(svc, "fetch_skill", return_value=content):
                assert svc.install_skill("owner/my-skill", target_dir=str(skills_dir)) is False
        assert not (skills_dir / "my-skill").exists()

    @patch("scripts.skill_discovery.socket.getaddrinfo")
    def test_remote_url_rejects_private_resolution_and_untrusted_hosts(self, resolve):
        resolve.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with pytest.raises(ValueError):
            SkillDiscoveryService._validate_remote_url("https://skills.sh/api")
        resolve.return_value = [(2, 1, 6, "", ("8.8.8.8", 443))]
        with pytest.raises(ValueError):
            SkillDiscoveryService._validate_remote_url("https://evil.example/api")
        with pytest.raises(ValueError):
            SkillDiscoveryService._validate_remote_url("http://skills.sh/api")

    @patch("scripts.skill_discovery.socket.getaddrinfo")
    @patch("scripts.skill_discovery.requests.get")
    def test_request_revalidates_redirect_destination(self, mock_get, resolve):
        resolve.return_value = [(2, 1, 6, "", ("8.8.8.8", 443))]
        mock_get.return_value.status_code = 302
        mock_get.return_value.headers = {"Location": "http://127.0.0.1/metadata"}
        with pytest.raises(ValueError):
            SkillDiscoveryService._request("https://skills.sh/api")
        assert mock_get.call_count == 1

    def test_install_skill_rejects_path_traversal_before_writing(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        svc = SkillDiscoveryService(skills_root=str(skills_dir))
        fake_content = SkillContent(
            skill_id="owner/my-skill",
            files=[{"path": "../../outside.py", "contents": "malicious"}],
        )
        with patch.object(svc, "fetch_skill", return_value=fake_content):
            result = svc.install_skill("owner/my-skill", target_dir=str(skills_dir))
        assert result is False
        assert not (tmp_path / "outside.py").exists()
        assert not (skills_dir / "my-skill").exists()

    def test_install_skill_rejects_absolute_path_before_writing(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        outside = (tmp_path / "outside.py").resolve()
        svc = SkillDiscoveryService(skills_root=str(skills_dir))
        fake_content = SkillContent(
            skill_id="owner/my-skill",
            files=[{"path": str(outside), "contents": "malicious"}],
        )
        with patch.object(svc, "fetch_skill", return_value=fake_content):
            result = svc.install_skill("owner/my-skill", target_dir=str(skills_dir))
        assert result is False
        assert not outside.exists()
        assert not (skills_dir / "my-skill").exists()

    def test_install_skill_already_exists(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        existing = skills_dir / "my-skill"
        existing.mkdir()
        (existing / "SKILL.md").write_text("name: my-skill", encoding="utf-8")

        svc = SkillDiscoveryService(skills_root=str(skills_dir))
        with patch.object(svc, "fetch_skill", return_value=SkillContent(skill_id="owner/my-skill", files=[])):
            assert svc.install_skill("owner/my-skill", target_dir=str(skills_dir)) is False

    def test_install_skill_invalid_id(self, tmp_path):
        svc = SkillDiscoveryService(skills_root=str(tmp_path))
        assert svc.install_skill("badid", target_dir=str(tmp_path)) is False

    @patch("scripts.skill_discovery.requests.get")
    def test_audit_skill(self, mock_get):
        payload = {
            "id": "vercel-labs/skills/find-skills",
            "audits": [
                {
                    "provider": "Socket",
                    "slug": "socket",
                    "status": "pass",
                    "summary": "No alerts",
                    "auditedAt": "2026-04-15T12:00:00.000Z",
                },
                {
                    "provider": "Snyk",
                    "slug": "snyk",
                    "status": "warn",
                    "summary": "1 medium issue",
                    "auditedAt": "2026-04-15T12:05:00.000Z",
                    "riskLevel": "MEDIUM",
                },
            ],
        }
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = payload
        mock_get.return_value.raise_for_status = lambda: None

        svc = SkillDiscoveryService(skills_sh_token="fake-token")
        audit = svc.audit_skill("vercel-labs/skills/find-skills")
        assert audit is not None
        assert len(audit.audits) == 2
        assert audit.audits[0].status == "pass"
        assert audit.audits[1].risk_level == "MEDIUM"

    @patch("scripts.skill_discovery.requests.get")
    def test_audit_skill_not_found(self, mock_get):
        mock_get.return_value.status_code = 404
        mock_get.return_value.raise_for_status = lambda: None

        svc = SkillDiscoveryService(skills_sh_token="fake-token")
        assert svc.audit_skill("missing/skill") is None

    def test_rank_sorts_by_score(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "alpha").mkdir()
        (skills_dir / "alpha" / "SKILL.md").write_text("name: alpha", encoding="utf-8")

        svc = SkillDiscoveryService(skills_root=str(skills_dir))
        skills = [
            Skill(id="a/1", slug="a1", name="A1", source="a", installs=10, source_type="github", install_url=None, url=""),
            Skill(id="a/2", slug="a2", name="A2", source="a", installs=100, source_type="github", install_url=None, url=""),
            Skill(id="a/3", slug="a3", name="A3", source="a", installs=50, source_type="github", install_url=None, url=""),
        ]
        ranked = svc._rank(skills, "mobile")
        assert ranked[0].installs == 100
