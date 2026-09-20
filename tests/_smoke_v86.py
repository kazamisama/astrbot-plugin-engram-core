"""Smoke v1.76.16 memory-recall repair batch.

Covers the two defects that made "memory is not recalled" happen at once, plus
the config block that was being ignored:

  D1  HippocampalStore.decay_pass compounded the decay: it multiplied the
      already-decayed strength by exp(-(now - anchor)/tau) on every sweep while
      never advancing the anchor, so a memory anchored N sweeps back lost
      exp(-D*N^2/2/tau) instead of exp(-D*N/tau). With the 1800s maintenance
      loop that cost a memory exp(-48*age/tau) per DAY, driving every row below
      tier_cold_strength_floor within ~2-3 days -- and because cold is excluded
      from normal recall, the row was never touched again and stayed at 0.0.

      Asserted here: one simulated day of sweeps costs one day of decay,
      independent of the engram's age.

  D2  EMB_BRIDGE_TIMEOUT must stay below InjectHandler._INJECT_HARD_TIMEOUT,
      otherwise the whole-injection cap fires first and the hook drops the
      injection entirely instead of degrading to the FTS/keyword route.

  D3  ConfigManager must flatten `summary_settings`; AstrBot writes that block
      as a nested object, and while it was missing from _GROUP_KEYS every field
      in it silently fell back to the MemoryConfig default.
"""
import math
import os
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- shim astrbot so handlers/ and main.py import outside a running AstrBot ---
_astrbot = types.ModuleType("astrbot")
_api = types.ModuleType("astrbot.api")
_event = types.ModuleType("astrbot.api.event")
_star = types.ModuleType("astrbot.api.star")


class AstrMessageEvent:  # typing only; hooks use plain attributes
    pass


class Star:  # main.HippocampusStar subclasses this
    pass


class Context:  # typing only
    pass


def _register(*a, **k):
    def deco(cls):
        return cls
    return deco


class _MT:
    ALL = "all"


class _Filter:
    EventMessageType = _MT

    def event_message_type(self, *a, **k):
        def deco(fn):
            return fn
        return deco

    def command(self, *a, **k):
        def deco(fn):
            return fn
        return deco

    @staticmethod
    def on_llm_request(*a, **k):
        def deco(fn):
            return fn
        return deco

    @staticmethod
    def on_llm_response(*a, **k):
        def deco(fn):
            return fn
        return deco


_event.AstrMessageEvent = AstrMessageEvent
_event.filter = _Filter()
_event.EventMessageType = _MT
_star.Star = Star
_star.Context = Context
_star.register = _register
_api.event = _event
_api.star = _star
_astrbot.api = _api
sys.modules.setdefault("astrbot", _astrbot)
sys.modules.setdefault("astrbot.api", _api)
sys.modules.setdefault("astrbot.api.event", _event)
sys.modules.setdefault("astrbot.api.star", _star)

SWEEP = 1800.0          # memory_decay_interval_seconds default
IMPORTANCE_MOD = 4.0    # decay_pass default modulator


def _service(tmp_db):
    from hippocampus import MemoryService, MemoryConfig
    cfg = MemoryConfig(sqlite_path=tmp_db, embedding_name="hash", llm_name="rule")
    cfg.memory_decay_enabled = False          # drive decay by hand
    return cfg, MemoryService(cfg)


