"""AstrBot Dashboard WebUI facade for hippocampus (B9).

Inspired by astrbot_plugin_livingmemory.core.page_api, adapted to
hippocampus's sync sqlite3 stack. The plugin main calls
`_register_official_page_api_if_available()` once at startup; this
class wires GET/POST endpoints to the handler modules.

AstrBot invokes registered handlers as `await view_handler(**path_vars)`
(see dashboard/server.py:srv_plug_route). It passes *no* query string
or JSON body to the callable, so every handler here is an async wrapper
that reads parameters from `quart.request` directly.

API prefix: /astrbot_plugin_engram_core/page

Endpoints: the authoritative list is ROUTES below. register_routes()
iterates it, so docs, registrations and count cannot drift apart
(v1.77.0).
"""
from __future__ import annotations
import asyncio
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
    PersonaHandler,
    PromptHandler,
    TransferHandler,
)

# FIX (v1.48): renamed to match metadata.yaml: name (was
# astrbot_plugin_engram; collided with another plugin in the
# AstrBot registry and broke plugin updates). The URL prefix below
# is derived from this constant, so every /astrbot_plugin_engram_core/page
# path now points at THIS plugin, no ambiguity.
PLUGIN_NAME = "astrbot_plugin_engram_core"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"

ROUTES: list[tuple[str, str, list[str], str]] = [
    ("/health", "_health", ["GET"], "Hippocampus health probe"),
    ("/stats", "_stats", ["GET"], "Hippocampus stats"),
    ("/memories", "_list_memories", ["GET"], "Hippocampus memory list"),
    ("/memories/detail", "_memory_detail", ["GET"], "Hippocampus memory detail"),
    ("/memories/delete", "_delete_memory", ["POST"], "Hippocampus memory delete"),
    ("/memories/update", "_update_memory", ["POST"], "Hippocampus memory update"),
    ("/memories/restore", "_restore_memory", ["POST"], "Hippocampus memory restore"),
    ("/memories/source", "_memory_source", ["GET"], "Hippocampus memory source transcript"),
    ("/memories/resummarize", "_resummarize_memory", ["POST"], "Hippocampus memory re-summarize"),
    ("/memories/batch-delete", "_batch_delete_memories", ["POST"], "Hippocampus memory batch delete"),
    ("/recall/test", "_test_recall", ["POST"], "Hippocampus recall test"),
    ("/graph/overview", "_graph_overview", ["GET"], "Hippocampus graph overview"),
    ("/graph/data", "_graph_data", ["GET"], "Hippocampus graph data (nodes+edges)"),
    ("/graph/query", "_graph_query", ["POST"], "Hippocampus graph query"),
    ("/graph/entity/delete", "_graph_entity_delete", ["POST"], "Hippocampus graph entity hard delete"),
    ("/graph/relation/delete", "_graph_relation_delete", ["POST"], "Hippocampus graph relation hard delete"),
    ("/graph/relation/update", "_graph_relation_update", ["POST"], "Hippocampus graph relation confidence update"),
    ("/backups", "_list_backups", ["GET"], "Hippocampus backup list"),
    ("/backups/restore", "_restore_backup", ["POST"], "Hippocampus backup restore"),
    ("/diaries/options", "_diary_options", ["GET"], "Hippocampus diary filter options"),
    ("/diaries", "_list_diaries", ["GET"], "Hippocampus diary list (filtered, paginated)"),
    ("/diaries/detail", "_diary_detail", ["GET"], "Hippocampus diary detail"),
    ("/diaries/delete", "_delete_diary", ["POST"], "Hippocampus diary soft/hard delete"),
    ("/personas", "_list_personas", ["GET"], "Hippocampus persona list"),
    ("/personas/detail", "_persona_detail", ["GET"], "Hippocampus persona detail"),
    ("/personas/build", "_build_persona", ["POST"], "Hippocampus persona build"),
    ("/personas/update", "_update_persona", ["POST"], "Hippocampus persona update"),
    ("/personas/delete", "_delete_persona", ["POST"], "Hippocampus persona delete"),
    ("/prompts", "_list_prompts", ["GET"], "Hippocampus prompt list"),
    ("/prompts/update", "_update_prompt", ["POST"], "Hippocampus prompt update"),
    ("/prompts/reset", "_reset_prompt", ["POST"], "Hippocampus prompt reset"),
    ("/memories/export", "_export_memories", ["POST"], "Hippocampus memory export"),
    ("/memories/import", "_import_memories", ["POST"], "Hippocampus memory import"),
    ("/memories/import/preview", "_preview_memory_import", ["POST"], "Hippocampus memory import preview"),
]


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
        self.persona_handler = PersonaHandler(self.utils)
        self.prompt_handler = PromptHandler(self.utils)
        self.transfer_handler = TransferHandler(self.utils)

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

    # v1.76.15: dashboard handlers execute synchronous SQLite/LLM work.
    # Run them off the Quart/AstrBot event loop and bound the await.
    _PAGE_BLOCKING_TIMEOUT: float = 120.0

    async def _run_blocking(self, fn, *args, timeout: float | None = None,
                            **kwargs):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, *args, **kwargs),
                timeout=timeout or self._PAGE_BLOCKING_TIMEOUT)
        except asyncio.TimeoutError:
            return self.utils.error(
                "operation timed out after "
                + str(timeout or self._PAGE_BLOCKING_TIMEOUT) + "s")
        except Exception as ex:
            return self.utils.error(repr(ex))

    def register_routes(self) -> None:
        """Register all endpoints from ROUTES. The plugin's context must expose
        register_web_api(route, handler, methods, desc); a missing method on
        older AstrBot is handled by the caller.

        v1.77.0: ROUTES is the single source of truth. The old hand-written
        register() list and the module docstring both drifted from reality
        (14 documented vs 35 registered), so the table now feeds both.
        """
        register = self.plugin.context.register_web_api
        for suffix, handler_name, methods, desc in ROUTES:
            register(PAGE_API_PREFIX + suffix, getattr(self, handler_name),
                     methods, desc)

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
        return await self._run_blocking(self.stats_handler.get_stats, self._service())

    async def _list_memories(self) -> dict[str, Any]:
        args = await _query_args()
        return await self._run_blocking(
            self.memory_handler.list_memories,
            self._service(),
            q=str(args.get("q", "") or args.get("actor_id", "")),
            k=_as_int(args.get("k"), 50),
            offset=_as_int(args.get("offset"), 0),
            status=str(args.get("status", "active")),
        )

    async def _memory_detail(self) -> dict[str, Any]:
        args = await _query_args()
        return await self._run_blocking(
            self.memory_handler.get_memory_detail,
            self._service(), eid=str(args.get("eid", "")))

    async def _delete_memory(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.memory_handler.delete_memory,
            self._service(),
            eid=str(body.get("eid", "")),
            hard=_as_bool(body.get("hard"), False),
        )

    async def _memory_source(self) -> dict[str, Any]:
        args = await _query_args()
        return await self._run_blocking(
            self.memory_handler.get_memory_source,
            self._service(), eid=str(args.get("eid", "")))

    async def _resummarize_memory(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.memory_handler.resummarize_memory,
            self._service(), eid=str(body.get("eid", "")))

    async def _restore_memory(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.memory_handler.restore_memory,
            self._service(), eid=str(body.get("eid", "")))

    async def _batch_delete_memories(self) -> dict[str, Any]:
        body = await _json_body()
        eids = body.get("eids")
        if not isinstance(eids, list):
            eids = []
        return await self._run_blocking(
            self.memory_handler.batch_delete_memories,
            self._service(), eids=eids, hard=_as_bool(body.get("hard"), False))

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
        return await self._run_blocking(
            self.memory_handler.update_memory,
            self._service(), eid=eid, fields=fields)

    async def _test_recall(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.recall_handler.test_recall,
            self._service(),
            query=str(body.get("query", "")),
            mode=str(body.get("mode", "dual")),
            k=_as_int(body.get("k"), 5),
            persona_id=(body.get("persona_id") or None),
            scope_id=(body.get("scope_id") or None),
        )

    async def _graph_overview(self) -> dict[str, Any]:
        return await self._run_blocking(self.graph_handler.graph_overview, self._service())

    async def _graph_data(self) -> dict[str, Any]:
        args = await _query_args()
        return await self._run_blocking(
            self.graph_handler.graph_data,
            self._service(), limit=_as_int(args.get("limit"), 300),
            full=_as_bool(args.get("full"), False),
            scope_id=(args.get("scope_id") or None),
            persona_id=(args.get("persona_id") or None))

    async def _graph_query(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.graph_handler.graph_query,
            self._service(), name=str(body.get("name", "")))

    async def _graph_entity_delete(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.graph_handler.delete_entity,
            self._service(), eid=str(body.get("eid", "")))

    async def _graph_relation_delete(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.graph_handler.delete_relation,
            self._service(), rid=str(body.get("rid", "")))

    async def _graph_relation_update(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.graph_handler.update_relation,
            self._service(), rid=str(body.get("rid", "")),
            confidence=body.get("confidence"))

    async def _list_backups(self) -> dict[str, Any]:
        return await self._run_blocking(self.backup_handler.list_backups, self._backup_manager())

    async def _restore_backup(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.backup_handler.restore_backup,
            self._backup_manager(), backup_id=str(body.get("backup_id", "")))

    async def _list_diaries(self) -> dict[str, Any]:
        args = await _query_args()
        return await self._run_blocking(
            self.diary_handler.list_diaries,
            self._service(),
            channel_id=str(args.get("channel_id", "")),
            persona_id=str(args.get("persona_id", "")),
            day=str(args.get("day", "")),
            q=str(args.get("q", "")),
            k=_as_int(args.get("k"), 50),
            offset=_as_int(args.get("offset"), 0),
        )

    async def _diary_options(self) -> dict[str, Any]:
        return await self._run_blocking(self.diary_handler.options, self._service())

    async def _diary_detail(self) -> dict[str, Any]:
        args = await _query_args()
        return await self._run_blocking(
            self.diary_handler.get_detail,
            self._service(), eid=str(args.get("eid", "")))

    async def _delete_diary(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.diary_handler.delete_diary,
            self._service(),
            eid=str(body.get("eid", "")),
            hard=_as_bool(body.get("hard"), False))

    async def _list_personas(self) -> dict[str, Any]:
        return await self._run_blocking(self.persona_handler.list_personas, self._service())

    async def _persona_detail(self) -> dict[str, Any]:
        args = await _query_args()
        return await self._run_blocking(
            self.persona_handler.get_persona_detail,
            self._service(), actor_id=str(args.get("actor_id", "")))

    async def _build_persona(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.persona_handler.build_persona,
            self._service(), actor_id=str(body.get("actor_id", "")))

    async def _update_persona(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.persona_handler.update_persona,
            self._service(),
            actor_id=str(body.get("actor_id", "")),
            summary=str(body.get("summary", "")),
            tags=body.get("tags"))

    async def _export_memories(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.transfer_handler.export_memories,
            self._service(), fmt=str(body.get("format", "json")))

    async def _preview_memory_import(self) -> dict[str, Any]:
        body = await _json_body()
        service = self._service()
        content = str(body.get("content", ""))
        fmt = str(body.get("format", "json"))
        return await asyncio.to_thread(
            self.transfer_handler.preview_import, service, content, fmt)

    async def _import_memories(self) -> dict[str, Any]:
        body = await _json_body()
        service = self._service()
        content = str(body.get("content", ""))
        fmt = str(body.get("format", "json"))
        dry_run = _as_bool(body.get("dry_run"), False)
        allow_duplicates = _as_bool(body.get("allow_duplicates"), False)
        derive_indexes = _as_bool(body.get("derive_indexes"), True)
        return await asyncio.to_thread(
            self.transfer_handler.import_memories, service, content, fmt,
            dry_run=dry_run, allow_duplicates=allow_duplicates,
            derive_indexes=derive_indexes)

    async def _list_prompts(self) -> dict[str, Any]:
        return await self._run_blocking(self.prompt_handler.list_prompts, self._service())

    async def _update_prompt(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.prompt_handler.update_prompt,
            self._service(), str(body.get("name", "")), str(body.get("content", "")))

    async def _reset_prompt(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.prompt_handler.reset_prompt, self._service(), str(body.get("name", "")))

    async def _delete_persona(self) -> dict[str, Any]:
        body = await _json_body()
        return await self._run_blocking(
            self.persona_handler.delete_persona,
            self._service(), actor_id=str(body.get("actor_id", "")))
