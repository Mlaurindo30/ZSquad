"""R9 Unit Tests — SkillResolver & Cognitive Budget.

Tests skill resolution loading order, domain vs tool isolation, catalog validation,
and fail-closed budget enforcement (<= 7 skills).
"""

from pathlib import Path
import tempfile
import pytest
import yaml

from scripts.runtime.activation.errors import (
    SkillBudgetExceededError,
    SkillNotFoundError,
    SkillResolutionError,
)
from scripts.runtime.activation.skills import DEFAULT_COGNITIVE_SKILL_BUDGET, SkillResolver


@pytest.fixture
def temp_skills_environment():
    """Creates a temporary agent and skill catalog structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Config directory & catalog
        cfg_dir = root / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "skills-catalog.yaml").write_text(
            yaml.dump({
                "cognitive_skill_budget": 7,
                "catalog": [
                    {"name": "clean-code", "path": "skills/clean-code"},
                    {"name": "unit-testing", "path": "skills/unit-testing"},
                    {"name": "security-scan", "path": "skills/security-scan"},
                    {"name": "db-migration", "path": "skills/db-migration"},
                    {"name": "api-design", "path": "skills/api-design"},
                    {"name": "perf-audit", "path": "skills/perf-audit"},
                    {"name": "docker-build", "path": "skills/docker-build"},
                    {"name": "k8s-deploy", "path": "skills/k8s-deploy"},
                ]
            }),
            encoding="utf-8",
        )

        # Create physical skill files
        for sname in [
            "clean-code",
            "unit-testing",
            "security-scan",
            "db-migration",
            "api-design",
            "perf-audit",
            "docker-build",
            "k8s-deploy",
            "agent-native",
        ]:
            s_dir = root / "skills" / sname
            s_dir.mkdir(parents=True, exist_ok=True)
            (s_dir / "SKILL.md").write_text(f"# Skill: {sname}\n\nDocumentation for {sname}.", encoding="utf-8")

        # Agent with 2 native, 3 assigned
        agent_dir = root / "agents" / "test-engineer" / "skills"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "manifest.yaml").write_text(
            yaml.dump({
                "agent": "test-engineer",
                "native": [
                    {"path": "skills/agent-native"},
                    {"path": "skills/unit-testing"},
                ],
                "assigned": [
                    {"path": "skills/clean-code"},
                    {"path": "skills/security-scan"},
                    {"path": "skills/perf-audit"},
                ],
                "discovery": {
                    "policy": "curated-local-first",
                    "maximum_loaded": 3,
                },
                "handoff": {"schema": "contracts/handoff.schema.json"},
            }),
            encoding="utf-8",
        )

        yield root


def test_skill_resolver_order_and_manifest(temp_skills_environment):
    root = temp_skills_environment
    resolver = SkillResolver(runtime_root=root)

    resolved = resolver.resolve_skills(
        agent_id="test-engineer",
        assigned=["skills/clean-code"],
        discovered=["skills/db-migration"],
    )

    assert resolved.agent_id == "test-engineer"
    assert resolved.native_skills == ["skills/agent-native", "skills/unit-testing"]
    assert resolved.assigned_skills == ["skills/clean-code"]
    assert resolved.discovered_skills == ["skills/db-migration"]
    assert resolved.total_count == 4
    # Load order contains relative SKILL.md paths
    assert "skills/agent-native/SKILL.md" in resolved.load_order
    assert "skills/unit-testing/SKILL.md" in resolved.load_order
    assert "skills/clean-code/SKILL.md" in resolved.load_order
    assert "skills/db-migration/SKILL.md" in resolved.load_order


def test_skill_resolver_rejects_unauthorized_assigned(temp_skills_environment):
    root = temp_skills_environment
    resolver = SkillResolver(runtime_root=root)

    with pytest.raises(SkillResolutionError, match="Skills not assigned to agent"):
        resolver.resolve_skills(
            agent_id="test-engineer",
            assigned=["skills/docker-build"],  # not in manifest assigned
        )


def test_skill_resolver_enforces_budget_cap(temp_skills_environment):
    root = temp_skills_environment
    resolver = SkillResolver(runtime_root=root)

    # Agent has 2 native + 3 assigned = 5. Max budget is 7.
    # If 3 discovered are requested (total 8), discovery is safely capped to 2 (remaining budget 7-5=2)
    resolved = resolver.resolve_skills(
        agent_id="test-engineer",
        assigned=["skills/clean-code", "skills/security-scan", "skills/perf-audit"],
        discovered=["skills/db-migration", "skills/api-design", "skills/docker-build"],
    )

    assert resolved.total_count == resolver.budget  # strictly 7
    assert len(resolved.discovered_skills) == 2  # trimmed to fit budget 7


def test_skill_budget_read_from_canonical_configuration(temp_skills_environment):
    root = temp_skills_environment
    cat_file = root / "config" / "skills-catalog.yaml"
    cat_file.write_text(yaml.dump({"cognitive_skill_budget": 5, "catalog": []}), encoding="utf-8")

    resolver = SkillResolver(runtime_root=root)
    assert resolver.budget == 5, "SkillResolver MUST read budget from canonical configuration"


def test_mandatory_skill_overflow_blocks_never_truncates(temp_skills_environment):
    root = temp_skills_environment
    # Overload manifest with 8 native skills
    manifest_file = root / "agents" / "test-engineer" / "skills" / "manifest.yaml"
    manifest_file.write_text(
        yaml.dump({
            "agent": "test-engineer",
            "native": [{"path": f"skills/s{i}"} for i in range(8)],
            "assigned": [],
        }),
        encoding="utf-8",
    )

    resolver = SkillResolver(runtime_root=root)
    with pytest.raises(SkillBudgetExceededError, match="exceed maximum cognitive budget"):
        # MUST raise SkillBudgetExceededError, NEVER silently truncate native/assigned skills
        resolver.resolve_skills(agent_id="test-engineer")

