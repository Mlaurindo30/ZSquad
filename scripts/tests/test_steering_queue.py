import threading
from typing import Any, Dict, List, Optional

import pytest

from scripts.steering_queue import (
    PendingMessageQueue,
    QueuedMessage,
    SteeringManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_msg(role: str = "user", content: str = "hello", metadata: Optional[Dict[str, Any]] = None) -> QueuedMessage:
    return QueuedMessage(role=role, content=content, queued_at="2026-01-01T00:00:00+0000", metadata=metadata or {})


# ---------------------------------------------------------------------------
# PendingMessageQueue
# ---------------------------------------------------------------------------


class TestPendingMessageQueue:
    def test_empty_on_init(self):
        q = PendingMessageQueue()
        assert len(q) == 0
        assert q.peek() is None
        assert q.drain() == []

    def test_enqueue_and_dequeue_fifo(self):
        q = PendingMessageQueue()
        q.enqueue(make_msg(content="a"))
        q.enqueue(make_msg(content="b"))
        assert len(q) == 2
        first = q.dequeue()
        assert first is not None
        assert first.content == "a"
        assert len(q) == 1
        second = q.dequeue()
        assert second is not None
        assert second.content == "b"
        assert q.dequeue() is None

    def test_drain_clears_queue(self):
        q = PendingMessageQueue()
        q.enqueue(make_msg(content="x"))
        q.enqueue(make_msg(content="y"))
        items = q.drain()
        assert len(items) == 2
        assert len(q) == 0
        assert q.peek() is None

    def test_peek_does_not_remove(self):
        q = PendingMessageQueue()
        q.enqueue(make_msg(content="only"))
        assert q.peek().content == "only"
        assert len(q) == 1

    def test_thread_safety(self):
        q = PendingMessageQueue()
        errors: List[Exception] = []

        def producer():
            try:
                for i in range(100):
                    q.enqueue(make_msg(content=f"msg-{i}"))
            except Exception as exc:
                errors.append(exc)

        def consumer():
            try:
                for _ in range(100):
                    q.dequeue()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=producer), threading.Thread(target=consumer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


# ---------------------------------------------------------------------------
# SteeringManager
# ---------------------------------------------------------------------------


class TestSteeringManager:
    def test_empty_on_init(self):
        mgr = SteeringManager()
        assert mgr.pending_count() == 0
        assert mgr.poll() == []

    def test_enqueue_steering(self):
        mgr = SteeringManager()
        mgr.enqueue_steering("user", "mude X para Y")
        assert mgr.pending_count() == 1
        msgs = mgr.poll()
        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert msgs[0].content == "mude X para Y"

    def test_enqueue_follow_up(self):
        mgr = SteeringManager()
        mgr.enqueue_follow_up("system", "continuar com Z")
        assert mgr.pending_count() == 1
        msgs = mgr.poll()
        assert len(msgs) == 1
        assert msgs[0].role == "system"
        assert msgs[0].content == "continuar com Z"

    def test_poll_returns_steering_then_follow_up(self):
        mgr = SteeringManager()
        mgr.enqueue_follow_up("system", "follow-up")
        mgr.enqueue_steering("user", "steer")
        msgs = mgr.poll()
        assert [m.role for m in msgs] == ["user", "system"]

    def test_poll_clears_queues(self):
        mgr = SteeringManager()
        mgr.enqueue_steering("user", "a")
        mgr.enqueue_follow_up("system", "b")
        assert mgr.pending_count() == 2
        mgr.poll()
        assert mgr.pending_count() == 0

    def test_multiple_polls_drain_all(self):
        mgr = SteeringManager()
        mgr.enqueue_steering("user", "a")
        mgr.enqueue_steering("user", "b")
        first = mgr.poll()
        assert len(first) == 2
        assert mgr.pending_count() == 0

    def test_metadata_propagated(self):
        mgr = SteeringManager()
        mgr.enqueue_steering("user", "muda", metadata={"source": "cli"})
        msgs = mgr.poll()
        assert msgs[0].metadata == {"source": "cli"}

    def test_clear(self):
        mgr = SteeringManager()
        mgr.enqueue_steering("user", "a")
        mgr.enqueue_follow_up("system", "b")
        mgr.clear()
        assert mgr.pending_count() == 0
        assert mgr.poll() == []

    def test_thread_safety(self):
        mgr = SteeringManager()
        errors: List[Exception] = []

        def producer():
            try:
                for i in range(100):
                    mgr.enqueue_steering("user", f"s-{i}")
                    mgr.enqueue_follow_up("system", f"f-{i}")
            except Exception as exc:
                errors.append(exc)

        def consumer():
            try:
                for _ in range(100):
                    mgr.poll()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=producer), threading.Thread(target=consumer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
