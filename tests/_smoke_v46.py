"""Smoke v46: RelationStore uses its own table (llm_relations), isolated
from SemanticStore's `relations` table, and auto-migrates new columns.

Regressions covered:
  1) (orig) legacy table missing rkey -> auto-migrate via PRAGMA + ALTER.
  2) (v1.29) RelationStore no longer collides with SemanticStore's
     `relations` table (different column shape). Its table is
     `llm_relations`; subject_type/object_type columns auto-migrate.
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_LEGACY = (
    "CREATE TABLE llm_relations ("
    " id TEXT PRIMARY KEY,"
    " subject TEXT, predicate TEXT, object TEXT,"
    " confidence REAL, actor_id TEXT, channel_id TEXT,"
    " source_engram_id TEXT,"
    " created_at REAL, updated_at REAL,"
    " superseded_by TEXT, forgotten_at REAL)"
)

# SemanticStore's table shape (subject_id/object_id) must NOT be touched.
_SEMANTIC_RELATIONS = (
    "CREATE TABLE relations ("
    " id TEXT PRIMARY KEY, subject_id TEXT, predicate TEXT, object_id TEXT,"
    " source_engram_id TEXT, confidence REAL, created_at REAL)"
)


def _cols(db, table):
    c = sqlite3.connect(db)
    try:
        return [r[1] for r in c.execute(
            "PRAGMA table_info(" + table + ")").fetchall()]
    finally:
        c.close()


def main():
    from hippocampus.relation_store import RelationStore, Relation
    td = tempfile.mkdtemp()

    # 1) legacy llm_relations without new columns -> migrates
    legacy = os.path.join(td, "legacy.db")
    c = sqlite3.connect(legacy)
    c.execute(_LEGACY)
    c.commit()
    c.close()
    assert "rkey" not in _cols(legacy, "llm_relations")
    rs = RelationStore(legacy)
    rs._ensure_conn()
    cols = _cols(legacy, "llm_relations")
    assert "rkey" in cols, cols
    assert "subject_type" in cols and "object_type" in cols, cols
    print("  legacy llm_relations migrated: rkey/subject_type/object_type OK")

    # 2) fresh db has all columns from the start
    fresh = os.path.join(td, "fresh.db")
    rs2 = RelationStore(fresh)
    rs2._ensure_conn()
    cols2 = _cols(fresh, "llm_relations")
    assert "rkey" in cols2 and "subject_type" in cols2 and "object_type" in cols2, cols2
    print("  fresh llm_relations has all columns OK")

    # 3) isolation: a pre-existing SemanticStore `relations` table is left
    #    intact; RelationStore writes to llm_relations and round-trips.
    shared = os.path.join(td, "shared.db")
    c = sqlite3.connect(shared)
    c.execute(_SEMANTIC_RELATIONS)
    c.commit(); c.close()
    rs3 = RelationStore(shared)
    rs3.add_with_supersede(Relation(
        subject="Alice", predicate="resides_in", object="Shanghai",
        confidence=0.8, subject_type="person", object_type="place"))
    # SemanticStore table untouched (still subject_id shape)
    assert _cols(shared, "relations") == [
        "id", "subject_id", "predicate", "object_id",
        "source_engram_id", "confidence", "created_at"], _cols(shared, "relations")
    got = rs3.relations_for("Alice")
    assert len(got) == 1 and got[0].subject_type == "person", got
    print("  isolation from SemanticStore.relations OK")

    print("v46 OK")


if __name__ == "__main__":
    main()
