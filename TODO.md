# TODO / 待办候选（合并版 · 2026-07-02 重整）

> 将原本分散在 ROADMAP.md / TODO.md / TODO_summarization_B.md 的所有候选项
> 整合到本文件。每项标注：状态（shipped / not started / declined）+ 版本 / commit
> + 备注（现状/原因/下次评估点）。新候选请追加在末尾"持续观察"段。

## 1. 已完成的所有里程碑

按版本顺序列出，每个里程碑列出实质改动，避免展开内部细节（细看 ROADMAP/CHANGELOG）。

### v1.3 — 双路召回 + Agent Tool（commit b7e0xxx 之前，详见 git log）

| ID | 项 | 备注 |
|----|----|----|
| A1 | 双路召回 (document + graph) + RRF 融合 | `hippocampus/retrieval/` |
| A2 | `/mem search --mode=dual` 路由 + 渲染 | 已稳定多年 |
| A3 | LLM Agent Tool (`recall_long_term_memory` / `memorize_long_term_memory`) | `hippocampus/tools.py` |
| A4 | RRF 业务 ID 去重（修 vec+fts 同 engram 不合并） | |
| A5 | 版本三方对齐（metadata / __init__ / _registered_version） | |
| A6 | smoke v08–v09 (7→9) | |

### v1.4 — 文本质量 + 会话策略 + 记忆原子 + 图存储 + 工程化

| ID | 项 | 备注 |
|----|----|----|
| B1 | TextProcessor (jieba + 停用词 + 否定词) | `hippocampus/processors/text_processor.py` |
| B2 | 群聊被动捕获 + session 过滤 | `hippocampus/session_filter.py`，`/mem session` 命令 |
| B3 | MemoryAtom 数据层 + store + lifecycle | `hippocampus/atom_store.py` |
| B4 | GraphStore + GraphRetriever（reverse-index 走 SQL） | `hippocampus/graph_store.py` |
| B5 | 补 3 个 Agent Tool (forget / list_recent / search_by_entity) | `hippocampus/tools.py` 5 个 tool |
| B6 | handlers/event/{observe,recall,manage}.py 拆分 + CommandRouter | ~44% main.py 瘦身 |
| B7 | ConfigManager 类（替代裸 dict）+ LABELS（67 字段 i18n 预抽） | `hippocampus/config_manager.py` |
| B8 | i18n 框架（zh + en） | `hippocampus/i18n_backend.py` + `i18n/*.json` |
| B9 | AstrBot Dashboard WebUI（PluginPageApi + 8 endpoints） | `page_api.py` + `page_api_modules/*` |
| B10 | BackupManager + db_migration | `hippocampus/managers/backup_manager.py` + `db_migration.py` |

### v1.4.x — B3/B4 wire + 异步维护循环

| ID | 项 | 备注 |
|----|----|----|
| - | B3 数据层接进 service | `MemoryService._post_ingest` wire atom + graph 块 |
| - | 异步维护循环（start/stop/run_decay/run_gc） | 参考 livingmemory，sync 退化到独立线程 |
| - | spread_activation facade 修 kwargs 转发 | commit 14187ec |

### v1.5 — auto-inject（commit 84d2xxx，TODO#1 兑现）

| ID | 项 | 备注 |
|----|----|----|
| - | `InjectHandler` + `@filter.on_llm_request()` | `handlers/event/inject.py` |
| - | auto_inject_enabled 默认开；top_k 默认 3 | configurable |

### v1.6.x — 扩散激活接入主召回（commit 918efea +）

| ID | 项 | 备注 |
|----|----|----|
| v1.61 | `engrams_for_batch` + `recent_for_actor` + `top_by_importance` | graph 路由主路径 O(度数) |
| v1.62 | spread 路由独立 RRF 融合 + reconsolidation 全路由触发 | `RouteKind.SPREAD` |
| v1.63 | session-context 种子 + MMR 多样性重排 | `Cue.session_id` + `_mmr_rerank` |

### v1.65 / v1.66 / v1.67 — Persona + 注入重构 + Bug 修复（本次窗口）

