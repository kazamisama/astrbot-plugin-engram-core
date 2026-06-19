# v1.4 B4: GraphStore -- adjacency + entity->engram index for the graph route.
# Single-file layout (NOT hippocampus/storage/) to avoid colliding with the
# legacy storage.py module.
#
# Reuses the `entities` and `relations` tables that SemanticStore already
# maintains. Adds two new tables:
#   - graph_adjacency(entity_id, neighbor_id, predicate, weight)
#       Undirected adjacency view of the relations table, kept in sync via
#       add_relation(). Used by GraphRetriever for 1..N hop walks.
#   - graph_engram_refs(entity_id, engram_id)
#       Reverse index from entity -> engram. Used by graph_keyword_retriever
#       and graph_vector_retriever to answer "which engrams mention X?"
#       in O(matches) instead of scanning every engram.
from __future__ import annotations

import json
import sqlite3
import threading
import time

from .types import Entity, Relation


class GraphStore:
    """Adjacency + reverse-index layer over SemanticStore's entities+relations."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        from .sqlite_util import apply_pragmas
        apply_pragmas(self._conn)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS graph_adjacency (
                    entity_id    TEXT NOT NULL,
                    neighbor_id  TEXT NOT NULL,
                    predicate    TEXT NOT NULL DEFAULT '',
                    weight       REAL NOT NULL DEFAULT 1.0,
                    PRIMARY KEY (entity_id, neighbor_id, predicate)
                );
                CREATE INDEX IF NOT EXISTS idx_graph_adj_entity
                    ON graph_adjacency(entity_id);

                CREATE TABLE IF NOT EXISTS graph_engram_refs (
                    entity_id  TEXT NOT NULL,
                    engram_id  TEXT NOT NULL,
                    weight     REAL NOT NULL DEFAULT 1.0,
                    PRIMARY KEY (entity_id, engram_id)
                );
                CREATE INDEX IF NOT EXISTS idx_graph_refs_entity
                    ON graph_engram_refs(entity_id);
                CREATE INDEX IF NOT EXISTS idx_graph_refs_engram
                    ON graph_engram_refs(engram_id);
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- writes --------------------------------------------------------

    def add_relation(self, rel: Relation) -> None:
        """Mirror a relation into the adjacency table (undirected). Also
        bumps the (entity -> engram) reverse index for both endpoints when
        the source engram is known."""
        if not (rel.subject_id and rel.object_id):
            return
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO graph_adjacency
                    (entity_id, neighbor_id, predicate, weight)
                VALUES (?, ?, ?, 1.0), (?, ?, ?, 1.0)
                """,
                (rel.subject_id, rel.object_id, rel.predicate,
                 rel.object_id, rel.subject_id, rel.predicate),
            )
            if rel.source_engram_id:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO graph_engram_refs
                        (entity_id, engram_id, weight)
                    VALUES (?, ?, 1.0), (?, ?, 1.0)
                    """,
                    (rel.subject_id, rel.source_engram_id,
                     rel.object_id, rel.source_engram_id),
                )

    def add_entity_engram_ref(self, entity_id: str, engram_id: str, weight: float = 1.0) -> None:
        """Index a single entity -> engram association. Used when an entity
        is mentioned but no relation is extracted (e.g. "Shanghai is a city"
        -- the entity is anchored to the engram, no relation)."""
        if not (entity_id and engram_id):
            return
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO graph_engram_refs
                    (entity_id, engram_id, weight)
                VALUES (?, ?, ?)
                """,
                (entity_id, engram_id, float(weight)),
            )

    # -- reads ---------------------------------------------------------

    def neighbors(self, entity_id: str, max_hops: int = 1) -> list[tuple[str, int, str]]:
        """BFS from `entity_id` up to `max_hops`. Returns a list of
        (neighbor_entity_id, depth, predicate) tuples. The starting entity
        is NOT included in the result.
        """
        if max_hops < 1 or not entity_id:
            return []
        out: list[tuple[str, int, str]] = []
        visited: set[str] = {entity_id}
        layer: list[tuple[str, int]] = [(entity_id, 0)]
        for _ in range(max_hops):
            nxt: list[tuple[str, int]] = []
            ids = [nid for nid, _ in layer]
            if not ids:
                break
            placeholders = ",".join("?" * len(ids))
            with self._lock:
                rows = self._conn.execute(
                    f"""
                    SELECT entity_id, neighbor_id, predicate
                    FROM graph_adjacency
                    WHERE entity_id IN ({placeholders})
                    """,
                    ids,
                ).fetchall()
            for r in rows:
                nb = r["neighbor_id"]
                if nb in visited:
                    continue
                visited.add(nb)
                out.append((nb, layer[0][1] + 1, r["predicate"]))
                nxt.append((nb, layer[0][1] + 1))
            layer = nxt
            if not layer:
                break
        return out

    def engrams_for(self, entity_id: str, limit: int = 100) -> list[tuple[str, float]]:
        """Return [(engram_id, weight)] for engrams that mention `entity_id`."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT engram_id, weight
                FROM graph_engram_refs
                WHERE entity_id = ?
                ORDER BY weight DESC
                LIMIT ?
                """,
                (entity_id, int(limit)),
            ).fetchall()
        return [(r["engram_id"], float(r["weight"])) for r in rows]

    def all_relations(self) -> list[Relation]:
        """Read every relation from the legacy relations table. Used by
        rebuild_from_semantic() and by retriever explain() paths."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT * FROM relations"
                ).fetchall()
            except sqlite3.OperationalError:
                # The relations table is owned by SemanticStore. If it does
                # not exist yet, there are simply no relations.
                return []
        return [Relation(**dict(r)) for r in rows]

    def rebuild_from_semantic(self, semantic) -> int:
        """One-shot: clear graph tables and rebuild from an existing
        SemanticStore's relations. Returns the number of relations mirrored.
        Intended for upgrades / migrations, not hot paths.
        """
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM graph_adjacency")
            self._conn.execute("DELETE FROM graph_engram_refs")
        rels = self.all_relations()
        for r in rels:
            self.add_relation(r)
        return len(rels)

    # -- diagnostics ---------------------------------------------------

    def stats(self) -> dict[str, int]:
        with self._lock:
            adj = self._conn.execute("SELECT COUNT(*) AS c FROM graph_adjacency").fetchone()["c"]
            refs = self._conn.execute("SELECT COUNT(*) AS c FROM graph_engram_refs").fetchone()["c"]
        return {"adjacency": int(adj), "engram_refs": int(refs)}


__all__ = ["GraphStore"]
