"""R9 Unit Tests — SpecialistInstructionCompiler.

Tests instruction compilation, section ordering, cognitive contract inclusion,
authoritative hash derivation, and fail-closed behavior.
"""

from pathlib import Path
import sys
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))

from scripts.domain.delegation import AncestorSnapshot, WorkContext
from scripts.domain.work_items import AcceptanceCriterion, WorkItemKind
from scripts.runtime.activation.compiler import SpecialistInstructionCompiler
from scripts.runtime.activation.errors import CompilationError
from scripts.runtime.activation.skills import ResolvedSkills


@pytest.fixture
def temp_compiler_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create agent directory & PROMPT.md
        agent_dir = root / "agents" / "software-engineer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "PROMPT.md").write_text(
            "# Role: Software Engineer\nYou write clean code following TDD.",
            encoding="utf-8",
        )

        # Create a skill file
        skill_dir = root / "skills" / "clean-code"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("# Clean Code Rules\n- Small functions\n- Descriptive names", encoding="utf-8")

        # Config devops.yaml
        cfg_dir = root / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "devops.yaml").write_text(
            "org: cbvgas\nproject: Arthemis\nteam: agent-squad\n",
            encoding="utf-8",
        )

        yield root


def test_compiler_assembles_authoritative_prompt_and_hash(temp_compiler_env):
    root = temp_compiler_env
    compiler = SpecialistInstructionCompiler(runtime_root=root)

    ctx = WorkContext(
        work_item_id="TASK-0001",
        project_id="test-proj",
        current_stage="IMPLEMENTATION",
        title="Create API endpoint",
        description="Write REST endpoint",
        definition_of_done=["Clean code", "Unit tests"],
        acceptance_criteria=[
            AcceptanceCriterion(
                id="AC-001",
                scenario="Success response",
                given="State is valid",
                when="Call GET /api",
                then="Return 200 OK",
            )
        ],
        ancestors=[
            AncestorSnapshot(
                work_item_id="STORY-001",
                kind=WorkItemKind.STORY,
                title="User Story 1",
                stage="IMPLEMENTATION",
                spec_summary="Allow user data access",
            )
        ],
        ancestor_artifacts={"STORY-001/story.md": "# Story\nUser can access data"},
        active_receipts=["CODE_DIFF"],
        filesystem_scope=["/work/test-proj/TASK-0001"],
    )

    skills = ResolvedSkills(
        agent_id="software-engineer",
        native_skills=["skills/clean-code"],
        assigned_skills=[],
        discovered_skills=[],
        load_order=["skills/clean-code/SKILL.md"],
        skill_manifest={"agent": "software-engineer"},
    )

    compiled, instr_hash = compiler.compile_instruction("software-engineer", ctx, skills)

    assert compiled
    assert instr_hash
    # Check all mandatory sections
    assert "<environment_details>" in compiled
    assert "# AGENT SYSTEM PROMPT: software-engineer" in compiled
    assert "# CONTRATO COGNITIVO, ANTI-ALUCINAÇÃO & QUALIDADE DE EXECUÇÃO" in compiled
    assert "## SKILL: skills/clean-code/SKILL.md" in compiled
    assert "# CONTEXTO DO WORK ITEM (TASK-0001)" in compiled
    assert "🔷 **STORY**: STORY-001" in compiled
    assert "## Azure DevOps — Contexto Operacional" in compiled
    assert "# ARQUITETURA CANÔNICA DE MEMÓRIA EM 3 PILARES" in compiled

    import hashlib
    expected_hash = hashlib.sha256(compiled.encode("utf-8")).hexdigest()
    assert instr_hash == expected_hash


def test_compiler_fails_closed_on_missing_agent_prompt(temp_compiler_env):
    root = temp_compiler_env
    compiler = SpecialistInstructionCompiler(runtime_root=root)

    ctx = WorkContext(
        work_item_id="TASK-0001",
        project_id="test-proj",
        current_stage="IMPLEMENTATION",
        title="Title",
        description="",
        definition_of_done=[],
        acceptance_criteria=[],
        ancestors=[],
        ancestor_artifacts={},
        active_receipts=[],
        filesystem_scope=[],
    )
    skills = ResolvedSkills(
        agent_id="nonexistent-agent",
        native_skills=[],
        assigned_skills=[],
        discovered_skills=[],
        load_order=[],
        skill_manifest={},
    )

    with pytest.raises(CompilationError, match="System prompt file not found"):
        compiler.compile_instruction("nonexistent-agent", ctx, skills)


