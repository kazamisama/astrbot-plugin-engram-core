# Engram 记忆总结化改造 · 确定方向 TODO (B 方案)

> 本文仅记录**已与用户确认**的方向与已查证的技术事实。代码未动。
> 前置：必须先验证 A（v1.16 修复的 set_llm）LLM 链路已接通，否则 B 全部依赖 LLM 无法跑。

## 已确认的用户需求（拍板）
1. **只存总结条**：逐条原文不入长期库，仅进工作记忆；只有 flush 的总结条入库。
2. **双触发**：会话冷却（idle）+ 定时强制总结，两者互补。
3. **私聊 idle 阈值更长**，群聊更短（示例：私聊 30min / 群聊 5min，最终值可调）。
4. **按总字数成比例压缩**：总结目标长度 ∝ 原文字数。
5. **明确主谓、话语关系、人物关系、时间关系**（livingmemory 风格）。
6. **关系可推翻更新**：同一对人物出现新关系且与旧关系冲突时，能纠正而非叠加矛盾。
7. **每条关系带独立置信度（confidence）**，作为推翻更新的判据。

## 已查证的技术事实（免重查）
- **私聊/群聊判别零成本**：`handlers/format.py:_extract` 已用 `event.get_group_id()`（DM 为空、群聊有群号，现被兑底成 "default"）；另 `unified_msg_origin` 格式 = `platform:type:sid`，type 段即会话类型。两条路都现成，不需碰 AstrBot 内部。
- **会话缓冲现状**：`hippocampus/session_buffer.py` `SessionAggregator` 是 **per-(channel_id, actor_id)** 分桶，只存 `content` 文本行（**丢了发言人名和时间戳**），idle 是单一全局值 `session_aggregate_idle_seconds`。冷却触发逻辑 `_is_idle()` 已有。
- **后台任务框架现成**：`service.start_background_tasks()` 有 asyncio 任务 + 降级守护线程框架（现跑 tier 重算 / atom GC），可复用加定时 flush。
- **Engram 无 chat_type 字段**；无 participants/relations 结构化字段；`supersedes` 字段存在但几乎无写入逻辑；`confidence` 是条目级不是关系级。
- **observe 入口**：`handlers/event/observe.py` `ObserveHandler.handle_message` → `_extract(event)` → 走聚合或直接 observe。聊天同步入口在这里。
- **总结入库复用点**：service 已有 LLM provider（astrmock bridge）+ encoder 的结构化抽取（v1.16 修复后生效）。总结器可复用这条 LLM 链路。

## 分阶段计划

### 阶段 0（前置，用户验证中）
- [ ] 验证 A：重启 AstrBot，确认新记忆是 LLM 摘要而非逐句原文（LLM bridge 接通）。

### B-1：会话总结主链路（先做）
- [ ] 新增 `conversation_buffer.py`：**per-channel**（不按 actor 分桶）会话缓冲，每行保留 `[发言人 时间] 内容`（群聊总结需跨发言人看整段，现 per-actor 结构做不到）。
- [ ] chat_type 判定：`get_group_id()` 非空=群聊 / 空=私聊（或解析 unified_msg_origin type 段）。
- [ ] 双触发：冷却（idle，按 chat_type 选 idle_private/idle_group）+ 后台定时 flush 活跃但久未落的会话。
- [ ] 新增 `summarizer.py`：算总字数→定比例压缩目标长度；LLM prompt 明确要求输出主谓/人物关系/时间关系/key_facts；解析结构化输出。
- [ ] 存 1 条 Engram（summary=总结）；**逐条原文不入长期库**（只存总结条，选项 2）。
- [ ] config + schema：idle_private / idle_group / 定时总结周期 / 压缩比 / 开关（中文说明）。
- [ ] 新增 chat_type 字段到 Engram（区分私聊/群聊记忆）。
- [ ] 烟测：buffer 双触发 / summarizer 解析 / 端到端。

