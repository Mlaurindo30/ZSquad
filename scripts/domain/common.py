"""Common domain primitives, canonical serialization, hashing and base models.

Strictly stdlib-only.
"""

from abc import ABC
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Set, Union


class ValidationError(ValueError):
    """Raised when a domain invariant or validation rule is violated."""
    pass


class SchemaVersion:
    """Canonical schema versioning constant."""
    V1_0_0 = "1.0.0"
    CURRENT = "1.0.0"


def _canonical_normalizer(obj: Any) -> Any:
    """Recursively normalizes Python objects into JSON-serializable structures deterministically."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        return sorted([_canonical_normalizer(item) for item in obj], key=lambda x: str(x))
    if isinstance(obj, (list, tuple)):
        return [_canonical_normalizer(item) for item in obj]
    if isinstance(obj, dict):
        return {
            str(k): _canonical_normalizer(v)
            for k, v in sorted(obj.items(), key=lambda item: str(item[0]))
        }
    if is_dataclass(obj) and not isinstance(obj, type):
        return _canonical_normalizer(asdict(obj))
    return obj


def canonical_json(data: Any) -> str:
    """Serializes domain data to deterministic, canonical JSON string.

    Rules:
    - Keys are lexicographically sorted at all depths
    - Compact separators (no trailing or whitespace separators: ',', ':')
    - Enums converted to value
    - Datetimes converted to ISO 8601 strings
    - UTF-8 representation without non-ascii escaping
    """
    normalized = _canonical_normalizer(data)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_hash(data: Any) -> str:
    """Computes SHA-256 hex digest of deterministic canonical JSON representation."""
    serialized = canonical_json(data)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BaseDomainModel(ABC):
    """Base abstract domain model providing serialization and hashing primitives."""

    def to_dict(self) -> Dict[str, Any]:
        """Converts model to normalized dict."""
        return _canonical_normalizer(self)

    def to_json(self) -> str:
        """Serializes model to canonical JSON."""
        return canonical_json(self)

    def content_hash(self) -> str:
        """Calculates canonical content hash."""
        return canonical_hash(self)
