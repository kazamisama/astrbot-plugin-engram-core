"""reltime: human relative-time labels for injected memories (v1.12).

When auto_inject prepends recalled memories to the LLM prompt, the raw
summaries carry no temporal anchor, so the model cannot tell whether a
memory is from minutes or months ago. Each engram already stores
created_at (epoch seconds); this renders it as a short Chinese relative
label like “刚刚” / “3 小时前” / “2 天前”, so the model gets cheap,
robust time awareness without leaking exact timestamps.
"""
from __future__ import annotations
import time


def relative_label(created_at, now=None) -> str:
    """Return a short zh relative-time label for an epoch-seconds value.

    Returns '' for missing / non-positive / future-but-tiny noise so the
    caller can simply skip prefixing when there is nothing meaningful."""
    try:
        ts = float(created_at or 0.0)
    except (TypeError, ValueError):
        return ''
    if ts <= 0:
        return ''
    n = time.time() if now is None else float(now)
    delta = n - ts
    # Small clock skew / future timestamps collapse to “刚刚”.
    if delta < 0:
        delta = 0.0
    minute, hour, day = 60.0, 3600.0, 86400.0
    if delta < minute:
        return '刚刚'
    if delta < hour:
        return str(int(delta // minute)) + ' 分钟前'
    if delta < day:
        return str(int(delta // hour)) + ' 小时前'
    if delta < day * 30:
        return str(int(delta // day)) + ' 天前'
    if delta < day * 365:
        return str(int(delta // (day * 30))) + ' 个月前'
    return str(int(delta // (day * 365))) + ' 年前'
