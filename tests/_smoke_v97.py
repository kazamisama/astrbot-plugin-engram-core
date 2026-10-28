"""Smoke v1.77.1: cached diary-chunk vectors.

Why this test exists
--------------------
The injection path spent 3.4-3.9s per request in recall_diary_chunks:
DiaryStore.all_chunks() re-parsed ~1100 chunks / 97MB of embedding JSON
and the cosine loop ran in pure Python. v1.77.1 caches the normalised
vectors in DiaryStore.rank_chunks() and keeps the cache in sync on
add_chunks / delete_chunks_for_diary.

Tests:
- rank_chunks order, persona/scope filters and min_score
- cache stays correct when chunks are added after warm-up, and when a
  diary's chunks are deleted
- chunk_texts only returns the requested ids
- MemoryService.recall_diary_chunks uses the cache end to end, keeps
  min_score, and still falls back to slicing a diary engram that has no
  chunks
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hippocampus import MemoryConfig, MemoryService
from hippocampus.diary_store import DiaryChunk, DiaryStore
from hippocampus.embeddings import EmbeddingProvider
from hippocampus.types import Engram


class _Embedder(EmbeddingProvider):
    name = "stub"

    def __init__(self, dim=4):
        self._dim = dim

    @property
    def dim(self):
        return self._dim

    def embed(self, text):
        v = [0.0] * self._dim
        v[0] = 1.0
        return v


def banner(t):
    print(chr(10) + "=== " + t + " ===")


def _chunk(i, vec, diary_id="d1", persona="", scope="", text=None):
    return DiaryChunk(id="c%d" % i, diary_id=diary_id, channel_id="ch",
                      seq=i, text=text or ("text %d" % i), embedding=vec,
                      embedding_model="m", persona_id=persona, scope_id=scope)


def test_rank_and_filters():
    banner("rank_chunks: order, filters, min_score, texts")
    ds = DiaryStore(os.path.join(tempfile.mkdtemp(), "d.db"))
    ds.add_chunks([
        _chunk(1, [1.0, 0.0, 0.0, 0.0], text="alpha"),
        _chunk(2, [0.0, 1.0, 0.0, 0.0], text="beta"),
        _chunk(3, [0.9, 0.1, 0.0, 0.0], persona="p1", text="gamma"),
        _chunk(4, [1.0, 0.0, 0.0, 0.0], diary_id="d2", scope="s1", text="delta"),
    ])
    q = [1.0, 0.0, 0.0, 0.0]
    ranked = ds.rank_chunks(q, limit=50)
    assert [r[0] for r in ranked] == ["c1", "c4", "c3", "c2"], ranked
    assert all(r[1] in ("d1", "d2") for r in ranked)
    only_p1 = ds.rank_chunks(q, limit=50, persona_id="p1")
    assert [r[0] for r in only_p1] == ["c3"], only_p1
    only_s1 = ds.rank_chunks(q, limit=50, scope_id="s1")
    assert [r[0] for r in only_s1] == ["c4"], only_s1
    assert ds.rank_chunks(q, limit=50, min_score=0.99)[0][0] == "c1"
    assert [r[0] for r in ds.rank_chunks(q, limit=50, min_score=0.995)] == ["c1", "c4"]
    texts = ds.chunk_texts(["c1", "c4"])
    assert texts == {"c1": "alpha", "c4": "delta"}, texts
    assert ds.chunk_diary_ids() == {"d1", "d2"}
    assert ds.chunk_diary_ids(persona_id="p1") == {"d1"}


def test_cache_sync_on_add_and_delete():
    banner("cache follows add_chunks / delete_chunks_for_diary")
    ds = DiaryStore(os.path.join(tempfile.mkdtemp(), "d.db"))
    ds.add_chunks([_chunk(1, [1.0, 0.0, 0.0, 0.0])])
    assert ds.warm_chunk_index() == 1
    ds.add_chunks([_chunk(2, [0.0, 1.0, 0.0, 0.0], text="new")])
    ranked = ds.rank_chunks([0.0, 1.0, 0.0, 0.0], limit=5)
    assert ranked and ranked[0][0] == "c2", ranked
    ds.delete_chunks_for_diary("d1")
    assert ds.rank_chunks([1.0, 0.0, 0.0, 0.0], limit=5) == []
    assert ds.chunk_diary_ids() == set()


def _svc(tmp):
    cfg = MemoryConfig(sqlite_path=os.path.join(tmp, "m.db"))
    cfg.enable_semantic = False
    cfg.enable_prospective = False
    cfg.enable_profile = False
    cfg.enable_persona = False
    cfg.dedup_enabled = False
    return MemoryService(cfg, embedder=_Embedder())


def test_service_integration():
    banner("MemoryService.recall_diary_chunks uses the cached ranking")
    svc = _svc(tempfile.mkdtemp())
    svc.store.upsert(Engram(id="d_other", summary="other diary",
                            memory_type="diary", scope_id="other",
                            created_at=time.time()))
    svc.diary_store.add_chunks([
        _chunk(1, [1.0, 0.0, 0.0, 0.0], text="the picnic was sunny"),
        _chunk(2, [0.0, 1.0, 0.0, 0.0], text="the meeting ran late"),
        _chunk(3, [0.9, 0.1, 0.0, 0.0], diary_id="d_other",
               scope="other", text="someone else"),
    ])
    hits = svc.recall_diary_chunks("picnic", top_n=1)
    assert hits and hits[0][0] == "the picnic was sunny", hits
    assert hits[0][1] > 0.9, hits
    scoped = svc.recall_diary_chunks("picnic", top_n=5, scope_id="other")
    assert [t for t, _s in scoped] == ["someone else"], scoped
    none = svc.recall_diary_chunks("picnic", top_n=1, min_score=1.001)
    assert none == [], none
    svc.close()


def test_service_fallback_without_chunks():
    banner("fallback: diary engram without chunks is sliced on the fly")
    svc = _svc(tempfile.mkdtemp())
    text = "今天下午我们一起去了公园，天气很好。" * 8
    e = Engram(id="diary1", content=text, summary=text,
               memory_type="diary", persona_id="", scope_id="",
               created_at=time.time(), importance=0.7)
    svc.store.upsert(e)
    hits = svc.recall_diary_chunks("公园", top_n=3)
    assert hits, hits
    assert all(s > 0.0 for _t, s in hits), hits
    svc.close()


if __name__ == "__main__":
    test_rank_and_filters()
    test_cache_sync_on_add_and_delete()
    test_service_integration()
    test_service_fallback_without_chunks()
    print(chr(10) + "ALL OK")
