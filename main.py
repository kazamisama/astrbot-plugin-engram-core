"""astrbot_plugin_engram_core entry.

AstrBot loads via: from main import HippocampusStar (registration is
metadata.yaml driven), so this file must be importable when astrbot.api is on path.

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
from typing import Any

from astrbot.api.star import Star, Context
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


def _run_async_gen_in_thread(factory):
    """Collect an async generator on a private loop in a worker thread.

    Used by command dispatch: the handler body is synchronous heavy work
    wrapped in an async generator, so simply awaiting it would still block
    the AstrBot event loop. Running the whole generator on a private loop
    keeps the bot responsive and returns the yielded result objects.
    """
    loop = asyncio.new_event_loop()
    try:
        async def _collect():
            out = []
            async for item in factory():
                out.append(item)
            return out
        return loop.run_until_complete(_collect())
    finally:
        loop.close()

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
    # Single source of truth for the plugin version; mirrored as a class
    # attribute so smoke v12/v16 can assert alignment with metadata.yaml.
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
                        # FIX (v1.42) BUG-7: clamp the loop to also honour
                        # diary_message_flush_interval_seconds when it is
                        # shorter, so a low-traffic channel still has its
                        # buffer drained within its own SLA.
                        diary_interval = float(getattr(
                            cfg, "diary_message_flush_interval_seconds", 30.0) or 30.0)
                        if diary_interval > 0:
                            interval = min(interval, max(5.0, diary_interval))
                    await _a.sleep(max(5.0, interval))
                    convbuf = getattr(self._observer, "_conv_buffer", None)
                    if convbuf is not None:
                        # v1.76.4 (M5): idle flush may trigger an LLM
                        # summary; run it off the event loop.
                        # v1.76.15: bound the await too so a multi-channel
                        # flush cannot hold this loop task forever.
                        try:
                            await _a.wait_for(
                                _a.to_thread(convbuf.flush_idle_now),
                                timeout=120.0)
                        except _a.TimeoutError:
                            print("[hippocampus] conv idle flush timed out "
                                  "after 120s; work continues in background")
                    # FIX (v1.42) BUG-7: time-trigger flush for the diary
                    # write buffer so low-traffic channels do not let lines
                    # sit in memory longer than the configured SLA.
                    ds = getattr(self.service, "diary_store", None)
                    if ds is not None and hasattr(ds, "flush_now"):
                        try:
                            n = await _a.wait_for(
                                _a.to_thread(ds.flush_now), timeout=30.0)
                            if n:
                                print("[hippocampus] diary buffer flushed "
                                      + str(n) + " lines")
                        except _a.TimeoutError:
                            print("[hippocampus] diary buffer flush timed "
                                  "out after 30s")
                        except Exception as dex:
                            print("[hippocampus] diary buffer flush error: "
                                  + repr(dex))
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
                        # v1.76.4 (M5): diary generation runs the LLM;
                        # keep the event loop responsive while it works.
                        # v1.76.15: bound the await as well.
                        n = await _a.wait_for(
                            _a.to_thread(self.service.run_daily_diary),
                            timeout=1800.0)
                        if n:
                            print("[hippocampus] daily diary wrote " + str(n) + " entries")
                    except _a.TimeoutError:
                        print("[hippocampus] daily diary run timed out after "
                              "1800s; work continues in background")
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
            from handlers.persona_resolver import stamp_persona_id, stamp_scope_id
            cfg = getattr(self.service, "cfg", None) if self.service else None
            enabled = bool(getattr(cfg, "persona_isolation_enabled", True)) if cfg else True

            async def _inner():
                await stamp_persona_id(self.context, event, enabled=enabled)
                await stamp_scope_id(self.context, event, cfg=cfg)

            await asyncio.wait_for(_inner(), timeout=self._STAMP_HARD_TIMEOUT)
        except asyncio.TimeoutError:
            print("[hippocampus] persona stamp timed out after "
                  + str(self._STAMP_HARD_TIMEOUT) + "s; continuing unscoped")
        except Exception as ex:
            print("[hippocampus] persona stamp error: " + repr(ex))

    # ---------- event hook ----------
    # v1.76.15: persona stamping calls AstrBot core APIs from every hook
    # and command. Bound it so a hung conversation/persona manager degrades
    # to "no persona scope" instead of a stuck hook/task.
    _STAMP_HARD_TIMEOUT: float = 10.0

    # v1.76.14 (2026-09-07 20:32 recurrence): the whole hook body is
    # bounded, not just HandleInject's inner wait_for. The 19:39 freeze
    # entered inject_memory and then the loop died within milliseconds --
    # before the 10s circuit breaker could fire (a frozen loop cannot
    # schedule the wait_for timer). stamp_persona_id / resolve_persona_id
    # call AstrBot core APIs (sp.get_async, conversation_manager,
    # persona_manager) that may block; wrap every hook so a wedged call
    # degrades to "skip this op" and the event/LLM request is released.
    _HOOK_HARD_TIMEOUT: float = 30.0
    _OBSERVE_HOOK_HARD_TIMEOUT: float = 90.0

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe_message(self, event: AstrMessageEvent):
        try:
            await asyncio.wait_for(self._do_observe_message(event),
                                   timeout=self._OBSERVE_HOOK_HARD_TIMEOUT)
        except asyncio.TimeoutError:
            print("[hippocampus] observe_message hook timed out after "
                  + str(self._OBSERVE_HOOK_HARD_TIMEOUT) + "s; released")

    async def _do_observe_message(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        await self._observer.handle_message(event)

    # ---------- v1.31: capture QQ poke notice (litepoke alignment) ----------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe_poke(self, event: AstrMessageEvent):
        """Record poke notices with real actor names so summaries don't lose
        who poked whom. handle_poke self-filters to poke notices only."""
        try:
            await asyncio.wait_for(self._do_observe_poke(event),
                                   timeout=self._OBSERVE_HOOK_HARD_TIMEOUT)
        except asyncio.TimeoutError:
            print("[hippocampus] observe_poke hook timed out after "
                  + str(self._OBSERVE_HOOK_HARD_TIMEOUT) + "s; released")

    async def _do_observe_poke(self, event: AstrMessageEvent):
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
            await asyncio.wait_for(self._do_inject_memory(event, req),
                                   timeout=self._HOOK_HARD_TIMEOUT)
        except asyncio.TimeoutError:
            print("[hippocampus] inject_memory hook timed out after "
                  + str(self._HOOK_HARD_TIMEOUT)
                  + "s - proceeding WITHOUT injection (LLM request released)")

    async def _do_inject_memory(self, event: AstrMessageEvent, req):
        await self._stamp_persona(event)
        await self._inject.handle_inject(event, req)

    # ---------- v1.17 B-1: capture the bot's own reply into the buffer ----------
    @filter.on_llm_response()
    async def observe_bot_reply(self, event: AstrMessageEvent, resp):
        """Feed the bot's own LLM reply into the conversation buffer so
        summaries include the bot's turns. No-op unless summary mode is on;
        never raises out of the hook."""
        try:
            await asyncio.wait_for(self._do_observe_bot_reply(event, resp),
                                   timeout=self._OBSERVE_HOOK_HARD_TIMEOUT)
        except asyncio.TimeoutError:
            print("[hippocampus] observe_bot_reply hook timed out after "
                  + str(self._OBSERVE_HOOK_HARD_TIMEOUT) + "s; released")

    async def _do_observe_bot_reply(self, event: AstrMessageEvent, resp):
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
    #
    # v1.76.15: handlers are async generators whose bodies execute heavy
    # synchronous code. The old wrappers awaited them directly on the
    # event loop, so a /mem rebuild or /mem search --mode=dual could
    # freeze the whole bot. _dispatch_command runs the generator on a
    # private loop in a worker thread and only resumes on the main loop
    # to yield the already-built result objects.
    _COMMAND_HARD_TIMEOUT: float = 180.0
    _COMMAND_HEAVY_TIMEOUT: float = 900.0
    _HEAVY_COMMANDS = {
        "mem rebuild", "mem replay", "mem diary", "mem consolidate",
        "mem export", "mem import", "mem graph", "mem debug",
    }

    async def _dispatch_command(self, command_name: str, event, args, kwargs):
        timeout = (self._COMMAND_HEAVY_TIMEOUT
                   if command_name in self._HEAVY_COMMANDS
                   else self._COMMAND_HARD_TIMEOUT)

        def _factory():
            return self._commands.dispatch(command_name, event, args, kwargs)

        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(_run_async_gen_in_thread, _factory),
                timeout=timeout)
        except asyncio.TimeoutError:
            yield event.plain_result(
                "[hippocampus] command timed out after " + str(timeout)
                + "s; work continues in background")
            return
        except Exception as ex:
            yield event.plain_result("[hippocampus] command error: " + repr(ex))
            return
        for r in results or []:
            yield r

    @filter.command("recall")
    async def cmd_recall(self, event: AstrMessageEvent, query: str):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "recall", event, (query,), {}):
            yield r

    @filter.command("mem help")
    async def cmd_mem_help(self, event: AstrMessageEvent):
        yield event.plain_result(HELP_TEXT)

    @filter.command("mem stats")
    async def cmd_mem_stats(self, event: AstrMessageEvent):
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(render_stats, self.service),
                timeout=self._COMMAND_HARD_TIMEOUT)
        except asyncio.TimeoutError:
            yield event.plain_result(
                "[hippocampus] mem stats timed out after "
                + str(self._COMMAND_HARD_TIMEOUT) + "s")
            return
        except Exception as ex:
            yield event.plain_result("[hippocampus] mem stats error: " + repr(ex))
            return
        yield event.plain_result(text)

    @filter.command("mem search")
    async def cmd_mem_search(self, event: AstrMessageEvent, arg: str):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem search", event, (arg,), {}):
            yield r

    @filter.command("mem model")
    async def cmd_mem_model(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem model", event, (), {}):
            yield r

    @filter.command("mem model use embedding")
    async def cmd_mem_use_emb(self, event: AstrMessageEvent, name: str):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem model use embedding", event, (name,), {}):
            yield r

    @filter.command("mem model use llm")
    async def cmd_mem_use_llm(self, event: AstrMessageEvent, name: str):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem model use llm", event, (name,), {}):
            yield r

    @filter.command("mem rebuild")
    async def cmd_mem_rebuild(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem rebuild", event, (), {}):
            yield r

    @filter.command("mem prospective")
    async def cmd_mem_prospective(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem prospective", event, (), {}):
            yield r

    @filter.command("mem session")
    async def cmd_mem_session(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem session", event, (), {}):
            yield r

    @filter.command("mem profile")
    async def cmd_mem_profile(self, event: AstrMessageEvent,
                              actor: str = ""):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem profile", event, (), {"actor": actor}):
            yield r

    @filter.command("mem persona")
    async def cmd_mem_persona(self, event: AstrMessageEvent,
                              actor: str = ""):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem persona", event, (), {"actor": actor}):
            yield r

    @filter.command("mem activate")
    async def cmd_mem_activate(self, event: AstrMessageEvent,
                               seeds: str = ""):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem activate", event, (), {"seeds": seeds}):
            yield r

    @filter.command("mem remember")
    async def cmd_mem_remember(self, event: AstrMessageEvent,
                               arg: str = ""):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem remember", event, (), {"arg": arg}):
            yield r

    @filter.command("mem cluster")
    async def cmd_mem_cluster(self, event: AstrMessageEvent, eid: str):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem cluster", event, (eid,), {}):
            yield r

    @filter.command("mem cluster-list")
    async def cmd_mem_cluster_list(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem cluster-list", event, (), {}):
            yield r

    @filter.command("mem confidence")
    async def cmd_mem_confidence(self, event: AstrMessageEvent,
                                 query: str = ""):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem confidence", event, (), {"query": query}):
            yield r

    @filter.command("mem decaycurve")
    async def cmd_mem_decaycurve(self, event: AstrMessageEvent,
                                 arg: str = ""):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem decaycurve", event, (), {"arg": arg}):
            yield r

    @filter.command("mem consolidate")
    async def cmd_mem_consolidate(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem consolidate", event, (), {}):
            yield r

    @filter.command("mem diary")
    async def cmd_mem_diary(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem diary", event, (), {}):
            yield r

    @filter.command("mem forget")
    async def cmd_mem_forget(self, event: AstrMessageEvent, eid: str):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem forget", event, (eid,), {}):
            yield r

    @filter.command("mem export")
    async def cmd_mem_export(self, event: AstrMessageEvent, path: str):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem export", event, (path,), {}):
            yield r

    @filter.command("mem import")
    async def cmd_mem_import(self, event: AstrMessageEvent, path: str):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem import", event, (path,), {}):
            yield r

    @filter.command("mem graph")
    async def cmd_mem_graph(self, event: AstrMessageEvent, query: str):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem graph", event, (query,), {}):
            yield r

    @filter.command("mem narrative")
    async def cmd_mem_narrative(self, event: AstrMessageEvent,
                                topic: str):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem narrative", event, (topic,), {}):
            yield r

    @filter.command("mem debug")
    async def cmd_mem_debug(self, event: AstrMessageEvent,
                            query: str = ""):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem debug", event, (), {"query": query}):
            yield r

    @filter.command("mem replay")
    async def cmd_mem_replay(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem replay", event, (), {}):
            yield r

    @filter.command("mem valence")
    async def cmd_mem_valence(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem valence", event, (), {}):
            yield r

    @filter.command("mem streams")
    async def cmd_mem_streams(self, event: AstrMessageEvent):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
                "mem streams", event, (), {}):
            yield r

    @filter.command("mem tier")
    async def cmd_mem_tier(self, event: AstrMessageEvent, arg: str = ""):
        await self._stamp_persona(event)
        async for r in self._dispatch_command(
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

    # ---------- v1.75 public cross-plugin API ----------
    def store_diary_line(self, persona_id: str, date: str, content: str, *,
                         mood: str = "", signature: str = "",
                         source_refs: list | None = None,
                         source: str = "external") -> str:
        """Stable cross-plugin API: persist one life diary line."""
        if self.service is None:
            return ""
        try:
            return self.service.store_diary_line(
                persona_id, date, content, mood=mood, signature=signature,
                source_refs=source_refs, source=source)
        except Exception as exc:
            print("[hippocampus] store_diary_line failed: " + repr(exc))
            return ""

    def query_recent_memory(self, persona_id: str, query: str = "",
                            k: int = 5, since: float = 0.0) -> list:
        """Stable cross-plugin API: recent memory / recall for a persona."""
        if self.service is None:
            return []
        try:
            return self.service.query_recent_memory(
                persona_id, query=query, k=k, since=since)
        except Exception as exc:
            print("[hippocampus] query_recent_memory failed: " + repr(exc))
            return []

    def claim_task(self, persona_id: str, task_kind: str,
                   holder: str = "", ttl_seconds: int = 300) -> bool:
        """Stable cross-plugin API: claim a per-persona task lease."""
        if self.service is None:
            return False
        try:
            return self.service.claim_task(
                persona_id, task_kind, holder=holder, ttl_seconds=ttl_seconds)
        except Exception as exc:
            print("[hippocampus] claim_task failed: " + repr(exc))
            return False

    def renew_task(self, persona_id: str, task_kind: str,
                   holder: str = "", ttl_seconds: int = 300) -> bool:
        """Stable cross-plugin API: renew a held task lease."""
        if self.service is None:
            return False
        try:
            return self.service.renew_task(
                persona_id, task_kind, holder=holder, ttl_seconds=ttl_seconds)
        except Exception as exc:
            print("[hippocampus] renew_task failed: " + repr(exc))
            return False

    def release_task(self, persona_id: str, task_kind: str,
                     holder: str = "") -> bool:
        """Stable cross-plugin API: release a held task lease."""
        if self.service is None:
            return False
        try:
            return self.service.release_task(persona_id, task_kind, holder=holder)
        except Exception as exc:
            print("[hippocampus] release_task failed: " + repr(exc))
            return False

    def task_lease_owner(self, persona_id: str, task_kind: str) -> str:
        """Stable cross-plugin API: current lease holder ('' when free)."""
        if self.service is None:
            return ""
        try:
            return self.service.task_lease_owner(persona_id, task_kind)
        except Exception as exc:
            print("[hippocampus] task_lease_owner failed: " + repr(exc))
            return ""

    def store_event(self, persona_id: str, platform: str, session_id: str,
                    ts: float, kind: str, payload: dict | None = None,
                    source: str = "external") -> str:
        """Stable cross-plugin API: persist one life event."""
        if self.service is None:
            return ""
        try:
            return self.service.store_event(
                persona_id, platform, session_id, ts, kind,
                payload=payload, source=source)
        except Exception as exc:
            print("[hippocampus] store_event failed: " + repr(exc))
            return ""

    def add_note(self, persona_id: str, note: dict,
                 source: str = "external") -> str:
        """Stable cross-plugin API: persist one life note."""
        if self.service is None:
            return ""
        try:
            return self.service.add_note(persona_id, note, source=source)
        except Exception as exc:
            print("[hippocampus] add_note failed: " + repr(exc))
            return ""

    def query_memory(self, persona_id: str, query: str, k: int = 5,
                     memory_types: list | None = None) -> list:
        """Stable cross-plugin API: persona-scoped memory query."""
        if self.service is None:
            return []
        try:
            return self.service.query_memory(
                persona_id, query, k=k, memory_types=memory_types)
        except Exception as exc:
            print("[hippocampus] query_memory failed: " + repr(exc))
            return []

    def search(self, persona_id: str, query: str, k: int = 5,
               memory_types: list | None = None) -> list:
        """Stable cross-plugin API: persona-scoped memory search."""
        if self.service is None:
            return []
        try:
            return self.service.search(
                persona_id, query, k=k, memory_types=memory_types)
        except Exception as exc:
            print("[hippocampus] search failed: " + repr(exc))
            return []

    def upsert_entity(self, persona_id: str, entity: dict) -> str:
        """Stable cross-plugin API: upsert one entity."""
        if self.service is None:
            return ""
        try:
            return self.service.upsert_entity(persona_id, entity)
        except Exception as exc:
            print("[hippocampus] upsert_entity failed: " + repr(exc))
            return ""

    def link_entities(self, persona_id: str, src_entity_id: str,
                      relation: str, dst_entity_id: str,
                      weight: float = 1.0) -> bool:
        """Stable cross-plugin API: upsert one typed edge."""
        if self.service is None:
            return False
        try:
            return self.service.link_entities(
                persona_id, src_entity_id, relation, dst_entity_id,
                weight=weight)
        except Exception as exc:
            print("[hippocampus] link_entities failed: " + repr(exc))
            return False

    def list_entities(self, persona_id: str, limit: int = 500) -> list:
        if self.service is None:
            return []
        try:
            return self.service.list_entities(persona_id, limit=limit)
        except Exception as exc:
            print("[hippocampus] list_entities failed: " + repr(exc))
            return []

    def list_links(self, persona_id: str, limit: int = 1000) -> list:
        if self.service is None:
            return []
        try:
            return self.service.list_links(persona_id, limit=limit)
        except Exception as exc:
            print("[hippocampus] list_links failed: " + repr(exc))
            return []

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
        # v1.76.15: stop the backup daemon so hot reload cannot leave an
        # old scheduler running against a closed/reopened database.
        initializer = getattr(self, "_initializer", None)
        if initializer is not None and hasattr(initializer, "shutdown"):
            try:
                initializer.shutdown()
            except Exception as e:
                print(f"[hippocampus] initializer shutdown error: {e!r}")
        if self.service is not None:
            try:
                await self.service.stop()
            except Exception as e:
                print(f"[hippocampus] terminate error: {e!r}")
