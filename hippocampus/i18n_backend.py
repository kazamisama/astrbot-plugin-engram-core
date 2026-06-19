"""i18n backend for hippocampus (B8).

Loads JSON resources from hippocampus/i18n/{lang}.json, walks dot-
notation keys (a.b.c), falls back to zh on missing key, and
auto-loads ConfigManager.LABELS so t("config.embedding_dim") returns
the same zh/en label that B7 exposed via LABELS.

Inspired by astrbot_plugin_livingmemory.core.i18n_backend (zh/en/ru),
but trimmed to hippocampus's two-language scope (zh + en per ROADMAP
B8 plan). Labels from B7's ConfigManager.LABELS are merged in at
init() time so a single t() call site can render either a hard-coded
user-facing string (e.g. "recall.no_memory") or a config field label
(e.g. "config.embedding_dim").

API:
  init(language: str) -> None
      Load translations for the given language. Must be called before
      any t() / t_list() lookup. Falls back to zh if language is not
      "zh" or "en" or the JSON file is missing.

  t(key: str, **kwargs) -> str
      Walk dot-path. Falls back: target -> zh -> ConfigManager.LABELS
      -> key itself (with a single warn).

  t_list(key: str) -> list[str]
      Same fallback chain; returns [] if missing.

  current_language() -> str
      Currently active language code.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

# Module-level state. init() rewrites these.
_translations: dict = {}
_fallback: dict = {}  # always zh
_current_lang: str = "zh"

# Path to the i18n directory (hippocampus/i18n/).
_I18N_DIR = Path(__file__).parent / "i18n"

# Languages we ship resources for. B8 ships zh + en per ROADMAP.
SUPPORTED_LANGS: tuple[str, ...] = ("zh", "en")


def init(language: str = "zh") -> None:
    """Load translations for `language`. Idempotent."""
    global _translations, _fallback, _current_lang
    if not language or language not in SUPPORTED_LANGS:
        language = "zh"
    _current_lang = language

    # Always load zh as the ultimate fallback. deep-copy so that
    # mutating it with LABELS doesn't pollute other copies.
    import copy
    _fallback = copy.deepcopy(_load_json("zh"))

    # Try target; fall back to zh if missing. deep-copy for the
    # same reason - LABELS writes to it.
    if language == "zh":
        _translations = copy.deepcopy(_fallback)
    else:
        _translations = copy.deepcopy(_load_json(language))
        if not _translations:
            _translations = copy.deepcopy(_fallback)

    # Merge ConfigManager.LABELS so t("config.<field>") works.
    # Placed under a synthetic "config" namespace in each lang. The
    # two language dicts are independent copies: copying the json.load
    # output would be enough, but `for lang in SUPPORTED_LANGS` writes
    # to a different dict per iteration, which is the only reason this
    # isn't already shared.
    try:
        from .config_manager import LABELS
        for lang in SUPPORTED_LANGS:
            target = _translations if lang == _current_lang else _fallback
            cfg_ns = target.setdefault("config", {})
            for fname, lab in LABELS.items():
                cfg_ns[fname] = lab.get(lang, lab.get("zh", fname))
    except Exception:
        # LABELS not yet available (e.g. circular import during test
        # bootstrapping). Skip; t("config.X") will fall through to key.
        pass


def _load_json(lang: str) -> dict:
    """Load i18n/<lang>.json. Returns {} on any error."""
    path = _I18N_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _walk(data: dict, key: str) -> Any:
    """Walk dot-path; return None if any segment is missing."""
    cur = data
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def t(key: str, **kwargs) -> str:
    """Look up a translation. Falls back through: target -> zh -> key.

    `kwargs` are passed to str.format on the resolved template.
    """
    value = _walk(_translations, key)
    if value is None:
        value = _walk(_fallback, key)
    if value is None:
        # Final fallback: return the key itself so the UI does not
        # break on missing strings. A single warn is fine here; this
        # is a programming error (typo in key) that should be caught
        # in smoke v24.
        return f"[i18n-missing:{key}]"
    if not isinstance(value, str):
        return str(value)
    if kwargs:
        try:
            return value.format(**kwargs)
        except Exception:
            return value
    return value


def t_list(key: str) -> list[str]:
    """Look up a list-valued translation. Returns [] if missing."""
    value = _walk(_translations, key)
    if value is None:
        value = _walk(_fallback, key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def current_language() -> str:
    return _current_lang
