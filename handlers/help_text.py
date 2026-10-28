"""Help text for /mem help, generated from the command registry.

v1.77.0: this module used to ship a hand-maintained wall of text that had
rotted badly -- it still said v1.4.x, listed commands that no longer
matched the router, omitted /mem persona, /mem session and /mem tier, and
even contained a literal backslash-n. The text is now BUILT from
engram_command_registry (the same table main.py dispatches through) plus
per-command usage/description strings in hippocampus/i18n/{zh,en}.json:

  * the version is read from the package, never hard-coded;
  * a command added to the router shows up automatically;
  * a command removed from the router disappears automatically;
  * a missing usage/description is a test failure (smoke v94), not a
    silently stale line.

get_help_text() renders in the currently active i18n language; the
module-level HELP_TEXT constant is the zh default captured at import and
is kept for callers that only need a string.
"""
from __future__ import annotations

from hippocampus.i18n_backend import init as i18n_init, t as _t, t_or as _t_or

# Eagerly init i18n so the module-level HELP_TEXT default is meaningful
# even before PluginInitializer calls init() with a real language.
i18n_init("zh")

_COLUMN = 49


def _version() -> str:
    try:
        from hippocampus import __version__ as ver
        return str(ver)
    except Exception:
        return "?"


def get_help_text() -> str:
    """Return the help text in the currently active i18n language."""
    from engram_command_registry import help_command_names

    lines: list[str] = [
        _t("help.header", version=_version()),
        "",
    ]
    for name in help_command_names():
        usage = _t_or("help.usage." + name, "/" + name)
        desc = _t_or("help.desc." + name, "")
        lines.append((usage.ljust(_COLUMN) + " " + desc).rstrip())
    return chr(10).join(lines)


HELP_TEXT: str = get_help_text()
