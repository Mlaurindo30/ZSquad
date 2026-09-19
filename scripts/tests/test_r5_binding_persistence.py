"""Canonical Tests for R5 SQLite Binding Persistence and Revision History.

Validates:
- DDL initialization in SQLite (tables project_bindings, project_binding_history, and indices)
- PRAGMA enforcement (foreign_keys = ON, busy_timeout = 5000, WAL mode for disk databases)
- SHA-256 deterministic fingerprint calculation
- Idempotency check: identical record yields no-op (changed=False, revision remains constant)
- Monotonic revision incrementation on state or metadata mutation (revision = current + 1)
- Append-only immutable audit trail in project_binding_history
- Querying bindings: get_binding, list_bindings, get_history
- Cascade deletion of history upon binding removal (PRAGMA foreign_keys)
- SEC-R1-01 credential stripping from URLs and sanitized_dict representation
"""

from pathlib import Path
import tempfile
import pytest

from scripts.runtime.delivery.repository import (
    BindingHistoryRecord,
    ProjectBindingRecord,
    SqliteBindingRepository,
    compute_binding_fingerprint,
    sanitize_credentials,
)


@pytest.fixture
def memory_repo():
    """Provides an in-memory SqliteBindingRepository."""
    repo = SqliteBindingRepository(":memory:")
    yield repo
    repo.close()


