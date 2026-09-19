"""Canonical Identifier Service and Legacy Alias Normalizer for Agent Squad Work Items.

Governs canonical grammar, prefix invariants, legacy alias resolution, and ensures
that legacy prefixes (FEAT-, US-, TK-) are never emitted for newly initialized demands.
Strictly stdlib-only.
"""

from __future__ import annotations

import re
from typing import Optional, Union

from scripts.domain.common import ValidationError
from scripts.domain.work_items import WorkItemKind

# Canonical grammar patterns
RE_CANONICAL_EPIC = re.compile(r"^EPIC-\d{3,}$")
RE_CANONICAL_FEATURE = re.compile(r"^FEATURE-\d{3,}$")
RE_CANONICAL_STORY = re.compile(r"^STORY-\d{3,}$")
RE_CANONICAL_TASK = re.compile(r"^TASK-\d{4,}$")
RE_CANONICAL_OPERATIONAL = re.compile(r"^(BUG|SPIKE|INCIDENT|RELEASE|SETUP|EVOLUTION)-\d{3,}$")

# Permissive grammar patterns for reading/resolving existing legacy demands
RE_PERMISSIVE_ANY = re.compile(
    r"^(EPIC|FEATURE|FEAT|STORY|US|TASK|TK|BUG|RELEASE|REL|EVOLUTION|EVOL|STUDY|SPIKE|INCIDENT|SETUP)-[A-Z0-9-]+$"
)

# Legacy alias mappings (Prefix -> Canonical Prefix)
LEGACY_PREFIX_MAP = {
    "FEAT-": "FEATURE-",
    "US-": "STORY-",
    "TK-": "TASK-",
    "REL-": "RELEASE-",
    "EVOL-": "EVOLUTION-",
}

# Reverse mapping for filesystem lookup fallback (Canonical Prefix -> Legacy Prefix)
CANONICAL_TO_LEGACY_PREFIX = {
    "FEATURE-": "FEAT-",
    "STORY-": "US-",
    "TASK-": "TK-",
    "RELEASE-": "REL-",
    "EVOLUTION-": "EVOL-",
}


