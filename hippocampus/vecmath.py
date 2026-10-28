"""Numpy-optional vector math + an in-memory metric index (v1.77.1).

Cosine similarity is the only metric this plugin needs, so vectors are
normalised once on the way into an index and query-time scoring becomes a
plain dot product. ``numpy`` is used when the runtime ships it (AstrBot does)
and a pure-Python path keeps the plugin importable without it.

Dimension mismatches score 0.0 instead of silently truncating the longer
vector: after an embedding-provider switch the old rows are still in the
store, and truncating them produced plausible-looking but meaningless
similarities (see also ``storage._cos``).
"""
from __future__ import annotations

import math
from operator import mul

try:  # numpy rides along with the AstrBot runtime but is never required
    import numpy as _np
except Exception:  # pragma: no cover - exercised only without numpy
    _np = None

HAVE_NUMPY = _np is not None


def normalize(vec):
    """Return a unit-length copy of ``vec`` (zero vectors stay zero)."""
    if _np is not None:
        arr = _np.asarray(vec, dtype=_np.float32)
        if arr.ndim != 1:
            arr = arr.reshape(-1)
        n = float(_np.linalg.norm(arr))
        if n > 0.0:
            arr = arr / n
        return arr
    out = [float(x) for x in vec]
    n = math.sqrt(sum(x * x for x in out))
    if n > 0.0:
        out = [x / n for x in out]
    return out


def dot(query, vec) -> float:
    """Dot product of two *normalised* vectors; 0.0 on a dimension mismatch."""
    if len(query) != len(vec):
        return 0.0
    if _np is not None:
        return float(_np.dot(query, vec))
    return float(sum(map(mul, query, vec)))


def pairwise_scores(query, vectors) -> list:
    """Scores for one normalised query against a list of normalised vectors."""
    if not vectors:
        return []
    if _np is not None:
        mat = _np.stack(vectors)
        return [float(x) for x in (mat @ query)]
    return [float(sum(map(mul, query, v))) for v in vectors]


class VectorIndex:
    """In-memory ``key -> (meta, normalised vector)`` index.

    The owning store is responsible for threading: every mutator takes the
    store lock. ``meta`` is an opaque tuple; ``predicate(meta)`` decides which
    rows a query may see (filters that used to live in SQL now run here so the
    expensive ``SELECT ... embedding_json`` never repeats).
    """

    def __init__(self) -> None:
        self._meta: dict = {}
        self._vecs: dict = {}
        self._warned_dim = False

    def __len__(self) -> int:
        return len(self._vecs)

    def clear(self) -> None:
        self._meta.clear()
        self._vecs.clear()

    def put(self, key: str, meta: tuple, vec) -> None:
        """Insert/replace one row; an empty vector removes the entry."""
        if not vec:
            self.drop(key)
            return
        self._meta[key] = meta
        self._vecs[key] = normalize(vec)

    def drop(self, key: str) -> None:
        self._meta.pop(key, None)
        self._vecs.pop(key, None)

    def drop_where(self, predicate) -> int:
        """Drop every key whose meta matches; returns how many were removed."""
        keys = [k for k, m in self._meta.items() if predicate(m)]
        for k in keys:
            self.drop(k)
        return len(keys)

    def meta(self, key: str):
        return self._meta.get(key)

    def vector(self, key: str):
        return self._vecs.get(key)

    def search(self, query, k: int, predicate=None, min_score=None,
               with_meta: bool = False) -> list:
        """Top-k ``(key, score)`` -- or ``(key, meta, score)`` -- best first."""
        if k <= 0 or not self._vecs:
            return []
        q = normalize(query)
        qdim = len(q)
        keys: list = []
        vecs: list = []
        mismatched = 0
        for key, meta in self._meta.items():
            if predicate is not None and not predicate(meta):
                continue
            vec = self._vecs[key]
            if len(vec) != qdim:
                mismatched += 1
                continue
            keys.append(key)
            vecs.append(vec)
        if mismatched and not self._warned_dim:
            self._warned_dim = True
            print("[hippocampus] vector dim mismatch: query=%d, skipped=%d rows"
                  " (embedding provider changed; run a vector rebuild)"
                  % (qdim, mismatched))
        if not keys:
            return []
        scores = pairwise_scores(q, vecs)
        if _np is not None:
            arr = _np.asarray(scores, dtype=_np.float32)
            order = _np.argsort(-arr, kind="stable")[:k]
            idxs = [int(i) for i in order]
        else:
            idxs = sorted(range(len(scores)), key=lambda i: scores[i],
                          reverse=True)[:k]
        out = []
        for i in idxs:
            score = float(scores[i])
            if min_score is not None and score < min_score:
                continue
            key = keys[i]
            if with_meta:
                out.append((key, self._meta[key], score))
            else:
                out.append((key, score))
        return out
