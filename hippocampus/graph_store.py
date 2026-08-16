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
from .graph_models import GraphNodeV2, GraphEdgeV2, GraphEntryV2


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

                -- v1.76.10: persistent full graph (nodes/edges/entries)
                CREATE TABLE IF NOT EXISTS graph_nodes_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_key TEXT NOT NULL UNIQUE,
                    node_type TEXT NOT NULL,
                    node_value TEXT NOT NULL,
                    canonical_value TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS graph_edges_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    edge_key TEXT NOT NULL UNIQUE,
                    source_node_id INTEGER NOT NULL,
                    target_node_id INTEGER NOT NULL,
                    relation_type TEXT NOT NULL,
                    source_memory_id TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    confidence REAL NOT NULL DEFAULT 0.8,
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_graph_edges_v2_sem
                    ON graph_edges_v2(source_node_id, target_node_id, relation_type);
                CREATE INDEX IF NOT EXISTS idx_graph_edges_v2_mem
                    ON graph_edges_v2(source_memory_id);
                CREATE TABLE IF NOT EXISTS graph_entries_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_key TEXT NOT NULL UNIQUE,
                    source_memory_id TEXT NOT NULL,
                    session_id TEXT,
                    persona_id TEXT,
                    scope_id TEXT,
                    entry_type TEXT NOT NULL,
                    relation_type TEXT,
                    content TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    edge_id INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_graph_entries_v2_mem
                    ON graph_entries_v2(source_memory_id);
                CREATE INDEX IF NOT EXISTS idx_graph_entries_v2_scope
                    ON graph_entries_v2(scope_id, persona_id, source_memory_id);
                CREATE TABLE IF NOT EXISTS graph_entry_nodes_v2 (
                    entry_id INTEGER NOT NULL,
                    node_id INTEGER NOT NULL,
                    PRIMARY KEY (entry_id, node_id)
                );
                CREATE INDEX IF NOT EXISTS idx_graph_entry_nodes_v2_node
                    ON graph_entry_nodes_v2(node_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS graph_entries_v2_fts
                USING fts5(content, entry_id UNINDEXED, tokenize='unicode61');
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

    def remove_engram_refs(self, engram_id: str) -> int:
        """Drop reverse-index rows for a hard-deleted engram."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM graph_engram_refs WHERE engram_id = ?",
                (engram_id,))
            return cur.rowcount if hasattr(cur, "rowcount") else 0

    def remove_entity(self, entity_id: str) -> None:
        """Drop adjacency + refs for an entity deleted from SemanticStore."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM graph_adjacency WHERE entity_id=? OR neighbor_id=? ",
                               (entity_id, entity_id))
            self._conn.execute("DELETE FROM graph_engram_refs WHERE entity_id=? ",
                               (entity_id,))

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

    def engrams_for_batch(self, entity_ids, limit_per_entity: int = 64):
        """Batch reverse lookup: one SQL query for N entities.

        Returns {entity_id: [(engram_id, weight), ...]} with at most
        `limit_per_entity` entries per entity, ordered by weight desc.
        Entities with no refs map to an empty list. Empty input returns {}.

        The INNER JOIN against engrams filters out soft-forgotten rows
        (forgotten_at > 0) at the SQL layer, so callers do not need to
        recheck. Replaces the O(N_engrams) Python scan that the legacy
        SpreadingActivation neighbor expansion used to do.
        """
        out = {eid: [] for eid in entity_ids}
        if not entity_ids:
            return out
        # Dedupe defensively; SQL IN can balloon with duplicates.
        uniq = list(dict.fromkeys(entity_ids))
        placeholders = ",".join("?" * len(uniq))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT r.entity_id, r.engram_id, r.weight
                FROM graph_engram_refs r
                JOIN engrams e ON e.id = r.engram_id
                WHERE r.entity_id IN ({placeholders})
                  AND e.forgotten_at = 0
                ORDER BY r.entity_id, r.weight DESC
                """,
                uniq,
            ).fetchall()
        per_count = {eid: 0 for eid in uniq}
        for r in rows:
            eid = r["entity_id"]
            if per_count.get(eid, 0) >= limit_per_entity:
                continue
            out[eid].append((r["engram_id"], float(r["weight"])))
            per_count[eid] = per_count.get(eid, 0) + 1
        return out

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


    # ---------- v1.76.10 persistent full-graph API -----------------

    @staticmethod
    def _now_v2() -> float:
        return time.time()

    def upsert_nodes_v2(self, nodes: list[GraphNodeV2]) -> dict[str, int]:
        now = self._now_v2()
        out: dict[str, int] = {}
        with self._lock, self._conn:
            for node in nodes:
                if node.node_key in out:
                    continue
                self._conn.execute(
                    """INSERT INTO graph_nodes_v2
                       (node_key, node_type, node_value, canonical_value,
                        metadata, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(node_key) DO UPDATE SET
                         node_value=excluded.node_value,
                         metadata=excluded.metadata,
                         updated_at=excluded.updated_at""",
                    (node.node_key, node.node_type, node.value,
                     node.canonical_value,
                     json.dumps(node.metadata, ensure_ascii=False), now, now))
                row = self._conn.execute(
                    "SELECT id FROM graph_nodes_v2 WHERE node_key=? ",
                    (node.node_key,)).fetchone()
                out[node.node_key] = int(row["id"])
        return out

    def add_edges_v2(self, edges: list[GraphEdgeV2],
                     node_map: dict[str, int]) -> dict[str, int]:
        now = self._now_v2()
        out: dict[str, int] = {}
        with self._lock, self._conn:
            for edge in edges:
                sid = node_map.get(edge.source_key)
                tid = node_map.get(edge.target_key)
                if sid is None or tid is None:
                    continue
                row = self._conn.execute(
                    "SELECT id FROM graph_edges_v2 WHERE edge_key=? ",
                    (edge.edge_key,)).fetchone()
                if row:
                    eid = int(row["id"])
                    self._conn.execute(
                        "UPDATE graph_edges_v2 SET weight=?, confidence=?, "
                        "status=?, metadata=?, updated_at=? WHERE id=? ",
                        (edge.weight, edge.confidence, edge.status,
                         json.dumps(edge.metadata, ensure_ascii=False), now, eid))
                    out[edge.edge_key] = eid
                    continue
                sem = self._conn.execute(
                    "SELECT id, confidence, weight FROM graph_edges_v2 "
                    "WHERE source_node_id=? AND target_node_id=? "
                    "AND relation_type=? ORDER BY id ASC LIMIT 1",
                    (sid, tid, edge.relation_type)).fetchone()
                if sem:
                    eid = int(sem["id"])
                    merged_conf = float(sem["confidence"] or 0.8) * 0.7 + edge.confidence * 0.3
                    merged_weight = float(sem["weight"] or 1.0) + edge.weight * 0.15
                    self._conn.execute(
                        "UPDATE graph_edges_v2 SET confidence=?, weight=?, "
                        "updated_at=? WHERE id=?",
                        (merged_conf, merged_weight, now, eid))
                    out[edge.edge_key] = eid
                    continue
                cur = self._conn.execute(
                    """INSERT INTO graph_edges_v2
                       (edge_key, source_node_id, target_node_id, relation_type,
                        source_memory_id, weight, confidence, status, metadata,
                        created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?) """,
                    (edge.edge_key, sid, tid, edge.relation_type,
                     edge.source_memory_id, edge.weight, edge.confidence,
                     edge.status,
                     json.dumps(edge.metadata, ensure_ascii=False), now, now))
                out[edge.edge_key] = int(cur.lastrowid)
        return out


    def add_entries_v2(self, entries: list[GraphEntryV2],
                       node_map: dict[str, int],
                       edge_map: dict[str, int]) -> list[int]:
        now = self._now_v2()
        ids: list[int] = []
        with self._lock, self._conn:
            for entry in entries:
                edge_id = None
                if entry.relation_type and len(entry.node_keys) >= 2:
                    ek = (f"{entry.node_keys[0]}|{entry.relation_type}|"
                          f"{entry.node_keys[1]}|{entry.source_memory_id}")
                    edge_id = edge_map.get(ek)
                row = self._conn.execute(
                    "SELECT id FROM graph_entries_v2 WHERE entry_key=? ",
                    (entry.entry_key,)).fetchone()
                if row:
                    entry_id = int(row["id"])
                    self._conn.execute(
                        "UPDATE graph_entries_v2 SET session_id=?, persona_id=?, "
                        "scope_id=?, entry_type=?, relation_type=?, content=?, "
                        "metadata=?, edge_id=?, updated_at=? WHERE id=? ",
                        (entry.session_id, entry.persona_id, entry.scope_id,
                         entry.entry_type, entry.relation_type, entry.content,
                         json.dumps(entry.metadata, ensure_ascii=False),
                         edge_id, now, entry_id))
                    self._conn.execute(
                        "DELETE FROM graph_entries_v2_fts WHERE entry_id=? ",
                        (entry_id,))
                    self._conn.execute(
                        "DELETE FROM graph_entry_nodes_v2 WHERE entry_id=? ",
                        (entry_id,))
                else:
                    cur = self._conn.execute(
                        """INSERT INTO graph_entries_v2
                           (entry_key, source_memory_id, session_id, persona_id,
                            scope_id, entry_type, relation_type, content,
                            metadata, edge_id, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?) """,
                        (entry.entry_key, entry.source_memory_id,
                         entry.session_id, entry.persona_id, entry.scope_id,
                         entry.entry_type, entry.relation_type, entry.content,
                         json.dumps(entry.metadata, ensure_ascii=False),
                         edge_id, now, now))
                    entry_id = int(cur.lastrowid)
                self._conn.execute(
                    "INSERT INTO graph_entries_v2_fts(entry_id, content) "
                    "VALUES (?,?) ", (entry_id, entry.content))
                pairs = [(entry_id, node_map[nk]) for nk in entry.node_keys
                         if nk in node_map]
                if pairs:
                    self._conn.executemany(
                        "INSERT OR IGNORE INTO graph_entry_nodes_v2"
                        "(entry_id, node_id) VALUES (?,?)", pairs)
                ids.append(entry_id)
        return ids

    def delete_graph_memory_v2(self, source_memory_id: str) -> dict[str, int]:
        stats = {"entries": 0, "edges": 0}
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id FROM graph_entries_v2 WHERE source_memory_id=? ",
                (source_memory_id,)).fetchall()
            entry_ids = [int(r["id"]) for r in rows]
            for eid in entry_ids:
                self._conn.execute(
                    "DELETE FROM graph_entries_v2_fts WHERE entry_id=? ", (eid,))
                self._conn.execute(
                    "DELETE FROM graph_entry_nodes_v2 WHERE entry_id=? ", (eid,))
            if entry_ids:
                ph = ",".join("?" * len(entry_ids))
                self._conn.execute(
                    f"DELETE FROM graph_entries_v2 WHERE id IN ({ph})", entry_ids)
                stats["entries"] = len(entry_ids)
            cur = self._conn.execute(
                "DELETE FROM graph_edges_v2 WHERE source_memory_id=? ",
                (source_memory_id,))
            stats["edges"] = cur.rowcount if hasattr(cur, "rowcount") else 0
            self._conn.execute(
                "DELETE FROM graph_nodes_v2 WHERE id NOT IN "
                "(SELECT DISTINCT node_id FROM graph_entry_nodes_v2)")
        return stats


    def full_graph_snapshot_v2(self, *, scope_id: str | None = None,
                               persona_id: str | None = None) -> dict:
        filters: list[str] = []
        params: list = []
        if scope_id is not None:
            filters.append("ge.scope_id = ?"); params.append(scope_id)
        if persona_id is not None:
            filters.append("ge.persona_id = ?"); params.append(persona_id)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        where2 = where.replace("ge.", "ge2.") if where else ""
        with self._lock, self._conn:
            nodes = self._conn.execute(
                f"""SELECT gn.id, gn.node_key, gn.node_type, gn.node_value,
                           gn.canonical_value, gn.metadata,
                           COUNT(DISTINCT ge.id) AS entry_count,
                           COUNT(DISTINCT ge.source_memory_id) AS memory_count
                    FROM graph_nodes_v2 gn
                    JOIN graph_entry_nodes_v2 gen ON gen.node_id = gn.id
                    JOIN graph_entries_v2 ge ON ge.id = gen.entry_id
                    {where}
                    GROUP BY gn.id ORDER BY gn.id ASC""", params).fetchall()
            edges = self._conn.execute(
                f"""SELECT DISTINCT e.id, e.edge_key, e.source_node_id,
                           e.target_node_id, e.relation_type,
                           e.source_memory_id, e.weight, e.confidence,
                           e.status, e.metadata
                    FROM graph_edges_v2 e
                    JOIN graph_entries_v2 ge
                      ON ge.source_memory_id = e.source_memory_id
                    {where} ORDER BY e.id ASC""", params).fetchall()
            memories = self._conn.execute(
                f"""SELECT ge.source_memory_id, ge.session_id, ge.persona_id,
                           ge.scope_id, ge.content, ge.metadata
                    FROM graph_entries_v2 ge
                    JOIN (SELECT ge2.source_memory_id, MAX(ge2.id) AS latest_id
                          FROM graph_entries_v2 ge2 {where2}
                          GROUP BY ge2.source_memory_id) latest
                      ON latest.latest_id = ge.id
                    ORDER BY ge.source_memory_id ASC""", params).fetchall()
        node_map: dict[int, dict] = {}
        for r in nodes:
            node_map[int(r["id"])] = {
                "id": int(r["id"]), "key": r["node_key"],
                "type": r["node_type"], "label": r["node_value"],
                "canonical_value": r["canonical_value"],
                "metadata": json.loads(r["metadata"] or "{}"),
                "entry_count": int(r["entry_count"] or 0),
                "memory_count": int(r["memory_count"] or 0),
                "degree": 0, "weight": 0.0}
        edge_list: list[dict] = []
        for r in edges:
            s = int(r["source_node_id"]); t = int(r["target_node_id"])
            if s not in node_map or t not in node_map:
                continue
            edge_list.append({
                "id": int(r["id"]), "key": r["edge_key"],
                "source": s, "target": t,
                "relation_type": r["relation_type"],
                "memory_id": r["source_memory_id"],
                "weight": float(r["weight"] or 1.0),
                "confidence": float(r["confidence"] or 0.8),
                "status": r["status"],
                "metadata": json.loads(r["metadata"] or "{}")})
            node_map[s]["degree"] += 1
            node_map[t]["degree"] += 1
        for n in node_map.values():
            n["weight"] = round(
                n["entry_count"] + n["memory_count"] * 0.75
                + n["degree"] * 0.35, 4)
        mem_list: list[dict] = []
        for r in memories:
            meta = r["metadata"] or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            mem_list.append({
                "memory_id": r["source_memory_id"],
                "summary": (meta.get("canonical_summary") or r["content"] or "")[:500],
                "session_id": meta.get("session_id") or r["session_id"],
                "persona_id": meta.get("persona_id") or r["persona_id"],
                "scope_id": meta.get("scope_id") or r["scope_id"],
                "importance": float(meta.get("importance", 0.0) or 0.0)})
        nodes_sorted = sorted(
            node_map.values(),
            key=lambda x: (-x["weight"], -x["degree"], x["label"]))
        return {"nodes": nodes_sorted, "edges": edge_list,
                "entries": [], "memories": mem_list}

    def graph_stats_v2(self) -> dict[str, int]:
        with self._lock, self._conn:
            return {
                "nodes": int(self._conn.execute(
                    "SELECT COUNT(*) c FROM graph_nodes_v2").fetchone()["c"]),
                "edges": int(self._conn.execute(
                    "SELECT COUNT(*) c FROM graph_edges_v2").fetchone()["c"]),
                "entries": int(self._conn.execute(
                    "SELECT COUNT(*) c FROM graph_entries_v2").fetchone()["c"]),
            }


__all__ = ["GraphStore"]
