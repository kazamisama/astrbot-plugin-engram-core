"""Help text shown by /mem help. Extracted from main.py cmd_mem_help.

B8: HELP_TEXT is now a function-style accessor returning the
t(help.full_text) translation for the currently active i18n
language. The module-level HELP_TEXT constant is kept for
back-compat (== zh default) and for any code path that imports it
as a string. New code should call get_help_text() instead.
"""
from hippocampus.i18n_backend import init as i18n_init, t as _t

# Eagerly init i18n so the module-level HELP_TEXT default is
# meaningful even before PluginInitializer calls init() with a
# real language.
i18n_init("zh")
HELP_TEXT_DEFAULT: str = _t("help.full_text")
HELP_TEXT: str = HELP_TEXT_DEFAULT


def get_help_text() -> str:
    """Return the help text in the currently active i18n language.

    Use this in new code instead of the HELP_TEXT constant.
    """
    return _t("help.full_text")
