"""astrbot_plugin_engram entry.

AstrBot loads via: from main import <registered class> so this file
must be importable when astrbot.api is on path.

Split history:
  v1.3 - rendering helpers moved to handlers/ package
  v1.4.x B6 - business logic moved to handlers/event/, dispatch to
              handlers/commands.py, init path to handlers/init.py.
              This file is now a thin Star shell: @filter decorators
              stay here (AstrBot scans Star subclasses), each command
              method is a 1-line forward to CommandRouter.
"""
from __future__ import annotations
import os
import sys
import asyncio
import json
import time
from typing import Any

from astrbot.api.star import Star, register, Context
from astrbot.api.event import filter, AstrMessageEvent

# AstrBot loads this plugin as a package; the plugin dir is not on
# sys.path. Inject it so the bundled hippocampus / handlers packages
# resolve via their existing absolute imports.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# core package lives next to this file (self-contained plugin layout)
from hippocampus import (MemoryService, MemoryConfig, Cue,
                         ProxyEmbeddingProvider, ProxyLLMProvider,
                         __version__ as HIPPO_VERSION,
                         EXPORT_FORMAT_VERSION)

# Back-compat re-export: v08-v13 smoke files (and any external
# caller) do `from main import format_xxx / export_engrams / ...`.
# Keep the original v1.3 re-export surface stable. B6 only
# adds the new handler / dispatch / init classes on top.
from handlers import (
    _extract,
    banner_text,
    emb_bridge_for_context,
    export_engrams,
    find_and_forget,
    format_activation,
    format_cluster,
    format_confidence,
    format_decaycurve,
    format_dual_route,
    format_graph,
    format_narrative,
    format_profile,
    format_session,
    HELP_TEXT,
    import_engrams,
    parse_search_args,
    render_stats,
)
from handlers.init import PluginInitializer
from handlers.event import (ObserveHandler, RecallHandler, ManageHandler,
                            InjectHandler)
from handlers.commands import CommandRouter




