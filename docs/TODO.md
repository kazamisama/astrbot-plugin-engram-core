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
   与写并发全靠 SQLite。表示层则同时存在 `entities/relations`、`llm_relations`、
   `graph_v2`、`profile_facts`、`life_*`（`graph_adjacency` 已于 v1.77.2 删除）。
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
2. ~~**graph_adjacency（D4-i）**~~：✅ 已 ship（v1.77.2，整层删除，见 §8）。
3. **R9 同库多连接 + 五套表示**：见 §6，需要先定「谁拥有 DB / 哪个表示是事实源」。
4. **format.py 拆分**：纯移动，风险低。
## 8. v1.77.2（2026-10-28）：删除 graph_adjacency

### 已 ship

| 项 | 内容 |
| --- | --- |
| D4-i | `graph_adjacency` 整层删除：建表/索引、`add_relation`、`neighbors`、`all_relations`、`rebuild_from_semantic`；旧库打开时 DROP；`stats()` 只报 `engram_refs` |
| D4-i | `GraphRetriever` 去掉 `max_hops` 与 N 跳游走，改为纯锚点实体 -> engram 反查；`DualRouteConfig.graph_relation_hops` 删除 |
| D4-i | 删除级联不再重建整图（原来每次硬删把 2120 条 relations 展开成 3543 行邻接，1.16s；展开后最高频实体 245 邻居，一跳每锚点新增 17-217 条候选） |
| test | 新增 `_smoke_v99.py`；`_smoke_v20.py` 改为反向索引契约。全量 89 个 smoke 全绿 |

### 仍然 open

1. **cluster 链（D4-g）**：需要产品决定（建簇并接注入消费方 vs 删链）。见 §7 open #1。
2. **R9**：见 §6，五套表示（`graph_adjacency` 已删）。
3. **format.py 拆分**：纯移动，风险低。

## 9. v1.78.0（2026-10-29）：删除 similar-to 聚类链

### 已 ship

| 项 | 内容 |
| --- | --- |
| D4-g | `Engram.similar_to` / `cluster_id` / `profile_fact_id` 三字段与 engrams 三列删除；`cluster_summaries` 表删除；旧库打开时 DROP TABLE + ALTER DROP COLUMN |
| D4-g | `PatternSeparator.apply_link` / `expand_cluster` 删除，`resolve()` 只返回 merge / new |
| D4-g | `ReplayConsolidator` 删除聚类 gist 与情节→语义抽象；`maintenance()` 只做 promotion + SWR replay |
| D4-g | dual-route 的簇扩展、激活图的 similar_to 边、MMR 的距离代理全部移除；MMR 改用 embedding 余弦 |
| D4-g | `/mem cluster`、`/mem cluster-list` 与 8 个配置键删除；`_conf_schema.json` 167 -> 159 项 |
| test | 新增 `_smoke_v100.py`；v09/v10/v11/v13/v23/v26/v58/v66 更新。全量 90 个 smoke 全绿 |
| 清理 | 删除失效的一次性脚本 `build_handlers.py`（行号范围早已与 main.py 对不上） |

### 为什么删而不是修

唯一写入点是 `observe()`，线上 `summary_mode_enabled=true` 时消息走 `store_summary()`，从不进入；
唯一独特产出 profile fact 不需要簇（`ProfileStore.build_from_relations` 直接铸），线上实测 267 条
里 266 条是 `resides_in` 句子碎片；簇结果没有任何注入消费方；死链的周期成本是每 30 分钟维护
里 2-3 次全表扫描。保留 merge 去重、干扰抑制、promotion、SWR replay、decay。

### 仍然 open

1. **R9**：同库多连接 + 五套表示（cluster 链删除后剩 entities/relations、llm_relations、graph_v2、profile_facts、life_*），需要先定事实源与事务边界。
2. **R8 空表**：`cluster_summaries` 已删；剩余 `atoms`（观察增长）、`profile_facts`、`life_*` / `task_leases`（跨插件与产品决定）、`triggers`。
3. ~~**format.py 拆分**~~：✅ 已 ship（v1.78.1，见 §10）。

## 10. v1.78.1（2026-10-29）：拆分 handlers/format.py

### 已 ship

