"""Smoke v1.76.13 injection-timeout circuit breaker (2026-09-07).

Covers:
  1. run_sync success path still works
  2. run_sync(hang, timeout=0.5) raises RuntimeError in ~0.5s
  3. after the timeout the worker loop recovers (cancellation un-sticks it)
  4. run_sync default timeout applies when timeout is omitted
  5. InjectHandler.handle_inject gives up after auto_inject_timeout and
     releases the LLM request (elapsed bounded)
  6. InjectHandler happy path still completes (no hits -> request untouched)
"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- shim astrbot so handlers.format's `from astrbot.api.event import
# AstrMessageEvent` resolves outside a running AstrBot ---
_astrbot = types.ModuleType("astrbot")
_api = types.ModuleType("astrbot.api")
_event = types.ModuleType("astrbot.api.event")


class AstrMessageEvent:  # typing only; hooks use plain attributes
    pass


_event.AstrMessageEvent = AstrMessageEvent
_api.event = _event
_astrbot.api = _api
sys.modules.setdefault("astrbot", _astrbot)
sys.modules.setdefault("astrbot.api", _api)
sys.modules.setdefault("astrbot.api.event", _event)

import asyncio  # noqa: E402

from hippocampus._async_bridge import run_sync  # noqa: E402
from handlers.event.inject import InjectHandler  # noqa: E402


async def _ok():
    return 42


async def _hang():
    await asyncio.sleep(999)


def test_run_sync_success():
    assert run_sync(_ok(), timeout=2.0) == 42
    print("R1 run_sync success: OK")


def test_run_sync_hang_times_out():
    t0 = time.perf_counter()
    try:
        run_sync(_hang(), timeout=0.5)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        dt = time.perf_counter() - t0
        assert dt < 2.0, dt
        print(f"R2 run_sync hang -> RuntimeError after {dt:.2f}s: OK")


def test_run_sync_worker_recovers():
    # cancellation must un-stick the worker loop for later calls
    t0 = time.perf_counter()
    assert run_sync(_ok(), timeout=2.0) == 42
    dt = time.perf_counter() - t0
    assert dt < 1.0, dt
    print(f"R3 worker loop recovers after cancel ({dt:.2f}s): OK")


def test_run_sync_default_timeout():
    import hippocampus._async_bridge as b
    old = b.DEFAULT_SYNC_TIMEOUT
    b.DEFAULT_SYNC_TIMEOUT = 0.3
    try:
        t0 = time.perf_counter()
        try:
            run_sync(_hang())
            raise AssertionError("expected RuntimeError")
        except RuntimeError:
            assert time.perf_counter() - t0 < 2.0
    finally:
        b.DEFAULT_SYNC_TIMEOUT = old
    print("R4 run_sync default timeout applies: OK")


class _FakeResult:
    def __init__(self, engrams):
        self.engrams = engrams


def _fake_svc(recall_fn):
    cfg = types.SimpleNamespace(
        auto_inject_enabled=True,
        auto_inject_top_k=3,
        auto_inject_position="before",
        auto_inject_relative_time=True,
        persona_isolation_enabled=True,
        persona_inject_enabled=False,
        relation_inject_top_n=3,
        relation_inject_min_confidence=0.0,
        diary_inject_top_n=1,
        diary_inject_min_score=0.0,
    )
    svc = types.SimpleNamespace(cfg=cfg, recall=recall_fn)
    return svc


class _Evt:
    def __init__(self):
        self.unified_msg_origin = "test:group:111"
        self.message_str = "追问我刚才说的那段话"

    def get_sender_id(self):
        return "fengjian"

    def get_platform_name(self):
        return "test"

    def get_group_id(self):
        return "111"


async def test_inject_circuit_breaker():
    def slow_recall(cue):
        time.sleep(2.0)  # simulate hung embedding call
        return _FakeResult([])

    h = InjectHandler(_fake_svc(slow_recall))
    h.service.cfg.auto_inject_timeout = 0.4
    t0 = time.perf_counter()
    await h.handle_inject(_Evt(), types.SimpleNamespace(prompt="hello"))
    dt = time.perf_counter() - t0
    assert dt < 1.5, f"circuit breaker did not trip: {dt:.2f}s"
    print(f"R5 inject circuit breaker tripped after {dt:.2f}s (LLM request released): OK")


async def test_inject_happy_path():
    def fast_recall(cue):
        return _FakeResult([])

    h = InjectHandler(_fake_svc(fast_recall))
    h.service.cfg.auto_inject_timeout = 5.0
    req = types.SimpleNamespace(prompt="hello")
    await h.handle_inject(_Evt(), req)
    assert req.prompt == "hello"
    print("R6 inject happy path (no hits -> request untouched): OK")


class _Evt2:
    def __init__(self):
        self.unified_msg_origin = "test:group:111"
        self.message_str = "她最喜欢蓝色"

    def get_sender_id(self):
        return "fengjian"

    def get_platform_name(self):
        return "test"

    def get_group_id(self):
        return "111"


async def test_inject_e2e_real_service():
    import tempfile

    from hippocampus import MemoryConfig, MemoryService

    tmp = tempfile.mkdtemp()
    cfg = MemoryConfig(sqlite_path=os.path.join(tmp, "e2e.db"))
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.enable_separation = False
    cfg.dedup_enabled = False
    cfg.auto_inject_enabled = True
    cfg.auto_inject_top_k = 3
    cfg.auto_inject_relative_time = True
    cfg.relation_inject_top_n = 0
    cfg.diary_inject_top_n = 0
    cfg.persona_inject_enabled = False
    cfg.auto_inject_timeout = 5.0
    svc = MemoryService(cfg)
    svc.observe(session_id="test:group:111", actor_id="fengjian",
                platform="test", channel_id="111", content="她最喜欢蓝色")
    h = InjectHandler(svc)

    import handlers.event.inject as ij

    had = ij.TextPart

    class _TextPart:
        def __init__(self, text, type="text"):
            self.text = text
            self.type = type

        def mark_as_temp(self):
            return self

    ij.TextPart = _TextPart
    try:
        req = types.SimpleNamespace(
            prompt="她最喜欢什么颜色", extra_user_content_parts=[])
        await h.handle_inject(_Evt2(), req)
        assert len(req.extra_user_content_parts) == 1, req.extra_user_content_parts
        txt = req.extra_user_content_parts[0].text
        assert "<engram-context>" in txt and "[近期对话]" in txt, txt
        assert req.prompt == "她最喜欢什么颜色"
        # re-injection defense: second firing must not duplicate
        await h.handle_inject(_Evt2(), req)
        assert len(req.extra_user_content_parts) == 1, len(req.extra_user_content_parts)
    finally:
        ij.TextPart = had
        svc.close()
    print("R7 inject e2e real service (injected once, no duplication, prompt untouched): OK")


def main():
    test_run_sync_success()
    test_run_sync_hang_times_out()
    test_run_sync_worker_recovers()
    test_run_sync_default_timeout()
    asyncio.run(test_inject_circuit_breaker())
    asyncio.run(test_inject_happy_path())
    asyncio.run(test_inject_e2e_real_service())
    print("\nv1.76.13 injection timeout circuit breaker: ALL PASS")


if __name__ == "__main__":
    main()
