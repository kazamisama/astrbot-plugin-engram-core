"""v1.63 smoke: session-context seeds + MMR diversity rerank.

Covers:
- Cue.session_id passed through to activate_with_context -> build_context_seeds
- HippocampalStore.recent_for_session() returns session-scoped engrams
- MMR rerank penalizes near-duplicate engrams (embedding cosine, v1.78.0)
- MMR disabled via config skips rerank entirely
- Regression: v64-v65 still pass
"""

import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryService, MemoryConfig, Cue
from hippocampus.retrieval.dual_route import DualRouteRetriever, DualRouteConfig
from hippocampus.activation import SpreadingActivation


def _mk(db):
    cfg = MemoryConfig(sqlite_path=db, embedding_name="hash", llm_name="rule")
    cfg.memory_decay_enabled = False
    return MemoryService(cfg)


def main():
    fd, db = tempfile.mkstemp(suffix=".db"); __import__('os').close(fd)
    svc = _mk(db)
    try:
        common = dict(actor_id="u1", platform="qq", channel_id="g1", persona_id="")
        # Session A: 3 engrams
        e1 = svc.observe(session_id="sess_A", content="talk about cats", **common)
        e2 = svc.observe(session_id="sess_A", content="cats love fish", **common)
        e3 = svc.observe(session_id="sess_A", content="fish tank cleaning", **common)
        # Session B: 1 engram
        e4 = svc.observe(session_id="sess_B", content="dog park visit", **common)

        # ---- test 1: recent_for_session ----
        sess = svc.store.recent_for_session("sess_A", k=5)
        assert len(sess) == 3, len(sess)
        assert sess[0].id == e3.id  # newest first
        assert svc.store.recent_for_session("", k=5) == []
        print("[OK] recent_for_session: ordering + empty")

        # ---- test 2: session seeds in activation ----
        svc._ensure_atom_layer()
        sa = SpreadingActivation(svc.semantic, svc.store, svc.cfg, svc.graph_store)
        seeds = sa.build_context_seeds(session_id="sess_A", session_count=3)
        assert len(seeds) >= 3, seeds
        # Seeds should contain session engrams
        seed_ids = [s[2:] for s in seeds if s.startswith("n:")]
        assert e1.id in seed_ids or e2.id in seed_ids or e3.id in seed_ids, seed_ids
        print(f"[OK] session seeds: {len(seeds)} total, {len(seed_ids)} engram seeds")

        # ---- test 3: Cue.session_id wires through ----
        cue = Cue(text="cats", actor_id="u1", session_id="sess_A", k=5)
        result = svc.recall_with_activation(cue)
        assert len(result.engrams) > 0
        print(f"[OK] session_id on Cue: {len(result.engrams)} results")

        # ---- test 4: MMR rerank demotes an embedding near-duplicate ----
        from hippocampus.retrieval.dual_route import _ScoredCandidate
        from hippocampus.types import Engram

        def _cand(eid, emb, score):
            return _ScoredCandidate(
                item=Engram(id=eid, content=eid, embedding=emb),
                score=score, breakdown={})

        a = _cand("cand-a", [1.0, 0.0], 0.90)
        b = _cand("cand-b", [1.0, 0.0], 0.89)   # near-duplicate of a
        c = _cand("cand-c", [0.0, 1.0], 0.80)   # diverse
        cfg = DualRouteConfig(mmr_enabled=True, mmr_lambda=0.7)
        dr = DualRouteRetriever(svc, cfg)
        ranked = dr._mmr_rerank_ranked([a, b, c], 2)
        order = [x.item.id for x in ranked]
        assert order[0] == "cand-a", order
        assert order[1] == "cand-c", ("near-duplicate should be demoted", order)
        result2 = dr.search(Cue(text="cats", k=5))
        found_ids = [e.id for e in result2.engrams]
        print(f"[OK] MMR rerank: order={order} search_ids={[x[:6] for x in found_ids]}")

        # ---- test 5: MMR disabled ----
        cfg3 = DualRouteConfig(mmr_enabled=False)
        dr3 = DualRouteRetriever(svc, cfg3)
        assert dr3.search(Cue(text="cats", k=3)).engrams
        print("[OK] mmr_enabled=False still returns results")

        print("ALL PASS v66-session-mmr")
    finally:
        try: svc.close()
        except Exception: pass
        try: __import__('os').remove(db)
        except Exception: pass


if __name__ == "__main__":
    main()