| 项 | 内容 |
| --- | --- |
| format 拆分 | 860 行单文件 -> `format_core`（身份/文本助手、banner、search 参数）、`format_stats`（render_stats + 默认模板）、`format_memory`（forget/export/import）、`format_recall`（narrative/profile/activation/graph/confidence/decaycurve）、`format_inspect`（debug/dual-route/session/tier） |
| 门面 | `handlers/format.py` 只做 re-export 并声明 `__all__`；`_extract` / `_bot_actor_id` / `_resolve_bot_name` / `_BOT_NAME_CACHE` / `DEFAULT_*` 等测试直接引用的名字全部保留 |
| 验证 | AST 逐函数比对 22 个函数体与拆分前完全一致；全量 90 个 smoke 全绿 |

### 为什么先做这一项

R9（同库多连接 + 表示层）要先定「谁拥有 DB / 哪个表示是事实源」，R8（长期空表）
删表等于删功能，两项都需要产品决定；format.py 拆分只是移动代码，风险低、不改变行为，
所以先收掉。

### 仍然 open

1. **R9**：同库 6–9 条连接 + 五套表示（entities/relations、llm_relations、graph_v2、profile_facts、life_*）。需要先定事实源与事务边界。
2. **R8 空表**：`atoms`（v1.77.0 接通后观察增长）、`profile_facts`（现有 `remember_fact` 与 `build_from_relations` 两个写入方，线上仍 0 行）、`life_*` / `task_leases`（跨插件修复后待重载验证）、`triggers`（依赖 LLM 产出 topics）。删表需要产品决定。

## 11. v1.78.2（2026-10-29）：修复热重载不重新加载 bundle

### 已 ship

| 项 | 内容 |
| --- | --- |
| 现象 | WebUI 重载后插件仍跑旧代码：metadata 1.78.1、日志横幅 `loaded: v1.77.0`，v1.77.1+ 的修复全部未生效 |
| 根因 | AstrBot 的 `reload()` 只清 `data.plugins.<dir>.*`；本插件把自身目录加入 `sys.path`，以顶层名导入 `hippocampus` / `handlers` 等包，重载时这些模块不匹配前缀、留在 `sys.modules` |
| 修复 | `main.py` 在导入 bundle 前调用 `_evict_stale_bundles()`，只删除**文件位于本插件目录下**的 `hippocampus*` / `handlers*` / `page_api*` / `engram_command_registry` / `engram_core_helpers`（含子模块）；同名的外部模块不动 |
| 时机 | 仅当宿主以点号模块名加载时执行（`_EVICT_ON_IMPORT = "." in __name__`）；smoke 测试以顶层 `main` 导入，不会误清 |
| 验证 | 新增 `tests/_smoke_v101.py`：清理单测 + 子进程以 `data.plugins...main` 点号名加载，断言缓存包被换新、旧标记消失 |
| 线上确认方式 | 重载后日志横幅应为 `loaded: v1.78.2`，随后出现空向量修复 / `vector caches ready`，`hippo_meta` 出现 `embedding:fingerprint` |

### 为什么之前没发现

日志里 `Removed module` 只列 `data.plugins.astrbot_plugin_engram_core(.main)` 两行，看起来重载成功了；
只有比对「metadata 版本 vs 横幅版本」才能发现代码没换。此后每次改核心包都必须看横幅。

### 仍然 open

1. **R9**：同库多连接（实测 WAL + busy_timeout 10s + 0 次锁冲突，不建议合并）与五套表示（各有读写方，合并需先定事实源）。见 §10。
2. **R8 空表**：`atoms` / `life_*` / `task_leases` 为 0 是旧代码仍在跑的直接后果；重载到 1.78.2 后观察一轮再决定去留。`triggers` 依赖 LLM 产出 topics，可能长期空。

## 12. R9 决策（2026-10-29）：五套表示的职责边界

### 结论：全部保留，不合并；边界用测试钉住

| 表示 | 角色 | 写入方 | 读取方 |
| --- | --- | --- | --- |
| `llm_relations` | LLM 三元组，**关系事实的事实源** | `store_summary` 的 relations（带 supersede/置信度） | 注入 `recall_relations`、WebUI 基础图谱、decay/purge |
| `entities` / `relations` | 派生语义图索引（规则抽取 + LLM 镜像） | `_post_ingest` 规则抽取、`_mirror_relation_to_semantic` | dual-route graph 锚点、激活扩散、profile 构建、atom 抽取 |
| `graph_v2`（nodes/edges/entries） | **WebUI-only** 全量图谱（topic/person/fact → memory） | `_index_graph_v2`（每次 ingest 一次 regex 抽取） | 仅 `page/graph/data?full=1` 与统计，**召回不读** |
| `profile_facts` | 按需物化的稳定事实 | `remember_fact` / `build_from_relations`（/mem profile 触发） | /mem profile、decay sweep |
| `life_*` / `task_leases` | 跨插件域数据 | `your_own_life`（adapter 修复后） | 该插件与 /mem 工具命令 |