### B-2：关系推翻更新（后做，风险隔离）
- [ ] participants/relations 作为结构化字段存储（每条关系带独立 confidence）。
- [ ] 关系归一化层：以 (人物A, 人物B) 或 (主语, 关系类型) 为键，新总结查同键旧关系。
- [ ] 冲突检测 + 推翻：新关系与旧冲突且 confidence 足够 → 置 `supersedes` + 软忘旧条；信度低→仅候选不覆盖。
- [ ] 参考 livingmemory `entity_resolver` + `graph_extractor`。
- [ ] 烟测：冲突取代 / 补充不覆盖 / 信度门槛。

## 未定（待讨论）
- idle_private / idle_group / 定时周期的具体默认值。
- 压缩比默认值（原文:总结）。
- 总结条的 memory_type 是否标 semantic。
- 私聊是否用更简化的总结 prompt（只有用户+bot两方）。


---

## 补充：v4 讨论确认项（人格预填充 + 日记层 + 关系注入）

### 已查证事实（免重查）
- **关系如何被 LLM 知道**：当前 `relations` 走 `recall_semantic`（`service.py`）独立链路，**需显式调用 / LLM 工具调用**才能拿到。自动注入 `handlers/event/inject.py` 用的是普通 `recall()`，**只拼 engrams 的 summary，从不碰 relations**。→ B 必须在 inject 里补拼 relations，关系才会默认进上下文。
- **人格预填充可行**：livingmemory `_build_system_prompt_with_persona` 已验证此法；engram `persona.py` 有 PersonaStore + `build_persona` 已用 LLM。总结器调 LLM 时取当前会话绑定人格的 system_prompt 预填充即可。

### 新增确认需求
8. **所有总结类 LLM 调用预填充对应人格的 system_prompt**（会话总结 + 日记都要，更符合心境）。
9. **注入层补 relations**：inject 时除 summary 外，把关系（带 confidence 阈值过滤）也拼进 LLM 上下文。
10. **日记层（独立高层记忆单元）**：
    - 每天固定 **中午 12:00** 触发，总结「前一天」。
    - 当天消息由插件**本地缓存到独立表**（与长期库分开）。
    - **裁断判定**：取夜间最晚冷却点（30min 无话题）作为一天边界；12 点后跨日仍算前一天，日记中需明确是「某日早 → 次日凌晨 X 点」。
    - 总结约 24h 区间，**明确时间顺序**。
    - 压缩：按裁断大区间的**总会话轮数**等比例，**最长不超 500 字**。
    - 作为单独记忆单元（`memory_type="diary"` 或专门标记）。

### 日记层设计决策（用户已拍板）
- **当日消息缓存**：独立 SQLite 表；**自动丢弃 7 天前的记录**。
- **夜间裁断窗**：**凌晨 0–6 点**找最后一个冷却点（30min 间隙）。
- **日记粒度**：**每个群/私聊各一篇**（per-channel）。
- **无夜间冷却点退化**：**按 0:00 截断**。

### 三层记忆体系（最终架构草图）
```
第1层 会话级总结 (B-1)    : per-channel 缓冲 -> idle/定时 -> LLM 总结一段 -> 1条记忆 (人格预填充)
第2层 日记级总结 (B-3,新): 当日原始消息表(7天TTL) -> 每天12:00 + 夜间裁断(0-6点) -> LLM 总结一天 -> 1篇日记/channel (≤500字, 人格预填充)
关系层 (B-2)              : 总结产 relations(带confidence) -> 推翻更新 -> 注入时拼进上下文
```

### 分阶段补充
- **B-1** 顺手加：人格 system_prompt 预填充（需求 8，会话总结先用上）。
- **B-2** 加：注入层拼 relations（需求 9）。
- **B-3（新，日记）**：
  - [ ] 独立当日消息表 + 7天 TTL 自动清理。
  - [ ] 12:00 到点触发器（算到下个 12:00 的延迟）。
  - [ ] 夜间裁断算法：0-6点最后一个 30min 冷却间隙；无则退化 0:00。
  - [ ] per-channel 逐篇日记；diary memory_type；按轮数比例压缩 ≤500字；明标跨日边界。
  - [ ] 人格 system_prompt 预填充。
  - [ ] 烟测：裁断点 / 跨日 / 退化 / TTL 清理 / 压缩上限。

