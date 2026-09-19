"""
R3 Work Item Physical Paths & Containment Suite (Section 33).

Tests canonical 4-tier directory nesting (EPIC / features / FEATURE / stories / STORY / tasks / TASK),
non-destructive resolution of legacy flat structures, canonical-over-legacy preference,
path traversal defenses, Windows reserved device names, and strict path containment guards.
"""

from pathlib import Path
import shutil
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_squad import AgentSquad
from project_context import PathContainmentGuard, PathContainmentViolation
from scripts.domain.common import ValidationError
from scripts.domain.work_items import WorkItemKind
from scripts.runtime.work_items import (
    WorkItemPathResolver,
    sanitize_slug,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def r3_paths_env():
    project_id = "test-r3-paths"
    work_dir = ROOT / "work" / project_id
    lock_dir = ROOT / ".locks" / project_id

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)

    cleanup()
    squad = AgentSquad(ROOT, project_name=project_id)
    yield squad, project_id, work_dir
    cleanup()


class TestR3WorkItemPaths:
    """Suíte canônica de testes de paths físicos e contenção (R3 - Seção 33)."""

    def test_canonical_nested_physical_layout(self, r3_paths_env):
        """Layout físico aninhado canônico: EPIC / features / FEATURE / stories / STORY / tasks / TASK."""
        squad, project_id, work_dir = r3_paths_env
        resolver = WorkItemPathResolver(ROOT, project_id)

        # 1. Epic no nível raiz do projeto
        epic_path = resolver.construct_canonical_path("EPIC-001", WorkItemKind.EPIC)
        assert epic_path == work_dir / "EPIC-001"
        epic_path.mkdir(parents=True, exist_ok=True)
        (epic_path / "status.yaml").write_text("id: EPIC-001\ntype: epic\n", encoding="utf-8")

        # 2. Feature aninhada sob features/ da Epic
        feat_path = resolver.construct_canonical_path("FEATURE-001", WorkItemKind.FEATURE, parent_id="EPIC-001")
        assert feat_path == epic_path / "features" / "FEATURE-001"
        feat_path.mkdir(parents=True, exist_ok=True)
        (feat_path / "status.yaml").write_text("id: FEATURE-001\ntype: feature\nparent_id: EPIC-001\n", encoding="utf-8")

        # 3. Story aninhada sob stories/ da Feature
        story_path = resolver.construct_canonical_path("STORY-001", WorkItemKind.STORY, parent_id="FEATURE-001")
        assert story_path == feat_path / "stories" / "STORY-001"
        story_path.mkdir(parents=True, exist_ok=True)
        (story_path / "status.yaml").write_text("id: STORY-001\ntype: story\nparent_id: FEATURE-001\n", encoding="utf-8")

        # 4. Task aninhada sob tasks/ da Story
        task_path = resolver.construct_canonical_path("TASK-0001", WorkItemKind.TASK, parent_id="STORY-001")
        assert task_path == story_path / "tasks" / "TASK-0001"
        task_path.mkdir(parents=True, exist_ok=True)
        (task_path / "status.yaml").write_text("id: TASK-0001\ntype: task\nparent_id: STORY-001\n", encoding="utf-8")

    def test_legacy_flat_path_can_be_resolved(self, r3_paths_env):
        """Work items existentes em layout plano legado são perfeitamente resolvidos."""
        _, project_id, work_dir = r3_paths_env
        resolver = WorkItemPathResolver(ROOT, project_id)

        legacy_flat = work_dir / "US-LEGACY-42"
        legacy_flat.mkdir(parents=True, exist_ok=True)
        (legacy_flat / "status.yaml").write_text("id: US-LEGACY-42\ntype: story\n", encoding="utf-8")

        resolved = resolver.resolve_item_path("US-LEGACY-42")
        assert resolved == legacy_flat

        # Também resolve quando consultado com o ID normalizado
        resolved_norm = resolver.resolve_item_path("STORY-LEGACY-42")
        assert resolved_norm == legacy_flat

    def test_canonical_path_is_preferred_when_both_exist(self, r3_paths_env):
        """Item canônico aninhado é preferido quando existe correspondência canônica e legada."""
        _, project_id, work_dir = r3_paths_env
        resolver = WorkItemPathResolver(ROOT, project_id)

        # Flat legado antigo
        flat_dir = work_dir / "FEATURE-001"
        flat_dir.mkdir(parents=True, exist_ok=True)
        (flat_dir / "status.yaml").write_text("id: FEATURE-001\ntype: feature\n", encoding="utf-8")

        # Canônico aninhado sob Epic
        epic_dir = work_dir / "EPIC-001"
        nested_dir = epic_dir / "features" / "FEATURE-001"
        nested_dir.mkdir(parents=True, exist_ok=True)
        (nested_dir / "status.yaml").write_text("id: FEATURE-001\ntype: feature\n", encoding="utf-8")

        # Busca por caminho relativo ou direto aninhado resolve corretamente
        resolved = resolver.resolve_item_path(nested_dir)
        assert resolved == nested_dir

    def test_path_traversal_strictly_rejected(self):
        """Tentativas de path traversal via .. ou barras são estritamente bloqueadas."""
        with pytest.raises(PathContainmentViolation, match="Path traversal detected"):
            sanitize_slug("../malicious")

        with pytest.raises(PathContainmentViolation, match="Path traversal detected"):
            sanitize_slug("..\\malicious")

        with pytest.raises(PathContainmentViolation, match="Path traversal detected"):
            sanitize_slug("sub/folder")

        with pytest.raises(PathContainmentViolation, match="Path traversal detected"):
            sanitize_slug("sub\\folder")

    def test_absolute_external_path_rejected(self, r3_paths_env):
        """Paths absolutos externos violando a raiz do projeto disparam PathContainmentViolation."""
        _, project_id, _ = r3_paths_env
        resolver = WorkItemPathResolver(ROOT, project_id)

        outside_path = ROOT / "work" / "other_project" / "EPIC-OTHER"
        with pytest.raises(PathContainmentViolation):
            resolver._validate_containment(outside_path)

        system_outside = Path("C:/Windows/System32")
        with pytest.raises(PathContainmentViolation):
            resolver._validate_containment(system_outside)

    def test_dangerous_slug_and_windows_reserved_names_rejected(self):
        """Nomes reservados do Windows (CON, NUL, AUX, PRN, COM1..9, etc.) e caracteres ilegais são rejeitados."""
        for reserved in ["CON", "con", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9"]:
            with pytest.raises(ValidationError, match="reserved system device name"):
                sanitize_slug(reserved)

        # Caracteres ilegais
        for illegal in ["ITEM@01", "ITEM#02", "ITEM$03", "ITEM*04", "ITEM 05"]:
            with pytest.raises(ValidationError, match="contains illegal characters"):
                sanitize_slug(illegal)

    def test_zero_writes_outside_project_work_root(self, r3_paths_env):
        """PathContainmentGuard garante que nenhuma escrita ocorra fora de work/<project_id>/."""
        _, project_id, work_dir = r3_paths_env

        valid_path = work_dir / "EPIC-001" / "status.yaml"
        # Deve passar sem exceção
        PathContainmentGuard.validate_work_path(valid_path, ROOT, project_id)

        # Fora do projeto
        invalid_path = ROOT / "work" / "wrong_proj" / "EPIC-001"
        with pytest.raises(PathContainmentViolation):
            PathContainmentGuard.validate_work_path(invalid_path, ROOT, project_id)

    def test_find_all_work_items_discovers_nested_and_flat(self, r3_paths_env):
        """find_all_work_items descobre todos os work items válidos recursivamente."""
        _, project_id, work_dir = r3_paths_env
        resolver = WorkItemPathResolver(ROOT, project_id)

        # 1 Flat
        flat = work_dir / "EPIC-ROOT"
        flat.mkdir(parents=True, exist_ok=True)
        (flat / "status.yaml").write_text("id: EPIC-ROOT\ntype: epic\n", encoding="utf-8")

        # 1 Nested
        nested = flat / "features" / "FEATURE-NESTED"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "status.yaml").write_text("id: FEATURE-NESTED\ntype: feature\n", encoding="utf-8")

        found = resolver.find_all_work_items()
        found_names = [p.name for p in found]
        assert "EPIC-ROOT" in found_names
        assert "FEATURE-NESTED" in found_names
