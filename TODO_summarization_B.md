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
- 日记是否参与普通召回 / 是否单独检索通道。
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
