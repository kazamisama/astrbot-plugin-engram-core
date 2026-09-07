"""Shared bounded runner for synchronous provider calls (v1.76.15).

The previous implementation started one fresh daemon thread per call and,
on timeout, detached it. Repeated timeouts therefore leaked an unbounded
number of threads. This runner keeps a fixed semaphore: at most N provider
calls may be in flight; when all slots are occupied by hung calls, further
calls fail fast instead of creating yet another thread.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

MAX_IN_FLIGHT = 8

_slots = threading.BoundedSemaphore(MAX_IN_FLIGHT)


def bounded_sync_call(fn: Callable[..., Any], args: tuple,
                      timeout: float) -> Any:
    """Run `fn(*args)` on a bounded daemon thread and wait up to *timeout*.

    On timeout the worker is detached (the slot is released only when the
    worker eventually returns), and RuntimeError is raised.
    """
    if not _slots.acquire(timeout=max(0.05, float(timeout))):
        raise RuntimeError(
            f"sync provider call rejected: all {MAX_IN_FLIGHT} worker slots "
            "are busy (previous calls hung?)")

    box: dict = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            try:
                box["out"] = fn(*args)
            except BaseException as ex:  # re-raised in caller
                box["err"] = ex
            finally:
                done.set()
        finally:
            _slots.release()

    thread = threading.Thread(target=_worker, daemon=True,
                              name="hippocampus-bounded-sync")
    thread.start()
    if not done.wait(timeout):
        raise RuntimeError(
            f"sync provider call timed out after {timeout}s "
            "(detached; provider hung?)")
    if "err" in box:
        raise box["err"]
    return box["out"]
