"""Smoke v1.4 B3: MemoryAtom data layer -- atom store + lifecycle manager.

Scope of B3: data layer only. Does NOT touch MemoryService.observe().

Tests:
- MemoryAtom roundtrip: to_dict / from_dict / triple
- AtomStore.upsert: INSERT on new triple, MERGE on existing triple
- AtomStore CRUD: get / delete / count / count_by_type
- AtomStore.list_by_source_engram: JSON1 EXISTS query
- AtomStore.soft_forget + gc_pass: floor-based forgetting
- AtomLifecycleManager.extract_atoms_from_engram: best-effort extraction
- AtomLifecycleManager.merge_evidence: in-place merge + persist
- AtomLifecycleManager.decay_pass: exponential decay with per-type tau
- Hippocampus init: from hippocampus import AtomStore / AtomLifecycleManager
"""
import os, sys, types, tempfile, time


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
# Stand-alone data layer; no MemoryService needed for B3.
from hippocampus import (
    MemoryAtom, AtomStatus, AtomType, DecayType,
    AtomStore, AtomLifecycleManager,
)
from hippocampus.memory_atom_models import (
    triple_key, make_fact_atom, make_preference_atom,
)


def banner(t): print("\n=== " + t + " ===")


def test_memoryatom_roundtrip():
    banner("MemoryAtom roundtrip + triple + factories")
    a = make_fact_atom("Alice", "likes", "Americano",
                        source_engram_id="e-1", confidence=0.85)
    assert a.subject == "alice" and a.predicate == "likes" and a.object == "americano"
    assert a.kind == AtomType.FACT.value
    assert a.decay_type == DecayType.SEMANTIC.value
    assert a.triple == ("alice", "likes", "americano")
    assert a.source_engram_ids == ["e-1"]
    d = a.to_dict()
    b = MemoryAtom.from_dict(d)
    assert b.subject == a.subject and b.object == a.object
    assert b.confidence == a.confidence
    # roundtrip preserves the whole field set
    assert b.to_dict() == a.to_dict()
    # forward-compat: unknown field is silently dropped
    c = MemoryAtom.from_dict({**d, "future_field": "ignored"})
    assert c.subject == a.subject
    # preference factory uses preference decay
    p = make_preference_atom("user", "prefers", "dark_mode")
    assert p.kind == AtomType.PREFERENCE.value
    assert p.decay_type == DecayType.PREFERENCE.value
    # triple_key is canonical (lowercased + stripped)
    assert triple_key("  Alice ", "Likes", "AMERICANO") == ("alice", "likes", "americano")
    print("  roundtrip + factories: OK")


