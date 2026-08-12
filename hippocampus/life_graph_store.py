"""LifeGraphStore: v1.76 persona-scoped entity graph for life plugins.

Downstream plugins (e.g. astrbot_plugin_your_own_life L2-02) store a
layered dimension model: source facts (platform/url) are written by the
system, semantic entities (person/project/community/topic) are upserted
by the plugin, and typed edges connect them. ``same_as`` links are
proposals; the owner confirms them in the downstream WebUI.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


class LifeGraphStore:
    """Persistent entities + typed links, partitioned by persona_id."""

    def __init__(self, sqlite_path: str, busy_timeout_ms: int = 5000):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(sqlite_path), timeout=max(1.0, busy_timeout_ms / 1000.0)
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS life_entities ("
            "id TEXT NOT NULL PRIMARY KEY,"
            "persona_id TEXT NOT NULL,"
            "dimension TEXT NOT NULL,"
            "entity_id TEXT NOT NULL,"
            "name TEXT NOT NULL,"
            "canonical_url TEXT DEFAULT '',"
            "first_seen_at REAL NOT NULL,"
            "last_seen_at REAL NOT NULL,"
            "seen_count INTEGER NOT NULL DEFAULT 1,"
            "UNIQUE (persona_id, dimension, entity_id))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS life_entity_links ("
            "id TEXT NOT NULL PRIMARY KEY,"
            "persona_id TEXT NOT NULL,"
            "src_entity_id TEXT NOT NULL,"
            "relation TEXT NOT NULL,"
            "dst_entity_id TEXT NOT NULL,"
            "weight REAL NOT NULL DEFAULT 1.0,"
            "first_seen_at REAL NOT NULL,"
            "last_seen_at REAL NOT NULL,"
            "seen_count INTEGER NOT NULL DEFAULT 1,"
            "UNIQUE (persona_id, src_entity_id, relation, dst_entity_id))"
        )
        self._conn.commit()

    def upsert_entity(self, persona_id: str, entity: dict) -> str:
        """Upsert one entity; returns the row id."""
        dimension = str(entity.get("dimension") or "topic").strip()
        entity_key = str(entity.get("entity_id") or "").strip()
        if not persona_id or not dimension or not entity_key:
            return ""
        requested_name = str(entity.get("name") or "").strip()
        requested_url = str(entity.get("canonical_url") or "").strip()
        now = _now()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, canonical_url, seen_count FROM life_entities "
                "WHERE persona_id = ? AND dimension = ? AND entity_id = ?",
                (persona_id, dimension, entity_key),
            ).fetchone()
            if row is None:
                rid = _new_id()
                name = requested_name or entity_key
                url = requested_url
                self._conn.execute(
                    "INSERT INTO life_entities "
                    "(id, persona_id, dimension, entity_id, name, canonical_url, "
                    "first_seen_at, last_seen_at, seen_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (rid, persona_id, dimension, entity_key, name,
                     url, now, now),
                )
            else:
                rid = str(row["id"])
                name = requested_name or str(row["name"] or "")
                url = requested_url if requested_url else str(row["canonical_url"] or "")
                self._conn.execute(
                    "UPDATE life_entities SET name = ?, canonical_url = ?, "
                    "last_seen_at = ?, seen_count = seen_count + 1 "
                    "WHERE id = ?",
                    (name, url, now, rid),
                )
            self._conn.commit()
            return rid

    def link_entities(self, persona_id: str, src_entity_id: str,
                      relation: str, dst_entity_id: str,
                      weight: float = 1.0) -> bool:
        """Upsert one typed edge; returns True on first creation."""
        src = str(src_entity_id or "").strip()
        rel = str(relation or "").strip()
        dst = str(dst_entity_id or "").strip()
        if not persona_id or not src or not rel or not dst:
            return False
        now = _now()
        with self._lock:
            src_row = self._conn.execute(
                "SELECT 1 FROM life_entities WHERE persona_id = ? AND id = ?",
                (persona_id, src),
            ).fetchone()
            dst_row = self._conn.execute(
                "SELECT 1 FROM life_entities WHERE persona_id = ? AND id = ?",
                (persona_id, dst),
            ).fetchone()
            if src_row is None or dst_row is None:
                return False
            row = self._conn.execute(
                "SELECT id FROM life_entity_links "
                "WHERE persona_id = ? AND src_entity_id = ? "
                "AND relation = ? AND dst_entity_id = ?",
                (persona_id, src, rel, dst),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO life_entity_links "
                    "(id, persona_id, src_entity_id, relation, dst_entity_id, "
                    "weight, first_seen_at, last_seen_at, seen_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (_new_id(), persona_id, src, rel, dst,
                     max(0.0, float(weight if weight is not None else 1.0)), now, now),
                )
                self._conn.commit()
                return True
            self._conn.execute(
                "UPDATE life_entity_links SET weight = ?, last_seen_at = ?, "
                "seen_count = seen_count + 1 WHERE id = ?",
                (max(0.0, float(weight if weight is not None else 1.0)), now, str(row["id"])),
            )
            self._conn.commit()
            return False

    def get_entity(self, persona_id: str, dimension: str,
                   entity_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM life_entities "
                "WHERE persona_id = ? AND dimension = ? AND entity_id = ?",
                (persona_id, dimension, entity_id),
            ).fetchone()
            return dict(row) if row else None

    def list_entities(self, persona_id: str, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM life_entities WHERE persona_id = ? "
                "ORDER BY last_seen_at DESC LIMIT ?",
                (persona_id, max(1, int(limit))),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_links(self, persona_id: str, limit: int = 1000) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM life_entity_links WHERE persona_id = ? "
                "ORDER BY last_seen_at DESC LIMIT ?",
                (persona_id, max(1, int(limit))),
            ).fetchall()
            return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
