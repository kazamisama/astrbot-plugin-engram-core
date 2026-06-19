# v1.4 B4: keyword-based graph retriever.
# Single-file layout.
# Given a list of query tokens, return entity matches with engram candidates.
from __future__ import annotations

from dataclasses import dataclass, field

from ..graph_store import GraphStore
from ..types import Entity
from .rrf import RankedCandidate
from ._graph_types import EntityMatch


class GraphKeywordRetriever:
    """Lexical entity match: token LIKE entity.name / entity.aliases.

    The match score combines:
      - exact name containment of the query token (strongest)
      - name-prefix overlap
      - alias hit
      - entity.mention_count (popularity prior)
    Returns EntityMatch objects; callers do their own engram hydration.
    """

    def __init__(self, graph: GraphStore) -> None:
        self._graph = graph

    def search(self, query_tokens: list[str], k: int = 16) -> list[EntityMatch]:
        toks = [t for t in (query_tokens or []) if t]
        if not toks:
            return []
        scores: dict[str, float] = {}
        entities: dict[str, Entity] = {}

        for tok in toks:
            tok_l = tok.lower()
            for ent in self._entities_for(tok):
                entities[ent.id] = ent
                s = 0.0
                name_l = (ent.name or "").lower()
                # Strongest: token equals name
                if tok_l == name_l:
                    s += 3.0
                # Strong: token in name
                elif tok_l in name_l:
                    s += 2.0
                # Weak: name in token (entity is a substring of the query)
                elif name_l and name_l in tok_l:
                    s += 1.0
                # Aliases
                for alias in ent.aliases or []:
                    a_l = alias.lower()
                    if tok_l == a_l:
                        s += 2.5
                    elif tok_l in a_l or a_l in tok_l:
                        s += 1.5
                # Popularity prior (log-scaled, capped)
                import math
                s += 0.5 * math.log1p(max(0, int(ent.mention_count)))
                if s > 0:
                    prev = scores.get(ent.id, 0.0)
                    if s > prev:
                        scores[ent.id] = s

        # Hydrate engrams via the reverse index; do NOT scan every engram.
        out: list[EntityMatch] = []
        for eid, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]:
            ent = entities[eid]
            engrams = self._graph.engrams_for(eid, limit=k * 4)
            out.append(EntityMatch(
                entity=ent,
                score=float(score),
                source="keyword",
                engram_refs=[eid for eid, _ in engrams],
            ))
        return out

    # -- internals -----------------------------------------------------

    def _entities_for(self, token: str) -> list[Entity]:
        """Open the graph DB and do a LIKE over the entities table.

        We reach into the same SQLite file that SemanticStore writes to,
        so a fresh graph_adjacency is only useful after rebuild_from_semantic.
        For a brand-new DB the entities table is empty and search returns [].
        """
        import sqlite3
        # Late import to avoid a hard dependency cycle at module load.
        from ..semantic import SemanticStore  # noqa: F401
        # Use a short-lived connection so we don't tangle with SemanticStore's
        # own RLock. Same DB file, separate connection -> safe with WAL.
        path = self._graph._db_path  # type: ignore[attr-defined]
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            like = "%" + token.lower() + "%"
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM entities
                    WHERE LOWER(name) LIKE ? OR LOWER(aliases) LIKE ?
                    ORDER BY mention_count DESC
                    LIMIT 32
                    """,
                    (like, like),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            return [Entity.from_row(dict(r)) for r in rows]
        finally:
            conn.close()


__all__ = ["GraphKeywordRetriever"]