def test_atomstore_crud():
    banner("AtomStore CRUD: insert / get / count / count_by_type")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    try:
        store = AtomStore(tmp.name)
        assert store.count() == 0
        a1 = make_fact_atom("Alice", "likes", "Americano")
        a2 = make_fact_atom("Alice", "lives_in", "Shanghai")
        a3 = make_preference_atom("user", "prefers", "dark_mode")
        store.upsert(a1); store.upsert(a2); store.upsert(a3)
        assert store.count() == 3
        cbt = store.count_by_type()
        assert cbt[AtomType.FACT.value] == 2
        assert cbt[AtomType.PREFERENCE.value] == 1
        got = store.get(a1.id)
        assert got is not None and got.object == "americano"
        assert store.delete(a1.id) is True
        assert store.get(a1.id) is None
        assert store.count() == 2
        # delete of missing id is False
        assert store.delete("atom:does-not-exist") is False
        store.close()
        print("  CRUD: OK (3 inserted, 1 deleted)")
    finally:
        import gc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_atomstore_upsert_merge():
    banner("AtomStore.upsert MERGE on same triple (case-insensitive)")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    try:
        store = AtomStore(tmp.name)
        first = make_fact_atom("Alice", "likes", "Americano", source_engram_id="e-1",
                                 confidence=0.5)
        store.upsert(first)
        assert store.count() == 1
        original_id = store.all()[0].id
        # Second observation: same triple (case + whitespace variant), new source.
        second = make_fact_atom("  alice ", "LIKES", "AMERICANO", source_engram_id="e-2",
                                  confidence=0.9)
        merged = store.upsert(second)
        # Still one row; original id preserved.
        assert store.count() == 1
        row = store.all()[0]
        assert row.id == original_id, "upsert must preserve the original id"
        assert row.confidence == 0.9, "confidence should take the max"
        assert row.evidence_count == 1, "upsert takes max(existing, caller); both are 1 here"
        assert set(row.source_engram_ids) == {"e-1", "e-2"}, "sources should union"
        assert store.by_triple("alice", "likes", "AMERICANO") is not None
        store.close()
        print("  upsert merge: OK")
    finally:
        import gc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_atomstore_list_by_source_engram():
    banner("AtomStore.list_by_source_engram (JSON1 EXISTS)")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    try:
        store = AtomStore(tmp.name)
        a1 = make_fact_atom("Alice", "likes", "Americano", source_engram_id="e-1")
        a2 = make_fact_atom("Alice", "lives_in", "Shanghai", source_engram_id="e-1")
        a3 = make_fact_atom("Bob", "likes", "Tea", source_engram_id="e-2")
        store.upsert(a1); store.upsert(a2); store.upsert(a3)
        e1_atoms = store.list_by_source_engram("e-1")
        assert len(e1_atoms) == 2
        e2_atoms = store.list_by_source_engram("e-2")
        assert len(e2_atoms) == 1 and e2_atoms[0].object == "tea"
        e3_atoms = store.list_by_source_engram("e-none")
        assert e3_atoms == []
        store.close()
        print("  list_by_source: OK (2/1/0)")
    finally:
        import gc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_atomstore_soft_forget_and_gc():
    banner("AtomStore.soft_forget + gc_pass (floor-based)")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    try:
        store = AtomStore(tmp.name)
        strong = make_fact_atom("Alice", "likes", "Americano")
        weak = make_fact_atom("Bob", "likes", "Tea")
        weak.strength = 0.01
        store.upsert(strong); store.upsert(weak)
        # soft_forget on strong
        assert store.soft_forget(strong.id) is True
        assert store.get(strong.id).status == AtomStatus.SOFT_FORGOTTEN.value
        assert store.count(AtomStatus.ACTIVE.value) == 1
        # gc_pass: floor=0.05 should sweep weak
        moved = store.gc_pass(floor=0.05)
        assert moved == 1
        assert store.get(weak.id).status == AtomStatus.GC.value
        # gc on a fresh DB returns 0
        assert AtomStore(tmp.name).gc_pass(floor=0.05) == 0
        store.close()
        print("  soft_forget + gc_pass: OK")
    finally:
        import gc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_lifecycle_extract():
    banner("AtomLifecycleManager.extract_atoms_from_engram")

    class FakeEngram:
        def __init__(self):
            self.id = "e-1"
            self.actor_id = "alice"
            self.platform = "qq"
            self.channel_id = "c1"

    class GoodExtractor:
        def extract_atoms(self, engram):
            return [
                {"subject": "Alice", "predicate": "likes", "object": "Americano",
                 "kind": "fact", "confidence": 0.9, "importance": 0.6,
                 "decay_type": "semantic"},
                {"subject": "user", "predicate": "prefers", "object": "dark_mode",
                 "kind": "preference", "confidence": 0.8},
                # bad rows: missing / blank fields should be skipped
                {"subject": "", "predicate": "x", "object": "y"},
                {"subject": "no-predicate", "object": "y"},
            ]

    class BadExtractor:
        def extract_atoms(self, engram):
            raise RuntimeError("kaboom")

    class NoExtractor:
        pass

    e = FakeEngram()
    mgr = AtomLifecycleManager(store=None)
    good = mgr.extract_atoms_from_engram(e, GoodExtractor())
    assert len(good) == 2
    assert good[0].kind == "fact" and good[0].object == "americano"
    assert good[1].kind == "preference" and good[1].decay_type == DecayType.PREFERENCE.value
    assert all(a.source_engram_ids == ["e-1"] for a in good)
    # Defensive: extractor throws -> empty list, no crash.
    assert mgr.extract_atoms_from_engram(e, BadExtractor()) == []
    # Defensive: extractor missing -> empty list.
    assert mgr.extract_atoms_from_engram(e, NoExtractor()) == []
    print("  extract: OK (2 good, 2 bad rows skipped, 1 throw caught)")


