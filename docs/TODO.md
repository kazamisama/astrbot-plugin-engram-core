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
  <engram-context> / [用户画像] / [人物关系] / [长期记忆] / [最近日记]
  ```

**为什么 declined**：
- 真机 bug（"被当成并列问题答"）已被 8.2 的 XML 包装堵上
- 8.3 是优化层（belt-and-suspenders），不是修复层
- 涉及 system prompt 模板改动，跨插件协调成本高于 8.2
- token 开销多 ~50/请求，且每次 LLM 请求都重发
- livingmemory 已经自发做了 8.3 风格的事（"CRITICAL RULES" inline instruction），证明这条路线有效，但 engram-core 用户暂未撞到需要

**下次评估点**：LLM 把 `<engram-context>` 标签当文本复读 / 误解的首次真机报告

**➜ 已 ship（v1.76.22，2026-09-21）：评估点已触发，按 8.3 的路线做了，但改了措辞。**

真机报告（2026-09-21 00:55，mortis 会话）：模型在记忆已被正确召回并注入后，
推理里写出

> "The injected engram-context explicitly includes the sexual memory summary…
> My rules are clear: **memories are background; the current conversation governs**…"

这句话在整台机器的任何配置文件/人格卡/插件里都不存在——是模型从包装器的
"背景"措辞里自己合成的，然后拿它当理由**拒绝使用**一条已召回的记忆。
即：不是召回失败，是块本身"只说了一半"。

实现与已 declined 的 8.3 草案有两点不同：
1. **措辞反过来**。8.3 草案复述的是「这是自动注入的背景」——正是引起误解的那句。
   v1.76.22 只补缺失的另一半：「也不要当成可以忽略的背景——它是你自己的记忆，
   与当前话题相关时按事实使用」。
2. **不做 system prompt 模板改动**。note 走我们自己的 temp TextPart，
   外加只挂在一次请求的**第一个块**上（不是每块一个），
   token 开销 72 字符 ≈ 48 token/请求，正好落在 8.3 当初估算的 ~50 之内。

因此当初 declined 的三条理由（跨插件协调成本 / token 开销 / 尚未撞到需要）
在 v1.76.22 里都不成立。

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

### 2.10 inject.py 双横线 bug — 已修复 (2026-08-08)

**来源**：v1.67.1 demo 输出发现（`tests/_demo_inject_view.py` 已删，留观测记录）

**修复 (2026-08-08)**：`handlers/event/inject.py:200` 改为去重逻辑（`t if t.startswith("- ") else "- " + t`），日记 chunk 已有 `- ` 前缀时不再叠加。

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

---

## 6. 2026-10-27 审计收口（v1.77.0）

一次完整审计（完备性 + 冗余性）后的落地清单。详细证据见
docs/HANDOFF_2026-10-27.md 与 CHANGELOG.md 的 [1.77.0] 节。

### 已 ship

| 项 | 内容 |
| --- | --- |
| C1 | diary_message_batch_size / diary_message_flush_interval_seconds 成为真实配置字段（此前落 extra，永远读默认） |
| C2 | /mem help 改为由 engram_command_registry + i18n 生成；补回 persona/session/tier；清掉字面反斜杠 n |
| C3 | 召回权重（document/graph/cross/dynamic/recency）全部作用于主链路（dual-route） |
| C4 | handlers/format.py 的输出全部走 i18n（cmd.），zh/en 各一套；死键从 27 个降到 1 个（recall.related_item 仅 t_list 测试用） |
| C5 | CHANGELOG 按版本切分（1.76.13–1.76.28）；新增 HANDOFF_2026-10-27；README/jieba 描述纠正；page_api 文档串改由 ROUTES 生成 |
| R1 | atoms 由关系抽取驱动，中文关系可产出原子；契约改为实体名字 |
| R2 | consolidation 收敛：maintenance() 每轮必跑，step() 全量手动/强制，LLM 版按开关附加 |
| R3 | 删除 PatternCompleter，recall() 统一走 recall_dual_route() |
| R4 | 删除重复的 _rrf_fuse 与 _mmr_rerank |
| R6 | 删除 17 个生产零调用定义 |
| R7 | 删除 rebuild_state 表 + 3 个 helper（旧库启动时 DROP） |
| R9 | 删除 /page/prompts/detail；_parse_json 两份合并为 hippocampus/jsonparse.py |
| C6 | `_conf_schema.json` 补到 167 个入口（只留 3 个内部字段）；v94 三条断言锁住「字段↔schema 全覆盖 / 类型合法 / 默认值一致」 |
| C7 | 92 个静默 except 走 `hippocampus/observability.py`；v95 用 AST 守卫宽泛 except |
| R5 | 35 个 smoke 的 33 份 `_install_stub` 收敛到 `tests/_smoke_common.py` |
| E1 | embedding 桥接超时改为整段解析的**总预算**，provider 按 `id()` 去重（原来同一实例经两条路径各花 5s = 注入硬超时 10s，整次注入被丢） |
| E2 | 注入超时后 detached worker 不再改动已释放的 `req`（`threading.Event` 停止标志） |
| E3 | 注入 banner 去掉硬编码 `v1.73` 与手转义 `最近日记`（日志乱码） |

### 顺带修掉的真实缺陷

- HippocampalStore.list_active / list_active_by_entity_ref 的 newest-first 只按 created_at 排序，
  同一时钟 tick 内的记忆顺序不稳定（v22 的 list_recent 断言因此偶发失败）；补 rowid DESC 稳定次序。
- 线上日志（2026-10-28）：49 次 embedding 桥接 5s 超时、1 次注入 10s 丢弃；实测 siliconflow
  端点 0.29–0.52s（4096 维），确认是超时结构问题而非 API 慢 —— 见上表 E1/E2。

### 仍然 open（按建议顺序）

1. **R9 剩余：同库多连接 + 六套实体/关系表示**。
   现状：11 个 store 类各自 `sqlite3.connect` 到同一个 DB，`MemoryService` 初始化按开关会建
   6–9 条独立连接（`store / semantic / relation_store / diary_store / lease_store / life_graph /
   prospective_store / profile / persona_store`），各持有自己的 `threading.Lock`，跨连接可见性
   与写并发全靠 SQLite。表示层则同时存在 `entities/relations`、`llm_relations`、`graph_adjacency`、
   `graph_v2`、`profile_facts`、`life_*`。
   为什么没动：连接收敛需要先决定「谁拥有 DB」以及事务边界（当前很多方法是 `with self._lock, conn`
   的隐式提交），表示收敛要定哪个体系是事实源。两者都不是机械改动，做错会伤到线上 170MB 数据。
2. **R8：长期空表**。线上 34 张表里 8 张 0 行：`atoms`（v1.77.0 接通后开始增长，先观察）、
   `cluster_summaries` / `profile_facts`（依赖的开关线上是关的）、`graph_adjacency`（graph_store 仍在
   维护但查询走 graph_v2）、`life_entities` / `life_entity_links` / `task_leases` / `triggers`。
   为什么没动：`task_leases` 对应 agent 工具（claim/renew/release task）这一公开 API，`life_*` 是否上线
   是产品决定 —— 删表等于删功能，需要明确「这些体系还做不做」。
3. **format.py 拆分**。本地化后函数边界清晰了，但文件仍 907 行、15 个 `format_*` 与解析/工具函数混在
   一起。可按 stats / recall / graph / diary 拆模块并让 format.py 做 re-export 门面；纯移动，风险低，
   但收益只是可维护性，所以排在这些之后。

## 7. v1.77.1（2026-10-28）：注入性能与嵌入空间

### 已 ship

| 项 | 内容 |
| --- | --- |
| D1-a | `_cos` 维度守卫（不再静默截断） |
| D1-b | 空向量自愈：激活时 `count_missing_vectors() > 0` 就 `reembed_stale()`（线上 60 条空向量，51 条未遗忘） |
| D1-c | `embedding:fingerprint`（维度 + 探测向量）不匹配 -> 后台全量重建 engram + 日记分块 |
| D2-d | 向量缓存（`hippocampus/vecmath.py` + store/diary 懒建 + 启动后台预热 + 写路径增量维护）；`vector_search` 1.1-1.6s -> 0.034s，`recall_diary_chunks` 3.4-3.9s -> 0.11s |
| D2-e | 注入锁 `_LOCK_WAIT=1.5s` 有界等待，等不到跳过而不是烧完 10s |
| D4-f | LLM 关系（`store_summary` 路径）也产原子；此前 atoms 只由 rule 抽取器产出 |
| D4-h | 跨插件：`your_own_life/life/memory_adapter.py` 由 `StarMetadata` 取 `star_cls`，`life_*` / `task_leases` 的写路径恢复 |

### 仍然 open

1. **cluster 链（D4-g）**：全库 `similar_to='[]'`，只有 `observe()` 里的模式分离会写；生产记忆走 `store_summary`，`cluster_summaries` / `profile_facts` 因此恒为 0。两条路：按 embedding 相似度在摘要写入时建簇并回填 `similar_to`（复用 `PatternSeparator` 的阈值），或删掉这条链与两张表。需要产品决定。
2. **graph_adjacency（D4-i）**：`GraphStore.add_relation()` 无生产调用，查询走 graph_v2。删表，或让 graph_v2 写入时同步维护。
3. **R9 同库多连接 + 六套表示**：见 §6，需要先定「谁拥有 DB / 哪个表示是事实源」。
4. **format.py 拆分**：纯移动，风险低。
