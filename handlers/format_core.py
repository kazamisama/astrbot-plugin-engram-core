"""Identity/text helpers shared by the format renderers.

Split out of the old 860-line handlers/format.py at v1.78.1; every name is
re-exported by handlers.format so existing imports keep working.
"""
from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from hippocampus import __version__ as HIPPO_VERSION
from hippocampus.observability import note as _obs_note


def _call(obj, name, default=None):
    """Call a 0-arg getter method if present, else return default."""
    fn = getattr(obj, name, None)
    if callable(fn):
        try:
            v = fn()
            return v if v not in (None, "") else default
        except Exception:
            return default
    return default


def _extract(event: AstrMessageEvent) -> dict:
    """Pull observation fields off a real AstrBot AstrMessageEvent.

    Uses the official getters / attributes (AstrBot
    core/platform/astr_message_event.py):
      - unified_msg_origin            -> session id (platform:type:sid)
      - get_sender_id()               -> actor id (message_obj.sender)
      - get_platform_name()           -> platform
      - get_group_id()                -> channel/group ("" in DM)
      - message_str / get_message_str -> text content
    Each lookup falls back to a sane default so a non-standard event
    (or a unit-test mock) never crashes the hook.
    """
    session_id = (getattr(event, "unified_msg_origin", None)
                  or _call(event, "get_session_id")
                  or getattr(event, "session_id", None)
                  or getattr(event, "message_id", "default"))
    actor_id = (_call(event, "get_sender_id")
                or getattr(getattr(event, "sender", None), "user_id", None)
                or "anonymous")
    platform = (_call(event, "get_platform_name")
                or getattr(getattr(event, "platform_meta", None), "name", None)
                or "unknown")
    group_id = _call(event, "get_group_id", "") or ""
    channel_id = group_id or session_id or "default"
    content = (getattr(event, "message_str", None)
               or _call(event, "get_message_str")
               or "")
    chat_type = "group" if group_id else "private"
    group_name = ""  # placeholder; full group name is set later when the event provides it
    # FIX (v1.56): friendly label for LLM context (e.g. group id "12345"
    # instead of raw session id; useful for the LLM extractor to disambiguate).
    channel_label = group_id or session_id or "default"
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
    speaker = (_call(event, "get_sender_name")
               or getattr(getattr(event, "sender", None), "nickname", None)
               or actor_id)
    # v1.17 B-1: conversation identity stamps
    # FIX (v1.56): add a friendly channel_label so the LLM extractor
    # (and diary summary) can refer to "the group" / "the chat" by
    # name instead of by raw session/group id.
    out = {
        "session_id": session_id,
        "actor_id": actor_id,
        "platform": platform,
        "channel_id": channel_id,
        "content": content,
        "chat_type": chat_type,
        "persona_id": persona_id,
        "scope_id": scope_id,
        "speaker": speaker,
        "group_id": group_id,
        "group_name": "",
        "channel_label": channel_label,
        "is_bot": False,
    }
    if chat_type == "private":
        out["peer_actor_id"] = actor_id
        out["peer_name"] = speaker
    return out


async def _resolve_group_name(event) -> str:
    """Best-effort async group-name lookup. AstrBot get_group() is async and
    platform-dependent; returns "" when unavailable (never raises)."""
    try:
        msg_obj = getattr(event, "message_obj", None)
        grp = getattr(msg_obj, "group", None) if msg_obj else None
        name = getattr(grp, "group_name", None) if grp else None
        if name:
            return str(name)
    except Exception:
        _obs_note("format", "_resolve_group_name")
    try:
        getter = getattr(event, "get_group", None)
        if callable(getter):
            grp = await getter()
            name = getattr(grp, "group_name", None) if grp else None
            if name:
                return str(name)
    except Exception:
        _obs_note("format", "_resolve_group_name")
    return ""


# (platform, self_id) -> bot nickname; avoids a network round-trip per reply.
_BOT_NAME_CACHE: dict[tuple, str] = {}


def _bot_actor_id(event) -> str:
    """Bot's own account id from the platform (e.g. QQ?). Falls back to "bot"."""
    try:
        getter = getattr(event, "get_self_id", None)
        if callable(getter):
            sid = getter()
            if sid:
                return str(sid)
    except Exception:
        _obs_note("format", "_bot_actor_id")
    try:
        sid = getattr(getattr(event, "message_obj", None), "self_id", None)
        if sid:
            return str(sid)
    except Exception:
        _obs_note("format", "_bot_actor_id")
    return "bot"


async def _resolve_bot_name(event, actor_id: str = "") -> str:
    """Best-effort bot nickname from the platform, cached per (platform, id).

    aiocqhttp exposes the bot's own nickname only via an async
    get_login_info call, so we fetch once and reuse. Returns the bot's
    account id (or "bot") when the platform cannot supply a nickname."""
    try:
        platform = ""
        getter = getattr(event, "get_platform_name", None)
        if callable(getter):
            platform = getter() or ""
    except Exception:
        platform = ""
    key = (platform, actor_id or _bot_actor_id(event))
    cached = _BOT_NAME_CACHE.get(key)
    if cached:
        return cached
    name = ""
    try:
        bot = getattr(event, "bot", None)
        if bot is not None and hasattr(bot, "call_action"):
            info = await bot.call_action("get_login_info")
            if isinstance(info, dict):
                name = str(info.get("nickname") or "").strip()
    except Exception:
        name = ""
    if not name:
        name = key[1] or "bot"
    _BOT_NAME_CACHE[key] = name
    return name


def banner_text(service):
    if service is None:
        return "[hippocampus] not initialized"
    try:
        n_e = len(service.store.all(limit=10_000_000))
    except Exception:
        n_e = -1
    return ("[hippocampus] loaded: v" + HIPPO_VERSION
              + ", embedding=" + service.current_embedding()
            + ", llm=" + service.current_llm()
            + ", engrams=" + str(n_e)
            + ", embeddings=" + str(service.registry.list_embeddings())
            + ", llms=" + str(service.registry.list_llms())
            + " | type /mem help")


def parse_search_args(arg):
    """Parse 'foo bar --mode=fts' -> ('foo bar', 'fts')."""
    arg = (arg or "").strip()
    mode = "hybrid"
    if " --mode=" in arg:
        arg, mode_part = arg.rsplit(" --mode=", 1)
        arg = arg.strip()
        mode = mode_part.strip().split()[0] if mode_part.strip() else "hybrid"
    if mode not in ("vector", "fts", "hybrid", "dual"):
        mode = "hybrid"
    return arg, mode
