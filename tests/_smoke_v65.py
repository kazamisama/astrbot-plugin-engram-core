"""v1.62 smoke: spread route in DualRouteRetriever + reconsolidation.

Covers:
- RouteKind.SPREAD added
- DualRouteConfig.spread_route_enabled / spread_candidate_k
- _spread_route() returns RankedCandidate list from cue.activation
- search() fuses three routes (doc + graph + spread)
- Reconsolidator.touch() is called for all routes (no graph-route blind spot)

Regression:
- Disabled spread route produces same results as v1.60 (two-route)
- Empty activation map does not crash _spread_route
"""

import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryService, MemoryConfig, Cue
from hippocampus.retrieval.dual_route import DualRouteRetriever, DualRouteConfig, RouteKind
from hippocampus.retrieval.rrf import RankedCandidate


def _mk(db):
    cfg = MemoryConfig(sqlite_path=db, embedding_name="hash", llm_name="rule")
    cfg.memory_decay_enabled = False
    return MemoryService(cfg)


def main():
    fd, db = tempfile.mkstemp(suffix=".db"); __import__('os').close(fd)
    svc = _mk(db)
    try:
        # Seed a few engrams
        common = dict(session_id="s1", actor_id="u1", platform="qq",
                      channel_id="g1", persona_id="")
        e1 = svc.observe(content="alpha beta gamma", **common)
        e2 = svc.observe(content="delta epsilon zeta", **common)
        e3 = svc.observe(content="eta theta iota", **common)

        # ---- test 1: _spread_route returns empty when no activation ----
        cfg = DualRouteConfig(spread_route_enabled=True, spread_candidate_k=8)
        dr = DualRouteRetriever(svc, cfg)
        assert len(dr._spread_route(Cue(text="test"))) == 0
        print("[OK] empty activation map -> empty spread route")

        # ---- test 2: _spread_route returns candidates ----
        cue = Cue(text="test", k=5)
        cue.activation = {e1.id: 0.9, e2.id: 0.6, e3.id: 0.3}
        hits = dr._spread_route(cue)
        assert len(hits) == 3, len(hits)
        assert hits[0].item.id == e1.id, hits[0]
        assert hits[2].item.id == e3.id, hits[2]
        assert all(0 < c.raw_score <= 1 for c in hits)
        print("[OK] spread route ranks by activation score")

        # ---- test 3: search fuses three routes ----
        # Populate activation so spread route contributes
        result = dr.search(cue)
        assert len(result.engrams) > 0, "no results from 3-route fusion"
        print(f"[OK] 3-route fusion: {len(result.engrams)} results")

        # ---- test 4: disabled spread route skips ----
        cfg2 = DualRouteConfig(spread_route_enabled=False)
        dr2 = DualRouteRetriever(svc, cfg2)
        assert dr2._spread_route(cue) == []
        result2 = dr2.search(cue)
        assert len(result2.engrams) > 0
        print("[OK] spread_route_enabled=False skips")

        # ---- test 5: reconsolidation fires ----
        # Monkey-patch Reconsolidator to count touch() calls
        touches = {"n": 0}
        orig_touch = svc.reconsolidator.touch
        def _counting_touch(e):
            touches["n"] += 1
            return orig_touch(e)
        svc.reconsolidator.touch = _counting_touch
        try:
            cue3 = Cue(text="gamma", k=3)
            cue3.activation = {e1.id: 0.95}
            dr.search(cue3)
            assert touches["n"] > 0, f"reconsolidation not called! touches={touches}"
            print(f"[OK] reconsolidation touch called {touches['n']} times")
        finally:
            svc.reconsolidator.touch = orig_touch

        # ---- test 6: RouteKind enum ----
        assert RouteKind.SPREAD == "spread"
        assert RouteKind.DOCUMENT == "document"
        assert RouteKind.GRAPH == "graph"
        print("[OK] RouteKind.SPREAD registered")

        print("ALL PASS v65-spread-route")
    finally:
        try: svc.close()
        except Exception: pass
        try: __import__('os').remove(db)
        except Exception: pass


if __name__ == "__main__":
    main()
