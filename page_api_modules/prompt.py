"""Prompt management page API (v1.76.6)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .utils import PageApiUtils


class PromptHandler:
    def __init__(self, utils: "PageApiUtils") -> None:
        self.utils = utils

    def list_prompts(self, service) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        try:
            return self.utils.ok({"items": service.list_prompts()})
        except Exception as e:
            return self.utils.error(f"list prompts failed: {e!r}")

    def get_prompt(self, service, name: str) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        name = (name or "").strip()
        for item in service.list_prompts():
            if item["name"] == name:
                return self.utils.ok(item)
        return self.utils.error("unknown prompt: " + name)

    def update_prompt(self, service, name: str, content: str) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        if not (name or "").strip():
            return self.utils.error("Missing name.")
        try:
            ok = service.set_prompt(name.strip(), content)
        except Exception as e:
            return self.utils.error(f"update prompt failed: {e!r}")
        return self.utils.ok({"name": name.strip(), "updated": bool(ok)})

    def reset_prompt(self, service, name: str) -> dict[str, Any]:
        if service is None:
            return self.utils.error("Memory service not initialized.")
        if not (name or "").strip():
            return self.utils.error("Missing name.")
        try:
            ok = service.reset_prompt(name.strip())
        except Exception as e:
            return self.utils.error(f"reset prompt failed: {e!r}")
        return self.utils.ok({"name": name.strip(), "reset": bool(ok)})
