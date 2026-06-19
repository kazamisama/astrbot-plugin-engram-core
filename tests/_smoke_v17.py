"""Smoke v1.4 TextProcessor: tokenize / stopwords / negation / dual route.

Tests the new hippocampus/processors/ package and the dual_route patch.
Verifies:
- ASCII / CJK / mixed tokenization
- stopword removal (incl. not/no/nor kept as negations)
- negation window marking (3 tokens after negation)
- fts_preprocess shape compat with legacy cjk_split
- embed_preprocess adds NOT_ prefix to negated content tokens
- dual_route graph route picks up entity when query is multi-token
- register_stopwords / register_negations / reset
"""
import os, tempfile, sys, types


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
import main
from hippocampus import MemoryService, MemoryConfig, Cue
from hippocampus.processors import TextProcessor, stopwords
from hippocampus.processors.text_processor import (
    _char_level_tokenize, _is_cjk, _has_jieba,
)


def banner(t): print("\n=== " + t + " ===")


def test_jieba_status():
    banner("jieba availability: " + str(_has_jieba()))
    # We do not require jieba; tests should pass either way.


def test_ascii_tokenize():
    banner("tokenize: ASCII")
    tp = TextProcessor
    assert tp.tokenize("I love Americano coffee") == ["I", "love", "Americano", "coffee"]
    assert tp.tokenize("") == []
    print("  ASCII: OK")


def test_cjk_tokenize():
    banner("tokenize: CJK + mixed")
    tp = TextProcessor
    toks = tp.tokenize("我爱 Americano 咖啡")
    assert "Americano" in toks
    # CJK chars split per-char in fallback mode
    cjk_count = sum(1 for t in toks if _is_cjk(t) or any(_is_cjk(c) for c in t))
    assert cjk_count >= 3  # 我 / 爱 / 咖 / 啡
    print("  CJK mixed: OK ->", toks)


def test_stopwords_basic():
    banner("stopwords: ZH particles + EN function words removed")
    tp = TextProcessor
    toks = tp.remove_stopwords(["我", "爱", "Americano", "咖啡", "的", "是", "the", "and"])
    for s in ("我", "的", "是", "the", "and"):
        assert s not in toks, s
    for k in ("爱", "Americano", "咖啡"):
        assert k in toks, k
    print("  basic stopwords: OK")


def test_negation_not_in_stopwords():
    banner("negation: not/no/nor kept as negations, not removed as stopwords")
    tp = TextProcessor
    toks = tp.remove_stopwords(["not", "love", "no", "good"])
    # "not" and "no" must NOT be removed (they are negations)
    assert "not" in toks, toks
    assert "no" in toks, toks
    print("  not/no preserved: OK")


def test_negation_window():
    banner("negation window: 3 tokens after negation are marked")
    tp = TextProcessor
    marked = tp.mark_negation(["I", "do", "not", "love", "Americano", "coffee", "today"])
    flagged = {t for t, neg in marked if neg}
    negated = {t for t, neg in marked if t == "not"}
    # not is a negation word, emitted as (not, False)
    assert ("not", False) in marked
    # 3 tokens after not: love, Americano, coffee
    assert "love" in flagged
    assert "Americano" in flagged
    assert "coffee" in flagged
    # "today" is the 4th token - should NOT be flagged
    assert "today" not in flagged
    print("  window=3: OK -> flagged=", sorted(flagged))


def test_zh_negation():
    banner("negation: Chinese 不 / 没有 / 别")
    tp = TextProcessor
    marked = tp.mark_negation(["我", "不", "喜欢", "Americano", "咖啡"])
    flagged = {t for t, neg in marked if neg}
    assert "喜欢" in flagged
    assert "Americano" in flagged
    assert "咖啡" in flagged
    print("  ZH negation: OK")


def test_fts_preprocess_compat():
    banner("fts_preprocess: shape equivalent to legacy cjk_split")
    tp = TextProcessor
    out = tp.fts_preprocess("我爱 Americano 咖啡!  ")
    # must be space-separated tokens, no punctuation, no double spaces
    assert "  " not in out
    assert "!" not in out
    assert "Americano" in out
    # each CJK char is its own token
    parts = out.split()
    assert "我" in parts and "爱" in parts and "Americano" in parts
    print("  fts_preprocess: OK ->", repr(out))


