"""Rendering helpers for /mem and /recall -- facade.

v1.78.1: the old 860-line module was split by concern into five modules:

    format_core     identity / text helpers (extract, banner, search args)
    format_stats    /mem stats + the default template
    format_memory   forget / export / import
    format_recall   narrative, profile, activation, graph, confidence, curve
    format_inspect  debug, dual-route, session, tier

Every name is re-exported here, so `from handlers.format import X` and the
main.py back-compat surface keep working unchanged.
"""
from __future__ import annotations

from .format_core import (
    _BOT_NAME_CACHE,
    _bot_actor_id,
    _call,
    _extract,
    _resolve_bot_name,
    _resolve_group_name,
    banner_text,
    parse_search_args,
)
from .format_inspect import (
    _join_or_all,
    format_debug,
    format_dual_route,
    format_session,
    format_tier,
    format_tier_archive,
)
from .format_memory import (
    DEFAULT_AMBIGUOUS,
    DEFAULT_ERR,
    DEFAULT_EXPORT_DONE,
    DEFAULT_FMT_WARN,
    DEFAULT_FORGET_OK,
    DEFAULT_IMPORT_DONE,
    DEFAULT_NOT_FOUND,
    export_engrams,
    find_and_forget,
    import_engrams,
)
from .format_recall import (
    format_activation,
    format_confidence,
    format_decaycurve,
    format_graph,
    format_narrative,
    format_profile,
)
from .format_stats import DEFAULT_STATS, format_stats, render_stats

__all__ = [
    "_BOT_NAME_CACHE",
    "_bot_actor_id",
    "_call",
    "_extract",
    "_join_or_all",
    "_resolve_bot_name",
    "_resolve_group_name",
    "banner_text",
    "parse_search_args",
    "format_debug",
    "format_dual_route",
    "format_session",
    "format_tier",
    "format_tier_archive",
    "DEFAULT_AMBIGUOUS",
    "DEFAULT_ERR",
    "DEFAULT_EXPORT_DONE",
    "DEFAULT_FMT_WARN",
    "DEFAULT_FORGET_OK",
    "DEFAULT_IMPORT_DONE",
    "DEFAULT_NOT_FOUND",
    "export_engrams",
    "find_and_forget",
    "import_engrams",
    "format_activation",
    "format_confidence",
    "format_decaycurve",
    "format_graph",
    "format_narrative",
    "format_profile",
    "DEFAULT_STATS",
    "format_stats",
    "render_stats",
]
