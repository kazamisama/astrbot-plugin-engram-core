"""Smoke v1.46 (diary WebUI delete + inline fold backend).

Covers:
  1. POST /diaries/delete is registered on PluginPageApi.
  2. The async handler returns a {status:error, ...} envelope when
     the service is not initialized (no real diary wired up; we
     verify the route reaches the handler and the handler responds
     gracefully).
  3. The route's HTTP method is POST (matches the other delete route
     at /memories/delete).
  4. DiaryHandler.delete_diary with no service returns an error
     envelope.
  5. DiaryHandler.delete_diary with a missing eid returns a
     structured error.
"""
from __future__ import annotations
import os
import sys
import types
import asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# --- minimal astrbot stub (mirror v61/v62) ---
_astro = types.ModuleType("astrbot")
_astro_api = types.ModuleType("astrbot.api")
_sp_mod = types.ModuleType("astrbot.api.sp")
_evt_mod = types.ModuleType("astrbot.api.event")
class _AstrMessageEvent: pass
_evt_mod.AstrMessageEvent = _AstrMessageEvent
sys.modules["astrbot"] = _astro
sys.modules["astrbot.api"] = _astro_api
sys.modules["astrbot.api.sp"] = _sp_mod
sys.modules["astrbot.api.event"] = _evt_mod
# --- end stub ---


class FakeContext:
    def __init__(self):
        self.routes = []
    def register_web_api(self, route, handler, methods, desc):
        self.routes.append((route, handler, tuple(methods), desc))


class FakePlugin:
    def __init__(self):
        self.context = FakeContext()
        self._initializer = types.SimpleNamespace(service=None)


def _banner(s):
    print()
    print("=== " + s + " ===")


def test_diary_delete_route_registered():
    _banner("v1.46: POST /diaries/delete is registered")
    from page_api import PluginPageApi, PAGE_API_PREFIX
    api = PluginPageApi(FakePlugin())
    api.register_routes()
    paths = [r[0] for r in api.plugin.context.routes]
    target = PAGE_API_PREFIX + "/diaries/delete"
    assert target in paths, ("missing diary delete route: " + target + "; "
                             "all: " + str(paths))
    by_path = {r[0]: r for r in api.plugin.context.routes}
    methods = by_path[target][2]
    assert "POST" in methods, (target, methods)
    print("PASS diary_delete_route_registered (POST)")


def test_diary_delete_handler_envelope_no_service():
    _banner("v1.46: _delete_diary without a service -> error envelope")
    from page_api import PluginPageApi
    api = PluginPageApi(FakePlugin())
    api.register_routes()
    res = asyncio.run(api._delete_diary())
    assert res.get("status") == "error", res
    assert "message" in res, res
    print("PASS diary_delete_envelope_no_service")


def test_diary_handler_delete_diary_no_service():
    _banner("v1.46: DiaryHandler.delete_diary(None, '') -> error envelope")
    from page_api_modules.diary import DiaryHandler
    from page_api_modules.utils import PageApiUtils
    h = DiaryHandler(PageApiUtils())
    res = h.delete_diary(None, "", hard=False)
    assert res.get("status") == "error", res
    # the missing-eid path is also covered: passing eid="X" with no
    # service should still hit the early "not initialized" guard
    # before any DB access.
    res2 = h.delete_diary(None, "abc", hard=True)
    assert res2.get("status") == "error", res2
    print("PASS diary_handler_delete_no_service")


def test_diary_handler_delete_diary_empty_eid():
    _banner("v1.46: DiaryHandler.delete_diary with empty eid -> error")
    # We need a service stub that survives the None-check; pass a
    # simple object with a no-op store.get that returns None.
    from page_api_modules.diary import DiaryHandler
    from page_api_modules.utils import PageApiUtils
    class _S:
        class _Store:
            def get(self, eid): return None
        store = _Store()
    h = DiaryHandler(PageApiUtils())
    res = h.delete_diary(_S(), "", hard=False)
    assert res.get("status") == "error", res
    assert "Missing" in res.get("message", "") or "missing" in res.get("message", ""), res
    print("PASS diary_handler_delete_empty_eid")


def test_diary_handler_delete_diary_wrong_memory_type():
    _banner("v1.46: DiaryHandler refuses to delete a non-diary engram")
    from page_api_modules.diary import DiaryHandler
    from page_api_modules.utils import PageApiUtils
    class _S:
        class _Store:
            def get(self, eid):
                return types.SimpleNamespace(id=eid, memory_type="episodic")
        store = _Store()
    h = DiaryHandler(PageApiUtils())
    res = h.delete_diary(_S(), "abc", hard=False)
    assert res.get("status") == "error", res
    assert "not a diary" in res.get("message", ""), res
    print("PASS diary_handler_delete_wrong_type")


def main():
    test_diary_delete_route_registered()
    test_diary_delete_handler_envelope_no_service()
    test_diary_handler_delete_diary_no_service()
    test_diary_handler_delete_diary_empty_eid()
    test_diary_handler_delete_diary_wrong_memory_type()
    print()
    print("ALL v63 PASS")


if __name__ == "__main__":
    main()
