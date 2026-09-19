"""
R3 Work Item Compatibility & Zero-Destruction Suite (Section 35).

Tests backward compatibility with legacy flat layouts and prefixes (FEAT-, US-, TK-),
non-destructive read semantics (zero in-place overwrites or forced moves),
logical alias normalization, resolution of canonical child to legacy parent,
and continued support for operational work item types (BUG, SPIKE, INCIDENT, RELEASE, EVOLUTION).
"""

import hashlib
from pathlib import Path
import shutil
import sys
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_squad import AgentSquad
from scripts.domain.work_items import WorkHierarchy, WorkItemKind
from scripts.runtime.work_items import (
    CanonicalIdService,
    HierarchyContextResolver,
    WorkItemPathResolver,
    validate_parent_child,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def r3_compat_env():
    project_id = "test-r3-compat"
    work_dir = ROOT / "work" / project_id
    lock_dir = ROOT / ".locks" / project_id

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)

    cleanup()
    squad = AgentSquad(ROOT, project_name=project_id)
    yield squad, project_id, work_dir
    cleanup()


class TestR3WorkItemCompatibility:
    """Suíte canônica de compatibilidade e não-destrutividade (R3 - Seção 35)."""

    def test_legacy_feat_fixture_readable_and_resolvable(self, r3_compat_env):
        """Fixture legada FEAT existente em layout plano é perfeitamente legível."""
        _, project_id, work_dir = r3_compat_env
        resolver = WorkItemPathResolver(ROOT, project_id)

        legacy_feat_dir = work_dir / "FEAT-100"
        legacy_feat_dir.mkdir(parents=True, exist_ok=True)
        status_content = "id: FEAT-100\ntype: feature\ntitle: Legacy Feature Spec\n"
        (legacy_feat_dir / "status.yaml").write_text(status_content, encoding="utf-8")

        # Resolução por nome legado original
        resolved = resolver.resolve_item_path("FEAT-100")
        assert resolved == legacy_feat_dir
        with open(resolved / "status.yaml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data["id"] == "FEAT-100"
        assert data["title"] == "Legacy Feature Spec"

        # Resolução por nome canônico equivalente
        resolved_canon = resolver.resolve_item_path("FEATURE-100")
        assert resolved_canon == legacy_feat_dir

    def test_legacy_us_fixture_readable_and_resolvable(self, r3_compat_env):
        """Fixture legada US existente em layout plano é perfeitamente legível."""
        _, project_id, work_dir = r3_compat_env
        resolver = WorkItemPathResolver(ROOT, project_id)

        legacy_us_dir = work_dir / "US-042"
        legacy_us_dir.mkdir(parents=True, exist_ok=True)
        status_content = "id: US-042\ntype: story\nstory_points: 5\n"
        (legacy_us_dir / "status.yaml").write_text(status_content, encoding="utf-8")

        resolved = resolver.resolve_item_path("US-042")
        assert resolved == legacy_us_dir

        resolved_canon = resolver.resolve_item_path("STORY-042")
        assert resolved_canon == legacy_us_dir

    def test_legacy_flat_task_readable_and_resolvable(self, r3_compat_env):
        """Task plana legada existente é perfeitamente legível."""
        _, project_id, work_dir = r3_compat_env
        resolver = WorkItemPathResolver(ROOT, project_id)

        legacy_task_dir = work_dir / "TK-0005"
        legacy_task_dir.mkdir(parents=True, exist_ok=True)
        (legacy_task_dir / "status.yaml").write_text("id: TK-0005\ntype: task\n", encoding="utf-8")

        resolved = resolver.resolve_item_path("TK-0005")
        assert resolved == legacy_task_dir
        assert resolver.resolve_item_path("TASK-0005") == legacy_task_dir

    def test_legacy_reads_are_strictly_non_destructive(self, r3_compat_env):
        """Leitura e resolução de itens legados não regravam nem alteram timestamps ou hashes."""
        _, project_id, work_dir = r3_compat_env
        resolver = WorkItemPathResolver(ROOT, project_id)
        h_resolver = HierarchyContextResolver(resolver)

        legacy_dir = work_dir / "US-IMMUTABLE-01"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        status_file = legacy_dir / "status.yaml"
        original_content = "id: US-IMMUTABLE-01\ntype: story\ncustom_field: preserve_me\n"
        status_file.write_text(original_content, encoding="utf-8")

        orig_hash = hashlib.sha256(status_file.read_bytes()).hexdigest()
        orig_mtime = status_file.stat().st_mtime_ns

        # Múltiplas leituras e resoluções
        _ = resolver.resolve_item_path("US-IMMUTABLE-01")
        _ = resolver.resolve_item_path("STORY-IMMUTABLE-01")
        _ = h_resolver.compile_hierarchy_context(legacy_dir)

        # Verificação de integridade estrita
        curr_hash = hashlib.sha256(status_file.read_bytes()).hexdigest()
        curr_mtime = status_file.stat().st_mtime_ns

        assert curr_hash == orig_hash
        assert curr_mtime == orig_mtime
        assert status_file.read_text(encoding="utf-8") == original_content

    def test_logical_alias_normalization(self):
        """Aliases legados são normalizados logicamente sem alterar a representação persistida."""
        assert CanonicalIdService.normalize("FEAT-999") == "FEATURE-999"
        assert CanonicalIdService.normalize("US-123") == "STORY-123"
        assert CanonicalIdService.normalize("TK-4567") == "TASK-4567"
        assert CanonicalIdService.normalize("REL-005") == "RELEASE-005"
        assert CanonicalIdService.normalize("EVOL-003") == "EVOLUTION-003"

    def test_canonical_child_resolves_legacy_parent(self, r3_compat_env):
        """Novo filho canônico consegue referenciar e resolver pai legado existente."""
        _, project_id, work_dir = r3_compat_env
        resolver = WorkItemPathResolver(ROOT, project_id)
        h_resolver = HierarchyContextResolver(resolver)

        # Pai legado FEAT plano
        feat_dir = work_dir / "FEAT-100"
        feat_dir.mkdir(parents=True, exist_ok=True)
        (feat_dir / "status.yaml").write_text("id: FEAT-100\ntype: feature\n", encoding="utf-8")

        # Filho Story canônico apontando para parent_id FEAT-100
        story_path = resolver.construct_canonical_path("STORY-200", WorkItemKind.STORY, parent_id="FEAT-100")
        story_path.mkdir(parents=True, exist_ok=True)
        (story_path / "status.yaml").write_text(
            "id: STORY-200\ntype: story\nparent_id: FEAT-100\nstory_points: 3\n",
            encoding="utf-8"
        )

        parent_info = h_resolver.get_parent(story_path)
        assert parent_info is not None
        assert parent_info["id"] == "FEAT-100"
        assert parent_info["path"] == feat_dir

    def test_zero_mass_automatic_migration(self, r3_compat_env):
        """Zero migração em massa automática: itens legados continuam em seus locais sem realocação forçada."""
        _, project_id, work_dir = r3_compat_env
        resolver = WorkItemPathResolver(ROOT, project_id)

        legacy_items = ["FEAT-001", "US-001", "TK-0001"]
        for item_id in legacy_items:
            item_dir = work_dir / item_id
            item_dir.mkdir(parents=True, exist_ok=True)
            (item_dir / "status.yaml").write_text(f"id: {item_id}\n", encoding="utf-8")

        all_items = resolver.find_all_work_items()
        all_item_names = [p.name for p in all_items]

        # Todos continuam em suas pastas legadas planas
        for item_id in legacy_items:
            assert item_id in all_item_names
            assert (work_dir / item_id).is_dir()
            assert not (work_dir / "features" / item_id).exists()
            assert not (work_dir / "stories" / item_id).exists()

    def test_operational_types_still_supported(self):
        """Tipos operacionais especializados continuam suportados e válidos."""
        operational_types = [
            (WorkItemKind.BUG, "BUG-001"),
            (WorkItemKind.SPIKE, "SPIKE-001"),
            (WorkItemKind.INCIDENT, "INCIDENT-001"),
            (WorkItemKind.RELEASE, "RELEASE-001"),
            (WorkItemKind.PROJECT_SETUP, "SETUP-001"),
        ]

        for kind, item_id in operational_types:
            assert CanonicalIdService.validate(item_id)
            assert CanonicalIdService.infer_kind(item_id) == kind

        # Hierarquia de tipos operacionais
        validate_parent_child(WorkItemKind.FEATURE, WorkItemKind.BUG)
        validate_parent_child(WorkItemKind.STORY, WorkItemKind.BUG)
        validate_parent_child(WorkItemKind.EPIC, WorkItemKind.SPIKE)
        validate_parent_child(WorkItemKind.FEATURE, WorkItemKind.SPIKE)
        validate_parent_child(None, WorkItemKind.INCIDENT)
        validate_parent_child(None, WorkItemKind.RELEASE)
        validate_parent_child(None, WorkItemKind.PROJECT_SETUP)
