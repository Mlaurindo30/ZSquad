"""Semantic Query-Before-Create (QBC) Engine and Lexical Deduplication Matcher.

Strictly stdlib-only.
Implements the Tripartite QBC Funnel:
1. SQLite Fast-Index (work_item_lifecycle_state, bindings, plan_items)
2. Local Filesystem (work/<project_id>/)
3. Remote Azure Boards (WIQL / Board Cards query port)

Provides Parent-Context Scoped Semantic Matching (Jaccard + Levenshtein + Tri-Gram)
with fail-closed thresholds:
- Score < 0.70: PASSED (CLEAR)
- 0.70 <= Score < 0.85: AMBIGUITY_DETECTED (REVIEW_REQUIRED)
- Score >= 0.85: DUPLICATE_REJECTED (CERTAIN DUPLICATE)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

import yaml

from scripts.domain.backlog import BacklogPlanItem
from scripts.domain.common import ValidationError, canonical_json
from scripts.domain.work_items import WorkItemId, WorkItemKind
from scripts.runtime.work_items.paths import WorkItemPathResolver

logger = logging.getLogger(__name__)

# Standard stop words in PT and EN for token normalization
STOP_WORDS: Set[str] = {
    # English
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", "at",
    "by", "for", "with", "about", "against", "between", "into", "through", "during",
    "before", "after", "above", "below", "to", "from", "up", "down", "in", "out",
    "on", "off", "over", "under", "again", "further", "then", "once", "here", "there",
    "all", "any", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "can",
    "will", "just", "should", "now", "is", "are", "was", "were", "be", "been", "being",
    # Portuguese
    "o", "a", "os", "as", "um", "uma", "uns", "umas", "e", "ou", "mas", "se",
    "entao", "quando", "em", "no", "na", "nos", "nas", "por", "pelo", "pela", "pelos",
    "pelas", "para", "com", "sem", "sobre", "entre", "ate", "desde", "contra",
    "de", "do", "da", "dos", "das", "dum", "duma", "duns", "dumas", "ao", "aos",
    "aqui", "ali", "la", "como", "onde", "quem", "qual", "que", "isso", "isto",
    "aquilo", "este", "esta", "estes", "estas", "esse", "essa", "esses", "essas",
    "aquele", "aquela", "aqueles", "aquelas", "seu", "sua", "seus", "suas", "meu",
    "minha", "meus", "minhas", "teu", "tua", "teus", "tuas", "nosso", "nossa",
}


def normalize_text(text: str) -> str:
    """Normalizes text by lowercasing and replacing non-alphanumeric chars with spaces."""
    if not text:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(cleaned.split())


def extract_tokens(text: str) -> Set[str]:
    """Extracts lowercase tokens with stop words stripped."""
    norm = normalize_text(text)
    return {w for w in norm.split() if len(w) > 1 and w not in STOP_WORDS}


def compute_jaccard_similarity(tokens_a: Set[str], tokens_b: Set[str]) -> float:
    """Computes Token Jaccard Similarity between two sets of tokens."""
    if not tokens_a and not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    intersection = tokens_a & tokens_b
    return len(intersection) / len(union)


def compute_levenshtein_ratio(s1: str, s2: str) -> float:
    """Computes normalized Levenshtein string similarity ratio in stdlib."""
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0

    len1, len2 = len(s1), len(s2)
    # Memory efficient O(min(m, n)) distance matrix
    if len1 > len2:
        s1, s2 = s2, s1
        len1, len2 = len2, len1

    current_row = list(range(len1 + 1))
    for i in range(1, len2 + 1):
        previous_row = current_row
        current_row = [i] + [0] * len1
        for j in range(1, len1 + 1):
            add = previous_row[j] + 1
            delete = current_row[j - 1] + 1
            change = previous_row[j - 1]
            if s1[j - 1] != s2[i - 1]:
                change += 1
            current_row[j] = min(add, delete, change)

    distance = current_row[len1]
    max_len = max(len(s1), len(s2))
    return max(0.0, 1.0 - (distance / max_len))


def extract_trigrams(text: str) -> Set[str]:
    """Extracts 3-gram character sequences from normalized text."""
    norm = normalize_text(text).replace(" ", "")
    if len(norm) < 3:
        return {norm} if norm else set()
    return {norm[i : i + 3] for i in range(len(norm) - 2)}


def compute_trigram_overlap(text_a: str, text_b: str) -> float:
    """Computes Tri-Gram character overlap (Sørensen-Dice coefficient)."""
    tri_a = extract_trigrams(text_a)
    tri_b = extract_trigrams(text_b)
    if not tri_a and not tri_b:
        return 0.0
    total = len(tri_a) + len(tri_b)
    if total == 0:
        return 0.0
    common = len(tri_a & tri_b)
    return (2.0 * common) / total


def compute_composite_similarity(title_a: str, title_b: str) -> float:
    """Computes weighted multi-factor composite similarity S(A, B).
    
    Weights: 0.50 * Jaccard + 0.25 * Levenshtein + 0.25 * Tri-Gram
    """
    tokens_a = extract_tokens(title_a)
    tokens_b = extract_tokens(title_b)

    jaccard = compute_jaccard_similarity(tokens_a, tokens_b)
    levenshtein = compute_levenshtein_ratio(normalize_text(title_a), normalize_text(title_b))
    trigram = compute_trigram_overlap(title_a, title_b)

    return (0.50 * jaccard) + (0.25 * levenshtein) + (0.25 * trigram)


@dataclass(frozen=True)
class ExistingItemContext:
    """Represents an existing work item discovered from any QBC source."""

    source: str  # SQLITE, FILESYSTEM, AZURE_BOARDS, IN_FLIGHT_PLAN
    canonical_id: str
    kind: WorkItemKind
    title: str
    parent_id: Optional[str] = None
    ado_id: Optional[int] = None


@dataclass(frozen=True)
class QbcMatchResult:
    """Result of a QBC evaluation for a proposed work item."""

    proposed_id: str
    kind: WorkItemKind
    title: str
    parent_id: Optional[str]
    decision_status: str  # PASSED, AMBIGUITY_DETECTED, DUPLICATE_REJECTED, OVERRIDDEN
    similarity_score: float
    matched_id: Optional[str] = None
    matched_source: str = "NONE"
    matched_title: Optional[str] = None
    rationale: str = ""

    @property
    def is_passed(self) -> bool:
        return self.decision_status in ("PASSED", "OVERRIDDEN")

    @property
    def is_ambiguous(self) -> bool:
        return self.decision_status == "AMBIGUITY_DETECTED"

    @property
    def is_duplicate(self) -> bool:
        return self.decision_status == "DUPLICATE_REJECTED"


class AzureBoardsQueryPort(ABC):
    """Port for querying remote Azure Boards work items for QBC."""

    @abstractmethod
    def query_work_items(
        self,
        project_name: str,
        area_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Returns list of remote items: [{'id': 123, 'type': 'User Story', 'title': '...', 'parent_id': 456}]"""
        ...


