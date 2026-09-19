"""Deterministic Clock and Time Abstraction for Operational Control Loop.

Strictly stdlib-only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional


class ClockPort(ABC):
    """Abstract clock interface allowing deterministic time injection."""

    @abstractmethod
    def now(self) -> datetime:
        """Returns the current UTC datetime."""
        raise NotImplementedError


class SystemClock(ClockPort):
    """Real-time system clock using UTC timezone."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class DeterministicClock(ClockPort):
    """Deterministic controllable clock for unit and integration testing."""

    def __init__(self, initial_time: Optional[datetime] = None) -> None:
        if initial_time is None:
            self._current_time = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
        else:
            if initial_time.tzinfo is None:
                self._current_time = initial_time.replace(tzinfo=timezone.utc)
            else:
                self._current_time = initial_time

    def now(self) -> datetime:
        return self._current_time

    def set_time(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            self._current_time = new_time.replace(tzinfo=timezone.utc)
        else:
            self._current_time = new_time

    def advance(self, delta: timedelta) -> datetime:
        self._current_time += delta
        return self._current_time

    def advance_seconds(self, seconds: float) -> datetime:
        return self.advance(timedelta(seconds=seconds))
