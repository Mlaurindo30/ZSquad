"""
R3 Work Item Hierarchy & Lineage Suite (Section 32).

Tests canonical 4-tier parent-child invariants (EPIC -> FEATURE -> STORY -> TASK),
rejection of invalid tier pairings, ancestor chain resolution, and hierarchy context compilation.
"""

from pathlib import Path
import shutil
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_squad import AgentSquad, SquadError
from scripts.domain.common import ValidationError
from scripts.domain.work_items import WorkHierarchy, WorkItemKind
from scripts.runtime.work_items import (
    HierarchyContextResolver,
    WorkItemPathResolver,
    validate_parent_child,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def r3_hierarchy_env():
    project_id = "test-r3-hierarchy"
    work_dir = ROOT / "work" / project_id
    lock_dir = ROOT / ".locks" / project_id

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)

    cleanup()
    squad = AgentSquad(ROOT, project_name=project_id)
    yield squad, project_id
    cleanup()


class TestR3WorkItemHierarchy:
    """Suíte canônica de testes de hierarquia e linhagem (R3 - Seção 32)."""

    def test_epic_without_parent_accepted(self):
        """Epic sem parent é aceito como raiz de escopo."""
        # Domain level
        WorkHierarchy.validate_parent_child(None, WorkItemKind.EPIC)
        # Runtime level
        validate_parent_child(None, WorkItemKind.EPIC)
        validate_parent_child(None, "EPIC")

    def test_feature_under_epic_accepted(self):
        """Feature sob Epic é estritamente aceita."""
        WorkHierarchy.validate_parent_child(WorkItemKind.EPIC, WorkItemKind.FEATURE)
        validate_parent_child(WorkItemKind.EPIC, WorkItemKind.FEATURE)
        validate_parent_child("EPIC", "FEATURE")

    def test_story_under_feature_accepted(self):
        """Story sob Feature é estritamente aceita."""
        WorkHierarchy.validate_parent_child(WorkItemKind.FEATURE, WorkItemKind.STORY)
        validate_parent_child(WorkItemKind.FEATURE, WorkItemKind.STORY)
        validate_parent_child("FEATURE", "STORY")

    def test_task_under_story_accepted(self):
        """Task sob Story é estritamente aceita."""
        WorkHierarchy.validate_parent_child(WorkItemKind.STORY, WorkItemKind.TASK)
        validate_parent_child(WorkItemKind.STORY, WorkItemKind.TASK)
        validate_parent_child("STORY", "TASK")

    def test_feature_without_epic_rejected(self):
        """Feature sem parent Epic é rejeitada."""
        with pytest.raises(ValidationError, match="Invalid parentage"):
            WorkHierarchy.validate_parent_child(None, WorkItemKind.FEATURE)

        with pytest.raises(ValidationError, match="Invalid parentage"):
            validate_parent_child(None, WorkItemKind.FEATURE)

    def test_story_without_feature_rejected(self):
        """Story sem parent Feature é rejeitada."""
        with pytest.raises(ValidationError, match="Invalid parentage"):
            WorkHierarchy.validate_parent_child(None, WorkItemKind.STORY)

        with pytest.raises(ValidationError, match="Invalid parentage"):
            validate_parent_child(None, WorkItemKind.STORY)

    def test_task_without_story_rejected(self):
        """Task sem parent Story é rejeitada."""
        with pytest.raises(ValidationError, match="Invalid parentage"):
            WorkHierarchy.validate_parent_child(None, WorkItemKind.TASK)

        with pytest.raises(ValidationError, match="Invalid parentage"):
            validate_parent_child(None, WorkItemKind.TASK)

    def test_feature_under_story_rejected(self):
        """Feature sob Story (inversão de hierarquia) é rejeitada."""
        with pytest.raises(ValidationError, match="Invalid parentage"):
            validate_parent_child(WorkItemKind.STORY, WorkItemKind.FEATURE)

    def test_story_under_epic_rejected(self):
        """Story diretamente sob Epic (salto de nível) é rejeitada."""
        with pytest.raises(ValidationError, match="Invalid parentage"):
            validate_parent_child(WorkItemKind.EPIC, WorkItemKind.STORY)

    def test_task_under_feature_rejected(self):
        """Task diretamente sob Feature (salto de nível) é rejeitada."""
        with pytest.raises(ValidationError, match="Invalid parentage"):
            validate_parent_child(WorkItemKind.FEATURE, WorkItemKind.TASK)

    def test_epic_with_parent_rejected(self):
        """Epic com parent (qualquer tipo) é rejeitado."""
        with pytest.raises(ValidationError, match="Invalid parentage"):
            validate_parent_child(WorkItemKind.EPIC, WorkItemKind.EPIC)

        with pytest.raises(ValidationError, match="Invalid parentage"):
            validate_parent_child(WorkItemKind.FEATURE, WorkItemKind.EPIC)

    def test_ancestor_chain_from_task_returns_story_feature_epic(self, r3_hierarchy_env):
        """Cadeia de ancestrais a partir de Task retorna: Story, Feature, Epic em ordem."""
        squad, project_id = r3_hierarchy_env
        path_resolver = WorkItemPathResolver(ROOT, project_id)
        h_resolver = HierarchyContextResolver(path_resolver)

        # Criação da árvore canônica de 4 níveis via AgentSquad
        epic_dir = squad.init_work_item("EPIC-H-01", "high", item_type="epic")
        feat_dir = squad.init_work_item("FEATURE-H-01", "medium", item_type="feature", parent_id="EPIC-H-01")
        story_dir = squad.init_work_item("STORY-H-01", "medium", item_type="story", parent_id="FEATURE-H-01", story_points=5)
        task_dir = squad.init_work_item("TASK-H-0001", "low", item_type="task", parent_id="STORY-H-01")

        # Verificação do direct parent
        parent_info = h_resolver.get_parent("TASK-H-0001")
        assert parent_info is not None
        assert parent_info["id"] == "STORY-H-01"
        assert parent_info["kind"] == WorkItemKind.STORY

        # Verificação da cadeia ascendente completa
        ancestors = h_resolver.get_ancestor_chain("TASK-H-0001")
        assert len(ancestors) == 3

        assert ancestors[0]["id"] == "STORY-H-01"
        assert ancestors[0]["kind"] == WorkItemKind.STORY
        assert ancestors[0]["story_points"] == 5

        assert ancestors[1]["id"] == "FEATURE-H-01"
        assert ancestors[1]["kind"] == WorkItemKind.FEATURE

        assert ancestors[2]["id"] == "EPIC-H-01"
        assert ancestors[2]["kind"] == WorkItemKind.EPIC

    def test_hierarchy_context_compilation_and_briefing(self, r3_hierarchy_env):
        """Compilação de contexto hierárquico injeta briefing e linhagem corretos."""
        squad, project_id = r3_hierarchy_env
        path_resolver = WorkItemPathResolver(ROOT, project_id)
        h_resolver = HierarchyContextResolver(path_resolver)

        squad.init_work_item("EPIC-CTX-01", "high", item_type="epic")
        squad.init_work_item("FEATURE-CTX-01", "medium", item_type="feature", parent_id="EPIC-CTX-01")
        squad.init_work_item("STORY-CTX-01", "medium", item_type="story", parent_id="FEATURE-CTX-01", story_points=3)
        squad.init_work_item("TASK-CTX-0001", "low", item_type="task", parent_id="STORY-CTX-01")

        ctx = h_resolver.compile_hierarchy_context("TASK-CTX-0001")
        assert ctx["work_item_id"] == "TASK-CTX-0001"
        assert ctx["type"] == "task"

        briefing = ctx["formatted_briefing"]
        assert "TASK-CTX-0001 (TASK)" in briefing
        assert "STORY: STORY-CTX-01 (3 SP)" in briefing
        assert "FEATURE: FEATURE-CTX-01" in briefing
        assert "EPIC: EPIC-CTX-01" in briefing

    def test_squad_runtime_rejects_missing_or_mismatched_parents(self, r3_hierarchy_env):
        """AgentSquad runtime rejeita inicialização violando invariantes de parentesco."""
        squad, _ = r3_hierarchy_env

        # Feature sem parent_id
        with pytest.raises(SquadError, match="requires a parent_id of type 'epic'"):
            squad.init_work_item("FEATURE-NO-PARENT", "medium", item_type="feature")

        # Story sem parent_id
        with pytest.raises(SquadError, match="requires a parent_id of type 'feature'"):
            squad.init_work_item("STORY-NO-PARENT", "medium", item_type="story")

        # Task sem parent_id
        with pytest.raises(SquadError, match="requires a parent_id of type 'story'"):
            squad.init_work_item("TASK-NO-PARENT-0001", "low", item_type="task")

        # Epic com parent_id
        with pytest.raises(SquadError, match="Epics cannot have a parent_id"):
            squad.init_work_item("EPIC-INVALID-01", "high", item_type="epic", parent_id="SOME-PARENT")

    def test_qbc_deduplication_allows_distinct_epics(self, r3_hierarchy_env):
        """Resolução R0-WORK-005: QBC permite criação de épicos distintos sem false-positive."""
        squad, _ = r3_hierarchy_env

        epic1 = squad.init_work_item("EPIC-ARCH-01", "high", item_type="epic")
        assert epic1.is_dir()

        # Épico distinto deve ser criado com sucesso sem --force
        epic2 = squad.init_work_item("EPIC-ARCH-02", "high", item_type="epic")
        assert epic2.is_dir()

        # Duplicata com ID idêntico deve ser bloqueada por QBC
        with pytest.raises(SquadError, match="QBC Violation: Duplicate epic detected"):
            squad.init_work_item("EPIC-ARCH-01", "high", item_type="epic")
