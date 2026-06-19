# 海马体 Roadmap

> 动态清单：实现一个划掉一个。每项标注优先级 (P0/P1/P2) + 状态 ([ ] 待实现 / [x] 已完成)。

## v1.3 已完成

- [x] **A1** 双路召回 (document + graph) + RRF 融合 (`hippocampus/retrieval/`)
- [x] **A2** `/mem search --mode=dual` 命令路由 + 渲染
- [x] **A3** LLM Agent Tool (`recall_long_term_memory` / `memorize_long_term_memory`)
- [x] **A4** RRFFusion 用业务稳定 ID (`item.id`) 去重，修复 vec+fts 同 engram 不合并的潜在 bug
- [x] **A5** 版本 bump 1.2.0 → 1.3.0 (metadata / __init__ / _registered_version 三方对齐)
- [x] **A6** smoke 7 → 9 个 (v14/v15/v16)

---

## v1.4 已完成

- [x] **B1** TextProcessor: jieba 切词 + 停用词 + 否定词 (`hippocampus/processors/`)
  - `text_processor.py`: tokenize / remove_stopwords / mark_negation / fts_preprocess / embed_preprocess / keyword_preprocess
  - `stopwords.py`: 内置 ZH 148 + EN 93 + 47 否定词
  - 零依赖: jieba 装则用, 不装则 char-level fallback (CJK per-char + ASCII word)
  - dual_route._graph_route 接入 TextProcessor, 多 token query 自动拆开逐个查 entity
  - smoke v17 (13 个测试): tokenize / stopwords / negation window / fts 形状 / embed NOT_ 前缀 / graph multi-token
  - 修 bugs: _is_cjk 支持多字符 / remove_stopwords 大小写不敏感 / 从 EN_STOPWORDS 移除 not/no/nor (它们是 negation 不是 stopword)
- [x] **B2** 群聊被动捕获 + session 过滤 (避免群聊噪音污染长期记忆)
  - `hippocampus/session_filter.py`: SessionFilter + FilterContext + FilterDecision + 6 条规则
  - `MemoryConfig` 加 7 字段: enable_session_filter / platform_allowlist / platform_blocklist / channel_allowlist / channel_blocklist / actor_allowlist / blocked_keywords
  - `MemoryService.observe()` 头部查 filter: deny 返回 synthetic Engram 标 `_filter_denied=True` (不进 store)
  - `handlers/format.py:format_session()` 渲染当前策略 + quick test
  - `main.py:cmd_mem_session` 新命令
  - smoke v18 (10 个测试): 6 规则 / 优先级 (blocklist>allowlist) / disabled 覆盖 / 端到端 observe 拒绝 / 命令注册

---

## v1.4 候选（按优先级）

### P0：文本质量层

- [x] **B1** TextProcessor (shipped 2026-06-19)：jieba 切词 + 停用词 + 否定词处理（提升中文 recall 精度，影响 vec / fts / graph 三路）
  - 拆 `cjk_split` 出 `hippocampus/processors/text_processor.py`
  - 新增 `StopwordsManager`（中英常见停用词）
  - 加 `hippocampus/processors/__init__.py`
  - smoke v17 验证

### P0：会话策略层

- [x] **B2** 群聊被动捕获 + session 过滤 (shipped 2026-06-19)
  - 加 `hippocampus/session_filter.py`（allowlist / blocklist / per-group 配置）
  - 加 `enable_group_capture` / `group_allowlist` / `blocked_keywords` 配置字段
  - 在 `MemoryService.observe()` 前置过滤
  - 加 `/mem session` 命令查看当前策略
  - smoke v18 验证