### 新增未定（待讨论）
- 12:00 是否可配置（时区？）。
- 会话轮数 -> 目标字数的具体比例公式（上限 500）。
- 日记是否参与普通召回 / 是否单独检索通道。→ 已定，见文末 v8 套颁。
- relations 注入的 confidence 阈值默认值。


---

## 补充：v5 讨论确认项（WebUI 编辑 + 关系注入取 top3）

### 状态
- **A 已确认 OK**（LLM 链路接通，记忆走 LLM 摘要）。后续可推进 B 系列。

### 已查证事实（免重查）
- **WebUI page_api 现有端点**（`page_api.py` register_routes）：`/health` `/stats` `/memories`（列表）`/memories/detail` `/memories/delete` `/recall/test`。**有删除+详情，无编辑写端点**。编辑功能 = 补一个 `/memories/update` 写端点 + 前端表单，与现有 delete 同类。
- store 已有 `upsert(e)` / `get(id)` / `delete(id)->bool` / `all(limit)`，编辑可直接 get->改字段->upsert。

### 新增确认需求
11. **WebUI 直接编辑记忆**：在面板里可编辑单条记忆的**各属性及内容**（summary/content/importance/strength/topics/tags/memory_type/tier 等可改字段）。
    - 新增 `/memories/update` 写端点（get->校验字段->upsert）。
    - 前端编辑表单（参考 livingmemory `memory_handler.py` 的 CRUD）。
    - 注意：改 content/summary 后是否重算 embedding（否则向量与文本不一致）——待定，建议改文本时重嵌。
12. **关系注入：管道式过滤（方案 4，不加权）**：inject 拼 relations 时按三道依次过滤：
    - 第1道 **相关性过滤**：只保留关系涉及的人物/话题与当前消息匹配的。
    - 第2道 **置信度阈值**：过滤掉 confidence < 阈值的（见需求 7，每条关系独立 confidence）。
    - 第3道 **top-N 截断**：剩下的取前 N 条（默认 N=3）。
    - **不做加权评分**，三道过滤依次收敛即可。

### 分阶段补充
- **B-2** 加：关系注入 = 相关性×置信度加权 -> top3（需求 12）。
- **B-4（新，WebUI）**：
  - [ ] `/memories/update` 写端点 + 字段校验。
  - [ ] 前端编辑表单（可改 summary/content/importance/strength/topics/tags/memory_type/tier）。
  - [ ] 改文本时重算 embedding。
  - [ ] 烟测：update 端点 round-trip / 重嵌生效。


---

## 补充：v6 默认数值与标识要求（拍板，仅记录不动工）

> 以下数值均为**默认值，可后台修改**。

### 触发阈值默认
- 会话冷却 idle：**私聊 1800s / 群聊 600s**。
- 定时强制总结周期：待定（先沿用原计划，后续可调）。

### 压缩公式默认
- **总结压缩（会话级 B-1）**：目标字数 = 原文字数 × **0.15**；**无下限（默认 0）**；**上限 1200 字**。
- **日记压缩（B-3）**：目标字数 = 总字数 × **(0.025 / 除自身外参与人数)**。
    - 私聊（参与人数=1，即用户本人）：系数 = 0.025。
    - 群聊：按「除 bot 自身外的参与人数」平摊，人越多系数越小。
    - **下限 50 字，上限 2500 字**。
    - （注意与之前「不超 500 字」不同：**以本 v6 为准，上限 2500。**）

### 调试开关（v6.1 修正）
- 「总结模式」开关：**默认开**。默认走会话缓冲->总结->只存总结条。
- 「逐条入库（旧行为）」：**仅调试用**（默认关，切回可回退）。
- （注：与之前 v3 需求 3 「默认关」相反，**以本 v6.1 为准：总结模式默认开**。）