@pytest.fixture
def disk_repo():
    """Provides a file-backed SqliteBindingRepository inside a temporary directory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "squad.db"
        repo = SqliteBindingRepository(db_path)
        yield repo
        repo.close()


def test_schema_bootstrap_and_indices(memory_repo):
    """Verifies that DDL tables and indices exist after initialization."""
    with memory_repo.connection() as conn:
        cursor = conn.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index');")
        objects = {row[0]: row[1] for row in cursor.fetchall()}

    assert "project_bindings" in objects
    assert "project_binding_history" in objects
    assert "idx_project_bindings_status" in objects
    assert "idx_project_bindings_backend" in objects
    assert "idx_binding_history_project" in objects
    assert "idx_binding_history_created" in objects


def test_pragmas_enforcement_disk(disk_repo):
    """Verifies that WAL mode, foreign keys, and busy timeout are configured on disk databases."""
    with disk_repo.connection() as conn:
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]

    assert fk == 1
    assert journal.lower() == "wal"
    assert timeout == 5000


def test_compute_binding_fingerprint_deterministic():
    """Verifies deterministic SHA-256 fingerprint generation regardless of dictionary key ordering."""
    data1 = {
        "project_id": "auth-service",
        "project_root": "C:/repos/auth-service",
        "display_name": "Auth Service",
        "delivery_backend_kind": "AZURE_DEVOPS",
        "delivery_binding_ref": "https://dev.azure.com/org/proj",
        "organization_url": "https://dev.azure.com/org",
        "team_project_name": "Enterprise-Core",
        "repository_name": "auth-service",
        "assigned_team_name": "Auth-Squad",
        "area_path": "Enterprise-Core\\Security\\Auth",
        "iteration_path": "Enterprise-Core\\2026-Q3",
    }
    data2 = dict(reversed(list(data1.items())))

    fp1 = compute_binding_fingerprint(data1)
    fp2 = compute_binding_fingerprint(data2)

    assert len(fp1) == 64
    assert fp1 == fp2


def test_upsert_binding_initial_insert(memory_repo):
    """Verifies initial insertion of a ProjectBindingRecord at revision 1 with history recording."""
    record = ProjectBindingRecord(
        project_id="catalog-service",
        project_root="/repos/catalog-service",
        display_name="Catalog Service",
        delivery_backend_kind="LOCAL_ONLY",
        delivery_binding_ref="local://catalog-service",
        binding_status="COMPLETE",
    )

    persisted, changed = memory_repo.upsert_binding(
        record=record,
        changed_by="architect",
        action="INIT",
        details={"reason": "First registration"},
    )

    assert changed is True
    assert persisted.revision == 1
    assert persisted.created_at is not None
    assert persisted.updated_at is not None
    assert persisted.fingerprint is not None

    # Verify query returns matching record
    fetched = memory_repo.get_binding("catalog-service")
    assert fetched is not None
    assert fetched.project_id == "catalog-service"
    assert fetched.revision == 1
    assert fetched.fingerprint == persisted.fingerprint

    # Verify history recorded exactly one entry
    history = memory_repo.get_history("catalog-service")
    assert len(history) == 1
    assert history[0].revision == 1
    assert history[0].action == "INIT"
    assert history[0].from_status is None
    assert history[0].to_status == "COMPLETE"
    assert history[0].changed_by == "architect"
    assert history[0].details.get("reason") == "First registration"


def test_upsert_binding_idempotency(memory_repo):
    """Verifies that saving an identical record is a no-op (changed=False, revision unchanged)."""
    record = ProjectBindingRecord(
        project_id="checkout-service",
        project_root="/repos/checkout-service",
        display_name="Checkout Service",
        delivery_backend_kind="LOCAL_ONLY",
        delivery_binding_ref="local://checkout-service",
        binding_status="COMPLETE",
    )

    # First upsert
    rec1, changed1 = memory_repo.upsert_binding(record, changed_by="lead", action="CREATE")
    assert changed1 is True
    assert rec1.revision == 1

    # Second upsert with identical content
    rec2, changed2 = memory_repo.upsert_binding(rec1, changed_by="lead", action="RE-RUN")
    assert changed2 is False
    assert rec2.revision == 1
    assert rec2.fingerprint == rec1.fingerprint

    # History must NOT have extra entries
    history = memory_repo.get_history("checkout-service")
    assert len(history) == 1


def test_upsert_binding_monotonic_revision_increment(memory_repo):
    """Verifies that mutating binding attributes strictly increments revision (rev 1 -> rev 2 -> rev 3)."""
    initial = ProjectBindingRecord(
        project_id="order-service",
        project_root="/repos/order-service",
        display_name="Order Service",
        delivery_backend_kind="LOCAL_ONLY",
        delivery_binding_ref="local://order-service",
        binding_status="COMPLETE",
    )

    rec1, _ = memory_repo.upsert_binding(initial, changed_by="agent", action="INIT")
    assert rec1.revision == 1

    # Mutate to AZURE_DEVOPS
    updated = ProjectBindingRecord(
        project_id="order-service",
        project_root="/repos/order-service",
        display_name="Order Service",
        delivery_backend_kind="AZURE_DEVOPS",
        delivery_binding_ref="https://dev.azure.com/enterprise/Core",
        binding_status="PARTIAL",
        team_project_name="Core",
        repository_name="order-service",
    )

    rec2, changed2 = memory_repo.upsert_binding(updated, changed_by="devops", action="CONNECT_ADO")
    assert changed2 is True
    assert rec2.revision == 2
    assert rec2.binding_status == "PARTIAL"

    # Mutate status to COMPLETE
    completed = ProjectBindingRecord(
        project_id="order-service",
        project_root="/repos/order-service",
        display_name="Order Service",
        delivery_backend_kind="AZURE_DEVOPS",
        delivery_binding_ref="https://dev.azure.com/enterprise/Core",
        binding_status="COMPLETE",
        team_project_name="Core",
        repository_name="order-service",
    )

    rec3, changed3 = memory_repo.upsert_binding(completed, changed_by="devops", action="RESOLVE_RESOURCES")
    assert changed3 is True
    assert rec3.revision == 3
    assert rec3.binding_status == "COMPLETE"

    # Verify history order (DESC revision)
    history = memory_repo.get_history("order-service")
    assert len(history) == 3
    assert history[0].revision == 3
    assert history[0].from_status == "PARTIAL"
    assert history[0].to_status == "COMPLETE"

    assert history[1].revision == 2
    assert history[1].from_status == "COMPLETE"
    assert history[1].to_status == "PARTIAL"

    assert history[2].revision == 1
    assert history[2].from_status is None
    assert history[2].to_status == "COMPLETE"


def test_list_bindings(memory_repo):
    """Verifies listing multiple registered project bindings ordered by project_id."""
    for pid in ("service-c", "service-a", "service-b"):
        memory_repo.upsert_binding(
            ProjectBindingRecord(
                project_id=pid,
                project_root=f"/repos/{pid}",
                display_name=pid.upper(),
                delivery_backend_kind="LOCAL_ONLY",
                delivery_binding_ref=f"local://{pid}",
                binding_status="COMPLETE",
            )
        )

    all_bindings = memory_repo.list_bindings()
    assert len(all_bindings) == 3
    assert [b.project_id for b in all_bindings] == ["service-a", "service-b", "service-c"]


def test_delete_binding_cascades_history(memory_repo):
    """Verifies that deleting a binding deletes its associated history via SQLite ON DELETE CASCADE."""
    record = ProjectBindingRecord(
        project_id="temp-service",
        project_root="/repos/temp-service",
        display_name="Temporary Service",
        delivery_backend_kind="LOCAL_ONLY",
        delivery_binding_ref="local://temp-service",
        binding_status="COMPLETE",
    )
    memory_repo.upsert_binding(record, action="INIT")
    assert memory_repo.get_binding("temp-service") is not None
    assert len(memory_repo.get_history("temp-service")) == 1

    # Delete
    deleted = memory_repo.delete_binding("temp-service")
    assert deleted is True
    assert memory_repo.get_binding("temp-service") is None
    assert len(memory_repo.get_history("temp-service")) == 0


def test_sanitize_credentials_removes_inline_tokens():
    """Verifies SEC-R1-01 stripping of embedded user/passwords or PATs from URLs."""
    raw_url = "https://my-secret-pat@dev.azure.com/enterprise-org"
    sanitized = sanitize_credentials(raw_url)
    assert sanitized == "https://dev.azure.com/enterprise-org"
    assert "my-secret-pat" not in sanitized

    raw_user_pass = "https://user:password123@dev.azure.com/enterprise-org"
    sanitized_up = sanitize_credentials(raw_user_pass)
    assert sanitized_up == "https://dev.azure.com/enterprise-org"
    assert "password123" not in sanitized_up


def test_project_binding_record_sanitized_dict():
    """Verifies sanitized_dict representation redacts credentials in organization_url and delivery_binding_ref."""
    record = ProjectBindingRecord(
        project_id="secure-service",
        project_root="/repos/secure-service",
        display_name="Secure Service",
        delivery_backend_kind="AZURE_DEVOPS",
        delivery_binding_ref="https://secret-token@dev.azure.com/enterprise-org/Core",
        binding_status="COMPLETE",
        organization_url="https://secret-token@dev.azure.com/enterprise-org",
    )
    sanitized = record.sanitized_dict()

    assert "secret-token" not in sanitized["organization_url"]
    assert "secret-token" not in sanitized["delivery_binding_ref"]
    assert sanitized["organization_url"] == "https://dev.azure.com/enterprise-org"
