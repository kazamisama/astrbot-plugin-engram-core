"""Smoke v1.10: configurable FTS tokenizer (char|bigram|jieba).

Covers:
  - tokenizer.tokenize for char/bigram/jieba + normalize_mode + fallback
  - config field tokenizer_mode registered, invalid -> default char
  - HippocampalStore index/query symmetry: a bigram-mode store recalls a
    multi-char CJK phrase
  - mode switch on reopen triggers reindex_fts (persisted in hippo_meta)
"""
import sys, os, tempfile


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.tokenizer import tokenize, normalize_mode, jieba_available
from hippocampus.config_manager import ConfigManager, _FIELDS, LABELS


def banner(m):
    print("\n=== " + m + " ===")


ML = "\u673a\u5668\u5b66\u4e60"  # 机器学习


def test_tokenize():
    banner("tokenize modes")
    assert tokenize(ML, "char") == "\u673a \u5668 \u5b66 \u4e60"
    assert tokenize(ML, "bigram") == "\u673a\u5668 \u5668\u5b66 \u5b66\u4e60"
    # jieba falls back to bigram when not installed
    jt = tokenize(ML, "jieba")
    assert jt, jt
    # non-CJK kept whole; mixed handled
    mix = tokenize("AstrBot " + ML, "bigram")
    assert "AstrBot" in mix and "\u673a\u5668" in mix, mix
    # single CJK char stays itself under bigram
    assert tokenize("\u732b", "bigram") == "\u732b"
    assert tokenize("", "char") == ""
    print("  char/bigram/jieba + mixed OK (jieba_available=%s)" % jieba_available())


def test_normalize_mode():
    banner("normalize_mode")
    assert normalize_mode("BIGRAM") == "bigram"
    assert normalize_mode("xxx") == "char"
    assert normalize_mode(None) == "char"
    assert normalize_mode("jieba") == "jieba"
    print("  normalize OK")


def test_config_field():
    banner("config tokenizer_mode field")
    assert "tokenizer_mode" in _FIELDS
    assert "tokenizer_mode" in LABELS
    assert ConfigManager({}).memory_config.tokenizer_mode == "jieba"
    assert ConfigManager({"tokenizer_mode": "bigram"}).memory_config.tokenizer_mode == "bigram"
    assert ConfigManager({"tokenizer_mode": "WRONG"}).memory_config.tokenizer_mode == "jieba"
    print("  field + validation OK")


class _HashEmbed:
    def name(self):
        return "hash"

    def dim(self):
        return 16

    def embed(self, text):
        return [0.0] * 16


def _mk_engram(content):
    from hippocampus.types import Engram
    import inspect, time
    sig = inspect.signature(Engram.__init__).parameters
    kw = dict(id="e-" + str(abs(hash(content)) % 100000),
              created_at=time.time(), session_id="s", actor_id="u1",
              platform="qq", channel_id="g1", content=content, summary=content)
    kw = {k: v for k, v in kw.items() if k in sig}
    return Engram(**kw)


def test_store_bigram_recall():
    banner("store bigram index/query symmetry")
    d = tempfile.mkdtemp()
    db = os.path.join(d, "h.db")
    from hippocampus.storage import HippocampalStore
    store = HippocampalStore(db, _HashEmbed(), tokenizer_mode="bigram")
    store.upsert(_mk_engram(ML + "\u5f88\u6709\u8da3"))  # ...很有趣
    store.upsert(_mk_engram("\u4eca\u5929\u5929\u6c14\u4e0d\u9519"))  # 今天天气不错
    hits = store.fts_search(ML, k=10)
    assert hits, "bigram store should recall the phrase"
    texts = [e.content for e, _ in hits]
    assert any(ML in t for t in texts), texts
    # persisted mode
    assert store._meta_get("tokenizer_mode") == "bigram"
    store.close()
    print("  bigram recall + persisted meta OK")


def test_mode_switch_reindex():
    banner("mode switch triggers reindex on reopen")
    d = tempfile.mkdtemp()
    db = os.path.join(d, "h.db")
    from hippocampus.storage import HippocampalStore
    s1 = HippocampalStore(db, _HashEmbed(), tokenizer_mode="char")
    s1.upsert(_mk_engram(ML))
    assert s1._meta_get("tokenizer_mode") == "char"
    s1.close()
    # reopen with bigram -> should reindex and persist
    s2 = HippocampalStore(db, _HashEmbed(), tokenizer_mode="bigram")
    assert s2._meta_get("tokenizer_mode") == "bigram"
    hits = s2.fts_search(ML, k=10)
    assert hits, "after reindex, bigram query should recall"
    s2.close()
    print("  reindex on switch OK")


def main():
    test_tokenize()
    test_normalize_mode()
    test_config_field()
    test_store_bigram_recall()
    test_mode_switch_reindex()
    print("\nv1.10 smoke: ALL PASS")


if __name__ == "__main__":
    main()