def test_sweeps_do_not_compound():
    """D1: N sweeps cost N*D of decay, not the engram's whole age each time."""
    from hippocampus.types import Engram

    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    cfg, svc = _service(db)
    try:
        now = time.time()
        ages = {"d1": 1.0, "d3": 3.0, "d30": 30.0}
        ids = {}
        for name, age in ages.items():
            ts = now - age * 86400.0
            e = Engram(content=name, summary=name, actor_id="a", strength=1.0,
                       importance=0.6, created_at=ts, last_accessed=ts)
            svc.store.upsert(e)
            ids[name] = e.id

        # First pass on a store with no recorded sweep time is age-based, by
        # design (that is what makes a brand-new DB decay by real age once).
        svc.store.decay_pass(cfg.decay_tau_base, 0.05)
        for name in ages:
            svc.store.get(ids[name])          # sanity: rows readable

        # Re-arm every row at full strength and forget the sweep time so the
        # gap of each simulated sweep is exactly SWEEP seconds.
        for name, age in ages.items():
            e = svc.store.get(ids[name])
            e.strength = 1.0
            e.created_at = time.time() - age * 86400.0
            e.last_accessed = e.created_at
            svc.store.upsert(e)
        svc.store._meta_set("decay:last_pass_at", repr(0.0))

        sweeps = int(86400.0 / SWEEP)          # one simulated day of the loop
        for _ in range(sweeps):
            svc.store.decay_pass(cfg.decay_tau_base, 0.05, elapsed_seconds=SWEEP)

        tau_eff = cfg.decay_tau_base * (1.0 + IMPORTANCE_MOD * 0.6)
        expected = math.exp(-86400.0 / tau_eff)     # exactly one day of decay
        for name in ages:
            got = svc.store.get(ids[name]).strength
            assert abs(got - expected) < 0.02, (
                f"{name}: {sweeps} sweeps gave {got:.4f}, expected ~{expected:.4f} "
                "(one day of decay)")
            print(f"  {name}: age={ages[name]:>4}d  after 1d of sweeps "
                  f"strength={got:.4f} (expected {expected:.4f})")

        # The regression itself: an old memory used to be annihilated.
        cold_floor = cfg.tier_cold_strength_floor
        assert svc.store.get(ids["d3"]).strength > cold_floor, \
            "a 3-day-old memory must not fall below the cold floor in one day"
        assert svc.store.get(ids["d30"]).strength > cold_floor, \
            "a 30-day-old memory must not fall below the cold floor in one day"
        print("  D1 compounding decay: OK")
    finally:
        try: svc.close()
        except Exception: pass
        try: os.remove(db)
        except Exception: pass


def test_sweep_time_is_recorded():
    """D1: the gap is taken from hippo_meta so it survives restarts."""
    from hippocampus.types import Engram

    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    cfg, svc = _service(db)
    try:
        assert svc.store._meta_get("decay:last_pass_at") is None
        svc.store.decay_pass(cfg.decay_tau_base, 0.05)
        first = svc.store._meta_get("decay:last_pass_at")
        assert first is not None, "decay_pass must record the sweep time"
        time.sleep(0.02)
        svc.store.decay_pass(cfg.decay_tau_base, 0.05)
        second = svc.store._meta_get("decay:last_pass_at")
        assert float(second) > float(first), "sweep time must advance"

        # a memory younger than the gap must not be aged by the whole gap
        now = time.time()
        e = Engram(content="young", summary="young", actor_id="a", strength=1.0,
                   importance=0.0, created_at=now, last_accessed=now)
        svc.store.upsert(e)
        svc.store.decay_pass(cfg.decay_tau_base, 0.05, elapsed_seconds=10 * 365 * 86400.0)
        got = svc.store.get(e.id).strength
        assert got > 0.99, f"a just-created memory was aged by a 10y gap: {got}"
        print(f"  young row survived a 10y-gap sweep: strength={got:.4f}")
        print("  D1 sweep-time bookkeeping: OK")
    finally:
        try: svc.close()
        except Exception: pass
        try: os.remove(db)
        except Exception: pass


def test_embed_timeout_below_inject_timeout():
    """D2: the inner embedding cap must be able to win over the outer one."""
    from handlers.recall import EMB_BRIDGE_TIMEOUT
    from handlers.event.inject import InjectHandler

    outer = InjectHandler._INJECT_HARD_TIMEOUT
    assert EMB_BRIDGE_TIMEOUT < outer, (
        f"EMB_BRIDGE_TIMEOUT ({EMB_BRIDGE_TIMEOUT}) must be < "
        f"_INJECT_HARD_TIMEOUT ({outer}); equality means the whole injection is "
        "dropped instead of degrading to FTS")
    print(f"  EMB_BRIDGE_TIMEOUT={EMB_BRIDGE_TIMEOUT} < "
          f"_INJECT_HARD_TIMEOUT={outer}: OK")


