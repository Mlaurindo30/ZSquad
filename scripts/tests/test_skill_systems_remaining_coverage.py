from __future__ import annotations

import hashlib
import json
import runpy
import socket
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests
import yaml

import scripts.auto_skill_learner as learner_mod
import scripts.skill_discovery as discovery
import scripts.skill_discovery_ast as discovery_ast
from scripts.auto_skill_learner import AutoSkillLearner, LearnedSkillDraft
from scripts.skill_curator import (
    _check_anti_fabrication,
    _check_dangerous_patterns,
    _check_license,
    _check_secrets,
    review_intake_skill,
)
from scripts.skill_discovery import Skill, SkillContent, SkillDiscoveryService
from scripts.skill_selector import (
    DiscoveredSkill,
    _boost_for_persona,
    _tokenize,
    select_skills_for_task,
    suggest_and_load,
)


def _skill_md(name: str = "good-skill", body: str = "") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        "description: A grounded operational skill.\n"
        "version: 1.0.0\n"
        "license: MIT License\n"
        "---\n\n"
        "# Skill\n\n"
        "## Ground Truth\nUse verified evidence.\n\n"
        "## Empty State\nReturn empty if empty.\n\n"
        f"{body}"
    )


# skill_selector

def test_selector_helpers_ranking_ties_cutoffs_and_empty_tokens():
    assert _tokenize("The API, com go-lang e x") == ["api", "go-lang"]
    exact = DiscoveredSkill("python-api", "x", description="backend", metadata={"persona": "Platform Engineer"})
    partial = DiscoveredSkill("ops", "y", description="platform automation")
    assert _boost_for_persona(exact, None) == 0
    assert _boost_for_persona(exact, "platform engineer") == 2
    assert _boost_for_persona(partial, "platform engineer") == pytest.approx(0.3)

    ranked = select_skills_for_task(
        "python api backend",
        [
            exact,
            DiscoveredSkill("unrelated", "z", description="none"),
            DiscoveredSkill("python", "a", description="api backend", metadata={"tag": "python"}),
        ],
        persona="platform engineer",
        max_results=2,
    )
    assert len(ranked) == 2
    assert ranked[0].score >= ranked[1].score
    assert any("name" in reason for reason in ranked[0].match_reasons)
    assert select_skills_for_task("the and com", [exact]) == []
    assert select_skills_for_task("python", [exact], min_score=999) == []


def test_suggest_and_load_success_and_failures(tmp_path, monkeypatch):
    good = tmp_path / "good" / "SKILL.md"
    good.parent.mkdir()
    good.write_text(_skill_md("good"), encoding="utf-8")
    missing = tmp_path / "missing" / "SKILL.md"
    bad = tmp_path / "bad" / "SKILL.md"
    bad.parent.mkdir()
    bad.write_bytes(b"\xff")
    skills = [
        discovery_ast.DiscoveredSkill("good", str(good), description="python api"),
        discovery_ast.DiscoveredSkill("missing", str(missing), description="python api"),
        discovery_ast.DiscoveredSkill("bad", str(bad), description="python api"),
    ]
    monkeypatch.setattr(discovery_ast, "discover_skills", lambda *a, **k: skills)
    original_read_text = Path.read_text

    def read_text_or_fail(path, *args, **kwargs):
        if path == bad:
            raise OSError("unreadable")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text_or_fail)
    suggestions = suggest_and_load("python api", tmp_path, max_results=5, cache=False)
    by_name = {s.ranked.skill.name: s for s in suggestions}
    assert by_name["good"].loaded and by_name["good"].content.startswith("---")
    assert not by_name["missing"].loaded and by_name["missing"].error is None
    assert by_name["bad"].error == "unreadable"


# skill_curator

def test_curator_all_security_patterns_and_license_paths(tmp_path):
    dangerous = "\n".join([
        "rm -rf /", "curl | x bash", "eval(x)", "os.system(x)",
        "subprocess.run(x, shell=True)", "__import__(x)", "pickle.load(x)",
        "yaml.load(x, Loader=None)",
    ])
    assert len(_check_dangerous_patterns(dangerous)) == 8
    secrets = "\n".join([
        "password='abcdefgh'", "sk-" + "a" * 20,
        "ghp_" + "a" * 36, "AKIA" + "A" * 16,
    ])
    assert len(_check_secrets(secrets)) == 4

    skill = tmp_path / "SKILL.md"
    assert _check_license(skill) == []
    skill.write_text("---\nlicense: Proprietary\n---", encoding="utf-8")
    findings = _check_license(skill)
    assert {f.severity for f in findings} == {"WARNING"}
    (tmp_path / "LICENSE").write_text("Apache License Version 2.0", encoding="utf-8")
    assert {f.severity for f in _check_license(skill)} == {"WARNING"}
    skill.write_text("---\nlicense: MIT License\n---", encoding="utf-8")
    assert _check_license(skill) == []
    skill.write_text("GNU General Public License GPL v3", encoding="utf-8")
    assert _check_license(skill)[0].severity == "ADVISORY"

    anti = _check_anti_fabrication("ordinary instructions")
    assert len(anti) == 1 and "ground truth" in anti[0].message
    assert _check_anti_fabrication("ground truth; empty if empty; not found; unverified; boundary; anti-fabricação; antifabricação") == []


