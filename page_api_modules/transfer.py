"""Memory import/export page API (v1.76.6)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .utils import PageApiUtils


class TransferHandler:
    def __init__(self, utils: "PageApiUtils") -> None:
        self.utils = utils

    def export_memories(self, service, fmt: str = "json") -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        fmt = (fmt or "json").lower()
        if fmt not in ("json", "csv"):
            return self.utils.error("format must be json or csv")
        try:
            from hippocampus.memory_transfer import export_memory_csv, export_memory_json
            content = export_memory_json(service) if fmt == "json" else export_memory_csv(service)
            count = 0
            truncated = False
            try:
                payload = __import__("json").loads(content) if fmt == "json" else None
                if isinstance(payload, dict):
                    count = payload.get("memory_count", 0)
                    truncated = bool(payload.get("truncated", False))
            except Exception:
                count = -1
            return self.utils.ok({
                "format": fmt,
                "content": content,
                "filename": "engram_memories_" + __import__("time").strftime("%Y%m%d_%H%M%S") + "." + fmt,
                "count": count,
                "truncated": truncated,
            })
        except Exception as e:
            return self.utils.error(f"export failed: {e!r}")

    def preview_import(self, service, content: str, fmt: str = "json") -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        try:
            from hippocampus.memory_transfer import preview_import
            return self.utils.ok(preview_import(content, fmt, service=service))
        except Exception as e:
            return self.utils.error(f"preview failed: {e!r}")

    def import_memories(self, service, content: str, fmt: str = "json",
                        dry_run: bool = False,
                        allow_duplicates: bool = False) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        try:
            from hippocampus.memory_transfer import import_memories, preview_import
            if dry_run:
                return self.utils.ok(preview_import(content, fmt, service=service))
            return self.utils.ok(import_memories(service, content, fmt,
                                                 allow_duplicates=allow_duplicates))
        except Exception as e:
            return self.utils.error(f"import failed: {e!r}")
