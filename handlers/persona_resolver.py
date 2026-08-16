"""v1.36: persona-id resolver for memory isolation.

Mirrors AstrBot's own 3-tier persona resolution (livingmemory parity):
  1. session_service_config.persona_id  (highest; /persona writes this)
  2. conversation.persona_id            (session-bound)
  3. global default persona             (lowest)

The result is stamped onto the event via set_extra("hippo_persona_id", ...)
so the synchronous _extract() in handlers.format can read it without a
Context handle. Persona isolation is gated by cfg.persona_isolation_enabled
(default on); when off, callers should fall back to "" (no persona scoping).

Best-effort: any failure resolves to "" and never raises into a hook.
"""
from __future__ import annotations

from hippocampus.memory_scope import resolve_scope_id

EXTRA_KEY = "hippo_persona_id"
EXTRA_SCOPE_KEY = "hippo_scope_id"


async def resolve_persona_id(context, event) -> str:
    """Return the active persona id for this event, or "" if none/unknown."""
    try:
        umo = getattr(event, "unified_msg_origin", None) or ""
        # tier 1: session_service_config (set by /persona)
        try:
            from astrbot.api import sp
            cfg = await sp.get_async(scope="umo", scope_id=umo,
                                     key="session_service_config", default={})
            pid = (cfg or {}).get("persona_id")
            if pid:
                return str(pid)
        except Exception:
            pass
        cm = getattr(context, "conversation_manager", None)
        # tier 2: conversation-bound persona
        if cm is not None:
            try:
                cid = await cm.get_curr_conversation_id(umo)
                if cid is not None:
                    conv = await cm.get_conversation(umo, cid)
                    pid = getattr(conv, "persona_id", None) if conv else None
                    if pid == "[%None]":
                        return ""          # explicitly persona-less session
                    if pid:
                        return str(pid)
            except Exception:
                pass
        # tier 3: global default persona
        try:
            pm = getattr(context, "persona_manager", None)
            if pm is not None:
                dp = await pm.get_default_persona_v3(umo=umo)
                pid = dp.get("name") if isinstance(dp, dict) else None
                if pid:
                    return str(pid)
        except Exception:
            pass
    except Exception:
        pass
    return ""


async def stamp_persona_id(context, event, *, enabled: bool) -> str:
    """Resolve + stamp persona id onto the event. Returns the id ("" if
    disabled/unknown). Safe to call from any hook.

    FIX (v1.45): idempotent. The observe_message and observe_bot_reply
    hooks in main.py both call this for the same event, and each call
    used to re-resolve + overwrite the extra. If the conversation
    state changed between calls (e.g. persona flipped to the
    "[%None]" sentinel in tier 2, a /persona command mutated
    session_service_config, or the first set_extra raised and only the
    second one landed) the user message and the bot reply would land
    in different daily_messages persona buckets, producing two
    diaries per session per day. Now we read the extra first; if it
    is already set to any value (including ""), we return that value
    verbatim. First hook wins; subsequent hooks are no-ops.
    """
    if not enabled:
        return ""
    # Idempotent: honour an existing stamp instead of re-resolving.
    try:
        ge = getattr(event, "get_extra", None)
        if callable(ge):
            existing = ge(EXTRA_KEY)
            if existing is not None:
                return str(existing)
    except Exception:
        pass
    pid = await resolve_persona_id(context, event)
    try:
        event.set_extra(EXTRA_KEY, pid)
    except Exception:
        pass
    return pid


async def stamp_scope_id(context, event, cfg=None) -> str:
    """Resolve + stamp the livingmemory-style memory scope onto the event.

    Legacy mode returns "" and leaves the old channel/persona behaviour
    untouched. Idempotent like stamp_persona_id.
    """
    try:
        ge = getattr(event, "get_extra", None)
        if callable(ge):
            existing = ge(EXTRA_SCOPE_KEY)
            if existing is not None:
                return str(existing)
    except Exception:
        pass
    scope = resolve_scope_id(cfg, event)
    try:
        event.set_extra(EXTRA_SCOPE_KEY, scope)
    except Exception:
        pass
    return scope
