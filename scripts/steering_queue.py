"""Steering/follow-up queues inspiradas no Prime Agent.

- ``steering_queue``: mensagens do usuário durante execução (steer).
- ``follow_up_queue``: tarefas derivadas de handoffs/next_gate.

Uso:
    from scripts.steering_queue import SteeringManager

    mgr = SteeringManager()
    mgr.enqueue_steering({"role": "user", "content": "mude X para Y"})
    mgr.enqueue_follow_up({"role": "system", "content": "continuar com Z"})
    pending = mgr.poll(signal=None)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueuedMessage:
    role: str
    content: str
    queued_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fila
# ---------------------------------------------------------------------------


class PendingMessageQueue:
    """Fila thread-safe de mensagens pendentes."""

    def __init__(self) -> None:
        self._queue: List[QueuedMessage] = []
        self._lock = threading.Lock()

    def enqueue(self, message: QueuedMessage) -> None:
        """Adiciona uma instrução à fila de steering."""
        with self._lock:
            self._queue.append(message)

    def dequeue(self) -> Optional[QueuedMessage]:
        """Remove e retorna a próxima instrução da fila."""
        with self._lock:
            if not self._queue:
                return None
            return self._queue.pop(0)

    def drain(self) -> List[QueuedMessage]:
        """Remove e retorna todas as instruções pendentes."""
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
            return items

    def peek(self) -> Optional[QueuedMessage]:
        """Retorna as instruções pendentes sem removê-las."""
        with self._lock:
            if not self._queue:
                return None
            return self._queue[0]

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)


# ---------------------------------------------------------------------------
# Steering Manager
# ---------------------------------------------------------------------------


class SteeringManager:
    """Gerencia filas de steering + follow-up.

    ``steering_queue``: mensagens do usuário durante execução.
    ``follow_up_queue``: tarefas derivadas de handoffs/gates.
    """

    def __init__(self) -> None:
        self.steering_queue = PendingMessageQueue()
        self.follow_up_queue = PendingMessageQueue()
        self._last_poll_at: Optional[float] = None

    def enqueue_steering(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Adiciona uma instrução global à fila de steering."""
        self.steering_queue.enqueue(QueuedMessage(
            role=role,
            content=content,
            queued_at=_now(),
            metadata=metadata or {},
        ))

    def enqueue_follow_up(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Adiciona um acompanhamento global à fila."""
        self.follow_up_queue.enqueue(QueuedMessage(
            role=role,
            content=content,
            queued_at=_now(),
            metadata=metadata or {},
        ))

    def poll(self, signal: Optional[threading.Event] = None) -> List[QueuedMessage]:
        """Retorna mensagens de steering + follow-up pendentes.

        A ordem é: steering primeiro, depois follow-up.
        """
        steering = self.steering_queue.drain()
        follow_up = self.follow_up_queue.drain()
        self._last_poll_at = time.time()
        return steering + follow_up

    def pending_count(self) -> int:
        """Retorna a quantidade de instruções globais pendentes."""
        return len(self.steering_queue) + len(self.follow_up_queue)

    def clear(self) -> None:
        """Remove todas as instruções globais pendentes."""
        self.steering_queue.drain()
        self.follow_up_queue.drain()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
