#!/usr/bin/env python3
"""One-shot migration: store embeddings as float32 BLOBs and compact the DB.

WHY THIS EXISTS
---------------
Until v1.79.1 every embedding was stored as JSON text:
  - engrams.embedding_json   (~92 KB for a 4096-dim vector)
  - diary_chunks.embedding   (~92 KB per chunk)
On the live store that was 152 MB of JSON out of a 178 MB file, and every cold
start parsed all of it. v1.79.2 stores the same numbers as little-endian
float32 in `engrams.embedding_vec` / `diary_chunks.embedding_vec` (16 KB per
4096-dim vector), and the read path still accepts old JSON rows, so a database
that has not been migrated keeps working.

This script performs that conversion once and then VACUUMs the file. It does
not change a single vector value: it only re-encodes it and drops the text.

USAGE
-----
    python compact_embeddings.py                    # dry run, prints the plan
    python compact_embeddings.py --yes              # backup + convert + vacuum
    python compact_embeddings.py --yes --no-vacuum  # skip the final VACUUM
    python compact_embeddings.py --yes --db <path>

Stop the plugin (or AstrBot) before a full run: the conversion itself is
crash-safe and interruptible, but VACUUM needs exclusive access and a busy
database would just make it fail. Re-running is safe; converted rows are
skipped.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASTRBOT_ROOT = HERE.parent.parent.parent          # <root>/data/plugins/<plugin>
DEFAULT_DB = ASTRBOT_ROOT / "data" / "hippocampus.db"
CONFIG = ASTRBOT_ROOT / "data" / "config" / "astrbot_plugin_engram_core_config.json"

sys.path.insert(0, str(HERE))
from hippocampus.vecmath import pack_vec          # noqa: E402
from hippocampus.sqlite_util import ensure_columns  # noqa: E402


def _config_db() -> str:
    if not CONFIG.exists():
        return ""
    try:
        raw = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except Exception as exc:                                  # noqa: BLE001
        print(f"  ! could not read {CONFIG}: {exc!r}")
        return ""
    flat: dict = {}
    for key in ("provider_settings", "storage_settings", "memory_settings",
                "summary_settings", "backup_settings"):
        block = raw.get(key)
        if isinstance(block, dict):
            flat.update(block)
    flat.update({k: v for k, v in raw.items() if not isinstance(v, dict)})
    return str(flat.get("sqlite_path") or "")


def _resolve_db(cli_db: str | None) -> Path:
    if cli_db:
        return Path(cli_db).expanduser().resolve()
    cfg_db = _config_db()
    if cfg_db:
        p = Path(cfg_db)
        return p if p.is_absolute() else (ASTRBOT_ROOT / p).resolve()
    return DEFAULT_DB


def _backup(db: Path) -> Path:
    dest_dir = db.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{db.stem}-compact-{stamp}{db.suffix}"
    src = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=60)
    try:
        out = sqlite3.connect(str(dest), timeout=60)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    return dest


def _vector_rows(conn, table: str, text_col: str, blob_col: str):
    return conn.execute(
        f"SELECT id, {text_col} FROM {table} "
        f"WHERE LENGTH(COALESCE({text_col}, '')) > 4").fetchall()


def _convert(conn, table: str, text_col: str, blob_col: str) -> tuple[int, int, int]:
    """Rewrite JSON vectors as float32 blobs; returns (rows, bytes_before, bytes_after)."""
    rows = _vector_rows(conn, table, text_col, blob_col)
    done = 0
    before = after = 0
    for rid, raw in rows:
        before += len(raw or "")
        try:
            vec = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            print(f"  ! {table}.{rid}: unreadable {text_col}, left as-is")
            continue
        blob = pack_vec(vec) if isinstance(vec, list) else None
        if blob is None:
            print(f"  ! {table}.{rid}: empty vector, left as-is")
            continue
        conn.execute(
            f"UPDATE {table} SET {blob_col}=?, {text_col}='' WHERE id=?",
            (blob, rid))
        done += 1
        after += len(blob)
        if done % 200 == 0:
            conn.commit()
    conn.commit()
    return done, before, after


def _has_column(conn, table: str, col: str) -> bool:
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def _count(conn, sql: str, params=()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate embeddings to float32 BLOBs")
    ap.add_argument("--db", default=None, help="path to hippocampus.db")
    ap.add_argument("--yes", action="store_true", help="write (default: dry run)")
    ap.add_argument("--no-vacuum", action="store_true", help="skip the final VACUUM")
    args = ap.parse_args()

    db = _resolve_db(args.db)
    if not db.exists():
        print(f"database not found: {db}")
        return 2
    size_before = db.stat().st_size
    print(f"db:   {db}")
    print(f"size: {size_before / 1e6:.1f} MB")

    mode = "rw" if args.yes else "ro"
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode={mode}", uri=True, timeout=60)
    try:
        if args.yes:
            # Self-sufficient: the plugin may not have run the v1.79.2 code yet,
            # so make sure the blob columns exist before converting.
            added = ensure_columns(conn, "engrams", (("embedding_vec", "BLOB"),))
            added += ensure_columns(conn, "diary_chunks", (("embedding_vec", "BLOB"),))
            if added:
                conn.commit()
                print("schema: added " + ", ".join(added))
        has_e = _has_column(conn, "engrams", "embedding_vec")
        has_d = _has_column(conn, "diary_chunks", "embedding_vec")
        e_rows = _vector_rows(conn, "engrams", "embedding_json", "embedding_vec")
        d_rows = _vector_rows(conn, "diary_chunks", "embedding", "embedding_vec")
        e_bytes = sum(len(r[1] or "") for r in e_rows)
        d_bytes = sum(len(r[1] or "") for r in d_rows)
        e_done = (_count(conn, "SELECT COUNT(*) FROM engrams"
                         " WHERE embedding_vec IS NOT NULL") if has_e else 0)
        d_done = (_count(conn, "SELECT COUNT(*) FROM diary_chunks"
                         " WHERE embedding_vec IS NOT NULL") if has_d else 0)
        print("\nrows to convert:")
        print(f"  engrams.embedding_json   {len(e_rows):5d}  {e_bytes / 1e6:7.1f} MB"
              f"  (already blobs: {e_done})")
        print(f"  diary_chunks.embedding   {len(d_rows):5d}  {d_bytes / 1e6:7.1f} MB"
              f"  (already blobs: {d_done})")
        if not e_rows and not d_rows:
            print("\nnothing to convert: every vector is already a blob")
            return 0
        est = e_bytes + d_bytes - (len(e_rows) + len(d_rows)) * 4 * 4096
        print(f"\nestimated saving: {est / 1e6:.1f} MB"
              f" (float32 = 16 KB per 4096-dim vector)")

        if not args.yes:
            print("\ndry run; re-run with --yes to write")
            return 0

        print("\nmaking backup ...")
        dest = _backup(db)
        print(f"  backup: {dest}  ({dest.stat().st_size / 1e6:.1f} MB)")

        e_total = _count(conn, "SELECT COUNT(*) FROM engrams")
        d_total = _count(conn, "SELECT COUNT(*) FROM diary_chunks")
        print("\nconverting ...")
        t0 = time.time()
        e_n, e_b, e_a = _convert(conn, "engrams", "embedding_json", "embedding_vec")
        d_n, d_b, d_a = _convert(conn, "diary_chunks", "embedding", "embedding_vec")
        print(f"  engrams     {e_n} rows  {e_b / 1e6:.1f} MB -> {e_a / 1e6:.1f} MB")
        print(f"  diary_chunks {d_n} rows  {d_b / 1e6:.1f} MB -> {d_a / 1e6:.1f} MB")
        print(f"  {time.time() - t0:.1f}s")

        try:
            conn.execute("INSERT INTO engrams_fts(engrams_fts) VALUES('rebuild')")
            conn.commit()
            print("  fts: rebuilt")
        except sqlite3.Error as exc:
            print(f"  ! fts rebuild skipped: {exc!r}")
        try:
            conn.execute("INSERT INTO engrams_fts(engrams_fts, rank) VALUES('integrity-check', 1)")
            print("  fts: integrity-check ok")
        except sqlite3.Error as exc:
            print(f"  ! fts integrity-check skipped: {exc!r}")

        conn.execute("PRAGMA optimize")
        if not args.no_vacuum:
            print("\nvacuum ...")
            try:
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
                conn.execute("VACUUM")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
                print("  done")
            except sqlite3.Error as exc:
                print(f"  ! VACUUM failed ({exc!r}); stop the plugin and re-run "
                      "with --yes (conversion is already saved)")

        qc = conn.execute("PRAGMA quick_check").fetchone()[0]
        print(f"\nquick_check: {qc}")
        e_after = _count(conn, "SELECT COUNT(*) FROM engrams")
        d_after = _count(conn, "SELECT COUNT(*) FROM diary_chunks")
        e_vec = _count(conn, "SELECT COUNT(*) FROM engrams WHERE embedding_vec IS NOT NULL")
        d_vec = _count(conn, "SELECT COUNT(*) FROM diary_chunks WHERE embedding_vec IS NOT NULL")
        print(f"engrams {e_total} -> {e_after}, vectors {e_vec}")
        print(f"diary_chunks {d_total} -> {d_after}, vectors {d_vec}")
        if (e_after, d_after) != (e_total, d_total):
            print("! row counts changed; restore the backup")
            return 1
    finally:
        conn.close()

    size_after = db.stat().st_size
    print(f"\nsize: {size_before / 1e6:.1f} MB -> {size_after / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())