def test_summary_settings_are_applied():
    """D3: ConfigManager must hoist the nested summary_settings block."""
    from hippocampus.config_manager import ConfigManager

    raw = {
        "provider_settings": {"embedding_dim": 4096},
        "summary_settings": {
            "summary_fallback_enabled": True,
            "summary_min_messages": 7,
            "summary_idle_seconds_private": 123.0,
            "summary_mode_enabled": False,
        },
    }
    cfg = ConfigManager(raw).memory_config
    assert cfg.embedding_dim == 4096
    assert cfg.summary_fallback_enabled is True, "summary_fallback_enabled ignored"
    assert cfg.summary_min_messages == 7, "summary_min_messages ignored"
    assert cfg.summary_idle_seconds_private == 123.0
    assert cfg.summary_mode_enabled is False, "top-level key should still win"
    print("  D3 summary_settings flattened: OK")


def test_session_aggregate_min_chars_accepts_zero():
    """The field's range used to exclude its own default (0), warning on load."""
    from hippocampus.config_manager import ConfigManager
    cfg = ConfigManager({"session_aggregate_min_chars": 0}).memory_config
    assert cfg.session_aggregate_min_chars == 0
    print("  session_aggregate_min_chars=0 accepted without fallback: OK")


def test_below_min_window_is_summarized_not_dropped():
    """A sub-min conversation window must be summarized, never discarded.

    `_settle_idle_buf` used to `pop()` the buffer when a channel stayed below
    `summary_min_messages` past the grace window, silently throwing the whole
    window away. The live store showed a day with 306 captured messages and 0
    new engrams. Here we pin the replacement behaviour: held while inside the
    grace (so short bursts still merge), summarized at expiry.
    """
    from hippocampus.config_manager import ConfigManager
    from hippocampus.conversation_buffer import ConversationBuffer

    class Clock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

        def tick(self, dt):
            self.t += dt

    clk = Clock()
    out = []
    # use the nested shape AstrBot actually writes
    cfg = ConfigManager({"summary_settings": {
        "summary_idle_seconds_private": 300.0,
        "summary_min_messages": 5,
        "summary_min_messages_grace_seconds": 600.0,
    }}).memory_config
    buf = ConversationBuffer(cfg, out.append, now_fn=clk)

    for i in range(2):                      # below the minimum of 5
        buf.feed({"channel_id": "c1", "chat_type": "private",
                  "actor_id": "u", "content": "m" + str(i)})

    clk.tick(301)                           # idle, still inside the grace
    buf.flush_idle_now()
    assert out == [], "a sub-min window must be held, not flushed early"
    assert buf.buffered_channel_count() == 1

    clk.tick(400)                           # grace elapsed
    buf.flush_idle_now()
    assert buf.buffered_channel_count() == 0, "the window must be released"
    assert len(out) == 1, "the window must be summarized at grace expiry, not dropped"
    assert [ln.content for ln in out[0].lines] == ["m0", "m1"], \
        "the summarized window must still carry its messages"
    assert out[0].chat_type == "private"
    print("  sub-min window held, then summarized (never dropped): OK")


def test_summary_min_messages_is_not_a_retention_policy():
    """min=1 flushes on idle immediately; nothing is ever discarded."""
    from hippocampus.config import MemoryConfig
    from hippocampus.conversation_buffer import ConversationBuffer

    class Clock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

    clk = Clock()
    out = []
    cfg = MemoryConfig()
    cfg.summary_idle_seconds_group = 600.0
    cfg.summary_min_messages = 1
    cfg.summary_min_messages_grace_seconds = 21600.0
    buf = ConversationBuffer(cfg, out.append, now_fn=clk)
    buf.feed({"channel_id": "g", "chat_type": "group", "actor_id": "a", "content": "hi"})
    clk.t += 601.0
    buf.flush_idle_now()
    assert len(out) == 1 and out[0].channel_id == "g"
    print("  summary_min_messages=1 flushes on first idle: OK")


