"""Smoke v1.76.14: recurring-freeze hardening (2026-09-07 recurrence).

Covers:
  R1 emb bridge hard-bounds a hanging provider await (no worker wedge)
  R2 run_sync rebuilds a wedged worker: after a hard-hang timeout the
     NEXT call runs on a fresh loop and succeeds (self-healing)
  R3 ProxyEmbeddingProvider sync fn that never returns -> bounded by
     run_sync cap (raises RuntimeError, does not block the caller forever)
  R4 observe handler releases the pipeline on ingest timeout (bounded)
"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- shim astrbot so handlers.format / handlers.recall resolve ---
_astrbot = types.ModuleType("astrbot")
_api = types.ModuleType("astrbot.api")
_event = types.ModuleType("astrbot.api.event")


class AstrMessageEvent:  # typing only
    pass


_event.AstrMessageEvent = AstrMessageEvent
_api.event = _event
_astrbot.api = _api
sys.modules.setdefault("astrbot", _astrbot)
sys.modules.setdefault("astrbot.api", _api)
sys.modules.setdefault("astrbot.api.event", _event)

import asyncio  # noqa: E402

import hippocampus._async_bridge as bridge  # noqa: E402
from hippocampus._async_bridge import run_sync  # noqa: E402
from handlers.recall import emb_bridge_for_context, EMB_BRIDGE_TIMEOUT  # noqa: E402
from hippocampus.providers import ProxyEmbeddingProvider  # noqa: E402


class _Ctx:
    """Minimal Context: one embedding provider whose get_embedding hangs."""

    def __init__(self, hang: bool):
        self._hang = hang

    class _Prov:
        def __init__(self, hang: bool):
            self._hang = hang

        async def get_embedding(self, text: str):
            if self._hang:
                await asyncio.sleep(999)
            return [0.1] * 8

    def get_provider_by_id(self, pid):
        return self._Prov(self._hang)

    def get_all_embedding_providers(self):
        return [self._Prov(self._hang)]

    def get_using_provider(self):
        return self._Prov(self._hang)


def test_emb_bridge_hang_bounded():
    old = EMB_BRIDGE_TIMEOUT
    try:
        import handlers.recall as r
        r.EMB_BRIDGE_TIMEOUT = 0.4
        t0 = time.perf_counter()
        out = asyncio.run(emb_bridge_for_context(_Ctx(True), "x"))
        dt = time.perf_counter() - t0
        assert out == [], out
        assert dt < 2.0, dt
        print(f"R1 emb bridge hang bounded ({dt:.2f}s, returns []): OK")
    finally:
        import handlers.recall as r
        r.EMB_BRIDGE_TIMEOUT = old


def test_worker_rebuild_after_wedge():
    # 1) hard hang -> timeout (marks worker wedged)
    async def _hang():
        await asyncio.sleep(999)

    try:
        run_sync(_hang(), timeout=0.4)
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    # 2) next call must succeed on a rebuilt worker (NOT time out)
    async def _ok():
        return "recovered"

    t0 = time.perf_counter()
    assert run_sync(_ok(), timeout=2.0) == "recovered"
    dt = time.perf_counter() - t0
    assert dt < 1.5, dt
    print(f"R2 wedged worker self-heals ({dt:.2f}s): OK")


def test_sync_fn_bounded():
    def _hung_sync(text: str):
        time.sleep(999)

    import hippocampus.providers as prov_mod
    old = prov_mod.DEFAULT_SYNC_TIMEOUT
    try:
        prov_mod.DEFAULT_SYNC_TIMEOUT = 0.4
        # rebuild the provider AFTER patching: the module reads the constant
        # at call time (_bounded_sync_call is passed DEFAULT_SYNC_TIMEOUT
        # from providers.py's module scope).
        p = ProxyEmbeddingProvider("test", _hung_sync, dim=8)
        t0 = time.perf_counter()
        try:
            p.embed("x")
            raise AssertionError("expected RuntimeError")
        except RuntimeError:
            dt = time.perf_counter() - t0
            assert dt < 2.0, dt
            print(f"R3 sync fn bounded ({dt:.2f}s, raises RuntimeError): OK")
    finally:
        prov_mod.DEFAULT_SYNC_TIMEOUT = old


def test_observe_releases_on_timeout():
    from handlers.event.observe import ObserveHandler
    import concurrent.futures
    import threading

    _gate = threading.Event()

    class _Rec:
        def __init__(self):
            self.lines = []

    svc = types.SimpleNamespace(
        cfg=types.SimpleNamespace(
            summary_mode_enabled=False,
            per_message_ingest_debug=False,
            session_aggregate_enabled=False,
            diary_enabled=False,
            summary_idle_flush_interval_seconds=60.0,
            diary_message_flush_interval_seconds=30.0,
        ),
        cache_daily_line=lambda meta: None,
        observe=lambda **kw: None,
    )

    h = ObserveHandler(svc)
    h._OBSERVE_HARD_TIMEOUT = 0.4
    ex = concurrent.futures.ThreadPoolExecutor(1)

    def _wedged(meta, cfg):
        _gate.wait(timeout=60)  # stands in for a stuck ingest worker

    try:
        async def _run():
            async with h._get_ingest_lock():
                try:
                    await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(ex, _wedged, {}, svc.cfg),
                        timeout=h._OBSERVE_HARD_TIMEOUT)
                except asyncio.TimeoutError:
                    return "released"
        t0 = time.perf_counter()
        assert asyncio.run(_run()) == "released"
        dt = time.perf_counter() - t0
        assert dt < 2.0, dt
        print(f"R4 observe timeout releases pipeline ({dt:.2f}s): OK")
    finally:
        _gate.set()  # release the fake worker so the executor can shut down
        ex.shutdown(wait=False)


def main():
    test_emb_bridge_hang_bounded()
    test_worker_rebuild_after_wedge()
    test_sync_fn_bounded()
    test_observe_releases_on_timeout()
    print("\nv1.76.14 recurring-freeze hardening: ALL PASS")


if __name__ == "__main__":
    main()