def test_embed_preprocess_negation_prefix():
    banner("embed_preprocess: NOT_ prefix on negated content tokens")
    tp = TextProcessor
    out = tp.embed_preprocess("I do not love Americano coffee today")
    # not -> NOT_love NOT_Americano NOT_coffee
    assert "NOT_love" in out
    assert "NOT_Americano" in out
    assert "NOT_coffee" in out
    # "today" is past the window
    assert "NOT_today" not in out
    assert "today" in out
    print("  embed_preprocess: OK ->", out)


def test_keyword_preprocess_strips_stopwords():
    banner("keyword_preprocess: stopwords gone, content words kept")
    tp = TextProcessor
    toks = tp.keyword_preprocess("I love Americano and dislike cilantro")
    for keep in ("love", "Americano", "dislike", "cilantro"):
        assert keep in toks
    for drop in ("I", "and"):
        assert drop not in toks
    print("  keyword_preprocess: OK")


def test_register_extra_stopwords():
    banner("register_stopwords + register_negations + reset")
    tp = TextProcessor
    n = tp.register_stopwords({"Americano", "latte"})
    assert n == 2
    toks = tp.remove_stopwords(["I", "love", "Americano", "latte", "coffee"])
    assert "Americano" not in toks
    assert "latte" not in toks
    assert "coffee" in toks
    # negation extras
    m = tp.register_negations({"definitely_not"})
    assert m == 1
    marked = tp.mark_negation(["definitely_not", "ready"])
    assert any(neg for _, neg in marked if _ == "ready")
    # reset
    tp.reset()
    toks = tp.remove_stopwords(["Americano", "latte"])
    assert "Americano" in toks
    print("  register/reset: OK")


def _new_service(enable_semantic=True):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    cfg = MemoryConfig(sqlite_path=tmp.name, embedding_dim=32,
                       enable_semantic=enable_semantic, enable_prospective=False)
    return MemoryService(cfg), tmp.name


def test_dual_route_graph_with_text_processor():
    banner("dual_route: graph route uses TextProcessor for multi-token queries")
    svc, db = _new_service()
    try:
        from hippocampus.types import Entity, Relation
        alice = Entity(id="ent_alice", name="Alice", type="person", mention_count=5)
        am = Entity(id="ent_am", name="Americano", type="drink", mention_count=3)
        for e in (alice, am):
            svc.semantic.upsert_entity(e)
        svc.semantic.add_relation(Relation(
            subject_id="ent_alice", predicate="likes", object_id="ent_am", confidence=0.9))
        e1 = svc.observe(session_id="s1", actor_id="u1", platform="mock",
                         channel_id="c1", content="Alice loves Americano coffee")
        e1.entity_refs = ["ent_alice", "ent_am"]
        svc.store.upsert(e1)
        # query "tell me about Alice" - multi-token with stopwords
        # TextProcessor.keyword_preprocess -> ["tell", "Alice"]  (removes "me", "about")
        res = svc.recall_dual_route(Cue(text="tell me about Alice", k=5))
        assert len(res.engrams) >= 1
        from hippocampus.retrieval import DualRouteRetriever, DualRouteConfig
        hits = DualRouteRetriever(svc, DualRouteConfig()).explain(Cue(text="tell me about Alice", k=5))
        assert any(h.route.value == "graph" for h in hits), \
            "graph route should match Alice entity from multi-token query"
        print("  graph multi-token: OK")
    finally:
        import gc; del svc; gc.collect()
        try: os.unlink(db)
        except Exception: pass


def test_stopword_lists_exposed():
    banner("stopwords module: built-in lists available")
    zh = stopwords.all_zh()
    en = stopwords.all_en()
    neg = stopwords.all_negations()
    assert len(zh) > 50
    assert len(en) > 30
    assert "不" in zh or "不" in stopwords.ZH_NEGATIONS
    assert "not" in neg
    print("  built-in lists: OK zh=%d en=%d neg=%d" % (len(zh), len(en), len(neg)))


if __name__ == "__main__":
    test_jieba_status()
    test_ascii_tokenize()
    test_cjk_tokenize()
    test_stopwords_basic()
    test_negation_not_in_stopwords()
    test_negation_window()
    test_zh_negation()
    test_fts_preprocess_compat()
    test_embed_preprocess_negation_prefix()
    test_keyword_preprocess_strips_stopwords()
    test_register_extra_stopwords()
    test_dual_route_graph_with_text_processor()
    test_stopword_lists_exposed()
    print("\nALL OK")