### 为什么保留 graph_v2 而不是删掉

- 它是 dashboard「全量图谱」开关的唯一数据源；实测 `full_graph_snapshot_v2()`
  818 节点 / 2453 边 / 85 条记忆，**0.04s**；抽取成本 0.04–0.16ms/条，可以忽略。
- 删除会移除一个可用视图，并波及 v82/v90/v92/v93 四个级联测试（它们覆盖
  硬删时 graph_entries/edges 的清理）。收益（省 1.8M 字符 ≈ 3.6MB，相对
  140MB 向量可忽略）远小于风险。
- `graph_entries_v2_content` 确实与 `engrams.content` 有重叠，但按「summary
  优先、content 兜底」使用，且体量小，暂不动。

### 为什么不动连接层

线上 `journal_mode=wal`、`busy_timeout=10s`、48k 行日志 0 次 `database is locked`。
每个 store 自持连接与锁是有意设计；合并连接要统一锁与事务边界，收益不可测，
不属于「缺陷」。

### 新不变量

`tests/_smoke_v102.py`：

1. 追踪 service 的**所有**连接（构造后懒建的也算），断言 `recall()` 与
   `recall_dual_route()` 不执行任何涉及 `graph_nodes_v2` / `graph_edges_v2` /
   `graph_entries_v2` / `graph_entry_nodes_v2` / `graph_edge_memories_v2` /
   `graph_entries_v2_fts` 的语句——以后若有人把 graph_v2 接进召回，测试会红。
2. 反向保证视图仍可用：`_index_graph_v2` 之后 `full_graph_snapshot_v2()` 返回
   nodes / edges / memories，且节点里能读到实体。

### 仍然 open

- R8：`atoms` / `life_*` / `task_leases` 待重载后的真实流量观察；`triggers`
  依赖 LLM 产出 topics，可能长期空。见 §11。

## 13. v1.78.3（2026-10-29）：修复升级库上 atom 层静默失效

### 症状

- 线上 `atoms` 表恒为 0 行，`MemoryService.atom_store is None`，`_ensure_atom_layer()` 返回 `False`。
- 写入 `store_summary` 的 relations 确实落到 `llm_relations` / `entities` / `relations`，但一条原子也不产生。
- 测试全绿——因为测试每次都新建库，新库的 `atoms` 表自带全部列。

### 根因

`hippocampus/atom_store.py` 的 `_init_schema` 里，`CREATE INDEX idx_atoms_expires ON atoms(expires_at)`
与 `CREATE TABLE` 在同一个 `executescript` 中，而给旧表补 `ttl_days` / `event_time` /
`expires_at` 的 `ALTER TABLE` 在脚本**之后**才执行。线上 `atoms` 表建于 v1.76.5 之前，
没有 `expires_at` → 建索引语句抛 `no such column: expires_at` → `AtomStore.__init__`
抛异常 → `service._ensure_atom_layer` 的 `except Exception: self.atom_store = None`
把它咽掉 → atom 层在这台机器上从未启用过。

### 修法

- `hippocampus/atom_store.py`：迁移顺序改为 **建表 → 补列 → 建索引**，四个索引各自独立
  `executescript`，`idx_atoms_expires` 不再早于 `expires_at`。
- `hippocampus/service.py` `_ensure_atom_layer`：`atom_store` / `graph_store` /
  `atom_lifecycle` 三处构造失败各记一次 `_obs_note`，避免「能力被禁用」伪装成「没有数据」。
- `tests/_smoke_v103.py`（12 检查）：用 v1.76.5 之前的 22 列 `atoms` DDL 建库，断言
  `AtomStore` 能打开、补齐三列与 `idx_atoms_expires`、原子可写入并能 `by_triple` 命中；
  再在同一旧库上走生产路径 `store_summary()`（带 `likes` 关系）断言恰好产出一条 preference 原子。

### 验证

- 线上库副本：修复前 `_ensure_atom_layer() -> False`、`atom_store is None`、写入后 `atoms=0`；
  修复后同一份数据上 `store_summary()` 写入 2 条原子：
  `('alice','likes','espresso','preference',0.9)`、`('alice','hates','loud cafes','preference',0.8)`。
