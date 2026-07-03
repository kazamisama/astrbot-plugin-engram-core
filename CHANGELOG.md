# Changelog

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