"""Cryptographic evidence hashing and validation utilities.

Strictly stdlib-only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Union

from scripts.domain.common import canonical_json
from scripts.runtime.execution.errors import InvalidEvidenceError


def hash_evidence_payload(payload: Union[Dict[str, Any], str, bytes]) -> str:
    """Computes SHA-256 deterministic hash of an evidence payload.

    Args:
        payload: Dict (serialized canonically), string, or bytes.

    Returns:
        Hexadecimal SHA-256 string.
    """
    if isinstance(payload, dict):
        raw_bytes = canonical_json(payload).encode("utf-8")
    elif isinstance(payload, str):
        # Normalize line endings to avoid OS-specific differences
        normalized_str = payload.replace("\r\n", "\n")
        raw_bytes = normalized_str.encode("utf-8")
    elif isinstance(payload, bytes):
        raw_bytes = payload
    else:
        raise InvalidEvidenceError(f"Unsupported evidence payload type: {type(payload)}")

    return hashlib.sha256(raw_bytes).hexdigest()


def hash_file(file_path: Union[str, Path]) -> str:
    """Computes SHA-256 of a file on disk."""
    p = Path(file_path)
    if not p.is_file():
        raise InvalidEvidenceError(f"Evidence file not found: {p}")
    try:
        data = p.read_bytes()
        return hashlib.sha256(data).hexdigest()
    except Exception as exc:
        raise InvalidEvidenceError(f"Failed to read evidence file {p}: {exc}") from exc


def verify_evidence_hash(payload: Union[Dict[str, Any], str, bytes], expected_hash: str) -> bool:
    """Verifies that payload produces expected_hash."""
    actual_hash = hash_evidence_payload(payload)
    return actual_hash.lower() == expected_hash.strip().lower()
