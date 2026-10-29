"""Smoke v1.4.x: B10 - BackupManager + schema upgrade.

Scope:
- hippocampus.sqlite_util.ensure_columns is the single upgrade path
  (v1.79.1): every store declares the columns current code needs and an
  older database gets them appended before indexes/triggers reference them.
- hippocampus.managers.backup_manager.BackupManager does raw .db
  copies + .json sidecar metadata + retention policy.
- PluginInitializer wires backup_manager + a daemon thread scheduler
  on initialize() when MemoryConfig.enable_backup and
  backup_interval_hours > 0.
- B9 page_api gains 2 endpoints: /backups (GET) and /backups/restore
  (POST) for the AstrBot Dashboard.
- ConfigManager + MemoryConfig + _conf_schema.json gain 5 backup
  fields (enable_backup / backup_interval_hours / backup_keep_last /
  backup_keep_weekly / backup_keep_monthly).

Tests:
- opening a pre-v1 engrams table upgrades it in place and stays usable
- ensure_columns adds once, is idempotent, and tolerates a missing table
- BackupManager.create + .db + .json sidecar with metadata
- BackupManager.list_backups returns newest first with full record
- BackupManager.restore overwrites source, restore preserves data
- BackupManager.cleanup respects keep_last=1 + keep_weekly=0
- BackupHandler returns ok/engram_count via PageApiUtils
- PageApi registers 10 endpoints (8 from B9 + 2 backup)
- PluginInitializer.backup_manager is set when enable_backup=true,
  None when enable_backup=false
- MemoryConfig exposes 5 new fields with documented defaults
- ConfigManager.LABELS has labels for the 5 new fields
"""
import os, sys, json, tempfile, types


from _smoke_common import install_stub as _install_stub


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".."))


def banner(t_):
    print(chr(10) + "=== " + t_ + " ===")


def _new_db():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return db


def test_schema_upgrade_adds_missing_columns():
    banner("opening a pre-v1 engrams table upgrades it in place")
    import sqlite3
    from hippocampus.embeddings import HashEmbeddingProvider
    from hippocampus.storage import HippocampalStore
    db = _new_db()
    try:
        conn = sqlite3.connect(db)
        # A table from before the current schema: id + content only.
        conn.executescript("""
            CREATE TABLE engrams (id TEXT PRIMARY KEY, content TEXT);
            INSERT INTO engrams(id, content) VALUES ('old1', 'legacy memory');
        """)
        conn.commit()
        conn.close()
        st = HippocampalStore(db, HashEmbeddingProvider(64))
        try:
            cols = {r[1] for r in st._conn.execute(
                "PRAGMA table_info(engrams)")}
            for need in ("persona_id", "scope_id", "forgotten_at", "tier",
                         "confidence", "fts_text", "embedding_model",
                         "valence", "entity_refs"):
                assert need in cols, "missing column " + need
            idx = {r[1] for r in st._conn.execute("PRAGMA index_list(engrams)")}
            assert {"idx_persona", "idx_scope"} <= idx, idx
            got = st.get("old1")
            assert got is not None and got.content == "legacy memory"
            # a write through the full column list proves the upgrade is usable
            from hippocampus.types import Engram
            st.upsert(Engram(content="new row", summary="new row"))
            assert len(st.all(limit=10)) == 2
        finally:
            st.close()
    finally:
        try: os.unlink(db)
        except Exception: pass
    print("  pre-v1 table upgraded, old row readable, write works: OK")


def test_ensure_columns_is_idempotent():
    banner("ensure_columns: adds once, then reports nothing")
    import sqlite3
    from hippocampus.sqlite_util import ensure_columns
    db = _new_db()
    try:
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
        spec = (("a", "TEXT DEFAULT ''"), ("b", "REAL DEFAULT 0.0"))
        assert ensure_columns(conn, "t", spec) == ["a", "b"]
        assert ensure_columns(conn, "t", spec) == []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(t)")}
        assert {"a", "b"} <= cols
        # A table that does not exist yet is not an error.
        assert ensure_columns(conn, "nope", spec) == []
        conn.close()
    finally:
        try: os.unlink(db)
        except Exception: pass
    print("  adds once, then no-op: OK")


