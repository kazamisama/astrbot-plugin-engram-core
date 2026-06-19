from __future__ import annotations
from dataclasses import dataclass
from .types import Engram
from .config import MemoryConfig

@dataclass
class TimeCell:
    session_id: str
    started_at: float
    engrams: list[Engram]

class WorkingMemory:
    """Per-session buffer. TimeCellLayer shares the bucket by session_id."""
    def __init__(self, cfg: MemoryConfig) -> None:
        self._cfg = cfg
        self._cells: dict[str, TimeCell] = {}

    def add(self, e: Engram) -> None:
        cell = self._cells.get(e.session_id)
        if cell is None:
            cell = TimeCell(session_id=e.session_id, started_at=e.created_at, engrams=[])
            self._cells[e.session_id] = cell
        cell.engrams.append(e)
        if len(cell.engrams) > self._cfg.working_memory_capacity:
            cell.engrams = cell.engrams[-self._cfg.working_memory_capacity:]

    def drain(self, session_id: str) -> list[Engram]:
        cell = self._cells.get(session_id)
        if not cell: return []
        out = cell.engrams
        self._cells.pop(session_id, None)
        return out

    def snapshot(self, session_id: str) -> list[Engram]:
        cell = self._cells.get(session_id)
        return list(cell.engrams) if cell else []

    def candidates_for_separation(self, session_id: str) -> list[Engram]:
        return self.snapshot(session_id)