- 全量 93 个 smoke 全绿（v08~v103）。

### 仍然 open

- R8 的 `atoms` 一行现在可以真正观察了：重载到 1.78.3 后，等下一次带 relations 的摘要写入，
  确认 `atoms` 从 0 开始增长（而不是再次停在 0）。
- `life_*` / `task_leases` / `triggers` 仍是 §11 的状态。

## 14. v1.79.0（2026-10-29）：按通用编码规则清理

### 触发

用户给了一份 8 条的通用编码规则（不要重复兜底 / 去掉死代码 / 函数不过长 / 测试打真代码 /
文档与实现一致等），要求按它审计整个插件。审计范围：生产 100 文件 / 1005 函数。

### 审计证据

| 规则 | 结论 | 证据 |
| --- | --- | --- |
| 2 不要重复兜底 | 偏离 | 68 处 `getattr(self.cfg, ...)` 自带默认值：38 处与 dataclass 默认值逐字相同，19 处不一致，5 个名字不是 `MemoryConfig` 字段（11 处读取恒落默认值） |
| 2 同类 | 偏离 | 33 处 `getattr(self, "attr", None)` 落在 `__init__` 必然赋值的属性上 |
| 3 不要为一次性代码造抽象 | 偏离 | `VectorIndex.vector()` 0 调用点；`_build_default_registry` 只有 `return default_registry()` |
| 4 函数不过长 | 偏离 | 25 个函数 ≥ 80 行；最长 `_handle_inject_sync` 209 行、`_install_bridges` 202 行 |
| 8 文档与实现一致 | 偏离 | README 称唯一可选依赖是 jieba，实际 vecmath 会机会性 import numpy |
| 7 测试打真代码 | 部分 | 无 mock、192 处 import 生产代码；但 v86 复制了衰减公式常量 |
| 1/5/6 | 合格 | — |

### 已 ship

| 项 | 做法 | 影响面 |
| --- | --- | --- |
| 配置读取单一事实源 | 68 处改直接属性访问，删 `or 默认值`；19 处两个默认值统一到 `MemoryConfig` | conversation_buffer / diary_writer / service / session_buffer / summarizer / dual_route / graph_retriever / observe |
| 幽灵键转正 | 5 个键成为字段 + `_FIELDS` + schema（159 -> 164 项） | `search_cache_enabled` / `search_cache_ttl_seconds` / `search_cache_max_entries` / `diary_consume_cache_on_write` / `bot_actor_id` |
| self getattr | 33 处改 `self.x`（17 个属性均在 `__init__` 无条件赋值） | service.py |
| 死码 | 删 `VectorIndex.vector()` / `HAVE_NUMPY` / `_build_default_registry` | vecmath.py / service.py |
| 长函数 | `_install_bridges` 拆 7 个方法；`_handle_inject_sync` 拆 5 个方法 | handlers/init.py、handlers/event/inject.py |
| 文档 | README / requirements.txt 说明 numpy 是可选的加速路径 | — |
| 测试 | v86 期望值改常量 `0.958854`；新增 v104（12 检查） | tests |

### 不变量（v104 钉住）

- 生产代码里不得再出现 `getattr(<任意>.cfg, "<public field>")`（`_` 前缀豁免，因为
  `_prompt_namespace` 是运行时注入的私有属性）。
- 5 个新键经 `ConfigManager` roundtrip 落到字段、不进 `extra`，且 schema 全覆盖。
- `search_cache_ttl_seconds=0` 时 `_recall_cache_ttl == 0.0` 且 `_recall_cache_get()` 返回 `None`。
- 三个死码符号在仓库里彻底消失。

### 仍然 open

- 宽泛 except 仍有 228+ 处（v1.77.0 已把 92 处纯 `pass` 改为 `_obs_note`）；进一步收敛
  需要按模块逐个判断哪些异常是契约的一部分，不是静态规则能回答的。
- `format.py` 拆分后的 5 个模块仍可再按需细化（当前最大函数已 ≤ 80 行，见 §10）。

## 15. v1.79.1（2026-10-29）：撤掉为旧数据打的补丁

### 触发

用户：「再检查一遍，注意我们要把目光放长远，不一定需要为了旧数据而打补丁」。
原则落成一句话：current code 需要的列必须保证存在；但「为了少几个废弃列/表而在启动或
召回路径上永久保留补丁」不划算——补丁会腐烂，废弃结构本身不花钱。

