"""Command registry for the Engram plugin (single source of truth).

Kept at plugin root rather than inside handlers/ so it can be imported
without pulling in handlers/__init__.py (which imports the astrbot API
chain). Zero third-party imports.

The router (handlers/commands.py) dispatches through COMMAND_TABLE;
/mem help is GENERATED from the same table (handlers/help_text.py), so a
command cannot drift out of the help again -- v1.77.0 fixed a help text
that was two years and nine commands stale.
"""
from __future__ import annotations

# command name -> attribute path on CommandRouter ("group.method").
# Insertion order is the order shown by /mem help: read/query first.
COMMAND_TABLE: dict[str, str] = {
    # read / query
    "recall":                  "recall.cmd_recall",
    "mem search":              "recall.cmd_mem_search",
    "mem profile":             "recall.cmd_mem_profile",
    "mem persona":             "recall.cmd_mem_persona",
    "mem activate":            "recall.cmd_mem_activate",
    "mem confidence":          "recall.cmd_mem_confidence",
    "mem decaycurve":          "recall.cmd_mem_decaycurve",
    "mem narrative":           "recall.cmd_mem_narrative",
    "mem debug":               "recall.cmd_mem_debug",
    # manage / write / debug
    "mem model":               "manage.cmd_mem_model",
    "mem model use embedding": "manage.cmd_mem_use_emb",
    "mem model use llm":       "manage.cmd_mem_use_llm",
    "mem rebuild":             "manage.cmd_mem_rebuild",
    "mem forget":              "manage.cmd_mem_forget",
    "mem export":              "manage.cmd_mem_export",
    "mem import":              "manage.cmd_mem_import",
    "mem graph":               "manage.cmd_mem_graph",
    "mem prospective":         "manage.cmd_mem_prospective",
    "mem replay":              "manage.cmd_mem_replay",
    "mem consolidate":         "manage.cmd_mem_consolidate",
    "mem diary":               "manage.cmd_mem_diary",
    "mem valence":             "manage.cmd_mem_valence",
    "mem streams":             "manage.cmd_mem_streams",
    "mem tier":                "manage.cmd_mem_tier",
    "mem session":             "manage.cmd_mem_session",
    "mem remember":            "manage.cmd_mem_remember",
}

# Commands handled directly by main.py (no router entry) that must still
# appear in the generated help.
DIRECT_COMMANDS: tuple[str, ...] = ("mem help", "mem stats")


def help_command_names() -> list[str]:
    """Ordered command names for /mem help: recall, direct, router."""
    seen: set[str] = set()
    out: list[str] = []
    for name in ("recall", *DIRECT_COMMANDS, *COMMAND_TABLE):
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out