def test_host_loop_routing():
    """The bridge must run host-bound coroutines ON the host loop.

    This is the fix for the embedding bridge hanging: AstrBot's provider holds
    aiohttp objects bound to AstrBot's loop, and awaiting them from the private
    worker loop timed out (10s at boot, 59x at runtime).
    """
    import asyncio
    import threading
    from hippocampus import _async_bridge as br

    # 1) no loop registered -> refuse (caller falls back to run_sync)
    br.set_host_loop(None)
    assert br.host_loop_usable() is False
    c = asyncio.sleep(0, result=1)
    try:
        br.call_on_host_loop(c, timeout=1.0)
        raise AssertionError("call_on_host_loop must refuse with no host loop")
    except RuntimeError:
        c.close()
    print("  refuses with no host loop: OK")

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True,
                              name="test-host-loop")
    thread.start()
    try:
        br.set_host_loop(loop)
        assert br.host_loop_usable() is True, "foreign thread must be allowed"

        async def _which_loop():
            return asyncio.get_running_loop()

        got = br.call_on_host_loop(_which_loop(), timeout=5.0)
        assert got is loop, "coroutine did NOT run on the host loop"
        print("  coroutine actually ran on the host loop: OK")

        # 2) from the host loop thread itself it must REFUSE. Blocking there
        #    would deadlock the loop that has to run the coroutine.
        async def _on_host():
            usable = br.host_loop_usable()
            inner = asyncio.sleep(0, result=1)
            try:
                br.call_on_host_loop(inner, timeout=0.5)
                return usable, None
            except RuntimeError as e:
                inner.close()
                return usable, str(e)

        fut = asyncio.run_coroutine_threadsafe(_on_host(), loop)
        on_host_usable, err = fut.result(timeout=5.0)
        assert on_host_usable is False, "must refuse when ON the host loop"
        assert err and "not usable" in err, err
        print("  refuses on the host-loop thread (no deadlock): OK")
    finally:
        br.set_host_loop(None)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)


def test_proxy_embedding_probe_flag():
    """probe=False must not touch the host during provider construction.

    The eager probe used to run inside the plugin's synchronous __init__, i.e.
    with the host loop blocked, so it could only time out -- and a failed probe
    silently pinned the plugin to the 64-dim `hash` placeholder.
    """
    from hippocampus.providers import ProxyEmbeddingProvider

    calls = []

    def fn(text):
        calls.append(text)
        return [0.0] * 4096

    eager = ProxyEmbeddingProvider("t", fn)
    assert calls == ["dim-probe"], calls
    assert eager.dim == 4096
    calls.clear()

    lazy = ProxyEmbeddingProvider("t", fn, probe=False)
    assert calls == [], "probe=False must not call the host at all"
    assert lazy.dim == 0, "dim must stay unresolved until a real embed()"
    vec = lazy.embed("hello")
    assert len(vec) == 4096 and lazy.dim == 4096, "dim must resolve lazily"
    assert calls == ["hello"], calls
    print("  probe=False skips the __init__ probe; dim resolves lazily: OK")


