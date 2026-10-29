"""Smoke v103 (v1.78.3): the atom layer survives an upgraded database.

Found on the live store: atoms had zero rows and MemoryService.atom_store was
None. Root cause: AtomStore._init_schema created idx_atoms_expires INSIDE the
CREATE TABLE script, i.e. BEFORE the ALTER TABLE migration that adds expires_at
to a pre-v1.76.5 atoms table. On such a table the index raised
"no such column: expires_at", the constructor failed, and _ensure_atom_layer
swallowed the exception -- so every upgraded install ran with the atom layer
silently disabled, no matter how many relations arrived.

Tests:
- a legacy atoms table (no ttl_days / event_time / expires_at) opens cleanly,
  gains the three columns plus idx_atoms_expires, and round-trips an atom
- MemoryService._ensure_atom_layer() on that database returns True
- the production path produces an atom: store_summary() with a "likes"
  relation -> one atoms row (the LLM relation mirror)
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _smoke_common import install_stub

install_stub()

from hippocampus import MemoryConfig, MemoryService  # noqa: E402
from hippocampus.atom_store import AtomStore  # noqa: E402
from hippocampus.types import MemoryAtom  # noqa: E402

FAILS = []
CHECKS = []


def check(ok, msg):
    print(("ok   " if ok else "FAIL ") + msg)
    CHECKS.append(bool(ok))
    if not ok:
        FAILS.append(msg)


# The live pre-v1.76.5 schema: 22 columns, none of the temporal trio.
LEGACY_ATOMS_DDL = """
CREATE TABLE atoms (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.5,
  evidence_count INTEGER NOT NULL DEFAULT 1,
  source_engram_ids TEXT NOT NULL DEFAULT '[]',
  actor_id TEXT NOT NULL DEFAULT '',
  platform TEXT NOT NULL DEFAULT '',
  channel_id TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  last_seen REAL NOT NULL,
  last_accessed REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  decay_type TEXT NOT NULL DEFAULT 'episodic',
  importance REAL NOT NULL DEFAULT 0.5,
  strength REAL NOT NULL DEFAULT 1.0,
  access_count INTEGER NOT NULL DEFAULT 0,
  tags TEXT NOT NULL DEFAULT '[]',
  attributes TEXT NOT NULL DEFAULT '{}',
  UNIQUE(subject COLLATE NOCASE, predicate COLLATE NOCASE, object COLLATE NOCASE)
);
"""


def _legacy_db(td, name="legacy.db"):
    path = os.path.join(td, name)
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_ATOMS_DDL)
    conn.commit()
    conn.close()
    return path


def test_store_migrates():
    print(chr(10) + "=== AtomStore migrates a legacy atoms table ===")
    with tempfile.TemporaryDirectory() as td:
        path = _legacy_db(td)
        store = AtomStore(path)          # must not raise
        try:
            cols = {r[1] for r in store._conn.execute("PRAGMA table_info(atoms)")}
            for col in ("ttl_days", "event_time", "expires_at"):
                check(col in cols, "column added: " + col)
            idx = {r[1] for r in store._conn.execute("PRAGMA index_list(atoms)")}
            check("idx_atoms_expires" in idx, "idx_atoms_expires created")
            atom = MemoryAtom(kind="preference", subject="Alice",
                              predicate="likes", object="espresso",
                              confidence=0.9)
            store.upsert(atom)
            n = store._conn.execute("SELECT COUNT(*) FROM atoms").fetchone()[0]
            check(n == 1, "atom round-trips through the migrated table")
            got = store.by_triple("alice", "likes", "espresso")
            check(got is not None and got.subject.lower() == "alice",
                  "by_triple finds the merged atom (COLLATE NOCASE)")
        finally:
            store.close()


def test_service_atom_layer_on_legacy_db():
    print(chr(10) + "=== the production path produces an atom ===")
    with tempfile.TemporaryDirectory() as td:
        path = _legacy_db(td, "h.db")
        svc = MemoryService(MemoryConfig(sqlite_path=path, embedding_dim=64))
        try:
            check(svc._ensure_atom_layer() is True,
                  "_ensure_atom_layer succeeds on an upgraded DB")
            check(svc.atom_store is not None, "atom_store constructed")
            summary = {
                "summary": "Alice likes espresso.",
                "key_facts": [],
                "topics": ["coffee"],
                "participants": ["Alice"],
                "participant_names": {},
                "relations": [{
                    "subject": "Alice", "relation": "likes", "object": "espresso",
                    "confidence": 0.9, "subject_type": "person",
                    "object_type": "object"}],
                "importance": 0.8,
            }
            identity = {"session_id": "s1", "actor_id": "alice",
                        "platform": "test", "channel_id": "c1",
                        "memory_type": "episodic"}
            e = svc.store_summary(summary, identity)
            check(e is not None, "store_summary stored the engram")
            rows = svc.store._conn.execute(
                "SELECT subject, predicate, object, kind FROM atoms").fetchall()
            check(len(rows) == 1, "exactly one atom from one LLM relation, got %d"
                  % len(rows))
            if rows:
                subj, pred, obj, kind = rows[0]
                check((subj.lower(), pred.lower(), obj.lower())
                      == ("alice", "likes", "espresso"),
                      "atom is the mirrored triple: %r" % (tuple(rows[0]),))
                check(kind == "preference", "kind classified as preference")
        finally:
            svc.close()


test_store_migrates()
test_service_atom_layer_on_legacy_db()

print(chr(10) + "TOTAL=" + str(len(CHECKS)) + " FAIL=" + str(len(FAILS)))
if FAILS:
    raise SystemExit(1)
