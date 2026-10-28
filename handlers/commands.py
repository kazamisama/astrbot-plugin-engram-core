"""CommandRouter: thin dispatcher for the @filter.command methods.

Split from main.py at v1.4.x B6. main.py's @filter.command methods
are now thin wrappers that call CommandRouter.dispatch(name, event,
*args, **kwargs). This keeps the decorator + signature contract
AstrBot needs while moving the dispatch table out of main.py.

v1.77.0: the table itself lives in engram_command_registry.py so the
generated /mem help can read it without importing the handlers package.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from engram_command_registry import COMMAND_TABLE

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
        self._table: dict[str, str] = dict(COMMAND_TABLE)

    def dispatch(self, command_name: str, event, args: tuple, kwargs: dict):
        """Look up and invoke the handler. Returns the async generator
        that the @filter.command wrapper will yield. Raises KeyError
        if the command is not registered (caller's bug)."""
        dotted = self._table[command_name]
        obj_name, _, attr = dotted.partition(".")
        obj = getattr(self, obj_name)
        method = getattr(obj, attr)
        return method(event, *args, **kwargs)