| ID | 项 | 备注 |
|----|----|----|
| v1.65 | Dashboard 用户画像 tab（list / detail / update / delete / build） | commit faad2f9，`page_api_modules/persona.py` 新 133 行 |
| v1.65 | `_conf_schema.json` persona 字段描述丰富化 | commit f4b18ac |
| v1.66 | 注入改结构化 TextPart 块（mark_as_temp + extra_user_content_parts） | commit 8abc052；借鉴 social_context/ESM v0.9.x 模式 |
| v1.67 | handle_poke 读 persona_id 修复双日记 bug | commit 4a19e77 |
| v1.67.1 | **issue #8** 注入块顺序 bug + `<engram-context>` XML 包装 (8.1 + 8.2) | commit 5192229；v70 新烟测覆盖 TextPart 路径（v28/v31 走 fallback 漏过） |
| v1.67.2 | **issue #8** re-injection 防御：`_strip_prior_engram_blocks` 三元匹配 (8.5) | commit 0953679；参考 emotion_state_machine find-and-replace；v71 4 条断言 |
| B14 | `/mem debug` 命令 + 修 explain() spread 路由 latent bug | commit 7a69b57 |
| B12 | CHANGELOG.md（Keep a Changelog 风格，人工维护） | commit c044023 + c4a1c60 |

### B 方案完成状态（v1.17-v1.21）— TODO_summarization_B.md 兑现

| ID | 项 | 备注 |
|----|----|----|
| B-1 | 会话总结（per-channel 缓冲 + idle/timer 双触发 + LLM 总结） | v1.17/v1.18 |
| B-2 | 关系层（relations + supersedes + confidence 推翻更新） | v1.19 |
| B-3 | 日记层（per-channel 日记 + 12:00 触发 + 0-6点裁断 + 分块召回 + 独立配额 + 来源标签） | v1.20 |
| B-4 | WebUI 编辑（`/memories/update` 端点 + 前端表单 + 改文本重嵌） | v1.21，附带 ON CONFLICT 漏字段 bug 修复 |
| - | bot 自身消息入库（`@filter.on_llm_response()`） | v1.56/bot 视角日记 | 

### TODO.md 候选兑现（v1.10 前后已 ship）

| ID | 项 | 备注 |
|----|----|----|
| TODO#1 | 召回自动注入 | ✅ v1.5（见 v1.5 段） |
| TODO#3 | 会话聚合 | ✅ v1.6（SessionAggregator） |
| TODO#4 | BM25 / 可配置分词器 / 写入去重 | ✅ v1.10/v1.11 |
| 6.2 | Persona 画像引擎 | ✅ v1.8 + v1.9（tags + LLM 总结） |
| 6.3 | SQLite WAL | ✅ v1.7 |
| 6.4 | 聊天内管理命令 | ✅ 全套 `/mem *` |
| 7.1 | persona tags | ✅ v1.9 |
| 7.2 | 泛化词质量校验 | ✅ v1.9 |
| 7.3 | FTS 分词器可配 (char/bigram/jieba) | ✅ v1.10 |
| 7.4 | Jaccard 词级去重 | ✅ v1.11 |

---

## 2. 未完成（评估后仍可能值得做的）

### 2.1 GC 判据改 `access_count` 逻辑 — declined (confirmed 2026-07-02)

**来源**：TODO.md#2

**现状**：`hippocampus/storage.py:455`
```python
and e.access_count == 0   # 永不衰减的 access_count 锁死 GC
```

**为什么 declined**：
- 已知副作用："曾经进过 top-k 即永不回收" → engram 实际只删"自创建起从未召回过"的纯冷记忆
- 改判据影响范围大（atom + 主 engram 两层都要对齐），实际影响小（0 engram 测试库无观测对象；当前 dev 部署也跑不到 10K+ 量级）
- 用户评估（2026-07-02）："TODO.md 中未完成项仅剩 GC 判据，已确认有修复方案，但 ROI 不高"
- 下次评估点：dev 库 engram > 1000 且 P99 GC > 0ms（持续观察）

### 2.2 write_ops 断电恢复 — not started

**来源**：ROADMAP 借鉴参考段第 3 条；README/todo 历史

**现状**：`hippocampus/` 下 0 个 write_op 命名的文件/类

**重复建设的负担**：
- 用户评估（2026-07-02）："low priority; memory write ops 通常 1-10ms，crash window 极窄"
- 当前 dev 部署单用户测试，crash 风险本就低
- 真实生产部署（多 bot、多 session）才需要

**下次评估点**：第一个生产部署出现 / 实测到 1 条不一致 engram

### 2.3 Persona 定时重建（scheduler）— not started

**来源**：TODO.md#6.2（数据已落地，scheduler 缺）

