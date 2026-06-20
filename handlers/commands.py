"""CommandRouter: thin dispatcher for the @filter.command methods.

Split from main.py at v1.4.x B6. main.py's @filter.command methods
are now thin wrappers that call CommandRouter.dispatch(name, event,
*args, **kwargs). This keeps the decorator + signature contract
AstrBot needs while moving the dispatch table out of main.py.

The dispatch table is keyed by the bare command name (e.g. "mem
search" not "/mem search"). main.py can normalize the decorator
name and forward the rest.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .event import ObserveHandler, RecallHandler, ManageHandler


class CommandRouter:
    """Dispatch a (command_name, event, *args) call to the right handler method."""

    def __init__(self, observer: "ObserveHandler",
                 recall: "RecallHandler",
                 manage: "ManageHandler") -> None:
        self.observer = observer
        self.recall = recall
        self.manage = manage

        # (command_name) -> (handler_attr, unwrap_event?)
        # unwrap_event=True means the handler is async and returns an
        # async generator of MessageEventResult; we forward the iterator
        # to the caller, which is the @filter.command wrapper, which
        # yields each result back to AstrBot.
        self._table: dict[str, str] = {
            # read / query
            "recall":                "recall.cmd_recall",
            "mem search":            "recall.cmd_mem_search",
            "mem profile":           "recall.cmd_mem_profile",
            "mem persona":           "recall.cmd_mem_persona",
            "mem activate":          "recall.cmd_mem_activate",
            "mem cluster":           "recall.cmd_mem_cluster",
            "mem cluster-list":      "recall.cmd_mem_cluster_list",
            "mem confidence":        "recall.cmd_mem_confidence",
            "mem decaycurve":        "recall.cmd_mem_decaycurve",
            "mem narrative":         "recall.cmd_mem_narrative",
            # manage / write / debug
            "mem model":             "manage.cmd_mem_model",
            "mem model use embedding": "manage.cmd_mem_use_emb",
            "mem model use llm":     "manage.cmd_mem_use_llm",
            "mem rebuild":           "manage.cmd_mem_rebuild",
            "mem forget":            "manage.cmd_mem_forget",
            "mem export":            "manage.cmd_mem_export",
            "mem import":            "manage.cmd_mem_import",
            "mem graph":             "manage.cmd_mem_graph",
            "mem prospective":       "manage.cmd_mem_prospective",
            "mem replay":            "manage.cmd_mem_replay",
            "mem consolidate":       "manage.cmd_mem_consolidate",
            "mem diary":             "manage.cmd_mem_diary",
            "mem valence":           "manage.cmd_mem_valence",
            "mem streams":           "manage.cmd_mem_streams",
            "mem tier":              "manage.cmd_mem_tier",
            "mem session":           "manage.cmd_mem_session",
            "mem remember":          "manage.cmd_mem_remember",
        }

    def dispatch(self, command_name: str, event, args: tuple, kwargs: dict):
        """Look up and invoke the handler. Returns the async generator
        that the @filter.command wrapper will yield. Raises KeyError
        if the command is not registered (caller's bug)."""
        dotted = self._table[command_name]
        obj_name, _, attr = dotted.partition(".")
        obj = getattr(self, obj_name)
        method = getattr(obj, attr)
        return method(event, *args, **kwargs)