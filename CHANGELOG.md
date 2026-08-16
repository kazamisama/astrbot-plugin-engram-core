# Changelog

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
