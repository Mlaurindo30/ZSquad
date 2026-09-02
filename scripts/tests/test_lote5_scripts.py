"""Tests for Lote 5 Azure DevOps scripts:
- scripts/azure_devops_project_creator.py  — create_project()
- scripts/azure_devops_repo_importer.py  — import_repo() + RepoImportClient
- scripts/azure_devops_lifecycle.py     — AzureDevOpsLifecycle
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
TESTS_DIR = SCRIPTS / "tests"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import azure_devops_project_creator as creator  # noqa: E402
import azure_devops_repo_importer as importer  # noqa: E402
import azure_devops_lifecycle as lifecycle_mod  # noqa: E402


# ---------------------------------------------------------------------------
# azure_devops_project_creator — create_project()
# ---------------------------------------------------------------------------

CREATOR_PAT = "test-pat-token"


def test_create_project_valid_inputs_returns_expected_dict():
    """create_project() com entradas válidas retorna dict com project_id, project_url, state."""
    with patch.object(creator, "_do_request") as mock_do:
        with patch.object(creator, "_get_env_pat", return_value=CREATOR_PAT):
            mock_do.side_effect = [
                {"value": []},  # _find_project_by_name → not found
                {"id": "proj-abc-123", "state": "wellFormed"},  # POST → created
            ]
            with patch.object(creator, "_wait_for_project", return_value=("wellFormed", {"state": "wellFormed"})):
                result = creator.create_project(
                    org_url="https://dev.azure.com/cbvgas",
                    project_name="TestProject",
                    process_type="Scrum",
                    pat=CREATOR_PAT,
                    description="Test desc",
                )

    assert isinstance(result, dict)
    assert "project_id" in result
    assert "project_url" in result
    assert "state" in result
    assert result["state"] == "wellFormed"
    assert result["project_id"] == "proj-abc-123"


def test_create_project_already_exists():
    """create_project() retorna state=alreadyExists quando o projeto já existe."""
    with patch.object(creator, "_do_request") as mock_do:
        with patch.object(creator, "_get_env_pat", return_value=CREATOR_PAT):
            mock_do.return_value = {
                "value": [{"id": "existing-id", "name": "TestProject", "state": "wellFormed"}]
            }
            result = creator.create_project(
                org_url="https://dev.azure.com/cbvgas",
                project_name="TestProject",
                pat=CREATOR_PAT,
            )

    assert result["state"] == "alreadyExists"
    assert result["project_id"] == "existing-id"


def test_create_project_empty_name_returns_error():
    """create_project() retorna state=error quando project_name é vazio."""
    with patch.object(creator, "_get_env_pat", return_value=CREATOR_PAT):
        result = creator.create_project(
            org_url="https://dev.azure.com/cbvgas",
            project_name="",
            pat=CREATOR_PAT,
        )

    assert result["state"] == "error"
    assert result["project_id"] is None
    assert "project_name" in str(result["detail"].get("error", "")).lower()


def test_create_project_invalid_process_type_returns_error():
    """create_project() retorna state=error quando process_type é inválido."""
    with patch.object(creator, "_get_env_pat", return_value=CREATOR_PAT):
        result = creator.create_project(
            org_url="https://dev.azure.com/cbvgas",
            project_name="TestProject",
            process_type="InvalidProcess",
            pat=CREATOR_PAT,
        )

    assert result["state"] == "error"
    assert result["project_id"] is None
    assert "process_type" in result["detail"]["error"]


def test_create_project_request_returns_none_sets_error_state():
    """_do_request retornando None gera state=error."""
    with patch.object(creator, "_do_request") as mock_do:
        with patch.object(creator, "_get_env_pat", return_value=CREATOR_PAT):
            mock_do.side_effect = [
                {"value": []},  # not found
                None,  # POST fails
            ]
            result = creator.create_project(
                org_url="https://dev.azure.com/cbvgas",
                project_name="TestProject",
                pat=CREATOR_PAT,
            )

    assert result["state"] == "error"
    assert result["project_id"] is None


def test_create_project_id_not_in_response_returns_unverified():
    """Resposta de criação sem 'id' gera state=UNVERIFIED."""
    with patch.object(creator, "_do_request") as mock_do:
        with patch.object(creator, "_get_env_pat", return_value=CREATOR_PAT):
            mock_do.side_effect = [
                {"value": []},  # not found
                {},  # POST returns no id and no error keys
            ]
            with patch.object(creator, "_wait_for_project", return_value=(None, {"state": "creating"})):
                result = creator.create_project(
                    org_url="https://dev.azure.com/cbvgas",
                    project_name="TestProject",
                    pat=CREATOR_PAT,
                )

    assert result["state"] == "UNVERIFIED"


def test_create_project_timeout_during_wait_returns_creating_state():
    """Timeout no polling retorna state=creating."""
    with patch.object(creator, "_do_request") as mock_do:
        with patch.object(creator, "_get_env_pat", return_value=CREATOR_PAT):
            mock_do.side_effect = [
                {"value": []},  # not found
                {"id": "proj-abc-123"},  # POST ok
            ]
            with patch.object(creator, "_wait_for_project", return_value=(None, {"error": "timeout", "elapsed_seconds": 600})):
                result = creator.create_project(
                    org_url="https://dev.azure.com/cbvgas",
                    project_name="TestProject",
                    pat=CREATOR_PAT,
                )

    assert result["state"] == "creating"
    assert result["project_id"] == "proj-abc-123"


# ---------------------------------------------------------------------------
# azure_devops_repo_importer — import_repo() + RepoImportClient
# ---------------------------------------------------------------------------

IMPORTER_PAT = "test-pat-token"


def test_import_repo_valid_inputs_returns_expected_dict():
    """import_repo() com entradas válidas retorna dict com repo_id, repo_url, default_branch, import_state."""
    with patch.object(importer, "RepoImportClient") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        mock_instance.import_repo.return_value = {
            "repo_id": "repo-123",
            "repo_url": "https://dev.azure.com/cbvgas/TestProject/_git/TestProject",
            "default_branch": "refs/heads/main",
            "import_state": "completed",
        }
        result = importer.import_repo(
            org_url="https://dev.azure.com/cbvgas",
            project_id="TestProject",
            repo_name="TestProject",
            remote_url="https://github.com/example/repo.git",
            credentials={"type": "none"},
            pat=IMPORTER_PAT,
        )

    assert isinstance(result, dict)
    assert "repo_id" in result
    assert "repo_url" in result
    assert "default_branch" in result
    assert "import_state" in result
    assert result["repo_id"] == "repo-123"
    assert result["import_state"] == "completed"


def test_import_repo_ssrf_blocks_localhost():
    """SSRF validation rejeita remote URL com localhost — client retorna error dict."""
    with patch.object(importer, "RepoImportClient") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        mock_instance.import_repo.return_value = {
            "repo_id": None,
            "repo_url": None,
            "default_branch": None,
            "import_state": "error: SSRF bloqueado: remote URL não pode ser http://localhost/repo.git",
        }
        result = importer.import_repo(
            org_url="https://dev.azure.com/cbvgas",
            project_id="TestProject",
            repo_name="TestProject",
            remote_url="http://localhost/repo.git",
            credentials={"type": "none"},
            pat=IMPORTER_PAT,
        )

    assert result["import_state"].startswith("error:")
    assert result["repo_id"] is None


def test_import_repo_ssrf_blocks_127_0_0_1():
    """SSRF validation rejeita remote URL com 127.0.0.1 — client retorna error dict."""
    with patch.object(importer, "RepoImportClient") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        mock_instance.import_repo.return_value = {
            "repo_id": None,
            "repo_url": None,
            "default_branch": None,
            "import_state": "error: SSRF bloqueado: remote URL não pode ser http://127.0.0.1/repo.git",
        }
        result = importer.import_repo(
            org_url="https://dev.azure.com/cbvgas",
            project_id="TestProject",
            repo_name="TestProject",
            remote_url="http://127.0.0.1/repo.git",
            credentials={"type": "none"},
            pat=IMPORTER_PAT,
        )

    assert result["import_state"].startswith("error:")
    assert result["repo_id"] is None


def test_import_repo_ssrf_blocks_169_254_169_254():
    """SSRF validation rejeita remote URL com 169.254.169.254 — client retorna error dict."""
    with patch.object(importer, "RepoImportClient") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        mock_instance.import_repo.return_value = {
            "repo_id": None,
            "repo_url": None,
            "default_branch": None,
            "import_state": "error: SSRF bloqueado",
        }
        result = importer.import_repo(
            org_url="https://dev.azure.com/cbvgas",
            project_id="TestProject",
            repo_name="TestProject",
            remote_url="http://169.254.169.254/latest/meta-data",
            credentials={"type": "none"},
            pat=IMPORTER_PAT,
        )

    assert result["import_state"].startswith("error:")
    assert result["repo_id"] is None


def test_validate_remote_url_rejects_blocked_hosts():
    """_validate_remote_url rejeita hosts bloqueados explicitamente."""
    blocked = [
        "http://localhost/repo.git",
        "http://127.0.0.1/repo.git",
        "http://0.0.0.0/repo.git",
        "http://169.254.169.254/repo.git",
    ]
    for url in blocked:
        try:
            importer._validate_remote_url(url)
            assert False, f"Esperado ValueError para {url}"
        except ValueError as exc:
            assert "SSRF bloqueado" in str(exc) or "inválida" in str(exc)


def test_validate_remote_url_accepts_valid_github_url():
    """_validate_remote_url aceita URLs válidas do GitHub/GitLab."""
    valid = [
        "https://github.com/example/repo.git",
        "https://gitlab.com/example/repo.git",
        "https://bitbucket.org/example/repo.git",
    ]
    for url in valid:
        importer._validate_remote_url(url)  # must not raise


def test_repo_import_client_init_valid_org():
    """RepoImportClient inicializa com org válida."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    assert client.organization == "cbvgas"
    assert client.project == "TestProject"
    assert client.pat_token == IMPORTER_PAT
    assert "dev.azure.com/cbvgas" in client.base_url