def test_curator_review_missing_invalid_and_custom_gates(tmp_path):
    missing = review_intake_skill(tmp_path / "none")
    assert not missing.approved and missing.gates_run == ("presence",)

    folder = tmp_path / "skill"
    folder.mkdir()
    skill = folder / "SKILL.md"
    skill.write_text(_skill_md(body="eval(x)\npassword='abcdefgh'"), encoding="utf-8")
    report = review_intake_skill(folder, run_linter=False)
    assert not report.approved
    assert report.gates_run == (
        "security-dangerous-patterns", "security-secrets", "license-compliance", "anti-fabrication"
    )

    lint = SimpleNamespace(severity="BLOCKER", rule_id="X", message="bad", line_number=2)
    report = review_intake_skill(folder, linter_callable=lambda *a, **k: [lint])
    assert "hermes-linter" in report.gates_run
    assert any(f.gate == "hermes-linter" for f in report.findings)
    with patch.object(AutoSkillLearner, "lint_skill_markdown", side_effect=RuntimeError):
        report = review_intake_skill(folder)
    assert "hermes-linter" in report.gates_run

    skill.write_text(_skill_md("skill", body="not found unverified boundary anti-fabricação antifabricação"), encoding="utf-8")
    assert review_intake_skill(folder).approved


# skill_discovery

def test_skill_model_equality_hash_and_local_index_edges(tmp_path):
    a = Skill("id", "s", "n", "src", 1, "x", None, "u")
    b = Skill("id", "other", "n", "src", 2, "x", None, "u")
    assert a == b and hash(a) == hash(b)
    assert a.__eq__(object()) is NotImplemented

    root = tmp_path / "missing"
    svc = SkillDiscoveryService(skills_root=str(root))
    assert svc._build_local_index() == set()
    assert svc._build_local_index() is svc._local_index
    root.mkdir()
    (root / "file").write_text("x", encoding="utf-8")
    svc.refresh_local_index()
    assert svc._local_index == set()


def test_validate_remote_url_all_rejections(monkeypatch):
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: public)
    SkillDiscoveryService._validate_remote_url("https://skills.sh/path")
    for url in [
        "http://skills.sh", "https://user@skills.sh", "https://user:pw@skills.sh",
        "https://skills.sh:444", "https://evil.example",
    ]:
        with pytest.raises(ValueError):
            SkillDiscoveryService._validate_remote_url(url)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
    with pytest.raises(ValueError, match="sem endereço"):
        SkillDiscoveryService._validate_remote_url("https://skills.sh")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="não público"):
        SkillDiscoveryService._validate_remote_url("https://skills.sh")


def test_request_redirect_success_missing_location_and_limit(monkeypatch):
    monkeypatch.setattr(SkillDiscoveryService, "_validate_remote_url", lambda url: None)
    final = Mock(status_code=200, headers={})
    redirect = Mock(status_code=302, headers={"Location": "/next"})
    get = Mock(side_effect=[redirect, final])
    monkeypatch.setattr(requests, "get", get)
    assert SkillDiscoveryService._request("https://skills.sh/start") is final
    assert get.call_count == 2

    monkeypatch.setattr(requests, "get", Mock(return_value=Mock(status_code=301, headers={})))
    with pytest.raises(requests.exceptions.TooManyRedirects, match="sem destino"):
        SkillDiscoveryService._request("https://skills.sh")

    monkeypatch.setattr(requests, "get", Mock(return_value=Mock(status_code=302, headers={"Location": "/again"})))
    with pytest.raises(requests.exceptions.TooManyRedirects, match="limite"):
        SkillDiscoveryService._request("https://skills.sh")


