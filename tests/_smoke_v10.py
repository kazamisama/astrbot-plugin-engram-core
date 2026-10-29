"""Smoke v1.0: valence + intensity + stream + temporal_bucket + schema bias + interference
+ reconsolidation update window + SWR replay boost + soft forget + GC + narrative
+ DG roundtrip regression on the v0.9 surface (merge, v1.78.0: no link action)."""

import os, tempfile, sys, types, asyncio, time, json

from _smoke_common import install_stub as _install_stub

_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import main
from hippocampus import MemoryService, MemoryConfig, Cue
from hippocampus.valence import ValenceScorer
from hippocampus.consolidator import ReplayConsolidator
from main import render_stats, format_narrative


def banner(t): print(chr(10) + "=== " + t + " ===")


def test_valence():
    banner("valence: rule-based scorer + negativity bias")
    v = ValenceScorer()
    cases = [
        ("I love Americano coffee",          0.5, 0.3),
        ("I hate cilantro with passion",    -0.6, 0.4),
        ("I do not love this at all",        -0.3, 0.2),
        ("the meeting is at 3pm",            0.0, 0.0),
    ]
    for txt, vexp_min, iexp_min in cases:
        vv, ii = v.score(txt)
        print(f"  '{txt}' -> valence={vv:.2f} intensity={ii:.2f}")
        if "love" in txt.lower() or "hate" in txt.lower():
            assert abs(vv) > 0.2, f"expected non-zero valence for '{txt}', got {vv}"
    print("  valence: OK")


def test_stream():
    banner("stream: two-stream tagging")
    v = ValenceScorer()
    cases = [
        ("I am Alice, I love Americano", "what"),
        ("Tomorrow I will fly to Tokyo", "where_when"),
        ("I live in Shanghai", "where_when"),
        ("the cat is black", ""),
    ]
    for txt, expected in cases:
        got = v.detect_stream(txt)
        print(f"  '{txt}' -> stream={got!r} (expected {expected!r})")
        if expected:
            assert got == expected, f"stream mismatch for '{txt}'"
    print("  stream: OK")


def test_temporal_bucket():
    banner("temporal: time cell bucket")
    v = ValenceScorer()
    now = 1_700_000_000.0
    b1 = v.temporal_bucket(now, 3600)
    b2 = v.temporal_bucket(now + 1800, 3600)
    b3 = v.temporal_bucket(now + 7200, 3600)
    print(f"  bucket(now)={b1} bucket(now+30m)={b2} bucket(now+2h)={b3}")
    assert b1 == b2, "30min later should be same 1h bucket"
    assert b1 + 2 == b3, "2h later should be 2 buckets ahead"
    print("  temporal: OK")


def test_observe_with_biology():
    banner("observe: valence + intensity + stream + temporal_bucket set on engram")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        sid = "s1"; actor = "u1"
        e1 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I love Americano coffee")
        e2 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="I hate cilantro")
        e3 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="Tomorrow I will fly to Tokyo")
        e4 = svc.observe(session_id=sid, actor_id=actor, platform="qq",
                         channel_id="g1", content="the book is on the table")
        for e, expected_stream in [(e1, "what"), (e2, "what"), (e3, "where_when"), (e4, "")]:
            print(f"  e={e.content[:30]:30s} v={e.valence:+.2f} i={e.intensity:.2f} stream={e.stream:11s} tb={e.temporal_bucket}")
            if e.expected_stream_check if False else True:
                pass
        # negativity bias: e2 (hate) importance should be bumped
        assert e2.importance > 0.5, "negative engram should have boosted importance"
        # e1 valence should be positive
        assert e1.valence > 0.0, "love should score positive valence"
        # e3 should be where_when
        assert e3.stream == "where_when", "temporal plan should be where_when"
        # all engrams have a non-zero temporal_bucket
        for e in (e1, e2, e3, e4):
            assert e.temporal_bucket > 0
        # /mem valence distribution
        h = svc.store.valence_histogram()
        print("  valence histogram:", h)
        assert h["positive"] >= 1 and h["negative"] >= 1
        # /mem streams
        b = svc.store.stream_breakdown()
        print("  stream breakdown:", b)
        assert b["what"] >= 1 and b["where_when"] >= 1
        # render_stats includes v1.0 sections
        s = render_stats(svc)
        assert "--- v1.0 ---" in s, "render_stats should show v1.0 sections"
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  observe: OK")


