"""Deterministic Mathematical Retry and Exponential Backoff Policies.

Strictly stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from typing import Optional


@dataclass(frozen=True)
class RetryPolicy:
    """Configurable exponential backoff and retry policy."""

    max_attempts: int = 3
    base_backoff_seconds: float = 1.0
    cap_backoff_seconds: float = 300.0
    multiplier: float = 2.0
    enable_jitter: bool = False

    def calculate_delay(self, attempt: int, entity_id: Optional[str] = None) -> float:
        """Calculates deterministic delay in seconds for a given attempt count (0-indexed)."""
        if attempt < 0:
            attempt = 0
        raw_delay = self.base_backoff_seconds * (self.multiplier ** attempt)
        capped_delay = min(self.cap_backoff_seconds, raw_delay)

        if not self.enable_jitter or not entity_id:
            return capped_delay

        # Deterministic pseudo-jitter seeded by entity_id + attempt to avoid thundering herds
        # while keeping tests 100% reproducible
        seed = f"{entity_id}:{attempt}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        jitter_factor = (int(digest[:8], 16) % 1000) / 1000.0  # 0.0 to 0.999
        # Jitter scales delay between 0.8x and 1.2x of capped_delay
        jittered_delay = capped_delay * (0.8 + 0.4 * jitter_factor)
        return min(self.cap_backoff_seconds, jittered_delay)

    def next_due_time(self, current_time: datetime, attempt: int, entity_id: Optional[str] = None) -> datetime:
        """Returns the next due timestamp given the current time and attempt count."""
        delay = self.calculate_delay(attempt, entity_id)
        return current_time + timedelta(seconds=delay)

    def is_retryable(self, attempt: int) -> bool:
        """Determines if another attempt is permitted given current attempt count (0-indexed)."""
        return attempt < self.max_attempts