### 人格预填充
- B-1 就要做（需求 8 确认）。

### 会话标识（新增确认，重要）
- **私聊/群聊必须标识清楚，写入记忆时带上这些标识**：
    - **私聊**：标明 bot 跟谁（对方用户标识/昵称）。
    - **群聊**：标明**群号 + 群名**。
- 需新增字段承载：chat_type、对方标识（私聊）、群号+群名（群聊）。需查证 AstrBot event 是否能拿群名（get_group_id 拿群号已知，群名待查）。

### bot 自身消息入库（新增确认，重要）
- **逐条入库时 bot 自身消息也入库**（不再只存用户消息）。
- **日记用的当日缓存会话记录也要包含 bot 自身的消息**（否则日记缺 bot 侧发言，上下文不完整）。
- 影响：现在 observe 只捕获 inbound 用户消息；bot 自身回复需额外 hook（如 on_decorating_result / after_message_sent 类钩子）抓 bot 输出并同样入库/入缓存。**需查证 AstrBot 是否有 bot 回复的事件钩子**。


---

## 补充：v6.1 日记分块召回

### 需求（待实现）
13. **日记检索召回时可分块**：日记可能长达 2500 字，整篇作为一个召回单元会（1）向量表征不精准（2）注入时 token 占用大。
    - **存储：仍为一篇完整日记（单一记忆单元，保留完整性）**。
    - **检索：按段落/时间段切块做嵌入，召回时命中块级**，只注入命中的那一（几）块而非整篇。
    - 可行性：日记生成时已按时间顺序组织（需求 10），天然适合按时间段切块。参考 RAG 的 parent-doc / chunk 检索模式：chunk 索引，parent（整篇）可按需回取。
    - **实现选型（待定）**：A. 独立 chunk 表（chunk 带 parent_diary_id + 各自 embedding + 时间段）；B. 复用主 Engram 表、日记拆多条子 engram 用 cluster_id/source_engram_ids 绑定。倾向 A（语义更清晰、不污染主库）。

### 分阶段补充
- **B-3** 加：日记分块召回（整篇存储 + chunk 级检索/注入，需求 13）。


---

## 补充：v7 总结实现参考 + 视角确认

### 已查证：livingmemory 总结实现（可借鉴）
- **消息缓存按 session_id 整体缓存**（`conversation_manager`，LRU），**不按发言人分桶** —— 正是 B-1 需要的 per-channel 整段结构。
- 触发：livingmemory 按**未总结轮数** `unsummarized_rounds >= summary_trigger_rounds`（默认 10 轮，2 条=1 轮）。
- 总结输入：整段 history（含所有人 + bot）送 LLM。
- 产出：summary / key_facts / topics / participants；双通道 canonical_summary（检索）+ persona_summary（人格风格）。
- `metadata.source_window` 标明本次总结覆盖的消息区间 —— **防重复总结**。

### 需求确认
14. **总结模式参考 livingmemory 实现**：借鉴其 session 级整段缓存（非 per-actor）、LLM 结构化产出（summary/key_facts/participants/双通道）、source_window 防重总结。
    - **但触发逻辑用本项目的 idle + 定时**（非 livingmemory 的轮数阈值）。
    - 双通道可借鉴：canonical 用于检索、persona_summary 保留人格风格。
15. **私聊总结含 bot** ✅（与群聊一致，总结输入含 bot 发言）。
16. **日记 = 以 bot 为主视角的总结**：日记不是中立第三方记录，而是「我（bot）」的第一人称视角。
    - prompt 需以 bot 视角 + 人格：如「今天我和用户A 聊了火锅，帮他订了位；群里大家在讨论…」。
    - 与会话总结（B-1，中立叙述）的**叙述人称不同**，需区分 prompt。

### 分阶段补充
- **B-1** 参考 livingmemory：session 级整段缓存 + 结构化产出 + 双通道 + source_window（需求 14）；触发用 idle+定时。
- **B-3** 日记 prompt 用 **bot 主视角 + 人格预填充**（需求 16）。


