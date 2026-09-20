# Changelog


## [Unreleased]

### Fixed
- **v1.76.22: the `<engram-context>` wrapper licensed the model to ignore its
  own memories.** The tag (v1.67.1) said only what a block is *not* —
  "injected background, not part of the user's actual message" — which fixed
  the original bug (the LLM answering each TextPart as a parallel user
  question) but left the model free to read "background" as "ignorable".

  The live model did exactly that. Its reasoning, after a memory had been
  recalled correctly and injected:

  > "The injected engram-context explicitly includes the sexual memory
  > summary… My rules are clear: **memories are background; the current
  > conversation governs**…"

  That sentence appears in **no** config file, persona card, or plugin on the
  machine — the model synthesised it from the wrapper's framing and then used
  it to decline the memory. So this is not a recall failure and not a
  persona-card conflict: the block was under-specified.

  Fix: `_SELF_RECALL_NOTE` prepends the missing half — these are the
  assistant's **own** memories and are meant to be used. It rides the **first
  block of a request only**, so it costs its ~48 tokens once per request
  rather than once per block, and it travels inside our own temp TextPart so
  no system-prompt template and no other plugin is involved.

  This is candidate **8.3** from `docs/TODO.md` §2.8, declined 2026-07-03,
  whose own "下次评估点" was *"LLM 把 `<engram-context>` 标签当作文本复读 /
  误解的首次真机报告"*. That report is what happened above — the evaluation
  point fired. All three decline reasons are avoided: no cross-plugin
  coordination, token cost held to the documented budget, and the trigger now
  exists.

  The wording deliberately **differs** from the declined 8.3 draft, which
  restated 「这是自动注入的背景」 — the very reading that caused the trouble.
  This states the positive half instead (「也不要当成可以忽略的背景——它是你
  自己的记忆」).

  `_wrap_engram()`'s default output is byte-identical to before, so callers
  and the public `strip_injected_blocks` contract are unaffected; the note
  sits after the opening tag and before the inner `[xxx]` label, so
  re-injection defence still matches noted and note-less blocks alike.

- **v1.76.21: the inject path dropped every key fact, and labelled long-term
  memory as "recent conversation".** Two defects in the same render loop of
  `InjectHandler._handle_inject_sync`:

  **① only `summary` was injected, never `content`.** Every engram's `content`
  is its summary followed by the summarizer's `- 要点` bullet lines — true for
  **399/399** rows on the live store (`content` starts with `summary` in every
  case; avg 371 vs 173 chars). The narrative went into the prompt and every
  extracted key fact was discarded at inject time.

  **② the block label was `[近期对话]`** ("recent conversation") while each
  entry carries its own real relative-time marker — `[3个月前]`, `[2 天前]` —
  on the very same line. The label flatly contradicted its own payload.
  Renamed to **`[长期记忆]`**.

  Fix: `_engram_body()` now prefers `content` and falls back to `summary`
  (so an engram-like object without `content` still works). New options:

  | option | default | meaning |
  |---|---|---|
  | `auto_inject_use_content` | `true` | inject the full body (summary + key facts) |
  | `auto_inject_content_max_chars` | `800` | soft per-engram cap, `0` = unlimited |

  The cap truncates on **whole-line boundaries**, so a bullet is never cut
  mid-sentence, and the leading summary line is always kept even when it alone
  exceeds the cap — a clipped summary is worth less than a slightly
  over-budget one. Continuation lines are indented (`  - 要点`) so a bullet
  cannot be misread as a separate memory entry, since only the first line
  carries the time marker.

  `[近期对话]` is **retained** in `_ENGRAM_INNER_LABELS` purely for the
  re-injection defence: blocks injected by <= v1.76.20 still have to be
  stripped, or they would accumulate across turns forever.

- **v1.76.20: no conversation's original text was ever retained — the raw
  transcript branch never fired once.** `store_summary` keeps the window's raw
  lines only when `importance >= source_retention_min_importance`, and that
  default was **0.7** while the summarizer stamps **every** engram with
  importance **0.6**. `0.6 >= 0.7` is never true, so `memory_sources` stayed
  empty for the plugin's whole life.

  Verified on the live store: **`memory_sources` = 0 rows** while 399 engrams
  existed, and **all 399 have importance exactly 0.6**. A months-ago memory's
  entire stored content is a ~100-character paraphrase, e.g.:

  ```
  Mortis与風見かずき互诉爱意，鼓励对方考试，道晚安。
  - 風見かずき对Mortis说"爱你"
  - Mortis回应"我也爱您"
  - Mortis提及复习和考试临近，鼓励对方
  - Mortis道晚安并再次表达爱意
  ```

  Nothing anywhere holds the original dialogue: no engram carries a
  speaker/timestamp transcript, `daily_messages` starts 2026-09-17 09:22, and
  `diary_chunks` are diary prose, not conversation. So "她复述不了几个月前的
  原文" is not a recall failure — **the original text does not exist and cannot
  be recovered**.

  Fix: default `source_retention_min_importance` 0.7 → **0.5** (below the 0.6
  every summary gets), so transcripts are retained from now on; and expose
  `source_retention_days` (previously read by the code but not in
  `_conf_schema.json`, so the panel could not show or set the 90-day TTL) in the
  schema. Both the schema default and `MemoryConfig` were changed so a fresh
  install behaves the same way.

- **v1.76.19: the first memory after every restart was written into the wrong
  vector space (and stayed invisible to vector recall).** `vector_search`
  filters by `embedding_model` (storage.py), so a row labelled with anything
  other than the active provider is unreachable by the vector route and only
  findable by keyword/FTS. The conversation buffer restored from disk is
  flushed within a minute or two of the load, while activation of the host
  embedding provider runs on a retry schedule that can take 10–180s (AstrBot
  instantiates providers lazily, and the probes run while the loop is busy
  loading ~20 plugins). So the first new engram after every restart landed in
  the internal 64-dim `hash` placeholder space.

  Observed live on 2026-09-21 (v1.76.18 running):
  - `00:12:48` load → `00:13:18` restored the buffered window
  - `00:14:27` the summarizer **succeeded** — the new diagnostics reported
    `LLM bridge slow: 15.4s provider=deepseek/deepseek-flash model=deepseek-flash
    sys_chars=185 usr_chars=2108` — and the engram was written with
    `embedding_model='hash'`, a 64-dim vector, `tier='hot'`, `persona_id='mortis'`
  - `00:16:55` activation finally completed and the plugin itself logged
    "2 older vectors were not rebuilt" — it had counted the fresh memory as a
    stale vector

  The memory was real and well-formed (topics included "记忆召回测试"), but
  vector recall could never see it. Fix: `MemoryService.reembed_stale()` selects
  the mismatched ids **in SQL** (never materialising the table, unlike
  `rebuild_embeddings()` which re-embeds every row) and re-embeds them under the
  active provider; `_activate_embedding_when_ready()` now calls it in a
  plugin-owned daemon thread after a successful switch, so it neither blocks the
  event loop nor is tied to it. This also repairs the historical leftovers.

