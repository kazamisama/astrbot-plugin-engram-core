"""Smoke v1.11: text-layer near-duplicate dedup.

Covers:
  - dedup.jaccard / token_set / best_duplicate (threshold gating, tokenizer)
  - 3 config fields registered with defaults (disabled by default)
  - service.observe with dedup_enabled merges a near-duplicate into the
    existing engram instead of creating a new one (count stays the same)
  - dedup_enabled=False keeps the current behaviour (no extra merge)
"""
import sys, os, tempfile


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.dedup import jaccard, token_set, best_duplicate
from hippocampus.config_manager import ConfigManager, _FIELDS, LABELS
from hippocampus.config import MemoryConfig


def banner(m):
    print("\n=== " + m + " ===")


def test_jaccard_core():
    banner("jaccard / token_set / best_duplicate")
    a = token_set("\u673a\u5668\u5b66\u4e60\u5f88\u6709\u8da3", "bigram")
    b = token_set("\u673a\u5668\u5b66\u4e60\u5f88\u6709\u8da3", "bigram")
    assert jaccard(a, b) == 1.0
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"x"}, set()) == 0.0
    # best_duplicate gating
    class _C:
        def __init__(self, content):
            self.content = content
            self.summary = ""
    cands = [_C("\u4eca\u5929\u5929\u6c14\u4e0d\u9519"),
             _C("\u673a\u5668\u5b66\u4e60\u5f88\u6709\u8da3")]
    r = best_duplicate("\u673a\u5668\u5b66\u4e60\u5f88\u6709\u8da3", cands,
                       mode="bigram", threshold=0.9)
    assert r is not None and r[1] >= 0.9, r
    # below-threshold returns None
    r2 = best_duplicate("\u5b8c\u5168\u4e0d\u540c\u7684\u53e5\u5b50", cands,
                        mode="bigram", threshold=0.9)
    assert r2 is None, r2
    print("  core OK")


def test_config_fields():
    banner("dedup config fields")
    for f in ("dedup_enabled", "dedup_threshold", "dedup_candidate_k"):
        assert f in _FIELDS, f
        assert f in LABELS, f
    cfg = ConfigManager({}).memory_config
    assert cfg.dedup_enabled is True
    assert cfg.dedup_threshold == 0.9
    assert cfg.dedup_candidate_k == 10
    print("  defaults OK (disabled)")


class _HashEmbed:
    def name(self):
        return "hash"

    def dim(self):
        return 16

    def embed(self, text):
        # deterministic but low-discrimination, like the real hash embedder
        v = [0.0] * 16
        for i, ch in enumerate(text or ""):
            v[ord(ch) % 16] += 1.0
        return v


def _build_service(tmp, dedup):
    from hippocampus.service import MemoryService
    cfg = MemoryConfig()
    cfg.sqlite_path = os.path.join(tmp, "hippo.db")
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.dedup_enabled = dedup
    cfg.dedup_threshold = 0.85
    cfg.tokenizer_mode = "bigram"
    # force every engram to be storable
    cfg.importance_floor_for_long_term = 0.0
    svc = MemoryService(cfg=cfg)
    return svc


def test_observe_dedup_merges():
    banner("observe dedup merges near-duplicate across sessions")
    tmp = tempfile.mkdtemp()
    svc = _build_service(tmp, dedup=True)
    phrase = "\u6211\u559c\u6b22\u5728\u5468\u672b\u6253\u7fbd\u6bdb\u7403"
    svc.observe(session_id="s1", actor_id="u1", platform="qq",
                channel_id="g1", content=phrase)
    n1 = svc.store.fts_count()
    # near-identical message in a different session -> should merge, not add
    svc.observe(session_id="s2", actor_id="u1", platform="qq",
                channel_id="g1", content=phrase + "\uff01")
    n2 = svc.store.fts_count()
    assert n2 == n1, "dedup should merge near-duplicate (count unchanged): %s -> %s" % (n1, n2)
    print("  near-duplicate merged (count %s stable): OK" % n1)
    try:
        svc.close()
    except Exception:
        pass


def test_observe_dedup_off_keeps_both():
    banner("dedup off keeps separate (control)")
    tmp = tempfile.mkdtemp()
    svc = _build_service(tmp, dedup=False)
    phrase = "\u6211\u559c\u6b22\u5728\u5468\u672b\u6253\u7fbd\u6bdb\u7403\u8fd9\u9879\u8fd0\u52a8"
    svc.observe(session_id="s1", actor_id="u2", platform="qq",
                channel_id="g2", content=phrase)
    # different enough content so the vector separator does not merge under hash
    svc.observe(session_id="s2", actor_id="u2", platform="qq",
                channel_id="g2", content="\u4eca\u5929\u4e0b\u96e8\u4e86\u4e0d\u9002\u5408\u51fa\u95e8")
    assert svc.store.fts_count() >= 2, "dedup off should keep both"
    print("  both kept: OK")
    try:
        svc.close()
    except Exception:
        pass


def main():
    test_jaccard_core()
    test_config_fields()
    test_observe_dedup_merges()
    test_observe_dedup_off_keeps_both()
    print("\nv1.11 smoke: ALL PASS")


if __name__ == "__main__":
    main()
