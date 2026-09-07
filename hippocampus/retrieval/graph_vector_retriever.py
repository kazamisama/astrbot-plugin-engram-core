# v1.4 B4: vector-based graph retriever.
# Single-file layout.
# Embed the entity name (and aliases) on the fly, rank by cosine.
from __future__ import annotations

import math

from ..embeddings import EmbeddingProvider
from ..graph_store import GraphStore
from ..types import Entity
from ._graph_types import EntityMatch


class GraphVectorRetriever:
    """Embed entity names and rank by cosine against the query vector.

    The query is supplied as a pre-computed embedding (the cue vec, not a
    re-embed of tokens). All entity names are embedded with the same
    EmbeddingProvider so the space is consistent.

    v1.76.15: the scan is capped to the most-mentioned `max_entities`
    entities (default 64) instead of 1024, so one dual-route query can no
    longer issue a huge batch of provider embedding calls.
    """

    DEFAULT_MAX_ENTITIES = 64

    def __init__(self, graph: GraphStore, embedder: EmbeddingProvider,
                 max_entities: int = DEFAULT_MAX_ENTITIES) -> None:
        self._graph = graph
        self._embedder = embedder
        self._max_entities = max(1, int(max_entities))

    def search(self, query_vec: list[float], k: int = 16) -> list[EntityMatch]:
        if not query_vec:
            return []
        entities = self._all_entities(limit=self._max_entities)
        if not entities:
            return []
        scored: list[tuple[Entity, float]] = []
        for ent in entities:
            text = ent.name or ""
            if not text:
                continue
            vec = self._embedder.embed(text)
            score = _cos(query_vec, vec)
            if score > 0:
                scored.append((ent, score))
        scored.sort(key=lambda kv: kv[1], reverse=True)
        out: list[EntityMatch] = []
        for ent, score in scored[:k]:
            engrams = self._graph.engrams_for(ent.id, limit=k * 4)
            out.append(EntityMatch(
                entity=ent,
                score=float(score),
                source="vector",
                engram_refs=[eid for eid, _ in engrams],
            ))
        return out

    # -- internals -----------------------------------------------------

    def _all_entities(self, limit: int = 64) -> list[Entity]:
        import sqlite3
        from ..semantic import SemanticStore  # noqa: F401
        path = self._graph._db_path  # type: ignore[attr-defined]
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            try:
                rows = conn.execute(
                    "SELECT * FROM entities ORDER BY mention_count DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            return [Entity.from_row(dict(r)) for r in rows]
        finally:
            conn.close()


def _cos(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    da = math.sqrt(sum(x * x for x in a[:n])) or 1.0
    db = math.sqrt(sum(x * x for x in b[:n])) or 1.0
    return sum(a[i] * b[i] for i in range(n)) / (da * db)


__all__ = ["GraphVectorRetriever"]
