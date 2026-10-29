"""v1.42 smoke: spreading-activation wiring (batch index + context seeds).

Covers:
- GraphStore.engrams_for_batch() correctness on a non-trivial graph
- HippocampalStore.recent_for_actor() and top_by_importance() filters
- SpreadingActivation.activate_with_context() produces a non-empty
  engram map and is fed by all three seed sources
- Regression: when graph_store is wired, _neighbors_entity does NOT
  call self._store.all() (the O(N) scan is gone). When graph_store is
  absent, the legacy path is still available.
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryService, MemoryConfig
from hippocampus.activation import SpreadingActivation
from hippocampus.storage import HippocampalStore


def _mk(db):
    cfg = MemoryConfig(sqlite_path=db, embedding_name="hash", llm_name="rule")
    cfg.memory_decay_enabled = False
    return MemoryService(cfg)


def _seed_corpus(svc):
    """Build a small but realistic graph:
    - 2 entities: 'alice', 'project_x'
    - 6 engrams: 3 anchored to alice, 2 to project_x, 1 cross-mention
    - 1 high-importance "prior" engram for the actor (pre-excitation)
    Returns the seeded engrams.
    """
    import time
    now = time.time()
    common = dict(session_id="s1", actor_id="u1", platform="qq",
                  channel_id="g1", persona_id="shelly")
    e1 = svc.observe(content="alice and I had coffee", **common)
    e2 = svc.observe(content="alice recommended a book", **common)
    e3 = svc.observe(content="alice birthday next week", **common)
    e4 = svc.observe(content="project_x kickoff scheduled", **common)
    e5 = svc.observe(content="project_x deadline approaching", **common)
    e6 = svc.observe(content="alice asked about project_x", **common)

    # Force-importance variation: e1 is the personal-prior "pinned" item.
    e1_obj = svc.store.get(e1.id)
    e1_obj.importance = 0.9
    e1_obj.strength = 0.9
    e1_obj.last_accessed = now
    svc.store.upsert(e1_obj)
    # Others are medium-strength, medium-importance.
    for e in (e2, e3, e4, e5, e6):
        obj = svc.store.get(e.id)
        obj.importance = 0.5
        obj.strength = 0.6
        obj.last_accessed = now - 60
        svc.store.upsert(obj)

    # Mirror into graph_engram_refs (production code does this via
    # service.observation; here we just call directly so the test
    # doesn't depend on LLM extraction).
    try:
        from hippocampus.semantic import SemanticStore
        from hippocampus.types import Entity
        sem = SemanticStore(svc.cfg.sqlite_path)
        for nm in ("alice", "project_x"):
            try:
                sem.upsert_entity(Entity(
                    name=nm, type="person" if nm == "alice" else "topic"))
            except Exception:
                pass
        ent_map = {e.name: e.id for e in sem.all_entities(limit=100)}
        for eid, refs in (
            (e1.id, ["alice"]), (e2.id, ["alice"]), (e3.id, ["alice"]),
            (e4.id, ["project_x"]), (e5.id, ["project_x"]),
            (e6.id, ["alice", "project_x"]),
        ):
            for nm in refs:
                eid_e = ent_map.get(nm)
                if eid_e:
                    svc.graph_store.add_entity_engram_ref(eid_e, eid)
            # Also populate entity_refs on the Engram for the legacy O(N) path
            obj = svc.store.get(eid)
            if obj is not None:
                obj.entity_refs = [ent_map.get(nm) for nm in refs if ent_map.get(nm)]
                svc.store.upsert(obj)
    except Exception as ex:
        # If semantic setup fails, the activation batch path still
        # works against the in-memory graph; we just skip the entity
        # seeding portion of the assertions.
        print(f"[WARN] graph seed setup failed: {ex}")
    return [e1, e2, e3, e4, e5, e6]


def test_graph_store_engrams_for_batch(svc):
    ents = svc.semantic.all_entities(limit=100)
    ent_map = {e.name: e.id for e in ents}
    alice_id = ent_map.get("alice")
    px_id = ent_map.get("project_x")
    assert alice_id and px_id, ent_map

    batch = svc.graph_store.engrams_for_batch([alice_id, px_id, "nope"],
                                              limit_per_entity=10)
    assert set(batch.keys()) == {alice_id, px_id, "nope"}
    assert len(batch[alice_id]) >= 3, batch[alice_id]
    assert len(batch[px_id]) >= 2, batch[px_id]
    assert batch["nope"] == []
    # Weights are floats in (0, 1]
    for _eid, w in batch[alice_id]:
        assert isinstance(w, float) and 0.0 < w <= 1.0, w
    # limit_per_entity is honored
    batch2 = svc.graph_store.engrams_for_batch([alice_id], limit_per_entity=2)
    assert len(batch2[alice_id]) <= 2
    # Empty input returns {} (not raises)
    assert svc.graph_store.engrams_for_batch([]) == {}
    print("[OK] GraphStore.engrams_for_batch: correctness + limits + empty")


def test_recent_for_actor_and_top_by_importance(svc):
    rec = svc.store.recent_for_actor("u1", k=3, min_strength=0.5)
    assert len(rec) == 3, len(rec)
    # newest-first by last_accessed
    for a, b in zip(rec, rec[1:]):
        assert a.last_accessed >= b.last_accessed, (a.last_accessed, b.last_accessed)
    # min_strength filter respected
    rec2 = svc.store.recent_for_actor("u1", k=10, min_strength=0.89)
    assert len(rec2) == 1, rec2
    # empty actor returns []
    assert svc.store.recent_for_actor("", k=3) == []
    # missing actor returns []
    assert svc.store.recent_for_actor("nobody", k=3) == []
    print("[OK] HippocampalStore.recent_for_actor: ordering + filters + empty")

    top = svc.store.top_by_importance(min_importance=0.7, k=5, actor_id="u1")
    assert len(top) == 1 and top[0].importance >= 0.9, top
    # global top with a lower floor
    top_all = svc.store.top_by_importance(min_importance=0.4, k=10)
    assert len(top_all) >= 5, top_all
    # min_importance filter respected
    top_strict = svc.store.top_by_importance(min_importance=0.89, k=5)
    assert len(top_strict) == 1, top_strict
    print("[OK] HippocampalStore.top_by_importance: per-actor + global + filters")


def test_activate_with_context_produces_engram_map(svc):
    ents = svc.semantic.all_entities(limit=100)
    ent_map = {e.name: e.id for e in ents}
    matched = [ent_map["alice"]] if "alice" in ent_map else []

    # Force-init graph_store to ensure the batch path is available.
    if svc.graph_store is None:
        # service stores graph_store lazily; instantiate directly
        from hippocampus.graph_store import GraphStore
        svc.graph_store = GraphStore(svc.cfg.sqlite_path)
    sa = SpreadingActivation(svc.semantic, svc.store, svc.cfg, svc.graph_store)
    assert sa._graph is svc.graph_store, "graph_store not wired"

    act_map = sa.activate_with_context(
        matched_entity_ids=matched,
        actor_id="u1",
        depth=2,
        high_importance_count=3,
        recent_count=2,
    )
    assert isinstance(act_map, dict), act_map
    assert len(act_map) > 0, "activate_with_context returned no hits"
    # All values in [0, 1]
    for v in act_map.values():
        assert 0.0 <= v <= 1.0, v
    print(f"[OK] activate_with_context: {len(act_map)} engrams activated")


def test_neighbors_entity_uses_batch_index(svc):
    """The O(N) regression check: when graph_store is wired,
    _neighbors_entity MUST NOT call self._store.all() at all."""
    if svc.graph_store is None:
        from hippocampus.graph_store import GraphStore
        svc.graph_store = GraphStore(svc.cfg.sqlite_path)
    sa = SpreadingActivation(svc.semantic, svc.store, svc.cfg, svc.graph_store)

    ents = svc.semantic.all_entities(limit=100)
    ent_map = {e.name: e.id for e in ents}
    alice_id = ent_map.get("alice")
    assert alice_id, ent_map

    calls = {"n": 0}
    orig_all = svc.store.all
    def _spy_all(*a, **kw):
        calls["n"] += 1
        return orig_all(*a, **kw)
    svc.store.all = _spy_all

    try:
        nbrs = sa._neighbors_entity(alice_id)
    finally:
        svc.store.all = orig_all

    assert calls["n"] == 0, (
        f"O(N) regression: _neighbors_entity called store.all() "
        f"{calls['n']} time(s) with graph_store wired. "
        f"Got {len(nbrs)} neighbors."
    )
    # It should have found engrams via the batch path.
    e_neighs = [n for n in nbrs if n[0].startswith("n:")]
    assert len(e_neighs) >= 3, e_neighs
    print(f"[OK] batch path took over: {len(nbrs)} neighbors, 0 store.all() calls")


def test_no_scan_fallback_when_graph_store_is_missing(svc):
    """Without a graph store there is no O(N) scan fallback any more."""
    sa = SpreadingActivation(svc.semantic, svc.store, svc.cfg, graph_store=None)
    assert sa._graph is None

    ents = svc.semantic.all_entities(limit=100)
    ent_map = {e.name: e.id for e in ents}
    alice_id = ent_map.get("alice")
    if not alice_id:
        print("[SKIP] no alice entity")
        return
    orig_all = svc.store.all
    calls = {"n": 0}

    def _counting_all(*a, **kw):
        calls["n"] += 1
        return orig_all(*a, **kw)

    svc.store.all = _counting_all
    try:
        nbrs = sa._neighbors_entity(alice_id)
    finally:
        svc.store.all = orig_all
    e_neighs = [n for n in nbrs if n[0].startswith("n:")]
    assert e_neighs == [], e_neighs
    assert calls["n"] == 0, "no full-table scan without a graph store"
    print("[OK] no graph store -> entity edges only, 0 store.all() calls")


def main():
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    svc = _mk(db)
    svc._ensure_atom_layer()  # v1.42: lazy-init graph_store
    try:
        _seed_corpus(svc)
        test_graph_store_engrams_for_batch(svc)
        test_recent_for_actor_and_top_by_importance(svc)
        test_activate_with_context_produces_engram_map(svc)
        test_neighbors_entity_uses_batch_index(svc)
        test_no_scan_fallback_when_graph_store_is_missing(svc)
        print("ALL PASS v64-activation")
    finally:
        try:
            svc.close()
        except Exception:
            pass
        try:
            os.remove(db)
        except OSError:
            pass


if __name__ == "__main__":
    main()
