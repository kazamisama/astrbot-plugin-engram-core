"""AstrBot Dashboard WebUI facade for hippocampus (B9).

Inspired by astrbot_plugin_livingmemory.core.page_api, adapted to
hippocampus's sync sqlite3 stack. The plugin main calls
`_register_official_page_api_if_available()` once at startup; this
class wires 8 GET/POST endpoints to the 4 handler modules.

API prefix: /astrbot-plugin-engram/page
Endpoints (10 total after B10):
  GET  /stats           -> {engrams, fts, entities, atoms, ...}
  GET  /memories        -> list_memories(actor_id, k, offset)
  GET  /memories/detail -> get_memory_detail(eid)
  POST /memories/delete -> delete_memory(eid, hard)
  POST /recall/test     -> test_recall(query, mode, k)
  GET  /graph/overview  -> graph_overview()
  POST /graph/query     -> graph_query(name)
  GET  /health          -> {status, version, language}
  GET  /backups         -> list backups (newest first)
  POST /backups/restore -> restore from backup_id (DANGEROUS)

B10 (BackupManager) adds /backups (list) and /backups/restore; see
page_api_modules/backup.py.
"""
from __future__ import annotations
from typing import Any

from page_api_modules import (
    PageApiUtils,
    StatsHandler,
    MemoryHandler,
    RecallHandler,
    GraphHandler,
    BackupHandler,
)

PLUGIN_NAME = "astrbot-plugin-engram"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


class PluginPageApi:
    """Facade registering 10 web endpoints with the AstrBot host."""

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self.utils = PageApiUtils()
        self.stats_handler = StatsHandler(self.utils)
        self.memory_handler = MemoryHandler(self.utils)
        self.recall_handler = RecallHandler(self.utils)
        self.graph_handler = GraphHandler(self.utils)
        self.backup_handler = BackupHandler(self.utils)

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
        """Register all 8 endpoints. The plugin's context must have
        `register_web_api(path, handler, methods, name)`; missing on
        older AstrBot versions is handled by the caller."""
        register = self.plugin.context.register_web_api

        def svc():
            return self._service()

        register(f"{PAGE_API_PREFIX}/health", self._health,
                 ["GET"], "Hippocampus health probe")
        register(f"{PAGE_API_PREFIX}/stats", lambda: self.stats_handler.get_stats(svc()),
                 ["GET"], "Hippocampus stats")
        register(f"{PAGE_API_PREFIX}/memories",
                 lambda actor_id="", k=50, offset=0: self.memory_handler.list_memories(
                     svc(), actor_id=actor_id, k=k, offset=offset),
                 ["GET"], "Hippocampus memory list")
        register(f"{PAGE_API_PREFIX}/memories/detail",
                 lambda eid="": self.memory_handler.get_memory_detail(svc(), eid=eid),
                 ["GET"], "Hippocampus memory detail")
        register(f"{PAGE_API_PREFIX}/memories/delete",
                 lambda eid="", hard=False: self.memory_handler.delete_memory(
                     svc(), eid=eid, hard=hard),
                 ["POST"], "Hippocampus memory delete")
        register(f"{PAGE_API_PREFIX}/recall/test",
                 lambda query="", mode="hybrid", k=5: self.recall_handler.test_recall(
                     svc(), query=query, mode=mode, k=k),
                 ["POST"], "Hippocampus recall test")
        register(f"{PAGE_API_PREFIX}/graph/overview",
                 lambda: self.graph_handler.graph_overview(svc()),
                 ["GET"], "Hippocampus graph overview")
        register(f"{PAGE_API_PREFIX}/graph/query",
                 lambda name="": self.graph_handler.graph_query(svc(), name=name),
                 ["POST"], "Hippocampus graph query")
        register(f"{PAGE_API_PREFIX}/backups",
                 lambda: self.backup_handler.list_backups(self._backup_manager()),
                 ["GET"], "Hippocampus backup list")
        register(f"{PAGE_API_PREFIX}/backups/restore",
                 lambda backup_id="": self.backup_handler.restore_backup(
                     self._backup_manager(), backup_id=backup_id),
                 ["POST"], "Hippocampus backup restore")

    def _health(self) -> dict[str, Any]:
        from hippocampus import __version__
        from hippocampus.i18n_backend import current_language
        return self.utils.ok({
            "version": __version__,
            "language": current_language(),
            "service_ready": self._service() is not None,
        })