def test_lifecycle_merge_evidence():
    banner("AtomLifecycleManager.merge_evidence")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    try:
        store = AtomStore(tmp.name)
        mgr = AtomLifecycleManager(store=store)
        a = make_fact_atom("Alice", "likes", "Americano", source_engram_id="e-1",
                            confidence=0.5, importance=0.4)
        a.strength = 0.7
        store.upsert(a)
        b = make_fact_atom("Alice", "likes", "Americano", source_engram_id="e-2",
                            confidence=0.9, importance=0.6)
        b.strength = 0.95
        out = mgr.merge_evidence(a, b)
        assert out is a
        # Persisted via store: only one row, merged.
        assert store.count() == 1
        row = store.get(a.id)
        assert row.evidence_count == 2, "a.merge(b) bumped a to 2 in memory; store max(1, 2) = 2"
        assert row.confidence == 0.9
        assert row.strength == 0.95
        assert set(row.source_engram_ids) == {"e-1", "e-2"}
        store.close()
        print("  merge_evidence: OK")
    finally:
        import gc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_lifecycle_decay_pass():
    banner("AtomLifecycleManager.decay_pass (exponential)")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    try:
        store = AtomStore(tmp.name)
        mgr = AtomLifecycleManager(store=store)
        # Plant an atom and backdate last_seen so decay has teeth.
        a = make_fact_atom("Alice", "likes", "Americano")
        a.strength = 1.0
        a.last_seen = time.time() - 10 * 86400  # 10 days ago
        store.upsert(a)
        # tau_base = 1 day, semantic multiplier = 4 -> tau = 4 days
        # 10 days => factor = exp(-10/4) ~= 0.082, floor=0.05 -> soft_forget
        mgr.decay_pass(tau_base=86400.0, floor=0.1)
        row = store.get(a.id)
        # After 10 days at tau=4d, strength should be well below 0.1.
        assert row.strength < 0.1
        assert row.status == AtomStatus.SOFT_FORGOTTEN.value

        # Now a fresh atom should not decay noticeably.
        b = make_fact_atom("Bob", "likes", "Tea")
        b.strength = 1.0
        store.upsert(b)
        mgr.decay_pass(tau_base=86400.0, floor=0.05)
        b_row = store.get(b.id)
        assert b_row.status == AtomStatus.ACTIVE.value
        assert b_row.strength > 0.99

        # Disable decay for a type via multiplier=0 -> no decay.
        c = make_fact_atom("Carol", "likes", "Juice")
        c.strength = 1.0
        c.last_seen = time.time() - 30 * 86400
        store.upsert(c)
        mgr.decay_pass(tau_base=86400.0, floor=0.0,
                       decay_type_multiplier={"semantic": 0.0})
        c_row = store.get(c.id)
        assert c_row.strength == 1.0, "multiplier=0 must skip decay"

        store.close()
        print("  decay_pass: OK (semantic forgets, preference-like stays)")
    finally:
        import gc; gc.collect()
        try: os.unlink(tmp.name)
        except Exception: pass


def test_public_exports():
    banner("hippocampus package exports the B3 symbols")
    import hippocampus
    for name in ("MemoryAtom", "AtomStatus", "AtomType", "DecayType",
                 "AtomStore", "AtomLifecycleManager"):
        assert name in hippocampus.__all__, name
        assert hasattr(hippocampus, name), name
    print("  public surface: OK")


def main():
    test_memoryatom_roundtrip()
    test_atomstore_crud()
    test_atomstore_upsert_merge()
    test_atomstore_list_by_source_engram()
    test_atomstore_soft_forget_and_gc()
    test_lifecycle_extract()
    test_lifecycle_merge_evidence()
    test_lifecycle_decay_pass()
    test_public_exports()
    print("\nALL OK")


if __name__ == "__main__":
    main()