**现状**：0 个 persona_rebuild_* 命名的函数 / 方法 / 后台线程

**为什么 declined**：
- `persona_store` + `build_persona` + `/mem persona` 命令三件套已完整
- 手动 `/mem persona` 触发；Dashboard 也能点"↻生成"
- 自动化收益：单用户场景下，攒够 20 条 engram 的周期可能数周到数月，等不到几小时一次的扫描
- 多用户场景下价值上升

**下次评估点**：dev 库 actor_id distinct > 2 + 画像改动频率 > 1/月

### 2.4 B11 graph route 全 SQL 下推 — deferred (有条件)

**来源**：ROADMAP P2 段

**现状**：`graph_engram_refs` reverse index + `engrams_for_batch` SQL JOIN 已落地（B4 + v1.61）。残留位置：`tools.py:_list_recent_handler` / `_search_by_entity_handler` 走 Python 端 `store.list_active(k*20)` + 内存过滤，注释里写 "B11 concern"

**为什么 deferred**：dev 库 0 engram，被 `k*20` cap 限制在 O(1000) 内，实测不影响体验

**下次评估点**：dev 库 engram > 5K 且 `_list_recent` / `_search_by_entity` 实测 > 200ms

### 2.5 B13 GitHub Actions CI — declined (重评估 2026-07-02)

**来源**：ROADMAP P2 段

**现状**：`.github/` 目录不存在

**为什么 declined**：
- 用户是单人开发者，已有手工 sweep 习惯（commit v1.60 "test: full sweep v08-v63"）
- 当前 dev DB 0 engram，跨平台/multi-py 有意义但跨度过大
- 当前手工 workflow：`python tests/_smoke_v65.py && python tests/_smoke_v66.py && python tests/_smoke_v68.py` 已成习惯

**下次评估点**：引入第二个开发者 / 准备公开发布到 AstrBot 市场

### 2.6 合并 LLM 调用 (TODO#7.5) — declined

**来源**：TODO.md 第 7.5 节

**为什么 declined**：
- 当前调用点：encode（每条消息）/ summarise（per burst）/ persona（手动）/ diary（每天 1 次）/ consolidate（手动）
- 单用户场景：LLM 调用总量本身小，合并主要省 token
- encode 走同步路径无法合（必须立即返回）；persona + consolidate 都默认关闭，没合的对象
- 真要省 token，`encode max_tokens=600 → 400` 一步就能省

**下次评估点**：DAU > 10 或接到多用户生产部署

### 2.7 B12 CHANGELOG.md — ✅ 已 ship

**已 ship**，等下一次版本发布时直接加段进 `CHANGELOG.md`。

### 2.8 issue #8 / 8.3 system prompt 提示词约定 — declined (option c, 2026-07-03)

**来源**：issue #8（2026-07-02 用户报告）

**现状**：
- 8.1（顺序）+ 8.2（XML 包装）+ 8.5（re-injection 防御）已 ship（v1.67.1 / v1.67.2）
- 8.3 候选解法：在 engram 的 system prompt 注入路径加一段说明
  ```
  以下标签开头的块是自动注入的背景，不是用户消息：
  <engram-context> / [用户画像] / [人物关系] / [近期对话] / [今日回顾]
  ```

**为什么 declined**：
- 真机 bug（"被当成并列问题答"）已被 8.2 的 XML 包装堵上
- 8.3 是优化层（belt-and-suspenders），不是修复层
- 涉及 system prompt 模板改动，跨插件协调成本高于 8.2
- token 开销多 ~50/请求，且每次 LLM 请求都重发
- livingmemory 已经自发做了 8.3 风格的事（"CRITICAL RULES" inline instruction），证明这条路线有效，但 engram-core 用户暂未撞到需要

**下次评估点**：LLM 把 `<engram-context>` 标签当文本复读 / 误解的首次真机报告

### 2.9 issue #8 / 8.4 跨插件注入协调 — deferred

**来源**：issue #8（2026-07-02 用户报告）

**现状**：
- engram-core（v1.67.2）/ livingmemory / emotion_state_machine 三家都用
  `extra_user_content_parts` + `mark_as_temp()` 注入，**互不感知**