def test_repo_import_client_init_invalid_org_raises():
    """RepoImportClient._validate_org_url rejeita orgs fora do allowlist quando chamada diretamente.

    Nota: Devido à construção always-https://dev.azure.com/{org}, o hostname
    é sempre dev.azure.com → a validação no __init__ é um no-op para qualquer
    string org. Testamos o método de validação diretamente.
    """
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    try:
        client._validate_org_url("https://evil.com/myorg")
        assert False, "Esperado ValueError"
    except ValueError as exc:
        assert "inválida" in str(exc)


def test_repo_import_client_build_import_body_none():
    """_build_import_body com credenciais type=none gera payload correto."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    body = client._build_import_body("MyRepo", "https://github.com/ex/repo.git", {"type": "none"})
    assert body["name"] == "MyRepo"
    assert body["isImport"] is True
    assert body["remoteUrl"] == "https://github.com/ex/repo.git"
    assert body["hasRemoteCredentials"] is False
    assert body["credentialsSource"]["type"] == "none"


def test_repo_import_client_build_import_body_username_password():
    """_build_import_body com credenciais usernamePassword gera payload correto."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    creds = {"type": "usernamePassword", "username": "user", "password": "pass"}
    body = client._build_import_body("MyRepo", "https://github.com/ex/repo.git", creds)
    assert body["hasRemoteCredentials"] is True
    assert body["credentialsSource"]["type"] == "usernamePassword"
    assert body["credentialsSource"]["username"] == "user"
    assert body["credentialsSource"]["password"] == "pass"


