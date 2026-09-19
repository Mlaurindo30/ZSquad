"""
R3 Work Item Templates & Artifact Containment Suite (Section 34).

Tests level-specific template materialization (Epic, Feature, Story, Task),
strict prohibition of artifact leakage (Task never receives epic.md or product-goal.md - R0-WORK-003),
required headers/sections presence, placeholder rendering, and absence of project-specific hardcoded constants.
"""

from pathlib import Path
import shutil
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_squad import AgentSquad
from scripts.domain.work_items import WorkItemKind
from scripts.runtime.work_items.templates import (
    ArtifactContainmentViolation,
    ArtifactMaterializer,
    PROHIBITED_ARTIFACTS,
    REQUIRED_ARTIFACTS,
)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT / "templates"


@pytest.fixture
def r3_templates_env():
    project_id = "test-r3-templates"
    work_dir = ROOT / "work" / project_id
    lock_dir = ROOT / ".locks" / project_id

    def cleanup():
        shutil.rmtree(work_dir, ignore_errors=True)
        shutil.rmtree(lock_dir, ignore_errors=True)

    cleanup()
    squad = AgentSquad(ROOT, project_name=project_id)
    yield squad, project_id, work_dir
    cleanup()


class TestR3WorkItemTemplates:
    """Suíte canônica de testes de templates e contenção de artefatos (R3 - Seção 34)."""

    def test_task_never_materializes_epic_or_product_artifacts(self, r3_templates_env):
        """Task NÃO recebe epic.md, product-goal.md, architecture-vision.md nem backlog.md (R0-WORK-003)."""
        squad, _, _ = r3_templates_env

        # Inicializa hierarquia completa
        squad.init_work_item("EPIC-TPL-01", "high", item_type="epic")
        squad.init_work_item("FEATURE-TPL-01", "medium", item_type="feature", parent_id="EPIC-TPL-01")
        squad.init_work_item("STORY-TPL-01", "medium", item_type="story", parent_id="FEATURE-TPL-01", story_points=3)
        task_dir = squad.init_work_item("TASK-TPL-0001", "low", item_type="task", parent_id="STORY-TPL-01")

        # Artefato autorizado de Task deve existir
        assert (task_dir / "task-scope.md").is_file()

        # Artefatos proibidos NUNCA devem existir no diretório de Task
        assert not (task_dir / "epic.md").exists()
        assert not (task_dir / "product-goal.md").exists()
        assert not (task_dir / "architecture-vision.md").exists()
        assert not (task_dir / "backlog.md").exists()
        assert not (task_dir / "feature-spec.md").exists()
        assert not (task_dir / "component-design.md").exists()

    def test_artifact_materializer_blocks_prohibited_artifacts_at_each_level(self):
        """ArtifactMaterializer dispara ArtifactContainmentViolation para artefatos proibidos."""
        materializer = ArtifactMaterializer(TEMPLATES_DIR)

        # TASK
        with pytest.raises(ArtifactContainmentViolation, match="strictly prohibited for TASK"):
            materializer.validate_artifact_allowed(WorkItemKind.TASK, "epic.md")

        with pytest.raises(ArtifactContainmentViolation, match="strictly prohibited for TASK"):
            materializer.validate_artifact_allowed(WorkItemKind.TASK, "product-goal.md")

        with pytest.raises(ArtifactContainmentViolation, match="strictly prohibited for TASK"):
            materializer.validate_artifact_allowed(WorkItemKind.TASK, "backlog.md")

        # STORY
        with pytest.raises(ArtifactContainmentViolation, match="strictly prohibited for STORY"):
            materializer.validate_artifact_allowed(WorkItemKind.STORY, "epic.md")

        with pytest.raises(ArtifactContainmentViolation, match="strictly prohibited for STORY"):
            materializer.validate_artifact_allowed(WorkItemKind.STORY, "product-goal.md")

        # FEATURE
        with pytest.raises(ArtifactContainmentViolation, match="strictly prohibited for FEATURE"):
            materializer.validate_artifact_allowed(WorkItemKind.FEATURE, "epic.md")

        with pytest.raises(ArtifactContainmentViolation, match="strictly prohibited for FEATURE"):
            materializer.validate_artifact_allowed(WorkItemKind.FEATURE, "task-scope.md")

        # EPIC
        with pytest.raises(ArtifactContainmentViolation, match="strictly prohibited for EPIC"):
            materializer.validate_artifact_allowed(WorkItemKind.EPIC, "task-scope.md")

    def test_epic_receives_only_epic_artifacts(self, r3_templates_env):
        """Epic recebe apenas artefatos de nível Epic."""
        squad, _, _ = r3_templates_env
        epic_dir = squad.init_work_item("EPIC-ONLY-01", "high", item_type="epic")

        assert (epic_dir / "epic.md").is_file()
        assert (epic_dir / "product-goal.md").is_file()
        assert (epic_dir / "architecture-vision.md").is_file()
        assert (epic_dir / "documentation" / "delivery-ledger.md").is_file()

        # Não recebe artefatos de Story ou Task
        assert not (epic_dir / "task-scope.md").exists()
        assert not (epic_dir / "acceptance-criteria.md").exists()

    def test_feature_receives_only_feature_artifacts(self, r3_templates_env):
        """Feature recebe apenas artefatos de nível Feature."""
        squad, _, _ = r3_templates_env
        squad.init_work_item("EPIC-ROOT-01", "high", item_type="epic")
        feat_dir = squad.init_work_item("FEATURE-ONLY-01", "medium", item_type="feature", parent_id="EPIC-ROOT-01")

        assert (feat_dir / "feature-spec.md").is_file()
        assert (feat_dir / "component-design.md").is_file()
        assert (feat_dir / "documentation" / "delivery-ledger.md").is_file()

        # Não recebe artefatos de Epic nem de Task
        assert not (feat_dir / "epic.md").exists()
        assert not (feat_dir / "task-scope.md").exists()

    def test_story_receives_only_story_artifacts(self, r3_templates_env):
        """Story recebe apenas artefatos de nível Story."""
        squad, _, _ = r3_templates_env
        squad.init_work_item("EPIC-SROOT-01", "high", item_type="epic")
        squad.init_work_item("FEATURE-SROOT-01", "medium", item_type="feature", parent_id="EPIC-SROOT-01")
        story_dir = squad.init_work_item("STORY-ONLY-01", "medium", item_type="story", parent_id="FEATURE-SROOT-01", story_points=5)

        assert (story_dir / "user-story.md").is_file()
        assert (story_dir / "acceptance-criteria.md").is_file()
        assert (story_dir / "documentation" / "delivery-ledger.md").is_file()

        # Não recebe artefatos de Epic nem de Task
        assert not (story_dir / "epic.md").exists()
        assert not (story_dir / "product-goal.md").exists()
        assert not (story_dir / "task-scope.md").exists()

    def test_required_headers_present_in_templates(self):
        """Templates base possuem seções e cabeçalhos obrigatórios."""
        epic_content = (TEMPLATES_DIR / "epic.md").read_text(encoding="utf-8")
        assert "## Objective" in epic_content
        assert "## Scope" in epic_content
        assert "## Definition of Done" in epic_content

        feat_content = (TEMPLATES_DIR / "feature.md").read_text(encoding="utf-8")
        assert "## Capability" in feat_content
        assert "## Scope" in feat_content
        assert "## Definition of Done" in feat_content

        story_content = (TEMPLATES_DIR / "story.md").read_text(encoding="utf-8")
        assert "## Outcome & Value Proposition" in story_content
        assert "## Acceptance Criteria" in story_content
        assert "## Definition of Done" in story_content

        task_content = (TEMPLATES_DIR / "task.md").read_text(encoding="utf-8")
        assert "## Objective" in task_content
        assert "## Acceptance Criteria" in task_content
        assert "## Definition of Done" in task_content

    def test_zero_project_specific_constants_in_work_item_templates(self):
        """Zero constantes de projetos específicos (Deepvision, Arthemis, cbvgas) em templates de work item."""
        work_item_templates = [
            "epic.md",
            "feature.md",
            "feature-spec.md",
            "component-design.md",
            "story.md",
            "user-story.md",
            "acceptance-criteria.md",
            "task.md",
            "task-scope.md",
            "product-goal.md",
            "architecture-vision.md",
            "delivery-ledger.md",
        ]

        forbidden_tokens = ["deepvision", "arthemis", "cbvgas"]

        for tpl_name in work_item_templates:
            tpl_path = TEMPLATES_DIR / tpl_name
            if not tpl_path.is_file():
                continue
            content = tpl_path.read_text(encoding="utf-8").lower()
            for token in forbidden_tokens:
                assert token not in content, (
                    f"Forbidden project token '{token}' detected in work item template: {tpl_name}"
                )
