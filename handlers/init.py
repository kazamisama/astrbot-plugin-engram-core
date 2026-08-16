"""PluginInitializer: extracted from HippocampusStar.__init__ at v1.4.x B6.

Owns:
  1. _init_service       - build MemoryService from config dict
  2. _install_bridges    - register astrmock LLM/embedding proxies
  3. _start_background   - kick off service.start() (sync/async aware)
  4. _register_agent_tools - hand tools off to AstrBot context

The class deliberately never raises - each step logs and continues,
matching the prior main.py behavior where a misconfigured plugin
should not crash the AstrBot host.
"""
from __future__ import annotations
import asyncio
import os
import threading
from typing import Any
from hippocampus import (MemoryService,
                         ProxyEmbeddingProvider, ProxyLLMProvider,
                         BackupManager)
from hippocampus.config_manager import ConfigManager
from hippocampus.i18n_backend import init as i18n_init
from .recall import emb_bridge_for_context
from .format import banner_text


class PluginInitializer:
    """Build a MemoryService and wire it to an AstrBot Context.

    Returned service is exposed as `initializer.service`; tools list
    as `initializer.tools` (None if context.register_tool absent).
    """

    def __init__(self, context) -> None:
        self.context = context
        self.service: MemoryService | None = None
        self.tools: list | None = None
        self.backup_manager: BackupManager | None = None
        self._backup_thread: threading.Thread | None = None

    def initialize(self, config_dict: dict | None = None) -> None:
        cfg_dict = config_dict or {}
        try:
            cfg_dict = self.context.get_config("hippocampus") or {}
        except Exception:
            cfg_dict = {}
        # B9: respect bot_language (default "zh", also accepts "en")
        # so t("help.full_text") and similar resolve to the right
        # language at plugin startup. Re-init is idempotent.
        try:
            i18n_init(str(cfg_dict.get("bot_language", "zh")))
        except Exception:
            i18n_init("zh")
        self._init_service(cfg_dict)
        if self.service is not None:
            print(banner_text(self.service))
            self._install_bridges()
            self._start_background()
            self._register_agent_tools()
            # B10: kick off backup scheduler (no-op if interval=0 or disabled)
            self._start_backup_scheduler()

    def _init_service(self, cfg_dict: dict) -> None:
        # B7: route every MemoryConfig field through ConfigManager
        # (type / range / fallback validation) instead of hand-rolling
        # defaults here. Unset fields silently fall back to
        # MemoryConfig defaults.
        cfg = ConfigManager(cfg_dict).memory_config
        self.service = MemoryService(cfg)

    def _install_bridges(self) -> None:
        if self.service is None:
            return

        cfg = self.service.cfg
        emb_pid = getattr(cfg, "embedding_provider_id", "") or ""
        llm_pid = getattr(cfg, "llm_provider_id", "") or ""

        async def _llm_bridge(system: str, user: str, **kw) -> str:
            try:
                provider = None
                if llm_pid:
                    getter = getattr(self.context, "get_provider_by_id", None)
                    if getter is not None:
                        provider = getter(llm_pid)
                if provider is None:
                    provider = self.context.get_using_provider()
                resp = await provider.text_chat(
                    system_prompt=system, prompt=user, **kw)
                if hasattr(resp, "text"):
                    return resp.text or ""
                if hasattr(resp, "completion_text"):
                    return resp.completion_text or ""
                return str(resp)
            except Exception as e:
                print(f"[hippocampus] LLM bridge error: {e!r}")
                return ""

        async def _emb_bridge(text: str) -> list[float]:
            return await emb_bridge_for_context(
                self.context, text, provider_id=emb_pid)

        try:
            self.service.register_llm(
                "astrmock", ProxyLLMProvider("astrmock", _llm_bridge))
        except Exception as e:
            print(f"[hippocampus] register astrmock llm failed: {e!r}")

        emb_provider = None
        try:
            emb_provider = ProxyEmbeddingProvider("astrmock", _emb_bridge)
            self.service.register_embedding("astrmock", emb_provider)
        except Exception as e:
            print(f"[hippocampus] register astrmock embedding failed: {e!r}")

        # Activate the AstrBot-backed providers by default so LLM and
        # embedding actually flow through the host. Users that explicitly
        # set embedding_name / llm_name to something else (hash / openai /
        # rule) in the config keep that choice. auto_rebuild_on_switch is
        # disabled here so a fresh install does not re-embed on boot.
        #
        # v1.76.4: do NOT auto-switch embedding when the astrmock probe
        # failed (dim==0). Switching to a non-working provider used to make
        # every subsequent engram embed as [] AND, because recall filtered
        # FTS by embedding_model, it hid all previously stored hash-vector
        # memories from keyword search as well.
        prev_rebuild = self.service.cfg.auto_rebuild_on_switch
        self.service.cfg.auto_rebuild_on_switch = False
        try:
            if (self.service.cfg.embedding_name == "hash"
                    and self.service.registry.has_embedding("astrmock")):
                emb_ready = bool(
                    emb_provider is not None and getattr(emb_provider, "dim", 0) > 0)
                if emb_ready:
                    old_model = self.service.current_embedding()
                    self.service.set_embedding("astrmock")
                    legacy = self.service.store.count_by_embedding_model(
                        old_model, include_forgotten=False)
                    if legacy:
                        print(
                            "[hippocampus] switched embedding hash -> astrmock; "
                            + str(legacy) + " older vectors were not rebuilt. "
                            "FTS recall still covers them; run /mem rebuild to "
                            "restore vector recall.")
                else:
                    print(
                        "[hippocampus] astrmock embedding probe returned no "
                        "usable vector; keeping configured embedding "
                        + self.service.current_embedding()
                        + ". Configure an AstrBot embedding provider or use "
                        "/mem model use embedding astrmock later.")
        except Exception as e:
            print(f"[hippocampus] activate astrmock embedding failed: {e!r}")
        try:
            if (self.service.cfg.llm_name == "rule"
                    and self.service.registry.has_llm("astrmock")):
                self.service.set_llm("astrmock")
        except Exception as e:
            print(f"[hippocampus] activate astrmock llm failed: {e!r}")
        finally:
            self.service.cfg.auto_rebuild_on_switch = prev_rebuild

    def _start_background(self) -> None:
        if self.service is None:
            return
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(self.service.start())
            else:
                asyncio.run(self.service.start())
        except Exception as e:
            print(f"[hippocampus] start background task failed: {e!r}")

    def _register_agent_tools(self) -> None:
        """Register the v1.3+ agent tools with AstrBot. Real AstrBot
        exposes context.register_tool(...); in mocks / unit tests that
        method may be absent, in which case we just stash the tool list
        on `self.tools` so callers can introspect it.
        """
        if self.service is None:
            return
        from hippocampus.tools import all_tools
        tools = all_tools()
        self.tools = tools
        register_fn = getattr(self.context, "register_tool", None)
        if not callable(register_fn):
            return
        for t in tools:
            try:
                register_fn(t)
            except Exception as e:
                print(f"[hippocampus] register tool {t.name} failed: {e!r}")

    def _start_backup_scheduler(self) -> None:
        """B10: periodic .db backup in a daemon thread.
        
        Honors MemoryConfig.enable_backup + backup_interval_hours.
        interval=0 disables. First backup is delayed by 1/12 of the
        interval (so a fresh plugin install does not slam the disk
        immediately); subsequent backups run at the full cadence.
        """
        if self.service is None:
            return
        # v1.76.4: idempotency guard. initialize() calls this method once,
        # and historical call sites (e.g. _register_agent_tools) may still
        # invoke it directly; never start a second scheduler for the same
        # plugin instance.
        if self._backup_thread is not None and self._backup_thread.is_alive():
            return
        if self.backup_manager is not None:
            return
        cfg = self.service.cfg
        if not cfg.enable_backup:
            return
        bd = os.path.join(
            os.path.dirname(cfg.sqlite_path) or ".", "backups")
        self.backup_manager = BackupManager(
            cfg.sqlite_path, bd,
            version_provider=lambda: "hippocampus-" + str(__import__("hippocampus").__version__))
        interval_s = float(cfg.backup_interval_hours) * 3600.0
        if interval_s <= 0:
            return
        first_delay = max(60.0, interval_s / 12.0)
        
        def _loop():
            import time as _t
            _t.sleep(first_delay)
            while True:
                try:
                    if self.backup_manager is not None:
                        self.backup_manager.create(reason="auto")
                        self.backup_manager.cleanup(
                            keep_last=cfg.backup_keep_last,
                            keep_weekly=cfg.backup_keep_weekly,
                            keep_monthly=cfg.backup_keep_monthly)
                except Exception as e:
                    print("[hippocampus] backup loop error: " + repr(e))
                _t.sleep(interval_s)
        
        t = threading.Thread(target=_loop, daemon=True, name="hippocampus-backup")
        t.start()
        self._backup_thread = t
