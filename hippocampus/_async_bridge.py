"""Run a coroutine to completion from synchronous code.

The plugin layer (handlers/init.py) bridges AstrBot's *async* LLM /
embedding APIs into this package's *sync* provider interface
(EmbeddingProvider.embed / LLMProvider.chat). Those sync methods are
in turn invoked from AstrBot's async event handlers, i.e. on a thread
that already has a running event loop. Calling asyncio.run() or
loop.run_until_complete() there raises "event loop is already running".

run_sync() sidesteps that by always driving the coroutine on a private
background loop running in a dedicated daemon thread, then blocking the
caller until the result is ready. This works whether or not the caller
already has a running loop, and keeps a single reusable worker loop so
we do not spin up a thread per call.

v1.76.13 (2026-09-07, astrbot "judge replied but bot never answered"):
every run_sync call now carries a hard timeout by default. Previously
timeout defaulted to None = wait forever; a hung upstream call (e.g. an
embedding HTTP request whose provider has no timeout of its own) left
the calling thread blocked indefinitely. In the observed outage that
stalled the astrbot on_llm_request hook chain at engram inject_memory
for 36 minutes and dead-locked the whole event loop. On expiry the
underlying task is cancelled best-effort so later run_sync calls are
not queued behind a hung coroutine, and a RuntimeError is raised --
callers already treat any exception as "skip this op", never as a
reason to stall the LLM request.
"""
from __future__ import annotations
import asyncio
import threading
from concurrent.futures import TimeoutError as _FutTimeoutError
from typing import Any, Awaitable

# Hard caps for the sync<->async bridge (v1.76.13). Generous for a
# healthy host; they exist so a wedged upstream call degrades to
# "skip this op" instead of blocking the caller forever.
DEFAULT_SYNC_TIMEOUT: float = 20.0       # embedding / short ops
DEFAULT_LLM_SYNC_TIMEOUT: float = 60.0   # LLM bridge (summarizer/diary/consolidator)

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _ensure_worker() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    with _lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        loop = asyncio.new_event_loop()

        def _runner() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(
            target=_runner, name="hippocampus-async-bridge", daemon=True)
        thread.start()
        _loop, _thread = loop, thread
        return loop


def run_sync(awaitable: Awaitable[Any], *, timeout: float | None = None) -> Any:
    """Block until *awaitable* completes on the worker loop and return its result.

    timeout=None means DEFAULT_SYNC_TIMEOUT (v1.76.13; no longer "wait
    forever" -- pass a large value explicitly to opt out). On expiry the
    underlying task is cancelled (best-effort, so the worker loop can keep
    serving later calls) and a RuntimeError is raised.
    """
    loop = _ensure_worker()
    fut = asyncio.run_coroutine_threadsafe(_as_coro(awaitable), loop)
    effective = DEFAULT_SYNC_TIMEOUT if timeout is None else timeout
    try:
        return fut.result(timeout=effective)
    except _FutTimeoutError:
        # Unstick the worker loop: later run_sync calls must not queue
        # behind a hung coroutine. Only helps when the coroutine is
        # awaiting (cancellation lands at the next await point); a
        # sync-blocked bridge fn would stay wedged, but the per-call
        # timeout above still releases *our* caller.
        fut.cancel()
        raise RuntimeError(
            f"run_sync timed out after {effective}s "
            "(coroutine cancelled: upstream provider hung?)") from None


async def _as_coro(awaitable: Awaitable[Any]) -> Any:
    return await awaitable