def test_search_provider_exception_dedup_and_limit(monkeypatch, tmp_path):
    svc = SkillDiscoveryService(skills_root=str(tmp_path))
    one = Skill("same", "one", "One", "a", 100, "x", None, "u")
    duplicate = Skill("same", "two", "Two", "b", 1, "x", None, "u")
    other = Skill("other", "other", "Other", "b", 5, "x", None, "u")
    monkeypatch.setattr(svc, "_search_skills_sh", lambda *a, **k: [one])
    monkeypatch.setattr(svc, "_search_skillsmp", lambda *a, **k: [duplicate, other])
    monkeypatch.setattr(svc, "_rank", lambda items, context="": list(reversed(items)))
    result = svc.search("q", limit=1)
    assert result == [other]

    monkeypatch.setattr(svc, "_search_skills_sh", Mock(side_effect=requests.RequestException("x")))
    monkeypatch.setattr(svc, "_search_skillsmp", Mock(side_effect=(ValueError("y"))))
    assert svc.search("q") == []


def test_provider_parsing_variants(monkeypatch):
    svc = SkillDiscoveryService(skills_sh_token="token", skillsmp_key="key")
    response = Mock()
    response.raise_for_status.return_value = None
    response.status_code = 200
    response.json.return_value = {"data": [
        {"id": "owner/repo/slug", "slug": "slug", "name": "Name", "installs": 7, "source": "github", "installUrl": "i", "url": "u"},
        {"id": "bad"},
    ], "searchType": "semantic"}
    monkeypatch.setattr(svc, "_request", Mock(return_value=response))
    found = svc._search_skills_sh("hello world", 3)
    assert len(found) == 2 and found[0].search_type == "semantic"

    response.json.return_value = {"success": True, "data": {"skills": [{
        "id": "mp", "slug": "slug", "name": "MP", "installs": 3,
        "sourceType": "market", "installUrl": "i", "url": "u", "owner": "dev",
    }]}}
    found = svc._search_skillsmp("q", 2)
    assert found[0].provider == "skillsmp" and found[0].source == "dev"

    response.json.return_value = {"success": False}
    assert svc._search_skillsmp("q") == []
    response.json.return_value = {"success": True, "data": "bad"}
    assert svc._search_skillsmp("q") == []


def test_rank_and_fetch_skill_branches(monkeypatch):
    svc = SkillDiscoveryService(skills_sh_token="token")
    installed = Skill("o/r/local", "local", "L", "s", 10, "x", None, "u")
    zero = Skill("zero", "zero", "Z", "s", 0, "x", None, "u")
    monkeypatch.setattr(svc, "is_installed", lambda skill_id: skill_id == installed.id)
    ranked = svc._rank([zero, installed], "local")
    assert ranked[0] == installed and svc._rank([], "") == []

    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {"id": "remote", "files": [{"path": "SKILL.md", "contents": "x"}], "hash": "h", "installs": 2}
    monkeypatch.setattr(svc, "_request", Mock(return_value=response))
    assert svc.fetch_skill("owner/repo/skill").skill_id == "remote"
    assert svc.fetch_skill("bad") is None
    for status in (404, 401):
        response.status_code = status
        assert svc.fetch_skill("owner/repo/skill") is None
    response.status_code = 500
    response.raise_for_status.side_effect = requests.exceptions.HTTPError()
    assert svc.fetch_skill("owner/repo/skill") is None


def test_install_skill_remaining_failures_and_success(monkeypatch, tmp_path):
    svc = SkillDiscoveryService(skills_root=str(tmp_path / "index"))
    files = [{"path": "SKILL.md", "contents": "hello"}, {"path": "sub/a.txt", "contents": "a"}]
    digest = svc._content_digest(files)
    monkeypatch.setattr(svc, "is_installed", lambda _: False)
    monkeypatch.setattr(svc, "fetch_skill", lambda _: SkillContent("o/r/name", files, digest))
    target = tmp_path / "target"
    assert svc.install_skill("o/r/name", str(target))
    assert (target / "name" / "sub" / "a.txt").read_text() == "a"
    assert not svc.install_skill("bad", str(target))

    monkeypatch.setattr(svc, "is_installed", lambda _: True)
    assert not svc.install_skill("o/r/other", str(target))
    monkeypatch.setattr(svc, "is_installed", lambda _: False)
    for content in [None, SkillContent("x", [], digest), SkillContent("x", files, None),
                    SkillContent("x", files, "wrong"), SkillContent("x", files, "f" * 64)]:
        monkeypatch.setattr(svc, "fetch_skill", lambda _, c=content: c)
        assert not svc.install_skill("o/r/other", str(target))
    for bad_files in [[{"path": "../escape", "contents": "x"}], [{"path": "x", "contents": 1}]]:
        monkeypatch.setattr(svc, "fetch_skill", lambda _, f=bad_files: SkillContent("x", f, svc._content_digest(f) if isinstance(f[0]["contents"], str) else "0" * 64))
        assert not svc.install_skill("o/r/other", str(target))
    monkeypatch.setattr(svc, "fetch_skill", lambda _: SkillContent("x", files, digest))
    (target / "other").mkdir()
    assert not svc.install_skill("o/r/other", str(target))

    fresh = tmp_path / "fresh"
    monkeypatch.setattr(svc, "fetch_skill", lambda _: SkillContent("x", files, digest))
    with patch.object(Path, "write_text", side_effect=OSError):
        assert not svc.install_skill("o/r/fail", str(fresh))