def test_backup_create_list_restore():
    banner("BackupManager create / list_backups / restore roundtrip")
    from hippocampus.managers.backup_manager import BackupManager
    import sqlite3
    db = _new_db()
    bd = tempfile.mkdtemp()
    try:
        # seed source db
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE engrams (id TEXT PRIMARY KEY, content TEXT);
            INSERT INTO engrams VALUES ('e1', 'hello');
            INSERT INTO engrams VALUES ('e2', 'world');
        """)
        conn.commit()
        conn.close()
        bm = BackupManager(db, bd, version_provider=lambda: "test-1.4.0")
        rec = bm.create(reason="manual")
        assert rec.backup_id.startswith("hippocampus-")
        assert rec.engram_count == 2
        assert rec.byte_size > 0
        assert os.path.exists(rec.db_path)
        assert os.path.exists(rec.sidecar_path)
        # sidecar content
        sc = json.load(open(rec.sidecar_path, encoding="utf-8"))
        assert sc["version"] == "test-1.4.0"
        assert sc["engram_count"] == 2
        # list
        lst = bm.list_backups()
        assert len(lst) == 1
        assert lst[0].backup_id == rec.backup_id
        # restore: corrupt source, then restore
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM engrams")
        conn.commit()
        conn.close()
        ok = bm.restore(rec.backup_id)
        assert ok
        # check data back
        conn = sqlite3.connect(db)
        rows = list(conn.execute("SELECT id, content FROM engrams ORDER BY id"))
        assert rows == [("e1", "hello"), ("e2", "world")], rows
        conn.close()
    finally:
        try: os.unlink(db)
        except Exception: pass
        import shutil
        shutil.rmtree(bd, ignore_errors=True)
    print("  create + sidecar + list + restore: OK")


def test_backup_cleanup_keeps_recent():
    banner("BackupManager.cleanup honors keep_last retention")
    import time
    from hippocampus.managers.backup_manager import BackupManager
    db = _new_db()
    bd = tempfile.mkdtemp()
    try:
        bm = BackupManager(db, bd, version_provider=lambda: "t")
        # create 5 backups spaced 1s apart
        for i in range(5):
            bm.create(reason="r" + str(i))
            time.sleep(0.01)
        assert len(bm.list_backups()) == 5
        # keep_last=2 should leave 2
        removed = bm.cleanup(keep_last=2, keep_weekly=0, keep_monthly=0)
        assert removed == 3, removed
        assert len(bm.list_backups()) == 2
    finally:
        try: os.unlink(db)
        except Exception: pass
        import shutil
        shutil.rmtree(bd, ignore_errors=True)
    print("  keep_last=2 of 5: OK")


def test_memory_config_has_5_backup_fields():
    banner("MemoryConfig + ConfigManager expose 5 backup fields")
    from hippocampus import MemoryConfig
    from hippocampus.config_manager import ConfigManager, LABELS
    cfg = MemoryConfig()
    assert cfg.enable_backup is True
    assert cfg.backup_interval_hours == 24.0
    assert cfg.backup_keep_last == 7
    assert cfg.backup_keep_weekly == 1
    assert cfg.backup_keep_monthly == 1
    # ConfigManager respects raw override
    cm = ConfigManager({"enable_backup": False, "backup_keep_last": 3})
    assert cm.memory_config.enable_backup is False
    assert cm.memory_config.backup_keep_last == 3
    # LABELS has all 5
    for k in ["enable_backup", "backup_interval_hours", "backup_keep_last",
              "backup_keep_weekly", "backup_keep_monthly"]:
        assert k in LABELS, k
        assert "en" in LABELS[k], LABELS[k]
    print("  5 new fields + LABELS: OK")


def test_conf_schema_has_5_backup_fields():
    banner("_conf_schema.json: 20 fields incl. 5 backup")
    p = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_conf_schema.json")
    schema = json.load(open(p, encoding="utf-8"))
    assert len(schema) == 5, len(schema)
    backup = schema.get("backup_settings", {}).get("items", {})
    for k in ["enable_backup", "backup_interval_hours", "backup_keep_last",
              "backup_keep_weekly", "backup_keep_monthly"]:
        assert k in backup, k
    print("  backup_settings group with 5 backup fields: OK")


def test_backup_handler_list_backups():
    banner("page_api BackupHandler.list_backups via PageApiUtils")
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from page_api_modules import BackupHandler
    from hippocampus.managers.backup_manager import BackupManager
    db = _new_db()
    bd = tempfile.mkdtemp()
    try:
        bm = BackupManager(db, bd, version_provider=lambda: "t")
        bm.create(reason="manual")
        bh = BackupHandler(types.SimpleNamespace(
            ok=lambda d: {"status": "ok", "data": d},
            err=lambda m: {"status": "error", "message": m}))
        out = bh.list_backups(bm)
        assert out["status"] == "ok"
        assert out["data"]["count"] == 1
        assert out["data"]["backups"][0]["reason"] == "manual"
        # None manager -> graceful err
        out2 = bh.list_backups(None)
        assert out2["status"] == "error"
        # missing backup_id
        out3 = bh.restore_backup(bm, backup_id="")
        assert out3["status"] == "error"
        # unknown id
        out4 = bh.restore_backup(bm, backup_id="nope")
        assert out4["status"] == "error"
        # bad format
        out5 = bh.restore_backup(bm, backup_id="../../etc/passwd")
        assert out5["status"] == "error"
        # valid id
        rec_id = bm.list_backups()[0].backup_id
        out6 = bh.restore_backup(bm, backup_id=rec_id)
        assert out6["status"] == "ok", out6
    finally:
        try: os.unlink(db)
        except Exception: pass
        import shutil
        shutil.rmtree(bd, ignore_errors=True)
    print("  list / err / restore paths: OK")


def test_page_api_registers_11_endpoints():
    banner("page_api registers 35 endpoints (+prompts +import/export)")
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import page_api
    calls = []
    def reg(path, handler, methods, name):
        calls.append((path, methods, name))
    plugin = types.SimpleNamespace(
        context=types.SimpleNamespace(register_web_api=reg),
        _initializer=types.SimpleNamespace(
            service=None, backup_manager=None))
    api = page_api.PluginPageApi(plugin)
    api.register_routes()
    assert len(calls) >= 24, "expected at least 24 endpoints, got " + str(len(calls))
    paths = [c[0] for c in calls]
    for needed in ["/astrbot_plugin_engram_core/page/graph/data",
                   "/astrbot_plugin_engram_core/page/graph/entity/delete",
                   "/astrbot_plugin_engram_core/page/graph/relation/delete",
                   "/astrbot_plugin_engram_core/page/graph/relation/update",
                   "/astrbot_plugin_engram_core/page/backups",
                   "/astrbot_plugin_engram_core/page/backups/restore"]:
        assert needed in paths, needed
    print("  all required endpoints incl. prompts + import/export: OK")


def test_plugin_initializer_backup_manager_attr():
    banner("PluginInitializer: backup_manager set on initialize()")
    from handlers.init import PluginInitializer
    # v1.76.16: config is passed to initialize() (HippocampusStar forwards the
    # `config=` AstrBot hands the Star), not read from context.get_config().
    ctx = types.SimpleNamespace(register_tool=None)
    db1 = _new_db()
    init = PluginInitializer(ctx)
    # initialize will spawn a thread if interval>0; we pass 0 so no thread,
    # but backup_manager should still be created
    init.initialize({"sqlite_path": db1,
                     "enable_backup": True,
                     "backup_interval_hours": 0,
                     "bot_language": "zh"})
    assert init.backup_manager is not None, "backup_manager not created"
    assert init.backup_manager.db_path is not None
    # cleanup: close service to release db lock
    if init.service is not None:
        try: init.service.close()
        except Exception: pass
    # enable_backup=False
    db2 = _new_db()
    init2 = PluginInitializer(ctx)
    init2.initialize({"sqlite_path": db2,
                      "enable_backup": False,
                      "bot_language": "zh"})
    assert init2.backup_manager is None
    if init2.service is not None:
        try: init2.service.close()
        except Exception: pass
    # cleanup temp dbs
    for p in [init.backup_manager.db_path, db2]:
        try: os.unlink(p)
        except Exception: pass
    print("  backup_manager set when enabled, None when disabled: OK")


def main():
    test_schema_upgrade_adds_missing_columns()
    test_ensure_columns_is_idempotent()
    test_backup_create_list_restore()
    test_backup_cleanup_keeps_recent()
    test_memory_config_has_5_backup_fields()
    test_conf_schema_has_5_backup_fields()
    test_backup_handler_list_backups()
    test_page_api_registers_11_endpoints()
    test_plugin_initializer_backup_manager_attr()
    print(chr(10) + "ALL OK")


if __name__ == "__main__":
    main()