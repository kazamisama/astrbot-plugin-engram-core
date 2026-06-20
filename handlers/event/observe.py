"""ObserveHandler: group/PM message capture.

Split from main.py at v1.4.x B6. Owns the @event_message_type hook.
Business logic only - the @filter.event_message_type decorator must
stay on HippocampusStar.observe_message() in main.py (AstrBot scans
Star subclasses for decorators). This class is constructed in
__init__ and invoked by the thin wrapper in main.py.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from ..format import _extract, _resolve_group_name
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
        self._aggregator = None
        self._conv_buffer = None
        self._summarizer = None

    def _get_aggregator(self):
        """Lazily build a SessionAggregator bound to this service.
        The sink forwards merged meta dicts straight to observe()."""
        if self._aggregator is None:
            from hippocampus.session_buffer import SessionAggregator
            self._aggregator = SessionAggregator(
                self.service.cfg,
                lambda meta: self.service.observe(**meta))
        return self._aggregator

    def _get_summarizer(self):
        if self._summarizer is None:
            from hippocampus.summarizer import ConversationSummarizer
            svc = self.service
            def _persona(rec):
                try:
                    if not getattr(svc.cfg, "persona_inject_enabled", False):
                        # still allow persona prefill independent of inject flag
                        pass
                    aid = rec.peer_actor_id or (rec.participants(include_bot=False) or [""])[0]
                    if not aid or not hasattr(svc, "get_persona"):
                        return None
                    p = svc.get_persona(aid)
                    return (getattr(p, "summary", "") or "").strip() or None
                except Exception:
                    return None
            self._summarizer = ConversationSummarizer(
                svc.cfg, llm=getattr(svc, "llm", None), persona_provider=_persona)
        else:
            # keep summarizer LLM in sync with any runtime switch
            try:
                self._summarizer.set_llm(self.service.llm)
            except Exception:
                pass
        return self._summarizer

    def _get_conv_buffer(self):
        """Per-channel conversation buffer; sink summarizes + stores one engram."""
        if self._conv_buffer is None:
            from hippocampus.conversation_buffer import ConversationBuffer

            def _sink(rec):
                try:
                    summ = self._get_summarizer().summarize(rec)
                    identity = {
                        "session_id": rec.session_id,
                        "actor_id": rec.peer_actor_id or "",
                        "platform": rec.platform,
                        "channel_id": rec.channel_id,
                        "chat_type": rec.chat_type,
                        "group_id": rec.group_id,
                        "group_name": rec.group_name,
                        "peer_actor_id": rec.peer_actor_id,
                        "peer_name": rec.peer_name,
                        "memory_type": "episodic",
                    }
                    self.service.store_summary(summ, identity)
                except Exception as ex:
                    print("[hippocampus] conv summary sink error: " + repr(ex))

            self._conv_buffer = ConversationBuffer(self.service.cfg, _sink)
        return self._conv_buffer

    async def handle_message(self, event) -> None:
        if self.service is None:
            return
        meta = _extract(event)
        if not meta["content"]:
            return
        if _is_synthetic(meta):
            # Bot-internal cron/wake event from another plugin - skip.
            return
        # v1.17 B-1: resolve group name (async, best-effort) for stamps.
        if meta.get("chat_type") == "group" and not meta.get("group_name"):
            try:
                meta["group_name"] = await _resolve_group_name(event)
            except Exception:
                meta["group_name"] = ""
        cfg = getattr(self.service, "cfg", None)
        summary_mode = bool(cfg is not None and getattr(
            cfg, "summary_mode_enabled", False))
        debug_ingest = bool(cfg is not None and getattr(
            cfg, "per_message_ingest_debug", False))
        # v1.20 B-3: cache every inbound line (incl. for diary) before any
        # summary routing, so the daily diary sees the full transcript.
        try:
            self.service.cache_daily_line(meta)
        except Exception as ce:
            print(f"[hippocampus] daily cache error: {ce!r}")
        try:
            if summary_mode:
                # Conversation-level summarization owns ingest. Per-message
                # ingest only happens in the debug fallback below.
                self._get_conv_buffer().feed(meta)
            if debug_ingest or not summary_mode:
                self._ingest_per_message(meta, cfg)
        except Exception as e:
            # Match prior main.py behavior: log to stdout, never raise
            # out of an event hook (would poison the AstrBot pipeline).
            print(f"[hippocampus] observe error: {e!r}")

    def _ingest_per_message(self, meta: dict, cfg) -> None:
        """Legacy one-engram-per-message path (default off; debug only when
        summary mode is on)."""
        # session_buffer.observe expects only the core fields.
        core = {k: meta[k] for k in (
            "session_id", "actor_id", "platform", "channel_id", "content")
            if k in meta}
        if cfg is not None and getattr(cfg, "session_aggregate_enabled", False):
            self._get_aggregator().feed(core)
        else:
            self.service.observe(**core)

    async def handle_bot_message(self, event, text: str) -> None:
        """Feed the bot's own reply into the conversation buffer (and the
        daily cache later in B-3) so summaries include the bot's turns."""
        if self.service is None:
            return
        body = (text or "").strip()
        if not body:
            return
        cfg = getattr(self.service, "cfg", None)
        summary_on = bool(cfg is not None and getattr(cfg, "summary_mode_enabled", False))
        diary_on = bool(cfg is not None and getattr(cfg, "diary_enabled", False))
        if not (summary_on or diary_on):
            return
        try:
            meta = _extract(event)
        except Exception:
            return
        meta["content"] = body
        meta["is_bot"] = True
        meta["actor_id"] = "bot"
        meta["speaker"] = "bot"
        if meta.get("chat_type") == "group" and not meta.get("group_name"):
            try:
                meta["group_name"] = await _resolve_group_name(event)
            except Exception:
                meta["group_name"] = ""
        # v1.20 B-3: cache bot's own line for the daily diary.
        try:
            self.service.cache_daily_line(meta)
        except Exception as ce:
            print(f"[hippocampus] bot daily cache error: {ce!r}")
        if not summary_on:
            return
        try:
            self._get_conv_buffer().feed(meta)
        except Exception as e:
            print(f"[hippocampus] bot observe error: {e!r}")
