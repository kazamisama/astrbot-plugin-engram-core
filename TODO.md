# TODO / 待办候选

> 候选改进项，均为「已讨论、未实现」。动手前需确认范围与最小 diff。

## 1. 召回结果自动注入（可选）

**现状**：召回结果只通过 `recall_long_term_memory` function tool 暴露给 LLM，由 LLM 自行决定是否调用（`hippocampus/tools.py`、`handlers/init.py:_register_agent_tools`）。不自动注入，因此 LLM 不主动调时记忆用不上。

**目标**：参考 livingmemory，加一个可配置的「自动注入」路径，把召回结果（摘要 + 置信度）直接拼进 system prompt / 对话上下文，与现有 function-tool 路径并存。

**要点**：
- 新增配置开关（默认关，保持现有行为），放进 `_conf_schema.json` 的 `memory_settings` 分组 + `MemoryConfig` + `ConfigManager._FIELDS`。
- 注入方式可选（system prompt / 上下文），注入条数上限、是否随近期上下文一起注入。
- 监听 AstrBot 的 LLM 请求钩子（需确认 AstrBot 是否暴露 on_llm_request / 等价 hook）做注入。
- 注意 token 预算，避免上下文爆炸。

## 2. 硬回收（GC）条件改用「有效强度 / 上次访问时长」

**现状**：`HippocampalStore.gc_pass()`（`hippocampus/storage.py:290`）硬删条件为
`strength < floor` 且 `access_count == 0` 且 `够老`。
`access_count` 单调递增、永不衰减（`hippocampus/recall.py:13` touch 时 +1，仅对 top-k 生效）。
后果：只要历史上进过一次召回 top-k，`access_count` 永久 ≥1，永远无法被硬回收——只有「自创建起从未进过任何 top-k」的纯冷记忆才会被删。偏保守。

**目标**：让「曾被召回过但早已冷却」的旧记忆也能被回收，更贴近艾宾浩斯「长期不用就忘」。

**候选改法**：GC 条件从 `access_count == 0` 改为基于
- 衰减后的有效强度（已随 decay 降到 floor 以下），和/或
- 距 `last_accessed` 的时长超过阈值（如 N 天没再被召回）。
保留软忘记审计（`forgotten_at`），仍只硬删「弱 + 长期未访问 + 够老」。

**要点**：
- 改 `gc_pass()` 判据；新增阈值配置（默认值需保守，避免误删）。
- 同步 atom 层 `atom_lifecycle_manager.run_gc()` 是否要一致改。
- GC 自动循环目前默认关闭（`atom_gc_interval_seconds=0.0`），改判据不改变「默认不自动跑」的前提。
- 需补烟测：构造一条「曾召回、已冷却」的 engram，断言新判据下可被回收、未冷却的不被回收。

## 3. 会话聚合：同一人/同一会话连续消息统一存储

**现状**：`MemoryService.observe()`（`hippocampus/service.py:159`）来一条消息立即 encode 落库。存在 merge/link/new 三动作，但触发判据是**向量相似度**（`pattern_similar_threshold`），**不看 actor_id**——不是按「同一人连续消息」聚合。副作用：群聊里不同人说相似内容也会被 merge 到同一条。

**目标**：参考 livingmemory 的 `conversation_manager` + `memory_reflection`，加会话缓冲层，按 `(channel_id, actor_id)`（或整会话）攒够 N 条/N 轮/停顿超时后，再总结成一条记忆落库，而非逐条入库。

**要点**：
- 引入会话状态管理（缓冲、TTL、容量上限、轮次计数）。
- 触发条件：攒够 N 轮 / 停顿超 N 秒 / 话题切换。
- 落库时用 LLM 总结成一条（engram 已有 encoder LLM 通路）。
- merge 是否应改为「先按发言人聚合，再按相似度去重」，避免跨发言人误并。
- 结构性改动较大，等价于移植 livingmemory 的 session_manager + reflection_engine。

## 4. 其他可借鉴自 livingmemory 的点（候选，未评估优先级）

- **检索器拆分 + BM25**：livingmemory 把检索拆成 vector / bm25 / graph_keyword / graph_vector / dual_route 多个独立 retriever（`core/retrieval/`）。engram 目前 FTS 用 unicode61 字符级分词，可考虑引入 BM25 + 停用词（`stopwords_manager`）提升中文检索质量。
- **注入策略适配器**（`core/utils/injection_adapter.py`）：按 provider/模型自动选择注入方式并降级（如 gemini 不支持 fake_tool_call 时回退）。配合 TODO#1 自动注入一起做。
- **实体消解 entity_resolver**（`core/processors/entity_resolver.py`）：把指代/别名归一到同一实体，减少实体图碎片。engram 当前实体抽取较朴素。
- **chatroom_parser / 群聊解析**（`core/processors/chatroom_parser.py`）：群聊多人消息的结构化解析（区分发言人、@、引用）。
- **decay_scheduler 独立调度器**（`core/schedulers/decay_scheduler.py`）：livingmemory 把衰减做成独立调度器并默认运行；engram 的 decay/GC 默认关闭（见 TODO#2）。
- **index_validator**（`core/validators/index_validator.py`）：启动时校验向量索引与库一致性，自动修复/重建。engram 切换 embedding 维度后无一致性校验。


## 5. 架构对比与借鉴优先级（engram vs livingmemory）

> 评估自用户提供的 livingmemory 架构图 + 已读源码。结论：livingmemory 是工业级 RAG 范式，engram 同范式但更轻量。差距集中在「会话处理 / 检索多样性 / 自动注入 / 默认调度」四块。

