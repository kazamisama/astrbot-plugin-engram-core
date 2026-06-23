"""AstrBot Dashboard WebUI facade for hippocampus (B9).

Inspired by astrbot_plugin_livingmemory.core.page_api, adapted to
hippocampus's sync sqlite3 stack. The plugin main calls
`_register_official_page_api_if_available()` once at startup; this
class wires GET/POST endpoints to the handler modules.

AstrBot invokes registered handlers as `await view_handler(**path_vars)`
(see dashboard/server.py:srv_plug_route). It passes *no* query string
or JSON body to the callable, so every handler here is an async wrapper
that reads parameters from `quart.request` directly.

API prefix: /astrbot_plugin_engram/page
Endpoints:
  GET  /health           -> {version, language, service_ready}
  GET  /stats            -> {engrams, fts, entities, atoms, ...}
  GET  /memories         -> list_memories(q, k, offset)
  GET  /memories/detail  -> get_memory_detail(eid)
  POST /memories/delete  -> delete_memory(eid, hard)
  POST /memories/update  -> update_memory(eid, fields) [re-embeds on text change]
  POST /recall/test      -> test_recall(query, mode, k)
  GET  /graph/overview   -> graph_overview()
  POST /graph/query      -> graph_query(name)
  POST /graph/entity/delete   -> delete_entity(eid) [hard]
  POST /graph/relation/delete -> delete_relation(rid) [hard]
  POST /graph/relation/update -> update_relation(rid, confidence)
  GET  /backups          -> list backups (newest first)
  POST /backups/restore  -> restore from backup_id (DANGEROUS)
"""
from __future__ import annotations
import os
import sys
from typing import Any

# Plugin dir on sys.path so sibling page_api_modules resolves when
# AstrBot imports this module under the plugin package.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from page_api_modules import (
    PageApiUtils,
    StatsHandler,
    MemoryHandler,
    RecallHandler,
    GraphHandler,
    BackupHandler,
    DiaryHandler,
)

PLUGIN_NAME = "astrbot_plugin_engram"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


async def _query_args() -> dict:
    """Read query-string args from the active quart request (GET)."""
    try:
        from quart import request
        return dict(request.args)
    except Exception:
        return {}


async def _json_body() -> dict:
    """Read the JSON body from the active quart request (POST).

    Falls back to form / query args so the page still works if the
    frontend sends params another way.
    """
    try:
        from quart import request
        data = await request.get_json(force=True, silent=True)
        if isinstance(data, dict):
            return data
        form = await request.form
        if form:
            return dict(form)
        return dict(request.args)
    except Exception:
        return {}