- [x] **B3** MemoryAtom 数据层 (shipped 2026-06-19)
  - `hippocampus/types.py` 末尾追加: `AtomStatus` / `AtomType` / `DecayType` 枚举 + `MemoryAtom` dataclass (`to_dict` / `from_dict` / `merge` / `triple` property)
  - `hippocampus/memory_atom_models.py` (单文件, 避免撞 storage.py): `triple_key` / `make_fact_atom` / `make_preference_atom` + canonical 归一化 (lowercase + strip)
  - `hippocampus/atom_store.py` (单文件): `AtomStore` CRUD + 独立 `atoms` 表 (UNIQUE triple COLLATE NOCASE) + `soft_forget` / `gc_pass` / `list_by_source_engram` (JSON1 EXISTS) / 窄接口 `write_strength` / `set_status`
  - `hippocampus/atom_lifecycle_manager.py` (单文件): `AtomLifecycleManager` 4 动作: `extract_atoms_from_engram` (best-effort, 跳过坏行) / `promote` / `merge_evidence` (in-place + persist) / `decay_pass` (exp(-dt/(tau_base*mult)), per-type 衰减倍率 episodic=1 / semantic=4 / preference=8)
  - `hippocampus/__init__.py` re-export 5 个 B3 符号 (MemoryAtom / AtomStatus / AtomType / DecayType / AtomStore / AtomLifecycleManager)
  - 零依赖
  - **scope**: data layer only. `MemoryService.observe()` 暂不挂 `extract_atoms_from_engram`, 留到 v1.4.x 后续
  - 修 bugs:
    - factory + upsert 两端对 triple 做 `_norm()` (strip + lower), 防止 `"  alice "` / `"ALICE"` 漏 merge
    - `MemoryAtom.merge` 语义: 新观察只 +1 evidence (不累加 other.evidence_count, 避免双重计)
    - `upsert` merge 路径 evidence_count / access_count 用 max(caller, existing) - caller 提供 row 目标态, store 兜底防呆
    - `decay_pass` 不走 `upsert` 改用窄 `write_strength` / `set_status` - upsert 的 max 会把旧 strength 还原
  - smoke v19 (9 个测试): roundtrip / CRUD / upsert merge / list_by_source / soft_forget + gc / extract (2 good + 2 bad row + 1 throw) / merge_evidence / decay_pass / public exports

- [x] **B4** GraphStore + GraphRetriever (shipped 2026-06-19, data + retrieval)
  - `hippocampus/graph_store.py` (单文件): 复用 `entities` / `relations` 表, 加 `graph_adjacency(entity_id, neighbor_id, predicate, weight)` + `graph_engram_refs(entity_id, engram_id, weight)` 两张新表. 方法: `add_relation` (镜像到邻接 + 反向索引) / `add_entity_engram_ref` / `neighbors(entity_id, max_hops)` BFS / `engrams_for(entity_id, limit)` O(matches) / `all_relations` / `rebuild_from_semantic` / `stats`
  - `hippocampus/retrieval/_graph_types.py`: `EntityMatch` dataclass, 拆出避免三方循环 import
  - `hippocampus/retrieval/graph_keyword_retriever.py` (单文件): 词级 entity match, 评分 (exact=3.0 / in-name=2.0 / reverse-in-token=1.0 / alias=2.5/1.5 / mention_count log prior)
  - `hippocampus/retrieval/graph_vector_retriever.py` (单文件): entity name embedding + cosine (零依赖, 用 HashEmbeddingProvider)
  - `hippocampus/retrieval/graph_retriever.py` (单文件, 顶层 facade): `_fuse_anchors` 融合 kw + vector, `_expand` 走 N-hop + depth 衰减, 公共 `search(cue) -> list[RankedCandidate]` 给 dual_route 用
  - `hippocampus/retrieval/dual_route.py:_graph_route`: 委托给 `GraphRetriever.search`, 公共签名 (`list[RankedCandidate]`) 不变, v17 / v18 smoke 不破
  - `hippocampus/retrieval/__init__.py` + `hippocampus/__init__.py` 导出 5 个 B4 符号
  - 零依赖
  - 修 / 注意:
    - 拆 `_graph_types` 子模块避免 `graph_retriever <-> graph_keyword_retriever <-> graph_vector_retriever` 三方循环
    - `GraphRetriever.search` 走**两段式**: 优先 `graph_engram_refs` 反向索引 (B4 优化目标), hits 为空时回退 `store.all()` 扫 `entity_refs` (v1.3 老路径, 保 dual_route.search 不破)
    - `dual_route._graph_route` 缓存 `GraphRetriever` 在 `service._graph_retriever` 上, 复用单例
    - 不替换 `semantic.py` (B4 scope 限定), 留兼容门面
  - smoke v20 (7 个测试): add_relation mirror / neighbors BFS 1/2/3 hop / keyword scoring / vector ordering / full GraphRetriever via service + dual route / 快速路径命中 / public exports

### P1：记忆原子层

