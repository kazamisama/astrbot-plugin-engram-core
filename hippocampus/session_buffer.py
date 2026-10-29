"""SessionAggregator: v1.6 optional per-speaker conversation buffering.

Why: ObserveHandler feeds every inbound message straight to
MemoryService.observe(), which encodes + stores one engram per message.
Two problems with that for chat:
  1. Noise / fragmentation - every short line becomes its own memory.
  2. Cross-speaker merges - the separator merges by vector similarity
     over a session's candidates without looking at actor_id, so two
     different people saying similar things in a group can collapse into
     one engram, losing the speaker boundary.

This buffer fixes both by aggregating *per (channel_id, actor_id)*:
consecutive messages from the same speaker in the same channel are held
in a small buffer and flushed as ONE combined observation. Because the
buffer key includes actor_id, different speakers never share a buffer,
so cross-speaker merges cannot happen at this layer.

Design constraints (kept deliberately small):
  - No background threads. Flushing is message-driven: every feed()
    first settles any other buffers that have gone idle, then appends to
    the caller's own buffer and flushes it if it hit the size cap.
  - Off by default. When disabled the caller bypasses this entirely and
    keeps the legacy one-engram-per-message behaviour.
  - A lightweight quality gate drops empty / too-short / exact-duplicate
    lines before they ever reach the buffer.

The aggregator does NOT store anything itself; it calls a `sink`
callable (normally MemoryService.observe) with the merged meta dict.
"""
from __future__ import annotations
import time
from typing import Callable


class _Buffer:
    __slots__ = ("meta", "lines", "first_ts", "last_ts")

    def __init__(self, meta: dict, now: float) -> None:
        # Keep the first message's identity fields; only content is merged.
        self.meta = {
            "session_id": meta.get("session_id", ""),
            "actor_id": meta.get("actor_id", ""),
            "platform": meta.get("platform", ""),
            "channel_id": meta.get("channel_id", ""),
            "persona_id": meta.get("persona_id", ""),
        }
        self.lines: list[str] = []
        self.first_ts = now
        self.last_ts = now


def _key(meta: dict) -> tuple:
    return (meta.get("channel_id") or "", meta.get("actor_id") or "")


class SessionAggregator:
    """Buffer consecutive same-speaker messages; flush as one observation.

    `sink(meta)` is called with a merged meta dict (same shape ObserveHandler
    passes to MemoryService.observe). `now_fn` is injectable for tests.
    """

    def __init__(self, cfg, sink: Callable[[dict], object],
                 now_fn: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self._sink = sink
        self._now = now_fn
        self._buffers: dict[tuple, _Buffer] = {}

    # ---- quality gate ----
    def _accept(self, content: str, buf: "_Buffer | None") -> bool:
        text = (content or "").strip()
        min_chars = int(self.cfg.session_aggregate_min_chars or 0)
        if len(text) < max(1, min_chars):
            return False
        # Drop an exact repeat of the immediately preceding buffered line.
        if buf is not None and buf.lines and buf.lines[-1].strip() == text:
            return False
        return True

    # ---- public API ----
    def feed(self, meta: dict) -> None:
        """Ingest one message. May flush other idle buffers and/or this
        speaker's buffer if it reaches the size cap."""
        now = self._now()
        self._flush_idle(now, exclude=_key(meta))

        content = (meta.get("content") or "")
        k = _key(meta)
        buf = self._buffers.get(k)
        if not self._accept(content, buf):
            # Still settle this speaker's buffer if it has gone idle.
            if buf is not None and self._is_idle(buf, now):
                self._flush_key(k)
            return

        if buf is None:
            buf = _Buffer(meta, now)
            self._buffers[k] = buf
        elif self._is_idle(buf, now):
            # The previous burst from this speaker is stale: flush it,
            # then start a fresh buffer for the new message.
            self._flush_key(k)
            buf = _Buffer(meta, now)
            self._buffers[k] = buf

        buf.lines.append(content.strip())
        buf.last_ts = now

        cap = int(self.cfg.session_aggregate_max_messages or 1)
        if len(buf.lines) >= max(1, cap):
            self._flush_key(k)

    def flush_all(self) -> None:
        """Force-flush every buffer (e.g. on plugin shutdown)."""
        for k in list(self._buffers.keys()):
            self._flush_key(k)

    # ---- internals ----
    def _idle_seconds(self) -> float:
        return float(self.cfg.session_aggregate_idle_seconds or 0.0)

    def _is_idle(self, buf: _Buffer, now: float) -> bool:
        idle = self._idle_seconds()
        if idle <= 0:
            return False
        return (now - buf.last_ts) >= idle

    def _flush_idle(self, now: float, exclude: tuple) -> None:
        for k in list(self._buffers.keys()):
            if k == exclude:
                continue
            buf = self._buffers.get(k)
            if buf is not None and self._is_idle(buf, now):
                self._flush_key(k)

    def _flush_key(self, k: tuple) -> None:
        buf = self._buffers.pop(k, None)
        if buf is None or not buf.lines:
            return
        merged = dict(buf.meta)
        merged["content"] = "\n".join(buf.lines).strip()
        if not merged["content"]:
            return
        try:
            self._sink(merged)
        except Exception as ex:
            print("[hippocampus] session aggregate flush error: " + repr(ex))