**范式对照**
- 捕获：两者都是事件捕获。engram 逐条立即 encode（`hippocampus/service.py:159`）；livingmemory 有 `conversation_manager` 会话缓冲 + `memory_reflection` 反思总结后落库。→ 见 TODO#3。
- 处理：livingmemory 有 graph_extractor / entity_resolver / atom_classifier / chatroom_parser 流水线；engram 实体抽取较朴素，无群聊结构化解析。→ 见 TODO#4。
- 存储：两者都是多维标签 + 向量 + FTS。engram 70 字段正交标签已较完整。
- 检索：livingmemory 5 路（vector/bm25/graph_keyword/graph_vector/dual_route）+ RRF；engram 2 路（vector + 字符级 FTS）+ RRF + 多因子重排。→ 见 TODO#4 BM25。
- 注入：livingmemory 有 injection_adapter 自动注入 + 降级；engram 仅 function-tool 被动暴露。→ 见 TODO#1。
- 调度：livingmemory decay/lifecycle/backup 默认运行；engram decay/GC 默认关闭。→ 见 TODO#2。

**推荐落地优先级**（投入产出比，从高到低）
1. **会话聚合（TODO#3）** — 直接解决「逐条入库 + 跨发言人误并」，对群聊质量影响最大，是当前最痛点。
2. **BM25 检索（TODO#4）** — 中文检索质量提升明显，改动相对自包含，风险可控。
3. **召回自动注入（TODO#1）** — 让记忆真正参与对话而非等 LLM 主动调；依赖确认 AstrBot LLM 请求钩子。
4. **GC 判据（TODO#2）** — 价值在长期库健康；当前 decay/GC 默认关，非紧急。
5. **其余（entity_resolver / chatroom_parser / decay_scheduler / index_validator）** — 按需补，非核心路径。

**风险提示**
- 1 与 3 都是结构性改动，动手前先各自给方案 + 确认 AstrBot 钩子能力，再写代码。
- 任何一项都需：改完同步运行副本 + bump 版本（`metadata.yaml` + `hippocampus/__init__.py`）+ 跑烟测 + 推送。


## 6. 借鉴自 atom.txt 架构解读的增量点

> 来源：用户提供的 `atom.txt`（某 Agent 记忆系统架构文字解读，DDD/Clean 风格）。与 livingmemory 同范式，去重后对 engram 真正有增量的为以下几项。BM25 / Graph检索 / index_validator / Prompts分层已在前文 TODO，不重复。

### 6.1 质量校验 + Warm 后台处理（并入 TODO#3 会话聚合）

**对照**：atom.txt 落库前有 `quality_validator`（过滤低质量/噪声）+ `warm_processor`（后台异步加工）。engram `observe()`（`hippocampus/service.py:159`）逐条同步入库，仅过滤空内容与合成事件，无质量门、无后台加工。

**目标**：在 TODO#3 会话缓冲层之上加「质量门」——聚合后、落库前判断是否值得记（长度/信息量/是否纯寒暄/是否重复），低质量丢弃；耗时的 LLM 总结放后台异步，避免阻塞消息处理。

**价值**：与当前最痛点（逐条入库噪声多）同源，建议与 TODO#3 一起做。

### 6.2 Persona 用户画像引擎（全新能力，engram 当前没有）

**对照**：atom.txt 有 `persona_engine` + `persona_store`，按用户长期沉淀偏好/行为画像。engram 有 `actor_id` 维度但只用于检索过滤，未做画像沉淀。

**目标**：新增按 `actor_id` 的画像表，定期（或攒够 N 条记忆后）用 LLM 总结该用户的稳定偏好/身份/行为，召回时作为稳定背景注入（配合 TODO#1 自动注入）。

**要点**：
- 新表 `personas(actor_id, platform, summary, updated_at, source_count)`。
- 触发：定时 / 攒够 N 条新记忆 / 手动命令。
- 与 episodic 记忆解耦：画像是「稳定背景」，不参与衰减 GC。
- 这是 atom.txt 相对 engram 的最大增量能力。

### 6.3 WAL + 崩溃恢复（低成本健壮性，建议优先）

**对照**：atom.txt 存储层用 `SQLite + FTS5 + WAL` + `write_op_log`（崩溃恢复日志）。engram 已用 SQLite+FTS5，需确认是否已开 WAL。

**目标**：
- 先上 `PRAGMA journal_mode=WAL`（近零成本，提升并发读写 + 崩溃恢复）——动手前先 grep 确认 `hippocampus/storage.py` 是否已设置，避免重复。
- `write_op_log`（写操作日志做崩溃恢复）成本高、收益边际，**暂不做**，仅记录。

**价值**：WAL 改动极小、风险极低，可作为最先落地项。

### 6.4 聊天内管理命令 /memory /forget /summary（易用性）

**对照**：atom.txt 有 `command_handler` 暴露 `/memory` `/forget` `/summary`。engram 目前只能用 WebUI 管理记忆，无聊天内命令。

**目标**：用 AstrBot 指令机制加少量命令：查询最近记忆 / 主动遗忘指定记忆 / 触发一次会话总结。改动自包含，不触碰检索与存储核心逻辑。

**要点**：需确认 AstrBot 指令注册 API（`@filter.command` 等），命令仅做读/标记，不直接硬删（走现有 soft_forget）。

### 落地优先级（综合 TODO#1–6）
1. **WAL（6.3）** — 最便宜，先做。
2. **会话聚合 + 质量门（TODO#3 + 6.1）** — 最痛点。
3. **Persona 画像（6.2）** — 最大增量能力。
4. **BM25 检索（TODO#4）** — 中文检索质量。
5. **自动注入（TODO#1）** — 让记忆真正进对话。
6. **管理命令（6.4）／ GC 判据（TODO#2）／ 其余** — 按需补。