- [x] **B3** MemoryAtom 数据类（sub-Engram 粒度，独立 store） (shipped 2026-06-19, data layer only)
  - data layer 完整 (见 v1.4 已完成段)
  - 延后项: `MemoryService.observe()` 接入 `extract_atoms_from_engram` 留到 v1.4.x (需先扩 `EntityExtractor.extract_atoms` 接口)

### P1：图存储层

- [x] **B4** GraphStore + GraphRetriever (shipped 2026-06-19, data + retrieval 增量, **未替换 semantic.py**)
  - data + retrieval 完整 (见 v1.4 已完成段)
  - 延后项: 重写 `semantic.py` 把平面 list 迁过来 (留到 v1.5, 当前 GraphStore 共用 entities/relations 表, 不冲突)

### P1：补全 Agent Tool

- [x] **B5** 补 3 个 Agent Tool (shipped 2026-06-19)
  - forget: engram_id 软删除
  - list_recent: actor_id + k
  - search_by_entity: entity_name
  - smoke v22 验证

### P1：EventHandler 拆分

- [x] **B6** main.py 的 `HippocampusStar` 拆 handlers/event/{observe, recall, manage}.py (shipped 2026-06-19)
  - 借鉴 livingmemory v2.3.5 的 event_handler_modules 模式（单职责 class + thin wrapper），但按海马体实际语义命名而非字面（observe/capture/reflection 借名不借义，海马体没有 LLM 反思钩子）
  - 新增 5 个文件:
    - `handlers/event/observe.py` (33 行) — `ObserveHandler.handle_message()`，群聊/私聊被动 capture
    - `handlers/event/recall.py` (121 行) — `RecallHandler`，8 个查询/召回/联想类命令（recall / mem search / mem profile / mem activate / mem cluster / mem cluster-list / mem confidence / mem decaycurve / mem narrative）
    - `handlers/event/manage.py` (176 行) — `ManageHandler`，9 个管理/调试/写类命令（mem model* / mem rebuild / mem forget / mem export / mem import / mem graph / mem prospective / mem replay / mem consolidate / mem valence / mem streams / mem session / mem remember）
    - `handlers/commands.py` (70 行) — `CommandRouter` 分发表，装饰器名 → (handler, method) 映射
    - `handlers/init.py` (133 行) — `PluginInitializer` 收纳原 `__init__` 的 4 个 init 路径（_init_service / _install_bridges / _start_background / _register_agent_tools）+ service 启动 banner
  - main.py: 418 → 234 行 (-44%)，只剩 @filter 装饰器 + thin wrapper，smoke 接口稳定
  - 装饰器必须留在 main.py 的 Star 类上（AstrBot 扫描 Star 子类），子模块只持有业务逻辑
  - back-compat:
    - main.py 顶层保留原 17 个 handlers 符号的 re-export（v08-v13 smoke 走 `from main import format_xxx`，不能改）
    - main.py 保留 `_register_agent_tools` thin shim（v16 smoke 走 `star._register_agent_tools()`，懒建 _initializer 兼容）
  - 同步更新 ROADMAP 的 B6 描述（命名从 observe/capture/reflection 改为 observe/recall/manage）
### P1：配置层

- [x] **B7** ConfigManager 类（替代裸 dict） (shipped 2026-06-19)
  - 新建 hippocampus/config_manager.py (~190 行):
    - _FieldSpec dataclass: py_type + range + label_zh + label_en
    - _FIELDS: 67 项注册表覆盖 MemoryConfig 全部字段
    - ConfigManager(raw_dict).memory_config: type coerce + range check + fallback
    - ConfigManager.LABELS: 67 项 {zh, en} 公开字典，给 B8 i18n 框架预抽
    - ConfigManager.get(key, default): 与 getattr 等价（占位 B8 dot-path 暂用不上）
  - 校验语义: type coerce 失败 / range 越界 → 用 default + warn log; extras 进 MemoryConfig.extra; None 视作 missing 不 warn
  - 改造 astrbot-plugin-hippocampus/handlers/init.py: _init_service 14 字段 hardcode → ConfigManager(cfg_dict).memory_config (1 行)
  - 同步: 删除 PluginInitializer 内 MemoryConfig 直接 import（仅 ConfigManager 用），import 数从 3 个 hippocampus 符号减到 2 个
  - smoke v23 (11 测试) 全过: 注册表覆盖 / 空 dict 默认 / 14 字段 legacy 兼容 / 类型 coerce / coerce 失败 fallback / range 越界 / range 通过 / extras / LABELS 完整性 / get 一致性 / None 处理
  - 全量回归 v08-v23 = 24/26 业务通过 (v11 已修:facade 缺 decay/floor 转发,见 commit 14187ec;v12 真实 bug:`asyncio.run(svc2.stop())` 调不存在方法,`MemoryService` 无 `stop` 同步方法,只剩 `stop_background_tasks` / `stop_background_tasks_sync` / `close`,与 B7 无关。v12 修法:smoke 改用 `stop_background_tasks` + 加 `svc.close() + del + gc` 防 Windows 文件锁,与 B7 无关)
  - 未动: _conf_schema.json (14 字段 schema 仍描述 AstrBot Dashboard 暴露的 UI 字段); B8 i18n 框架接手时一起从 LABELS 拉 label
