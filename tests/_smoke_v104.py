"""Smoke v104 (v1.79.0): typed-config access, no phantom knobs, no dead API.

The config audit found 68 `getattr(self.cfg, "...", default)` reads on a
dataclass that always has the attribute: 38 repeated the default verbatim, 19
disagreed with the real default, and 5 named fields that did not exist at all
(so those knobs could never be configured). This pins the cleanup:

1. the five former phantom names are real MemoryConfig fields, reachable
   through ConfigManager;
2. search_cache_ttl_seconds=0 really disables the recall cache (the old
   `or 60.0` guard silently turned 0 back into 60);
3. no production module reads a config value through getattr() again;
4. the dead vecmath API stays gone.
"""
import ast
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _smoke_common import install_stub

install_stub()

from hippocampus import MemoryConfig, MemoryService  # noqa: E402
from hippocampus.config_manager import ConfigManager  # noqa: E402
from hippocampus import vecmath  # noqa: E402

FAILS = []
CHECKS = []


def check(ok, msg):
    print(("ok   " if ok else "FAIL ") + msg)
    CHECKS.append(bool(ok))
    if not ok:
        FAILS.append(msg)


PHANTOM = ("search_cache_enabled", "search_cache_ttl_seconds",
           "search_cache_max_entries", "diary_consume_cache_on_write",
           "bot_actor_id")


def test_fields() -> None:
    print(chr(10) + "=== the five former phantom knobs are real fields ===")
    cfg_dict = {
        "search_cache_enabled": False,
        "search_cache_ttl_seconds": 0,
        "search_cache_max_entries": 4,
        "diary_consume_cache_on_write": False,
        "bot_actor_id": "bot-42",
    }
    cfg = ConfigManager(cfg_dict).memory_config
    check(cfg.search_cache_enabled is False, "search_cache_enabled round-trips")
    check(cfg.search_cache_ttl_seconds == 0.0, "search_cache_ttl_seconds round-trips")
    check(cfg.search_cache_max_entries == 4, "search_cache_max_entries round-trips")
    check(cfg.diary_consume_cache_on_write is False,
          "diary_consume_cache_on_write round-trips")
    check(cfg.bot_actor_id == "bot-42", "bot_actor_id round-trips")
    check(cfg.extra == {}, "no phantom key leaked into extra: %r" % (cfg.extra,))


def test_zero_disables_cache() -> None:
    print(chr(10) + "=== ttl=0 disables the recall cache ===")
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        cfg = MemoryConfig(sqlite_path=db, embedding_dim=16,
                           embedding_name="hash", llm_name="rule",
                           search_cache_ttl_seconds=0.0)
        svc = MemoryService(cfg)
        try:
            check(svc._recall_cache_ttl == 0.0, "service reads ttl=0 verbatim")
            svc._recall_cache_set(("k",), object())
            check(svc._recall_cache_get(("k",)) is None,
                  "cache read returns nothing when ttl=0")
        finally:
            svc.close()
    finally:
        try:
            os.unlink(db)
        except OSError:
            pass


def test_no_getattr_cfg() -> None:
    print(chr(10) + "=== config is read as an attribute, not through getattr ===")
    roots = ["hippocampus", "handlers", "page_api_modules",
             "main.py", "page_api.py"]
    files = []
    for root in roots:
        if os.path.isfile(root):
            files.append(root)
            continue
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d != "__pycache__"]
            for name in fn:
                if name.endswith(".py"):
                    files.append(os.path.join(dp, name))
    offenders = []
    for path in files:
        tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Name) and fn.id == "getattr"):
                continue
            if len(node.args) < 2:
                continue
            target, attr = node.args[0], node.args[1]
            if not (isinstance(target, ast.Attribute) and target.attr == "cfg"):
                continue
            if not isinstance(attr, ast.Constant) or not isinstance(attr.value, str):
                continue
            if attr.value.startswith("_"):
                continue          # _prompt_namespace is injected at runtime
            offenders.append("%s:%d %s" % (path, node.lineno, attr.value))
    check(not offenders,
          "no getattr(self.cfg, <public field>) left"
          + ("" if not offenders else ": " + "; ".join(offenders[:6])))


def test_dead_api_gone() -> None:
    print(chr(10) + "=== dead API stays deleted ===")
    check(not hasattr(vecmath.VectorIndex, "vector"),
          "VectorIndex.vector() (0 call sites) is gone")
    check(not hasattr(vecmath, "HAVE_NUMPY"),
          "HAVE_NUMPY (0 production readers) is gone")
    check(not hasattr(MemoryService, "_build_default_registry"),
          "the one-line registry pass-through is gone")


test_fields()
test_zero_disables_cache()
test_no_getattr_cfg()
test_dead_api_gone()

print(chr(10) + "TOTAL=" + str(len(CHECKS)) + " FAIL=" + str(len(FAILS)))
if FAILS:
    raise SystemExit(1)