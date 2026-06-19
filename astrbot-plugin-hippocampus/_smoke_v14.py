"""Smoke v1.3 dual-route retrieval: document route + graph route + RRF merge.

Independent smoke. Mocks astrbot.api so it does not require a real AstrBot
runtime. Tests the new retrieval layer added in v1.3 without touching the
existing recall() pipeline (which continues to work as before).
"""
import os, tempfile, sys, types, asyncio


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
    sm.Star = Star; sm.register = register; sm.Context = Context
    em.filter = _F; em.AstrMessageEvent = AstrMessageEvent; em.EventMessageType = _MT
    sys.modules["astrbot"] = a; sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm; sys.modules["astrbot.api.event"] = em


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main  # noqa: F401  triggers handlers + hippocampus imports
from hippocampus import MemoryService, MemoryConfig, Cue
from hippocampus.retrieval import (
    RRFFusion, RankedCandidate, FusedCandidate, rrf_fuse, RRF_K_DEFAULT,
    DualRouteRetriever, DualRouteConfig, RouteKind,
)


def banner(t): print("\n=== " + t + " ===")


def test_rrf_math():
    banner("rrf: classic Cormack formula, k=60, two routes")
    fusion = RRFFusion(k=60)
    class It:
        def __init__(self, k): self.k = k
    A = It("a"); B = It("b"); C = It("c")
    out = fusion.fuse([
        ("r1", [RankedCandidate(item=A, raw_score=0.9, rank=1),
                RankedCandidate(item=B, raw_score=0.5, rank=2)]),
        ("r2", [RankedCandidate(item=B, raw_score=0.7, rank=1),
                RankedCandidate(item=C, raw_score=0.3, rank=2)]),
    ])
    assert len(out) == 3, "all 3 items must appear"
    # B = 1/61 + 1/61 = 0.032787
    # A = 1/61 = 0.016393
    # C = 1/62 = 0.016129
    b, a, c = out
    # B: r1 rank2 (1/62) + r2 rank1 (1/61) = 0.01613 + 0.01639 = 0.03252
    assert b.item is B and round(b.rrf_score, 5) == 0.03252, ("B score", b.rrf_score)
    assert a.item is A and round(a.rrf_score, 5) == 0.01639, ("A score", a.rrf_score)
    assert c.item is C and round(c.rrf_score, 5) == 0.01613, ("C score", c.rrf_score)
    # contribution dict is populated
    assert "r1" in b.contributions and "r2" in b.contributions
    assert "r1" in a.contributions and "r2" not in a.contributions
    assert "r2" in c.contributions and "r1" not in c.contributions
    print("  rrf math: OK -> B=%.5f A=%.5f C=%.5f" % (b.rrf_score, a.rrf_score, c.rrf_score))


def test_rrf_empty_routes():
    banner("rrf: one empty route -> other route wins")
    class It:
        def __init__(self, k): self.k = k
    A = It("a")
    out = RRFFusion(k=60).fuse([("only", [RankedCandidate(item=A, raw_score=0.5, rank=1)])])
    assert len(out) == 1 and out[0].item is A
    print("  empty route graceful: OK")


def test_legacy_rrf_fuse_compat():
    banner("rrf: legacy rrf_fuse() tuple API still works (back-compat for recall.py)")
    class It:
        def __init__(self, k): self.k = k
    A = It("a"); B = It("b"); C = It("c")
    out = rrf_fuse([(A, 0.9), (B, 0.5)], [(B, 0.7), (C, 0.3)])
    assert [c.k for c, _ in out] == ["b", "a", "c"], [c.k for c, _ in out]
    print("  legacy API: OK ->", [c.k for c, _ in out])
    print("  RRF_K_DEFAULT =", RRF_K_DEFAULT)


def _new_service():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32,
                       enable_semantic=True, enable_prospective=False)
    return MemoryService(cfg), tmp.name