### 证据（哪些是补丁）

| 位置 | 补丁 | 为什么不该留 |
| --- | --- | --- |
| `hippocampus/retrieval/graph_retriever.py` | 反查为空时 `store.all(limit=10_000)` | 每次未命中都全表扫；掩盖了写路径的 bug |
| `hippocampus/activation.py` | `_neighbors_entity` 的 `store.all(limit=10_000_000)` | 更极端；graph 懒建时生产会走到 |
| `hippocampus/db_migration.py` | 6 条按版本 ALTER，每次打开都 PRAGMA | 新增列要改两处；PRAGMA 常驻启动路径 |
| `hippocampus/storage.py` / `graph_store.py` | 打开旧库就 DROP `rebuild_state` / `cluster_summaries` / 三列 / `graph_adjacency` | 删除无人读取的结构换不来收益 |
| `handlers/init.py` | `count_by_embedding_model(old)` 两处计数驱动 stale re-embed | v1.77.1 的指纹 + 空向量检查已覆盖，计数分支是死路 |

根因（graph 两份表示）：`_mirror_relation_to_semantic()` 在 engram 落库后修改 dataclass，
既没持久化也没写反查索引。线上：556 engram、437 带 `entity_refs`、397 已索引、
**40 个缺失（111 对）**、`graph_engram_refs` 992 行。

### 已 ship

| 项 | 做法 | 影响面 |
| --- | --- | --- |
| 升级路径统一 | `sqlite_util.ensure_columns()`：缺列就 ALTER，只加不删 | storage / atom_store / diary_store / relation_store / persona |
| schema 单一事实源 | `_ENGRAM_COLUMNS`（32 列）生成 CREATE 与升级；索引/FTS/触发器在补列后建 | `hippocampus/storage.py` |
| graph refs 单写者 | `_link_engram_refs()` 同时写 `entity_refs` 与反查索引；`store_summary()` 变化才重存 | `hippocampus/service.py` |
| 一次性回填 | `_mirror_missing_refs()` 在 graph store 首次建立时幂等补写（线上 111 对） | `hippocampus/service.py` |
| 删扫描兜底 | graph route 直接返回；activation 无 graph 时只走 entity-entity 边 | graph_retriever / activation |
| 激活前置建图 | `spread_activation` / `recall_with_activation` 先 `_ensure_atom_layer()` | `hippocampus/service.py` |
| stale 重嵌简化 | 去掉 legacy 计数，`_check_embedding_space()` 末尾无条件执行 | `handlers/init.py` |
| 删除迁移模块 | `hippocampus/db_migration.py` | 每次启动少 6 次 PRAGMA |
| 不再做减法 | 废弃列/表保留但不读不写 | storage / graph_store |

### 不变量（v105 钉住）

- `store_summary()` 带 relations 时：`entity_refs` 随行持久化，且反查索引恰好 2 行。
- 只有 `entity_refs` 的旧行在 graph store 建立时被回填；第二次回填不新增行。
- graph route 未命中时 `store.all()` 调用 0 次（回归到全表扫会立刻红）。

### 验证

- 手工（temp DB）：store_summary → 2 refs；清空后重建 → `backfilled 2 graph reference(s)`；
  `spread_activation(['Alice'])` 3 节点、store.all 0 次。
- 全量 95 个 smoke（v08~v105）全绿。

### 仍然 open

- 旧库是否彻底不支持（删废弃列/表、不再兼容 v1.x schema）——需要用户决定；当前策略是「保留但
  不读不写」，代价为零。
- 保留的两处兼容不是数据补丁：`inject.py` 的 `[近期对话]` / `[今日回顾]`（strip 契约）、
  `handlers/recall.py` 的 provider API 探测（宿主版本兼容）。要去掉它们得先改公开契约或
  提高宿主最低版本。

## 16. v1.79.2（2026-10-29）：embedding 归档为 float32 BLOB + 一次性迁移

### 触发

用户问「本机旧库能改造迁移吗？」。检查结论：**结构已经迁完了**——线上库 `engrams` 正好
32 列，与 `_ENGRAM_COLUMNS` 集合一致，`cluster_summaries` / `rebuild_state` /
`graph_adjacency` 都不存在（v1.78.0 打开时清的），`quick_check` ok。剩下的「旧」是存储形态：
86% 的体积是 embedding 的 JSON 文本。

### 证据