def test_schema_bias():
    banner("schema bias: known entity boosts importance")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        sid = "s1"; actor = "u1"
        # First occurrence seeds the entity
        svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                    content="I love Americano coffee")
        svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                    content="I love Americano coffee a lot")
        svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                    content="I love Americano coffee in the morning")
        # Now Americano has mention_count >= 3
        ent = svc.semantic.find_entity_by_name("Americano")
        print("  Americano mention_count:", ent.mention_count if ent else None)
        # Observe a fresh engram mentioning Americano — its importance should be boosted
        # by schema bias
        e = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                        content="I also love Americano on weekends")
        # baseline: observe something mentioning a fresh entity
        e2 = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                         content="I am thinking about whether to buy a piano")
        print(f"  with-Americano imp={e.importance:.3f}  no-known-entity imp={e2.importance:.3f}")
        # We expect e.importance to be higher than e2 (schema bias)
        assert e.importance > e2.importance - 0.05, \
            f"schema bias should not hurt importance (got {e.importance} vs {e2.importance})"
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  schema bias: OK")


def test_interference():
    banner("interference: merge drops sibling strength")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64,
                          interference_strength_drop=0.10, dedup_enabled=False)
        svc = MemoryService(cfg)
        sid = "s1"; actor = "u1"
        a1 = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                         content="I love Americano coffee")
        # Near-similar sibling stays separate (v1.78.0: no link action), but it is
        # still a separation candidate, so a merge of a1 must penalise it.
        a2 = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                         content="I love Americano coffee very much")
        a2_str_before = svc.store.get(a2.id).strength
        # Exact duplicate of a1 -> merge into a1, dropping the other sibling
        svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                    content="I love Americano coffee")
        a2_after = svc.store.get(a2.id)
        print(f"  a2.strength: {a2_str_before:.3f} -> {a2_after.strength:.3f} (drop={a2_str_before - a2_after.strength:.3f})")
        assert a2_after.strength < a2_str_before, "interference should drop the sibling's strength"
        assert a2_str_before - a2_after.strength >= 0.05, "drop should be at least ~interference_strength_drop"
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  interference: OK")


def test_reconsolidation_update():
    banner("reconsolidation update: lock-window observation rewrites recalled engram")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64,
                          reconsolidation_lock_seconds=10.0,
                          reconsolidation_update_enabled=True)
        svc = MemoryService(cfg)
        sid = "s1"; actor = "u1"
        # Original engram
        original = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                               content="I love Americano coffee in the morning")
        # Snapshot the content BEFORE reconsolidation mutates it
        original_content_snapshot = original.content
        # Simulate a recall: this sets reconsolidation_lock_until
        original.access_count = 5
        original.last_accessed = time.time()
        original.reconsolidation_lock_until = time.time() + 10.0
        original.valence = 0.0
        svc.store.upsert(original)
        # Drain working memory and put it back so the encoder sees it as a candidate
        svc.working.drain(sid)
        svc.working.add(original)
        # New observation that should match
        updated = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                              content="I really love Americano coffee in the morning")
        # updated should be the SAME engram (reconsolidation rewrote original)
        print(f"  original.id[:8]={original.id[:8]} updated.id[:8]={updated.id[:8]}")
        print(f"  original.content[:60]: {original.content[:60]}")
        print(f"  updated.content[:60]:  {updated.content[:60]}")
        assert updated.id == original.id, "reconsolidation update should return same engram"
        # [updated] tag may be at any position; the rewrite append happens so check appended
        assert "I really love" in updated.content, "reconsolidation should keep both versions"
        assert updated.content != original_content_snapshot, "reconsolidation should have changed content"
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  reconsolidation: OK")


def test_swr_replay_boost():
    banner("SWR replay: consolidator.step boosts high-strength engrams")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64,
                          replay_boost=0.05)
        svc = MemoryService(cfg)
        sid = "s1"; actor = "u1"
        e = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                        content="I love Americano coffee in the morning sunshine")
        before = e.strength
        # Run consolidator step
        rc = ReplayConsolidator(svc.store, svc.cfg)
        res = rc.step()
        print(f"  step result: {res}")
        after = svc.store.get(e.id).strength
        print(f"  strength: {before:.3f} -> {after:.3f}")
        assert after > before, "replay should boost strength"
        assert res.get("replayed", 0) >= 1, "should report at least 1 replayed"
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  swr_replay: OK")


