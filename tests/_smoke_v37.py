"""Smoke v1.14: cold-tier physical archive + /mem tier command wiring.

Covers:
  - ColdArchiver.archive_cold writes gzip JSONL, deletes archived rows,
    respects min_age_days, count_archived reads back
  - service.archive_cold delegates + returns result dict
  - 2 config fields registered with defaults
  - router has "mem tier"; format_tier / format_tier_archive render
"""
import sys, os, tempfile, time, gzip, json, types


def _install_stub():
    a = types.ModuleType("astrbot"); ai = types.ModuleType("astrbot.api")
    sm = types.ModuleType("astrbot.api.star"); em = types.ModuleType("astrbot.api.event")
    class Star: ...
    def register(*a, **k):
        def deco(cls): return cls
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
        @staticmethod
        def on_llm_request(*a, **k):
            def deco(fn): return fn
            return deco
    sm.Star = Star; sm.register = register; sm.Context = Context
    em.filter = _F; em.AstrMessageEvent = AstrMessageEvent; em.EventMessageType = _MT
    sys.modules["astrbot"] = a; sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm; sys.modules["astrbot.api.event"] = em


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.config import MemoryConfig
from hippocampus.config_manager import ConfigManager, _FIELDS, LABELS


def banner(m):
    print(chr(10) + "=== " + m + " ===")


DAY = 86400.0


def _build_service(tmp):
    from hippocampus.service import MemoryService
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, "hippo.db")
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.dedup_enabled = False
    cfg.tiering_enabled = True
    cfg.importance_floor_for_long_term = 0.0
    cfg.cold_archive_path = os.path.join(tmp, "cold.jsonl.gz")
    cfg.cold_archive_min_age_days = 30.0
    return MemoryService(cfg=cfg), cfg


def _seed(svc, content, strength, age_days):
    from hippocampus.types import Engram
    now = time.time()
    e = Engram(actor_id="u1", platform="qq", channel_id="g1",
               content=content, summary=content, strength=strength,
               created_at=now - age_days * DAY,
               embedding_model=svc._current_embedding_name)
    e.embedding = svc.embedder.embed(content)
    svc.store.upsert(e)
    return e


def test_config_fields():
    banner("cold archive config fields")
    for f in ("cold_archive_path", "cold_archive_min_age_days"):
        assert f in _FIELDS, f
        assert f in LABELS, f
    cfg = ConfigManager({}).memory_config
    assert cfg.cold_archive_path == ""
    assert cfg.cold_archive_min_age_days == 60.0
    print("  fields + defaults OK")


def test_archive_moves_and_deletes():
    banner("archive_cold moves cold rows to gzip + deletes from DB")
    tmp = tempfile.mkdtemp()
    svc, cfg = _build_service(tmp)
    hot = _seed(svc, "\u70ed\u8bb0\u5fc6", 1.0, 0.0)
    cold_old = _seed(svc, "\u51b7\u4e14\u8001", 0.02, 90.0)
    cold_recent = _seed(svc, "\u51b7\u4f46\u65b0", 0.02, 5.0)  # cold but younger than 30d
    res = svc.archive_cold()
    assert res.get("archived", 0) == 1, res
    assert res.get("skipped_recent", 0) == 1, res  # cold_recent skipped
    ids = {e.id for e in svc.store.all(limit=100)}
    assert cold_old.id not in ids, "archived row must be deleted from DB"
    assert hot.id in ids and cold_recent.id in ids
    # archive file has exactly 1 record, restorable JSON
    from hippocampus.cold_archive import ColdArchiver
    arch = ColdArchiver(svc.store, cfg)
    assert arch.count_archived() == 1
    with gzip.open(cfg.cold_archive_path, "rt", encoding="utf-8") as gz:
        recs = [json.loads(l) for l in gz if l.strip()]
    assert recs[0]["engram"]["id"] == cold_old.id
    print("  archived 1 / skipped 1 / file restorable: OK")
    try:
        svc.close()
    except Exception:
        pass


def test_archive_disabled_when_tiering_off():
    banner("archive_cold returns {} when tiering disabled")
    tmp = tempfile.mkdtemp()
    svc, cfg = _build_service(tmp)
    cfg.tiering_enabled = False
    assert svc.archive_cold() == {}
    print("  disabled -> {{}}: OK")
    try:
        svc.close()
    except Exception:
        pass


def test_router_and_formatters():
    banner("router mem tier + formatters")
    from handlers.commands import CommandRouter
    r = CommandRouter(observer=None, recall=None, manage=None)
    assert "mem tier" in r._table
    assert r._table["mem tier"] == "manage.cmd_mem_tier"
    from handlers.format import format_tier, format_tier_archive
    txt = format_tier_archive({"archived": 2, "skipped_recent": 1, "path": "x"})
    assert "2" in txt and "x" in txt
    txt2 = format_tier_archive({"error": "boom"})
    assert "boom" in txt2
    print("  router + formatters OK")


def main():
    test_config_fields()
    test_archive_moves_and_deletes()
    test_archive_disabled_when_tiering_off()
    test_router_and_formatters()
    print(chr(10) + "v1.14 smoke: ALL PASS")


if __name__ == "__main__":
    main()