| 项 | 行数 | JSON | float32 BLOB |
| --- | --- | --- | --- |
| `engrams.embedding_json` | 547 | 50.4 MB | 8.96 MB |
| `diary_chunks.embedding` | 1112 | 102.2 MB | 18.2 MB |
| 合计 | 1659 | 152.7 MB | 27.2 MB |

冷启动预热 3.45s（engrams 0.86s + diary 2.59s）几乎全是 `json.loads`。纯 VACUUM 只能回收
1.7 MB freelist，不值得单独做。

### 已 ship

| 项 | 做法 |
| --- | --- |
| 编解码 | `vecmath.pack_vec()` / `unpack_vec()` / `decode_vec()`（小端 float32；blob 优先、JSON 回退） |
| schema | `engrams.embedding_vec BLOB`、`diary_chunks.embedding_vec BLOB`，`ensure_columns()` 补列 |
| 写路径 | `upsert()` / `update_embedding()` / `add_chunks()` 只写 BLOB，文本列置空 |
| 读路径 | `Engram.from_row()` / `_vector_index()` / `DiaryChunk.from_row()` / `_chunk_index()` / `chunk_diary_ids()` / `count_missing_vectors()` / `reembed_stale()` |
| 迁移脚本 | 根目录 `compact_embeddings.py`：干跑 / `--yes` / `--no-vacuum`；备份 → 转换 → FTS rebuild → VACUUM → 校验 |

### 实测（线上库副本）

- 186.3 MB → 55.8 MB；转换 4.7s；`quick_check` ok；FTS integrity-check ok。
- 行数 / 向量数不变（556/547、1145/1112）。
- 预热 3.45s → 0.41s。

### 不变量（v106 钉住）

- 新写入必须落在 `*_vec` 且文本列为空；`store.get()` / `all_chunks()` 能解码。
- 只有 JSON 的旧行照常可读、可搜索；下一次写入即转成 BLOB。
- 迁移脚本保持行数与向量值不变，重复执行是 no-op。

### 仍然 open

- 线上库的实际转换要停一次 AstrBot（VACUUM 需要独占）；脚本已可在副本上完成全流程。
- 不打算进一步压到 float16：4096 维下再省一半，但会动向量精度，收益不足以抵消风险。

## 17. v1.79.3（2026-10-29）：嵌入标签记录真实模型名

### 触发

用户问「还有什么问题吗」。上一轮遗留的两条小瑕疵之一：线上 554 个 engram 的
`embedding_model` 全是 `astrmock`，但 `embedding_json` 是 4096 维——实际由宿主的
Qwen3-Embedding-8B 产生。`astrmock` 是桥的注册名，不是模型名。

### 为什么这是一个真问题

- 指纹（v1.77.1）能发现「空间变了」，但标签若永远是注册名，诊断、切换判断、导出/迁移都会
  被误导；如果指纹丢失或首次升级，标签是唯一线索。
- `reembed_stale()` 按标签选择旧行：标签不真实时，它无法区分「空间里的旧向量」与
  「名字没更新的同空间向量」。

### 已 ship

| 项 | 做法 |
| --- | --- |
| 注册名/标签分离 | `current_embedding()` 返回注册名；新增 `embedding_label()`；`set_embedding(name, label=None)` |
| 激活取真实模型名 | `handlers/recall.py:embedding_provider_model()` 读 provider 的 `model`（不联网） |
| 指纹 | `model` 字段改用 `embedding_label()` |
| 同空间重标签 | `_check_embedding_space()` → `retag_embeddings()`，只 UPDATE 元数据 + 同步内存索引，不重嵌 |
| 维度残留 | `reembed_mismatched_chunks(dim)`：只重嵌维度不符的 chunk（线上 4 条 64 维） |
| 展示 | `/mem model` 增加 `embedding.label`；stats/横幅显示标签 |

### 不变量（v107 钉住）

- `retag_embeddings()` 不调用 `embed()`，行标签改完后 `vector_search(embedding_model=新标签)` 仍命中。
- 探针匹配（空间未变）+ 旧标签 → 只重标签、重写指纹、`_start_full_reembed` 不被调用。
- 维度不符的 chunk 被重嵌，健康 chunk 的 embed 次数不变，文本保留。

### 仍然 open

- 线上要重载一次才会把 547 + 1112 行的标签改成真实模型名（重载后自动、无 embedding 调用）。
- provider 没暴露 `.model` 时标签回退到注册名；宿主若只在 `provider_config` 里放 `embedding_model`，
  已按该键回退。

