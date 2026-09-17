"""Process-local health for the worker owned by the local run.py entrypoint."""
from __future__ import annotations

from subprocess import Popen

_managed = False
_worker: Popen | None = None


def track_worker(worker: Popen | None, *, managed: bool = True) -> None:
    global _managed, _worker
    _worker = worker
    _managed = managed


def worker_state() -> str:
    # An API started directly can use external workers. No local ownership is implied.
    if not _managed:
        return "external"
    return "running" if _worker is not None and _worker.poll() is None else "unavailable"
