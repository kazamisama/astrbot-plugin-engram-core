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


# Platforms whose events are bot-internal (synthetic cron / scheduler
# replays), not real user messages. AstrBot CronMessageEvent sets
# platform_meta.name = "cron".
_SYNTHETIC_PLATFORMS = {"cron"}

# Marker strings injected by other plugins (e.g. proactive-reply) into a
# replayed wake event. These are prompts the bot sends to *itself*, never
# user-authored content, so they must not enter episodic memory.
_SYNTHETIC_MARKERS = (
    "[主动消息唤醒]",
    "[预约提醒唤醒]",
)


def _is_synthetic(meta: dict) -> bool:
    """True when the observation is a bot-internal / injected event.

    Engram listens on EventMessageType.ALL, so cron-replayed wake events
    from sibling plugins also reach this hook. They carry no real
    user/author, and recording them pollutes memory with the bot talking
    to itself. Filter by platform tag and by known wake-prompt markers.
    """
    platform = str(meta.get("platform") or "").strip().lower()
    if platform in _SYNTHETIC_PLATFORMS:
        return True
    content = meta.get("content") or ""
    for marker in _SYNTHETIC_MARKERS:
        if marker in content:
            return True
    return False


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
        if _is_synthetic(meta):
            # Bot-internal cron/wake event from another plugin - skip.
            return
        try:
            self.service.observe(**meta)
        except Exception as e:
            # Match prior main.py behavior: log to stdout, never raise
            # out of an event hook (would poison the AstrBot pipeline).
            print(f"[hippocampus] observe error: {e!r}")