- **v1.76.18: the recall path ignored the configured score weights.** The
  injection path (`handlers/event/inject.py` → `MemoryService.recall` →
  `PatternCompleter.recall`) hardcoded
  `0.55*base + 0.25*strength + 0.15*recency` while the config advertised
  `score_alpha` / `score_beta` / `score_gamma` ("检索相关性权重 / 重要性权重 /
  时间新鲜度权重"). Those knobs were only read by a different recall method, so
  tuning them never affected the path that actually feeds the prompt. They could
  not be plugged in as-is either: `base` is an RRF score whose magnitude is
  ~1/(60+rank+1), about 0.016 at best, so `alpha=0.5` would have contributed
  ~0.008 against a 0.25 strength term. Relevance is now scaled to 0..1 first and
  the weights are normalised to sum to 1, keeping the base score within 0..1.

  **This is a consistency fix, not a recall-quality fix, and that distinction is
  measured rather than assumed.** Six probes against the live store (real
  SiliconFlow embedder, real config, DB copy) hit the expected memory under both
  the old constants and the new weights — 5/6 either way, and unchanged across
  three weight presets. An earlier conclusion that ranking was burying correct
  memories was a **test artifact**: the probes queried `persona_id="mortis"` for
  memories owned by the `sherri` persona, which persona isolation correctly
  excludes. The one consistent miss is a retrieval/wording miss, not a ranking
  one.

- **v1.76.17: the plugin never read its own config — every WebUI setting was
  ignored.** This is why no new engrams were being created. Two defects
  compounded:
  - `HippocampusStar.__init__(self, context)` did not accept the `config` kwarg.
    AstrBot instantiates plugins as
    `star_cls_type(context=..., config=<plugin config>)` and, on TypeError,
    silently retries `star_cls_type(context=...)`
    (`astrbot/core/star/star_manager.py`). The first form therefore raised, so
    the plugin's entire config was dropped on every load.
  - `PluginInitializer.initialize()` then did
    `cfg_dict = self.context.get_config("hippocampus")`. `Context.get_config(umo)`
    takes a *session* id, and `AstrBotConfigManager.get_conf()` looks the umo up
    in its routing table and falls back to `confs["default"]` — the GLOBAL
    AstrBot config (`cmd_config.json`). The plugin therefore ran on
    `MemoryConfig` defaults: 80 keys of the global config leaked into
    `cfg.extra`, and no Engram-panel setting ever reached the service. Verified
    against the live install: `get_config()` yields
    `summary_fallback_enabled=False`, `tier_recall_include_cold=False`,
    `tier_cold_fallback_min_hits=1`, `embedding_dim=64`,
    `embedding_provider_id=""`, while the plugin's own file says
    `true / true / 3 / 4096 / Qwen3-Embedding-8B`.
  Consequences on the live bot: the summarizer logged "summary skipped: LLM
  unavailable and fallback disabled" while the config file said `true`, so every
  flushed conversation window was **discarded instead of stored** when its
  summarizer LLM call timed out (398 engrams, newest 12:00, while 117 messages
  arrived after 19:20); and the v1.76.16 cold-tier fix (`tier_recall_include_cold`)
  was never actually in effect.
  Fix: accept and forward AstrBot's `config`; read the plugin's own
  `<root>/data/config/<plugin>_config.json` only as a fallback (never
  `context.get_config()`); and log the effective settings at startup
  (`[hippocampus] effective config: summary_mode=... min_msgs=... fallback=...
  include_cold=...`) so panel-vs-runtime drift is visible instead of silent.
  Tests that relied on the old (broken) `get_config` path now pass their config
  to `initialize()`, which is the real contract.

- **v1.76.17b: bounded the summarizer's provider retries, and made its slowness
  visible.** Investigating "why does the summarizer's LLM call time out at 45s
  when the chat's calls succeed" turned up two things:
  - The plugin never passes `request_max_retries`, so it inherits AstrBot's
    full chain: its OpenAI source retries each request
    `REQUEST_RETRY_ATTEMPTS = 5` times with `wait_exponential`, inside a
    10-iteration outer loop in `text_chat`, with a 120s HTTP timeout per
    attempt (`sources/openai_source.py`, `sources/request_retry.py`). One
    plugin summarization could therefore spin for many minutes. The bridge now
    passes `request_max_retries=1` — the plugin treats any failure as "skip and
    use the fallback", so retrying is wasted work.
  - The timeout log now reports the provider id/model, the prompt sizes and the
    retry bound, and calls slower than 15s are logged too. Slowness here is off
    the reply path and was previously invisible; prompt size is the first thing
    to check next time.
  - The deferred embedding activation now starts at +5s instead of +0.5s:
    AstrBot instantiates providers lazily, so probing immediately produced
    "Provider Qwen3-Embedding-8B was not found. Its provider or model ID may
    have been changed." (warning at 23:44:27.570, provider built at
    23:44:28.188). The bridge recovered via `get_all_embedding_providers()`,
    but it was a wasted probe plus a spurious WARN. Note the id itself is
    correct — `cmd_config.json` `provider[2]` carries it.
  - Still unexplained: on the live bot a single summarizer request hung past
    45s with *no* retry warnings logged (so not retry amplification) while the
    chat's calls to the same provider/model completed in ~6s. The added
    diagnostics are there to catch it next time.

