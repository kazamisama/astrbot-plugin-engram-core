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
from .recall import emb_bridge_for_context, EMB_BRIDGE_TIMEOUT
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
        self._backup_stop: threading.Event | None = None
        # v1.76.16: set when the astrmock embedding activation had to be
        # deferred to _activate_embedding_when_ready (see _install_bridges).
        self._pending_emb_activation = False

    def initialize(self, config_dict: dict | None = None) -> None:
        # v1.76.16 CRITICAL: use the config the host handed us.
        #
        # This used to do `cfg_dict = self.context.get_config("hippocampus")`,
        # which does NOT return this plugin's config. Context.get_config(umo)
        # takes a *session* id, and AstrBotConfigManager.get_conf() looks the
        # umo up in its routing table and falls back to confs["default"] -- the
        # GLOBAL AstrBot config (cmd_config.json). Passing "hippocampus" thus
        # returned the global config: every field in this plugin's WebUI panel
        # was silently ignored and MemoryConfig defaults applied instead. That
        # is why the live bot logged "summary skipped: LLM unavailable and
        # fallback disabled" while the config file said
        # summary_fallback_enabled = true, and why tier_recall_include_cold
        # never took effect (cold memories stayed excluded from recall).
        cfg_dict = config_dict if isinstance(config_dict, dict) else {}
        if not cfg_dict:
            cfg_dict = self._load_own_config_dict()
        # v1.76.16: record AstrBot's own event loop BEFORE anything probes a
        # host provider. The host embedding provider's aiohttp objects belong
        # to this loop; awaiting them anywhere else hangs until the cap.
        try:
            from hippocampus._async_bridge import set_host_loop
            set_host_loop(asyncio.get_running_loop())
        except RuntimeError:
            pass          # sync init path: no host loop to bind to
        except Exception as exc:
            print(f"[hippocampus] set_host_loop failed: {exc!r}")
        # B9: respect bot_language (default "zh", also accepts "en")
        # so t("help.full_text") and similar resolve to the right
        # language at plugin startup. Re-init is idempotent.
        try:
            i18n_init(str(cfg_dict.get("bot_language", "zh")))
        except Exception:
            i18n_init("zh")
        self._init_service(cfg_dict)
        self._log_effective_config()
        if self.service is not None:
            print(banner_text(self.service))
            self._install_bridges()
            self._start_background()
            self._register_agent_tools()
            # B10: kick off backup scheduler (no-op if interval=0 or disabled)
            self._start_backup_scheduler()

    def _load_own_config_dict(self) -> dict:
        """Read this plugin's own config file as a fallback.

        Only used when the host did not pass `config` to the Star constructor
        (see initialize() for why context.get_config() must not be used here).
        The file lives at <astrbot root>/data/config/<plugin name>_config.json.
        """
        try:
            here = os.path.dirname(os.path.abspath(__file__))        # .../handlers
            plugin_dir = os.path.dirname(here)                       # .../plugins/<name>
            root = os.path.dirname(os.path.dirname(os.path.dirname(plugin_dir)))
            name = os.path.basename(plugin_dir)
            path = os.path.join(root, "data", "config", name + "_config.json")
            if not os.path.exists(path):
                return {}
            import json
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                print("[hippocampus] config read from " + path)
                return data
        except Exception as e:
            print("[hippocampus] own-config load failed: " + repr(e))
        return {}

    def _log_effective_config(self) -> None:
        """Log the settings that actually took effect.

        The WebUI panel and this plugin's effective config can silently
        diverge (they did for months), and that divergence is invisible in
        every other log line. One line here makes it checkable at a glance.
        """
        try:
            c = getattr(self.service, "cfg", None)
            if c is None:
                return

            def g(name):
                return getattr(c, name, "<missing>")

            print("[hippocampus] effective config: summary_mode=" + str(g("summary_mode_enabled"))
                  + " min_msgs=" + str(g("summary_min_messages"))
                  + " max_msgs=" + str(g("summary_max_messages"))
                  + " fallback=" + str(g("summary_fallback_enabled"))
                  + " include_cold=" + str(g("tier_recall_include_cold"))
                  + " tiering=" + str(g("tiering_enabled"))
                  + " decay=" + str(g("memory_decay_enabled"))
                  + " embedding_dim=" + str(g("embedding_dim")))
        except Exception as e:
            print("[hippocampus] effective-config log failed: " + repr(e))

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

        async def _llm_bridge_coro(system: str, user: str, **kw) -> str:
            try:
                provider = None
                if llm_pid:
                    getter = getattr(self.context, "get_provider_by_id", None)
                    if getter is not None:
                        provider = getter(llm_pid)
                if provider is None:
                    provider = self.context.get_using_provider()
                # v1.76.14: hard-bounded INSIDE the bridge. AstrBot's
                # provider.text_chat may carry no timeout of its own; a
                # run_sync caller-side cap alone releases the caller but
                # can leave this coroutine wedged on the shared worker
                # loop. 45s (LLM bridge cap is 60s) keeps the worker
                # healthy while still allowing slow completions.
                resp = await asyncio.wait_for(
                    provider.text_chat(system_prompt=system, prompt=user, **kw),
                    timeout=45.0)
                if hasattr(resp, "text"):
                    return resp.text or ""
                if hasattr(resp, "completion_text"):
                    return resp.completion_text or ""
                return str(resp)
            except asyncio.TimeoutError:
                print("[hippocampus] LLM bridge timed out after 45.0s")
                return ""
            except Exception as e:
                print(f"[hippocampus] LLM bridge error: {e!r}")
                return ""

        def _llm_bridge(system: str, user: str, **kw) -> str:
            """Sync LLM bridge; host loop first, worker loop as fallback.

            v1.76.16: same root cause as the embedding bridge below. AstrBot's
            chat provider also talks over an aiohttp session bound to AstrBot's
            loop, so driving it from the private worker loop hung until the cap
            -- the 12x "LLM bridge timed out after 45.0s" that made the
            summarizer give up and (with summary_fallback_enabled off) discard
            whole conversations instead of storing them.
            """
            from hippocampus._async_bridge import (DEFAULT_LLM_SYNC_TIMEOUT,
                                                   call_on_host_loop,
                                                   host_loop_usable, run_sync)
            # Keep this below ProxyLLMProvider's own DEFAULT_LLM_SYNC_TIMEOUT
            # caller cap so the provider's 45s inner bound surfaces first
            # instead of racing the outer wait.
            cap = max(5.0, DEFAULT_LLM_SYNC_TIMEOUT - 10.0)
            if host_loop_usable():
                return call_on_host_loop(
                    _llm_bridge_coro(system=system, user=user, **kw),
                    timeout=cap)
            return run_sync(
                _llm_bridge_coro(system=system, user=user, **kw),
                timeout=cap)

        def _emb_bridge_sync(text: str) -> list[float]:
            """Sync embedding bridge (v1.76.16).

            Prefer AstrBot's OWN loop. AstrBot's embedding provider wraps an
            aiohttp ClientSession, and its connection objects belong to the
            loop they were created on: awaiting them from our private worker
            loop does not fail fast, it hangs until the caller's cap. That is
            what made this bridge time out (10s at boot, 59x at runtime) and
            left the plugin pinned to its 64-dim `hash` placeholder while the
            store held 4096-dim vectors.

            The worker-loop path is kept only for the cases where the host
            loop genuinely cannot be used: the sync init path (no host loop),
            or a call already ON the host loop, where blocking on the result
            would deadlock the loop that has to run the coroutine.
            """
            from hippocampus._async_bridge import (call_on_host_loop,
                                                   host_loop_usable, run_sync)
            if host_loop_usable():
                return call_on_host_loop(
                    emb_bridge_for_context(self.context, text,
                                           provider_id=emb_pid),
                    timeout=EMB_BRIDGE_TIMEOUT)
            return run_sync(
                emb_bridge_for_context(self.context, text, provider_id=emb_pid),
                timeout=EMB_BRIDGE_TIMEOUT)

        try:
            self.service.register_llm(
                "astrmock", ProxyLLMProvider("astrmock", _llm_bridge))
        except Exception as e:
            print(f"[hippocampus] register astrmock llm failed: {e!r}")

        # v1.76.16: skip the one-shot dim probe when a host loop exists. The
        # probe would run inside the plugin's synchronous __init__, i.e. with
        # the host loop BLOCKED inside it, so it could only ever time out.
        # Activation is decided after __init__ returns, on the host loop.
        from hippocampus._async_bridge import get_host_loop
        defer_activation = get_host_loop() is not None

        emb_provider = None
        try:
            emb_provider = ProxyEmbeddingProvider(
                "astrmock", _emb_bridge_sync, probe=not defer_activation)
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
            want_astrmock = (
                self.service.cfg.embedding_name == "hash"
                and self.service.registry.has_embedding("astrmock")
                and emb_provider is not None)
            if want_astrmock and defer_activation:
                # v1.76.16: a running host loop means we are inside the
                # plugin's synchronous __init__, with that loop blocked, so a
                # probe here cannot succeed. Hand the decision to a task that
                # runs once __init__ has returned.
                self._pending_emb_activation = True
            elif want_astrmock:
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
                # v1.76.16: __init__ is synchronous, so the host loop is
                # blocked until it returns -- and the embedding probe can only
                # answer once it is free. This task runs right after.
                if getattr(self, "_pending_emb_activation", False):
                    loop.create_task(self._activate_embedding_when_ready())
            else:
                asyncio.run(self.service.start())
        except Exception as e:
            print(f"[hippocampus] start background task failed: {e!r}")

    async def _activate_embedding_when_ready(self) -> None:
        """Activate the host embedding provider once the host loop is free.

        v1.76.16. Why this exists: the astrmock activation used to be a single
        probe inside the plugin's synchronous __init__. At that moment AstrBot's
        event loop is blocked inside __init__ itself, so awaiting the host
        embedding provider could only ever time out -- and a failed probe
        silently left the plugin on its 64-dim `hash` placeholder even though
        the store held 4096-dim vectors from the host provider. The plugin then
        stayed that way for the whole session (no retry), which is why vector
        ("same meaning") recall could not see those memories.

        Here we run as an asyncio task ON the host loop, so `await` genuinely
        executes the provider's coroutine on the loop its objects belong to and
        the probe can actually answer. Each attempt is bounded, and the
        v1.76.4 guard is preserved: we only switch when the probe returns a
        usable vector, so a non-working provider can never become the default.
        """
        svc = self.service
        if svc is None:
            return
        cfg = svc.cfg
        pid = getattr(cfg, "embedding_provider_id", "") or ""
        delays = (0.5, 5.0, 20.0, 60.0)
        for attempt, delay in enumerate(delays, start=1):
            await asyncio.sleep(delay)
            if not (cfg.embedding_name == "hash"
                    and svc.registry.has_embedding("astrmock")):
                return                       # operator pinned another provider
            if svc.current_embedding() == "astrmock":
                return                       # already active (e.g. /mem model)
            try:
                vec = await asyncio.wait_for(
                    emb_bridge_for_context(self.context, "dim-probe",
                                           provider_id=pid),
                    timeout=EMB_BRIDGE_TIMEOUT + 5.0)
            except asyncio.TimeoutError:
                vec = []
            except Exception as e:
                print("[hippocampus] astrmock activation probe error: "
                      + repr(e))
                vec = []
            if not vec:
                if attempt < len(delays):
                    continue
                break
            prev = cfg.auto_rebuild_on_switch
            # Never rebuild here: set_embedding would re-embed the whole store
            # synchronously, and doing that on the host loop would put us back
            # on the worker-loop path we are fixing.
            cfg.auto_rebuild_on_switch = False
            try:
                old = svc.current_embedding()
                svc.set_embedding("astrmock")
                legacy = svc.store.count_by_embedding_model(
                    old, include_forgotten=False)
                print("[hippocampus] astrmock embedding activated on the host "
                      "loop (" + str(len(vec)) + "-dim, was " + old + ")"
                      + ("; " + str(legacy) + " older vectors were not "
                         "rebuilt. FTS recall still covers them; run /mem "
                         "rebuild to restore vector recall." if legacy else ""))
            except Exception as e:
                print(f"[hippocampus] astrmock activation failed: {e!r}")
            finally:
                cfg.auto_rebuild_on_switch = prev
            return
        print("[hippocampus] astrmock embedding probe returned nothing after "
              + str(len(delays)) + " attempts on the host loop; keeping "
              + svc.current_embedding()
              + ". Vector recall will only see vectors in that space "
              "(keyword/FTS recall still covers everything).")

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
        stop = threading.Event()
        self._backup_stop = stop

        def _loop():
            if stop.wait(first_delay):
                return
            while not stop.is_set():
                try:
                    if self.backup_manager is not None:
                        self.backup_manager.create(reason="auto")
                        self.backup_manager.cleanup(
                            keep_last=cfg.backup_keep_last,
                            keep_weekly=cfg.backup_keep_weekly,
                            keep_monthly=cfg.backup_keep_monthly)
                except Exception as e:
                    print("[hippocampus] backup loop error: " + repr(e))
                if stop.wait(interval_s):
                    return

        t = threading.Thread(target=_loop, daemon=True, name="hippocampus-backup")
        t.start()
        self._backup_thread = t

    def shutdown(self) -> None:
        """Stop the backup scheduler thread (idempotent)."""
        stop = getattr(self, "_backup_stop", None)
        if stop is not None:
            try:
                stop.set()
            except Exception:
                pass
        thread = getattr(self, "_backup_thread", None)
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=5.0)
            except Exception:
                pass
        self._backup_thread = None
