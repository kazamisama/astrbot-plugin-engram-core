from __future__ import annotations
import threading
import time
from dataclasses import dataclass
from .types import Engram
from .config import MemoryConfig

@dataclass
class TimeCell:
    session_id: str
    started_at: float
    last_seen: float
    engrams: list[Engram]

class WorkingMemory:
    """Per-session buffer keyed by session_id, with channel_id aliases.

    Group messages carry a session_id (AstrBot unified_msg_origin) that
    differs from channel_id (the group id). Recall used to look up only
    channel_id and therefore missed every group-session cell. The cell is
    now indexed under both keys and drain() removes every alias atomically.
    """
    def __init__(self, cfg: MemoryConfig) -> None:
        self._cfg = cfg
        self._cells: dict[str, TimeCell] = {}
        self._lock = threading.RLock()

    def _index_aliases(self, cell: TimeCell, session_id: str,
                       channel_id: str) -> None:
        if session_id:
            self._cells[session_id] = cell
        if channel_id and channel_id != session_id:
            self._cells[channel_id] = cell

    def add(self, e: Engram) -> None:
        with self._lock:
            self._add(e)

    def _add(self, e: Engram) -> None:
        now = time.time()
        cell = self._cells.get(e.session_id)
        if cell is None:
            cell = TimeCell(session_id=e.session_id, started_at=e.created_at,
                            last_seen=now, engrams=[])
        else:
            cell.last_seen = now
        self._index_aliases(cell, e.session_id, e.channel_id)
        cell.engrams.append(e)
        if len(cell.engrams) > self._cfg.working_memory_capacity:
            cell.engrams = cell.engrams[-self._cfg.working_memory_capacity:]
        self._evict_excess(now)

    def drain(self, session_id: str) -> list[Engram]:
        with self._lock:
            return self._drain(session_id)

    def _drain(self, session_id: str) -> list[Engram]:
        cell = self._cells.get(session_id)
        if not cell:
            return []
        out = cell.engrams
        for key, val in list(self._cells.items()):
            if val is cell:
                self._cells.pop(key, None)
        cell.engrams = []
        return out

    def evict_idle(self, now: float | None = None) -> int:
        """Drop cells that have not been touched for the configured idle TTL."""
        now = time.time() if now is None else now
        idle = float(getattr(self._cfg, "working_memory_idle_seconds", 86400.0) or 0.0)
        if idle <= 0:
            return 0
        with self._lock:
            idle_cells = {
                id(cell) for cell in self._cells.values()
                if now - cell.last_seen >= idle
            }
            for key, cell in list(self._cells.items()):
                if id(cell) in idle_cells:
                    self._cells.pop(key, None)
            return len(idle_cells)

    def _evict_excess(self, now: float) -> None:
        max_cells = int(getattr(self._cfg, "working_memory_max_cells", 512) or 512)
        if max_cells <= 0:
            return
        # Unique cells (aliases point at the same object).
        cells: dict[int, TimeCell] = {}
        for cell in self._cells.values():
            cells[id(cell)] = cell
        if len(cells) <= max_cells:
            return
        oldest = sorted(cells.values(), key=lambda c: c.last_seen)
        remove = {id(c) for c in oldest[: len(cells) - max_cells]}
        for key, cell in list(self._cells.items()):
            if id(cell) in remove:
                self._cells.pop(key, None)

    def snapshot(self, key: str) -> list[Engram]:
        with self._lock:
            cell = self._cells.get(key)
            if cell is None:
                return []
            cell.last_seen = time.time()
            return list(cell.engrams)

    def candidates_for_separation(self, session_id: str) -> list[Engram]:
        return self.snapshot(session_id)