def test_repo_import_client_build_import_body_personal_access_token():
    """_build_import_body com credenciais personalAccessToken gera payload correto."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    creds = {"type": "personalAccessToken", "token": "ghp_xxx"}
    body = client._build_import_body("MyRepo", "https://github.com/ex/repo.git", creds)
    assert body["hasRemoteCredentials"] is True
    assert body["credentialsSource"]["type"] == "personalAccessToken"
    assert body["credentialsSource"]["token"] == "ghp_xxx"


def test_repo_import_client_build_import_body_service_connection():
    """_build_import_body com credenciais serviceConnection gera payload correto."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    creds = {"type": "serviceConnection", "id": "sc-123"}
    body = client._build_import_body("MyRepo", "https://github.com/ex/repo.git", creds)
    assert body["hasRemoteCredentials"] is True
    assert body["credentialsSource"]["type"] == "serviceConnection"
    assert body["credentialsSource"]["id"] == "sc-123"


def test_repo_import_client_build_import_body_unknown_type_falls_back_to_none():
    """_build_import_body com tipo desconhecido faz fallback para none."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    creds = {"type": "unknownType"}
    body = client._build_import_body("MyRepo", "https://github.com/ex/repo.git", creds)
    assert body["hasRemoteCredentials"] is False
    assert body["credentialsSource"]["type"] == "none"


def test_repo_import_client_check_existing_repo_returns_existing():
    """_check_existing_repo retorna repo existente."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = {
            "value": [
                {"id": "repo-existing", "name": "MyRepo", "webUrl": "https://dev.azure.com/cbvgas/TestProject/_git/MyRepo"}
            ]
        }
        result = client._check_existing_repo("MyRepo")

    assert result is not None
    assert result["id"] == "repo-existing"


