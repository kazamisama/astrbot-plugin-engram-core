"""Smoke v1.7: WAL pragma applied to store connections.

Verifies hippocampus.sqlite_util.apply_pragmas and that the real stores
end up in WAL mode on a file-backed database.
  - apply_pragmas sets journal_mode=wal on a file db
  - apply_pragmas never raises (memory db can't WAL -> swallowed)
  - HippocampalStore opens its db in WAL mode
"""
import sys, os, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus.sqlite_util import apply_pragmas


def banner(m):
    print("\n=== " + m + " ===")


def test_apply_pragmas_file_db():
    banner("apply_pragmas -> WAL on file db")
    d = tempfile.mkdtemp()
    path = os.path.join(d, "t.db")
    conn = sqlite3.connect(path)
    apply_pragmas(conn)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal", mode
    sync = conn.execute("PRAGMA synchronous").fetchone()[0]
    assert int(sync) == 1, sync  # NORMAL == 1
    conn.close()
    print("  journal_mode=wal, synchronous=NORMAL: OK")


def test_apply_pragmas_memory_db_safe():
    banner("apply_pragmas on :memory: never raises")
    conn = sqlite3.connect(":memory:")
    apply_pragmas(conn)  # memory db cannot be WAL; must not raise
    conn.close()
    print("  memory db handled without exception: OK")


def test_store_uses_wal():
    banner("HippocampalStore db ends up in WAL")
    from hippocampus.storage import HippocampalStore

    class _HashEmb:
        name = "hash"
        def embed(self, text):
            return [float(len(text) % 7)] * 8
        def get_dimension(self):
            return 8

    d = tempfile.mkdtemp()
    path = os.path.join(d, "hippo.db")
    store = HippocampalStore(path, _HashEmb())
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal", mode
    print("  HippocampalStore journal_mode=wal: OK")


if __name__ == "__main__":
    test_apply_pragmas_file_db()
    test_apply_pragmas_memory_db_safe()
    test_store_uses_wal()
    print("\nALL v1.7 WAL smoke tests passed.")