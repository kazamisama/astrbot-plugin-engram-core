"""Smoke v1.79.2: embeddings are float32 BLOBs, JSON rows still readable.

Storage format only -- the vectors themselves are unchanged. These checks
pin both halves of the contract:

1. New writes land in `embedding_vec` (engrams) / `diary_chunks.embedding_vec`
   and leave the legacy text columns empty.
2. Reads (get / vector index / diary chunk index / rank_chunks) decode blobs.
3. Rows still stored as JSON are read identically -- a database that has not
   run compact_embeddings.py keeps working.
4. compact_embeddings.py converts a legacy database, preserves every vector
   value and row count, and is idempotent.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hippocampus import MemoryConfig, MemoryService
from hippocampus.diary_store import DiaryChunk, DiaryStore
from hippocampus.types import Engram
from hippocampus.vecmath import decode_vec, pack_vec, unpack_vec

FAILS = []


def banner(msg):
    print("\n=== " + msg + " ===")


def check(cond, msg):
    if cond:
        print("  OK   " + msg)
    else:
        print("  FAIL " + msg)
        FAILS.append(msg)


def _svc(td, name="h.db"):
    cfg = MemoryConfig(sqlite_path=os.path.join(td, name),
                       embedding_name="hash", llm_name="rule")
    return MemoryService(cfg)


def _raw(cfg_path, sql, params=()):
    import sqlite3
    c = sqlite3.connect(cfg_path)
    c.row_factory = sqlite3.Row
    try:
        return c.execute(sql, params).fetchone()
    finally:
        c.close()


def test_blob_roundtrip():
    banner("engram writes: blob column, empty text, decoded reads")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        try:
            vec = [0.5, -0.25, 0.125, 0.75]
            e = Engram(content="blob roundtrip", embedding=vec,
                       embedding_model="m1")
            svc.store.upsert(e)
            row = _raw(svc.cfg.sqlite_path,
                       "SELECT embedding_json, embedding_vec FROM engrams WHERE id=?",
                       (e.id,))
            check(row["embedding_vec"] is not None, "embedding_vec is populated")
            check((row["embedding_json"] or "") == "", "embedding_json left empty")
            got = svc.store.get(e.id)
            check(got is not None and len(got.embedding) == 4,
                  "store.get decoded the blob")
            check(all(abs(a - b) < 1e-6 for a, b in zip(got.embedding, vec)),
                  "decoded values match float32")
            svc.store.warm_vector_index()
            hits = svc.store.vector_search(vec, 3)
            check(bool(hits) and hits[0][0].id == e.id,
                  "vector index ranks the row first")
            svc.store.update_embedding(e.id, [1.0, 0.0, 0.0, 0.0], "m2")
            row2 = _raw(svc.cfg.sqlite_path,
                        "SELECT embedding_json, embedding_vec, embedding_model"
                        " FROM engrams WHERE id=?", (e.id,))
            check(row2["embedding_vec"] is not None
                  and (row2["embedding_json"] or "") == "",
                  "update_embedding rewrites the blob, not JSON")
            check(row2["embedding_model"] == "m2", "model label updated")
            hits2 = svc.store.vector_search([1.0, 0.0, 0.0, 0.0], 3)
            check(hits2[0][0].id == e.id, "re-embedded vector is searchable")
        finally:
            svc.close()


def test_legacy_json_rows():
    banner("legacy rows: JSON-only embeddings are still read")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        try:
            vec = [0.1, 0.2, 0.3, 0.4]
            e = Engram(content="legacy json", embedding=vec)
            svc.store.upsert(e)
            # Simulate a pre-v1.79.2 row: JSON text, no blob.
            svc.store._conn.execute(
                "UPDATE engrams SET embedding_vec=NULL, embedding_json=? WHERE id=?",
                (json.dumps(vec), e.id))
            svc.store._conn.commit()
            got = svc.store.get(e.id)
            check(got is not None and len(got.embedding) == 4,
                  "from_row decodes legacy JSON")
            check(svc.store.count_missing_vectors() == 0,
                  "legacy row is not counted as missing")
            svc.store._vindex = None
            n = svc.store.warm_vector_index()
            check(n == 1, "vector index includes the JSON row (got %r)" % (n,))
            hits = svc.store.vector_search(vec, 2)
            check(bool(hits) and hits[0][0].id == e.id,
                  "legacy row is searchable")
            svc.store.update_embedding(e.id, vec, "m1")
            row = _raw(svc.cfg.sqlite_path,
                       "SELECT embedding_vec, embedding_json FROM engrams WHERE id=?",
                       (e.id,))
            check(row["embedding_vec"] is not None
                  and (row["embedding_json"] or "") == "",
                  "next write migrates the row to a blob")
        finally:
            svc.close()


def test_diary_chunks():
    banner("diary chunks: blob writes, decoded reads, legacy JSON")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        try:
            ds = svc.diary_store
            vec = [0.25, 0.5, 0.75, 0.125]
            ch = DiaryChunk(diary_id="d1", text="chunk one", embedding=vec,
                            embedding_model="m1")
            ds.add_chunks([ch])
            row = _raw(svc.cfg.sqlite_path,
                       "SELECT embedding, embedding_vec FROM diary_chunks WHERE id=?",
                       (ch.id,))
            check(row["embedding_vec"] is not None
                  and (row["embedding"] or "") == "",
                  "chunk embedding stored as a blob")
            chunks = ds.all_chunks()
            check(len(chunks) == 1 and len(chunks[0].embedding) == 4,
                  "all_chunks decodes the blob")
            ds._chunk_idx = None
            hits = ds.rank_chunks(vec, limit=5)
            check(len(hits) == 1 and hits[0][0] == ch.id,
                  "rank_chunks scores the cached chunk")
            check(ds.chunk_diary_ids() == {"d1"},
                  "chunk_diary_ids sees blob rows")
            # Legacy JSON chunk: text column only.
            ds._conn.execute(
                "UPDATE diary_chunks SET embedding_vec=NULL, embedding=? WHERE id=?",
                (json.dumps(vec), ch.id))
            ds._conn.commit()
            ds._chunk_idx = None
            hits2 = ds.rank_chunks(vec, limit=5)
            check(len(hits2) == 1 and hits2[0][0] == ch.id,
                  "legacy JSON chunk is still ranked")
            check(ds.chunk_diary_ids() == {"d1"},
                  "chunk_diary_ids sees JSON rows too")
        finally:
            svc.close()


def test_pack_helpers():
    banner("codec: pack/unpack/decode edge cases")
    check(pack_vec([]) is None, "empty vector packs to None")
    blob = pack_vec([1.0, -2.0])
    check(isinstance(blob, bytes) and len(blob) == 8, "float32 x2 = 8 bytes")
    check(unpack_vec(blob) == [1.0, -2.0], "unpack roundtrip")
    check(unpack_vec(b"abc") == [], "non-multiple-of-4 bytes rejected")
    check(decode_vec(None, None) == [], "empty decode")
    check(decode_vec(None, "[1,2,3]") == [1.0, 2.0, 3.0],
          "JSON fallback")
    check(decode_vec(blob, "[9,9]") == [1.0, -2.0],
          "blob wins over stale JSON")
    check(decode_vec(None, "not json") == [], "unreadable JSON gives []")


def test_migration_script():
    banner("compact_embeddings.py: converts, preserves values, idempotent")
    with tempfile.TemporaryDirectory() as td:
        svc = _svc(td)
        path = svc.cfg.sqlite_path
        try:
            vecs = {}
            for i in range(3):
                vec = [float(i + 1), 0.5, -1.0, 0.25]
                e = Engram(content="legacy %d" % i, embedding=vec)
                svc.store.upsert(e)
                vecs[e.id] = vec
            ch = DiaryChunk(diary_id="d1", text="legacy chunk",
                            embedding=[0.5, 0.5, 0.5, 0.5])
            svc.diary_store.add_chunks([ch])
            vecs[ch.id] = ch.embedding
            # Rewind both tables to the legacy encoding.
            for eid in [k for k in vecs if k != ch.id]:
                svc.store._conn.execute(
                    "UPDATE engrams SET embedding_vec=NULL, embedding_json=?"
                    " WHERE id=?", (json.dumps(vecs[eid]), eid))
            svc.store._conn.commit()
            svc.diary_store._conn.execute(
                "UPDATE diary_chunks SET embedding_vec=NULL, embedding=?"
                " WHERE id=?", (json.dumps(vecs[ch.id]), ch.id))
            svc.diary_store._conn.commit()
        finally:
            svc.close()

        def run(extra=()):
            return subprocess.run(
                [sys.executable, os.path.join(ROOT, "compact_embeddings.py"),
                 "--yes", "--no-vacuum", "--db", path] + list(extra),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", cwd=ROOT)

        proc = run()
        check(proc.returncode == 0,
              "migration exits 0 (%s)" % (proc.stderr[-200:],))
        c = sqlite3_connect(path)
        try:
            left = c.execute("SELECT COUNT(*) FROM engrams"
                             " WHERE LENGTH(COALESCE(embedding_json,'')) > 4").fetchone()[0]
            chars = c.execute("SELECT COUNT(*) FROM diary_chunks"
                              " WHERE LENGTH(COALESCE(embedding,'')) > 4").fetchone()[0]
            n_blob = c.execute("SELECT COUNT(*) FROM engrams"
                               " WHERE embedding_vec IS NOT NULL").fetchone()[0]
            d_blob = c.execute("SELECT COUNT(*) FROM diary_chunks"
                               " WHERE embedding_vec IS NOT NULL").fetchone()[0]
            rows = c.execute("SELECT id, embedding_vec FROM engrams").fetchall()
            chunk = c.execute("SELECT embedding_vec FROM diary_chunks"
                              " WHERE id=?", (ch.id,)).fetchone()[0]
        finally:
            c.close()
        check(left == 0 and chars == 0, "all JSON text converted")
        check(n_blob == 3 and d_blob == 1,
              "blob columns populated (%d/%d)" % (n_blob, d_blob))
        ok = True
        for rid, blob in rows:
            vec = unpack_vec(blob)
            want = vecs[rid]
            if len(vec) != len(want) or any(abs(a - b) > 1e-6
                                            for a, b in zip(vec, want)):
                ok = False
        check(ok, "engram vector values preserved")
        check(unpack_vec(chunk) == [0.5, 0.5, 0.5, 0.5],
              "diary vector preserved")
        proc2 = run()
        check(proc2.returncode == 0 and "nothing to convert" in proc2.stdout,
              "second run is a no-op")
        svc2 = _svc(td, "h.db")
        try:
            n = svc2.store.warm_vector_index()
            check(n == 3, "converted DB loads its vector index")
            hits = svc2.store.vector_search(vecs[list(vecs)[0]], 2)
            check(bool(hits), "converted DB is searchable")
        finally:
            svc2.close()


def sqlite3_connect(path):
    import sqlite3
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def main():
    test_pack_helpers()
    test_blob_roundtrip()
    test_legacy_json_rows()
    test_diary_chunks()
    test_migration_script()
    if FAILS:
        print("\nFAILED %d check(s):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        raise SystemExit(1)
    print("\nall v1.79.2 embedding-storage checks passed")


if __name__ == "__main__":
    main()