---

## 补充：v8 前置查证结论（bot 钩子 + 群名，均源自 AstrBot 框架代码）

### bot 输出钩子 ✅
- `@filter.on_llm_response()`：LLM 生成回复后触发，拿 `LLMResponse`（livingmemory main.py:343 已用）。→ engram 收 bot 消息进缓冲用这个。
- `@filter.after_message_sent()`：消息发出后触发。
- 与现有 `@filter.on_llm_request()`（注入）并存，不冲突。

### 群名 ✅ （异步 + 平台相关）
- `await event.get_group()` -> `Group` 对象，有 `.group_name`（astr_message_event.py:502 / astrbot_message.py:26）。
- 或 `event.message_obj.group.group_name`（框架自用 astr_main_agent.py:872）。
- **get_group() 是 async，且依赖平台适配器**（aiocqhttp 已实现；部分平台可能返回 None，需容错）。`get_group_id()`（群号）是同步、稳。
- 会话类型判定用 `event.get_message_type() == MessageType.GROUP_MESSAGE`（比“group_id 是否为空”更规范）。

## 实现进度
- [进行中] B-1 纯逻辑核心：conversation_buffer.py + summarizer.py + 烟测（不接 AstrBot 事件，可独立测）。

---

## 补充：v8 讨论确认项（日记召回与总结片段重叠问题）

### 根因
- 两层（B-1 会话总结 / B-3 日记）共用同一张 engram 表。
- `service.recall()`（service.py:480）不按 `memory_type` 过滤，一个 query 会同时捞出日记与会话总结。
- 日记是会话总结区间的二次压缩，内容高度重叠：同一事实可能被注入两遍（中立视角 + bot 主视角），在 top_k=3 额度下挤掉其它记忆。

### 设计契约（方案 A 为主 + 吸收 B 的时间检索）
日记与会话总结是**互补的两个抽象层级**（当天概览 vs 单次对话），B-3 实现时须按以下四点落地：
1. **分层召回**：召回端按 `memory_type` 分桶，日记与会话总结各走独立召回（给 `Cue`/`completer.recall` 加 `memory_type` 过滤参数），避免长文本日记稀释短总结的 FTS 打分。
2. **独立配额**：注入端两层各给独立 top-N（默认总结 top-3 + 日记 top-1，可配），互不挤占。
3. **来源标签**：注入块各自标明来源与粒度，例如 `[近期对话]`（会话总结）vs `[今日回顾]`（日记），避免 LLM 混淆中立/主观视角与时间尺度。
4. **时间/分块检索**：日记另支持按时间/分块检索（需求 13，整篇存储 + chunk 级命中只注入命中块），用于“那天发生什么”这类查询。

### 默认值（可改，实现时落配置）
- `diary_inject_top_n: int = 1`
- `summary_inject_top_n`：沿用现有 `auto_inject_top_k`（会话总结）。
- 来源标签文案：总结 `[近期对话]` / 日记 `[今日回顾]`。

### 被排除的方案
- 方案 B（日记不进默认召回，仅时间检索）：零重叠但丢失日记“长期人物印象”价值；改用 A+时间检索兼顾。
- 方案 C（召回后相似度去重合并）：逻辑复杂、阈值难调、最易出 bug，不采用。

---

## 补充：B 方案完成状态（v1.21）
- B-1 会话总结：✅ v1.17/v1.18
- B-2 关系层：✅ v1.19
- B-3 日记层：✅ v1.20（分层召回+独立配额+来源标签+chunk 检索）
- B-4 WebUI 编辑：✅ v1.21（/memories/update 端点 + 前端表单，改原文重算向量）
  - 顺手修复 storage.upsert 的 ON CONFLICT 漏更新 content 字段（预存 bug，编辑旧 engram 正文不生效）。
- 待办（非 B 主线）：/mem diary 手动触发日记（方便验证，未做）。
