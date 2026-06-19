"""Smoke v0.9: DG pattern-separation (merge / link / new) + cluster expansion at recall + /mem cluster command + bidirectional + cap + kill switch."""
import os, tempfile, sys, types, asyncio, json

def _install_astrbot_stub():
    astrbot = types.ModuleType("astrbot"); astrbot_api = types.ModuleType("astrbot.api")
    star_mod = types.ModuleType("astrbot.api.star")
    event_mod = types.ModuleType("astrbot.api.event")
    class Star: ...
    def register(*a, **k):
        def deco(cls): return cls
        return deco
    class Context: ...
    class AstrMessageEvent: ...
    class _EventMessageType: ALL = "all"
    class _Filter:
        EventMessageType = _EventMessageType
        def event_message_type(self, *a, **k):
            def deco(fn): return fn
            return deco
        def command(self, *a, **k):
            def deco(fn): return fn
            return deco
    star_mod.Star = Star; star_mod.register = register; star_mod.Context = Context
    event_mod.filter = _Filter
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.EventMessageType = _EventMessageType
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.event"] = event_mod

_install_astrbot_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import main
from hippocampus import MemoryService, MemoryConfig, Cue
from hippocampus.separation import PatternSeparator
from hippocampus.storage import _cos
from main import (HippocampusStar, render_stats, find_and_forget,
                  format_graph, format_cluster)


def banner(t): print(chr(10) + "=== " + t + " ===")


def test_enabled_separation():
    banner("enabled: link + bidirectional + cluster recall")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        sid = "chat-1"
        actor = "u1"

        # First message establishes the cluster anchor.
        a1 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee")
        # Very similar -> should LINK
        a2 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee very much")
        # Also similar -> should LINK to a1 (working memory still has a1)
        a3 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I really love Americano coffee")
        # Unrelated -> NEW
        b = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                        channel_id="g1", content="Bob dislikes cilantro intensely")

        print("a1 id=" + a1.id[:8] + " similar_to=" + str([x[:8] for x in a1.similar_to]))
        print("a2 id=" + a2.id[:8] + " similar_to=" + str([x[:8] for x in a2.similar_to]))
        print("a3 id=" + a3.id[:8] + " similar_to=" + str([x[:8] for x in a3.similar_to]))
        print("b  id=" + b.id[:8]  + " similar_to=" + str([x[:8] for x in b.similar_to]))

        # Verify bidirectional
        assert a2.id in a1.similar_to, "a1 should link to a2"
        assert a1.id in a2.similar_to, "a2 should reverse-link to a1 (bidirectional)"
        assert a3.id in a1.similar_to, "a1 should link to a3"
        assert a1.id in a3.similar_to, "a3 should reverse-link to a1"
        # Unrelated B has no links
        assert b.similar_to == [], "B should not link to anyone"

        # Sanity-check raw cosine of the linked pair
        cos12 = _cos(a1.embedding, a2.embedding)
        cos1b = _cos(a1.embedding, b.embedding)
        print("cos(a1,a2)=" + str(round(cos12, 3)) + " cos(a1,b)=" + str(round(cos1b, 3)))
        assert cos12 > 0.5, "linked pair should have non-trivial cosine"
        assert cos1b < cos12, "B should be less similar to A1 than A2 is"

        # Cluster expansion in recall: query A1's content, expect [A1, A2, A3] (or A1 + cluster)
        result = svc.recall(Cue(text="Americano coffee", actor_id=actor, channel_id="g1", k=3))
        ids = [e.id for e in result.engrams]
        print("recall ids: " + str([i[:8] for i in ids]) + " scores=" + str([round(s, 3) for s in result.scores]))
        assert a1.id in ids, "recall must include a1"
        # The cluster expansion should pull in at least one of a2/a3
        assert a2.id in ids or a3.id in ids, "recall cluster expansion should include a sibling"
        # B may appear in the working-memory head (prepended by service.recall),
        # so we only assert that B is NOT a cluster sibling of a1 (depth=1 expansion):
        a1_idx = ids.index(a1.id) if a1.id in ids else -1
        if a1_idx >= 0:
            tail = ids[a1_idx + 1:]
            for sib_id in tail[:3]:  # siblings live right after the root
                if sib_id != a1.id and sib_id != a2.id and sib_id != a3.id:
                    assert sib_id == b.id, "unknown non-cluster sibling leaked: " + sib_id

        # format_cluster helper
        out = format_cluster(svc, a1.id[:4])
        print(out)
        assert "cluster for" in out
        assert "similar_to" in out

        try: svc.close()

        except Exception: pass

        del svc

        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  enabled_separation: OK")