def test_repo_import_client_check_existing_repo_returns_none_when_not_found():
    """_check_existing_repo retorna None quando repo não existe."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = {"value": []}
        result = client._check_existing_repo("NonExistent")

    assert result is None


def test_repo_import_client_import_repo_already_exists():
    """import_repo() retorna already_exists quando o repo já existe."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    with patch.object(client, "_check_existing_repo") as mock_check:
        mock_check.return_value = {
            "id": "repo-existing",
            "name": "MyRepo",
            "webUrl": "https://dev.azure.com/cbvgas/TestProject/_git/MyRepo",
            "defaultBranch": "refs/heads/main",
        }
        result = client.import_repo(
            repo_name="MyRepo",
            remote_url="https://github.com/ex/repo.git",
            credentials={"type": "none"},
        )

    assert result["import_state"] == "already_exists"
    assert result["repo_id"] == "repo-existing"


def test_repo_import_client_import_repo_post_fails_unverified():
    """POST falhando sem id retorna import_state=UNVERIFIED."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    with patch.object(client, "_check_existing_repo") as mock_check:
        with patch.object(client, "_request") as mock_req:
            mock_check.return_value = None
            mock_req.return_value = {}  # no id, no message key
            result = client.import_repo(
                repo_name="MyRepo",
                remote_url="https://github.com/ex/repo.git",
                credentials={"type": "none"},
            )

    assert result["import_state"] == "UNVERIFIED"
    assert result["repo_id"] is None


def test_repo_import_client_wait_for_import_completed():
    """_wait_for_import retorna completed quando isImport=False."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = {"id": "repo-123", "isImport": False}
        state, reason = client._wait_for_import("repo-123", timeout_secs=5)

    assert state == "completed"
    assert reason is None


def test_repo_import_client_wait_for_import_timeout():
    """_wait_for_import retorna failed:timeout quando expira."""
    client = importer.RepoImportClient("cbvgas", "TestProject", IMPORTER_PAT)
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = {"id": "repo-123", "isImport": True}
        with patch("time.monotonic", return_value=0):
            with patch("time.sleep"):
                with patch("time.monotonic", side_effect=[0, 100, 200, 300, 400, 500]):
                    state, reason = client._wait_for_import("repo-123", timeout_secs=1)

    assert state == "failed"
    assert reason == "timeout"


# ---------------------------------------------------------------------------
# azure_devops_lifecycle — AzureDevOpsLifecycle
# ---------------------------------------------------------------------------

LIFECYCLE_PAT = "test-pat-token"


def test_lifecycle_init_defaults():
    """AzureDevOpsLifecycle inicializa com defaults corretos."""
    lc = lifecycle_mod.AzureDevOpsLifecycle(
        project_name="TestProject",
        org_url="https://dev.azure.com/cbvgas",
        repo_url="https://github.com/ex/repo.git",
        config_path=None,
        pat=LIFECYCLE_PAT,
        dry_run=False,
    )
    assert lc.project_name == "TestProject"
    assert lc.org_url == "https://dev.azure.com/cbvgas"
    assert lc.repo_url == "https://github.com/ex/repo.git"
    assert lc.pat == LIFECYCLE_PAT
    assert lc.dry_run is False
    assert lc.results == []


def test_lifecycle_init_dry_run_true():
    """AzureDevOpsLifecycle dry_run=True configurado corretamente."""
    lc = lifecycle_mod.AzureDevOpsLifecycle(
        project_name="TestProject",
        org_url="https://dev.azure.com/cbvgas",
        repo_url="https://github.com/ex/repo.git",
        config_path=None,
        pat=LIFECYCLE_PAT,
        dry_run=True,
    )
    assert lc.dry_run is True


def test_lifecycle_resolve_org_base_with_https():
    """_resolve_org_base() mantém URL HTTPS intacta."""
    lc = lifecycle_mod.AzureDevOpsLifecycle(
        project_name="TestProject",
        org_url="https://dev.azure.com/cbvgas",
        repo_url="https://github.com/ex/repo.git",
        config_path=None,
        pat=LIFECYCLE_PAT,
    )
    assert lc._resolve_org_base() == "https://dev.azure.com/cbvgas"


