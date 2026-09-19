"""Activation Service for Milestone R9.

Transforms canonical R8 ExecutionAssignments into fully compiled, immutable,
and persisted ActivationPackets. Zero host dispatch. Zero MCP session authority.
Strictly stdlib-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import uuid

from scripts.domain.delegation import ActivationPacket, ExecutionAssignment
from .compiler import SpecialistInstructionCompiler
from .context import WorkContextBuilder
from .repository import ActivationRepository
from .skills import SkillResolver


class ActivationService:
    """Canonical service that transforms an ExecutionAssignment into an ActivationPacket."""

    def __init__(
        self,
        runtime_root: Union[str, Path],
        db_path: Optional[Union[str, Path]] = None,
    ):
        self.runtime_root = Path(runtime_root).resolve()
        if db_path is None:
            db_path = self.runtime_root / "banco" / "squad.db"
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.skill_resolver = SkillResolver(self.runtime_root)
        self.compiler = SpecialistInstructionCompiler(self.runtime_root)
        self.repository = ActivationRepository(self.db_path)

    def activate(
        self,
        assignment: ExecutionAssignment,
        project_id: str,
        assigned_skills: Optional[List[str]] = None,
        discovered_skills: Optional[List[str]] = None,
        active_receipts: Optional[List[str]] = None,
    ) -> ActivationPacket:
        """Transforms an R8 ExecutionAssignment into a persisted ActivationPacket.

        Idempotent: Identical assignment and context will return existing packet.
        """
        context_builder = WorkContextBuilder(self.runtime_root, project_id)
        work_context, fingerprint = context_builder.build_work_context(
            work_item_id=assignment.work_item_id,
            current_stage=assignment.stage,
            active_receipts=active_receipts,
        )

        resolved_skills = self.skill_resolver.resolve_skills(
            agent_id=assignment.agent_id,
            assigned=assigned_skills,
            discovered=discovered_skills,
        )

        compiled_instruction, instruction_hash = self.compiler.compile_instruction(
            agent_id=assignment.agent_id,
            work_context=work_context,
            resolved_skills=resolved_skills,
        )

        # Check existing in repository
        existing = self.repository.find_existing(
            assignment_id=assignment.assignment_id,
            context_fingerprint=fingerprint,
            instruction_hash=instruction_hash,
        )
        if existing:
            return existing

        activation_id = f"ACT-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        packet = ActivationPacket(
            session_id=activation_id,
            agent_id=assignment.agent_id,
            role_name=assignment.assigned_role,
            work_item_id=assignment.work_item_id,
            work_context=work_context,
            skill_manifest=resolved_skills.skill_manifest,
            compiled_instruction=compiled_instruction,
            instruction_hash=instruction_hash,
            created_at=now,
        )

        self.repository.save(
            packet=packet,
            assignment_id=assignment.assignment_id,
            context_fingerprint=fingerprint,
        )

        return packet

    def close(self) -> None:
        """Closes repository database connections."""
        if hasattr(self, "repository") and self.repository:
            self.repository.close()