def test_cap():
    banner("cap: chain length <= separation_max_links")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"),
                           embedding_dim=64, separation_max_links=3)
        svc = MemoryService(cfg)
        sid = "chat-1"; actor = "u1"
        # Anchor
        a1 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee")
        # Add 8 very-similar messages; chain should cap at 3
        for i in range(8):
            svc.observe(session_id=sid, actor_id=actor, platform="qq",
                        channel_id="g1",
                        content="I love Americano coffee number " + str(i))
        chain = a1.similar_to
        print("a1.similar_to (cap=3): " + str([x[:8] for x in chain]) + " len=" + str(len(chain)))
        assert len(chain) <= 3, "chain must be capped at separation_max_links=3"
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  cap: OK")


def test_disabled():
    banner("disabled: enable_separation=False -> no links")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"),
                           embedding_dim=64, enable_separation=False)
        svc = MemoryService(cfg)
        sid = "chat-1"; actor = "u1"
        a1 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee")
        a2 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee very much")
        print("a1.similar_to=" + str([x[:8] for x in a1.similar_to]))
        print("a2.similar_to=" + str([x[:8] for x in a2.similar_to]))
        assert a1.similar_to == [], "with separation disabled a1 must have no links"
        assert a2.similar_to == [], "with separation disabled a2 must have no links"
        # Recall should also not expand (no links exist)
        result = svc.recall(Cue(text="Americano coffee", actor_id=actor, channel_id="g1", k=3))
        ids = [e.id for e in result.engrams]
        # just the top-k, no cluster siblings since none linked
        assert a1.id in ids
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  disabled: OK")


def test_apply_link_helper():
    banner("apply_link: bidirectional + dedup + cap")
    from hippocampus.types import Engram
    a = Engram(content="a")
    b = Engram(content="b")
    c = Engram(content="c")
    PatternSeparator.apply_link(a, b, max_links=5)
    assert b.id in a.similar_to and a.id in b.similar_to
    # a chain
    PatternSeparator.apply_link(a, c, max_links=5)
    assert c.id in a.similar_to
    # cap test
    a.similar_to = []
    b.similar_to = []
    for i in range(10):
        x = Engram(content="x" + str(i))
        PatternSeparator.apply_link(a, x, max_links=4)
    assert len(a.similar_to) <= 4, "a must be capped at 4"
    print("  apply_link: OK")


def test_expand_cluster():
    banner("expand_cluster: roots + 1-hop + cycle-safe")
    from hippocampus.types import Engram
    a, b, c, d = Engram(content="a"), Engram(content="b"), Engram(content="c"), Engram(content="d")
    a.similar_to = [b.id, c.id]
    b.similar_to = [a.id, d.id]  # d is a 2-hop neighbor, should NOT be returned
    c.similar_to = [a.id]
    store = {a.id: a, b.id: b, c.id: c, d.id: d}
    out = PatternSeparator.expand_cluster([a], fetch=lambda i: store.get(i), max_total=20)
    ids = [e.id for e, _s, _o in out]
    print("cluster ids: " + str([i[:8] for i in ids]))
    assert ids[0] == a.id
    assert b.id in ids and c.id in ids, "1-hop siblings should be present"
    assert d.id not in ids, "2-hop should NOT be expanded (depth=1)"
    print("  expand_cluster: OK")


if __name__ == "__main__":
    test_apply_link_helper()
    test_expand_cluster()
    test_enabled_separation()
    test_cap()
    test_disabled()
    print(chr(10) + "ALL OK")
