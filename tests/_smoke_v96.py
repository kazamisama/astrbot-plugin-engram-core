"""Smoke v1.77.1: vector index + cosine dimension guard.

Why this test exists
--------------------
Recall used to read and json.loads() every embedding on every query
(~1.5s on the live 550-row store, 42MB of JSON), and _cos silently
truncated a longer vector to a shorter one -- so after an embedding
provider switch old rows produced plausible but meaningless scores.
v1.77.1 adds an in-memory index (hippocampus/vecmath.py) that the store
keeps in sync, and makes dimension mismatches score 0.0.

Tests:
- _cos returns 0.0 for a dimension mismatch instead of a truncated score
- VectorIndex: normalisation, predicate filtering, min_score, top-k order,
  dimension-mismatch rows are skipped instead of scored
- HippocampalStore.vector_search matches a brute-force reference for every
  filter; upsert / update_embedding / soft_forget / restore / delete are
  visible immediately without a rebuild
- warm_vector_index() count, count_missing_vectors()
- cached queries do not re-read embedding_json (the store connection sees
  no new SELECT of that column)
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hippocampus.storage import HippocampalStore, _cos
from hippocampus.types import Engram
from hippocampus.vecmath import VectorIndex, normalize


class _Embedder:
    name = "stub"

    def embed(self, text):
        return [1.0, 0.0, 0.0]

    def embed_batch(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


def banner(t):
    print(chr(10) + "=== " + t + " ===")


def _store(tmp):
    return HippocampalStore(os.path.join(tmp, "t.db"), _Embedder())


def _put(st, i, vec, **kw):
    e = Engram(id="e%d" % i, content="c%d" % i, embedding=vec,
               embedding_model=kw.pop("embedding_model", "m"), **kw)
    st.upsert(e)
    return e


def test_cos_dim_guard():
    banner("_cos: dimension mismatch scores 0.0")
    assert abs(_cos([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9
    assert abs(_cos([1.0, 0.0], [0.0, 1.0])) < 1e-9
    assert _cos([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0
    assert _cos([1.0], [1.0, 2.0, 3.0]) == 0.0
    assert _cos([], [1.0]) == 0.0


def test_index_basics():
    banner("VectorIndex: predicate, min_score, order, dim mismatch")
    idx = VectorIndex()
    idx.put("a", ("p1",), [1.0, 0.0])
    idx.put("b", ("p2",), [0.0, 1.0])
    idx.put("c", ("p1",), [0.9, 0.1])
    idx.put("empty", ("p1",), [])
    assert len(idx) == 3, len(idx)
    hits = idx.search([1.0, 0.0], 5)
    assert [k for k, _s in hits] == ["a", "c", "b"], hits
    only_p1 = idx.search([1.0, 0.0], 5, lambda m: m[0] == "p1")
    assert [k for k, _s in only_p1] == ["a", "c"], only_p1
    top1 = idx.search([1.0, 0.0], 1, min_score=0.5)
    assert [k for k, _s in top1] == ["a"], top1
    # 3-dim query skips the 2-dim rows rather than scoring a truncated dot.
    assert idx.search([1.0, 0.0, 0.0], 5) == []
    idx.drop_where(lambda m: m[0] == "p1")
    assert len(idx) == 1, len(idx)
    v = normalize([3.0, 4.0])
    assert abs(float(v[0]) - 0.6) < 1e-6


def test_store_filters_match_reference():
    banner("HippocampalStore.vector_search matches brute force")
    tmp = tempfile.mkdtemp()
    st = _store(tmp)
    rows = [
        (1, [1.0, 0.0, 0.0], {}),
        (2, [0.0, 1.0, 0.0], {}),
        (3, [0.8, 0.6, 0.0], {"persona_id": "p1"}),
        (4, [1.0, 0.0, 0.0], {"scope_id": "s1"}),
        (5, [1.0, 0.0, 0.0], {"memory_type": "diary"}),
        (6, [1.0, 0.0, 0.0], {"embedding_model": "other"}),
        (7, [], {}),
        (8, [1.0, 0.0, 0.0], {"actor_id": "alice"}),
        (9, [1.0, 0.0, 0.0], {"channel_id": "ch1"}),
    ]
    for i, vec, kw in rows:
        _put(st, i, vec, **kw)
    assert st.warm_vector_index() == 8, st.warm_vector_index()
    assert st.count_missing_vectors() == 1

    q = [1.0, 0.0, 0.0]
    cases = [
        ({}, {"e1", "e2", "e3", "e4", "e5", "e6", "e8", "e9"}),
        ({"persona_id": "p1"}, {"e3"}),
        ({"scope_id": "s1"}, {"e4"}),
        ({"memory_types": ["diary"]}, {"e5"}),
        ({"embedding_model": "other"}, {"e6"}),
        ({"actor_id": "alice"}, {"e8"}),
        ({"channel_id": "ch1"}, {"e9"}),
    ]
    for kw, expected in cases:
        got = {e.id for e, _s in st.vector_search(q, 50, **kw)}
        assert got == expected, (kw, got, expected)
    top = st.vector_search(q, 2)
    assert len(top) == 2 and top[0][1] >= top[1][1], top


def test_mutations_visible_without_rebuild():
    banner("upsert/update/forget/restore/delete keep the index in sync")
    tmp = tempfile.mkdtemp()
    st = _store(tmp)
    _put(st, 1, [1.0, 0.0])
    _put(st, 2, [0.0, 1.0])
    st.warm_vector_index()
    q = [1.0, 0.0]

    def hits():
        return [e.id for e, s in st.vector_search(q, 5) if s > 0.5]

    assert hits() == ["e1"], hits()

    st.update_embedding("e2", [1.0, 0.0], "m")
    assert sorted(hits()) == ["e1", "e2"], hits()

    _put(st, 3, [1.0, 0.0])
    assert "e3" in hits(), hits()

    st.soft_forget("e1")
    assert sorted(hits()) == ["e2", "e3"], hits()
    st.restore("e1")
    assert "e1" in hits(), hits()

    st.delete("e2")
    assert "e2" not in hits(), hits()


def test_cached_query_does_not_reread_embeddings():
    banner("second query uses the cache (no embedding_json SELECT)")
    tmp = tempfile.mkdtemp()
    st = _store(tmp)
    for i in range(20):
        _put(st, i, [float(i + 1), 1.0])
    st.warm_vector_index()
    seen = []
    st._conn.set_trace_callback(seen.append)
    try:
        st.vector_search([1.0, 0.0], 5)
        st.vector_search([1.0, 0.0], 5)
    finally:
        st._conn.set_trace_callback(None)
    embedding_reads = [s for s in seen if "embedding_json" in s]
    assert embedding_reads == [], embedding_reads


if __name__ == "__main__":
    test_cos_dim_guard()
    test_index_basics()
    test_store_filters_match_reference()
    test_mutations_visible_without_rebuild()
    test_cached_query_does_not_reread_embeddings()
    print(chr(10) + "ALL OK")