class CanonicalIdService:
    """Service governing canonical ID lifecycle, normalization, validation, and generation."""

    @staticmethod
    def normalize(raw_id: str) -> str:
        """Normalizes legacy aliases (FEAT-, US-, TK-, REL-, EVOL-) to canonical prefixes.

        Idempotent: passing an already canonical identifier returns it unchanged.
        """
        if not raw_id or not isinstance(raw_id, str):
            raise ValidationError("WorkItem ID must be a non-empty string")

        trimmed = raw_id.strip()
        for legacy_prefix, canonical_prefix in LEGACY_PREFIX_MAP.items():
            if trimmed.startswith(legacy_prefix):
                return canonical_prefix + trimmed[len(legacy_prefix):]
        return trimmed

    @staticmethod
    def to_legacy_alias(canonical_or_raw_id: str) -> Optional[str]:
        """Returns the legacy alias representation of an ID if one exists, or None.

        Used for fallback lookups on disk where legacy directories may exist.
        """
        if not canonical_or_raw_id or not isinstance(canonical_or_raw_id, str):
            return None
        trimmed = canonical_or_raw_id.strip()
        for canonical_prefix, legacy_prefix in CANONICAL_TO_LEGACY_PREFIX.items():
            if trimmed.startswith(canonical_prefix):
                return legacy_prefix + trimmed[len(canonical_prefix):]
        return None

    @staticmethod
    def is_legacy_alias(raw_id: str) -> bool:
        """Returns True if the given ID uses a legacy prefix."""
        if not raw_id or not isinstance(raw_id, str):
            return False
        trimmed = raw_id.strip()
        return any(trimmed.startswith(prefix) for prefix in LEGACY_PREFIX_MAP)

    @classmethod
    def validate(cls, canonical_id: str) -> bool:
        """Validates strict canonical ID grammar and minimum digit constraints."""
        if not canonical_id or not isinstance(canonical_id, str):
            return False
        trimmed = canonical_id.strip()
        return bool(
            RE_CANONICAL_EPIC.match(trimmed)
            or RE_CANONICAL_FEATURE.match(trimmed)
            or RE_CANONICAL_STORY.match(trimmed)
            or RE_CANONICAL_TASK.match(trimmed)
            or RE_CANONICAL_OPERATIONAL.match(trimmed)
        )

    @classmethod
    def validate_permissive(cls, raw_id: str) -> bool:
        """Validates that an identifier matches either canonical or supported legacy formats."""
        if not raw_id or not isinstance(raw_id, str):
            return False
        trimmed = raw_id.strip()
        return bool(RE_PERMISSIVE_ANY.match(trimmed))

    @classmethod
    def infer_kind(cls, raw_or_canonical_id: str) -> WorkItemKind:
        """Infers WorkItemKind from ID prefix or kind name, applying normalization first."""
        if not raw_or_canonical_id or not isinstance(raw_or_canonical_id, str):
            raise ValidationError("WorkItem ID or kind name must be a non-empty string")
        norm_id = cls.normalize(raw_or_canonical_id).strip()
        upper = norm_id.upper()

        if upper in {"EPIC"} or norm_id.startswith("EPIC-"):
            return WorkItemKind.EPIC
        if upper in {"FEATURE", "FEAT"} or norm_id.startswith("FEATURE-") or norm_id.startswith("FEAT-"):
            return WorkItemKind.FEATURE
        if upper in {"STORY", "US", "PBI"} or norm_id.startswith("STORY-") or norm_id.startswith("US-"):
            return WorkItemKind.STORY
        if upper in {"TASK", "TK"} or norm_id.startswith("TASK-") or norm_id.startswith("TK-"):
            return WorkItemKind.TASK
        if upper in {"BUG"} or norm_id.startswith("BUG-"):
            return WorkItemKind.BUG
        if upper in {"SPIKE", "STUDY"} or norm_id.startswith("SPIKE-") or norm_id.startswith("STUDY-"):
            return WorkItemKind.SPIKE
        if upper in {"INCIDENT"} or norm_id.startswith("INCIDENT-"):
            return WorkItemKind.INCIDENT
        if upper in {"RELEASE", "REL"} or norm_id.startswith("RELEASE-") or norm_id.startswith("REL-"):
            return WorkItemKind.RELEASE
        if upper in {"SETUP", "PROJECT_SETUP"} or norm_id.startswith("SETUP-"):
            return WorkItemKind.PROJECT_SETUP
        if upper in {"EVOLUTION", "EVOL"} or norm_id.startswith("EVOLUTION-") or norm_id.startswith("EVOL-"):
            return WorkItemKind.EPIC
        raise ValidationError(f"Cannot infer WorkItemKind from ID: '{raw_or_canonical_id}'")

    @classmethod
    def format_canonical_id(cls, kind: Union[WorkItemKind, str], sequence_number: int) -> str:
        """Formats a strictly canonical identifier for a new item.

        Never emits legacy prefixes (FEAT-, US-, TK-).
        """
        if sequence_number < 1:
            raise ValidationError(f"Sequence number must be >= 1, got {sequence_number}")

        if isinstance(kind, str):
            kind_str = kind.upper().strip()
        else:
            kind_str = kind.value.upper()

        if kind_str == "TASK":
            return f"TASK-{sequence_number:04d}"
        elif kind_str in {"EPIC", "FEATURE", "STORY", "BUG", "SPIKE", "INCIDENT", "RELEASE", "SETUP", "EVOLUTION"}:
            prefix = "FEATURE" if kind_str == "FEATURE" else ("STORY" if kind_str == "STORY" else kind_str)
            return f"{prefix}-{sequence_number:03d}"
        else:
            raise ValidationError(f"Unsupported WorkItem kind for formatting: '{kind}'")