- 4 个候选解法：
  - 统一 marker 规范（如 `block:engram:relation`、`block:social:compressed`）
  - 抽象 AstrBot 注入注册表（plugin 注册 block type + 优先级，core 编排）
  - 三家各管各的 marker（engram: `<engram-context>`，livingmemory: `<RAG-Faiss-Memory>`，emotion: `<!-- esm:emotion-block:start/end -->`），互不污染
  - 都不做（依赖 AstrBot core 自己出 `injection registry`）

**为什么 deferred**：
- 当前三家注入互不冲突（engram 用 temp 块、livingmemory 用 temp 块、emotion 用 temp 块，LLM 都能区分）
- 跨插件协调需要 engram ↔ social_context ↔ emotion_state_machine ↔ livingmemory 四方同意 + AstrBot 配合，改动面太大
- 8.5（re-injection 防御）已经堵上 engram 这边的累积风险，剩下的是「未来某家插件不守规矩」的尾部风险

**下次评估点**：第一次出现两家插件的 marker 撞名 / 第二次出现 LLM 把别家的注入块当用户问题答 / AstrBot 上游推出 `injection registry` 规范

### 2.10 inject.py 双横线 bug — not started

**来源**：v1.67.1 demo 输出发现（`tests/_demo_inject_view.py` 已删，留观测记录）

**现状**：`handlers/event/inject.py:131-132`
```python
dlines = ["- " + t for t, _sc in hits if (t or "").strip()]
```
日记 chunk 文本里若已有 `- ` 前缀，handler 叠一层变成 `- - ...`。**和 issue #8 无关**，历史遗留。

**为什么 not started**：
- 视觉瑕疵，LLM 看得懂
- 修复一行：去掉 `"- " + ` 改成 `t`，或加去重逻辑 `if t.startswith("- ") else "- " + t`
- 风险：影响 [今日回顾] 块所有当前样式的下游消费（如有）

**下次评估点**：下一个 engram 改动触及 `inject.py:131-132` 顺手修；或用户报告日记显示异常

---

## 3. 持续观察（暂未评级）

- format.py 工程债（700+ 行，可拆；不影响功能；不阻塞任何东西）
- 中间版本 smoke（v27-v42、v44、v46-v62 共 33 个）不在 ROADMAP 的"当前绿点"基线里 —— 这些是已通过的中间版本快照，按用户工作流会随每次发版自动清理（下一个 release 起点就把中间版本跳过即可）
- AstrBot plugin 路由 reload 失效问题（v0.8.x AstrBot 框架 bug）：用"卸载重装"绕，框架侧 fix 后自动恢复

---

## 4. 借鉴参考（不复述代码，只记落点）

- `astrbot_plugin_livingmemory` 本地路径：`C:\Users\chiriu\.astrbot\data\plugins\astrbot_plugin_livingmemory`
- 已借鉴且 ship：MemoryEngine 中央 facade → MemoryService（局部）/ write_ops 表 → 未 ship（见 §2.2）/ BM25 retriever → ship（v1.10）/ EventHandler 拆分 → ship（B6）/ i18n_backend → ship（B8）/ db_migration → ship（B10）/ page_api_modules → ship（B9）
- `astrbot_plugin_social_context` 本地路径：`C:\Users\chiriu\.astrbot\data\plugins\astrbot_plugin_social_context`
- 已借鉴且 ship：TextPart 块注入模式 → ship（v1.66）
- `astrbot_plugin_emotion_state_machine` 本地路径：`C:\Users\chiriu\.astrbot\data\plugins\astrbot_plugin_emotion_state_machine`
- 已借鉴且 ship：find-and-replace 防累积模式（HTML 注释 marker）→ ship（v1.67.2, issue #8 / 8.5）
- 持续观察中：3 家插件（engram / livingmemory / emotion_state_machine）都用 `extra_user_content_parts` + `mark_as_temp()` 注入，**互不感知**（见 §2.9）

---

## 5. 来源索引

本合并版的合并来源：
- `ROADMAP.md` — v1.3 / v1.4 / v1.4.x / v1.6.x 已完成段、B11/B13 deferred、借鉴参考段
- `TODO_summarization_B.md` — B-1/B-2/B-3/B-4 已 ship，记忆三层架构草图，B 实施细节（已沉淀进代码）
- `TODO.md` — TODOs 1-7 大部分已 ship

旧文件状态：建议删除 `ROADMAP.md` 和 `TODO_summarization_B.md`（已合并），保留 `TODO.md`。
`CHANGELOG.md` 独立保留（格式不同，功能不同）。
