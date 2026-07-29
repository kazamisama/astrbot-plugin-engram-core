# Changelog

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