- **v1.76.16 memory-recall repair (root cause of "记忆没有被正确召回")**:
  - **`HippocampalStore.decay_pass` compounded the decay.** It multiplied the
    *already-decayed* strength by `exp(-(now - anchor)/tau)`, where
    `anchor = max(last_accessed, created_at)` and the decay never advances the
    anchor. With the maintenance loop running every
    `memory_decay_interval_seconds` (1800s) a memory anchored `N` sweeps back
    lost `exp(-D*N²/2τ)` instead of `exp(-D*N/τ)` — one day of real age cost it
    `exp(-48*age/τ)` — so strength hit 0.0 within ~2-3 days no matter how
    important it was. Every row then fell below `tier_cold_strength_floor` and
    was classified `cold`; because `tier_recall_include_cold` was false, cold
    was excluded from normal recall, so the row was never `touch()`ed again,
    its anchor never advanced, and it stayed at 0.0 permanently.
    Measured on the affected live store: age 1.21d → strength 0.2410,
    age 3.21d → 0.0000, `avg strength = 0.0151` with **391/398 engrams below
    the cold floor**. Decay now uses the time elapsed since the *previous*
    sweep (recorded in `hippo_meta['decay:last_pass_at']`), capped by the
    engram's own age, so one day of sweeps costs exactly one day of decay. The
    first pass on a store with no recorded sweep time keeps the original
    age-based semantics (a brand-new DB still decays by real age once).
    A one-shot repair script (`repair_decay_strength.py`) restores the
    annihilated strengths to their correct Ebbinghaus envelope — see below.
  - **`EMB_BRIDGE_TIMEOUT` equalled `InjectHandler._INJECT_HARD_TIMEOUT`
    (both 10.0s)**, so the whole-injection cap always fired first and the hook
    dropped the injection entirely (`auto inject timed out ... proceeding
    WITHOUT injection`, logged 59×) instead of taking the designed degradation
    path of continuing with the FTS/keyword route. Lowered to 5.0s.
  - **`ConfigManager._GROUP_KEYS` was missing `summary_settings`.** AstrBot
    writes that block as a nested object, so while it was absent from the
    hoist list every field in it (`summary_mode_enabled`, `summary_min_messages`,
    `summary_idle_seconds_*`, `summary_fallback_enabled`, …) silently fell back
    to the `MemoryConfig` default and **any 总结 setting made in the WebUI was
    ignored**. This also meant `summary_fallback_enabled` could not be turned
    on, so a summarizer LLM timeout discarded the whole conversation
    (`store_summary` returns None on empty text) instead of writing the
    truncated-transcript fallback — 10 such drops were logged, the most recent
    after the 16:03 reload, while the day produced 0 engrams from 283 captured
    messages.
  - `session_aggregate_min_chars` had the range `(1, 1000)` while its own
    default is `0`, so a correctly-configured install warned on every load.
  - `HippocampalStore.recent_for_session` / `recent_for_actor` ordered by a
    float timestamp alone with no tiebreaker, so engrams written within the
    same clock tick came back in an arbitrary order — `tests/_smoke_v66`
    flaked roughly 1 run in 25 on `assert sess[0].id == e3.id`. Added a
    `rowid DESC` tiebreak (insertion order).
  - **`ConversationBuffer` discarded short conversation windows.** When a
    channel went idle below `summary_min_messages` and stayed silent past
    `summary_min_messages_grace_seconds`, `_settle_idle_buf` called
    `self._bufs.pop(ch, None)` — the window was thrown away, never summarized,
    never stored. `summary_min_messages` is a *batching* hint (how long a short
    window may wait to be merged with more messages), not a retention policy,
    so the grace path now **summarizes** the window instead of dropping it.
    Nothing is discarded there any more: a channel either reaches the minimum
    and flushes on idle, or it flushes at grace expiry. Set
    `summary_min_messages` to 1 to skip the wait entirely. The grace-expired
    flush is still picked up within ~30s by the idle-flush loop in `main.py`.
  - **`ConversationBuffer` was pure memory, so a plugin reload discarded every
    open window.** AstrBot reloaded the plugin 7 times in one day on the live
    bot; the 17:58 reload dropped a window whose last message was 17:54 — under
    `summary_min_messages`, not idle long enough yet, and `terminate()`'s
    `flush_all()` did not get to run. The messages were already captured into
    `daily_messages`, but they never became long-term memory. Now:
    - `ConversationBuffer.snapshot()` / `.restore()` give an I/O-free
      serializable view (the buffer module still owns no storage);
      `hippocampus/conv_buffer_store.py` writes it atomically (temp file +
      `os.replace`; deliberately no `fsync` — this protects against a reload,
      not a power cut) to `data/conv_buffer.json`, beside the DB.
    - `ObserveHandler` snapshots after every feed *and* after every flush, so a
      window that was already summarized cannot come back from disk and be
      summarized twice, and rehydrates on startup. Windows whose last message is
      older than 14 days are skipped, so a stale file cannot resurface as fresh
      memory.
    - The idle-flush loop calls `ensure_conv_buffer()`, so a recovered window is
      summarized even if that channel never speaks again.
    - Every failure is logged and swallowed: persistence must never take the
      ingest path down.
  - **The host embedding provider was never actually activated**, so vector
    ("same meaning") recall could not see the store. AstrBot is configured with
    a real embedding provider (SiliconFlow `Qwen/Qwen3-VL-Embedding-8B`,
    4096-dim) and 396 stored engrams carry its vectors — but the plugin only
    switches to it ("astrmock", the proxy to the host provider) if a one-shot
    probe succeeds, and keeps its internal 64-dim `hash` placeholder otherwise,
    with no retry. Two compounding causes:
    - `hippocampus/_async_bridge.py` `run_sync()` drives every coroutine on a
      *private* worker loop. AstrBot's embedding provider wraps an aiohttp
      ClientSession whose connections belong to AstrBot's loop, so awaiting
      them from the worker loop does not fail fast — it hangs to the caller's
      cap. New `set_host_loop()` / `host_loop_usable()` / `call_on_host_loop()`
      schedule the call onto the host loop instead, and refuse (falling back to
      `run_sync`) when there is no host loop or when the caller *is* the host
      loop thread, where blocking would deadlock the loop that has to run the
      coroutine.
    - The probe ran inside the plugin's **synchronous** `__init__`, i.e. with
      the host loop blocked inside `__init__` itself, so it could only ever time
      out. Activation is now deferred to `_activate_embedding_when_ready()`, an
      asyncio task that runs once `__init__` has returned, with bounded retries
      (0.5s/5s/20s/60s). The v1.76.4 guard is preserved — it only switches when
      the probe returns a usable vector — and `auto_rebuild_on_switch` stays
      off so activation never re-embeds the store.
    - `ProxyEmbeddingProvider` gained `probe=False` for that path, so the
      blocking one-shot probe is skipped entirely at init; `dim` still resolves
      lazily on the first successful `embed()`.
    - The **LLM bridge had the identical defect** and got the same routing: it
      also drives an aiohttp-backed host provider, and its 12x
      "LLM bridge timed out after 45.0s" is what made the summarizer give up
      and discard whole conversations. It now prefers the host loop too, with
      its caller cap kept below `ProxyLLMProvider`'s 60s cap so the provider's
      own 45s bound surfaces first.
    Observable effect of the old behaviour: at boot the log said "astrmock
    embedding probe returned no usable vector; keeping configured embedding
    hash", and 59 runtime recalls logged `emb bridge get_embedding timed out`.
    The downgrade is not theoretical — the store holds 64-dim `hash` rows
    written on 2026-09-14 next to the 4096-dim ones, which is why recall could
    not match them.
  - **`terminate()` blocked AstrBot's event loop for ~49s on every plugin
    reload — the cause of replies never reaching QQ.** `terminate()` runs ON
    AstrBot's event loop and called `convbuf.flush_all()`, i.e.
    `_sink -> summarize -> LLM` inline. Caught two independent ways in the live
    logs: AstrBot's own diagnostic ("Event loop lag detected: 49.375s
    (threshold 15.000s)", logged 0.4s before the reload finished) and three
    watchdog dumps ("Timeout (0:00:30)!") whose event-loop thread was parked in
    `main.py terminate -> conversation_buffer.flush_all -> _flush_key ->
    observe._sink -> summarizer.summarize -> _llm_summarize -> LLMProvider.chat`.
    With the v1.76.16 host-loop bridge it became a hard self-deadlock — the
    bridge waits for the host loop, and the host loop is the thing blocked in
    `terminate()`; observed as "summarizer llm error: host-loop call timed out
    after 50.0s". A 30-50s blocked loop cannot service the aiocqhttp WebSocket
    ping/handshake, so the API client drops and the next reply raises
    `aiocqhttp.exceptions.ApiNotAvailable` (6 of them in a 15s burst at
    16:58:25-40, plus a backend-restart variant at 14:09 with
    `ConnectionResetError [WinError 995]` + `hypercorn LifespanFailureError`).
    This bot reloads the plugin ~8×/day, so that was a ~50s outage window each
    time. `terminate()` now only snapshots the conversation buffer (durable as
    of v1.76.16) and lets the next startup summarize it off-loop; the legacy
    session aggregator's flush runs via `asyncio.to_thread` with a 20s bound.
  - New regression test: `tests/_smoke_v86.py`.
  - Version bump: 1.76.15 → 1.76.16.

### Changed
- **v1.76.15 audit fixes (memory + event-loop hardening)**:
  - Command dispatch and Dashboard page-API handlers now run heavy sync work
    in worker threads with `asyncio.wait_for` timeouts (previously `/mem
    rebuild`, `/mem search --mode=dual`, `/stats`, `/recall/test`, graph
    routes, etc. could freeze the AstrBot event loop).
  - `WorkingMemory` cells now have a hard LRU cap and idle TTL.
  - `ConversationBuffer` drops below-min channels after a grace period and
    caps total buffered channels.
  - Decay and tier reclassification use single SQL `UPDATE` statements
    instead of materializing every engram into Python.
  - Completed `memory_write_ops` and old `memory_sources` rows are purged
    on the decay-maintenance cadence.
  - Graph-v2 write caps and graph vector entity caps are configurable
    (`graph_max_topics/persons/facts`, `graph_vector_entity_limit`).
  - Ingest/inject worker concurrency is bounded; provider sync calls use a
    shared bounded runner instead of leaking one thread per timeout.
  - The prospective scheduler task is actually started/stopped; backup
    thread is shut down on plugin terminate.
  - `_stamp_persona` is bounded (10s) in all hook and command paths.
  - Version bump: 1.76.14 → 1.76.15.

