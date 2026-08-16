"""Memory scope resolver (livingmemory-inspired, v1.76.6).

Scope is stored as an opaque string on each engram and filtered on both
write and recall. Legacy mode keeps the old behaviour (empty scope).
"""
from __future__ import annotations
from typing import Any

GLOBAL_SCOPE = "scope:global"


def _cfg_get(cfg: Any, key: str, default: Any = None) -> Any:
    return getattr(cfg, key, default) if cfg is not None else default


def parse_identity_aliases(value: Any) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for line in str(value or "").splitlines():
        source, sep, target = line.partition("=")
        if not sep:
            continue
        source = source.strip()
        target = target.strip()
        if source and target:
            aliases[source.casefold()] = target
    return aliases


def _event_value(event: Any, method: str, attr: str = "") -> str:
    fn = getattr(event, method, None)
    if callable(fn):
        try:
            v = fn()
            return str(v).strip() if v is not None else ""
        except Exception:
            pass
    v = getattr(event, attr or method, "")
    return str(v).strip() if v is not None else ""


def resolve_event_identity(cfg: Any, event: Any) -> str:
    """Return canonical identity for the sender, applying alias config."""
    sender_id = _event_value(event, "get_sender_id", "sender_id")
    sender_name = _event_value(event, "get_sender_name", "sender_name")
    platform = _event_value(event, "get_platform_name", "platform").casefold()
    aliases = parse_identity_aliases(_cfg_get(cfg, "identity_aliases", ""))
    candidates = (
        f"{platform}:{sender_id}" if platform and sender_id else "",
        sender_id,
        sender_name,
    )
    for cand in candidates:
        if cand and cand.casefold() in aliases:
            return aliases[cand.casefold()]
    # Prefer the stable platform id over a mutable nickname. Nicknames still
    # work as alias sources, but an unaliased user must not split into a new
    # scope partition every time their display name changes.
    return sender_id or sender_name or "anonymous"


def resolve_scope_id(cfg: Any, event: Any,
                     session_id: str | None = None,
                     platform: str | None = None) -> str:
    """Return the scope partition for an event, or '' in legacy mode."""
    mode = str(_cfg_get(cfg, "memory_scope_mode", "legacy") or "legacy").lower()
    sid = session_id or _event_value(event, "unified_msg_origin", "unified_msg_origin") or ""
    isolated = set(
        str(x).strip() for x in (_cfg_get(cfg, "isolated_sessions", []) or []) if str(x).strip()
    )
    if sid and sid in isolated:
        return f"scope:session:{sid}"
    if mode == "session":
        return f"scope:session:{sid}" if sid else ""
    if mode == "global":
        return GLOBAL_SCOPE
    if mode == "user":
        plat = (platform or _event_value(event, "get_platform_name", "platform") or "unknown").casefold()
        identity = resolve_event_identity(cfg, event).casefold()
        return f"scope:user:{plat}:{identity}"
    return ""