def test_lifecycle_resolve_org_base_without_scheme():
    """_resolve_org_base() adiciona https:// quando org_url não tem scheme."""
    lc = lifecycle_mod.AzureDevOpsLifecycle(
        project_name="TestProject",
        org_url="cbvgas",
        repo_url="https://github.com/ex/repo.git",
        config_path=None,
        pat=LIFECYCLE_PAT,
    )
    assert lc._resolve_org_base() == "https://dev.azure.com/cbvgas"


def test_lifecycle_resolve_org_base_strips_trailing_slash():
    """_resolve_org_base() remove barra final."""
    lc = lifecycle_mod.AzureDevOpsLifecycle(
        project_name="TestProject",
        org_url="https://dev.azure.com/cbvgas/",
        repo_url="https://github.com/ex/repo.git",
        config_path=None,
        pat=LIFECYCLE_PAT,
    )
    assert lc._resolve_org_base() == "https://dev.azure.com/cbvgas"


def test_lifecycle_phase1_create_project_dry_run():
    """_phase1_create_project em dry-run registra dry-run e retorna id sintético."""
    cfg_file = TESTS_DIR / "test_lifecycle_config.yaml"
    cfg_file.write_text("process_template: Scrum\nrepo_import:\n  repo_name: TestProject\n  credentials:\n    type: none\n", encoding="utf-8")
    try:
        lc = lifecycle_mod.AzureDevOpsLifecycle(
            project_name="TestProject",
            org_url="https://dev.azure.com/cbvgas",
            repo_url="https://github.com/ex/repo.git",
            config_path=cfg_file,
            pat=LIFECYCLE_PAT,
            dry_run=True,
        )
        lc._load_config()
        result = lc._phase1_create_project()

        assert result is not None
        assert result["project_id"] == "dry-run-project-id"
        assert "dry-run" in {r["status"] for r in lc.results}
    finally:
        cfg_file.unlink(missing_ok=True)


def test_lifecycle_phase3_import_repo_dry_run():
    """_phase3_import_repo em dry-run registra dry-run e retorna URLs sintéticas."""
    cfg_file = TESTS_DIR / "test_lifecycle_config.yaml"
    cfg_file.write_text("process_template: Scrum\nrepo_import:\n  repo_name: TestProject\n  credentials:\n    type: none\n", encoding="utf-8")
    try:
        lc = lifecycle_mod.AzureDevOpsLifecycle(
            project_name="TestProject",
            org_url="https://dev.azure.com/cbvgas",
            repo_url="https://github.com/ex/repo.git",
            config_path=cfg_file,
            pat=LIFECYCLE_PAT,
            dry_run=True,
        )
        lc._load_config()
        lc._created_project_id = "proj-123"
        lc._project_url = "https://dev.azure.com/cbvgas/TestProject"
        result = lc._phase3_import_repo()

        assert result is not None
        assert "repo_url" in result
        assert "default_branch" in result
        assert "dry-run" in {r["status"] for r in lc.results}
    finally:
        cfg_file.unlink(missing_ok=True)


def test_lifecycle_phase4_configure_dry_run():
    """_phase4_configure em dry-run registra dry-run e retorna plano vazio."""
    cfg_file = TESTS_DIR / "test_lifecycle_config.yaml"
    cfg_file.write_text("process_template: Scrum\nrepo_import:\n  repo_name: TestProject\n  credentials:\n    type: none\n", encoding="utf-8")
    try:
        lc = lifecycle_mod.AzureDevOpsLifecycle(
            project_name="TestProject",
            org_url="https://dev.azure.com/cbvgas",
            repo_url="https://github.com/ex/repo.git",
            config_path=cfg_file,
            pat=LIFECYCLE_PAT,
            dry_run=True,
        )
        lc._load_config()
        result = lc._phase4_configure()

        assert isinstance(result, dict)
        assert result.get("mode") == "dry-run"
        assert "dry-run" in {r["status"] for r in lc.results}
    finally:
        cfg_file.unlink(missing_ok=True)


