"""v1.4 SessionFilter: decide whether an incoming message should be captured.

Filters (evaluated in this order, first deny wins):
1. platform_blocklist     -> drop if platform in blocklist
2. platform_allowlist     -> drop if non-empty AND platform not in it
3. channel_blocklist      -> drop if channel_id in blocklist
4. channel_allowlist      -> drop if non-empty AND channel_id not in it
5. actor_allowlist        -> drop if non-empty AND actor_id not in it
6. blocked_keywords       -> drop if content contains any keyword (case-insensitive)

If `enable_session_filter` is False on the bound config, the filter is
disabled and every message passes (legacy v1.3 behaviour).

The FilterDecision returned by decide() carries the *reason* so callers
can log/display it via /mem session.
"""
from __future__ import annotations
import enum
from dataclasses import dataclass, field
from typing import Iterable


class FilterVerdict(str, enum.Enum):
    PASS = "pass"
    DENY = "deny"


@dataclass
class FilterContext:
    """Minimal view of an incoming message for filter decisions."""
    platform: str = "unknown"
    channel_id: str = "default"
    actor_id: str = "anonymous"
    content: str = ""


@dataclass
class FilterDecision:
    verdict: FilterVerdict
    reason: str = ""
    """One of: 'disabled', 'platform_blocked', 'platform_not_allowed',
       'channel_blocked', 'channel_not_allowed', 'actor_not_allowed',
       'keyword_blocked:<kw>', 'pass'."""
    matched_rule: str = ""

    def is_pass(self) -> bool:
        return self.verdict == FilterVerdict.PASS


class SessionFilter:
    """Stateless filter bound to a MemoryConfig. Cheap to instantiate per-call
    or hold on the service. All rule lists are read from the bound cfg."""
    def __init__(self, cfg) -> None:
        self._cfg = cfg

    def decide(self, ctx: FilterContext) -> FilterDecision:
        cfg = self._cfg
        if not getattr(cfg, "enable_session_filter", False):
            return FilterDecision(FilterVerdict.PASS, "disabled", "")
        # 1. platform blocklist
        if ctx.platform in getattr(cfg, "platform_blocklist", []):
            return FilterDecision(FilterVerdict.DENY, "platform_blocked", ctx.platform)
        # 2. platform allowlist
        allow = list(getattr(cfg, "platform_allowlist", []) or [])
        if allow and ctx.platform not in allow:
            return FilterDecision(FilterVerdict.DENY, "platform_not_allowed", ctx.platform)
        # 3. channel blocklist
        if ctx.channel_id in getattr(cfg, "channel_blocklist", []):
            return FilterDecision(FilterVerdict.DENY, "channel_blocked", ctx.channel_id)
        # 4. channel allowlist
        ch_allow = list(getattr(cfg, "channel_allowlist", []) or [])
        if ch_allow and ctx.channel_id not in ch_allow:
            return FilterDecision(FilterVerdict.DENY, "channel_not_allowed", ctx.channel_id)
        # 5. actor allowlist
        act_allow = list(getattr(cfg, "actor_allowlist", []) or [])
        if act_allow and ctx.actor_id not in act_allow:
            return FilterDecision(FilterVerdict.DENY, "actor_not_allowed", ctx.actor_id)
        # 6. blocked keywords (case-insensitive substring match)
        content_low = (ctx.content or "").lower()
        for kw in getattr(cfg, "blocked_keywords", []) or []:
            if not kw:
                continue
            if kw.lower() in content_low:
                return FilterDecision(FilterVerdict.DENY, "keyword_blocked", kw)
        return FilterDecision(FilterVerdict.PASS, "pass", "")

    def is_allowed(self, ctx: FilterContext) -> bool:
        return self.decide(ctx).is_pass()

    def summary(self) -> dict:
        """Return the current rule set as a dict (for /mem session)."""
        cfg = self._cfg
        return {
            "enabled": bool(getattr(cfg, "enable_session_filter", False)),
            "platform_allowlist": list(getattr(cfg, "platform_allowlist", []) or []),
            "platform_blocklist": list(getattr(cfg, "platform_blocklist", []) or []),
            "channel_allowlist": list(getattr(cfg, "channel_allowlist", []) or []),
            "channel_blocklist": list(getattr(cfg, "channel_blocklist", []) or []),
            "actor_allowlist": list(getattr(cfg, "actor_allowlist", []) or []),
            "blocked_keywords": list(getattr(cfg, "blocked_keywords", []) or []),
        }