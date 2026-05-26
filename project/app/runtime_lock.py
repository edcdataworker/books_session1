"""Verrou d'execution single-instance pour eviter les runs concurrents."""

from __future__ import annotations

import os
from pathlib import Path


class LockAcquisitionError(RuntimeError):
    """Le lock est deja pris par un autre processus."""


class RuntimeLock:
    def __init__(self, lock_path: Path):
        self._lock_path = lock_path
        self._fh = None

    def __enter__(self) -> "RuntimeLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._lock_path.open("a+", encoding="utf-8")

        try:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LockAcquisitionError(
                f"Lock deja pris: {self._lock_path}"
            ) from error

        self._fh.seek(0)
        self._fh.truncate(0)
        self._fh.write(f"pid={os.getpid()}\n")
        self._fh.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._fh is None:
            return

        try:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