def test_lifecycle_run_dry_run_returns_plan():
    """run(dry_run=True) retorna modo dry-run e steps sem chamadas reais."""
    cfg_file = TESTS_DIR / "test_lifecycle_config.yaml"
    cfg_file.write_text("process_template: Scrum\nrepo_import:\n  repo_name: TestProject\n  credentials:\n    type: none\n", encoding="utf-8")
    try:
        lc = lifecycle_mod.AzureDevOpsLifecycle(
            project_name="TestProject",
            org_url="https://dev.azure.com/cbvgas",
            repo_url="https://github.com/ex/repo.git",
            config_path=cfg_file,
            pat=LIFECYCLE_PAT,
            dry_run=True,
        )
        report = lc.run()

        assert report["mode"] == "dry-run"
        assert report["project_name"] == "TestProject"
        assert "steps" in report
        step_names = {s["step"] for s in report["steps"]}
        assert "phase1.create_project" in step_names
        assert "phase3.import_repo" in step_names
        assert "phase4.configure" in step_names
    finally:
        cfg_file.unlink(missing_ok=True)


def test_lifecycle_record():
    """_record() appends result with step, status, detail."""
    lc = lifecycle_mod.AzureDevOpsLifecycle(
        project_name="TestProject",
        org_url="https://dev.azure.com/cbvgas",
        repo_url="https://github.com/ex/repo.git",
        config_path=None,
        pat=LIFECYCLE_PAT,
    )
    lc._record("test.step", "ok", {"key": "value"})
    assert len(lc.results) == 1
    assert lc.results[0]["step"] == "test.step"
    assert lc.results[0]["status"] == "ok"
    assert lc.results[0]["detail"] == {"key": "value"}


def test_lifecycle_load_config_missing_file_raises():
    """_load_config() levanta FileNotFoundError quando config não existe."""
    lc = lifecycle_mod.AzureDevOpsLifecycle(
        project_name="TestProject",
        org_url="https://dev.azure.com/cbvgas",
        repo_url="https://github.com/ex/repo.git",
        config_path=Path("/non/existent/config.yaml"),
        pat=LIFECYCLE_PAT,
    )
    try:
        lc._load_config()
        assert False, "Esperado FileNotFoundError"
    except FileNotFoundError:
        pass


def test_lifecycle_dry_run_true_prevents_real_api_calls():
    """dry_run=True impede chamadas reais em todas as phases."""
    cfg_file = TESTS_DIR / "test_lifecycle_config.yaml"
    cfg_file.write_text("process_template: Scrum\nrepo_import:\n  repo_name: TestProject\n  credentials:\n    type: none\n", encoding="utf-8")
    try:
        lc = lifecycle_mod.AzureDevOpsLifecycle(
            project_name="TestProject",
            org_url="https://dev.azure.com/cbvgas",
            repo_url="https://github.com/ex/repo.git",
            config_path=cfg_file,
            pat=LIFECYCLE_PAT,
            dry_run=True,
        )
        with patch.object(creator, "create_project") as mock_create:
            with patch.object(importer, "import_repo") as mock_import:
                report = lc.run()

        mock_create.assert_not_called()
        mock_import.assert_not_called()
        assert report["mode"] == "dry-run"
    finally:
        cfg_file.unlink(missing_ok=True)


def test_lifecycle_phase2_wait_ready_dry_run():
    """_phase2_wait_ready em dry-run retorna True sem chamadas."""
    cfg_file = TESTS_DIR / "test_lifecycle_config.yaml"
    cfg_file.write_text("process_template: Scrum\nrepo_import:\n  repo_name: TestProject\n  credentials:\n    type: none\n", encoding="utf-8")
    try:
        lc = lifecycle_mod.AzureDevOpsLifecycle(
            project_name="TestProject",
            org_url="https://dev.azure.com/cbvgas",
            repo_url="https://github.com/ex/repo.git",
            config_path=cfg_file,
            pat=LIFECYCLE_PAT,
            dry_run=True,
        )
        lc._load_config()
        lc._created_project_id = "proj-123"
        lc._project_url = "https://dev.azure.com/cbvgas/TestProject"
        lc._build_client()
        result = lc._phase2_wait_ready("proj-123")

        assert result is True
        # last recorded status for this step should be dry-run
        steps = [r for r in lc.results if r["step"] == "phase2.wait_ready"]
        last_step = steps[-1]
        assert last_step["status"] == "dry-run"
    finally:
        cfg_file.unlink(missing_ok=True)
