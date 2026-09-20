#!/usr/bin/env python3
"""One-shot repair for memory strength destroyed by the compounding-decay bug.

WHY THIS EXISTS
---------------
Until v1.76.16 ``HippocampalStore.decay_pass`` decayed every engram by

    strength *= exp(-(now - max(last_accessed, created_at)) / tau)

on *every* maintenance sweep. The anchor was never advanced by the decay
itself, and the loop runs every ``memory_decay_interval_seconds`` (1800s by
default), so a memory anchored N sweeps back lost ``exp(-D*N**2/2/tau)``
instead of ``exp(-D*N/tau)`` -- one day of real age cost it ``exp(-48*age/tau)``.
Strength collapsed to 0.0 within ~2-3 days regardless of importance, every row
fell below ``tier_cold_strength_floor`` (0.1) and was classified ``cold``, and
because cold was excluded from normal recall the memory was never touched
again -- no ``touch()``, so the anchor never advanced and the row stayed at 0.0
permanently.

On the affected live store that left ``avg strength = 0.0151`` with 391/398
engrams below the floor: effectively all memory older than a couple of days
was unreachable.

The code fix stops the bleeding. This script undoes the damage: it rewrites the
annihilated strengths to the value the correct Ebbinghaus envelope would have
produced from the same anchor, so the rows re-enter normal recall.

ORDERING MATTERS
----------------
Run this AFTER the plugin has been (re)started with the v1.76.16 code. The
first decay sweep of a store with no recorded sweep time is still age-based
(that is what makes a brand-new DB behave), so back-filling before the fixed
code is live would just be annihilated again. This script therefore also writes
``hippo_meta['decay:last_pass_at'] = now`` so the next sweep is incremental.

USAGE
-----
    python repair_decay_strength.py                 # dry run, prints the plan
    python repair_decay_strength.py --yes           # make a backup, then write
    python repair_decay_strength.py --yes --db <path>

Always makes a consistent backup (via the sqlite3 backup API) before writing.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASTRBOT_ROOT = HERE.parent.parent.parent          # <root>/data/plugins/<plugin>
DEFAULT_DB = ASTRBOT_ROOT / "data" / "hippocampus.db"
CONFIG = ASTRBOT_ROOT / "data" / "config" / "astrbot_plugin_engram_core_config.json"

# Mirrors MemoryConfig defaults; overridden by the plugin config when present.
DEFAULTS = {
    "decay_tau_base": 60 * 60 * 24 * 7.0,
    "tier_cold_strength_floor": 0.1,
}
IMPORTANCE_MODULATOR = 4.0
STRENGTH_FLOOR = 0.05          # never leave a repaired row at exactly 0.0
META_KEY = "decay:last_pass_at"


def _load_config() -> tuple[float, float, str]:
    """Return (tau_base, cold_floor, sqlite_path) honouring the plugin config."""
    tau = DEFAULTS["decay_tau_base"]
    floor = DEFAULTS["tier_cold_strength_floor"]
    db = ""
    if CONFIG.exists():
        try:
            raw = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
            flat: dict = {}
            for key in ("provider_settings", "storage_settings",
                        "memory_settings", "summary_settings",
                        "backup_settings"):
                block = raw.get(key)
                if isinstance(block, dict):
                    flat.update(block)
            flat.update({k: v for k, v in raw.items() if not isinstance(v, dict)})
            tau = float(flat.get("decay_tau_base", tau) or tau)
            floor = float(flat.get("tier_cold_strength_floor", floor) or floor)
            db = str(flat.get("sqlite_path") or "")
        except Exception as ex:                                  # noqa: BLE001
            print(f"  ! could not read {CONFIG}: {ex!r}")
    return tau, floor, db


def _resolve_db(cli_db: str | None, cfg_db: str) -> Path:
    if cli_db:
        return Path(cli_db).expanduser().resolve()
    if cfg_db:
        p = Path(cfg_db)
        return p if p.is_absolute() else (ASTRBOT_ROOT / p).resolve()
    return DEFAULT_DB


def _code_is_fixed() -> bool:
    """True when the on-disk storage.py carries the v1.76.16 decay fix."""
    try:
        src = (HERE / "hippocampus" / "storage.py").read_text(encoding="utf-8")
    except OSError:
        return False
    return "_DECAY_PASS_META_KEY" in src and "MIN(?, MAX(0.0, ? - " in src


def _backup(db: Path) -> Path:
    dest_dir = db.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{db.stem}-repair-{stamp}{db.suffix}"
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="path to hippocampus.db (default: from plugin config)")
    ap.add_argument("--yes", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--dry-run", action="store_true", help="explicit dry run (default)")
    args = ap.parse_args()

    tau_base, cold_floor, cfg_db = _load_config()
    db = _resolve_db(args.db, cfg_db)
    write = bool(args.yes) and not args.dry_run

    print("=" * 74)
    print("hippocampus strength repair (compounding-decay bug)")
    print("=" * 74)
    print(f"  astrbot root : {ASTRBOT_ROOT}")
    print(f"  database     : {db}")
    print(f"  tau_base     : {tau_base / 86400:.2f} d   cold floor: {cold_floor}")
    print(f"  mode         : {'WRITE' if write else 'DRY RUN'}")

    if not db.exists():
        print(f"\n  ! database not found: {db}")
        print("    pass --db <path> if it lives elsewhere.")
        return 2

    if not _code_is_fixed():
        print("\n  ! hippocampus/storage.py does NOT contain the v1.76.16 decay fix.")
        print("    Repairing now would be undone by the next sweep. Aborting.")
        return 3

    conn = sqlite3.connect(str(db), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) FROM engrams").fetchone()[0]
        if total == 0:
            print(f"\n  ! {db} has 0 engrams -- this is not the live store. Aborting.")
            return 4
        print(f"  engrams      : {total}")

        prev = conn.execute(
            "SELECT value FROM hippo_meta WHERE key=?", (META_KEY,)).fetchone()
        print(f"  {META_KEY}: {prev['value'] if prev else '<unset>'}"
              "   (unset => next sweep would be age-based, which is why this")
        print("                              script records the sweep time)")

        rows = conn.execute(
            """
            SELECT id, importance, created_at, last_accessed, strength
              FROM engrams
             WHERE COALESCE(forgotten_at, 0.0) = 0.0
               AND COALESCE(strength, 0.0) < ?
            """, (cold_floor,)).fetchall()

        before = conn.execute(
            "SELECT MIN(strength) mn, MAX(strength) mx, AVG(strength) av FROM engrams"
        ).fetchone()
        print(f"\n  before: min={before['mn']:.4f} max={before['mx']:.4f} avg={before['av']:.4f}")
        print(f"  rows to repair (strength < {cold_floor}): {len(rows)}")

        if not rows:
            print("\n  Nothing to repair.")
            return 0

        now = time.time()
        updates: list[tuple[float, str]] = []
        buckets = {"0.0-0.05": 0, "0.05-0.10": 0, "0.10-0.20": 0, ">=0.20": 0}
        for r in rows:
            anchor = max(float(r["last_accessed"] or 0.0),
                         float(r["created_at"] or 0.0))
            if anchor <= 0.0:
                new = STRENGTH_FLOOR
            else:
                tau = max(1.0, tau_base * (1.0 + IMPORTANCE_MODULATOR *
                                           float(r["importance"] or 0.0)))
                age = max(0.0, now - anchor)
                new = max(STRENGTH_FLOOR, pow(2.718281828459045, -age / tau))
            updates.append((new, r["id"]))
            if new < 0.05:
                buckets["0.0-0.05"] += 1
            elif new < 0.10:
                buckets["0.05-0.10"] += 1
            elif new < 0.20:
                buckets["0.10-0.20"] += 1
            else:
                buckets[">=0.20"] += 1

        print("  repaired strength distribution: " + ", ".join(
            f"{k}={v}" for k, v in buckets.items()))
        print(f"  sample (first 5): " + ", ".join(
            f"{r['id'][:8]} {r['strength']:.4f} -> {u[0]:.4f}"
            for r, u in list(zip(rows, updates))[:5]))

        if not write:
            print("\n  DRY RUN -- nothing written. Re-run with --yes to apply.")
            return 0

        print("\n  making backup ...")
        dest = _backup(db)
        print(f"  backup: {dest}  ({dest.stat().st_size / 1e6:.1f} MB)")

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany("UPDATE engrams SET strength=? WHERE id=?", updates)
            conn.execute(
                "INSERT INTO hippo_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (META_KEY, repr(now)))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

        after = conn.execute(
            "SELECT MIN(strength) mn, MAX(strength) mx, AVG(strength) av FROM engrams"
        ).fetchone()
        below = conn.execute(
            "SELECT COUNT(*) FROM engrams WHERE COALESCE(forgotten_at,0.0)=0.0 "
            "AND strength < ?", (cold_floor,)).fetchone()[0]
        print(f"\n  after : min={after['mn']:.4f} max={after['mx']:.4f} avg={after['av']:.4f}")
        print(f"  still below cold floor: {below} / {total}")
        print(f"  wrote {META_KEY} = {now}")
        print("\n  Done. Tiers refresh on the plugin's next startup/reclassify sweep.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