def test_deferred_activation_switches_to_astrmock():
    """End-to-end: initialize() inside a running loop must still activate.

    Simulates AstrBot's startup: initialize() runs synchronously inside a
    running loop (so the host loop is blocked during it), and the activation
    must then happen on that loop -- without rebuilding the store.
    """
    import asyncio
    import tempfile

    from handlers.init import PluginInitializer

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    probe_calls = []
    llm_calls = []

    class _HostEmb:
        async def get_embedding(self, text):
            probe_calls.append(text)
            return [0.5] * 8

    class _HostLLM:
        async def text_chat(self, system_prompt="", prompt="", **kw):
            llm_calls.append((system_prompt, prompt,
                              asyncio.get_running_loop(), dict(kw)))

            class _R:
                text = "hello-from-host"
            return _R()

    plugin_cfg = {
        "storage_settings": {"sqlite_path": db},
        "provider_settings": {"embedding_dim": 16},
        "memory_settings": {"enable_backup": False,
                            "diary_enabled": False,
                            "memory_decay_enabled": False},
    }

    class _Ctx:
        def get_config(self, key):
            # v1.76.16: Context.get_config(umo) returns the GLOBAL AstrBot
            # config, never the plugin's. initialize() must not consult it.
            # This deliberately returns a global-shaped config with a
            # conflicting value; if it were used, embedding_dim would be 64.
            return {"config_version": 1,
                    "provider_settings": {"embedding_dim": 64, "prompt_prefix": ""},
                    "agent_runner": {"x": 1}}

        def get_all_embedding_providers(self):
            return [_HostEmb()]

        def get_using_provider(self):
            return _HostLLM()

    loop = asyncio.new_event_loop()
    try:
        init = PluginInitializer(_Ctx())

        async def _startup():
            # exactly what HippocampusStar.__init__ passes now
            init.initialize(plugin_cfg)
            assert init.service.cfg.embedding_dim == 16, \
                ("initialize() must use the config the host passed, not "
                 "context.get_config() (which returns the global config)")
            assert "agent_runner" not in (init.service.cfg.extra or {}), \
                "the global config must not leak into cfg.extra"
            assert init._pending_emb_activation is True, \
                "activation must be deferred while __init__ holds the loop"
            for _ in range(200):            # let the deferred task run (first
                await asyncio.sleep(0.1)    # probe is at +5s in v1.76.17)
                if init.service.current_embedding() == "astrmock":
                    break
            svc = init.service
            # The LLM bridge goes through the same rework: call it the way the
            # plugin does (from a worker thread, off the host loop) and check
            # the host provider was awaited ON the host loop.
            out = await asyncio.to_thread(
                lambda: svc.llm.chat(system="sys", user="usr"))
            return out

        out = loop.run_until_complete(_startup())
        svc = init.service
        assert svc.current_embedding() == "astrmock", \
            f"expected astrmock, got {svc.current_embedding()}"
        assert probe_calls == ["dim-probe"], \
            f"activation must not re-embed the store: {probe_calls}"
        assert out == "hello-from-host", out
        assert llm_calls and llm_calls[0][2] is loop, \
            "the LLM bridge must await the host provider on the host loop"
        # AstrBot's OpenAI source retries each request 5x (inner) inside a
        # 10-iteration outer loop with a 120s HTTP timeout per attempt, so an
        # unbounded plugin call can spin for minutes. The bridge must pass its
        # own bound.
        kw = llm_calls[0][3]
        assert kw.get("request_max_retries") == 1, \
            f"the LLM bridge must bound provider retries, got {kw!r}"
        print("  deferred activation switched to astrmock on the host loop, "
              "without a rebuild: OK")
        print("  LLM bridge awaited the host provider on the host loop: OK")
        print("  LLM bridge bounds the provider retry chain "
              "(request_max_retries=1): OK")
        try:
            svc.close()
        except Exception:
            pass
    finally:
        # tear the background tasks down so the loop closes quietly
        try:
            from hippocampus._async_bridge import set_host_loop
            set_host_loop(None)
        except Exception:
            pass
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        loop.close()
        try:
            os.unlink(db)
        except Exception:
            pass


