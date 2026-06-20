"""Smoke v1.19 (B-2 relation layer): RelationStore supersede branches,
recall_relations pipeline filter, and end-to-end store_summary -> relations.
Uses astrbot stub (mirrors v41 header).
"""
import sys, os, tempfile, types


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
        @staticmethod
        def on_llm_response(*a, **k):
            def deco(fn): return fn
            return deco
    sm.Star = Star; sm.register = register; sm.Context = Context
    em.filter = _F; em.AstrMessageEvent = AstrMessageEvent; em.EventMessageType = _MT
    sys.modules["astrbot"] = a; sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm; sys.modules["astrbot.api.event"] = em


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.config import MemoryConfig
from hippocampus.llm import LLMProvider
from hippocampus.relation_store import RelationStore, Relation


def banner(m):
    print(chr(10) + "=== " + m + " ===")


def _rel(subj, pred, obj, conf, eid="e1"):
    return Relation(subject=subj, predicate=pred, object=obj,
                    confidence=conf, source_engram_id=eid)


def test_relationstore_branches():
    banner("RelationStore: insert / reinforce / supersede / candidate")
    tmp = tempfile.mkdtemp()
    rs = RelationStore(os.path.join(tmp, "r.db"))
    # insert
    rep = rs.add_with_supersede(_rel("A", "likes", "tea", 0.6))
    assert rep["action"] == "insert", rep
    assert rs.count_active() == 1
    # reinforce: same key + same object -> bump confidence, no new active
    rep = rs.add_with_supersede(_rel("A", "likes", "tea", 0.9))
    assert rep["action"] == "reinforce", rep
    assert rs.count_active() == 1
    acts = rs.all_active()
    assert abs(acts[0].confidence - 0.9) < 1e-9, acts[0].confidence
    # supersede: same key + diff object + conf >= old-hyst -> old forgotten
    rep = rs.add_with_supersede(_rel("A", "likes", "coffee", 0.95))
    assert rep["action"] == "supersede", rep
    assert rs.count_active() == 1
    assert rs.all_active()[0].object == "coffee"
    # candidate: same key + diff object + weaker -> kept forgotten, not active
    rep = rs.add_with_supersede(_rel("A", "likes", "juice", 0.10))
    assert rep["action"] == "candidate", rep
    assert rs.count_active() == 1
    assert rs.all_active()[0].object == "coffee"
    rs.close()
    print("PASS branches")


def test_supersede_hysteresis():
    banner("RelationStore: hysteresis lets a slightly-weaker new win")
    tmp = tempfile.mkdtemp()
    rs = RelationStore(os.path.join(tmp, "r.db"))
    rs.add_with_supersede(_rel("B", "works_at", "X", 0.80))
    # new is 0.75 (< 0.80) but within hysteresis 0.1 -> supersede
    rep = rs.add_with_supersede(_rel("B", "works_at", "Y", 0.75), hysteresis=0.1)
    assert rep["action"] == "supersede", rep
    assert rs.all_active()[0].object == "Y"
    rs.close()
    print("PASS hysteresis")


class _JsonLLM(LLMProvider):
    def name(self): return "json"
    def chat(self, system, user, **kw):
        return ('{"summary":"\u4f1a\u8bdd\u603b\u7ed3 A \u559c\u6b22 tea",'
                '"key_facts":["A \u559c\u6b22 tea"],"topics":["t"],'
                '"participants":["A"],'
                '"relations":[{"subject":"A","relation":"\u559c\u6b22","object":"tea","confidence":0.8}]}')


def _svc(tmp):
    from hippocampus.service import MemoryService
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, "h.db")
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.tiering_enabled = False
    svc = MemoryService(cfg=cfg)
    svc.register_llm("json", _JsonLLM())
    svc.set_llm("json")
    return svc


def test_store_summary_persists_relations():
    banner("store_summary writes relations -> relation_store active")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp)
    summary = {
        "summary": "A \u559c\u6b22 tea",
        "key_facts": ["A \u559c\u6b22 tea"],
        "topics": ["t"],
        "participants": ["A"],
        "relations": [{"subject": "A", "relation": "\u559c\u6b22",
                       "object": "tea", "confidence": 0.8}],
    }
    identity = {"actor_id": "A", "channel_id": "g1", "session_id": "s1",
                "platform": "qq", "memory_type": "episodic"}
    e = svc.store_summary(summary, identity)
    assert e is not None
    assert svc.relation_store.count_active() == 1
    a = svc.relation_store.all_active()[0]
    assert a.subject == "A" and a.object == "tea", (a.subject, a.object)
    assert a.source_engram_id == e.id
    svc.close()
    print("PASS store_summary relations")


def test_recall_relations_pipeline():
    banner("recall_relations: relevance + confidence + top-N filter")
    tmp = tempfile.mkdtemp()
    svc = _svc(tmp)
    rs = svc.relation_store
    rs.add_with_supersede(_rel("Alice", "\u559c\u6b22", "tea", 0.9))
    rs.add_with_supersede(_rel("Bob", "\u8ba8\u538c", "rain", 0.8))
    rs.add_with_supersede(_rel("Carol", "lives_in", "Tokyo", 0.2))
    # query mentions Alice -> only Alice relation is relevant
    out = svc.recall_relations("Alice \u4eca\u5929\u600e\u4e48\u6837", top_n=3)
    assert len(out) == 1 and out[0].subject == "Alice", [r.subject for r in out]
    # confidence threshold drops Carol even if mentioned
    out2 = svc.recall_relations("Carol lives_in Tokyo", top_n=3, min_confidence=0.5)
    assert out2 == [], out2
    # no match -> no noise
    out3 = svc.recall_relations("\u65e0\u5173\u8bdd\u9898", top_n=3)
    assert out3 == [], out3
    svc.close()
    print("PASS recall_relations")


if __name__ == "__main__":
    test_relationstore_branches()
    test_supersede_hysteresis()
    test_store_summary_persists_relations()
    test_recall_relations_pipeline()
    print(chr(10) + "ALL v42 PASS")
