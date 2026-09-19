"""Modular runtime package for Agent Squad Backlog Planning, Semantic QBC, and Materialization.

Strictly stdlib-only.
Exposes the authoritative backlog repository, ID allocator, semantic QBC engine,
plan validator, materialization saga, and plan service.
"""

from .id_allocator import CanonicalIdAllocator
from .materializer import (
    BacklogMaterializer,
    MaterializationReceipt,
    MaterializedItemResult,
)
from .qbc import (
    AzureBoardsQueryPort,
    ExistingItemContext,
    QbcMatchResult,
    SemanticQbcEngine,
    compute_composite_similarity,
    compute_jaccard_similarity,
    compute_levenshtein_ratio,
    compute_trigram_overlap,
    normalize_text,
)
from .repository import (
    BacklogPlanRepository,
    QbcDecisionRecord,
)
from .service import BacklogPlanService
from .validator import (
    BacklogPlanValidator,
    ValidationResult,
)

__all__ = [
    # Repository
    "BacklogPlanRepository",
    "QbcDecisionRecord",
    # ID Allocator
    "CanonicalIdAllocator",
    # QBC & Semantic Matching
    "SemanticQbcEngine",
    "QbcMatchResult",
    "ExistingItemContext",
    "AzureBoardsQueryPort",
    "compute_composite_similarity",
    "compute_jaccard_similarity",
    "compute_levenshtein_ratio",
    "compute_trigram_overlap",
    "normalize_text",
    # Validator
    "BacklogPlanValidator",
    "ValidationResult",
    # Materializer
    "BacklogMaterializer",
    "MaterializationReceipt",
    "MaterializedItemResult",
    # Service Facade
    "BacklogPlanService",
]
