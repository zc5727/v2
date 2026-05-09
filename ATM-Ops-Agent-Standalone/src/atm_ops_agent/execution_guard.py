from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Iterator


class ExecutionGuard:
    def __init__(self) -> None:
        self._meta_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    @contextmanager
    def acquire(self, scope: str) -> Iterator[None]:
        with self._meta_lock:
            lock = self._locks.setdefault(scope, threading.Lock())
        acquired = lock.acquire(blocking=False)
        if not acquired:
            raise RuntimeError(f"another guarded execution is already running for scope: {scope}")
        try:
            yield
        finally:
            lock.release()
