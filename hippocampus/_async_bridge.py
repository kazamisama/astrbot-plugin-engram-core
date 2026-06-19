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
"""
from __future__ import annotations
import asyncio
import threading
from typing import Any, Awaitable

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
    """Block until *awaitable* completes on the worker loop and return its result."""
    loop = _ensure_worker()
    fut = asyncio.run_coroutine_threadsafe(_as_coro(awaitable), loop)
    return fut.result(timeout=timeout)


async def _as_coro(awaitable: Awaitable[Any]) -> Any:
    return await awaitable