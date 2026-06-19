"""B10 BackupManager: raw .db file copies with retention policy.

Design (B10 Q1-Q3):
- Format: raw SQLite .db copy + .json sidecar with metadata (created_at,
  reason, hippocampus __version__, EXPORT_FORMAT_VERSION, schema hash,
  engram_count). This is the simplest, fastest, and most reliable
  backup; cross-version restore just runs run_migrations on the copy.
- Schedule: caller-driven (PluginInitializer background thread; interval
  comes from MemoryConfig.backup_interval_hours, 0 disables).
- Retention: keep_last + keep_weekly + keep_monthly, evaluated on every
  cleanup. weekly = 1 of every 7 most recent days past the keep_last
  window; monthly = 1 per 30-day bucket. Cheap heuristic, not LRU.
- All public methods are sync; SQLite connections are closed for the
  duration of the copy so the .db file on disk is in a consistent state
  (avoids WAL-half-written copies on Windows).
"""
from __future__ import annotations
import json
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass(frozen=True)
class BackupRecord:
    backup_id: str
    db_path: str
    sidecar_path: str
    created_at: float
    reason: str
    version: str
    engram_count: int
    byte_size: int


class BackupManager:
    """Single-DB backup manager. One instance per .db path.

    The manager is intentionally stateless w.r.t. the .db file contents
    - it just copies the file and runs retention. The store layer
    (HippocampalStore) is responsible for closing its connection during
    the copy via the optional `acquire` callback.
    """

    def __init__(self, db_path: str, backup_dir: str,
                 version_provider: Callable[[], str] | None = None) -> None:
        self._db_path = db_path
        self._backup_dir = backup_dir
        self._version_provider = version_provider or (lambda: "unknown")
        os.makedirs(backup_dir, exist_ok=True)
        self._lock = threading.RLock()

    # -- public API ----------------------------------------------------

    def create(self, reason: str = "auto",
               acquire: Callable[[], None] | None = None,
               release: Callable[[], None] | None = None) -> BackupRecord:
        """Copy db_path -> backup_dir/hippocampus-{ts}-{reason}.db
        plus a .json sidecar.

        `acquire` / `release` are called around the copy so callers can
        pause writers / close the connection. Both are optional; raw
        file copy on a quiescent .db is safe (no writes == no torn copy).
        """
        ts = time.time()
        ts_str = _fmt_ts(ts)
        safe_reason = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in (reason or "x"))[:32]
        bid = "hippocampus-" + ts_str + "-" + safe_reason
        dst = os.path.join(self._backup_dir, bid + ".db")
        sidecar = os.path.join(self._backup_dir, bid + ".json")
        if acquire is not None:
            acquire()
        try:
            with self._lock:
                if not os.path.exists(self._db_path):
                    raise FileNotFoundError(
                        "source db not found: " + self._db_path)
                shutil.copy2(self._db_path, dst)
                size = os.path.getsize(dst)
                engram_count = _safe_count_engrams(dst)
                rec = BackupRecord(
                    backup_id=bid, db_path=dst, sidecar_path=sidecar,
                    created_at=ts, reason=reason or "auto",
                    version=self._version_provider(),
                    engram_count=engram_count, byte_size=size)
                _write_sidecar(sidecar, rec)
                return rec
        finally:
            if release is not None:
                release()

    def list_backups(self) -> List[BackupRecord]:
        """Read sidecar json files, newest first. Skips orphan .db files."""
        with self._lock:
            out: List[BackupRecord] = []
            if not os.path.isdir(self._backup_dir):
                return out
            for name in os.listdir(self._backup_dir):
                if not name.endswith(".json"):
                    continue
                sp = os.path.join(self._backup_dir, name)
                bid = name[:-5]
                dbp = os.path.join(self._backup_dir, bid + ".db")
                if not os.path.exists(dbp):
                    continue
                try:
                    data = json.load(open(sp, "r", encoding="utf-8"))
                except Exception:
                    continue
                out.append(BackupRecord(
                    backup_id=bid, db_path=dbp, sidecar_path=sp,
                    created_at=float(data.get("created_at", 0.0)),
                    reason=str(data.get("reason", "")),
                    version=str(data.get("version", "")),
                    engram_count=int(data.get("engram_count", 0)),
                    byte_size=int(data.get("byte_size", 0))))
            out.sort(key=lambda r: r.created_at, reverse=True)
            return out

    def restore(self, backup_id: str,
                acquire: Callable[[], None] | None = None,
                release: Callable[[], None] | None = None) -> bool:
        """Overwrite db_path with the named backup. Returns True on
        success. Caller is responsible for re-running migrations if
        restoring to a different schema version.
        """
        with self._lock:
            src = os.path.join(self._backup_dir, backup_id + ".db")
            if not os.path.exists(src):
                return False
            if acquire is not None:
                acquire()
            try:
                shutil.copy2(src, self._db_path)
                return True
            finally:
                if release is not None:
                    release()

    def cleanup(self, *, keep_last: int = 7,
                keep_weekly: int = 1, keep_monthly: int = 1) -> int:
        """Apply retention policy. Returns number of backups removed.

        Selection rule (newest first):
          - always keep the first keep_last entries (most recent)
          - then keep one per ~7-day bucket among the older ones, up to
            keep_weekly buckets
          - then keep one per ~30-day bucket, up to keep_monthly buckets
        """
        if keep_last < 0 or keep_weekly < 0 or keep_monthly < 0:
            raise ValueError("retention counts must be >= 0")
        recs = self.list_backups()
        if not recs:
            return 0
        keep_ids: set = set()
        idx = 0
        # Phase 1: newest keep_last
        for r in recs[:keep_last]:
            keep_ids.add(r.backup_id)
        idx = max(keep_last, 0)
        # Phase 2: weekly buckets over remaining
        if keep_weekly > 0:
            bucket_now = _week_bucket(recs[0].created_at)
            count = 0
            for r in recs[idx:]:
                if count >= keep_weekly:
                    break
                if _week_bucket(r.created_at) != bucket_now:
                    keep_ids.add(r.backup_id)
                    bucket_now = _week_bucket(r.created_at)
                    count += 1
            idx += count
        # Phase 3: monthly buckets
        if keep_monthly > 0:
            bucket_now = _month_bucket(recs[0].created_at)
            count = 0
            for r in recs[idx:]:
                if count >= keep_monthly:
                    break
                if _month_bucket(r.created_at) != bucket_now:
                    keep_ids.add(r.backup_id)
                    bucket_now = _month_bucket(r.created_at)
                    count += 1
        # Delete the rest
        removed = 0
        for r in recs:
            if r.backup_id in keep_ids:
                continue
            try:
                os.unlink(r.db_path)
            except OSError:
                pass
            try:
                os.unlink(r.sidecar_path)
            except OSError:
                pass
            removed += 1
        return removed

    # -- convenience ---------------------------------------------------

    @property
    def backup_dir(self) -> str:
        return self._backup_dir

    @property
    def db_path(self) -> str:
        return self._db_path


# ---- helpers --------------------------------------------------------------


def _fmt_ts(ts: float) -> str:
    lt = time.localtime(ts)
    return time.strftime("%Y%m%d-%H%M%S", lt)


def _safe_count_engrams(db_file: str) -> int:
    """Best-effort engram count from a copy. Opens read-only, never raises."""
    try:
        conn = sqlite3.connect("file:" + db_file + "?mode=ro", uri=True)
        try:
            cur = conn.execute("SELECT COUNT(*) FROM engrams")
            return int(cur.fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def _write_sidecar(path: str, rec: BackupRecord) -> None:
    payload = {
        "backup_id": rec.backup_id,
        "created_at": rec.created_at,
        "reason": rec.reason,
        "version": rec.version,
        "engram_count": rec.engram_count,
        "byte_size": rec.byte_size,
        "source_db": os.path.basename(rec.db_path) and "hippocampus.db",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _week_bucket(ts: float) -> int:
    return int(ts // (7 * 24 * 3600))


def _month_bucket(ts: float) -> int:
    return int(ts // (30 * 24 * 3600))