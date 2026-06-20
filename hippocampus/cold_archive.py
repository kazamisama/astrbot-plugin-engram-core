"""cold_archive: physical archive of cold-tier engrams (v1.14).

memori archives stale content to a compressed cold file and deletes it from
the hot DB (archiver.py `_write_cold`). We offer the same as an *explicit*,
opt-in operation (never automatic): cold-tier engrams are appended to a
gzip-compressed JSONL archive on disk, then deleted from the live DB so the
hot store stays lean. The archive is restore-friendly (full engram JSON).

This is destructive to the live row (the data moves to the archive file),
so it only runs when the operator invokes `/mem tier archive` or calls
service.archive_cold(). The recall-side cold *fallback* (tiering.py) is a
separate, non-destructive feature; archiving is the heavier physical step.
"""
from __future__ import annotations
import gzip
import io as _io
import json
import os
import time

ARCHIVE_FORMAT_VERSION = "1.0"


def _default_path(sqlite_path: str) -> str:
    base = os.path.dirname(os.path.abspath(sqlite_path or "."))
    return os.path.join(base, "engram_cold_archive.jsonl.gz")


class ColdArchiver:
    """Append cold engrams to a gzip JSONL file and evict them from the DB."""

    def __init__(self, store, cfg) -> None:
        self._store = store
        self._cfg = cfg

    def _archive_path(self) -> str:
        p = getattr(self._cfg, "cold_archive_path", "") or ""
        if p:
            return p
        return _default_path(getattr(self._cfg, "sqlite_path", "") or "")

    def archive_cold(self, *, min_age_days: float | None = None,
                     limit: int = 1_000_000) -> dict:
        """Move cold-tier engrams older than `min_age_days` to the archive
        file and delete them from the live DB. Returns a result dict:
        {archived, path, skipped_recent}. Never raises on per-row errors."""
        from .tiering import classify, COLD
        now = time.time()
        floor_days = (float(getattr(self._cfg, "cold_archive_min_age_days", 60.0))
                      if min_age_days is None else float(min_age_days))
        floor_secs = max(0.0, floor_days) * 86400.0
        path = self._archive_path()

        rows = self._store.all(limit=limit)
        to_archive = []
        skipped_recent = 0
        for e in rows:
            if classify(e, self._cfg, now) != COLD:
                continue
            ref = float(getattr(e, "last_accessed", 0.0) or 0.0) or \
                float(getattr(e, "created_at", 0.0) or 0.0)
            age = (now - ref) if ref > 0 else 0.0
            if age < floor_secs:
                skipped_recent += 1
                continue
            to_archive.append(e)

        if not to_archive:
            return {"archived": 0, "path": path, "skipped_recent": skipped_recent}

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        archived = 0
        # Append as gzip JSONL. Opening in "ab" keeps prior archives; each
        # call writes a self-describing gzip member, which gzip readers
        # concatenate transparently.
        try:
            with gzip.open(path, "ab") as gz:
                writer = _io.TextIOWrapper(gz, encoding="utf-8", newline="\n")
                try:
                    for e in to_archive:
                        rec = {"_v": ARCHIVE_FORMAT_VERSION,
                               "_archived_at": now,
                               "engram": json.loads(e.to_json())}
                        writer.write(json.dumps(rec, ensure_ascii=False) + "\n")
                finally:
                    writer.flush()
                    writer.detach()
        except Exception as ex:
            print("[hippocampus] cold archive write error: " + repr(ex))
            return {"archived": 0, "path": path, "skipped_recent": skipped_recent,
                    "error": repr(ex)}

        # Only delete rows that were successfully written.
        for e in to_archive:
            try:
                if self._store.delete(e.id):
                    archived += 1
            except Exception as ex:
                print("[hippocampus] cold archive delete error: " + repr(ex))

        return {"archived": archived, "path": path,
                "skipped_recent": skipped_recent}

    def count_archived(self) -> int:
        """Count records currently in the archive file (0 when absent)."""
        path = self._archive_path()
        if not os.path.exists(path):
            return 0
        n = 0
        try:
            with gzip.open(path, "rt", encoding="utf-8") as gz:
                for line in gz:
                    if line.strip():
                        n += 1
        except Exception as ex:
            print("[hippocampus] cold archive read error: " + repr(ex))
        return n