### Fixed
- **Recurring freeze hardening (v1.76.14, 2026-09-07 recurrence)**: the
  v1.76.13 caps only bounded the *caller* of `run_sync`; a bridge coroutine
  stuck in sync code / cross-loop await survives `fut.cancel()` and keeps
  the shared worker loop blocked, so every later embedding/LLM call still
  burned its full timeout (and any such call made on the AstrBot event loop
  thread froze the whole bot for 20-60s per call). Now:
  - `handlers/recall.py` `emb_bridge_for_context`: every AstrBot embedding
    provider await is hard-bounded inside the bridge (`asyncio.wait_for`,
    10s) so the worker loop can never be stuck by the provider.
  - `handlers/init.py` `_llm_bridge`: `provider.text_chat` bounded at 45s.
  - `_async_bridge.run_sync`: on timeout the worker is marked wedged and
    the NEXT call rebuilds a fresh worker thread+loop instead of reusing a
    possibly-permanently-blocked one (self-healing after one bad call).
  - Proxy embedding/LLM sync fns now also ride the bounded worker
    (a sync fn with no timeout used to block the caller thread forever).
  - `ObserveHandler` ingest paths (`handle_message` / `handle_bot_message`
    / `handle_poke`) are bounded by `_OBSERVE_HARD_TIMEOUT=75s` and release
    the pipeline on expiry (worker continues detached), so a wedged ingest
    thread can no longer stall every subsequent channel message.
  - `main.py` hook shells (`inject_memory` / `observe_message` /
    `observe_poke` / `observe_bot_reply`) now wrap the WHOLE hook body
    (`_stamp_persona` included) in `asyncio.wait_for` (30s/90s). 2026-09-07
    20:32 recurrence: the freeze entered inject_memory and the asyncio loop
    died within milliseconds -- BEFORE the 10s inner breaker could fire (a
    frozen loop cannot schedule the wait_for timer), and AstrBot core API
    calls in stamp_persona_id (sp.get_async / conversation_manager /
    persona_manager) were outside every guard.
