"""v1.36 smoke: persona-scoped memory isolation.

Engrams written under different persona ids must not leak across recall;
passing persona_id=None disables scoping.
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryService, MemoryConfig, Cue


def _mk(db):
    cfg = MemoryConfig(sqlite_path=db, embedding_name="hash", llm_name="rule")
    cfg.memory_decay_enabled = False
    return MemoryService(cfg)


def main():
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    svc = _mk(db)
    try:
        assert MemoryConfig().persona_isolation_enabled is True
        print("[OK] persona_isolation_enabled defaults True")

        common = dict(session_id="s1", actor_id="u1", platform="qq", channel_id="g100")
        e_cat = svc.observe(content="cat persona note alpha", persona_id="cat", **common)
        e_dog = svc.observe(content="dog persona note beta", persona_id="dog", **common)

        assert svc.store.get(e_cat.id).persona_id == "cat"
        assert svc.store.get(e_dog.id).persona_id == "dog"
        print("[OK] persona_id persisted on engram")

        r_cat = svc.recall(Cue(text="note", actor_id="u1", channel_id="g100",
                               persona_id="cat", k=10, mode="hybrid"))
        pids = {getattr(e, "persona_id", "") for e in r_cat.engrams}
        assert pids <= {"cat"}, ("leak in cat recall", pids)

        r_dog = svc.recall(Cue(text="note", actor_id="u1", channel_id="g100",
                               persona_id="dog", k=10, mode="hybrid"))
        pids2 = {getattr(e, "persona_id", "") for e in r_dog.engrams}
        assert pids2 <= {"dog"}, ("leak in dog recall", pids2)
        print("[OK] cross-persona recall isolated")

        r_all = svc.recall(Cue(text="note", actor_id="u1", channel_id="g100",
                               persona_id=None, k=10, mode="hybrid"))
        pids3 = {getattr(e, "persona_id", "") for e in r_all.engrams}
        assert {"cat", "dog"} <= pids3, ("None scope must not filter persona", pids3)
        print("[OK] persona_id=None disables scoping")
        print("ALL PASS")
    finally:
        try:
            svc.close()
        except Exception:
            pass
        try:
            os.remove(db)
        except Exception:
            pass




def test_old_db_migration_and_index():
    """Regression: opening an OLD engrams table (no persona_id column) must
    migrate cleanly, build idx_persona, and not leak legacy rows into a
    persona-scoped recall."""
    import sqlite3
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    conn = sqlite3.connect(db)
    conn.executescript("""
      CREATE TABLE engrams (
        id TEXT PRIMARY KEY, created_at REAL, session_id TEXT, actor_id TEXT,
        platform TEXT, channel_id TEXT, content TEXT, summary TEXT,
        topics TEXT, entities TEXT, entity_refs TEXT, tags TEXT, similar_to TEXT,
        importance REAL, strength REAL, access_count INTEGER, last_accessed REAL,
        reconsolidation_lock_until REAL, supersedes TEXT, embedding_json TEXT,
        memory_type TEXT, promoted_at REAL, embedding_model TEXT, fts_text TEXT,
        valence REAL DEFAULT 0.0, intensity REAL DEFAULT 0.0, temporal_bucket INTEGER DEFAULT 0,
        stream TEXT DEFAULT '', forgotten_at REAL DEFAULT 0.0,
        cluster_id TEXT DEFAULT '', profile_fact_id TEXT DEFAULT '',
        confidence REAL DEFAULT 0.5, tier TEXT DEFAULT 'hot'
      );
      INSERT INTO engrams(id, content, summary, actor_id, channel_id, strength)
        VALUES ('old1', 'legacy memory', 'legacy memory', 'a', 'c', 1.0);
    """)
    conn.commit(); conn.close()
    svc = _mk(db)
    try:
        old = svc.store.get("old1")
        assert old is not None and old.persona_id == "", old
        idx = {r[1] for r in svc.store._conn.execute("PRAGMA index_list(engrams)").fetchall()}
        assert "idx_persona" in idx, idx
        svc.observe(session_id="s", actor_id="a", platform="qq", channel_id="c",
                    content="new scoped note", persona_id="cat")
        r = svc.recall(Cue(text="memory note", actor_id="a", channel_id="c",
                           persona_id="cat", k=10, mode="hybrid"))
        pids = {getattr(e, "persona_id", "") for e in r.engrams}
        assert pids <= {"cat"}, ("legacy leaked", pids)
        print("[OK] old-DB migration + idx_persona + scoped recall")
    finally:
        try: svc.close()
        except Exception: pass
        try: os.remove(db)
        except Exception: pass


if __name__ == "__main__":
    main()
    test_old_db_migration_and_index()
    print("ALL PASS v58")