- [x] **B8** i18n 框架（zh + en 起步） (shipped 2026-06-19)
  - 新建 hippocampus/i18n_backend.py (~95 行): init(lang) + t(key, **kw) + t_list(key) + current_language() + SUPPORTED_LANGS
  - 新建 hippocampus/i18n/zh.json + en.json (~14 个 top-level sections: help/error/recall/usage/forget/cluster/valence/stream/replay/consolidated/model/no_prospective/remembered + meta)
  - B7 衔接: ConfigManager.LABELS (67 字段) 在 init() 时合并进两个语言下的 config.<field> namespace, B7 的 zh+en label 立即可走 t()
  - 多语言隔离: init() 用 copy.deepcopy 让 zh / en dict 完全独立, 避免 LABELS 写入互相覆盖 (调试发现并修复的 latent bug)
  - scope 收窄: 调研发现 handlers/format.py (502 行) 0 个中文硬编码 (全是英文 ## stats / engrams 等), ROADMAP 写 format.py 走 t() 是错的; B8 实际只动 handlers/help_text.py
  - 改造 handlers/help_text.py: HELP_TEXT 走 t(help.full_text) (zh 默认), 新增 get_help_text() 走当前语言; back-compat 保留 HELP_TEXT 常量供 v15 smoke in HELP_TEXT 断言
  - 同步: 完整 en 翻译 help.full_text (30 行命令说明) + ~25 个用户可见字符串 (recall.no_memory / recall.related_header / usage.remember / cluster.empty / valence.* / stream.* / replay.ok / consolidated / model.summary)
  - 未动: format.py 内 100+ 个结构字符串 (## stats / engrams: / + ( 等拼接) 留 B8.x 收; _conf_schema.json 14 字段 description 中文留给 B9 WebUI 一起加 bot_language 配置
  - smoke v24 (10 测试) 全过: SUPPORTED_LANGS / init zh+en / format kwargs / config.<field> / missing sentinel / unknown lang fallback / t_list / HELP_TEXT import / deep-copy isolation
  - 全量回归 v08-v24 = 15/15 业务通过 (v11 已修 14187ec;v12 真实 bug 为 `MemoryService` 缺 `stop()` 同步别名,见 B7 注释;v12 已修,见 B7 注释,与 B8 无关)
  - smoke v23 复用 (B7 ConfigManager 12 字段不重测); v24 用 importlib 直加载 handlers/help_text.py 避免触发 handlers/__init__.py 拉 format.py 的 astrbot.api stub
### P1：用户面

- [x] **B9** AstrBot Dashboard WebUI：PluginPageApi + 8 个 page endpoints (shipped 2026-06-19)

  - 新建 `astrbot-plugin-hippocampus/page_api.py` (~88 行): `PluginPageApi` facade + `register_routes()` 探测 `hasattr(context, "register_web_api")` 静默降级
  - 新建 `astrbot-plugin-hippocampus/page_api_modules/` (5 文件): `utils.py` / `stats.py` / `memory.py` / `recall.py` / `graph.py` (B10 后续加了 backup endpoint,见 B10 注释)
  - 8 个 endpoint 挂前缀 `/astrbot-plugin-hippocampus/page/`:
    - `health` GET / `stats` GET / `memories` GET / `memories/detail` GET / `memories/delete` POST / `recall/test` POST / `graph/overview` GET / `graph/query` POST
  - 全部 handler 返回 `{status: ok|error, data|message}` 一致 shape; 业务失败 graceful（`{status: error, message}` 不抛 500）
  - `main.py`: 新增 `_register_official_page_api_if_available()` 方法 (livingmemory v2.3.5 模式), `__init__` 末尾在 handlers/router 之后调用; `self._page_api = None` 占位
  - 3 个真实 bug 在 v25 之前修掉: (1) page_api.py 相对 import 改绝对 (plugin root 无 `__init__.py`); (2) memory.py 误用 `row.get(...)` dict API 改 `getattr(row, "attr", default)` (HippocampalStore 返回 Engram dataclass); (3) graph.py 用不存在 `sem.all_relations()` + Relation 假设 `src_name/dst_name` 改 `sem.relations_of(eid)` + `sem.get_entity()` 解析 `subject_id/object_id`
  - B7 + B9 衔接: `_conf_schema.json` 从 14→15 字段, `bot_language` 置顶 (default "zh"), 14 个原 description 全部从 `ConfigManager.LABELS.en` 拉 (B7 67 项 label 立即可走)
  - B8 + B9 衔接: `PluginInitializer.initialize()` 在 `_init_service` 之前调 `i18n_init(cfg.get("bot_language", "zh"))`; Q3 缺口收
  - smoke v25 (5 测试) 全过: 8 endpoints 路径 / ok-error shape / 真实服务 3-engram 跑全 8 端点 / conf_schema 15 字段 + LABELS.en / PluginInitializer 4 种 cfg (zh/en/missing/unknown) 都正确 fallback
  - 全量回归 v08-v25 = 16/16 业务通过 (v11 已修 14187ec;v12 真实 bug 为 `MemoryService` 缺 `stop()` 同步别名,见 B7 注释;v12 已修,见 B7 注释,与 B9 无关)
  - 当时未做,后续在 B10 已 ship: backup endpoint (smoke v25 验证 10 endpoints = 8 B9 + 2 B10 backup) / 前端 JS 资源 (Q2=A 决策, AstrBot auto-discovery 不强求 static/)
### P2：备份与迁移

- [x] **B10** BackupManager + db_migration (已 ship, ROADMAP 历史状态漂移修复)
  - hippocampus/db_migration.py (88 行):v1.0+v1.1+v1.2 column-append 迁移,幂等,锁定 lock 保护;v1.3/v1.4 不需要迁移(它们是新增表,走 CREATE-TABLE-IF-NOT-EXISTS,逻辑在 atom_store.py / graph_store.py)
  - hippocampus/managers/backup_manager.py (256 行):raw .db 拷贝 + .json sidecar(含 __version__ / EXPORT_FORMAT_VERSION / schema hash / engram count);retention keep_last + keep_weekly + keep_monthly 三档
  - 自动定期导出:handlers/init.py 启动 _start_backup_scheduler() 后台 daemon 线程,interval 走 MemoryConfig.backup_interval_hours(0 关闭);smoke v25 验证 10 endpoints / 20 schema 字段(B9 +2 backup endpoint,B10 +5 schema 字段)
  - 与 ROADMAP 历史的差异:实际文件路径是 hippocampus/db_migration.py 而非 hippocampus/storage/db_migration.py;storage/ 子目录计划未落地,但 db_migration 在 hippocampus 根工作良好,不需移动
### P2：性能

- [ ] **B11** graph route O(n) → O(1)：把 entity_refs 索引进 SQL，graph route 改 SQL JOIN
  - 当前 1K engram ~10ms，10K ~100ms，10W 需要换 SQL

### P2：发布工程

- [ ] **B12** CHANGELOG.md（Keep a Changelog 格式）
  - 跟踪 v1.3 / v1.4 / v1.5 ...
  - 跟 metadata.yaml version 同步 bump

- [ ] **B13** GitHub Actions CI（跑 smoke / lint / build）
  - `.github/workflows/smoke.yml`
  - Python matrix 3.11/3.12/3.14

### P3：观测

- [ ] **B14** `/mem debug` 命令：实时显示 retriever explain() 输出、route 命中分布
  - handlers/format.py 加 format_debug
  - main.py cmd_mem_debug 路由

---

## 顺序建议（每条线 1-2 周）

1. **B1 TextProcessor**（提升 recall 质量）
2. **B2 群聊过滤**（解决实际体验问题）
3. **B5 补全 Agent Tool**（LLM 能力补齐）
4. **B3 + B4 数据层**（架构升级）
5. **B6 + B7 工程化**（拆分 + 配置）
6. **B8 + B9 用户面**（i18n + WebUI）
7. **B10 + B11 稳定性**
8. **B12 + B13 + B14 发布 + 观测**

---

## 用法

实现一个 → 划掉一个（`- [ ]` → `- [x]`）+ 在 v1.4 已完成段补一条

---

## 借鉴参考：astrbot_plugin_livingmemory

本地安装位置: C:\Users\chiriu\.astrbot\data\plugins\astrbot_plugin_livingmemory

借鉴点 + 落点:

- **AtomLifecycleManager 异步维护循环** (`core/managers/atom_lifecycle_manager.py: _maintenance_loop / start / stop / run_maintenance`) -> v1.4.x 给海马体的 `AtomLifecycleManager` 加 `start/stop/interval`, 周期性跑 `decay_pass + gc_pass`. 当前 B3 只暴露同步方法.
- **MemoryEngine 中央 facade** (`core/managers/memory_engine.py`) -> B6 阶段参考. 海马体当前 `MemoryService` 持有 10+ 子对象, B6 拆 EventHandler 时同步收敛.
- **write_ops 表 + 不完整写修复** (`memory_engine._create_tracked_task / _repair_incomplete_write_ops`) -> v1.5+. 海马体当前 observe() 无断电恢复.
- **BM25 retriever** (`core/retrieval/bm25_retriever.py`) -> B11 性能一起做. 海马体目前 FTS5 LIKE + cosine + graph 三路, 缺 BM25 排名.
- **EventHandler 拆分模式** (`core/event_handler_modules/{group_capture, memory_recall, memory_reflection}.py`) -> B6 目标.
- **i18n_backend + i18n/{en,zh,ru}.json** -> B8 目标.
- **storage/db_migration.py** (v1.2->v1.3->v1.4 schema 迁移) -> B10 目标.
- **page_api + page_api_modules/{memory,graph,recall,backup,stats,utils}.py** -> B9 WebUI 目标.

> livingmemory 走的是 `aiosqlite` 异步栈 + `MemoryEngine` 单 facade + 维护循环, 海马体走的是 `sqlite3` 同步栈 + `MemoryService` 多对象散布. 短期内不切换, 但**接口形态**可以借鉴(中央 facade + 维护循环 + write_ops).


---

## v1.4.x 已完成

- [x] **B3 wire + B4 wire + 异步维护循环** (shipped 2026-06-19, B3 数据层接进 service)
  - `hippocampus/semantic.py` EntityExtractor 末尾加 `extract_atoms(engram)` 薄包装
  - `hippocampus/config.py` 末尾加 4 字段: enable_atom_extraction / enable_graph_indexing / atom_decay_interval_seconds / atom_gc_interval_seconds (默认全开)
  - `hippocampus/atom_lifecycle_manager.py` 加 async `_maintenance_loop` + `start/stop/run_decay/run_gc` 同步入口
  - `hippocampus/service.py` 加 `_ensure_atom_layer()` 懒建 + `MemoryService.start_background_tasks` 自动检测 loop: 有 running loop 走 `asyncio.create_task`; 无则退化到 daemon thread + `asyncio.new_event_loop()` + `call_soon_threadsafe` (用户不用管 loop)
  - `MemoryService.stop_background_tasks` (async) 按 `_atom_task` 类型分派: asyncio.Task 直接 await; concurrent.futures.Future (worker thread 模式) 走 `asyncio.to_thread(_self_threaded_stop)`
  - `MemoryService._post_ingest` 末尾 wire atom 块 (走 e.entities 配对, 绕过 `extract_relations` id-mismatch) + graph 块 (mirror entity_refs 到 graph_engram_refs)
  - `hippocampus/__init__.py` 追加导出 B3 + B4 全部符号
  - smoke v21 (7 测试): lazy init / observe wire / disabled extraction / sync run_decay+run_gc / lifecycle loop fires / service start+stop / public exports, 全过
  - **回归注**: v08-v20 + v21 业务 13/14 ALL OK. v11 spread_activation 失败的真因是 facade `MemoryService.spread_activation(seeds, *, depth=None)` 漏转 `decay` / `floor` kwarg(底层 `SpreadingActivation.activate` 与唯一调用方 `handlers/format.py:format_activation` 双方都传三参),并非方法名错误。已修于 commit `14187ec`(2 行 facade 转发),v11 现全过
  - **借鉴点 (v1.4.x 已应用)**: AtomLifecycleManager 异步维护循环 + start/stop 接口形式借鉴 livingmemory; sync 退化到独立线程借鉴 livingmemory MemoryEngine 在 AstrBot 同步钩子里的用法



- [x] **B5** 补 3 个 Agent Tool (shipped 2026-06-19, 与 v21 同期)
  - `hippocampus/tools.py` 加 3 个 handler: `_forget_handler` / `_list_recent_handler` / `_search_by_entity_handler`
  - `_forget_handler` 默认 soft_forget (forgotten_at=now + strength=0, 保留 audit); `hard=True` 走 `store.delete` 真删; 重复 forget 返 `mode='noop'`; missing/unknown id 返 `ok=False`
  - `_list_recent_handler` 走 `store.list_active(limit=max(50, k*20))` + Python 端按 `actor_id` 过滤; 不足 k 时只返实际条数; missing actor_id 返 `ok=False`
  - `_search_by_entity_handler` 先 `find_entity_by_name` 精确查, 失败回退 `search_entities` LIKE; 命中后扫 `engram.entity_refs` 含该 id 的; unknown entity 返 `ok=False`; missing entity_name 返 `ok=False`
  - 加 3 个 factory: `build_forget_tool` / `build_list_recent_tool` / `build_search_by_entity_tool`; `all_tools()` 扩到 5 tool (recall / memorize / forget / list_recent / search_by_entity)
  - 同步更新 `_smoke_v16.py` 契约: `all_tools()` 期望从 2 tool 升到 5 tool (2 处断言: `test_all_tools_returns_five` + `test_star_registers_tools`)
  - 新建 `_smoke_v22.py` (7 测试): schema 稳定名 / soft round-trip / hard 真删 / error 分支 / list_recent actor 隔离 + newest first / list_recent missing actor / search_by_entity 大小写不敏感 + entity_refs join, 全过
  - 性能注: list_recent + search_by_entity 当前 Python 端 filter (`store.list_active` O(N)); 大规模下用 SQL JOIN 是 B11 范畴, B5 scope 内够用
  - 全量 v08-v22 业务 12/15 ALL OK. v11 spread_activation 真因为 facade 缺 kwarg 转发, 已修于 commit `14187ec`; v12 真实 bug 是 `MemoryService` 无 `stop()` 同步方法(smoke 调了不存在的 API),已修(smoke 改 `stop_background_tasks` + 加 `close/del/gc` 防 Windows 文件锁),与 B5 无关;v13 已通过(本轮重跑 ALL OK,9 测试)

---


## v1.4.x smoke 状态基线 (2026-06-19)

最后一次完整回归:python astrbot-plugin-hippocampus/_smoke_v08.py ... _smoke_v26.py,**19/19 ALL OK,零失败**。

最近两个修复:

- **v11** (commit 14187ec):facade MemoryService.spread_activation 漏转 decay / floor kwarg,2 行 facade 转发修复
- **v12** (commit bb3b9bc):smoke 调了不存在的 MemoryService.stop(),改用 stop_background_tasks + 加 close/del/gc 防 Windows 文件锁,15 行

修复模式总结(给后续 v1.4.x 维护参考):

- 都是 **smoke 与 service 重构不同步**造成的 latent bug,不是业务逻辑错
- v11 是 facade **签名丢 kwarg**(底层有,facade 没透)
- v12 是 facade **方法名整体丢失**(被 B4 的 async 命名替换)
- 两者都是 v1.3 → v1.4 演进期 facade 重命名/重签名时 smoke 漏改,藏在测试金字塔底层
- 教训:facade 改名/改签名时,**优先 grep 仓库所有调用点**,而不是只改实现;或在新 facade 加 @deprecated 兼容层跑过过渡期

历史 v11/v12 注释(分散在上方 B5 / B7 / B8 / B9 段落)已统一指向 14187ec / bb3b9bc。如果将来 v1.4.x smoke 又出现 1/N 失败,先来这里对比上次绿点。