def test_audit_payload_shapes(monkeypatch):
    svc = SkillDiscoveryService()
    response = Mock(status_code=200); response.raise_for_status.return_value = None
    monkeypatch.setattr(svc, "_request", Mock(return_value=response))
    response.json.return_value = {"audits": [{"provider": "custom", "status": "pass", "summary": "ok", "auditedAt": "now", "riskLevel": "low"}]}
    assert svc.audit_skill("owner/repo/skill").audits[0].provider == "custom"
    assert svc.audit_skill("short") is None
    response.status_code = 404
    assert svc.audit_skill("owner/repo/skill") is None
    response.status_code = 500
    response.raise_for_status.side_effect = requests.exceptions.HTTPError()
    with pytest.raises(requests.exceptions.HTTPError):
        svc.audit_skill("owner/repo/skill")


# skill_discovery_ast

def test_ast_helper_error_paths(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"; cache_dir.mkdir()
    cache_file = cache_dir / ".skill_discovery_cache.json"
    cache_file.write_text("{", encoding="utf-8")
    assert discovery_ast._load_cache(cache_dir) == {}

    with patch.object(Path, "write_text", side_effect=OSError):
        discovery_ast._save_cache(cache_dir, {})
    with patch.object(Path, "stat", side_effect=OSError):
        assert discovery_ast._file_key(tmp_path / "x") == ""

    missing = tmp_path / "missing.py"
    assert not discovery_ast._module_registers_tools(missing)
    plain = tmp_path / "plain.py"; plain.write_text("x = 1", encoding="utf-8")
    assert not discovery_ast._module_registers_tools(plain)
    broken = tmp_path / "broken.py"; broken.write_text("registry.register(", encoding="utf-8")
    assert not discovery_ast._module_registers_tools(broken)


def test_ast_discovery_missing_read_errors_cache_and_registration(tmp_path, monkeypatch):
    assert discovery_ast.discover_skills(tmp_path / "none") == []
    skills = tmp_path / "skills"
    md = skills / "fallback" / "SKILL.md"; md.parent.mkdir(parents=True)
    md.write_text("no frontmatter", encoding="utf-8")
    first = discovery_ast.discover_skills(skills, cache=True)
    assert first[0].name == "fallback"
    second = discovery_ast.discover_skills(skills, cache=True)
    assert second == first
    with patch.object(Path, "read_text", side_effect=OSError):
        assert discovery_ast.discover_skills(skills, cache=False) == []

    tools = tmp_path / "tools"
    module = tools / "pkg" / "mod.py"; module.parent.mkdir(parents=True)
    module.write_text("registry.register(tool)\n", encoding="utf-8")
    init = tools / "__init__.py"; init.write_text("registry.register(x)\n", encoding="utf-8")
    found = discovery_ast.discover_tools(tools, cache=True)
    assert [t.name for t in found] == ["__init__", "mod"]
    assert discovery_ast.discover_tools(tools, cache=True) == found
    assert discovery_ast.discover_tools(tmp_path / "absent") == []

    with patch.object(discovery_ast, "_module_registers_tools", return_value=False):
        assert discovery_ast.discover_tools(tools, cache=False) == []


def test_ast_parse_frontmatter_yaml_error_non_dict_and_json_safe():
    assert discovery_ast._parse_skill_frontmatter("---\n[bad\n---\n") == ("", "", {})
    assert discovery_ast._parse_skill_frontmatter("---\n- one\n---\n") == ("", "", {})
    assert discovery_ast._parse_skill_frontmatter("---\nname: x") == ("", "", {})
    value = {"dates": [datetime(2024, 1, 2), "x"]}
    assert discovery_ast._coerce_json_safe(value)["dates"][0].startswith("2024-01-02")


# auto_skill_learner

def test_learner_helpers_catalog_and_description_paths(tmp_path):
    folder = tmp_path / "skills" / "engineering" / "new"; folder.mkdir(parents=True)
    md = folder / "SKILL.md"; md.write_text("\ufeff---\ndescription: Desc\n---\nbody", encoding="utf-8")
    (folder / "z.txt").write_text("z", encoding="utf-8")
    assert len(learner_mod._compute_folder_sha256(folder)) == 64
    assert learner_mod._extract_description_from_skill_md(md) == "Desc"
    md.write_text("body", encoding="utf-8")
    assert learner_mod._extract_description_from_skill_md(md) == ""
    md.write_text("---\ndescription: x", encoding="utf-8")
    assert learner_mod._extract_description_from_skill_md(md) == ""
    md.write_text("---\n- x\n---", encoding="utf-8")
    assert learner_mod._extract_description_from_skill_md(md) == ""
    with patch.object(Path, "read_text", side_effect=OSError):
        assert learner_mod._extract_description_from_skill_md(md) == ""

    learner_mod._update_skills_catalog(tmp_path, "new", "engineering", folder)
    config = tmp_path / "config"; config.mkdir()
    catalog = config / "skills-catalog.yaml"
    catalog.write_text("[bad]", encoding="utf-8")
    learner_mod._update_skills_catalog(tmp_path, "new", "engineering", folder)
    catalog.write_text("{", encoding="utf-8")
    learner_mod._update_skills_catalog(tmp_path, "new", "engineering", folder)
    catalog.write_text("catalog: []\n", encoding="utf-8")
    md.write_text(_skill_md("new"), encoding="utf-8")
    learner_mod._update_skills_catalog(tmp_path, "new", "engineering", folder)
    learner_mod._update_skills_catalog(tmp_path, "new", "engineering", folder)
    data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    assert data["active_skill_count"] == 1
    with patch.object(Path, "write_text", side_effect=OSError):
        learner_mod._update_skills_catalog(tmp_path, "other", "engineering", folder)


def test_learner_synthesis_ledger_lint_and_quality_branches(tmp_path):
    work = tmp_path / "work" / "W" / "documentation"; work.mkdir(parents=True)
    (work / "delivery-ledger.md").write_text("Ledger context", encoding="utf-8")
    learner = AutoSkillLearner(tmp_path)
    draft = learner.synthesize_skill_from_work_item("W", " My Weird Skill! ", allowed_tools=[])
    assert draft.skill_name == "my-weird-skill" and "Ledger context" in draft.raw_markdown

    assert learner.lint_skill_markdown("body")[0].rule_id == "MISSING_FRONTMATTER"
    assert learner.lint_skill_markdown("---\n- item\n---\n")[0].rule_id == "INVALID_YAML"
    assert learner.lint_skill_markdown("---\n: bad\n---\n")[0].rule_id == "YAML_PARSE_ERROR"
    missing_fields = learner.lint_skill_markdown("---\nname: ''\n---\n")
    assert {f.rule_id for f in missing_fields} == {"MISSING_NAME", "MISSING_DESCRIPTION"}
    bad = "---\nname: Bad_Name\ndescription: revolutionary\n---\n" + "cat file\n" * 160
    ids = {f.rule_id for f in learner.lint_skill_markdown(bad, expected_slug="expected")}
    assert {"NAME_DIRECTORY_MISMATCH", "MARKETING_LANGUAGE", "SHELL_UTIL_PROSE"} <= ids

    empty = learner.evaluate_prompt_quality("")
    assert empty.overall_score == 40 and empty.suggestions
    vague = learner.evaluate_prompt_quality("do it maybe etc")
    assert len(vague.suggestions) >= 4
    strong = learner.evaluate_prompt_quality(
        "Objective: implement exact output. Given input and context, must not fabricate. "
        "If missing return empty. Do not modify outside scope. Acceptance criteria: test evidence. "
        "Example response format json."
    )
    assert strong.overall_score > vague.overall_score


def test_learner_refine_deposit_promote_discover(tmp_path, monkeypatch):
    learner = AutoSkillLearner(tmp_path)
    work = tmp_path / "work" / "W"; (work / "documentation").mkdir(parents=True)
    (work / "status.yaml").write_text("gates:\n  g1:\n    status: rejected\n", encoding="utf-8")
    (work / "documentation" / "delivery-ledger.md").write_text("ERROR happened", encoding="utf-8")
    assert len(learner.refine_persona_heuristics("W", "agent")) == 2
    (work / "status.yaml").write_text("{", encoding="utf-8")
    (work / "documentation" / "delivery-ledger.md").write_text("ok", encoding="utf-8")
    assert "convergiu" in learner.refine_persona_heuristics("W", "agent")[0]

    draft = LearnedSkillDraft("new", "desc", "p", [], [], "W", _skill_md("new"))
    deposited = learner.deposit_in_intake(draft)
    assert deposited.exists()
    ok, message = learner.promote_skill("missing")
    assert not ok and "não encontrada" in message

    bad_dir = tmp_path / "skills" / "discovery" / "intake" / "bad"; bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("body", encoding="utf-8")
    ok, message = learner.promote_skill("bad")
    assert not ok and "bloqueada" in message

    ok, message = learner.promote_skill("new", "engineering")
    assert ok and "promovida" in message
    assert learner.promote_skill("new", "engineering")[0] is False

    monkeypatch.setattr(learner_mod, "discover_skills", Mock(return_value=["s"]))
    monkeypatch.setattr(learner_mod, "discover_tools", Mock(return_value=["t"]))
    assert learner.discover_local_skills() == ["s"]
    assert learner.discover_local_tools() == ["t"]
    assert learner.discover_local_skills(tmp_path) == ["s"]
    assert learner.discover_local_tools(tmp_path) == ["t"]


def test_remaining_selector_discovery_and_ast_branches(tmp_path, monkeypatch):
    assert select_skills_for_task("the is", []) == []

    local = tmp_path / "local"
    skill_dir = local / "present"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("x", encoding="utf-8")
    second_dir = local / "second"
    second_dir.mkdir()
    (second_dir / "SKILL.md").write_text("x", encoding="utf-8")
    (local / "without-skill").mkdir()
    svc = SkillDiscoveryService(skills_root=local)
    assert svc._build_local_index() == {"present", "second"}

    low = Skill("low", "low", "Low", "src", 1, "github", None, "url")
    high = Skill("high", "high", "High", "src", 10, "github", None, "url")
    monkeypatch.setattr(svc, "_search_skills_sh", lambda *a, **k: [low, high])
    monkeypatch.setattr(svc, "_search_skillsmp", lambda *a, **k: [])
    assert [s.id for s in svc.search("q", min_installs=5)] == ["high"]

    remote = SkillDiscoveryService(skills_root=local)
    assert remote._search_skills_sh("q") == []
    remote._skills_sh_token = "token"
    monkeypatch.setattr(remote, "_request", lambda *a, **k: Mock(status_code=401))
    assert remote._search_skills_sh("q") == []
    monkeypatch.setattr(remote, "_request", Mock(side_effect=requests.RequestException))
    assert remote._search_skills_sh("q") == []

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"data": [None, {"id": "ok"}]}
    monkeypatch.setattr(remote, "_request", lambda *a, **k: response)
    assert [s.id for s in remote._search_skillsmp("q")] == ["ok"]

    monkeypatch.setattr(svc, "is_installed", lambda skill_id: False)
    assert svc._rank([low], "")[0].id == "low"
    assert svc.audit_skill("low") is None
    monkeypatch.setattr(svc, "_request", lambda *a, **k: Mock(status_code=200, json=lambda: {"files": []}))
    fetched = svc.fetch_skill("src/low")
    assert fetched is not None and fetched.files == []

    monkeypatch.setattr(svc, "fetch_skill", lambda skill_id: SkillContent(skill_id, [], None))
    assert svc.install_skill("src/low", local) is False

    target = local / "high"
    target.mkdir(parents=True)
    monkeypatch.setattr(svc, "fetch_skill", lambda skill_id: SkillContent(
        skill_id,
        [{"path": "SKILL.md", "contents": "ok"}],
        hashlib.sha256(b"ok").hexdigest(),
    ))
    assert svc.install_skill("src/high", local) is False

    symlink_content = SkillContent(
        "src/link",
        [{"path": "folder/SKILL.md", "contents": "ok"}],
        SkillDiscoveryService._content_digest([{"path": "folder/SKILL.md", "contents": "ok"}]),
    )
    monkeypatch.setattr(svc, "fetch_skill", lambda skill_id: symlink_content)
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == "folder" or original_is_symlink(path))
    assert svc.install_skill("src/link", local) is False

    escaped = SkillContent(
        "src/escape",
        [{"path": "SKILL.md", "contents": "ok"}],
        SkillDiscoveryService._content_digest([{"path": "SKILL.md", "contents": "ok"}]),
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "resolve", lambda path: Path("C:/outside") if path.name == "SKILL.md" else path.absolute())
        scoped.setattr(svc, "fetch_skill", lambda skill_id: escaped)
        assert svc.install_skill("src/escape", local) is False

    svc._skills_sh_token = "token"
    auth_response = Mock(status_code=404)
    monkeypatch.setattr(svc, "_request", lambda *a, **k: auth_response)
    assert svc.audit_skill("src/auth") is None
    assert auth_response is not None

    import ast

    assert discovery_ast._is_registry_register_call(ast.parse("call()")) is False
    assert discovery_ast._parse_skill_frontmatter("\ufeff---\nname: bom\n---\n")[:2] == ("bom", "")

    tools = tmp_path / "cached-tools"
    tools.mkdir()
    module = tools / "plain.py"
    module.write_text("x = 1\n", encoding="utf-8")
    key = discovery_ast._file_key(module)
    cache = {str(module): {"key": key, "registers": False, "name": "plain", "module": str(module)}}
    discovery_ast._cache_path(tools).write_text(json.dumps(cache), encoding="utf-8")
    assert discovery_ast.discover_tools(tools, cache=True) == []


