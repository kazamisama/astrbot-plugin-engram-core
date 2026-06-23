"""Smoke v1.43 (WebUI diary routes).

Verifies that the three diary WebUI endpoints are actually registered
on PluginPageApi.register_routes(). Regression: the v1.40 / v1.41 / v1.42
PluginPageApi defined async handlers (_list_diaries, _diary_options,
_diary_detail) but never wired them up in register_routes(), so the
WebUI "diary" tab hit "route not found" on load.
"""
from __future__ import annotations
import os
import sys
import types
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# --- astrbot stub ---
_astro = types.ModuleType("astrbot")
_astro_api = types.ModuleType("astrbot.api")
_star_mod = types.ModuleType("astrbot.api.star")
class _Star:
    def __init__(self, *a, **kw): pass
class _Context:
    pass
def _register(*a, **kw):
    def _dec(cls): return cls
    return _dec
_star_mod.Star = _Star
_star_mod.register = _register
_star_mod.Context = _Context
_evt_mod = types.ModuleType("astrbot.api.event")
class _Filter:
    class EventMessageType:
        ALL = 0
    def event_message_type(self, *a, **kw):
        def _dec(f): return f
        return _dec
    def on_llm_request(self):
        def _dec(f): return f
        return _dec
    def on_llm_response(self):
        def _dec(f): return f
        return _dec
    def command(self, *a, **kw):
        def _dec(f): return f
        return _dec
_evt_mod.filter = _Filter()
class _AstrMessageEvent: pass
_evt_mod.AstrMessageEvent = _AstrMessageEvent
sys.modules["astrbot"] = _astro
sys.modules["astrbot.api"] = _astro_api
sys.modules["astrbot.api.star"] = _star_mod
sys.modules["astrbot.api.event"] = _evt_mod
# --- end stub ---

from page_api import PluginPageApi, PAGE_API_PREFIX


class _FakeContext:
    """Captures every register_web_api call so the test can assert on it."""
    def __init__(self):
        self.routes = []  # list[(route, handler, methods, desc)]

    def register_web_api(self, route, handler, methods, desc):
        self.routes.append((route, handler, tuple(methods), desc))


class _FakePlugin:
    def __init__(self, ctx):
        self.context = ctx
        # _initializer.service is read by PluginPageApi._service(); set
        # up a tiny stand-in so the diary handlers can run if invoked.
        self._initializer = types.SimpleNamespace(service=None)


def _banner(s):
    print()
    print("=== " + s + " ===")


def test_diary_routes_registered():
    _banner("v1.43: page/diaries[/options|detail] are registered")
    ctx = _FakeContext()
    api = PluginPageApi(_FakePlugin(ctx))
    api.register_routes()

    paths = [r[0] for r in ctx.routes]
    expected = [
        PAGE_API_PREFIX + "/diaries/options",
        PAGE_API_PREFIX + "/diaries",
        PAGE_API_PREFIX + "/diaries/detail",
    ]
    missing = [p for p in expected if p not in paths]
    assert not missing, ("missing diary routes: " + str(missing) + "; "
                         "all registered: " + str(paths))
    # also assert methods are GET
    by_path = {r[0]: r for r in ctx.routes}
    for p in expected:
        methods = by_path[p][2]
        assert "GET" in methods, (p, methods)
    print("PASS diary_routes_registered (" + str(len(expected)) + " routes)")


def test_diary_options_handler_returns_envelope():
    _banner("v1.43: /diaries/options returns {status:ok, data:{...}} envelope")
    ctx = _FakeContext()
    api = PluginPageApi(_FakePlugin(ctx))
    api.register_routes()

    # call the async handler directly (no real service -> returns error envelope)
    import asyncio
    res = asyncio.run(api._diary_options())
    assert res.get("status") == "error", res
    assert "message" in res, res
    print("PASS diary_options_envelope (no service -> error envelope)")


def test_diary_list_handler_returns_envelope():
    _banner("v1.43: /diaries returns {status:ok|error, ...} envelope")
    ctx = _FakeContext()
    api = PluginPageApi(_FakePlugin(ctx))
    import asyncio
    res = asyncio.run(api._list_diaries())
    assert "status" in res, res
    assert res["status"] == "error", res
    print("PASS diary_list_envelope")


def test_diary_detail_handler_returns_envelope():
    _banner("v1.43: /diaries/detail returns {status:ok|error, ...} envelope")
    ctx = _FakeContext()
    api = PluginPageApi(_FakePlugin(ctx))
    import asyncio
    res = asyncio.run(api._diary_detail())
    assert "status" in res, res
    assert res["status"] == "error", res
    print("PASS diary_detail_envelope")


def main():
    test_diary_routes_registered()
    test_diary_options_handler_returns_envelope()
    test_diary_list_handler_returns_envelope()
    test_diary_detail_handler_returns_envelope()
    print()
    print("ALL v61 PASS")


if __name__ == "__main__":
    main()