class HippocampusStar(Star):
    # Single source of truth for the @register version; mirrored as a
    # class attribute so smoke v12/v16 can assert alignment with
    # metadata.yaml.
    _registered_version = HIPPO_VERSION

    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self.context = context

        # 1. Build service + register tools (init path consolidated)
        self._initializer = PluginInitializer(context)
        self._initializer.initialize()
        self.service: Any = self._initializer.service
        self._tools = self._initializer.tools

        # 2. Build event handlers + dispatch router
        self._observer = ObserveHandler(self.service)
        self._recall = RecallHandler(self.service)
        self._manage = ManageHandler(self.service)
        self._inject = InjectHandler(self.service)
        self._commands = CommandRouter(self._observer, self._recall,
                                       self._manage)

        # 3. Register the Dashboard WebUI page API (no-op on old AstrBot)
        self._page_api = None
        self._register_official_page_api_if_available()

        # 4. v1.17 B-1: periodic idle-flush so quiet channels get summarized
        #    even without a triggering message. Best-effort; skips when no
        #    running loop (sync init path) - terminate() still flushes.
        self._idle_flush_task = None
        self._start_idle_flush_loop()

        # 5. v1.20 B-3: daily diary scheduler (runs at diary_trigger_hour).
        self._diary_task = None
        self._start_diary_loop()

    def _start_idle_flush_loop(self) -> None:
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            print("[hippocampus] idle flush loop: no running asyncio loop at init; "
                  "background idle flush disabled (conversations will still flush on demand).")
            return
        except Exception:
            print("[hippocampus] idle flush loop: unexpected init failure; disabled.")
            return

        async def _loop():
            import asyncio as _a
            while True:
                try:
                    interval = 60.0
                    cfg = getattr(self.service, "cfg", None)
                    if cfg is not None:
                        interval = float(getattr(
                            cfg, "summary_idle_flush_interval_seconds", 60.0) or 60.0)
                    await _a.sleep(max(5.0, interval))
                    convbuf = getattr(self._observer, "_conv_buffer", None)
                    if convbuf is not None:
                        convbuf.flush_idle_now()
                except _a.CancelledError:
                    break
                except Exception as ex:
                    print("[hippocampus] idle flush loop error: " + repr(ex))
        try:
            self._idle_flush_task = loop.create_task(_loop())
        except Exception:
            self._idle_flush_task = None

    def _start_diary_loop(self) -> None:
        """Fire service.run_daily_diary() once per day at the configured
        local hour. Best-effort; skips when no running loop (sync init).

        FIX (v1.41) BUG-6: previously returned silently when no loop was
        running, leaving the operator without any signal that the auto
        diary trigger is off. Now logs a one-line warning and the user
        can still trigger via /mem diary."""
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            print("[hippocampus] diary loop: no running asyncio loop at init; "
                  "daily auto-trigger disabled. Use /mem diary manually.")
            return
        except Exception as ex:
            print("[hippocampus] diary loop: unexpected init failure; disabled: " + repr(ex))
            return

        async def _loop():
            import asyncio as _a
            import time as _t
            while True:
                try:
                    cfg = getattr(self.service, "cfg", None)
                    if cfg is None or not getattr(cfg, "diary_enabled", False):
                        await _a.sleep(3600.0)
                        continue
                    hour = int(getattr(cfg, "diary_trigger_hour", 12) or 12)
                    now = _t.time()
                    lt = _t.localtime(now)
                    target = _t.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                        hour, 0, 0, lt.tm_wday, lt.tm_yday,
                                        lt.tm_isdst))
                    if target <= now:
                        target += 86400.0
                    await _a.sleep(max(5.0, target - now))
                    try:
                        n = self.service.run_daily_diary()
                        if n:
                            print("[hippocampus] daily diary wrote " + str(n) + " entries")
                    except Exception as ex:
                        print("[hippocampus] daily diary run error: " + repr(ex))
                except _a.CancelledError:
                    break
                except Exception as ex:
                    print("[hippocampus] diary loop error: " + repr(ex))
                    await _a.sleep(3600.0)
        try:
            self._diary_task = loop.create_task(_loop())
        except Exception:
            self._diary_task = None

    # ---------- v1.36: persona-id stamping for memory isolation ----------
    async def _stamp_persona(self, event) -> None:
        """Resolve the active persona id and stamp it onto the event so the
        synchronous _extract() can scope writes/recall by persona. Gated by
        persona_isolation_enabled (default on); best-effort, never raises."""
        try:
            from handlers.persona_resolver import stamp_persona_id
            cfg = getattr(self.service, "cfg", None) if self.service else None
            enabled = bool(getattr(cfg, "persona_isolation_enabled", True)) if cfg else True
            await stamp_persona_id(self.context, event, enabled=enabled)
        except Exception as ex:
            print("[hippocampus] persona stamp error: " + repr(ex))

    # ---------- event hook ----------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe_message(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        await self._observer.handle_message(event)

    # ---------- v1.31: capture QQ poke notice (litepoke alignment) ----------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe_poke(self, event: AstrMessageEvent):
        """Record poke notices with real actor names so summaries don't lose
        who poked whom. handle_poke self-filters to poke notices only."""
        try:
            await self._stamp_persona(event)
            await self._observer.handle_poke(event)
        except Exception as ex:
            print(f"[hippocampus] observe_poke error: {ex!r}")

    # ---------- v1.5: auto memory injection before each LLM call ----------
    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req):
        """Auto-inject recalled memories into req.prompt. No-op unless
        auto_inject_enabled is on; never aborts the LLM request."""
        try:
            await self._stamp_persona(event)
            await self._inject.handle_inject(event, req)
        except Exception as ex:
            print("[hippocampus] inject_memory hook error: " + repr(ex))

    # ---------- v1.17 B-1: capture the bot's own reply into the buffer ----------
    @filter.on_llm_response()
    async def observe_bot_reply(self, event: AstrMessageEvent, resp):
        """Feed the bot's own LLM reply into the conversation buffer so
        summaries include the bot's turns. No-op unless summary mode is on;
        never raises out of the hook."""
        try:
            text = ""
            for attr in ("completion_text", "text"):
                v = getattr(resp, attr, None)
                if v:
                    text = str(v)
                    break
            if text:
                await self._stamp_persona(event)
                await self._observer.handle_bot_message(event, text)
        except Exception as ex:
            print("[hippocampus] observe_bot_reply hook error: " + repr(ex))

    # ---------- commands (thin wrappers) ----------
    # Each wrapper yields whatever the handler returns. Decorator
    # names mirror AstrBot's command syntax; routing table lives in
    # CommandRouter.

    @filter.command("recall")
    async def cmd_recall(self, event: AstrMessageEvent, query: str):
        async for r in self._commands.dispatch(
                "recall", event, (query,), {}):
            yield r

    @filter.command("mem help")
    async def cmd_mem_help(self, event: AstrMessageEvent):
        yield event.plain_result(HELP_TEXT)

    @filter.command("mem stats")
    async def cmd_mem_stats(self, event: AstrMessageEvent):
        yield event.plain_result(render_stats(self.service))

    @filter.command("mem search")
    async def cmd_mem_search(self, event: AstrMessageEvent, arg: str):
        async for r in self._commands.dispatch(
                "mem search", event, (arg,), {}):
            yield r

    @filter.command("mem model")
    async def cmd_mem_model(self, event: AstrMessageEvent):
        async for r in self._commands.dispatch(
                "mem model", event, (), {}):
            yield r

    @filter.command("mem model use embedding")
    async def cmd_mem_use_emb(self, event: AstrMessageEvent, name: str):
        async for r in self._commands.dispatch(
                "mem model use embedding", event, (name,), {}):
            yield r

    @filter.command("mem model use llm")
    async def cmd_mem_use_llm(self, event: AstrMessageEvent, name: str):
        async for r in self._commands.dispatch(
                "mem model use llm", event, (name,), {}):
            yield r

    @filter.command("mem rebuild")
    async def cmd_mem_rebuild(self, event: AstrMessageEvent):
        async for r in self._commands.dispatch(
                "mem rebuild", event, (), {}):
            yield r

    @filter.command("mem prospective")
    async def cmd_mem_prospective(self, event: AstrMessageEvent):
        async for r in self._commands.dispatch(
                "mem prospective", event, (), {}):
            yield r

    @filter.command("mem session")
    async def cmd_mem_session(self, event: AstrMessageEvent):
        async for r in self._commands.dispatch(
                "mem session", event, (), {}):
            yield r

    @filter.command("mem profile")
    async def cmd_mem_profile(self, event: AstrMessageEvent,
                              actor: str = ""):
        async for r in self._commands.dispatch(
                "mem profile", event, (), {"actor": actor}):
            yield r

    @filter.command("mem persona")
    async def cmd_mem_persona(self, event: AstrMessageEvent,
                              actor: str = ""):
        async for r in self._commands.dispatch(
                "mem persona", event, (), {"actor": actor}):
            yield r

    @filter.command("mem activate")
    async def cmd_mem_activate(self, event: AstrMessageEvent,
                               seeds: str = ""):
        async for r in self._commands.dispatch(
                "mem activate", event, (), {"seeds": seeds}):
            yield r

    @filter.command("mem remember")
    async def cmd_mem_remember(self, event: AstrMessageEvent,
                               arg: str = ""):
        async for r in self._commands.dispatch(
                "mem remember", event, (), {"arg": arg}):
            yield r

    @filter.command("mem cluster")
    async def cmd_mem_cluster(self, event: AstrMessageEvent, eid: str):
        async for r in self._commands.dispatch(
                "mem cluster", event, (eid,), {}):
            yield r

    @filter.command("mem cluster-list")
    async def cmd_mem_cluster_list(self, event: AstrMessageEvent):
        async for r in self._commands.dispatch(
                "mem cluster-list", event, (), {}):
            yield r

    @filter.command("mem confidence")
    async def cmd_mem_confidence(self, event: AstrMessageEvent,
                                 query: str = ""):
        async for r in self._commands.dispatch(
                "mem confidence", event, (), {"query": query}):
            yield r

    @filter.command("mem decaycurve")
    async def cmd_mem_decaycurve(self, event: AstrMessageEvent,
                                 arg: str = ""):
        async for r in self._commands.dispatch(
                "mem decaycurve", event, (), {"arg": arg}):
            yield r

    @filter.command("mem consolidate")
    async def cmd_mem_consolidate(self, event: AstrMessageEvent):
        async for r in self._commands.dispatch(
                "mem consolidate", event, (), {}):
            yield r

    @filter.command("mem diary")
    async def cmd_mem_diary(self, event: AstrMessageEvent):
        async for r in self._commands.dispatch(
                "mem diary", event, (), {}):
            yield r

    @filter.command("mem forget")
    async def cmd_mem_forget(self, event: AstrMessageEvent, eid: str):
        async for r in self._commands.dispatch(
                "mem forget", event, (eid,), {}):
            yield r

    @filter.command("mem export")
    async def cmd_mem_export(self, event: AstrMessageEvent, path: str):
        async for r in self._commands.dispatch(
                "mem export", event, (path,), {}):
            yield r

    @filter.command("mem import")
    async def cmd_mem_import(self, event: AstrMessageEvent, path: str):
        async for r in self._commands.dispatch(
                "mem import", event, (path,), {}):
            yield r

    @filter.command("mem graph")
    async def cmd_mem_graph(self, event: AstrMessageEvent, query: str):
        async for r in self._commands.dispatch(
                "mem graph", event, (query,), {}):
            yield r

    @filter.command("mem narrative")
    async def cmd_mem_narrative(self, event: AstrMessageEvent,
                                topic: str):
        async for r in self._commands.dispatch(
                "mem narrative", event, (topic,), {}):
            yield r

    @filter.command("mem replay")
    async def cmd_mem_replay(self, event: AstrMessageEvent):
        async for r in self._commands.dispatch(
                "mem replay", event, (), {}):
            yield r

    @filter.command("mem valence")
    async def cmd_mem_valence(self, event: AstrMessageEvent):
        async for r in self._commands.dispatch(
                "mem valence", event, (), {}):
            yield r

    @filter.command("mem streams")
    async def cmd_mem_streams(self, event: AstrMessageEvent):
        async for r in self._commands.dispatch(
                "mem streams", event, (), {}):
            yield r

    @filter.command("mem tier")
    async def cmd_mem_tier(self, event: AstrMessageEvent, arg: str = ""):
        async for r in self._commands.dispatch(
                "mem tier", event, (), {"arg": arg}):
            yield r

    # Back-compat thin shim: smoke v16 calls this method directly
    # (it bypasses __init__ by using __new__, then sets star.service
    # manually before calling this). The real work lives in
    # PluginInitializer; we lazy-build one and inject the caller'''s
    # service so the tools list gets populated.
    def _register_agent_tools(self) -> None:
        if getattr(self, "_initializer", None) is None:
            self._initializer = PluginInitializer(self.context)
        self._initializer.service = self.service
        self._initializer._register_agent_tools()
        self._tools = self._initializer.tools

    def _register_official_page_api_if_available(self) -> None:
        """Register the B9 web API with the AstrBot Dashboard if the
        host exposes context.register_web_api. Missing on older AstrBot
        versions: silently skip and stay functional. Mirrors the
        livingmemory pattern.
        """
        if not hasattr(self.context, "register_web_api"):
            return
        try:
            from page_api import PluginPageApi
        except Exception as e:
            print(f"[hippocampus] page_api import failed: {e!r}")
            return
        try:
            self._page_api = PluginPageApi(self)
            self._page_api.register_routes()
        except Exception as e:
            self._page_api = None
            print(f"[hippocampus] page_api register failed: {e!r}")

    # ---------- lifecycle ----------
    async def terminate(self):
        # Drain any buffered session-aggregate bursts before shutdown so
        # the last in-memory batch is not lost. No-op when aggregation is
        # disabled (the aggregator was never built).
        try:
            agg = getattr(self._observer, "_aggregator", None)
            if agg is not None:
                agg.flush_all()
            convbuf = getattr(self._observer, "_conv_buffer", None)
            if convbuf is not None:
                convbuf.flush_all()
            task = getattr(self, "_idle_flush_task", None)
            if task is not None:
                task.cancel()
            dtask = getattr(self, "_diary_task", None)
            if dtask is not None:
                dtask.cancel()
        except Exception as e:
            print(f"[hippocampus] terminate flush error: {e!r}")
        if self.service is not None:
            try:
                await self.service.stop()
            except Exception as e:
                print(f"[hippocampus] terminate error: {e!r}")