def test_conversation_buffer_survives_a_reload():
    """A plugin reload must not discard an unsummarized window.

    AstrBot reloaded the plugin 7x in one day on the live bot; every reload
    silently dropped the open windows (messages were captured but never became
    memory). The buffer now snapshots to disk and rehydrates on startup.
    """
    import tempfile

    from hippocampus import MemoryConfig
    from hippocampus.conversation_buffer import ConversationBuffer
    from hippocampus.conv_buffer_store import ConvBufferStore

    class Clock:
        def __init__(self, t=1000.0):
            self.t = t

        def __call__(self):
            return self.t

    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "conv_buffer.json")

    cfg = MemoryConfig()
    cfg.summary_idle_seconds_private = 1800.0
    cfg.summary_min_messages = 20          # the window below is far under it
    cfg.summary_min_messages_grace_seconds = 21600.0

    # --- process 1: three messages arrive, then the plugin reloads ---
    clk = Clock()
    first = []
    buf1 = ConversationBuffer(cfg, first.append, now_fn=clk)
    store1 = ConvBufferStore(path)
    for i in range(3):
        buf1.feed({"channel_id": "c1", "chat_type": "private",
                   "session_id": "s1", "platform": "test", "actor_id": "u",
                   "peer_actor_id": "u", "content": "m" + str(i)})
        store1.save(buf1.snapshot())

    saved = store1.load()
    assert "c1" in saved.get("channels", {}), saved
    assert len(saved["channels"]["c1"]["lines"]) == 3
    assert first == [], "must not have been summarized before the reload"

    # --- process 2: fresh buffer, restores from disk ---
    second = []
    buf2 = ConversationBuffer(cfg, second.append, now_fn=clk)
    n = buf2.restore(store1.load())
    assert n == 1, f"expected 1 restored window, got {n}"
    assert buf2.buffered_channel_count() == 1

    # the recovered window now behaves like any other: idle long enough and it
    # is summarized (the grace path summarizes instead of discarding).
    clk.t += 21601.0
    buf2.flush_idle_now()
    assert len(second) == 1, "the recovered window must be summarized"
    assert [ln.content for ln in second[0].lines] == ["m0", "m1", "m2"]
    assert second[0].session_id == "s1"
    print("  buffered window survived a simulated reload and was summarized: OK")

    # --- stale windows are not resurrected ---
    buf3 = ConversationBuffer(cfg, [], now_fn=lambda: 1000.0 + 30 * 86400.0)
    assert buf3.restore(store1.load(), max_age_seconds=14 * 86400.0) == 0, \
        "a 30-day-old window must not be restored"
    print("  stale snapshot window is skipped: OK")

    # --- store robustness ---
    assert ConvBufferStore(os.path.join(tmpdir, "nope.json")).load() == {}
    bad = os.path.join(tmpdir, "bad.json")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert ConvBufferStore(bad).load() == {}, "corrupt file must not raise"
    s = ConvBufferStore(path)
    assert s.save({"version": 1, "channels": {}}) is True
    assert s.load() == {"version": 1, "channels": {}}
    s.clear()
    assert s.load() == {}
    leftovers = [f for f in os.listdir(tmpdir) if f.startswith(".convbuf-")]
    assert leftovers == [], f"atomic write left temp files: {leftovers}"
    print("  ConvBufferStore atomic/robust (no temp leftovers): OK")

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_buffer_flush_updates_the_snapshot():
    """A summarized window must not come back from disk and be stored twice."""
    import tempfile

    from hippocampus import MemoryConfig
    from hippocampus.conversation_buffer import ConversationBuffer
    from hippocampus.conv_buffer_store import ConvBufferStore

    class Clock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

    tmpdir = tempfile.mkdtemp()
    store = ConvBufferStore(os.path.join(tmpdir, "conv_buffer.json"))
    clk = Clock()
    out = []
    cfg = MemoryConfig()
    cfg.summary_idle_seconds_group = 600.0
    cfg.summary_min_messages = 1
    cfg.summary_max_messages = 3

    def sink(rec):
        out.append(rec)
        store.save(buf.snapshot())        # mirrors ObserveHandler._sink

    buf = ConversationBuffer(cfg, sink, now_fn=clk)
    for i in range(3):
        buf.feed({"channel_id": "g", "chat_type": "group", "actor_id": "a",
                  "content": "x" + str(i)})
        store.save(buf.snapshot())

    assert len(out) == 1, "cap flush should have fired"
    disk = store.load()
    assert disk.get("channels", {}).get("g") is None, \
        "the flushed window must be gone from the snapshot"
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("  flushed window removed from the snapshot (no double-store): OK")


