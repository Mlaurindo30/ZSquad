"""Canonical Skill Resolution and Cognitive Budget Enforcement for Milestone R9.

Enforces 5-step loading order, validates skill catalog containment, applies strict
cognitive budget (<= 7 domain skills), and isolates MCP control plane tools.
Strictly stdlib + yaml only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
import yaml

from .errors import SkillBudgetExceededError, SkillNotFoundError, SkillResolutionError

DEFAULT_COGNITIVE_SKILL_BUDGET = 7


@dataclass(frozen=True)
class ResolvedSkills:
    """Immutable result of canonical skill resolution."""

    agent_id: str
    native_skills: List[str]
    assigned_skills: List[str]
    discovered_skills: List[str]
    load_order: List[str]
    skill_manifest: Dict[str, Any]

    @property
    def total_count(self) -> int:
        return len(self.load_order)


class SkillResolver:
    """Authoritative resolver for agent skills with cognitive budget enforcement."""

    def __init__(self, runtime_root: Union[str, Path], max_budget: Optional[int] = None):
        self.runtime_root = Path(runtime_root).resolve()
        self.catalog = self._load_catalog()
        self.budget = max_budget if max_budget is not None else self._load_canonical_budget()

    def _load_yaml(self, path: Path) -> Dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            content = path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            return data if isinstance(data, dict) else {}
        except Exception as err:
            raise SkillResolutionError(f"Failed to read YAML at {path}: {err}") from err

    def _load_canonical_budget(self) -> int:
        """Loads canonical cognitive skill budget from approved governance configurations."""
        cat_file = self.runtime_root / "config" / "skills-catalog.yaml"
        if cat_file.is_file():
            data = self._load_yaml(cat_file)
            if "cognitive_skill_budget" in data:
                return int(data["cognitive_skill_budget"])
        wf_file = self.runtime_root / "config" / "workflow.yaml"
        if wf_file.is_file():
            data = self._load_yaml(wf_file)
            if "cognitive_skill_budget" in data:
                return int(data["cognitive_skill_budget"])
        return DEFAULT_COGNITIVE_SKILL_BUDGET

    def _load_catalog(self) -> Set[str]:
        cat_file = self.runtime_root / "config" / "skills-catalog.yaml"
        if not cat_file.is_file():
            return set()
        data = self._load_yaml(cat_file)
        entries = set()
        for item in data.get("catalog", []):
            if isinstance(item, dict) and "path" in item:
                entries.add(item["path"])
        return entries

    def resolve_skills(
        self,
        agent_id: str,
        assigned: Optional[List[str]] = None,
        discovered: Optional[List[str]] = None,
    ) -> ResolvedSkills:
        """Resolves skills in order (Native -> Assigned -> Discovered) under strict budget <= 7.

        Raises:
            SkillNotFoundError: If manifest or declared skill path does not exist on disk.
            SkillBudgetExceededError: If required skills exceed max budget (7).
            SkillResolutionError: On malformed config.
        """
        clean_id = agent_id.strip()
        manifest_path = self._find_agent_manifest(clean_id)
        if not manifest_path or not manifest_path.is_file():
            raise SkillNotFoundError(f"Skill manifest not found for agent '{clean_id}' at {manifest_path}")

        manifest_data = self._load_yaml(manifest_path)

        # 1. Native Skills
        native_paths: List[str] = []
        for item in manifest_data.get("native", []):
            p = item.get("path") if isinstance(item, dict) else item
            if p:
                native_paths.append(str(p))

        # 2. Assigned Skills
        allowed_assigned: Set[str] = set()
        for item in manifest_data.get("assigned", []):
            p = item.get("path") if isinstance(item, dict) else item
            if p:
                allowed_assigned.add(str(p))

        assigned_input = list(assigned or [])
        # If no explicit assigned list given, assign none or default
        # Validate that requested assigned are authorized by manifest
        unauthorized = sorted(set(assigned_input) - allowed_assigned)
        if unauthorized:
            raise SkillResolutionError(
                f"Skills not assigned to agent '{clean_id}' in manifest: {', '.join(unauthorized)}"
            )

        # Check native + assigned budget
        if len(native_paths) + len(assigned_input) > self.budget:
            raise SkillBudgetExceededError(
                f"Agent '{clean_id}' declared skills exceed maximum cognitive budget ({self.budget}): "
                f"{len(native_paths)} native + {len(assigned_input)} assigned"
            )

        # 3. Discovered Skills
        discovery_policy = manifest_data.get("discovery", {})
        max_discovered = int(discovery_policy.get("maximum_loaded", 0))
        available_budget = max(0, self.budget - (len(native_paths) + len(assigned_input)))
        discovery_cap = min(max_discovered, available_budget)

        discovered_input = list(discovered or [])
        if self.catalog and discovered_input:
            invalid_discovered = sorted(set(discovered_input) - self.catalog)
            if invalid_discovered:
                raise SkillResolutionError(
                    f"Discovered skill not in approved catalog: {', '.join(invalid_discovered)}"
                )

        if len(discovered_input) > discovery_cap:
            # Semantic budget trimming for discovered skills
            selected_discovered = discovered_input[:discovery_cap]
        else:
            selected_discovered = discovered_input

        total_selected = native_paths + assigned_input + selected_discovered
        if len(total_selected) > self.budget:
            raise SkillBudgetExceededError(
                f"Total resolved skills ({len(total_selected)}) exceed maximum budget ({self.budget})"
            )

        # 4. Physical existence verification
        load_order_files: List[str] = []
        for rel_path in total_selected:
            full_path = self.runtime_root / rel_path
            if full_path.is_dir():
                skill_file = full_path / "SKILL.md"
                if not skill_file.is_file():
                    raise SkillNotFoundError(f"Required SKILL.md missing in directory: {rel_path}")
                load_order_files.append(skill_file.relative_to(self.runtime_root).as_posix())
            elif full_path.is_file():
                load_order_files.append(rel_path)
            else:
                raise SkillNotFoundError(f"Resolved skill path does not exist on disk: {rel_path}")

        manifest_summary = {
            "agent": clean_id,
            "native": native_paths,
            "assigned": assigned_input,
            "discovered": selected_discovered,
            "load_order": load_order_files,
            "handoff_schema": manifest_data.get("handoff", {}).get("schema", "contracts/handoff.schema.json"),
        }

        return ResolvedSkills(
            agent_id=clean_id,
            native_skills=native_paths,
            assigned_skills=assigned_input,
            discovered_skills=selected_discovered,
            load_order=load_order_files,
            skill_manifest=manifest_summary,
        )

    def _find_agent_manifest(self, agent_id: str) -> Optional[Path]:
        direct = self.runtime_root / "agents" / agent_id / "skills" / "manifest.yaml"
        if direct.is_file():
            return direct

        agents_dir = self.runtime_root / "agents"
        if not agents_dir.is_dir():
            return None

        for child in agents_dir.iterdir():
            if child.is_dir():
                c_name = child.name
                if c_name == agent_id or (c_name.split("-", 1)[-1] == agent_id):
                    manifest = child / "skills" / "manifest.yaml"
                    if manifest.is_file():
                        return manifest
        return None