def test_remaining_learner_branch_paths(tmp_path, monkeypatch, capsys):
    learner = AutoSkillLearner(tmp_path)
    ledger = tmp_path / "work" / "W-1" / "documentation" / "delivery-ledger.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("ledger evidence", encoding="utf-8")
    assert "ledger evidence" in learner.synthesize_skill_from_work_item("W-1", "ledger-skill", "agent", "").raw_markdown
    assert "explicit summary" in learner.synthesize_skill_from_work_item("W-1", "summary-skill", "agent", "explicit summary").raw_markdown

    valid = _skill_md(body="Use grep tool.\nUse cat instead of view_file.\n")
    assert not [f for f in learner.lint_skill_markdown(valid, expected_slug="good-skill") if f.rule_id == "SHELL_UTIL_PROSE"]

    trajectories = tmp_path / "work" / "W-2" / "trajectories"
    trajectories.mkdir(parents=True)
    (trajectories / "bad.json").write_text("{", encoding="utf-8")
    (trajectories / "ok.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    assert "convergiu" in learner.refine_persona_heuristics("W-2", "agent")[0]
    status = tmp_path / "work" / "W-2" / "status.yaml"
    status.write_text("gates: []\n", encoding="utf-8")
    assert "convergiu" in learner.refine_persona_heuristics("W-2", "agent")[0]
    status.write_text("gates:\n  approved:\n    status: passed\n", encoding="utf-8")
    assert "convergiu" in learner.refine_persona_heuristics("W-2", "agent")[0]

    destination = tmp_path / "skills" / "engineering" / "replace-me"
    destination.mkdir(parents=True)
    (destination / "old").write_text("old", encoding="utf-8")
    intake = tmp_path / "skills" / "discovery" / "intake" / "replace-me"
    intake.mkdir(parents=True)
    (intake / "SKILL.md").write_text(_skill_md("replace-me"), encoding="utf-8")
    monkeypatch.setattr(learner_mod, "review_intake_skill", Mock(), raising=False)
    monkeypatch.setattr("scripts.skill_curator.review_intake_skill", lambda path: SimpleNamespace(findings=[]))
    monkeypatch.setattr(learner, "lint_skill_markdown", lambda *a, **k: [])
    assert learner.promote_skill("replace-me", "engineering")[0] is True
    assert (destination / "SKILL.md").is_file()

    blocked = tmp_path / "skills" / "discovery" / "intake" / "lint-blocked"
    blocked.mkdir(parents=True)
    (blocked / "SKILL.md").write_text(_skill_md("lint-blocked"), encoding="utf-8")
    blocker = SimpleNamespace(severity="BLOCKER", message="blocked")
    monkeypatch.setattr(learner, "lint_skill_markdown", lambda *a, **k: [blocker])
    ok, message = learner.promote_skill("lint-blocked", "engineering")
    assert ok is False and "linter Hermes" in message

    complete_prompt = (
        "Objective mission " + "x" * 120 + " ground truth standards invent empty "
        "anti-fabrication boundaries read-only scope"
    )
    assert learner.evaluate_prompt_quality(complete_prompt).ground_truth_score == 25

    perfect = tmp_path / "perfect.txt"
    perfect.write_text("x", encoding="utf-8")
    report = SimpleNamespace(
        overall_score=100, objective_clarity=25, ground_truth_score=25,
        anti_fabrication_score=25, boundary_score=25, suggestions=[],
    )
    monkeypatch.setattr(AutoSkillLearner, "evaluate_prompt_quality", lambda *a: report)
    monkeypatch.setattr(learner_mod, "AutoSkillLearner", lambda: learner)
    assert learner_mod.main(["eval-prompt", "--path", str(perfect)]) == 0
    assert "Sugestões" not in capsys.readouterr().out

    suggestions = [
        SimpleNamespace(ranked=SimpleNamespace(skill=SimpleNamespace(name=f"s{i}", path="p"), score=1.0, match_reasons=[]), loaded=False, error=None, content=None)
        for i in range(2)
    ]
    monkeypatch.setattr("scripts.skill_selector.suggest_and_load", lambda **kwargs: suggestions)
    assert learner_mod.main(["select-skills", "--task", "task"]) == 0
    output = capsys.readouterr().out
    assert "1. s0" in output and "2. s1" in output and "erro desconhecido" in output


def test_learner_cli_all_commands_and_runpy(tmp_path, monkeypatch, capsys):
    learner = AutoSkillLearner(tmp_path)
    monkeypatch.setattr(learner_mod, "AutoSkillLearner", lambda: learner)
    monkeypatch.setattr(learner, "synthesize_skill_from_work_item", Mock(return_value=LearnedSkillDraft("n", "d", "p", [], [], "w", "md")))
    monkeypatch.setattr(learner, "deposit_in_intake", Mock(return_value=tmp_path / "SKILL.md"))
    assert learner_mod.main(["learn", "--work-item", "W", "--name", "n"]) == 0

    lint_dir = tmp_path / "lint"; lint_dir.mkdir()
    lint_file = lint_dir / "SKILL.md"; lint_file.write_text(_skill_md("lint"), encoding="utf-8")
    assert learner_mod.main(["lint", "--path", str(lint_file)]) == 0
    lint_file.write_text("body", encoding="utf-8")
    assert learner_mod.main(["lint", "--path", str(lint_file)]) == 1
    assert learner_mod.main(["lint", "--path", str(tmp_path / "missing.md")]) == 1

    monkeypatch.setattr(learner, "refine_persona_heuristics", Mock(return_value=["h"]))
    assert learner_mod.main(["refine", "--work-item", "W"]) == 0
    prompt = tmp_path / "prompt.txt"; prompt.write_text("objective exact output", encoding="utf-8")
    assert learner_mod.main(["eval-prompt", "--path", str(prompt)]) == 0
    assert learner_mod.main(["eval-prompt", "--path", str(tmp_path / "missing.txt")]) == 1

    monkeypatch.setattr(learner, "promote_skill", Mock(return_value=(True, "ok")))
    assert learner_mod.main(["promote", "--name", "n"]) == 0
    learner.promote_skill.return_value = (False, "no")
    assert learner_mod.main(["promote", "--name", "n"]) == 1

    monkeypatch.setattr(learner, "discover_local_skills", Mock(return_value=[SimpleNamespace(name="s", source="local", path="p")]))
    monkeypatch.setattr(learner, "discover_local_tools", Mock(return_value=[SimpleNamespace(name="t", source="local", module="m")]))
    assert learner_mod.main(["discover-skills", "--dir", str(tmp_path)]) == 0
    assert learner_mod.main(["discover-tools"]) == 0
    learner.discover_local_skills.return_value = []
    learner.discover_local_tools.return_value = []
    assert learner_mod.main(["discover-skills"]) == 0
    assert learner_mod.main(["discover-tools"]) == 0

    with patch("scripts.skill_selector.suggest_and_load", return_value=[]):
        assert learner_mod.main(["select-skills", "--task", "q"]) == 0
    suggestion = SimpleNamespace(
        ranked=SimpleNamespace(skill=SimpleNamespace(name="s", path="p"), score=1.2, match_reasons=("name",)),
        loaded=True, error=None, content="body\ntext",
    )
    with patch("scripts.skill_selector.suggest_and_load", return_value=[suggestion]):
        assert learner_mod.main(["select-skills", "--task", "q", "--dir", str(tmp_path)]) == 0
    monkeypatch.setattr(sys, "argv", ["auto_skill_learner.py"])
    assert learner_mod.main(None) == 0

    # Exercise both import-path bootstrap and the __main__ guard without persistent process changes.
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != str(learner_mod.ROOT)])
    monkeypatch.setattr(sys, "argv", [str(Path(learner_mod.__file__)), "discover-skills"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(learner_mod.__file__, run_name="__main__")
    assert exc.value.code == 0
    assert capsys.readouterr().out