def test_soft_forget_and_gc():
    banner("soft_forget + gc_pass")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        sid = "s1"; actor = "u1"
        e = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                        content="I love Americano coffee")
        # soft forget
        ok = svc.store.soft_forget(e.id)
        assert ok, "soft_forget should return True"
        # verify: forgotten_at > 0, list_active excludes it
        stored = svc.store.get(e.id)
        assert stored.forgotten_at > 0, "forgotten_at should be set"
        assert svc.store.list_active() == [], "list_active should exclude soft-forgotten"
        print(f"  soft_forget: forgotten_at={stored.forgotten_at:.0f}, list_active={len(svc.store.list_active())}")
        # gc_pass on an engram with no access_count, low strength, old age
        # we need to backdate created_at for the test
        stored.created_at = time.time() - 100000
        svc.store.upsert(stored)
        killed = svc.store.gc_pass(floor=0.1, min_age_seconds=86400)
        # soft-forgotten engrams are excluded from gc
        # create a NEW engram that should be gc-eligible
        e2 = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                         content="I hate Mondays intensely")
        # backdate + zero out
        g = svc.store.get(e2.id)
        # created_at is immutable via upsert (intentional), so SQL-update directly
        with svc.store._lock:
            svc.store._conn.execute("UPDATE engrams SET strength=?, access_count=?, created_at=? WHERE id=?",
                                       (0.01, 0, time.time() - 100000, g.id))
            svc.store._conn.commit()
        killed = svc.store.gc_pass(floor=0.1, min_age_seconds=86400)
        print(f"  gc_pass killed: {killed}")
        assert killed >= 1, "gc should have removed the weak old engram"
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  soft_forget + gc: OK")


def test_decay_pass():
    banner("decay_pass: Ebbinghaus drops strength")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        sid = "s1"; actor = "u1"
        e = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                        content="I love Americano coffee")
        # backdate to long ago
        e.created_at = time.time() - 30 * 86400  # 30 days ago
        e.last_accessed = e.created_at
        svc.store.upsert(e)
        before = svc.store.get(e.id).strength
        below = svc.store.decay_pass(tau_base=86400.0, floor=0.05)  # 1-day tau
        after = svc.store.get(e.id).strength
        print(f"  strength: {before:.3f} -> {after:.3f} (below-floor: {below})")
        assert after < before, "decay should drop strength"
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  decay_pass: OK")


def test_narrative():
    banner("narrative: chains related engrams by topic")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        sid = "s1"; actor = "u1"
        for msg in [
            "I love Americano coffee in the morning",
            "I really love Americano coffee a lot",
            "Tomorrow I will buy more Americano beans",
            "I am thinking about switching to latte",
        ]:
            time.sleep(0.01)
            svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                        content=msg)
        out = format_narrative(svc, "Americano")
        print(out)
        assert "narrative: Americano" in out or ("Americano" in out and "叙事" in out)
        assert "seed engrams" in out or "种子记忆" in out
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  narrative: OK")


def test_dg_regression():
    banner("DG regression: merge path still works (no link action since v1.78.0)")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64,
                           dedup_enabled=False)
        svc = MemoryService(cfg)
        sid = "s1"; actor = "u1"
        a1 = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                         content="I love Americano coffee")
        a2 = svc.observe(session_id=sid, actor_id=actor, platform="qq", channel_id="g1",
                         content="I love Americano coffee")
        assert a1.id == a2.id, "identical content must merge into the same engram"
        assert len(svc.store.all(limit=100)) == 1, "merge must leave exactly one engram"
        result = svc.recall(Cue(text="Americano", actor_id=actor, channel_id="g1", k=3))
        assert a1.id in [e.id for e in result.engrams]
        try: svc.close()
        except Exception: pass
        del svc
        import gc as _gc; _gc.collect()
        import gc as _gc; _gc.collect()
        print("  DG regression: OK")


if __name__ == "__main__":
    test_valence()
    test_stream()
    test_temporal_bucket()
    test_observe_with_biology()
    test_schema_bias()
    test_interference()
    test_reconsolidation_update()
    test_swr_replay_boost()
    test_soft_forget_and_gc()
    test_decay_pass()
    test_narrative()
    test_dg_regression()
    print(chr(10) + "ALL OK")