class SemanticQbcEngine:
    """Multi-source Query-Before-Create Engine with Parent-Scoped Semantic Deduplication."""

    THRESHOLD_CLEAR = 0.70
    THRESHOLD_DUPLICATE = 0.85

    def __init__(
        self,
        db_path: Optional[Union[str, Path, sqlite3.Connection]] = None,
        runtime_root: Optional[Union[str, Path]] = None,
        azure_query_port: Optional[AzureBoardsQueryPort] = None,
    ) -> None:
        self._external_conn: Optional[sqlite3.Connection] = None
        self._db_path: Optional[Path] = None
        self._is_memory = False

        if isinstance(db_path, sqlite3.Connection):
            self._external_conn = db_path
            self._is_memory = True
        elif db_path == ":memory:":
            self._is_memory = True
            self._external_conn = sqlite3.connect(":memory:", check_same_thread=False)
        elif db_path is not None:
            self._db_path = Path(db_path)
        else:
            root = Path(os.environ.get("SQUAD_RUNTIME", runtime_root or Path.cwd()))
            self._db_path = root / "banco" / "squad.db"

        self.runtime_root = Path(runtime_root or os.environ.get("SQUAD_RUNTIME", Path.cwd()))
        self.azure_query_port = azure_query_port

    def _get_connection(self) -> sqlite3.Connection:
        if self._external_conn is not None:
            return self._external_conn
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    @staticmethod
    def compute_fingerprint(kind: WorkItemKind, parent_id: Optional[str], title: str) -> str:
        """Computes SHA-256 fingerprint of normalized (kind || parent_id || title)."""
        norm_parent = (parent_id or "").strip()
        norm_title = normalize_text(title)
        payload = f"{kind.value}|{norm_parent}|{norm_title}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def discover_sqlite_items(self, project_id: str) -> List[ExistingItemContext]:
        """Discovers existing items from SQLite (lifecycle, bindings, and plan items)."""
        items: List[ExistingItemContext] = []
        conn = self._get_connection()
        should_close = (self._external_conn is None)
        try:
            cursor = conn.cursor()
            # Query bindings to get titles/parents stored in metadata
            try:
                cursor.execute(
                    "SELECT work_item_id, ado_id, metadata_json FROM delivery_work_item_bindings WHERE project_id = ?",
                    (project_id,),
                )
                for wid, ado_id, m_json in cursor.fetchall():
                    meta = json.loads(m_json) if m_json else {}
                    kind_val = meta.get("kind", "STORY")
                    title = meta.get("title", wid)
                    p_id = meta.get("parent_id")
                    try:
                        k = WorkItemKind(kind_val)
                    except Exception:
                        k = WorkItemId.infer_kind(wid)
                    items.append(
                        ExistingItemContext(
                            source="SQLITE",
                            canonical_id=WorkItemId.normalize(wid),
                            kind=k,
                            title=title,
                            parent_id=WorkItemId.normalize(p_id) if p_id else None,
                            ado_id=ado_id,
                        )
                    )
            except sqlite3.OperationalError:
                pass

            # Query approved/materialized backlog_plan_items
            try:
                cursor.execute(
                    """
                    SELECT bpi.proposed_id, bpi.canonical_id, bpi.kind, bpi.title, bpi.parent_id
                    FROM backlog_plan_items bpi
                    JOIN backlog_plans bp ON bpi.plan_id = bp.plan_id
                    WHERE bp.project_id = ? AND bp.status IN ('VALIDATED', 'APPROVED', 'MATERIALIZED')
                    """,
                    (project_id,),
                )
                for pid, cid, kind_val, title, parent_id in cursor.fetchall():
                    chosen_id = cid or pid
                    try:
                        k = WorkItemKind(kind_val)
                    except Exception:
                        k = WorkItemId.infer_kind(chosen_id)
                    items.append(
                        ExistingItemContext(
                            source="SQLITE",
                            canonical_id=WorkItemId.normalize(chosen_id),
                            kind=k,
                            title=title,
                            parent_id=WorkItemId.normalize(parent_id) if parent_id else None,
                        )
                    )
            except sqlite3.OperationalError:
                pass

        finally:
            if should_close:
                conn.close()

        return items

    def discover_filesystem_items(self, project_id: str) -> List[ExistingItemContext]:
        """Discovers existing items from work/<project_id>/ by reading status.yaml."""
        items: List[ExistingItemContext] = []
        project_work_dir = self.runtime_root / "work" / project_id
        if not project_work_dir.is_dir():
            return items

        for status_path in project_work_dir.glob("**/status.yaml"):
            try:
                content = status_path.read_text(encoding="utf-8")
                data = yaml.safe_load(content) or {}
                raw_id = status_path.parent.name
                norm_id = WorkItemId.normalize(raw_id)
                type_str = str(data.get("type", "")).upper()
                try:
                    kind = WorkItemKind(type_str)
                except Exception:
                    kind = WorkItemId.infer_kind(norm_id)

                title = data.get("title", norm_id)
                parent_id = data.get("parent_id")
                devops_id = data.get("devops_id")

                items.append(
                    ExistingItemContext(
                        source="FILESYSTEM",
                        canonical_id=norm_id,
                        kind=kind,
                        title=str(title),
                        parent_id=WorkItemId.normalize(str(parent_id)) if parent_id else None,
                        ado_id=int(devops_id) if devops_id and str(devops_id).isdigit() else None,
                    )
                )
            except Exception as e:
                logger.debug(f"Failed to read status.yaml from {status_path}: {e}")

        return items

    def discover_azure_items(self, project_id: str) -> List[ExistingItemContext]:
        """Discovers existing remote cards from Azure DevOps via AzureBoardsQueryPort."""
        if self.azure_query_port is None:
            return []

        remote_raw = self.azure_query_port.query_work_items(project_name=project_id)
        items: List[ExistingItemContext] = []
        for r in remote_raw:
            ado_id = r.get("id")
            title = r.get("title", "")
            type_str = r.get("type", "").upper()
            parent_id = r.get("parent_id")

            # Map Azure type to WorkItemKind
            kind = WorkItemKind.STORY
            if "EPIC" in type_str:
                kind = WorkItemKind.EPIC
            elif "FEATURE" in type_str:
                kind = WorkItemKind.FEATURE
            elif "TASK" in type_str:
                kind = WorkItemKind.TASK
            elif "BUG" in type_str:
                kind = WorkItemKind.BUG

            items.append(
                ExistingItemContext(
                    source="AZURE_BOARDS",
                    canonical_id=f"ADO-{ado_id}",
                    kind=kind,
                    title=title,
                    parent_id=str(parent_id) if parent_id else None,
                    ado_id=ado_id,
                )
            )
        return items

    def evaluate_item(
        self,
        project_id: str,
        item: BacklogPlanItem,
        in_flight_items: Optional[List[BacklogPlanItem]] = None,
    ) -> QbcMatchResult:
        """Evaluates a proposed item against SQLite, Filesystem, Azure Boards, and in-flight items."""
        norm_proposed_id = WorkItemId.normalize(item.proposed_id)
        norm_parent_id = WorkItemId.normalize(item.parent_id) if item.parent_id else None
        item_fp = self.compute_fingerprint(item.kind, norm_parent_id, item.title)

        # Collect all existing items across 3 sources + in-flight
        existing_items: List[ExistingItemContext] = []
        existing_items.extend(self.discover_sqlite_items(project_id))
        existing_items.extend(self.discover_filesystem_items(project_id))
        existing_items.extend(self.discover_azure_items(project_id))

        if in_flight_items:
            for if_item in in_flight_items:
                if if_item.proposed_id != item.proposed_id:
                    existing_items.append(
                        ExistingItemContext(
                            source="IN_FLIGHT_PLAN",
                            canonical_id=WorkItemId.normalize(if_item.proposed_id),
                            kind=if_item.kind,
                            title=if_item.title,
                            parent_id=WorkItemId.normalize(if_item.parent_id) if if_item.parent_id else None,
                        )
                    )

        # 1. Exact Matching Phase
        for exist in existing_items:
            # 1a. Canonical ID match
            if exist.canonical_id == norm_proposed_id:
                return QbcMatchResult(
                    proposed_id=item.proposed_id,
                    kind=item.kind,
                    title=item.title,
                    parent_id=item.parent_id,
                    decision_status="DUPLICATE_REJECTED",
                    similarity_score=1.0,
                    matched_id=exist.canonical_id,
                    matched_source=exist.source,
                    matched_title=exist.title,
                    rationale=f"Exact canonical ID match '{exist.canonical_id}' detected in {exist.source}.",
                )

            # 1b. Exact scope fingerprint match
            exist_fp = self.compute_fingerprint(exist.kind, exist.parent_id, exist.title)
            if exist_fp == item_fp:
                return QbcMatchResult(
                    proposed_id=item.proposed_id,
                    kind=item.kind,
                    title=item.title,
                    parent_id=item.parent_id,
                    decision_status="DUPLICATE_REJECTED",
                    similarity_score=1.0,
                    matched_id=exist.canonical_id,
                    matched_source=exist.source,
                    matched_title=exist.title,
                    rationale=f"Exact scope fingerprint match '{exist.title}' detected in {exist.source}.",
                )

        # 2. Parent-Scoped Semantic Matching Phase (Seção 13)
        highest_score = 0.0
        best_match: Optional[ExistingItemContext] = None

        for exist in existing_items:
            # Same kind only
            if exist.kind != item.kind:
                continue

            # Scope Invariant Check:
            # - EPIC: global project comparison
            # - FEATURE: must have same parent EPIC
            # - STORY: must have same parent FEATURE
            # - TASK: must have same parent STORY
            if item.kind == WorkItemKind.EPIC:
                in_same_scope = True
            else:
                in_same_scope = (exist.parent_id == norm_parent_id)

            if not in_same_scope:
                continue

            sim = compute_composite_similarity(item.title, exist.title)
            if sim > highest_score:
                highest_score = sim
                best_match = exist

        # 3. Threshold Decision Matrix (Seção 14)
        if highest_score >= self.THRESHOLD_DUPLICATE:
            return QbcMatchResult(
                proposed_id=item.proposed_id,
                kind=item.kind,
                title=item.title,
                parent_id=item.parent_id,
                decision_status="DUPLICATE_REJECTED",
                similarity_score=round(highest_score, 4),
                matched_id=best_match.canonical_id if best_match else None,
                matched_source=best_match.source if best_match else "NONE",
                matched_title=best_match.title if best_match else None,
                rationale=(
                    f"Composite similarity ({highest_score:.2f}) >= {self.THRESHOLD_DUPLICATE} "
                    f"with '{best_match.title if best_match else ''}' in {best_match.source if best_match else ''}."
                ),
            )

        if highest_score >= self.THRESHOLD_CLEAR:
            return QbcMatchResult(
                proposed_id=item.proposed_id,
                kind=item.kind,
                title=item.title,
                parent_id=item.parent_id,
                decision_status="AMBIGUITY_DETECTED",
                similarity_score=round(highest_score, 4),
                matched_id=best_match.canonical_id if best_match else None,
                matched_source=best_match.source if best_match else "NONE",
                matched_title=best_match.title if best_match else None,
                rationale=(
                    f"Ambiguity detected: composite similarity ({highest_score:.2f}) in "
                    f"[{self.THRESHOLD_CLEAR}, {self.THRESHOLD_DUPLICATE}) with "
                    f"'{best_match.title if best_match else ''}' in {best_match.source if best_match else ''}. "
                    f"Requires REVIEW_REQUIRED resolution."
                ),
            )

        return QbcMatchResult(
            proposed_id=item.proposed_id,
            kind=item.kind,
            title=item.title,
            parent_id=item.parent_id,
            decision_status="PASSED",
            similarity_score=round(highest_score, 4),
            matched_id=best_match.canonical_id if best_match and highest_score > 0 else None,
            matched_source=best_match.source if best_match and highest_score > 0 else "NONE",
            matched_title=best_match.title if best_match and highest_score > 0 else None,
            rationale="QBC passed. No blocking duplicates or ambiguities detected.",
        )
