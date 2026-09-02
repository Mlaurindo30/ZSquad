"""Primitivas de I/O durável para artefatos governados do Agents Squad."""
from __future__ import annotations

import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class LockTimeoutError(TimeoutError):
    """Indica que um lock de artefato não foi obtido no prazo configurado."""


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Grava texto por substituição atômica usando um temporário no mesmo diretório."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(directory: Path) -> None:
    """Persiste a entrada renomeada quando a plataforma permite fsync de diretório."""
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def file_lock(path: Path, *, timeout: float = 30.0, poll_interval: float = 0.05) -> Iterator[None]:
    """Obtém um lock exclusivo entre processos e o libera ao sair do contexto."""
    if timeout < 0:
        raise ValueError("timeout não pode ser negativo")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                _try_lock(handle)
                break
            except (BlockingIOError, OSError) as exc:
                if not _lock_is_busy(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(f"timeout aguardando lock: {path}") from exc
                time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()


def _try_lock(handle: object) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: object) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _lock_is_busy(exc: OSError) -> bool:
    if isinstance(exc, BlockingIOError):
        return True
    if os.name == "nt":
        return getattr(exc, "winerror", None) in {33, 36} or exc.errno in {13}
    return exc.errno in {11, 13}
