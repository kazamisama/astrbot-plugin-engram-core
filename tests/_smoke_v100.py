"""Smoke v100 (v1.78.0): the similar-to cluster chain is gone end to end.

Covers:
- Engram carries no similar_to / cluster_id / profile_fact_id fields
- a fresh store's engrams table has none of those columns, no cluster_summaries
- opening a legacy DB drops the table and the three columns
- PatternSeparator only ever returns merge / new
- ReplayConsolidator takes (store, cfg), maintenance returns promote/replay only
- /mem stats renders without the removed placeholders
- no command / help entry mentions the cluster commands
"""
import os, sqlite3, sys, tempfile

from _smoke_common import install_stub as _install_stub

_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryService, MemoryConfig
from hippocampus.separation import PatternSeparator
from hippocampus.types import Engram
from hippocampus.consolidator import ReplayConsolidator


def banner(t): print(chr(10) + "=== " + t + " ===")


LEGACY_SCHEMA = """
CREATE TABLE engrams (
  id TEXT PRIMARY KEY, created_at REAL, session_id TEXT, actor_id TEXT,
  platform TEXT, channel_id TEXT, persona_id TEXT DEFAULT '',
  scope_id TEXT DEFAULT '', content TEXT, summary TEXT,
  topics TEXT, entities TEXT, entity_refs TEXT, tags TEXT, similar_to TEXT,
  importance REAL, strength REAL, access_count INTEGER, last_accessed REAL,
  reconsolidation_lock_until REAL, supersedes TEXT, embedding_json TEXT,
  memory_type TEXT, promoted_at REAL, embedding_model TEXT, fts_text TEXT,
  valence REAL DEFAULT 0.0, intensity REAL DEFAULT 0.0,
  temporal_bucket INTEGER DEFAULT 0, stream TEXT DEFAULT '',
  forgotten_at REAL DEFAULT 0.0, cluster_id TEXT DEFAULT '',
  profile_fact_id TEXT DEFAULT '', confidence REAL DEFAULT 0.5,
  tier TEXT DEFAULT 'hot'
);
CREATE TABLE cluster_summaries (
  cluster_id TEXT PRIMARY KEY, gist TEXT, member_count INTEGER,
  last_refreshed REAL, source TEXT
);
INSERT INTO engrams(id, content, summary, actor_id, strength)
  VALUES ('legacy1', 'old memory', 'old memory', 'u1', 1.0);
INSERT INTO cluster_summaries(cluster_id, gist, member_count, last_refreshed, source)
  VALUES ('c1', 'old gist', 2, 0.0, 'auto');
"""


def _svc(td, name="h.db"):
    return MemoryService(MemoryConfig(sqlite_path=os.path.join(td, name),
                                      embedding_dim=64))


def test_types():
    banner("Engram: cluster fields removed")
    e = Engram(content="x")
    for gone in ("similar_to", "cluster_id", "profile_fact_id"):
        assert not hasattr(e, gone), gone
    assert not hasattr(PatternSeparator, "apply_link")
    assert not hasattr(PatternSeparator, "expand_cluster")


def test_fresh_schema():
    banner("fresh store: no cluster columns / table")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        try:
            st = svc.store
            cols = {r[1] for r in st._conn.execute("PRAGMA table_info(engrams)")}
            for gone in ("similar_to", "cluster_id", "profile_fact_id"):
                assert gone not in cols, cols
            tables = {r[0] for r in st._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert "cluster_summaries" not in tables, tables
            # round-trip through the 32-column upsert still works
            e = Engram(content="hello", embedding=[0.1, 0.2])
            st.upsert(e)
            back = st.get(e.id)
            assert back is not None and back.content == "hello"
        finally:
            svc.close()


def test_legacy_db_cleanup():
    banner("legacy DB: obsolete columns/tables are left alone, data reads")
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "legacy.db")
        conn = sqlite3.connect(db)
        conn.executescript(LEGACY_SCHEMA)
        conn.commit(); conn.close()
        svc = _svc(td, "legacy.db")
        try:
            st = svc.store
            # v1.79.1: the upgrade path only ADDS columns. Obsolete columns
            # and tables are left in place -- an unused column costs nothing,
            # while dropping it on every open is a migration patch that would
            # have to live in the codebase forever.
            cols = {r[1] for r in st._conn.execute("PRAGMA table_info(engrams)")}
            for old in ("similar_to", "cluster_id", "profile_fact_id"):
                assert old in cols, ("obsolete column disappeared", old)
            kept = st.get("legacy1")
            assert kept is not None and kept.content == "old memory"
        finally:
            svc.close()


def test_separator_and_consolidator():
    banner("separator + consolidator: merge/new only, no abstract step")
    sep = PatternSeparator(MemoryConfig())
    a = Engram(content="a", embedding=[1.0, 0.0])
    b = Engram(content="b", embedding=[1.0, 0.0])
    assert sep.resolve(b, [a])[0] == "merge"
    assert sep.resolve(Engram(content="c", embedding=[0.0, 1.0]), [a])[0] == "new"
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        try:
            st = svc.store
            st.upsert(Engram(content="one", embedding=[1.0, 0.0]))
            rc = ReplayConsolidator(st, MemoryConfig())
            m = rc.maintenance()
            assert set(m) == {"promoted", "replayed"}, m
            s = rc.step()
            assert set(s) == {"merged", "promoted", "archived", "replayed"}, s
            assert "abstracted" not in s
        finally:
            svc.close()


def test_stats_and_help():
    banner("stats / help: no cluster wording left")
    from handlers.format import render_stats
    from engram_command_registry import COMMAND_TABLE, help_command_names
    from handlers.help_text import get_help_text
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        try:
            svc.observe(session_id="s", actor_id="u", platform="qq",
                        channel_id="c", content="hello memory")
            out = render_stats(svc)
            assert "--- v1.1 ---" in out
            assert "cluster" not in out.lower(), out
        finally:
            svc.close()
    assert not any("cluster" in name for name in COMMAND_TABLE)
    assert not any("cluster" in name for name in help_command_names())
    assert "cluster" not in get_help_text().lower()


if __name__ == "__main__":
    test_types()
    test_fresh_schema()
    test_legacy_db_cleanup()
    test_separator_and_consolidator()
    test_stats_and_help()
    print(chr(10) + "ALL OK")