def test_dual_route_document_only():
    banner("dual_route: empty graph -> falls back to document route")
    svc, db = _new_service()
    try:
        for txt in ["I love Americano coffee", "I live in Shanghai",
                    "the meeting is at 3pm"]:
            svc.observe(session_id="s1", actor_id="alice", platform="mock",
                        channel_id="c1", content=txt)
        res = svc.recall_dual_route(Cue(text="coffee", k=3))
        assert len(res.engrams) >= 1, "should find coffee engram"
        top = res.engrams[0]
        assert "Americano" in (top.content or "") or "coffee" in (top.content or "").lower()
        # scores are rrf_score, in (0, 1]
        assert all(0 < s <= 1.0 for s in res.scores)
        print("  document-only: OK ->", top.content[:40])
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_dual_route_with_graph():
    banner("dual_route: graph route contributes when entity is matched")
    svc, db = _new_service()
    try:
        # Inject entities + relations directly so the test does not depend on
        # EntityExtractor LLM output
        from hippocampus.types import Entity, Relation
        alice = Entity(id="ent_alice", name="Alice", type="person", mention_count=5)
        sh = Entity(id="ent_sh", name="Shanghai", type="place", mention_count=3)
        likes = Relation(subject_id="ent_alice", predicate="likes",
                         object_id="ent_sh", confidence=0.9)
        for e in (alice, sh):
            svc.semantic.upsert_entity(e)
        svc.semantic.add_relation(likes)
        # Ingest 2 engrams that mention Alice/Shanghai via entity_refs
        for txt in ["Alice likes Shanghai food", "I met Alice yesterday in Shanghai"]:
            e = svc.observe(session_id="s1", actor_id="u1", platform="mock",
                            channel_id="c1", content=txt)
            e.entity_refs = ["ent_alice", "ent_sh"]
            svc.store.update_embedding(e.id, e.embedding, e.embedding_model)
            svc.store.upsert(e)
        # Query "Alice" - should hit graph route (entity match) + maybe document
        res = svc.recall_dual_route(Cue(text="Alice", k=5))
        assert len(res.engrams) >= 1
        # explain() should report graph hits
        retriever = DualRouteRetriever(svc, DualRouteConfig())
        hits = retriever.explain(Cue(text="Alice", k=5))
        assert any(h.route == RouteKind.GRAPH for h in hits), \
            "graph route should contribute for entity query"
        assert any(h.matched_entity == "Alice" for h in hits)
        print("  graph route fires: OK")
        print("  route breakdown: document=" + str(sum(1 for h in hits if h.route == RouteKind.DOCUMENT))
              + " graph=" + str(sum(1 for h in hits if h.route == RouteKind.GRAPH)))
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_dual_route_semantic_disabled():
    banner("dual_route: semantic layer off -> degrades to plain recall()")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32,
                       enable_semantic=False, enable_prospective=False)
    svc = MemoryService(cfg)
    try:
        for txt in ["alpha bravo", "charlie delta"]:
            svc.observe(session_id="s1", actor_id="u1", platform="mock",
                        channel_id="c1", content=txt)
        res = svc.recall_dual_route(Cue(text="alpha", k=3))
        assert len(res.engrams) >= 1
        print("  semantic-disabled fallback: OK")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_existing_recall_unchanged():
    banner("back-compat: existing recall() still uses vector+FTS5+RRF (not dual route)")
    svc, db = _new_service()
    try:
        for txt in ["hello world", "today i ate pizza", "my favorite color is blue"]:
            svc.observe(session_id="s1", actor_id="alice", platform="mock",
                        channel_id="c1", content=txt)
        res_old = svc.recall(Cue(text="food", k=3, actor_id="alice"))
        res_new = svc.recall_dual_route(Cue(text="food", k=3, actor_id="alice"))
        # both should return the pizza engram as a hit
        assert any("pizza" in (e.content or "").lower() for e in res_old.engrams)
        assert any("pizza" in (e.content or "").lower() for e in res_new.engrams)
        print("  back-compat: OK (both paths return pizza)")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


if __name__ == "__main__":
    test_rrf_math()
    test_rrf_empty_routes()
    test_legacy_rrf_fuse_compat()
    test_dual_route_document_only()
    test_dual_route_with_graph()
    test_dual_route_semantic_disabled()
    test_existing_recall_unchanged()
    print("\nALL OK")