def test_terminate_never_runs_an_llm_on_the_event_loop():
    """terminate() must snapshot, not summarize.

    It used to call `convbuf.flush_all()` while running ON AstrBot's event
    loop, so `_sink -> summarize -> LLM` executed inline and blocked the loop
    for ~49s. AstrBot's own diagnostic caught it ("Event loop lag detected:
    49.375s (threshold 15.000s)") and three watchdog dumps showed the loop
    thread parked in
      main.py terminate -> flush_all -> _flush_key -> _sink -> summarize -> LLM.
    A 30-50s blocked loop cannot service the aiocqhttp WebSocket, so the API
    client drops and the next reply dies with
    `aiocqhttp.exceptions.ApiNotAvailable` -- messages never reach QQ.
    """
    import asyncio
    import main

    calls = {"persist": 0, "flush_all": 0, "flushed": 0}

    class _Buf:
        def flush_all(self):
            calls["flush_all"] += 1
            calls["flushed"] += 1
            raise AssertionError(
                "terminate() must not LLM-flush while holding the event loop")

    class _Obs:
        _conv_buffer = _Buf()
        _aggregator = None

        def persist_conv_buffer(self):
            calls["persist"] += 1

    star = main.HippocampusStar.__new__(main.HippocampusStar)
    star._observer = _Obs()
    star._idle_flush_task = None
    star._diary_task = None
    star._initializer = None
    star.service = None

    t0 = time.time()
    asyncio.run(star.terminate())
    dt = time.time() - t0

    assert calls["flush_all"] == 0, "terminate() must not call flush_all()"
    assert calls["flushed"] == 0
    assert calls["persist"] == 1, "terminate() must snapshot the buffer"
    assert dt < 2.0, f"terminate() blocked the loop for {dt:.2f}s"
    print(f"  terminate() snapshots without an LLM flush ({dt * 1000:.0f} ms): OK")


def test_star_accepts_the_host_config_kwarg():
    """HippocampusStar.__init__ must accept `config`, or the host drops it.

    AstrBot instantiates `star_cls_type(context=..., config=<plugin config>)`
    and, on TypeError, silently retries `star_cls_type(context=...)`. Taking
    only `context` therefore meant the host threw the plugin's whole config
    away on every load, and the plugin fell back to the global AstrBot config
    -- every WebUI setting was ignored.
    """
    import inspect

    import main
    from handlers.init import PluginInitializer

    sig = inspect.signature(main.HippocampusStar.__init__)
    assert "config" in sig.parameters, (
        "HippocampusStar.__init__ must accept the config kwarg, otherwise "
        "AstrBot's TypeError fallback drops the plugin config")
    assert sig.parameters["config"].default is None

    sig2 = inspect.signature(PluginInitializer.initialize)
    names = list(sig2.parameters)
    assert names[:2] == ["self", "config_dict"], names
    print("  HippocampusStar/initializer accept the host config kwarg: OK")


def main():
    test_sweeps_do_not_compound()
    test_sweep_time_is_recorded()
    test_summary_settings_are_applied()
    test_session_aggregate_min_chars_accepts_zero()
    test_below_min_window_is_summarized_not_dropped()
    test_summary_min_messages_is_not_a_retention_policy()
    test_conversation_buffer_survives_a_reload()
    test_buffer_flush_updates_the_snapshot()
    test_host_loop_routing()
    test_proxy_embedding_probe_flag()
    test_star_accepts_the_host_config_kwarg()
    test_deferred_activation_switches_to_astrmock()
    test_terminate_never_runs_an_llm_on_the_event_loop()
    # needs the astrbot shim, so run it last
    test_embed_timeout_below_inject_timeout()
    print("\nv1.76.16 memory-recall repair smoke: ALL PASS")


if __name__ == "__main__":
    main()