def _as_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _as_bool(val: Any, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


class PluginPageApi:
    """Facade registering the web endpoints with the AstrBot host."""

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self.utils = PageApiUtils()
        self.stats_handler = StatsHandler(self.utils)
        self.memory_handler = MemoryHandler(self.utils)
        self.recall_handler = RecallHandler(self.utils)
        self.graph_handler = GraphHandler(self.utils)
        self.backup_handler = BackupHandler(self.utils)
        self.diary_handler = DiaryHandler(self.utils)

    def _service(self):
        """Return the live MemoryService or None if not initialized."""
        init = getattr(self.plugin, "_initializer", None)
        if init is None:
            return None
        return getattr(init, "service", None)

    def _backup_manager(self):
        """Return the live BackupManager or None if backup disabled."""
        init = getattr(self.plugin, "_initializer", None)
        if init is None:
            return None
        return getattr(init, "backup_manager", None)

    def register_routes(self) -> None:
        """Register all endpoints. The plugin's context must expose
        `register_web_api(route, handler, methods, desc)`; a missing
        method on older AstrBot is handled by the caller."""
        register = self.plugin.context.register_web_api

        register(f"{PAGE_API_PREFIX}/health", self._health,
                 ["GET"], "Hippocampus health probe")
        register(f"{PAGE_API_PREFIX}/stats", self._stats,
                 ["GET"], "Hippocampus stats")
        register(f"{PAGE_API_PREFIX}/memories", self._list_memories,
                 ["GET"], "Hippocampus memory list")
        register(f"{PAGE_API_PREFIX}/memories/detail", self._memory_detail,
                 ["GET"], "Hippocampus memory detail")
        register(f"{PAGE_API_PREFIX}/memories/delete", self._delete_memory,
                 ["POST"], "Hippocampus memory delete")
        register(f"{PAGE_API_PREFIX}/memories/update", self._update_memory,
                 ["POST"], "Hippocampus memory update")
        register(f"{PAGE_API_PREFIX}/recall/test", self._test_recall,
                 ["POST"], "Hippocampus recall test")
        register(f"{PAGE_API_PREFIX}/graph/overview", self._graph_overview,
                 ["GET"], "Hippocampus graph overview")
        register(f"{PAGE_API_PREFIX}/graph/data", self._graph_data,
                 ["GET"], "Hippocampus graph data (nodes+edges)")
        register(f"{PAGE_API_PREFIX}/graph/query", self._graph_query,
                 ["POST"], "Hippocampus graph query")
        register(f"{PAGE_API_PREFIX}/graph/entity/delete",
                 self._graph_entity_delete,
                 ["POST"], "Hippocampus graph entity hard delete")
        register(f"{PAGE_API_PREFIX}/graph/relation/delete",
                 self._graph_relation_delete,
                 ["POST"], "Hippocampus graph relation hard delete")
        register(f"{PAGE_API_PREFIX}/graph/relation/update",
                 self._graph_relation_update,
                 ["POST"], "Hippocampus graph relation confidence update")
        register(f"{PAGE_API_PREFIX}/backups", self._list_backups,
                 ["GET"], "Hippocampus backup list")
        register(f"{PAGE_API_PREFIX}/backups/restore", self._restore_backup,
                 ["POST"], "Hippocampus backup restore")
        # FIX (v1.43) WebUI diary routes. The async handlers
        # (_list_diaries, _diary_options, _diary_detail) were already
        # defined on this class but never wired up here, so the
        # frontend `apiGet("page/diaries[/options|detail]")` calls
        # returned "route not found". Frontend URLs (line 693/753/786
        # of app.js) are:
        #   page/diaries/options  -> filter dropdowns
        #   page/diaries          -> paginated list
        #   page/diaries/detail   -> full diary engram
        register(f"{PAGE_API_PREFIX}/diaries/options", self._diary_options,
                 ["GET"], "Hippocampus diary filter options")
        register(f"{PAGE_API_PREFIX}/diaries", self._list_diaries,
                 ["GET"], "Hippocampus diary list (filtered, paginated)")
        register(f"{PAGE_API_PREFIX}/diaries/detail", self._diary_detail,
                 ["GET"], "Hippocampus diary detail")
        # FIX (v1.46): WebUI inline delete (mirrors /memories/delete).
        register(f"{PAGE_API_PREFIX}/diaries/delete", self._delete_diary,
                 ["POST"], "Hippocampus diary soft/hard delete")

    # ---------- async route handlers ----------
    async def _health(self) -> dict[str, Any]:
        from hippocampus import __version__
        from hippocampus.i18n_backend import current_language
        return self.utils.ok({
            "version": __version__,
            "language": current_language(),
            "service_ready": self._service() is not None,
        })

    async def _stats(self) -> dict[str, Any]:
        return self.stats_handler.get_stats(self._service())

    async def _list_memories(self) -> dict[str, Any]:
        args = await _query_args()
        return self.memory_handler.list_memories(
            self._service(),
            q=str(args.get("q", "") or args.get("actor_id", "")),
            k=_as_int(args.get("k"), 50),
            offset=_as_int(args.get("offset"), 0),
        )

    async def _memory_detail(self) -> dict[str, Any]:
        args = await _query_args()
        return self.memory_handler.get_memory_detail(
            self._service(), eid=str(args.get("eid", "")))

    async def _delete_memory(self) -> dict[str, Any]:
        body = await _json_body()
        return self.memory_handler.delete_memory(
            self._service(),
            eid=str(body.get("eid", "")),
            hard=_as_bool(body.get("hard"), False),
        )

    async def _update_memory(self) -> dict[str, Any]:
        body = await _json_body()
        eid = str(body.get("eid", ""))
        fields = body.get("fields")
        if not isinstance(fields, dict):
            # accept flat body too: pull known editable keys directly
            fields = {k: body[k] for k in (
                "summary", "content", "memory_type", "tier",
                "importance", "strength", "topics", "tags",
                "persona_id") if k in body}
        return self.memory_handler.update_memory(
            self._service(), eid=eid, fields=fields)

    async def _test_recall(self) -> dict[str, Any]:
        body = await _json_body()
        return self.recall_handler.test_recall(
            self._service(),
            query=str(body.get("query", "")),
            mode=str(body.get("mode", "hybrid")),
            k=_as_int(body.get("k"), 5),
        )

    async def _graph_overview(self) -> dict[str, Any]:
        return self.graph_handler.graph_overview(self._service())

    async def _graph_data(self) -> dict[str, Any]:
        args = await _query_args()
        return self.graph_handler.graph_data(
            self._service(), limit=_as_int(args.get("limit"), 300))

    async def _graph_query(self) -> dict[str, Any]:
        body = await _json_body()
        return self.graph_handler.graph_query(
            self._service(), name=str(body.get("name", "")))

    async def _graph_entity_delete(self) -> dict[str, Any]:
        body = await _json_body()
        return self.graph_handler.delete_entity(
            self._service(), eid=str(body.get("eid", "")))

    async def _graph_relation_delete(self) -> dict[str, Any]:
        body = await _json_body()
        return self.graph_handler.delete_relation(
            self._service(), rid=str(body.get("rid", "")))

    async def _graph_relation_update(self) -> dict[str, Any]:
        body = await _json_body()
        return self.graph_handler.update_relation(
            self._service(), rid=str(body.get("rid", "")),
            confidence=body.get("confidence"))

    async def _list_backups(self) -> dict[str, Any]:
        return self.backup_handler.list_backups(self._backup_manager())

    async def _restore_backup(self) -> dict[str, Any]:
        body = await _json_body()
        return self.backup_handler.restore_backup(
            self._backup_manager(), backup_id=str(body.get("backup_id", "")))

    async def _list_diaries(self) -> dict[str, Any]:
        args = await _query_args()
        return self.diary_handler.list_diaries(
            self._service(),
            channel_id=str(args.get("channel_id", "")),
            persona_id=str(args.get("persona_id", "")),
            day=str(args.get("day", "")),
            q=str(args.get("q", "")),
            k=_as_int(args.get("k"), 50),
            offset=_as_int(args.get("offset"), 0),
        )

    async def _diary_options(self) -> dict[str, Any]:
        return self.diary_handler.options(self._service())

    async def _diary_detail(self) -> dict[str, Any]:
        args = await _query_args()
        return self.diary_handler.get_detail(
            self._service(), eid=str(args.get("eid", "")))

    async def _delete_diary(self) -> dict[str, Any]:
        body = await _json_body()
        return self.diary_handler.delete_diary(
            self._service(),
            eid=str(body.get("eid", "")),
            hard=_as_bool(body.get("hard"), False))
