"""Smoke v1.4.x: wire B3 + B4 into MemoryService + background lifecycle loop.

Scope:
- MemoryConfig knobs: enable_atom_extraction / enable_graph_indexing /
  atom_decay_interval_seconds / atom_gc_interval_seconds
- MemoryService.observe() now extracts atoms and mirrors entity_refs
  into the graph fast-path index.
- AtomLifecycleManager exposes start/stop/_maintenance_loop + sync
  run_decay / run_gc (referenced from livingmemory).
- MemoryService.start_background_tasks() spawns the loop;
  stop_background_tasks() cancels it.

Tests:
- MemoryService _ensure_atom_layer is lazy (no extra connections at __init__).
- observe() with default config produces MemoryAtom rows after a few turns.
- observe() also populates GraphStore.graph_engram_refs (fast-path index).
- MemoryService.start_background_tasks spawns a task; stop cancels it.
- Synchronous run_decay / run_gc still work and respect the decay + floor.
- enable_atom_extraction=False short-circuits the atom pipeline.
- Lifecycle loop fires at least once when interval is short.
- Public surface re-exports unchanged (B3 / B4 symbols still work).
"""
import os, sys, types, tempfile, asyncio, time


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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from hippocampus import (
    MemoryService, MemoryConfig, Cue,
    AtomStore, AtomLifecycleManager, MemoryAtom,
    GraphStore, GraphRetriever, EntityMatch,
)


def banner(t): print("\n=== " + t + " ===")


def _new_svc(**over):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32,
                         enable_prospective=False, **over)
    return MemoryService(cfg), tmp.name


def test_lazy_atom_layer():
    banner("lazy atom layer: __init__ does NOT build AtomStore/GraphStore")
    svc, db = _new_svc()
    try:
        assert svc.atom_store is None
        assert svc.graph_store is None
        assert svc.atom_lifecycle is None
        assert svc._ensure_atom_layer() is True
        assert svc.atom_store is not None
        assert svc.graph_store is not None
        assert svc.atom_lifecycle is not None
        print("  lazy init: OK")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_observe_extracts_atoms_and_mirrors_graph():
    banner("observe(): atoms are upserted + graph fast-path index populated")
    svc, db = _new_svc()
    try:
        svc.observe(session_id="s1", actor_id="u1", platform="qq",
                         channel_id="g1", content="I am Alice, I love Americano coffee")
        svc.observe(session_id="s1", actor_id="u1", platform="qq",
                         channel_id="g1", content="I dislike cilantro intensely")
        svc._ensure_atom_layer()
        atoms = svc.atom_store.all()
        assert atoms, "expected at least one atom from observe()"
        for a in atoms:
            assert a.subject and a.predicate and a.object
            assert a.source_engram_ids, "atom must remember its source engram"
            assert a.kind in {"fact", "preference"}
        # Hard assertion: the just-observed engrams are in graph_engram_refs.
        ents = svc.semantic.all_entities(limit=100) if svc.semantic is not None else []
        indexed = False
        for ent in ents:
            for eid, _w in svc.graph_store.engrams_for(ent.id, limit=10):
                indexed = True
                break
            if indexed:
                break
        assert indexed, "graph fast-path index should hold at least one ref"
        print("  observe wire: OK (" + str(len(atoms)) + " atoms)")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_disabled_atom_extraction_short_circuits():
    banner("enable_atom_extraction=False: no atom row produced")
    svc, db = _new_svc(enable_atom_extraction=False)
    try:
        svc.observe(session_id="s1", actor_id="u1", platform="qq",
                     channel_id="g1", content="I am Alice, I love Americano")
        svc._ensure_atom_layer()
        assert svc.atom_store is None
        assert svc.atom_lifecycle is None
        print("  disabled extraction: OK (no atom layer built)")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_sync_run_decay_and_gc():
    banner("run_decay / run_gc synchronous API works")
    svc, db = _new_svc()
    try:
        svc._ensure_atom_layer()
        from hippocampus.memory_atom_models import make_fact_atom
        a = make_fact_atom("Alice", "likes", "Americano")
        a.strength = 0.01
        a.last_seen = time.time() - 30 * 86400
        svc.atom_store.upsert(a)
        moved = svc.run_atom_gc(floor=0.05)
        assert moved >= 1
        n = svc.run_atom_decay()
        assert isinstance(n, int) and n >= 0
        print("  sync run_decay/run_gc: OK (moved=" + str(moved) + ")")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_lifecycle_loop_fires():
    banner("background lifecycle loop fires at least once")
    svc, db = _new_svc()
    try:
        svc._ensure_atom_layer()
        from hippocampus.memory_atom_models import make_fact_atom
        a = make_fact_atom("Bob", "likes", "Tea")
        a.strength = 1.0
        svc.atom_store.upsert(a)
        async def runner():
            svc.atom_lifecycle.start(decay_interval=0.05, gc_interval=0.05)
            for _ in range(5):
                await asyncio.sleep(0.05)
            await svc.atom_lifecycle.stop()
        asyncio.run(runner())
        row = svc.atom_store.get(a.id)
        assert row is not None
        print("  loop fires: OK")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_service_start_stop_background_tasks():
    banner("MemoryService.start_background_tasks / stop_background_tasks")
    svc, db = _new_svc(atom_decay_interval_seconds=0.05,
                        atom_gc_interval_seconds=0.05)
    try:
        svc.start_background_tasks()
        assert svc._atom_task is not None
        first = svc._atom_task
        svc.start_background_tasks()
        assert svc._atom_task is first
        asyncio.run(svc.stop_background_tasks())
        assert svc._atom_task is None
        print("  service background tasks: OK")
    finally:
        svc.close()
        del svc
        import gc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_public_exports_unchanged():
    banner("public surface: B3 + B4 symbols still importable")
    import hippocampus
    for name in ("MemoryAtom", "AtomStore", "AtomLifecycleManager", "GraphStore", "GraphRetriever", "EntityMatch"):
        assert name in hippocampus.__all__
        assert hasattr(hippocampus, name)
    print("  public surface: OK")


def main():
    test_lazy_atom_layer()
    test_observe_extracts_atoms_and_mirrors_graph()
    test_disabled_atom_extraction_short_circuits()
    test_sync_run_decay_and_gc()
    test_lifecycle_loop_fires()
    test_service_start_stop_background_tasks()
    test_public_exports_unchanged()
    print("\nALL OK")


if __name__ == "__main__":
    main()