def test_full_prompt_preservation_with_sentinel_blocks(temp_compiler_env):
    root = temp_compiler_env
    sentinel_content = (
        "# SENTINEL PROMPT ALPHA\n"
        "SENTINEL_UNIQUE_TOKEN_998877\n"
        "| Table Col 1 | Table Col 2 |\n"
        "|---|---|\n"
        "| Value 1 | Value 2 |\n"
        "```python\ndef sentinel_code(): pass\n```\n"
        "FINAL_SENTINEL_TOKEN_554433"
    )
    (root / "agents" / "software-engineer" / "PROMPT.md").write_text(sentinel_content, encoding="utf-8")

    compiler = SpecialistInstructionCompiler(runtime_root=root)
    ctx = WorkContext(
        work_item_id="TASK-0001",
        project_id="test-proj",
        current_stage="IMPLEMENTATION",
        title="Title",
        description="",
        definition_of_done=[],
        acceptance_criteria=[],
        ancestors=[],
        ancestor_artifacts={},
        active_receipts=[],
        filesystem_scope=[],
    )
    skills = ResolvedSkills(
        agent_id="software-engineer",
        native_skills=[],
        assigned_skills=[],
        discovered_skills=[],
        load_order=[],
        skill_manifest={},
    )

    compiled, _ = compiler.compile_instruction("software-engineer", ctx, skills)

    # Must preserve every token and block without reduction
    assert "SENTINEL_UNIQUE_TOKEN_998877" in compiled
    assert "| Table Col 1 | Table Col 2 |" in compiled
    assert "def sentinel_code(): pass" in compiled
    assert "FINAL_SENTINEL_TOKEN_554433" in compiled


def test_zero_unauthorized_compiler_sections(temp_compiler_env):
    root = temp_compiler_env
    compiler = SpecialistInstructionCompiler(runtime_root=root)

    ctx = WorkContext(
        work_item_id="TASK-0001",
        project_id="test-proj",
        current_stage="IMPLEMENTATION",
        title="Title",
        description="Desc",
        definition_of_done=[],
        acceptance_criteria=[],
        ancestors=[],
        ancestor_artifacts={},
        active_receipts=[],
        filesystem_scope=[],
    )
    skills = ResolvedSkills(
        agent_id="software-engineer",
        native_skills=["skills/clean-code"],
        assigned_skills=[],
        discovered_skills=[],
        load_order=["skills/clean-code/SKILL.md"],
        skill_manifest={},
    )

    compiled, _ = compiler.compile_instruction("software-engineer", ctx, skills)

    # Section 5 classification:
    # SPECIALIST_PROMPT, WORK_CONTEXT, ASSIGNMENT, RESOLVED_SKILL,
    # TOOL_REQUIREMENT, CANONICAL_GLOBAL_POLICY, UNAUTHORIZED_SYNTHETIC_INSTRUCTION
    section_classifications = {
        "<environment_details>": "WORK_CONTEXT",
        "# AGENT SYSTEM PROMPT:": "SPECIALIST_PROMPT",
        "# CONTRATO COGNITIVO, ANTI-ALUCINAÇÃO & QUALIDADE DE EXECUÇÃO": "CANONICAL_GLOBAL_POLICY",
        "# HABILIDADES E CONHECIMENTOS CARREGADOS (SKILLS)": "RESOLVED_SKILL",
        "# CONTEXTO DO WORK ITEM": "WORK_CONTEXT",
        "## Azure DevOps — Contexto Operacional": "TOOL_REQUIREMENT",
        "# ARQUITETURA CANÔNICA DE MEMÓRIA EM 3 PILARES": "CANONICAL_GLOBAL_POLICY",
    }

    found_categories = set()
    for heading, cat in section_classifications.items():
        if heading in compiled:
            found_categories.add(cat)

    assert "SPECIALIST_PROMPT" in found_categories
    assert "WORK_CONTEXT" in found_categories
    assert "CANONICAL_GLOBAL_POLICY" in found_categories
    assert "RESOLVED_SKILL" in found_categories
    assert "TOOL_REQUIREMENT" in found_categories
    assert "UNAUTHORIZED_SYNTHETIC_INSTRUCTION" not in found_categories


def test_activation_packet_single_payload_authority():
    from scripts.domain.delegation import ActivationPacket, WorkContext
    import dataclasses

    fields = [f.name for f in dataclasses.fields(ActivationPacket)]
    assert "compiled_instruction" in fields
    assert "raw_prompt" not in fields
    assert "briefing" not in fields
    assert "fallback_instruction" not in fields
    assert "persona_prompt" not in fields


def test_prepare_delegation_propagates_compiler_error(monkeypatch):
    from integrations.resolvers.assignment_resolver import prepare_delegation
    from integrations.mcp_session_store import SessionStore
    from integrations.resolvers import ResolverContext

    store = SessionStore()
    session = store.create_session("host", "root", "TASK-0001", "hash")

    # Mock render_agent_prompt to raise CompilationError
    def mock_render(*args, **kwargs):
        raise CompilationError("Strict compilation failure in test")

    import scripts.render_agent_prompt
    monkeypatch.setattr(scripts.render_agent_prompt, "render_agent_prompt", mock_render)

    args = {
        "session": session["session_id"],
        "target_role": "software-engineer",
        "scope": "Test",
        "action": "Exec",
    }
    ctx = ResolverContext(db_path=":memory:", config_dir="config")

    with pytest.raises(CompilationError, match="Strict compilation failure in test"):
        prepare_delegation(args, ctx, store)

