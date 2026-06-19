"""ObserveHandler: group/PM message capture.

Split from main.py at v1.4.x B6. Owns the @event_message_type hook.
Business logic only - the @filter.event_message_type decorator must
stay on HippocampusStar.observe_message() in main.py (AstrBot scans
Star subclasses for decorators). This class is constructed in
__init__ and invoked by the thin wrapper in main.py.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from ..format import _extract
if TYPE_CHECKING:
    from hippocampus import MemoryService


class ObserveHandler:
    """Capture every inbound message and feed it to MemoryService.observe()."""

    def __init__(self, service: "MemoryService | None") -> None:
        self.service = service

    async def handle_message(self, event) -> None:
        if self.service is None:
            return
        meta = _extract(event)
        if not meta["content"]:
            return
        try:
            self.service.observe(**meta)
        except Exception as e:
            # Match prior main.py behavior: log to stdout, never raise
            # out of an event hook (would poison the AstrBot pipeline).
            print(f"[hippocampus] observe error: {e!r}")
