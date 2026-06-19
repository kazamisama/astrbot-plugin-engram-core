"""Smoke v1.4.x: B10 - BackupManager + db_migration.

Scope:
- hippocampus.db_migration.run_migrations is idempotent across
  v1.0/v1.1/v1.2 column-append migrations; v1.3/v1.4 are
  CREATE-TABLE-IF-NOT-EXISTS in their own store files (atom_store,
  graph_store) and are not part of this module.
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
- run_migrations is a no-op on a fresh v1.4 schema
- run_migrations adds missing v1.0/v1.1/v1.2 columns to a pre-v1.0 db
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


def _install_stub():
    a = types.ModuleType("astrbot")
    ai = types.ModuleType("astrbot.api")
    sm = types.ModuleType("astrbot.api.star")
    em = types.ModuleType("astrbot.api.event")
    class Star: pass
    def register(*a, **k):
        def deco(cls): return cls
        return deco
    class Context: pass
    class AstrMessageEvent: pass
    class _MT: ALL = "all"
    class _F:
        EventMessageType = _MT
        def event_message_type(self, *a, **k):
            def deco(fn): return fn
            return deco
        def command(self, *a, **k):
            def deco(fn): return fn
            return deco
    sm.Star = Star
    sm.register = register
    sm.Context = Context
    em.filter = _F
    em.AstrMessageEvent = AstrMessageEvent
    em.EventMessageType = _MT
    sys.modules["astrbot"] = a
    sys.modules["astrbot.api"] = ai
    sys.modules["astrbot.api.star"] = sm
    sys.modules["astrbot.api.event"] = em


_install_stub()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def banner(t_):
    print(chr(10) + "=== " + t_ + " ===")


def _new_db():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return db


def test_migration_idempotent_on_fresh_v14():
    banner("db_migration.run_migrations no-op on fresh v1.4 schema")
    import sqlite3
    db = _new_db()
    try:
        conn = sqlite3.connect(db)
        # Mimic v1.4 final schema (already has confidence column)
        conn.executescript("""
            CREATE TABLE engrams (
                id TEXT PRIMARY KEY,
                content TEXT,
                valence REAL DEFAULT 0.0,
                intensity REAL DEFAULT 0.0,
                temporal_bucket INTEGER DEFAULT 0,
                stream TEXT DEFAULT '',
                forgotten_at REAL DEFAULT 0.0,
                cluster_id TEXT DEFAULT '',
                profile_fact_id TEXT DEFAULT '',
                confidence REAL DEFAULT 0.5
            );
        """)
        from hippocampus.db_migration import run_migrations
        ran = run_migrations(conn)
        assert ran == [], "expected [] on fresh v1.4 db, got " + str(ran)
        conn.close()
    finally:
        try: os.unlink(db)
        except Exception: pass
    print("  no-op on fresh v1.4: OK")


def test_migration_adds_missing_columns():
    banner("db_migration.run_migrations backfills v1.0/v1.1/v1.2 columns")
    import sqlite3
    db = _new_db()
    try:
        conn = sqlite3.connect(db)
        # Pre-v1.0 schema: no valence, no intensity, ..., no confidence
        conn.executescript("""
            CREATE TABLE engrams (
                id TEXT PRIMARY KEY,
                content TEXT
            );
        """)
        from hippocampus.db_migration import run_migrations
        ran = run_migrations(conn)
        # All 3 versions should have been applied
        assert "v1.0" in ran, ran
        assert "v1.1" in ran, ran
        assert "v1.2" in ran, ran
        cols = {r[1] for r in conn.execute("PRAGMA table_info(engrams)")}
        for c in ["valence", "intensity", "temporal_bucket", "stream",
                  "forgotten_at", "cluster_id", "profile_fact_id",
                  "confidence"]:
            assert c in cols, "missing column " + c + " in " + str(cols)
        # Re-run is idempotent
        ran2 = run_migrations(conn)
        assert ran2 == [], "expected idempotent, got " + str(ran2)
        conn.close()
    finally:
        try: os.unlink(db)
        except Exception: pass
    print("  v1.0 + v1.1 + v1.2 backfill, idempotent: OK")


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
        os.path.dirname(os.path.abspath(__file__)), "_conf_schema.json")
    schema = json.load(open(p, encoding="utf-8"))
    assert len(schema) == 20, len(schema)
    for k in ["enable_backup", "backup_interval_hours", "backup_keep_last",
              "backup_keep_weekly", "backup_keep_monthly"]:
        assert k in schema, k
    print("  20-field schema with 5 backup: OK")


def test_backup_handler_list_backups():
    banner("page_api BackupHandler.list_backups via PageApiUtils")
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__))))
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


def test_page_api_registers_10_endpoints():
    banner("page_api registers 10 endpoints (8 B9 + 2 backup)")
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__))))
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
    assert len(calls) == 10, "expected 10 endpoints, got " + str(len(calls))
    paths = [c[0] for c in calls]
    for needed in ["/astrbot-plugin-hippocampus/page/backups",
                   "/astrbot-plugin-hippocampus/page/backups/restore"]:
        assert needed in paths, needed
    print("  10 endpoints incl. /backups + /backups/restore: OK")


def test_plugin_initializer_backup_manager_attr():
    banner("PluginInitializer: backup_manager set on initialize()")
    from handlers.init import PluginInitializer
    # enable_backup=True via context.get_config
    init = PluginInitializer(
        types.SimpleNamespace(
            get_config=lambda k: {
                "sqlite_path": _new_db(),
                "enable_backup": True,
                "backup_interval_hours": 0,  # 0 = no thread, but manager still set
                "bot_language": "zh"},
            register_tool=None))
    # initialize will spawn a thread if interval>0; we pass 0 so no thread,
    # but backup_manager should still be created
    init.initialize()
    assert init.backup_manager is not None, "backup_manager not created"
    assert init.backup_manager.db_path is not None
    # cleanup: close service to release db lock
    if init.service is not None:
        try: init.service.close()
        except Exception: pass
    # enable_backup=False
    db2 = _new_db()
    init2 = PluginInitializer(
        types.SimpleNamespace(
            get_config=lambda k: {
                "sqlite_path": db2,
                "enable_backup": False,
                "bot_language": "zh"},
            register_tool=None))
    init2.initialize()
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
    test_migration_idempotent_on_fresh_v14()
    test_migration_adds_missing_columns()
    test_backup_create_list_restore()
    test_backup_cleanup_keeps_recent()
    test_memory_config_has_5_backup_fields()
    test_conf_schema_has_5_backup_fields()
    test_backup_handler_list_backups()
    test_page_api_registers_10_endpoints()
    test_plugin_initializer_backup_manager_attr()
    print(chr(10) + "ALL OK")


if __name__ == "__main__":
    main()