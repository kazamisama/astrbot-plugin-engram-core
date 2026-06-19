"""Page API utilities for the hippocampus WebUI.

B9: minimal utility set. Returns shaped dicts that match the AstrBot
Dashboard auto-discovered page-API contract:
  ok(data)       -> {"status": "ok", "data": data}
  error(message) -> {"status": "error", "message": str(message)}
"""
from __future__ import annotations
from typing import Any


class PageApiUtils:
    """Shared helpers for page-api handlers."""

    @staticmethod
    def ok(data: Any = None) -> dict[str, Any]:
        return {"status": "ok", "data": data}

    @staticmethod
    def error(message: str) -> dict[str, Any]:
        return {"status": "error", "message": str(message)}

    @staticmethod
    def safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
