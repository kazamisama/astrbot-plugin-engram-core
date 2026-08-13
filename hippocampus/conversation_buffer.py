"""ConversationBuffer: v1.17 (B-1) per-channel whole-conversation buffer.

Unlike SessionAggregator (per-(channel, actor) buckets, used for the legacy
one-engram-per-speaker aggregation), this buffer keys ONLY by channel and
holds the *interleaved* messages of everyone in that channel - including the
bot itself - in arrival order. That is what conversation-level summarization
needs: a single time-ordered transcript per channel, not per speaker.

Each appended line keeps its speaker label and timestamp so the downstream
summarizer can attribute who-said-what and reconstruct the timeline.

Triggering (decided by the caller via flush callbacks):
  - idle: a channel that has gone quiet for >= idle_seconds (chat-type
    dependent: private vs group) is ready to flush.
  - scheduled / shutdown: flush_all() forces every channel.

This module stores nothing itself. On flush it calls `sink(record)` with a
ConversationRecord describing the whole window; the caller (service/handler)
turns that into a summarized engram. No AstrBot imports here so it is unit
testable in isolation.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ConvLine:
    actor_id: str
    speaker: str          # display name; falls back to actor_id
    content: str
    ts: float
    is_bot: bool = False


@dataclass
class ConversationRecord:
    """A flushed per-channel window handed to the summarizer sink."""
    channel_id: str
    chat_type: str        # "private" | "group"
    session_id: str
    platform: str
    persona_id: str = ""
    # identity stamps (B-1 requirement): private -> peer; group -> id+name
    peer_actor_id: str = ""     # private: who the bot talks to
    peer_name: str = ""
    group_id: str = ""
    group_name: str = ""
    lines: list = field(default_factory=list)   # list[ConvLine], time-ordered
    first_ts: float = 0.0
    last_ts: float = 0.0

    def participants(self, *, include_bot: bool = False) -> list:
        seen = []
        for ln in self.lines:
            if ln.is_bot and not include_bot:
                continue
            if ln.actor_id not in seen:
                seen.append(ln.actor_id)
        return seen

    def actor_names(self, *, include_bot: bool = False) -> dict:
        """Map actor_id -> latest display name (speaker). Lets downstream
        link a QQ? (actor_id) to its ?? as an alias."""
        out = {}
        for ln in self.lines:
            if ln.is_bot and not include_bot:
                continue
            nm = (ln.speaker or "").strip()
            if ln.actor_id and nm and nm != ln.actor_id:
                out[ln.actor_id] = nm
        return out

    def bot_name(self) -> str:
        """Latest display name the bot used in this window (its own replies).
        Empty if the bot did not speak in this buffer."""
        name = ""
        for ln in self.lines:
            if ln.is_bot:
                nm = (ln.speaker or "").strip()
                if nm and nm != ln.actor_id:
                    name = nm
        return name

    def transcript(self) -> str:
        """Time-ordered, speaker-labelled text for the LLM prompt."""
        out = []
        for ln in self.lines:
            t = time.strftime("%H:%M", time.localtime(ln.ts))
            out.append("[" + t + " " + (ln.speaker or ln.actor_id) + "] " + ln.content)
        return "\n".join(out)

    def round_count(self) -> int:
        """Rough conversational rounds: 2 messages ~= 1 round (mirrors
        livingmemory's unsummarized_rounds = messages // 2)."""
        return max(1, len(self.lines) // 2)


class _ChannelBuf:
    __slots__ = ("meta", "lines", "first_ts", "last_ts")

    def __init__(self, meta: dict, now: float) -> None:
        self.meta = {
            "channel_id": meta.get("channel_id", "") or "",
            "chat_type": meta.get("chat_type", "") or "",
            "session_id": meta.get("session_id", "") or "",
            "platform": meta.get("platform", "") or "",
            "persona_id": meta.get("persona_id", "") or "",
            "peer_actor_id": meta.get("peer_actor_id", "") or "",
            "peer_name": meta.get("peer_name", "") or "",
            "group_id": meta.get("group_id", "") or "",
            "group_name": meta.get("group_name", "") or "",
        }
        self.lines: list = []
        self.first_ts = now
        self.last_ts = now


class ConversationBuffer:
    """Per-channel interleaved buffer. `sink(record)` receives a
    ConversationRecord on flush. `now_fn` is injectable for tests."""

    def __init__(self, cfg, sink: Callable[[ConversationRecord], object],
                 now_fn: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self._sink = sink
        self._now = now_fn
        self._bufs: dict[str, _ChannelBuf] = {}

    # ---- chat-type aware idle ----
    def _idle_seconds(self, chat_type: str) -> float:
        if chat_type == "private":
            return float(getattr(self.cfg, "summary_idle_seconds_private", 1800.0) or 0.0)
        return float(getattr(self.cfg, "summary_idle_seconds_group", 600.0) or 0.0)

    def _is_idle(self, buf: _ChannelBuf, now: float) -> bool:
        idle = self._idle_seconds(buf.meta.get("chat_type", ""))
        if idle <= 0:
            return False
        return (now - buf.last_ts) >= idle

    def _min_messages(self) -> int:
        return int(getattr(self.cfg, "summary_min_messages", 0) or 0)

    def _below_min(self, buf: _ChannelBuf) -> bool:
        min_n = self._min_messages()
        return min_n > 0 and len(buf.lines) < min_n

    def _settle_idle_buf(self, ch: str, now: float) -> None:
        buf = self._bufs.get(ch)
        if buf is None or not self._is_idle(buf, now):
            return
        if self._below_min(buf):
            buf.last_ts = now
            return
        self._flush_key(ch)

    # ---- quality gate ----
    def _accept(self, content: str) -> bool:
        text = (content or "").strip()
        if not text:
            return False
        min_chars = int(getattr(self.cfg, "summary_min_chars", 0) or 0)
        return len(text) >= max(0, min_chars)

    # ---- public API ----
    def feed(self, meta: dict) -> None:
        """Ingest one message (user or bot) into its channel buffer.
        First settles any other channel that has gone idle."""
        now = self._now()
        ch = meta.get("channel_id", "") or ""
        self._flush_idle(now, exclude=ch)

        if not self._accept(meta.get("content", "")):
            self._settle_idle_buf(ch, now)
            return

        buf = self._bufs.get(ch)
        if buf is None:
            buf = _ChannelBuf(meta, now)
            self._bufs[ch] = buf
        elif self._is_idle(buf, now):
            if self._below_min(buf):
                buf.last_ts = now
            else:
                self._flush_key(ch)
                buf = _ChannelBuf(meta, now)
                self._bufs[ch] = buf

        buf.lines.append(ConvLine(
            actor_id=meta.get("actor_id", "") or "",
            speaker=meta.get("speaker", "") or meta.get("actor_id", "") or "",
            content=(meta.get("content", "") or "").strip(),
            ts=now,
            is_bot=bool(meta.get("is_bot", False)),
        ))
        buf.last_ts = now
        # fill late-arriving identity stamps (e.g. group_name resolved async)
        for k in ("peer_name", "group_name", "peer_actor_id", "group_id", "session_id", "persona_id"):
            if not buf.meta.get(k) and meta.get(k):
                buf.meta[k] = meta[k]

        cap = int(getattr(self.cfg, "summary_max_messages", 0) or 0)
        if cap > 0 and len(buf.lines) >= cap:
            self._flush_key(ch)

    def flush_all(self) -> None:
        for ch in list(self._bufs.keys()):
            self._flush_key(ch)

    def flush_idle_now(self) -> None:
        """Scheduled maintenance entrypoint: flush only channels gone idle."""
        now = self._now()
        for ch in list(self._bufs.keys()):
            self._settle_idle_buf(ch, now)

    # ---- internals ----
    def _flush_idle(self, now: float, exclude: str) -> None:
        for ch in list(self._bufs.keys()):
            if ch == exclude:
                continue
            self._settle_idle_buf(ch, now)

    def _flush_key(self, ch: str) -> None:
        buf = self._bufs.pop(ch, None)
        if buf is None or not buf.lines:
            return
        rec = ConversationRecord(
            channel_id=buf.meta.get("channel_id", ""),
            chat_type=buf.meta.get("chat_type", ""),
            session_id=buf.meta.get("session_id", ""),
            platform=buf.meta.get("platform", ""),
            persona_id=buf.meta.get("persona_id", ""),
            peer_actor_id=buf.meta.get("peer_actor_id", ""),
            peer_name=buf.meta.get("peer_name", ""),
            group_id=buf.meta.get("group_id", ""),
            group_name=buf.meta.get("group_name", ""),
            lines=list(buf.lines),
            first_ts=buf.first_ts,
            last_ts=buf.last_ts,
        )
        try:
            self._sink(rec)
        except Exception as ex:
            print("[hippocampus] conversation flush error: " + repr(ex))
