"""Tests for scripts/azure_devops_project_setup.py — reusable Azure DevOps process."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from integrations.devops_platform_connector import AzureDevOpsClient  # noqa: E402
from azure_devops_project_setup import AzureDevOpsProjectSetup  # noqa: E402


TEMPLATE = ROOT / "templates" / "devops.yaml"
SCHEMA = ROOT / "contracts" / "devops-config.schema.json"


def _cfg() -> dict:
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def test_template_matches_schema():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_cfg())


def test_identities_map_squad_accounts_to_personas():
    cfg = _cfg()
    identities = cfg["identities"]
    registry = yaml.safe_load((ROOT / "config" / "agent-registry.yaml").read_text(encoding="utf-8"))
    registry_ids = {agent["id"] for agent in registry["agents"]}
    dev = set(identities["development_team"]["used_by"])
    approvers = set(identities["pr_and_card_approver"]["used_by"])
    # US-ACCT-01 (2026-09-02): offensive-cyber-operator foi movido de pr_and_card_approver
    # para service_accounts.cyber_red (cyber-red@michellaurindooutlook812) para garantir
    # duplo sign-off com security-reviewer em paths sensíveis.
    service_users = set()
    if "service_accounts" in cfg:
        for sa in cfg["service_accounts"].values():
            service_users.update(sa.get("used_by", []))
    mapped = dev | approvers | service_users
    assert identities["development_team"]["email"].startswith("squads@")
    assert identities["pr_and_card_approver"]["email"].startswith("arthemis@")
    assert identities["human_master"]["notifications"] is False
    assert identities["human_master"]["used_by"] == []
    # US-16 (2026-09-02): pr_and_card_approver com 5 personas votantes (path filter)
    # + governance-auditor que fecha card no G6. offensive-cyber-operator sai daqui
    # em US-ACCT-01.
    expected_approvers = {
        "code-reviewer",
        "security-reviewer",
        "qa-engineer",
        "performance-engineer",
        "governance-auditor",
    }
    assert approvers == expected_approvers, f"approvers={approvers} esperado={expected_approvers}"
    # US-ACCT-01: cyber-red@ dedicado a offensive-cyber-operator
    assert "service_accounts" in cfg, "service_accounts obrigatório (US-ACCT-01)"
    assert cfg["service_accounts"]["cyber_red"]["email"] == "cyber-red@michellaurindooutlook812.onmicrosoft.com"
    assert "offensive-cyber-operator" in cfg["service_accounts"]["cyber_red"]["used_by"]
    assert cfg["service_accounts"]["customer_data_pii"]["email"] == "customer_data_pii@michellaurindooutlook812.onmicrosoft.com"
    # 41 personas continuam cobertas (40 em development_team + 1 em cyber_red)
    assert "offensive-cyber-operator" not in dev
    assert "offensive-cyber-operator" not in approvers
    # security-reviewer continua nas duas contas (intencional — collaborator + voter)
    assert "security-reviewer" in approvers
    assert mapped == registry_ids
    assert len(mapped) == 41
    # Nota: intersection dev & approvers é permitida para security-reviewer;
    # SoD é garantida pelo nível AAD (squads@ ≠ arthemis@).
    assert cfg["approvals"]["pull_request_agent"] == "code-reviewer"
    assert cfg["approvals"]["card_agent"] == "governance-auditor"
    assert "G1-product" in cfg["approvals"]["human_gates"]
    assert cfg["board"]["apply_custom_card_rules"] is False


def test_phase_tags_and_squad_tags_present():
    """US-1 (2026-09-02): phase_tags e squad_tags devem estar presentes no template."""
    cfg = _cfg()
    assert "phase_tags" in cfg, "phase_tags obrigatório (US-1)"
    assert "squad_tags" in cfg, "squad_tags obrigatório (US-1)"
    phase_tags = cfg["phase_tags"]
    for state in ("blueprint", "scaffolding", "implementation", "code-security-review",
                  "quality-validation", "governance-release", "done"):
        assert state in phase_tags, f"phase_tag ausente para {state}"
        assert phase_tags[state].startswith("phase-")
    squad_tags = cfg["squad_tags"]
    for squad in ("core", "web", "mobile", "data", "ai", "infra-cloud", "quality"):
        assert squad in squad_tags, f"squad_tag ausente para {squad}"
        assert squad_tags[squad].startswith("squad-")


def test_review_qa_state_removed():
    """US-10 (2026-09-02): `review-qa` órfão deve estar fora de state_map."""
    cfg = _cfg()
    assert "review-qa" not in cfg["state_map"], "review-qa deve estar removido do state_map"
    # workflow.yaml também deve estar coerente
    wf = yaml.safe_load((ROOT / "config" / "workflow.yaml").read_text(encoding="utf-8"))
    assert "review-qa" not in wf["flow"]["columns"], "review-qa deve estar fora de flow.columns"
    cycles = yaml.safe_load((ROOT / "config" / "cycles.yaml").read_text(encoding="utf-8"))
    for cycle_name, cycle in cycles["cycles"].items():
        assert "review-qa" not in cycle.get("states", []), f"review-qa deve estar fora de cycles.{cycle_name}.states"


def test_queries_block_present_and_opt_in():
    """US-2 (2026-09-02): bloco queries deve existir e estar habilitado por padrão."""
    cfg = _cfg()
    assert "queries" in cfg, "queries obrigatório"
    assert cfg["queries"]["enabled"] is True
    assert "folder" in cfg["queries"]


def test_analytics_and_delivery_plan_opt_in():
    """US-3 + US-4 (2026-09-02): analytics e delivery_plan configurados."""
    cfg = _cfg()
    assert "analytics" in cfg
    assert cfg["analytics"]["base_url"].startswith("https://analytics.dev.azure.com/")
    assert "delivery_plan" in cfg
    assert cfg["delivery_plan"]["enabled"] is False  # opt-in, default OFF


def test_wiki_and_dashboards_opt_in():
    """US-5 + US-6 (2026-09-02): wiki e dashboards opt-in (default OFF)."""
    cfg = _cfg()
    assert "wiki" in cfg
    assert cfg["wiki"]["enabled"] is False
    assert len(cfg["wiki"]["pages"]) == 5  # Home, Architecture, Onboarding, Runbooks, Compliance
    assert "dashboards" in cfg
    assert cfg["dashboards"]["enabled"] is False
    items = cfg["dashboards"]["items"]
    assert any(d.get("scope") == "project_Team" for d in items)
    assert any(d.get("scope") == "project" for d in items)


def test_scoring_is_fibonacci_capped_at_eight():
    scoring = _cfg()["scoring"]["story_points"]
    assert scoring["allowed"] == [1, 2, 3, 5, 8]
    assert scoring["max"] == 8
    assert scoring["split_rule"] == "mandatory"


def test_setup_applies_required_reviewer_from_arthemis_identity():
    cfg = _cfg()
    client = AzureDevOpsClient("https://cbvgas.visualstudio.com", "Arthemis", "token")
    setup = AzureDevOpsProjectSetup(client, cfg)
    setup.project_id = "proj"
    setup.team_id = "team"
    setup.repo_id = "repo"
    setup.identities = {
        "pr_and_card_approver": {"id": "30124b8c-3fb8-41d3-9d5b-cf2cf6479b6f"},
        "development_team": {"id": "4906a8dd-1bc8-4f3c-84b2-8dbbd0b15a3f"},
    }
    captured = []

    def fake_request(method, url, body=None, content_type="application/json"):
        captured.append((method, url, body))
        if method == "GET" and "repositories" in url:
            return {"defaultBranch": "refs/heads/main"}
        if method == "GET" and "policy/configurations" in url:
            return {"count": 0, "value": []}
        return {"id": "ok"}

    with patch.object(setup, "get", side_effect=lambda url: fake_request("GET", url)):
        with patch.object(setup, "send", side_effect=lambda method, url, body=None, content_type="application/json": fake_request(method, url, body)):
            setup.apply_branch_policies()

    posts = [c for c in captured if c[0] == "POST"]
    assert posts, "expected policy POSTs"
    required = next(c for c in posts if (c[2] or {}).get("type", {}).get("id") == "fd2167ab-b0be-447a-8ec8-39368250530e")
    assert required[2]["settings"]["requiredReviewerIds"] == ["30124b8c-3fb8-41d3-9d5b-cf2cf6479b6f"]


def test_setup_skips_branch_policy_when_repo_has_no_default_branch():
    cfg = _cfg()
    client = AzureDevOpsClient("https://cbvgas.visualstudio.com", "Arthemis", "token")
    setup = AzureDevOpsProjectSetup(client, cfg)
    setup.project_id = "proj"
    setup.repo_id = "repo"
    setup.identities = {"pr_and_card_approver": {"id": "x"}}

    with patch.object(setup, "get", return_value={"id": "repo"}):
        setup.apply_branch_policies()
    statuses = {r["step"]: r["status"] for r in setup.results}
    assert statuses["policy.branch"] == "skip"


def test_required_cycles_present():
    """US-CYCLES-01 (2026-09-02): 8 cycles presentes; spike/release/evolution/incident adicionados."""
    cycles = yaml.safe_load((ROOT / "config" / "cycles.yaml").read_text(encoding="utf-8"))["cycles"]
    expected = {"development", "user-story", "new-project", "bugfix", "spike", "release", "evolution", "incident"}
    assert set(cycles.keys()) == expected, f"cycles ausentes: {expected - set(cycles.keys())}"
    # spike: timebox em timeboxes (map por estado), sem quality/gov
    assert cycles["spike"]["timeboxes"]["blueprint"] == "3d"
    assert "quality-validation" not in cycles["spike"]["states"]
    # release: foco em rollout/rollback
    assert "rollout-plan-defined" in cycles["release"]["practices"]["implementation"]
    # evolution: ADR + blast radius + deprecation
    bp = cycles["evolution"]["practices"]["blueprint"]
    assert "adr-recorded" in bp and "blast-radius-defined" in bp and "deprecation-strategy-defined" in bp
    # incident: triage 15m, blameless postmortem
    assert cycles["incident"]["timeboxes"]["triage"] == "15m"
    assert "blameless-postmortem-recorded" in cycles["incident"]["practices"]["postmortem"]


def _build_setup(cfg: dict | None = None) -> AzureDevOpsProjectSetup:
    cfg = cfg if cfg is not None else _cfg()
    client = AzureDevOpsClient("https://cbvgas.visualstudio.com", "Arthemis", "token")
    return AzureDevOpsProjectSetup(client, cfg)


def test_apply_team_iterations():
    """B-8a (Sprint 1-C): apply_team_iterations vincula iteration nodes ao team.

    Verifica:
    - POST/PATCH são chamados corretamente quando o team não tem as iterações.
    - PATCH final agrega todas as novas identificações.
    - Idempotência: quando as iterações já estão vinculadas, registra skip.
    - Robustez: quando um nó de iteração não existe, registra erro e continua.
    """
    cfg = _cfg()
    cfg["iterations"] = [
        {"name": "Sprint 1", "start": "2026-09-01", "finish": "2026-09-14"},
        {"name": "Sprint 2", "start": "2026-09-15", "finish": "2026-09-28"},
    ]
    setup = _build_setup(cfg)
    setup.project_id = "proj-1"
    setup.team_id = "team-1"
    setup.results = []

    captured: list[tuple[str, str, object]] = []

    def fake_send(method, url, body=None, content_type="application/json"):
        captured.append((method, url, body))
        if "teamsettings/iterations" in url and method == "PATCH":
            return {"ok": True, "value": body}
        if url.endswith("/_apis/projects/Agents%20Squad") or "/_apis/projects/Agents%20Squad?" in url:
            return {"id": "proj-1"}
        return {"id": "ok"}

    def fake_get(url):
        if "teamsettings/iterations" in url:
            return {"value": []}
        if "classificationnodes/iterations/Sprint%201" in url:
            return {"identifier": "node-1", "name": "Sprint 1"}
        if "classificationnodes/iterations/Sprint%202" in url:
            return {"identifier": "node-2", "name": "Sprint 2"}
        return {}

    with patch.object(setup, "get", side_effect=fake_get), \
         patch.object(setup, "send", side_effect=fake_send):
        setup.apply_team_iterations()

    statuses = {r["step"]: r["status"] for r in setup.results}
    assert statuses["team.iterations"] == "ok", f"expected ok, got {statuses}"
    pacts = [c for c in captured if c[0] == "PATCH"]
    assert len(pacts) == 1, f"expected 1 PATCH (merge de identificadores), got {len(pacts)}"
    payload = pacts[0][2]
    assert isinstance(payload, list) and len(payload) == 2
    assert {p["id"] for p in payload} == {"node-1", "node-2"}

    # --- caso idempotente: já vinculado → skip
    setup2 = _build_setup(cfg)
    setup2.project_id = "proj-1"
    setup2.team_id = "team-1"

    def fake_get2(url):
        if "teamsettings/iterations" in url:
            return {"value": [
                {"identification": {"id": "node-1", "name": "Sprint 1"}},
                {"identification": {"id": "node-2", "name": "Sprint 2"}},
            ]}
        if "classificationnodes/iterations/Sprint%201" in url:
            return {"identifier": "node-1", "name": "Sprint 1"}
        if "classificationnodes/iterations/Sprint%202" in url:
            return {"identifier": "node-2", "name": "Sprint 2"}
        return {}

    captured2: list[tuple[str, str, object]] = []
    def fake_send2(method, url, body=None, content_type="application/json"):
        captured2.append((method, url, body))
        return {"ok": True}

    with patch.object(setup2, "get", side_effect=fake_get2), \
         patch.object(setup2, "send", side_effect=fake_send2):
        setup2.apply_team_iterations()

    statuses2 = {r["step"]: r["status"] for r in setup2.results}
    assert statuses2["team.iterations"] == "ok", f"idempotente deve retornar ok, got {statuses2}"
    assert statuses2["team.iterations.Sprint 1"] == "skip"
    assert statuses2["team.iterations.Sprint 2"] == "skip"
    assert not [c for c in captured2 if c[0] == "PATCH"], "não deve PATCH quando já vinculado"

    # --- caso de erro: nó de iteração ausente
    setup3 = _build_setup(cfg)
    setup3.project_id = "proj-1"
    setup3.team_id = "team-1"

    def fake_get3(url):
        if "teamsettings/iterations" in url:
            return {"value": []}
        if "classificationnodes/iterations/Sprint%201" in url:
            return None
        if "classificationnodes/iterations/Sprint%202" in url:
            return {"identifier": "node-2", "name": "Sprint 2"}
        return {}

    captured3: list[tuple[str, str, object]] = []
    def fake_send3(method, url, body=None, content_type="application/json"):
        captured3.append((method, url, body))
        if method == "PATCH":
            return {"ok": True}
        return {"id": "ok"}

    with patch.object(setup3, "get", side_effect=fake_get3), \
         patch.object(setup3, "send", side_effect=fake_send3):
        setup3.apply_team_iterations()

    statuses3 = {r["step"]: r["status"] for r in setup3.results}
    assert statuses3["team.iterations.Sprint 1"] == "error"
    assert statuses3["team.iterations"] == "ok"


def test_apply_queries():
    """B-8b (Sprint 1-C): apply_queries cria 5 saved queries em Shared/Agents Squad/.

    Verifica:
    - Opt-out: queries.enabled=false no template não cria nada.
    - Opt-in: 5 queries são POSTadas na pasta correta.
    - WIQL sintaxe inclui o project name e filtros esperados.
    - Idempotência: queries já existentes são skipped.
    """
    cfg = _cfg()
    cfg["queries"]["enabled"] = True
    cfg["squad_tags"] = {"core": "squad-core", "ai": "squad-ai"}

    # --- opt-out: nada é criado
    cfg_off = _cfg()
    cfg_off["queries"]["enabled"] = False
    setup_off = _build_setup(cfg_off)
    captured_off: list[tuple[str, str, object]] = []

    def fake_send_off(method, url, body=None, content_type="application/json"):
        captured_off.append((method, url, body))
        return {"id": "ok"}

    with patch.object(setup_off, "get", return_value={}), \
         patch.object(setup_off, "send", side_effect=fake_send_off):
        setup_off.apply_queries()

    statuses_off = {r["step"]: r["status"] for r in setup_off.results}
    assert statuses_off["queries"] == "skip"
    assert not captured_off, "opt-out não deve fazer nenhuma chamada"

    # --- opt-in: 5 queries criadas
    setup = _build_setup(cfg)
    setup.results = []
    captured: list[tuple[str, str, object]] = []

    def fake_send(method, url, body=None, content_type="application/json"):
        captured.append((method, url, body))
        if "wit/queries/" in url and method == "POST":
            return {"id": "q-1", "name": (body or {}).get("name")}
        return {"id": "ok"}

    def fake_get(url):
        if "_apis/projects/" in url and ("api-version" in url):
            return {"id": "proj-1"}
        if url.endswith("/_apis/wit/queries?api-version=7.1"):
            return {"value": []}
        return {}

    with patch.object(setup, "get", side_effect=fake_get), \
         patch.object(setup, "send", side_effect=fake_send):
        setup.apply_queries()

    posts = [c for c in captured if c[0] == "POST"]
    # 1 POST para folder + 5 POSTs para queries
    assert len(posts) == 6, f"esperado 6 POSTs (1 folder + 5 queries), got {len(posts)}"
    query_posts = [c for c in posts if "isFolder" in (c[2] or {}) and (c[2] or {}).get("isFolder") is False]
    assert len(query_posts) == 5, f"esperado 5 query POSTs, got {len(query_posts)}"

    # Verifica folder path em todos
    for c in posts:
        assert "Shared/Agents%20Squad" in c[1] or "Shared/Agents Squad" in c[1] or c[2] == {"name": "Agents Squad", "isFolder": True}

    # Verifica WIQL contém project name e os 5 nomes esperados
    query_names = {(c[2] or {}).get("name") for c in query_posts}
    assert query_names == {"Active", "WIP by State", "Missing Iteration", "Critical Bugs", "G6 Governance — last sprint"}
    for c in query_posts:
        wiql = (c[2] or {}).get("wiql", "")
        assert "[System.TeamProject] = 'Arthemis'" in wiql, f"WIQL sem project: {wiql}"
        assert wiql.startswith("SELECT [System.Id]") or wiql.startswith("SELECT [System.Id],")
        assert "WHERE" in wiql
        assert "WorkItems" in wiql

    # --- idempotência: queries já existem
    setup2 = _build_setup(cfg)
    setup2.results = []

    def fake_get2(url):
        if "_apis/projects/" in url and ("api-version" in url):
            return {"id": "proj-1"}
        if url.endswith("/_apis/wit/queries?api-version=7.1"):
            return {"value": [
                {"name": "Active"}, {"name": "WIP by State"},
                {"name": "Missing Iteration"}, {"name": "Critical Bugs"},
                {"name": "G6 Governance — last sprint"},
            ]}
        return {}

    captured2: list[tuple[str, str, object]] = []
    def fake_send2(method, url, body=None, content_type="application/json"):
        captured2.append((method, url, body))
        return {"id": "ok"}

    with patch.object(setup2, "get", side_effect=fake_get2), \
         patch.object(setup2, "send", side_effect=fake_send2):
        setup2.apply_queries()

    query_posts2 = [c for c in captured2 if c[0] == "POST" and (c[2] or {}).get("isFolder") is False]
    assert len(query_posts2) == 0, "idempotente: nenhuma query recriada"


def test_apply_delivery_plans():
    """B-8c (Sprint 1-C): apply_delivery_plans é opt-in (default OFF) e idempotente.

    Verifica:
    - Default OFF: delivery_plan.enabled=false → skip sem chamadas.
    - Opt-in: cria plan com nome do config e mapeamento de team.
    - Idempotência: plan já existente → skip.
    """
    cfg = _cfg()
    assert cfg["delivery_plan"]["enabled"] is False, "default deve ser OFF"

    # --- default OFF
    setup_off = _build_setup(cfg)
    captured_off: list[tuple[str, str, object]] = []

    def fake_send_off(method, url, body=None, content_type="application/json"):
        captured_off.append((method, url, body))
        return {"id": "p-1"}

    with patch.object(setup_off, "get", return_value={}), \
         patch.object(setup_off, "send", side_effect=fake_send_off):
        setup_off.apply_delivery_plans()

    statuses_off = {r["step"]: r["status"] for r in setup_off.results}
    assert statuses_off["delivery_plan"] == "skip"
    assert not captured_off, "OFF não deve fazer chamadas"

    # --- opt-in + criação
    cfg_on = _cfg()
    cfg_on["delivery_plan"]["enabled"] = True
    cfg_on["delivery_plan"]["name"] = "Agents Squad — Delivery"
    setup = _build_setup(cfg_on)
    setup.project_id = "proj-1"
    setup.team_id = "team-1"
    setup.results = []
    captured: list[tuple[str, str, object]] = []

    def fake_send(method, url, body=None, content_type="application/json"):
        captured.append((method, url, body))
        if method == "POST":
            return {"id": "plan-99", "name": (body or {}).get("name")}
        return {"id": "ok"}

    def fake_get(url):
        if "work/plans" in url:
            return {"value": []}
        return {}

    with patch.object(setup, "get", side_effect=fake_get), \
         patch.object(setup, "send", side_effect=fake_send):
        setup.apply_delivery_plans()

    posts = [c for c in captured if c[0] == "POST"]
    assert len(posts) == 1, f"esperado 1 POST, got {len(posts)}"
    assert posts[0][2]["name"] == "Agents Squad — Delivery"
    assert posts[0][2]["type"] == "deliveryTimelineView"
    mappings = posts[0][2]["properties"]["teamBacklogMappings"]
    assert mappings[0]["teamId"] == "team-1"
    assert mappings[0]["categoryReferenceName"] == "Microsoft.RequirementCategory"
    statuses = {r["step"]: r["status"] for r in setup.results}
    assert statuses["delivery_plan"] == "ok"
    assert setup.results[-1]["detail"]["id"] == "plan-99"

    # --- idempotente: plan já existe
    setup2 = _build_setup(cfg_on)
    setup2.project_id = "proj-1"
    setup2.team_id = "team-1"
    setup2.results = []
    captured2: list[tuple[str, str, object]] = []

    def fake_get2(url):
        if "work/plans" in url:
            return {"value": [{"id": "plan-99", "name": "Agents Squad — Delivery"}]}
        return {}

    def fake_send2(method, url, body=None, content_type="application/json"):
        captured2.append((method, url, body))
        return {"id": "x"}

    with patch.object(setup2, "get", side_effect=fake_get2), \
         patch.object(setup2, "send", side_effect=fake_send2):
        setup2.apply_delivery_plans()

    statuses2 = {r["step"]: r["status"] for r in setup2.results}
    assert statuses2["delivery_plan"] == "skip"
    assert not [c for c in captured2 if c[0] == "POST"], "idempotente não deve recriar"


def test_apply_wiki():
    """B-8d (Sprint 1-C): apply_wiki é opt-in (default OFF) e cria 5 pages.

    Verifica:
    - Default OFF: wiki.enabled=false → skip.
    - Opt-in: cria project wiki + 5 pages com slugs esperados.
    - Idempotência: wiki já existente → skip.
    """
    cfg = _cfg()
    assert cfg["wiki"]["enabled"] is False, "default deve ser OFF"
    assert len(cfg["wiki"]["pages"]) == 5

    # --- default OFF
    setup_off = _build_setup(cfg)
    captured_off: list[tuple[str, str, object]] = []
    with patch.object(setup_off, "get", return_value={}), \
         patch.object(setup_off, "send", side_effect=lambda m, u, b=None, ct="application/json": captured_off.append((m, u, b)) or {"id": "x"}):
        setup_off.apply_wiki()
    assert {r["step"]: r["status"] for r in setup_off.results}["wiki"] == "skip"
    assert not captured_off

    # --- opt-in: cria wiki + 5 pages
    cfg_on = _cfg()
    cfg_on["wiki"]["enabled"] = True
    setup = _build_setup(cfg_on)
    setup.project_id = "proj-1"
    setup.results = []
    captured: list[tuple[str, str, object]] = []

    def fake_send(method, url, body=None, content_type="application/json"):
        captured.append((method, url, body))
        if "wikis?api-version" in url and method == "POST":
            return {"id": "wiki-77", "type": "projectWiki"}
        if "wiki/wikis/wiki-77/pages" in url:
            return {"id": "page-1", "path": (url.split("path=")[-1].split("&")[0] if "path=" in url else "/")}
        return {"id": "ok"}

    def fake_get(url):
        if "wiki/wikis" in url:
            return {"value": []}
        return {}

    with patch.object(setup, "get", side_effect=fake_get), \
         patch.object(setup, "send", side_effect=fake_send):
        setup.apply_wiki()

    page_creates = [c for c in captured if c[0] == "PUT" and "wiki/wikis/wiki-77/pages" in c[1]]
    assert len(page_creates) == 5, f"esperado 5 PUTs para pages, got {len(page_creates)}"
    page_slugs = []
    for c in page_creates:
        assert "path=" in c[1]
        path = c[1].split("path=")[-1].split("&")[0]
        page_slugs.append(path)
    assert set(page_slugs) == {"/home", "/architecture", "/onboarding", "/runbooks", "/compliance"}

    # POST do wiki root
    wiki_posts = [c for c in captured if c[0] == "POST" and "wiki/wikis" in c[1]]
    assert len(wiki_posts) == 1
    assert wiki_posts[0][2]["type"] == "projectWiki"
    assert wiki_posts[0][2]["projectId"] == "proj-1"

    # --- idempotente
    setup2 = _build_setup(cfg_on)
    setup2.project_id = "proj-1"
    setup2.results = []

    def fake_get2(url):
        if "wiki/wikis" in url:
            return {"value": [{"id": "wiki-77", "type": "projectWiki", "projectId": "proj-1"}]}
        return {}

    captured2: list[tuple[str, str, object]] = []
    with patch.object(setup2, "get", side_effect=fake_get2), \
         patch.object(setup2, "send", side_effect=lambda m, u, b=None, ct="application/json": captured2.append((m, u, b)) or {"id": "x"}):
        setup2.apply_wiki()
    assert {r["step"]: r["status"] for r in setup2.results}["wiki"] == "skip"
    assert not [c for c in captured2 if c[0] == "POST"], "idempotente não deve recriar wiki"
    assert not [c for c in captured2 if c[0] == "PUT"], "idempotente não deve recriar pages"


def test_apply_dashboards():
    """B-8e (Sprint 1-C): apply_dashboards é opt-in (default OFF) e cria 2 dashboards.

    Verifica:
    - Default OFF: dashboards.enabled=false → skip.
    - Opt-in: cria 1 team dashboard + 1 project dashboard.
    - Idempotência: dashboards já existentes → skip.
    """
    cfg = _cfg()
    assert cfg["dashboards"]["enabled"] is False, "default deve ser OFF"

    # --- default OFF
    setup_off = _build_setup(cfg)
    captured_off: list[tuple[str, str, object]] = []
    with patch.object(setup_off, "get", return_value={}), \
         patch.object(setup_off, "send", side_effect=lambda m, u, b=None, ct="application/json": captured_off.append((m, u, b)) or {"id": "x"}):
        setup_off.apply_dashboards()
    assert {r["step"]: r["status"] for r in setup_off.results}["dashboards"] == "skip"
    assert not captured_off

    # --- opt-in: 2 dashboards
    cfg_on = _cfg()
    cfg_on["dashboards"]["enabled"] = True
    setup = _build_setup(cfg_on)
    setup.project_id = "proj-1"
    setup.team_id = "team-1"
    setup.results = []
    captured: list[tuple[str, str, object]] = []

    def fake_send(method, url, body=None, content_type="application/json"):
        captured.append((method, url, body))
        if method == "POST":
            return {"id": "d-1", "name": (body or {}).get("name")}
        return {"id": "ok"}

    with patch.object(setup, "get", return_value={"value": []}), \
         patch.object(setup, "send", side_effect=fake_send):
        setup.apply_dashboards()

    posts = [c for c in captured if c[0] == "POST"]
    assert len(posts) == 2, f"esperado 2 POSTs (1 team + 1 project), got {len(posts)}"
    scopes = {c[2].get("dashboardScope") for c in posts}
    assert scopes == {"project_Team", "project"}, f"scopes esperados não cobertos: {scopes}"
    team_post = next(c for c in posts if c[2].get("dashboardScope") == "project_Team")
    proj_post = next(c for c in posts if c[2].get("dashboardScope") == "project")
    assert "/team-1/" in team_post[1], f"URL team deve incluir team_id: {team_post[1]}"
    assert "/team-1/" not in proj_post[1], f"URL project não deve incluir team_id: {proj_post[1]}"

    # --- idempotente
    setup2 = _build_setup(cfg_on)
    setup2.project_id = "proj-1"
    setup2.team_id = "team-1"
    setup2.results = []

    def fake_get2(url):
        if "team-1/_apis/dashboard/dashboards" in url:
            return {"value": [{"id": "d-1", "name": "Agents Squad — Team Overview"}]}
        if "_apis/dashboard/dashboards" in url:
            return {"value": [{"id": "d-2", "name": "Agents Squad — Project Overview"}]}
        return {}

    captured2: list[tuple[str, str, object]] = []
    with patch.object(setup2, "get", side_effect=fake_get2), \
         patch.object(setup2, "send", side_effect=lambda m, u, b=None, ct="application/json": captured2.append((m, u, b)) or {"id": "x"}):
        setup2.apply_dashboards()

    statuses2 = {r["step"]: r["status"] for r in setup2.results}
    assert all(s == "skip" for s in statuses2.values()), f"todos devem ser skip, got {statuses2}"
    assert not [c for c in captured2 if c[0] == "POST"], "idempotente não deve recriar"


def test_create_team():
    """Novos times são criados via POST /_apis/projects/{projectId}/teams.

    Verifica:
    - Default: sem times configurados → skip.
    - Criação: times novos geram POST com name + description.
    - Idempotência: time já existente → skip.
    """
    cfg = _cfg()
    cfg["teams"] = [
        {"name": "Squad Core", "description": "Backend team"},
        {"name": "Squad AI", "description": "AI team"},
    ]
    setup = _build_setup(cfg)
    setup.project_id = "proj-1"
    setup.results = []
    captured: list[tuple[str, str, object]] = []

    def fake_send(method, url, body=None, content_type="application/json"):
        captured.append((method, url, body))
        if method == "POST":
            return {"id": "team-new", "name": (body or {}).get("name")}
        return {"id": "ok"}

    def fake_get(url):
        if "/teams?" in url:
            return {"value": [{"name": "Squad Core"}]}
        return {}

    with patch.object(setup, "get", side_effect=fake_get), \
         patch.object(setup, "send", side_effect=fake_send):
        setup.create_team()

    posts = [c for c in captured if c[0] == "POST"]
    assert len(posts) == 1, f"esperado 1 POST (Squad AI), got {len(posts)}"
    assert posts[0][2]["name"] == "Squad AI"
    assert posts[0][2]["description"] == "AI team"
    statuses = {r["step"]: r["status"] for r in setup.results}
    assert statuses["teams.Squad Core"] == "skip"
    assert statuses["teams.Squad AI"] == "ok"

    # --- idempotente: ambos já existem
    setup2 = _build_setup(cfg)
    setup2.project_id = "proj-1"
    setup2.results = []

    def fake_get2(url):
        if "/teams?" in url:
            return {"value": [{"name": "Squad Core"}, {"name": "Squad AI"}]}
        return {}

    captured2: list[tuple[str, str, object]] = []
    with patch.object(setup2, "get", side_effect=fake_get2), \
         patch.object(setup2, "send", side_effect=lambda m, u, b=None, ct="application/json": captured2.append((m, u, b)) or {"id": "x"}):
        setup2.create_team()

    statuses2 = {r["step"]: r["status"] for r in setup2.results}
    assert statuses2["teams.Squad Core"] == "skip"
    assert statuses2["teams.Squad AI"] == "skip"
    assert not [c for c in captured2 if c[0] == "POST"], "idempotente não deve POSTar"


def test_apply_swimlanes_not_found_then_wiql():
    """apply_swimlanes documenta 404 na API direta e implementa via WIQL.

    GAP (2026-09-02): POST /_apis/work/boards/{board}/swimlanes retorna
    404 Not Found — Azure DevOps não expõe swimlanes como REST API pública.
    A implementação alternativa grava cada swimlane como saved query em
    Shared/Agents Squad/SWIMLANES/{name} com a WIQL clause do template.

    Verifica:
    - board.swimlanes ausente → skip.
    - board.swimlanes presente → cria folder SWIMLANES + queries por lane.
    - Idempotência: query já existe → skip.
    """
    cfg = _cfg()
    cfg["board"]["swimlanes"] = [
        {"name": "Squad Core", "query": "System.Tags CONTAINS 'squad-core'"},
        {"name": "Without Squad", "query": "NOT (System.Tags CONTAINS 'squad-')"},
    ]
    setup = _build_setup(cfg)
    setup.project_id = "proj-1"
    setup.results = []
    captured: list[tuple[str, str, object]] = []

    def fake_send(method, url, body=None, content_type="application/json"):
        captured.append((method, url, body))
        if method == "POST" and "isFolder" in (body or {}):
            return {"id": "folder-1"}
        if method == "POST" and "wiql" in (body or {}):
            return {"id": "q-1", "name": (body or {}).get("name")}
        return {"id": "ok"}

    def fake_get(url):
        if "_apis/projects/" in url and "api-version" in url:
            return {"id": "proj-1"}
        if "/wit/queries?" in url:
            return {"value": []}
        return {}

    with patch.object(setup, "get", side_effect=fake_get), \
         patch.object(setup, "send", side_effect=fake_send):
        setup.apply_swimlanes()

    posts = [c for c in captured if c[0] == "POST"]
    assert len(posts) == 3, f"esperado 3 POSTs (1 folder + 2 queries), got {len(posts)}"
    query_posts = [c for c in posts if "wiql" in (c[2] or {})]
    assert len(query_posts) == 2
    names = {(c[2] or {}).get("name") for c in query_posts}
    assert names == {"Squad Core", "Without Squad"}
    for c in query_posts:
        wiql = (c[2] or {}).get("wiql", "")
        assert "System.TeamProject" in wiql
        assert wiql.startswith("SELECT [System.Id] FROM WorkItems WHERE")
    statuses = {r["step"]: r["status"] for r in setup.results}
    assert statuses["swimlanes.folder"] == "ok"
    assert statuses["swimlanes.Squad Core"] == "ok"
    assert statuses["swimlanes.Without Squad"] == "ok"

    # --- idempotente: query já existe
    setup2 = _build_setup(cfg)
    setup2.project_id = "proj-1"
    setup2.results = []

    def fake_get2(url):
        if "_apis/projects/" in url and "api-version" in url:
            return {"id": "proj-1"}
        if "/wit/queries?" in url:
            return {"value": [{"name": "Squad Core"}]}
        return {}

    captured2: list[tuple[str, str, object]] = []
    with patch.object(setup2, "get", side_effect=fake_get2), \
         patch.object(setup2, "send", side_effect=lambda m, u, b=None, ct="application/json": captured2.append((m, u, b)) or {"id": "x"}):
        setup2.apply_swimlanes()

    statuses2 = {r["step"]: r["status"] for r in setup2.results}
    assert statuses2["swimlanes.Squad Core"] == "skip"
    assert statuses2["swimlanes.Without Squad"] == "ok"


def test_apply_security_acls():
    """apply_security_acls cria ACLs via POST /_apis/accesscontrollists/{namespaceId}.

    Verifica:
    - security.enabled=false → skip.
    - ACLs criados com namespace, token e entries corretas.
    - ACL já existente → skip (sem overwrite).
    """
    cfg = _cfg()
    cfg["security"] = {
        "enabled": True,
        "acls": [
            {
                "namespace": "Project",
                "namespaceId": "52d94092-0000-0000-0000-000000000000",
                "token": "proj-1",
                "inherit": True,
                "entries": [
                    {"descriptor": "Microsoft.TeamFoundation.Identity\\c1c73e21-a7c4-4f2c-9b4e-0e7a3d3e3e3e", "allow": [6], "deny": []}
                ]
            }
        ]
    }
    setup = _build_setup(cfg)
    setup.project_id = "proj-1"
    setup.results = []
    captured: list[tuple[str, str, object]] = []

    def fake_send(method, url, body=None, content_type="application/json"):
        captured.append((method, url, body))
        if method == "POST":
            return {"count": 1, "value": [{}]}
        return {"id": "ok"}

    def fake_get(url):
        if "accesscontrollists" in url and "token=" in url:
            return {"count": 0, "value": []}
        return {}

    with patch.object(setup, "get", side_effect=fake_get), \
         patch.object(setup, "send", side_effect=fake_send):
        setup.apply_security_acls()

    posts = [c for c in captured if c[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][1].startswith("https://cbvgas.visualstudio.com/_apis/accesscontrollists/")
    assert posts[0][2]["token"] == "proj-1"
    assert posts[0][2]["inherit"] is True
    assert len(posts[0][2]["contributor"]) == 1
    statuses = {r["step"]: r["status"] for r in setup.results}
    assert statuses["security.acls.Project.proj-1"] == "ok"

    # --- ACL já existe: skip
    setup2 = _build_setup(cfg)
    setup2.project_id = "proj-1"
    setup2.results = []

    def fake_get2(url):
        if "accesscontrollists" in url:
            return {"count": 1, "value": [{"descriptor": "Microsoft.TeamFoundation.Identity\\c1c73e21"}]}
        return {}

    captured2: list[tuple[str, str, object]] = []
    with patch.object(setup2, "get", side_effect=fake_get2), \
         patch.object(setup2, "send", side_effect=lambda m, u, b=None, ct="application/json": captured2.append((m, u, b)) or {"id": "x"}):
        setup2.apply_security_acls()

    statuses2 = {r["step"]: r["status"] for r in setup2.results}
    assert statuses2["security.acls.Project.proj-1"] == "skip"
    assert not [c for c in captured2 if c[0] == "POST"], "skip não deve POSTar ACL existente"

    # --- security.enabled=false: skip
    cfg_off = _cfg()
    cfg_off["security"] = {"enabled": False}
    setup3 = _build_setup(cfg_off)
    captured3: list[tuple[str, str, object]] = []
    with patch.object(setup3, "send", side_effect=lambda m, u, b=None, ct="application/json": captured3.append((m, u, b)) or {"id": "x"}):
        setup3.apply_security_acls()
    assert {r["step"]: r["status"] for r in setup3.results}["security.acls"] == "skip"


def test_create_service_connections():
    """create_service_connections cria via POST /_apis/serviceEndpoints.

    Verifica:
    - Nenhum service_connection configurado → skip.
    - Azure RM, GitHub, Docker, Kubernetes geram POSTs com payloads corretos.
    - Idempotência: já existente → skip.
    """
    cfg = _cfg()
    cfg["service_connections"] = [
        {"name": "Azure-ARMTemplate", "type": "azure_rm", "scope": {"subscription_id": "sub-1"}, "auth": {"tenant_id": "t-1", "client_id": "c-1", "client_secret": "s-1"}},
        {"name": "GitHub-Arthemis", "type": "github", "auth": {"token": "ghp_xxx"}},
        {"name": "DockerHub", "type": "docker_registry", "auth": {"docker_registry": "https://index.docker.io/v1/", "username": "user", "password": "pass"}},
        {"name": "AKS-Cluster", "type": "kubernetes", "auth": {"kubeconfig": "YXNkZGFzZA=="}},
    ]
    setup = _build_setup(cfg)
    setup.project_id = "proj-1"
    setup.results = []
    captured: list[tuple[str, str, object]] = []

    def fake_send(method, url, body=None, content_type="application/json"):
        captured.append((method, url, body))
        if method == "POST":
            return {"id": "sc-new", "name": (body or {}).get("name")}
        return {"id": "ok"}

    def fake_get(url):
        if "serviceConnections?" in url:
            return {"value": []}
        return {}

    with patch.object(setup, "get", side_effect=fake_get), \
         patch.object(setup, "send", side_effect=fake_send):
        setup.create_service_connections()

    posts = [c for c in captured if c[0] == "POST"]
    assert len(posts) == 4, f"esperado 4 POSTs, got {len(posts)}"
    types = {(c[2] or {}).get("type") for c in posts}
    assert types == {"Azure Resource Manager", "GitHub", "DockerRegistry", "Kubernetes"}
    names = {(c[2] or {}).get("name") for c in posts}
    assert names == {"Azure-ARMTemplate", "GitHub-Arthemis", "DockerHub", "AKS-Cluster"}
    for c in posts:
        body = c[2] or {}
        assert "authorization" in body
        assert body.get("is_shared") is False
        assert body.get("owner") == "Library"
    statuses = {r["step"]: r["status"] for r in setup.results}
    assert all(v == "ok" for v in statuses.values()), f"todos ok, got {statuses}"

    # --- idempotente: Azure-ARMTemplate já existe
    setup2 = _build_setup(cfg)
    setup2.project_id = "proj-1"
    setup2.results = []

    def fake_get2(url):
        if "serviceConnections?" in url:
            return {"value": [{"name": "Azure-ARMTemplate"}]}
        return {}

    captured2: list[tuple[str, str, object]] = []
    with patch.object(setup2, "get", side_effect=fake_get2), \
         patch.object(setup2, "send", side_effect=lambda m, u, b=None, ct="application/json": captured2.append((m, u, b)) or {"id": "x"}):
        setup2.create_service_connections()

    statuses2 = {r["step"]: r["status"] for r in setup2.results}
    assert statuses2["service_connections.Azure-ARMTemplate"] == "skip"
    assert statuses2["service_connections.GitHub-Arthemis"] == "ok"
    # apenas Azure-ARMTemplate é skip, as outras 3 são criadas
    ok_count = sum(1 for s in statuses2.values() if s == "ok")
    assert ok_count == 3, f"esperado 3 ok (novos), got {ok_count}"
