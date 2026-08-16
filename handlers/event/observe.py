"""ObserveHandler: group/PM message capture.

Split from main.py at v1.4.x B6. Owns the @event_message_type hook.
Business logic only - the @filter.event_message_type decorator must
stay on HippocampusStar.observe_message() in main.py (AstrBot scans
Star subclasses for decorators). This class is constructed in
__init__ and invoked by the thin wrapper in main.py.
"""
from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING
from ..format import _extract, _resolve_group_name, _bot_actor_id, _resolve_bot_name
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
        self._ingest_lock = None

    def _get_ingest_lock(self):
        """Serialize ingest workers so message order is preserved even
        though the blocking SQLite/LLM work runs off the event loop."""
        if self._ingest_lock is None:
            self._ingest_lock = asyncio.Lock()
        return self._ingest_lock

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
                # FIX (v1.56): delegate to the shared _build_persona_provider
                # so the source of truth is the service (encoder + diary
                # writer also go through it). rec carries actor info we
                # extract here.
                try:
                    aid = (rec.peer_actor_id
                           or (rec.participants(include_bot=False) or [""])[0])
                except Exception:
                    aid = ""
                if not aid:
                    return None
                return svc._build_persona_provider()(aid, getattr(rec, "channel_id", ""))
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
                        "persona_id": getattr(rec, "persona_id", "") or "",
                        "scope_id": getattr(rec, "scope_id", "") or "",
                        "chat_type": rec.chat_type,
                        "group_id": rec.group_id,
                        "group_name": rec.group_name,
                        "peer_actor_id": rec.peer_actor_id,
                        "peer_name": rec.peer_name,
                        "memory_type": "episodic",
                        "source_lines": [{
                            "actor_id": ln.actor_id,
                            "speaker": ln.speaker,
                            "content": ln.content,
                            "ts": ln.ts,
                            "is_bot": bool(ln.is_bot),
                        } for ln in getattr(rec, "lines", [])],
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
        # v1.76.4 (M5): the summary/LLM/sqlite path is synchronous and can
        # block the AstrBot event loop for seconds on a flush. Run it on a
        # worker thread, serialized per ObserveHandler so channel message
        # order is preserved.
        async with self._get_ingest_lock():
            await asyncio.to_thread(self._process_inbound_meta, meta, cfg)

    def _process_inbound_meta(self, meta: dict, cfg) -> None:
        """Blocking part of handle_message(); never called concurrently."""
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
        # session_buffer.observe expects only the core fields. FIX
        # (v1.56): also pass channel_label + chat_type so the per-
        # message LLM extractor can build channel context.
        core = {k: meta[k] for k in (
            "session_id", "actor_id", "platform", "channel_id", "content",
            "persona_id", "scope_id", "channel_label", "chat_type")
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
        bot_aid = _bot_actor_id(event)
        meta["actor_id"] = bot_aid
        try:
            meta["speaker"] = await _resolve_bot_name(event, bot_aid)
        except Exception:
            meta["speaker"] = bot_aid
        if meta.get("chat_type") == "group" and not meta.get("group_name"):
            try:
                meta["group_name"] = await _resolve_group_name(event)
            except Exception:
                meta["group_name"] = ""
        async with self._get_ingest_lock():
            await asyncio.to_thread(self._process_bot_meta, meta, cfg, summary_on)

    def _process_bot_meta(self, meta: dict, cfg, summary_on: bool) -> None:
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

    async def handle_poke(self, event) -> None:
        """Capture a QQ poke notice (litepoke-style) as one named line so the
        real actor's name reaches the summary/diary instead of being lost.

        Poke is a `notice` event (no text body), so it never flows through
        handle_message (which drops empty content). We synthesize a line like
        "<sender> \u6233\u4e86\u6233 <target>" and route it through the same
        daily-cache + conversation-buffer path used for ordinary messages."""
        if self.service is None:
            return
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if not isinstance(raw, dict):
            return
        if raw.get("post_type") != "notice":
            return
        if raw.get("notice_type") != "notify" or raw.get("sub_type") != "poke":
            return
        self_id = str(raw.get("self_id", "") or "")
        sender_id = str(raw.get("user_id", "") or "")
        target_id = str(raw.get("target_id", "") or "")
        group_id = str(raw.get("group_id", "") or "")
        if not sender_id or not target_id:
            return
        # Resolve display names. Sender via event getter; bot side via login info.
        sender_name = ""
        try:
            getter = getattr(event, "get_sender_name", None)
            if callable(getter):
                sender_name = (getter() or "").strip()
        except Exception:
            sender_name = ""
        if not sender_name:
            sender_name = sender_id
        target_name = await self._resolve_poke_target_name(
            event, target_id, self_id, group_id)
        verb = "\u6233\u4e86\u6233"  # "poked"
        content = sender_name + " " + verb + " " + target_name
        cfg = getattr(self.service, "cfg", None)
        chat_type = "group" if group_id else "private"
        # FIX (v1.67): mirror _extract() and read persona_id from event extra.
        # Without this, poke lines land in daily_messages with persona_id=""
        # which makes channels_with_lines() return (channel, "") as a separate
        # diary group, causing two diaries per day: one with the persona
        # (containing all normal messages) and one empty/no-persona
        # (containing only poke history). Same root cause as the early
        # v1.36 persona-scoping rollout, but pokes were missed at the time.
        persona_id = ""
        scope_id = ""
        try:
            ge = getattr(event, "get_extra", None)
            if callable(ge):
                persona_id = ge("hippo_persona_id") or ""
                scope_id = ge("hippo_scope_id") or ""
        except Exception:
            persona_id = ""
            scope_id = ""
        meta = {
            "session_id": getattr(event, "unified_msg_origin", "") or "",
            "actor_id": sender_id,
            "platform": _call_name(event),
            "channel_id": group_id or (getattr(event, "unified_msg_origin", "") or "default"),
            "content": content,
            "chat_type": chat_type,
            "persona_id": persona_id,
            "scope_id": scope_id,
            "speaker": sender_name,
            "group_id": group_id,
            "group_name": "",
            "is_bot": bool(self_id and sender_id == self_id),
        }
        if chat_type == "private":
            meta["peer_actor_id"] = sender_id
            meta["peer_name"] = sender_name
        if chat_type == "group" and not meta["group_name"]:
            try:
                meta["group_name"] = await _resolve_group_name(event)
            except Exception:
                meta["group_name"] = ""
        summary_on = bool(cfg is not None and getattr(cfg, "summary_mode_enabled", False))
        async with self._get_ingest_lock():
            await asyncio.to_thread(self._process_poke_meta, meta, summary_on)

    def _process_poke_meta(self, meta: dict, summary_on: bool) -> None:
        try:
            self.service.cache_daily_line(meta)
        except Exception as ce:
            print(f"[hippocampus] poke daily cache error: {ce!r}")
        if summary_on:
            try:
                self._get_conv_buffer().feed(meta)
            except Exception as e:
                print(f"[hippocampus] poke observe error: {e!r}")

    async def _resolve_poke_target_name(self, event, target_id, self_id, group_id):
        """Best-effort display name for the poke target. Bot self -> bot
        nickname; group member -> get_group_member_info; else the raw id."""
        if self_id and target_id == self_id:
            try:
                return await _resolve_bot_name(event, target_id)
            except Exception:
                return target_id
        try:
            bot = getattr(event, "bot", None)
            if bot is not None and group_id and hasattr(bot, "call_action"):
                info = await bot.call_action(
                    "get_group_member_info",
                    group_id=int(group_id), user_id=int(target_id), no_cache=False)
                if isinstance(info, dict):
                    nm = (str(info.get("card") or "").strip()
                          or str(info.get("nickname") or "").strip())
                    if nm:
                        return nm
        except Exception:
            pass
        return target_id


def _call_name(event):
    try:
        getter = getattr(event, "get_platform_name", None)
        if callable(getter):
            return getter() or "unknown"
    except Exception:
        pass
    return "unknown"
