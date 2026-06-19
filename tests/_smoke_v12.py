"""Smoke v1.2: version single-source-of-truth + export/import format-version guard.

Independent smoke. Mocks astrbot.api so it does not require a real AstrBot runtime.
Also re-checks that export/import round-trips still work end to end.
"""
import os, json, tempfile, sys, types, asyncio, re


def _install_stub():
    a = types.ModuleType("astrbot"); ai = types.ModuleType("astrbot.api")
    sm = types.ModuleType("astrbot.api.star"); em = types.ModuleType("astrbot.api.event")
    class Star: ...
    captured = {}
    def register(*args, **kwargs):
        # main.py calls @register("hippocampus", "shirley", <desc>, <version>)
        if len(args) >= 4:
            captured["version"] = args[3]
        def deco(cls):
            cls._registered_version = captured.get("version")
            return cls
        return deco
    class Context: ...
    class AstrMessageEvent: ...
    class _MT: ALL = "all"
    class _F:
        EventMessageType = _MT
        def event_message_type(self, *a, **k):
            def deco(fn): return fn
            return deco
        def command(self, *a, **k):
            def deco(fn): return fn
            return deco
    sm.Star = Star; sm.register = register; sm.Context = Context
    em.filter = _F; em.AstrMessageEvent = AstrMessageEvent; em.EventMessageType = _MT
    sys.modules["astrbot"] = a; sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm; sys.modules["astrbot.api.event"] = em
    return captured


_captured = _install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import main
import hippocampus
from hippocampus import MemoryService, MemoryConfig, EXPORT_FORMAT_VERSION
from main import export_engrams, import_engrams, banner_text, HIPPO_VERSION


def banner(t): print(chr(10) + "=== " + t + " ===")


def _read_metadata_version():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    txt = open(os.path.join(here, "metadata.yaml"), encoding="utf-8").read()
    m = re.search(r"(?m)^version:\s*(.+?)\s*$", txt)
    return m.group(1).strip() if m else None


def test_version_single_source():
    banner("version: metadata.yaml == __version__ == HIPPO_VERSION == @register")
    meta = _read_metadata_version()
    assert meta == hippocampus.__version__, (meta, hippocampus.__version__)
    assert HIPPO_VERSION == hippocampus.__version__
    assert main.HippocampusStar._registered_version == hippocampus.__version__, \
        ("@register version drifted", main.HippocampusStar._registered_version)
    # banner_text on a None service should not blow up
    assert banner_text(None) == "[hippocampus] not initialized"
    print("  version single source: OK ->", hippocampus.__version__)


def _new_service(td):
    cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"))
    return MemoryService(cfg)


def test_export_uses_format_version():
    banner("export: payload version field == EXPORT_FORMAT_VERSION")
    with tempfile.TemporaryDirectory() as td:
        svc = _new_service(td)
        svc.observe(session_id="s1", actor_id="u1", platform="qq",
                    channel_id="g1", content="Shirley loves espresso")
        out = os.path.join(td, "dump.json")
        msg = export_engrams(svc, out)
        assert "exported" in msg, msg
        payload = json.load(open(out, encoding="utf-8"))
        assert payload["version"] == EXPORT_FORMAT_VERSION, payload["version"]
        assert len(payload["engrams"]) >= 1
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  export format version: OK ->", payload["version"])


def test_import_roundtrip_and_warn():
    banner("import: round-trip OK + stale format version warns, not fatal")
    with tempfile.TemporaryDirectory() as td:
        svc = _new_service(td)
        svc.observe(session_id="s1", actor_id="u1", platform="qq",
                    channel_id="g1", content="Shirley loves espresso")
        out = os.path.join(td, "dump.json")
        export_engrams(svc, out)
        svc.close()
        del svc
        import gc as _gc; _gc.collect()

        # current-version import: no warn
        svc2 = _new_service(td)
        msg = import_engrams(svc2, out)
        assert "imported" in msg, msg
        assert "warn" not in msg, ("current version should not warn", msg)
        asyncio.run(svc2.stop_background_tasks())
        svc2.close()
        del svc2
        import gc as _gc2; _gc2.collect()

        # stale-version import: should warn but still import
        payload = json.load(open(out, encoding="utf-8"))
        payload["version"] = "0.7"
        stale = os.path.join(td, "stale.json")
        json.dump(payload, open(stale, "w", encoding="utf-8"))
        svc3 = _new_service(td)
        msg2 = import_engrams(svc3, stale)
        assert "imported" in msg2, msg2
        assert "warn" in msg2 and "0.7" in msg2, ("stale import should warn", msg2)
        asyncio.run(svc3.stop_background_tasks())
        svc3.close()
        del svc3
        import gc as _gc3; _gc3.collect()

        # missing version field: no warn (back-compat with very old dumps)
        payload.pop("version", None)
        nov = os.path.join(td, "noversion.json")
        json.dump(payload, open(nov, "w", encoding="utf-8"))
        svc4 = _new_service(td)
        msg3 = import_engrams(svc4, nov)
        assert "imported" in msg3 and "warn" not in msg3, msg3
        asyncio.run(svc4.stop_background_tasks())
        svc4.close()
        del svc4
        import gc as _gc4; _gc4.collect()
        print("  import round-trip + warn: OK")


if __name__ == "__main__":
    test_version_single_source()
    test_export_uses_format_version()
    test_import_roundtrip_and_warn()
    print(chr(10) + "ALL OK")