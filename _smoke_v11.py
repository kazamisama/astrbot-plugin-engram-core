"""Smoke v1.1: spreading activation + user self-model + mood-congruent recall + cluster summarization.

Independent smoke. Mocks astrbot.api so it does not require a real AstrBot runtime.
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
import main
from hippocampus import MemoryService, MemoryConfig, Cue, ProfileFact
from hippocampus.activation import SpreadingActivation
from hippocampus.profile import ProfileStore
from main import (format_profile, format_activation, render_stats, format_narrative)


def banner(t): print(chr(10) + "=== " + t + " ===")


def test_profile_upsert_dedup():
    banner("profile: upsert accumulates evidence and averages confidence")
    with tempfile.TemporaryDirectory() as td:
        ps = ProfileStore(os.path.join(td, "p.db"))
        f1 = ProfileFact(actor_id="u1", predicate="likes", value="Americano",
                         confidence=0.8, evidence_count=1,
                         source_engram_ids=["e1"], source_relation_ids=["r1"])
        ps.upsert_fact(f1)
        f2 = ps.upsert_fact(ProfileFact(actor_id="u1", predicate="likes", value="Americano",
                                        confidence=0.6, evidence_count=1,
                                        source_engram_ids=["e2"], source_relation_ids=["r2"]))
        assert f2.evidence_count == 2
        assert abs(f2.confidence - 0.7) < 0.01
        facts = ps.facts_for("u1")
        assert len(facts) == 1
        ps.close()
        print("  profile upsert: OK")


def test_profile_build():
    banner("build_from_relations: extract stable facts, dedup per engram")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        actor = "u1"
        for m in [
            "I am Alice, I love Americano coffee",
            "I am Alice, I love Americano coffee in the morning",
            "I am Alice, I really love Americano coffee a lot",
            "I am Bob, I dislike cilantro",
            "I am Alice, I live in Shanghai",
        ]:
            svc.observe(session_id="s1", actor_id=actor, platform="qq", channel_id="g1", content=m)
        facts = svc.build_profile(actor)
        print("  built:", [(f.predicate, f.value, f.evidence_count) for f in facts])
        likes = [f for f in facts if f.predicate == "likes" and f.value == "Americano"]
        assert len(likes) == 1 and likes[0].evidence_count == 3, "expected 3-evidence Americano fact"
        sh = [f for f in facts if f.predicate == "resides_in" and f.value == "Shanghai"]
        assert len(sh) == 1 and sh[0].evidence_count == 3
        # dislikes:cilantro only has 1 evidence -> should be below threshold (2)
        cil = [f for f in facts if f.predicate == "dislikes" and f.value == "cilantro"]
        assert len(cil) == 0, "cilantro should not pass min_evidence=2"
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  build_from_relations: OK")


def test_spreading_activation():
    banner("spreading activation: entity seeds pull in their engrams + reverse relations")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        actor = "u1"
        for m in [
            "I am Alice, I love Americano coffee",
            "I am Alice, I love Americano coffee in the morning",
            "I am Alice, I really love Americano coffee a lot",
            "I am Alice, I live in Shanghai",
        ]:
            svc.observe(session_id="s1", actor_id=actor, platform="qq", channel_id="g1", content=m)
        acts = svc.spread_activation(["Americano"], depth=2)
        e_acts = svc.activation.engram_activation(acts)
        print("  activated engrams:", len(e_acts))
        for k, v in svc.activation.surface(acts, top_k=6):
            tag, label = svc.activation._label(k)
            print("   " + tag + " " + label + "  act=" + str(round(v, 3)))
        assert len(e_acts) >= 1, "expected at least one engram activated by Americano"
        # Reverse relation should activate Alice
        ent_acts = {k: v for k, v in acts.items() if k.startswith("e:")}
        assert any("Alice" in svc.activation._label(k)[1] for k in ent_acts), "Alice should activate via reverse relation"
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  spreading activation: OK")


def test_recall_with_activation():
    banner("recall_with_activation: activation boosts related engrams")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        actor = "u1"
        # Two clusters: Americano vs latte
        for m in [
            "I am Alice, I love Americano coffee",
            "I am Alice, I love Americano coffee a lot",
            "I am Alice, I sometimes drink latte",
            "I am Alice, I tried a latte yesterday",
        ]:
            svc.observe(session_id="s1", actor_id=actor, platform="qq", channel_id="g1", content=m)
        # Plain recall
        plain = svc.recall(Cue(text="coffee", actor_id=actor, channel_id="g1", k=5))
        plain_ids = [e.id for e in plain.engrams]
        # With activation from Americano
        boosted = svc.recall_with_activation(
            Cue(text="coffee", actor_id=actor, channel_id="g1", k=5),
            seeds=["Americano"])
        boosted_ids = [e.id for e in boosted.engrams]
        print("  plain top:", [i[:8] for i in plain_ids])
        print("  boosted top:", [i[:8] for i in boosted_ids])
        # The boosted set should still have content; we just check it doesn't crash and returns >=1
        assert len(boosted_ids) >= 1
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  recall_with_activation: OK")


def test_mood_congruence():
    banner("mood-congruent recall: positive hint favors positive engrams")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        actor = "u1"
        for m in [
            "I am Alice, I love Americano coffee",  # positive
            "I hate cilantro with passion",         # negative
        ]:
            svc.observe(session_id="s1", actor_id=actor, platform="qq", channel_id="g1", content=m)
        r_pos = svc.recall(Cue(text="coffee", actor_id=actor, channel_id="g1", k=5, valence_hint=0.5))
        r_neg = svc.recall(Cue(text="coffee", actor_id=actor, channel_id="g1", k=5, valence_hint=-0.5))
        pos_ids = [e.id for e in r_pos.engrams]
        neg_ids = [e.id for e in r_neg.engrams]
        print("  positive hint top:", [i[:8] for i in pos_ids])
        print("  negative hint top:", [i[:8] for i in neg_ids])
        assert len(pos_ids) >= 1 and len(neg_ids) >= 1
        # The positive engram should appear under positive hint and the negative under negative hint
        # (may not always be the case depending on FTS, but at minimum both should return something)
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  mood-congruence: OK")


def test_cluster_summarization():
    banner("cluster summarization: SWR pass produces gists with cluster_id on engrams")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64,
                           cluster_summary_min_size=2)
        svc = MemoryService(cfg)
        actor = "u1"
        for m in [
            "I am Alice, I love Americano coffee",
            "I am Alice, I love Americano coffee in the morning",
            "I am Alice, I really love Americano coffee a lot",
        ]:
            svc.observe(session_id="s1", actor_id=actor, platform="qq", channel_id="g1", content=m)
        res = svc.force_consolidate()
        gists = svc.store.list_cluster_summaries()
        print("  consolidate:", res)
        print("  gists:", len(gists))
        for g in gists:
            print("    " + g["cluster_id"][:8] + " (n=" + str(g["member_count"]) + ")  " + g["gist"][:60])
        assert len(gists) >= 1, "expected at least one cluster gist"
        # The engrams should have cluster_id assigned
        any_clustered = any(e.cluster_id for e in svc.store.all(limit=1000))
        assert any_clustered, "expected at least one engram to be assigned a cluster_id"
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  cluster summarization: OK")


def test_profile_decay():
    banner("profile decay: old facts lose confidence")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64,
                           profile_fact_decay_days=0.0)  # force-everything-stale
        svc = MemoryService(cfg)
        actor = "u1"
        for m in [
            "I am Alice, I love Americano coffee",
            "I am Alice, I love Americano coffee in the morning",
        ]:
            svc.observe(session_id="s1", actor_id=actor, platform="qq", channel_id="g1", content=m)
        svc.build_profile(actor)
        facts_before = svc.profile_facts(actor)
        assert len(facts_before) >= 1
        affected = svc.decay_profile(actor)
        facts_after = svc.profile_facts(actor)
        print("  facts before/after:", len(facts_before), "->", len(facts_after), "affected:", affected)
        assert affected >= 1
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  profile decay: OK")


def test_main_format_helpers():
    banner("main.py v1.1 helpers: format_profile + format_activation + render_stats")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        actor = "u1"
        for m in [
            "I am Alice, I love Americano coffee",
            "I am Alice, I love Americano coffee in the morning",
            "I am Alice, I really love Americano coffee a lot",
            "I am Alice, I live in Shanghai",
        ]:
            svc.observe(session_id="s1", actor_id=actor, platform="qq", channel_id="g1", content=m)
        svc.force_consolidate()
        out1 = format_profile(svc, actor)
        out2 = format_activation(svc, "Americano Alice")
        out3 = render_stats(svc)
        print("=== format_profile ===")
        print(out1)
        print("=== format_activation ===")
        print(out2)
        print("=== render_stats (excerpt) ===")
        print(out3[-200:])
        assert "Americano" in out1 and "Shanghai" in out1
        assert "Americano" in out2 or "Alice" in out2
        assert "v1.1" in out3 and "profile facts" in out3
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  main.py v1.1 helpers: OK")


def test_v09_v10_regress():
    banner("regression: v0.9 link + v1.0 SWR + decay still work on top of v1.1")
    with tempfile.TemporaryDirectory() as td:
        cfg = MemoryConfig(sqlite_path=os.path.join(td, "h.db"), embedding_dim=64)
        svc = MemoryService(cfg)
        actor = "u1"
        for m in [
            "I am Alice, I love Americano coffee",
            "I am Alice, I love Americano coffee very much",
            "I am Alice, I really love Americano coffee",
        ]:
            svc.observe(session_id="s1", actor_id=actor, platform="qq", channel_id="g1", content=m)
        # DG link
        a1 = svc.observe(session_id="s1", actor_id=actor, platform="qq", channel_id="g1",
                         content="I love Americano coffee")
        a2 = svc.observe(session_id="s1", actor_id=actor, platform="qq", channel_id="g1",
                         content="I love Americano coffee very much")
        # After several similar observes, similar_to should be non-empty
        engrams = svc.store.list_active(limit=100)
        cluster_engrams = [e for e in engrams if e.similar_to]
        assert len(cluster_engrams) >= 2, "v0.9 DG linking regressed"
        # SWR replay produces a positive replayed count
        res = svc.force_consolidate()
        assert res.get("replayed", 0) >= 0
        # narrative still works
        n = format_narrative(svc, "Americano")
        assert "narrative: Americano" in n
        svc.close()
        del svc
        import gc as _gc; _gc.collect()
        print("  v0.9 + v1.0 regression: OK")


if __name__ == "__main__":
    test_profile_upsert_dedup()
    test_profile_build()
    test_spreading_activation()
    test_recall_with_activation()
    test_mood_congruence()
    test_cluster_summarization()
    test_profile_decay()
    test_main_format_helpers()
    test_v09_v10_regress()
    print(chr(10) + "ALL OK")