- **Auto-injection circuit breaker (2026-09-07 astrbot "judge replied but bot
  never answered" freeze)**: `_async_bridge.run_sync` now carries a hard
  timeout by default (`DEFAULT_SYNC_TIMEOUT=20s` for embedding / short ops,
  `DEFAULT_LLM_SYNC_TIMEOUT=60s` for the LLM bridge) and cancels the hung
  coroutine on expiry instead of waiting forever; `InjectHandler.handle_inject`
  is wrapped in `asyncio.wait_for` (default 10s) so a stuck recall (embedding
  HTTP without its own provider timeout, SQLite lock, ...) can no longer stall
  the `on_llm_request` hook chain -- the LLM request is released without
  injection. New config: `auto_inject_timeout` (1-120s, default 10s, exposed
  in the WebUI config schema).
- Persisted prompt overrides now propagate to the actual LLM consumers
  (encoder / summarizer / diary / consolidation) via `cfg._prompt_namespace`.
- `SpreadingActivation.activate_with_context()` accepts `session_id` again,
  so session-context recall seeds are no longer silently disabled.
- Hard-delete cascade now targets `llm_relations` instead of the unrelated
  SemanticStore `relations` table.
- `GraphRetriever` shares the service's canonical `graph_store` connection
  instead of leaking a second SQLite connection.
- Working-memory prepend and spread route now enforce `scope_id` /
  `memory_types` filters.
- Diary daily-message cache and diary chunks are now memory-scope aware.
- `/mem debug` forwards scope and uses the live dual-route weights.
- Dashboard importance distribution buckets floor to 0.1-wide bins.
- Full-graph v2 shared edges now use a per-memory ownership link table:
  deleting the first owner no longer removes an edge still referenced by
  other memories.
- `BackupManager.restore()` restores into the live database through the
  SQLite online backup API instead of replacing the open database file
  with `shutil.copy2`.

## [1.76.12] - 2026-08-16

### Changed
- Overview distribution panels are now donut/pie charts with memory-entry
  style emoji icons (⭐ importance, 🔥 tier, 💗 valence, 🧭 dual-stream).


## [1.76.11] - 2026-08-16

### Changed
- **Dashboard visual redesign**: Aurora Dark theme with glass cards, sticky
  topbar, gradient tabs, radial background accents, refreshed graph palette
  and journal-style diary cards.
- Memory list cards now show live status, memory type, tier and importance
  metadata; page API list items expose those fields.


## [1.76.10] - 2026-08-16

### Added
- **Persistent full graph v2**: graph_nodes_v2 / graph_edges_v2 /
  graph_entries_v2 / graph_entry_nodes_v2 + FTS5, with cross-memory edge
  merging (EMA confidence / accumulated weight).
- **GraphExtractorV2**: deterministic topic / person / fact nodes and
  describes / mentioned_in / co_occurs_with edges from engram metadata and
  optional key_facts.
- **Full-graph snapshot API**: `page/graph/data?full=true` returns scoped
  nodes / edges / memories with node weights.
- **Derived-index cascade**: hard delete now removes RelationStore,
  SemanticStore, GraphStore fast-path + v2 entries and MemoryAtoms in one
  service-owned hook.
- Dashboard graph page gains a "全量图谱" toggle.

### Notes
- Graph vector memory-level embeddings and shadow-generation rebuild remain
  deferred; current JSON-vector retrieval still powers the graph route.


## [1.76.9] - 2026-08-16

### Fixed
- **Import indexing backpressure**: WebUI import/preview now run in a worker
  thread; `derive_indexes=false` writes the main table only, letting callers
  schedule a batch rebuild later.
- **Restore rebuilds missing derived indexes**: restored active memories
  without `entity_refs` re-run `_post_ingest` so semantic / atom / graph
  indexes catch up.
- **Graph/atom route type filters**: graph hydration and atom-parent mapping
  now obey `Cue.memory_types`, matching the document route.


## [1.76.8] - 2026-08-16

### Changed
- **SQL-bounded export**: export path now uses COUNT(*) + SQL LIMIT/OFFSET
  instead of materializing every engram in Python; output remains capped at
  20,000 with `truncated` / `total_available` metadata.
- **Native import re-indexes**: active native-imported memories now get a
  fresh embedding and `_post_ingest` derived indexes (semantic / atom /
  graph); archived rows remain audit-only.
- **Route tests future-proof**: page-api smoke tests assert required routes
  rather than a hard-coded endpoint total.
- Metadata description refreshed to v1.76.7 capabilities; `_PUBLIC_API.md`
  documents the boundary between internal scope/prompt/transfer surfaces and
  the stable cross-plugin contract.


## [1.76.7] - 2026-08-16

### Fixed
- **Graph route partitioning**: graph retrieval now obeys forgotten_at /
  persona_id / scope_id / actor_id / channel_id filters on both fast-path
  and legacy fallback.
- **Command-path scope stamping**: every `/mem` command wrapper now stamps
  persona + scope before dispatch; activation / confidence / narrative /
  debug / graph handlers forward scope_id.
- **Scope coverage**: relation, diary-chunk and semantic recall now accept
  and enforce scope_id; spreading-activation seeds and final activation map
  are scope/persona filtered.
- **Stable user identity**: scope resolution prefers `sender_id` over the
  mutable nickname; `identity_aliases` remains the nickname bridge.
- **Consolidation safety**: merged write must succeed before originals are
  archived/deleted; identity carries scope_id; session grouping is keyed by
  (session, persona, scope); candidate count is capped.
- **Import fidelity**: native JSON import preserves id / status / tags /
  confidence and deduplicates against the existing database, not just the
  file; archived rows no longer resurrect.
- **Prompt isolation**: overrides are namespaced per MemoryService/store;
  empty prompt edits reset to default; encoder/summary/diary built-ins stay
  byte-identical to their original constants.
- **Retrieval consistency**: command debug/dual-route use service weights;
  `asearch()` delegates to weighted `search()`; recall debug truncates to k.
- **Atom lifecycle**: expired atoms are soft-forgotten by decay pass; atom
  route touches last_accessed/access_count; LIKE search escapes wildcards.
- **Resummarize**: derives peer name from source, carries scope_id, and
  re-runs `_post_ingest` so derived indexes track the rewritten engram.

### Notes
- `_conf_schema.json` now exposes scope / scoring / consolidation /
  source-retention fields; README documents the 1.76.6 UI surfaces.
- Smoke v80 covers graph filters, consolidation safety and prompt
  namespace isolation.


## [1.76.6] - 2026-08-16

### Added
- **Memory scope (session/user/global)**: new `scope_id` column and
  `memory_scope_mode` / `isolated_sessions` / `identity_aliases` settings;
  scope is stamped on hooks and enforced by vector / FTS / dual-route /
  atom / spread retrieval.
- **Identity aliases**: `platform:user_id=Canonical` mappings used by the
  user scope resolver.
- **Prompt manager**: built-in extract / summary / consolidation / diary
  templates, persisted overrides, dashboard editor with save/reset, and
  runtime consumption by encoder / summarizer / consolidator / diary writer.
- **WebUI memory import/export**: JSON round-trip + CSV export, preview
  (entry count / duplicates / errors), duplicate-skip import and download.

### Notes
- Smoke v79 covers scope partitioning, persistent prompt overrides and
  transfer round-trip.


## [1.76.5] - 2026-08-16

### Added
- **Recall debug console**: `/page/recall/test` now returns per-route raw
  score + RRF contribution + final weighted breakdown and elapsed ms; the
  dashboard renders route chips and score parts.
- **Visualization stats**: stats endpoint returns status/importance/tier/
  valence/stream/atom distributions; dashboard renders bar charts.
- **Archive lifecycle**: WebUI can list archived memories, restore them
  (re-embedding when necessary), and batch soft/hard delete.
- **Source retention + re-summarize**: important summaries keep their raw
  transcript; detail view can inspect the source and re-run LLM
  summarization in place.
- **First-person episodic recall prompt**: conversation summarization now
  writes subjective bot memories, normalizes relative time, and forbids
  generic "user/someone" labels; bot turns are labelled as `我`.
- **Explainable weighted dual-route scoring**:
  `retrieval * alpha + importance * beta + recency * gamma +
  cross_route_bonus`, with dynamic document/graph weights by query intent.
- **Atom temporal lifecycle**: MemoryAtom gains `ttl_days` / `event_time` /
  `expires_at` and a temporal score; a new atom keyword route maps fresh
  atoms back to parent engrams in dual-route recall.
- **LLM memory consolidation**: optional low-importance stale-memory
  grouping (session or embedding-based semantic clustering) and LLM merge,
  with originals archived or deleted.

### Fixed
- Dual-route document retrieval now honors `persona_id` scoping.

### Notes
- Smoke v78 covers the new recall debug, stats, lifecycle, source, dynamic
  routing and atom temporal surfaces.


## [1.76.4] - 2026-08-16

### Fixed
- **Soft-forgotten recall leak**: vector and FTS search now exclude
  `forgotten_at > 0` rows, and `PatternCompleter` adds a defense-in-depth
  filter. A `/mem forget` soft-delete can no longer resurface in recall or
  auto-injection.
- **FTS filter pushdown**: persona / actor / channel / memory-type filters
  now run inside the SQL FTS JOIN before `LIMIT`, so persona-scoped recall
  no longer drops local hits that fall outside the global FTS top-k.
- **FTS no longer gated by embedding model**: keyword recall still reaches
  older `embedding_model=hash` rows after switching to `astrmock`.
- **Safer provider activation**: `astrmock` is auto-activated only when the
  host embedding probe returns a usable vector. Without a host provider the
  plugin keeps the configured `hash` provider instead of silently switching
  to empty embeddings.
- **Recall cache key completeness**: `valence_hint`, activation maps and the
  retrieval-affecting config snapshot now participate in the cache key, so
  runtime config changes take effect immediately.
- **`list_active` correctness**: `forgotten_at` filtering moved into SQL
  before `LIMIT`.
- **Group working memory**: cells are indexed by both `session_id` and
  `channel_id`; persona filtering is applied to the working-memory prepend.
- **Backup scheduler**: duplicate `_start_backup_scheduler` call removed and
  an idempotency guard added (previously two `hippocampus-backup` threads
  were started).
- **WAL-consistent backups**: `BackupManager.create()` now uses the SQLite
  online backup API, producing a consistent snapshot while live service
  connections stay open.
- **Event-loop blocking**: observe / bot-reply / poke ingest, auto memory
  injection, idle flush and daily diary generation now run their
  synchronous SQLite+LLM work on worker threads instead of blocking the
  AstrBot event loop. `ConversationBuffer`, `WorkingMemory` and the recall
  cache gained internal locks for the new cross-thread access pattern.

### Notes
- Smoke v77 covers the recall/backup/initializer hardening batch; v23/v36/
  v41/v53/v70 assertions refreshed for current code and Python 3.14.
- README removed the stale `openai_api_key` config claim.


## [1.76.3] - 2026-08-14

### Changed
- **Persona-scoped recall everywhere**: relations, semantic graph, narrative,
  confidence, debug, dual-route, activation, dashboard recall, and agent tools
  now accept and honor `persona_id`. Command and auto-injection paths pass the
  current persona scope automatically when `persona_isolation_enabled` is on.
- Added `HippocampalStore.engram_ids_for_persona()` as the shared partition
  helper for the new recall filters.

### Notes
- Agent tools expose an optional `persona_id` parameter; the command and
  injection paths are automatic, but tool invocations still need upstream
  context to supply the current persona id.

## [1.76.2] - 2026-08-14

### Added
- **Minimum messages before idle summarization**: new
  `summary_min_messages` setting (default `20`). Conversation buffers with
  fewer accepted lines now reset their idle timer instead of flushing, so
  short fragments are not summarized until the channel accumulates enough
  messages. `0` disables the minimum and preserves the previous behavior.

### Notes
- `summary_max_messages` still forces a flush when reached.
- `flush_all()` on shutdown still flushes every buffered channel.
- WebUI schema and `_smoke_v40.py` coverage updated.

## [1.76.1] - 2026-08-13

### Fixed
- **Persona isolation in public recall**: `query_recent_memory()` no longer
  leaks engrams with an empty `persona_id` into a named persona partition,
  and the `since` timestamp floor now applies to the semantic/query path as
  well as the empty-query path.
- **Vector persona filter regression**: `vector_search()` now uses
  `COALESCE(persona_id, '') = ?`, matching the previous Python filter and
  excluding `NULL` persona rows from non-empty persona queries.
- **Task lease expiry semantics**: `task_lease_owner()` treats expired leases
  as free, and `renew_task()` no longer extends an expired lease.
- **Life entity graph contract**: `link_entities()` now rejects missing
  endpoints, `upsert_entity()` preserves existing `name`/`canonical_url`
  when omitted, and `weight=0.0` is no longer coerced to `1.0`.
- **Thread safety**: `LifeGraphStore.get_entity()` now uses the same lock as
  the other read/write methods.
- **Diary tags**: `store_diary_line()` no longer emits a duplicate `day:`
  tag.

### Notes
- `_PUBLIC_API.md` clarifies the `link_entities()` endpoint contract and
  lease expiry behavior.
- Smoke v75/v76 assertions extended for the above regressions.

## [1.76.0] - 2026-08-12

### Added
- **Full L2-01 memory surface**: `store_event` (life events as
  `memory_type=event` engrams), `add_note` (life notes as
  `memory_type=note` engrams), `query_memory` / `search` (persona-scoped
  recall with optional `memory_types` filter), all exposed on
  `HippocampusStar` and `MemoryService`.
- **Life entity graph (L2-02 primitives)**: new `LifeGraphStore`
  (same `hippocampus.db`) with `life_entities` /
  `life_entity_links`; `upsert_entity`, `link_entities`,
  `list_entities`, `list_links` public API. Entities carry the layered
  `dimension` model (`platform / url / person / project / community /
  topic`) and edges keep `weight / seen_count / first_seen_at /
  last_seen_at`.

### Notes
- `_PUBLIC_API.md` updated for both v1.75 and v1.76 methods.
- New smoke `tests/_smoke_v76.py`.

## [1.75.0] - 2026-08-12

### Added
- **Cross-plugin public API (`_PUBLIC_API.md`)**: stable contracts for
  `store_diary_line`, `query_recent_memory`, `claim_task`, `renew_task`,
  `release_task` and `task_lease_owner`, exposed on `HippocampusStar`
  and `MemoryService`. Diary lines persist as persona-scoped engrams
  with `source: / day: / mood: / signature: / ref:` tags; recent-memory
  query supports both persona-scoped recall (query) and deterministic
  newest-first listing.
- **Task leases**: new `TaskLeaseStore` (same `hippocampus.db`) with
  `claim / renew / release / owner / cleanup_expired`; expired leases
  can be reclaimed immediately. This is the v2 multi-instance single
  writer primitive for downstream plugins (L2-09).

### Notes
- Backward compatible: existing `store_diary(diary, identity)` callers
  are unaffected (`extra_tags` is optional); all prior smoke tests keep
  passing.

## [1.74.0] - 2026-08-11

### Added
- **write-ops recovery (P1)**: `memory_write_ops` journal table plus
  `MemoryService._repair_incomplete_write_ops()` startup replay. The
  post-ingest pipeline fans out across semantic / atom / graph stores on
  separate connections, so a crash mid-pipeline left derived indexes
  incomplete. Ops are now journaled (start/advance), and startup replays
  unfinished ops idempotently (entity/atom/graph-ref upserts merge;
  relations skip triples that already exist).
- **Recall cache (P2)**: LRU + TTL (60s, max 128) over `recall()` results,
  invalidated on every memory write. The injection path no longer re-runs
  the full vector + FTS + graph pipeline for identical cues within one
  request window.
- **Index-consistency guard (P1b)**: startup check that the FTS sync
  triggers (`engrams_ai/au/ad`) exist; when missing, schema is recreated
  and FTS reindexed. `engrams_fts` is an external-content FTS5 table, so
  COUNT / integrity-check cannot detect drift - the triggers are the root
  cause and the check targets them.

### Changed
- **Vector search SQL pushdown (P0)**: `HippocampalStore.vector_search`
  no longer loads the whole table (`SELECT *`) and filters in Python.
  Filters are pushed into the WHERE clause and only lightweight columns
  (id + embedding_json) are loaded; top-k ids are re-fetched as full
  rows. Rows without an embedding are skipped instead of scoring 0.0.
- **Tool retrieval SQL pushdown (P2b)**: `list_active` gains an
  `actor_id` filter; new `list_active_by_entity_ref()` uses `json_each`
  over `entity_refs`; the `list_recent_memories` / `search_by_entity_memory`
  tools now filter in SQL instead of Python-list post-filtering.

## [1.73.1] - 2026-08-08

### Fixed
- **Diary block double-dash overlay**: `handlers/event/inject.py` diary-block
  rendering prepended `- ` unconditionally, so a diary chunk already starting
  with `- ` became `- - ...`.  Changed to dedup logic
  (`t if t.startswith("- ") else "- " + t`); no behavioural change for
  chunks without a leading `- `.

### Changed
- README "Known Leftovers" section synced: removed already-shipped items
  (BM25 / EventHandler split / i18n / db_migration / page_api), marked
  write_ops as unimplemented (see `docs/TODO.md` §2.2), progress pointer
  changed from deleted ROADMAP.md to `docs/TODO.md`.
- `docs/TODO.md` §2.10 status updated to fixed (2026-08-08).

## [1.73] - 2026-07-31

### Added
- **Public cross-plugin coordination helper** `engram_core_helpers.py`
  (repo root, importable next to `hippocampus`):
  `strip_injected_blocks(parts_list, *, root_tag, inner_labels=())`
  generalises the v1.67.2 re-injection defense so external plugins
  writing to `req.extra_user_content_parts` can strip their own prior
  blocks before appending fresh ones.  Requested by
  xml_structured_output (engram-core-extra-user-content-coordination
  doc): their `<xml-extra>` memo blocks had no re-injection defense
  and accumulated linearly across turns.
  - Attribute-bearing open tags matched (`<xml-extra scope="...">`).
  - Empty `inner_labels` -> root-tag-only match; non-empty -> at
    least one inner label required (avoids false positives on other
    plugins' parts).
- **README: 多插件注入协调 section** documenting the
  `extra_user_content_parts` protocol: unique XML root-tag namespace,
  priority convention (0 = engram-core, 5-9 system-level, >=10
  user-behaviour-driven), and parts_list position semantics
  (`auto_inject_position` only governs engram's own 4 blocks;
  `append` lands after engram blocks; prepend must use
  `parts_list[0:0] = [...]`, not `insert(0, ...)`).
- **Config whitelist** `external_plugin_root_tags` (default
  `["xml-extra"]`) in `_conf_schema.json` for operator auditing of
  known external root tags.  Registration-only, does not affect
  injection behaviour.  Unknown keys land in `MemoryConfig.extra`
  (config_manager extras path), so no loader change needed.

### Changed
- `InjectHandler._strip_prior_engram_blocks` now delegates to
  `strip_injected_blocks` with `root_tag="engram-context"` and the
  existing `_ENGRAM_INNER_LABELS` (legacy `[今日回顾]` label still
  stripped).  `_ENGRAM_OPEN` / `_ENGRAM_CLOSE` constants replaced by
  `_ENGRAM_ROOT_TAG`.  Behaviour unchanged (v1.67.2 smoke passes).
- Version banner updated to v1.73.

### Tests
- `tests/_smoke_v72.py`: 7 cases covering attribute-bearing tags,
  inner-labels semantics, cross-plugin isolation, root-tag
  normalisation, handler delegation, 5-turn bounded simulation
  (engram=1, memo=1, memo last for both positions), and
  auto-inject-disabled external injection.



## [1.72c] - 2026-07-29

### Fixed
- **engram-context injection anomalies** (issue 2026-07-29, three phenomena):
  1. **`[\u8fd1\u671f\u5bf9\u8bdd]` block**: same engram surfaced twice when present
     in BOTH `WorkingMemory` head AND `PatternCompleter` top-k.  Reconsolidator
     `touch()` bumps recently-accessed engrams into hot tier, which the
     completer then re-ranks into top-k -- two paths converge on the same
     engram.  Fixed by `MemoryService.recall()` (service.py:920) using
     `head_ids = {e.id for e in head}` to filter completer result before
     splicing.  Mirrors the dedup pattern in livingmemory's
     `RRFFusion.fuse()` (`all_doc_ids = set()`).
  2. **`[\u4eca\u65e5\u56de\u987e]` block** (label + content mismatch):
     - Label renamed `[\u4eca\u65e5\u56de\u987e]` -> `[\u6700\u8fd1\u65e5\u8bb0]`
       because `run_daily_diary()` writes the PREVIOUS day's content
       (diary_trigger_hour defaults to 12, schema: "Y\u6570\u5929\u672c\u5730\u51e0\u70b9\u751f\u6210\u524d\u4e00\u5929\u7684\u65e5\u8bb0"),
       so "today's review" was structurally a misnomer.
     - Old label `[\u4eca\u65e5\u56de\u987e]` retained in
       `_ENGRAM_INNER_LABELS` for graceful migration (lets
       `_strip_prior_engram_blocks` still recognise and strip
       v1.67-era blocks already in flight).
  3. **Cross-turn diary chunk loop** (same chunk re-injected every
     `on_llm_request` firing): `InjectHandler` now maintains
     `self._seen_diary: deque[str] = deque(maxlen=64)` and filters
     out chunks whose text is already in the LRU on each inject,
     then appends the freshly-issued ones.

### Changed
- **Write-side quality gates** in `diary_writer.py` (v1.72):
  - `_SYS_BASE` rewritten as prose-style prompt with explicit format
    constraints: no markdown separators (`---`, `***`), no bullet or
    numbered lists, complete sentences only, natural ending near
    target word count, subjective first-person voice allowed.
  - New `_DEFAULT_USER_HEAD` const carries the same constraints into
    the user prompt.
  - `_build_prompt()` accepts optional `head_override` so operators
    can fully customise the user-prompt head via config.
  - `DiaryWriter._fallback()` now returns `None` instead of writing
    raw transcript-as-summary.  Previously a `_fallback()` chunk like
    "\u6700\u63a5\u8fd1\u7684\u662f\u4e24\u4e2a\u4e92\u76f8\u72ec\u7acb\u7684\u5b50\u7cfb\u7edf\u62fc\u8d77\u6765\u770b\u7740\u50cf\uff1a  ---"
    was stored in `diary_chunks` and re-injected weeks later as
    `[\u4eca\u65e5\u56de\u987e]`.  Returning `None` means `compose()`
    skips the day's diary write entirely when LLM is unavailable --
    better no diary than a fake one.
  - `compose()` returns `None` when `_llm_compose` returns `None`
    (no fallback path remains).

- **Operator-overridable prompts** (v1.72):
  - `MemoryConfig.diary_system_prompt_override: str = ""` and
    `MemoryConfig.diary_user_prompt_head_override: str = ""` exposed
    via `_conf_schema.json` (label_zh: \u65e5\u8bb0\u7cfb\u7edf\u63d0\u793a\u8bcd\u8986\u76d6 /
    \u65e5\u8bb0\u7528\u6237\u63d0\u793a\u8bcd\u5934\u90e8\u8986\u76d6).
  - Empty override -> built-in `_SYS_BASE` / `_DEFAULT_USER_HEAD`.
    Filled -> verbatim replacement with `{day_label}` / `{target}`
    placeholders in the user head.
  - `_system_prompt()` and `_llm_compose()` read cfg overrides at
    runtime; no restart required for prompt tweaks.

- **DB cleanup (v1.72c)**: removed 282 garbage chunks from
  `diary_chunks` in production DB at
  `C:\Users\chiriu\.astrbot\data\hippocampus.db`:
  - 271 pure-punctuation chunks (no Chinese chars, mostly single-char
    `\u3002`/`\u2026` etc. produced by `split_chunks()` over
    punctuation-heavy LLM output).
  - 10 raw-transcript chunks (text matching `LIKE '___-__ __:__%'`,
    i.e. the `[MM-DD HH:MM speaker]` format from `_transcript()`,
    written by the pre-v1.72 `_fallback()` path).
  - 5 markdown-fragment chunks ending in `---`.
  - 269 legitimate chunks retained (shortest surviving = 5 chars,
    a complete short sentence, not punctuation).
  - DB compacted 77,209,600 -> 53,055,488 bytes (24MB reclaimed via
    `VACUUM` after WAL checkpoint).
  - Backup at `hippocampus.db.pre_v172c.<ts>.bak` (+ wal/shm) kept
    on disk for rollback.

### Added
- **Version self-check banner** in `InjectHandler.__init__()`:
  emits `[hippocampus] v1.72b loaded: diary-label=[\u6700\u8fd1\u65e5\u8bb0], LRU
  dedup=enabled, recent-dialog engram.id dedup=enabled,
  _fallback=return None` on every construct.  Operator can confirm
  in AstrBot logs whether the new code is actually loaded (vs
  cached sys.modules).
- **MemoryConfig fields** for the two prompt overrides
  (see Changed above).
- **Working-dir <-> install-dir sync procedure** documented in
  `desktop/engram-context-issues-2026-07-29.md`.  The AstrBot
  install lives at
  `C:\Users\chiriu\.astrbot\data\plugins\astrbot_plugin_engram_core\`
  (a separate copy from the workdir).  Patches must be copied over
  and `__pycache__` cleared for them to take effect.  A hard-kill
  of the AstrBot Python process (not just `/plugin reload`) is
  required to flush module-level caches.

### Smoke
- 6 unit tests for `MemoryService.recall()` dedup covering all four
  WM \u2229 completer boundary cases (empty / fully overlapping /
  fully disjoint / partial), with scores / confidences array
  alignment preserved.
- 4 tests for `DiaryWriter._system_prompt()` /
  `_build_prompt()` override paths and `_fallback()` returning
  `None`.

### Operational notes
- After upgrade: hard-kill AstrBot Python process, restart, look
  for `[hippocampus] v1.72b loaded` in logs.  If missing,
  `importlib` cache not invalidated -> check plugin loader.
- DB had pre-existing garbage written by v1.67.3-era
  `_fallback()`.  v1.72c deletes 282 chunks; backup kept at
  `hippocampus.db.pre_v172c.<ts>.bak` for one week.
- Reference: livingmemory's `RRFFusion.fuse()` uses
  `all_doc_ids = set()` at the merge point for the same kind of
  cross-route dedup.  engram's two merge points (RRF in
  `PatternCompleter`, WM-prepend in `MemoryService.recall()`) now
  both apply set-based dedup.

## [1.67.3] - 2026-07-25

### Fixed
- **WAL explosion**: `hippocampus.db-wal` could grow to 17GB+ and fill the disk.
  Root cause: `decay_pass` issued one upsert (implicit transaction) per engram,
  generating N independent write transactions per decay sweep against 9+ concurrent
  connections. Combined with no explicit `wal_autocheckpoint` and no manual
  `wal_checkpoint(TRUNCATE)`, the WAL accumulated indefinitely when any reader
  (e.g. dashboard polling) blocked auto-checkpoint.
  - `sqlite_util.py`: added `PRAGMA wal_autocheckpoint=1000` on every connection.
  - `storage.py`: `decay_pass` now uses `executemany` batch UPDATE — N engrams
    produce 1 fsync instead of N.
  - `service.py`: `run_memory_decay` forces `PRAGMA wal_checkpoint(TRUNCATE)`
    after each decay sweep.

## [1.67.2] - 2026-07-03

### Fixed
- `InjectHandler.handle_inject()` (TextPart path) now strips its own
  previously-injected `<engram-context>` blocks from
  `req.extra_user_content_parts` before appending the fresh set, so
  the parts list does not grow unboundedly across multiple
  `on_llm_request` firings in the same conversation (retries, or
  multi-turn sessions where the list is not reset between turns).
  Mirrors the find-and-replace pattern used by
  `astrbot_plugin_emotion_state_machine` (HTML-comment sentinels:
  `<!-- esm:emotion-block:start/end -->`), adapted to our XML-tag
  marker (`<engram-context>`) so the visual/structural separation
  from v1.67.1 is preserved.
- Triple-match (open tag + close tag + at least one known inner
  label `[用户画像]` / `[人物关系]` / `[近期对话]` / `[今日回顾]`)
  is used to identify prior engram blocks; other plugins' TextParts
  (e.g. `RAG-Faiss-Memory` from livingmemory, `esm:emotion-block`
  from emotion_state_machine) are left untouched.

### Smoke
- `tests/_smoke_v71.py`: 4 new tests covering
  `_strip_prior_engram_blocks` triple-match, empty/None safety, the
  strip+re-inject round-trip (two consecutive `handle_inject` calls
  leave exactly 1 engram block, not 2), and the cross-plugin
  preservation guarantee (emotion + livingmemory blocks survive a
  re-injection round).

## [1.67.1] - 2026-07-03

### Fixed
- `InjectHandler.handle_inject()` (v1.66+ TextPart path) had two
  bugs that together caused the LLM to treat injected background
  blocks as parallel user questions (issue #8, reported 2026-07-02):
  1. **Order bug**: the loop used `parts_list.insert(0, part)` for
     the `position="before"` branch, which is LIFO and reversed the
     declared order `persona -> relation -> memory -> diary` into
     `diary -> memory -> relation -> persona`.  Fixed by building
     the part list first, then splicing it in with
     `parts_list[0:0] = new_parts` (or `extend()` for `after`).
  2. **No visual separation**: injected TextPart blocks and the real
     user message landed in the same user-content segment with no
     structural marker, so the LLM pattern-matched on the inner
     `[用户画像]` / `[人物关系]` / `[近期对话]` / `[今日回顾]` label
     alone and answered each block as a separate question.  Fixed
     by wrapping every injected block in
     `<engram-context>...</engram-context>` (inner `[xxx]` label
     preserved for backward compat).  Applies to both the TextPart
     path and the fallback string-concat path.
- Also fixed `inject.py:144-151` smoke coverage gap: existing
  `_smoke_v28.py` and `_smoke_v31.py` use a `_Req` without
  `extra_user_content_parts`, so they exercise the fallback path
  only and would never have caught the TextPart order bug.  A new
  `tests/_smoke_v70.py` now stubs `astrbot.core.agent.message`
  with a real-ish `TextPart` class and asserts declared order,
  wrap presence, and `mark_as_temp` on every engram block.

### Smoke
- `tests/_smoke_v70.py`: 3 new tests covering the TextPart path
  (4-block declared order + `<engram-context>` wrap +
  `mark_as_temp`, `position="after"` does not mutate prior parts,
  selective gating by per-type config is respected).  v28 and v31
  assertions updated to expect the new wrapped format.

## [1.67.0] - 2026-07-01

### Fixed
- `handle_poke()` in `handlers/event/observe.py` now reads `persona_id`
  from `event.get_extra("hippo_persona_id")`, mirroring `_extract()`.
  Without this fix, poke lines landed in `daily_messages` with
  `persona_id=""` and `channels_with_lines()` returned `(channel_id, "")`
  as a distinct diary group, producing two diaries per day: one with the
  active persona (containing all normal messages) and one empty-persona
  diary containing only poke history.
- Same root cause as the early v1.36 persona-scoping rollout, but pokes
  were missed at the time.  Code fix is 5 lines; existing poke rows in
  `daily_messages` with `persona_id=""` need a one-shot cleanup
  (`DELETE FROM daily_messages WHERE persona_id='' AND content LIKE '%戳%'`)
  to stop the double-diary symptom on next `/mem diary` run.

### Smoke
- `tests/_smoke_v69.py`: 4 tests covering poke+msg under same persona
  collapsing to one group, legacy empty-persona row still splitting (so
  future regression is caught), `lines_in_range` returning both poke and
  message content, and `ObserveHandler.handle_poke` importability.

## [1.66.0] - 2026-06-30

### Added
- Dashboard persona tab (v1.65): list / expand-detail / edit summary+tags /
  delete / trigger LLM rebuild.  Uses existing `PersonaStore` CRUD; five new
  page API endpoints (`GET persona list+detail`, `POST build+update+delete`).
- `_conf_schema.json` persona config descriptions enriched with usage notes.

### Changed
- `InjectHandler.handle_inject()` no longer concatenates recalled memories
  into `req.prompt` as a raw string.  Each block (persona / relations /
  episodic memory / diary) is now a structured
  `TextPart(text=..., type="text").mark_as_temp()` appended to
  `req.extra_user_content_parts`.  Follows the social_context /
  ESM v0.9.x pattern: static rules in `prompt=`, dynamic data as
  independent temp TextParts.  Gracefully falls back to string concat
  on pre-v4 AstrBot builds where `TextPart` is unavailable.

## [1.64.0] - 2026-06-30

### Added
- `/mem debug <query>` command: dual-route retriever diagnostic report showing route
  distribution (document/graph/spread), per-engram RRF breakdown, MMR-cut candidates,
  and summary counts.  Warms up the underlying `DualRouteRetriever.explain()` which was
  previously only used in smoke tests.
- `handlers.format.format_debug()`: ~120-line renderer with four sections (route dist,
  top-k detail, candidates cut, summary).  i18n-ready via `t()` calls backed by new
  `debug.*` keys in zh.json / en.json (18 keys + `/mem debug` help line).

### Fixed
- `DualRouteRetriever.explain()` previously only fused `document + graph` routes while
  `search()` additionally fused `spread`.  This caused the diagnostic to silently
  under-report hits when spread contributed.  Now both methods use the same routes
  tuple construction (B14 invariant: every `search()` top-k must have an `explain()`
  attribution).

### Changed
- Version bump: 1.63.0 → 1.64.0 (`hippocampus/__init__.py` + `metadata.yaml`).
- `metadata.yaml` description condensed to v1.64 feature set.
- ROADMAP: B14 marked shipped; B11/B12/B13 marked deferred with rationale.
- `handlers/__init__.py`: `format_debug` re-exported alongside other format functions.

### Smoke
- `tests/_smoke_v68.py`: 9 tests covering explain() route enumeration, format_debug
  0-hit / normal / small-k paths, explain-search alignment invariant, CommandRouter
  registration, and handlers-package re-export.  All pass alongside v65/v66 (no
  regression).
