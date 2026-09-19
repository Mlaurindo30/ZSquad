"""Modular runtime package for Agent Squad Work Items, Canonical Paths, Hierarchy, and Artifacts.

Provides canonical identification, physical 4-tier nesting, non-destructive legacy fallback,
and level-specific artifact materialization. Strictly stdlib-only.
"""

from .hierarchy import HierarchyContextResolver, validate_parent_child
from .ids import CanonicalIdService
from .paths import WorkItemPathResolver, sanitize_slug
from .templates import (
    ArtifactContainmentViolation,
    ArtifactMaterializer,
    PROHIBITED_ARTIFACTS,
    REQUIRED_ARTIFACTS,
)

__all__ = [
    "CanonicalIdService",
    "WorkItemPathResolver",
    "HierarchyContextResolver",
    "ArtifactMaterializer",
    "ArtifactContainmentViolation",
    "validate_parent_child",
    "sanitize_slug",
    "REQUIRED_ARTIFACTS",
    "PROHIBITED_ARTIFACTS",
]
