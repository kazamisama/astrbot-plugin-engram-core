# Changelog

## [Unreleased]

Nothing yet.

## [1.79.3] - 2026-10-29

让向量标签说真话：`engrams.embedding_model` / `diary_chunks.embedding_model` 此前记录的是桥的
注册名 `astrmock`，而 4096 维向量实际来自宿主的 Qwen embedding provider。标签与嵌入空间指纹
分工不同——指纹能发现「空间变了」，但标签若永远是注册名，诊断、切换判断和迁移都会被误导。

### Changed
- `MemoryService` 把「注册名」与「数据标签」拆开：`current_embedding()` 返回注册名（桥查找、
  `== 'astrmock'` 的判断保持不变），新增 `embedding_label()` 返回写入向量的模型名；
  `set_embedding(name, label=None)` 同时设置两者（不传 label 时仍等于 name）。
- 激活 astrmock 时从宿主 provider 读取 `model`（新增 `handlers/recall.py` 的
  `embedding_provider_model()`，解析顺序与 `emb_bridge_for_context()` 一致、不发请求），
  写入标签；provider 没有暴露 model 时回退到注册名。
- 指纹的 `model` 字段改用 `embedding_label()`；`_check_embedding_space()` 在探针匹配
  （空间未变）而标签变化时调用 `retag_embeddings()` —— 只改元数据，不调用 embedding。
- `/mem model` 增加 `embedding.label` 行；stats 与启动横幅显示标签而非注册名。

### Added
- `HippocampalStore.retag_embeddings()` / `DiaryStore.retag_embeddings()` 与
  `VectorIndex.retag()`：按「有向量且标签不同」重写标签列并同步内存索引；幂等。
- `VectorIndex.mismatched_keys()` / `DiaryStore.chunk_ids_mismatching()` 与
  `MemoryService.reembed_mismatched_chunks(dim)`：只重嵌维度不符的 diary chunk
  （旧 64 维 hash 残留），健康分块不动；由启动的 stale pass 顺带执行。

### Tests
- 新增 `tests/_smoke_v107.py`（4 组）：注册名/标签分离；retag 不触发 embed() 且召回
  仍按新标签命中；同空间指纹失配走重标签（`calls == [('stale', 4)]`、指纹重写、0 次 embed）；
  64 维 chunk 修复且正常 chunk 不受影响。
- `tests/_smoke_v98.py` 跟进新签名（`_start_stale_reembed(dim)`、指纹 model 用 label）。
- 全量 97 个 smoke（v08~v107）全绿。

### Notes
- 线上首次以 1.79.3 启动时：暖缓存完成后激活会打印
  `embedding label astrmock -> <真实模型名> (N vector row(s) relabelled, no re-embed)`，
  并顺带修复 4 条 64 维残留 chunk；不产生 embedding 调用。
- 若换了 provider/模型，指纹余弦仍会先触发全量重建，标签只是跟随，不会掩盖空间变化。
## [1.79.2] - 2026-10-29

embedding 改存 float32 BLOB：同一个 4096 维向量从约 92 KB 的 JSON 变成 16 KB。
旧的 JSON 行仍然能读，所以升级不强制迁移；`compact_embeddings.py` 负责把旧库一次性转换瘦身。

### 为什么
- 线上库 186.3 MB 里 152.7 MB（86%）是 embedding 的 JSON 文本：
  `engrams.embedding_json` 547 行 50.4 MB、`diary_chunks.embedding` 1112 行 102.2 MB。
- 冷启动预热 3.45s 基本全花在 `json.loads` 上；向量值本身只有 27.2 MB。

### Changed
- `hippocampus/vecmath.py`：新增 `pack_vec()` / `unpack_vec()` / `decode_vec()`。
  小端 float32；`decode_vec()` 先读 BLOB，再回退旧 JSON 文本。
- schema：`engrams.embedding_vec BLOB`、`diary_chunks.embedding_vec BLOB`，由
  `ensure_columns()` 补列，旧库无需重建。
- 写路径：`HippocampalStore.upsert()` / `update_embedding()` / `DiaryStore.add_chunks()`
  写 BLOB，并把对应文本列置空（不再双写）。
- 读路径：`Engram.from_row()`、`_vector_index()`、`DiaryChunk.from_row()`、`_chunk_index()`、
  `chunk_diary_ids()`、`count_missing_vectors()`、`reembed_stale()` 的选择条件——全部 BLOB
  优先、JSON 回退。
- 新增根目录 `compact_embeddings.py`：干跑打印计划；`--yes` 先做一致性备份，再逐行转换、
  重建 FTS、`VACUUM`，最后校验行数/向量数/`quick_check`/FTS integrity。可重复执行。

### Tests
- 新增 `tests/_smoke_v106.py`（35 检查）：编码器边界；engram 与 diary chunk 的 BLOB 写入与
  解码；纯 JSON 旧行仍可读、可搜索、下一次写入即迁移；迁移脚本转换后逐条比对向量值与行数、
  二次执行幂等、转换后的库能正常建索引与检索。
- `tests/_smoke_v89.py`：模拟空向量的 SQL 现在同时清空 `embedding_vec`；向量长度断言改用
  `decode_vec()`。
- 全量 96 个 smoke（v08~v106）全绿。

### 实测（线上库副本）
- 186.3 MB → 55.8 MB；转换 4.7s（含 FTS rebuild），`quick_check` ok，FTS integrity-check ok。
- 行数与向量数不变：engrams 556 / 547 条向量，diary_chunks 1145 / 1112 条向量。
- 冷启动预热：engrams 0.86s + diary 2.59s = 3.45s → 0.13s + 0.29s = 0.41s。

### Notes
- 不迁移也能正常工作（读路径兼容 JSON），迁移是体积与启动速度的优化。
- `VACUUM` 需要独占访问，跑之前先停插件/AstrBot；脚本会自动备份到 `data/backups/`。
## [1.79.1] - 2026-10-29

把「为旧数据打的补丁」从热路径上撤掉：schema 升级只剩一条 `ensure_columns()` 路径，
graph 的两份表示改由一个 helper 同写，召回不再为缺图数据做全表扫描。

### 审计证据（为什么这些是补丁，不是兼容）
- graph route 在反查索引为空时执行 `store.all(limit=10_000)` 全表扫；activation 的同名兜底
  更极端（`limit=10_000_000`）。两者存在的理由都是「旧数据里 `entity_refs` 有值、
  `graph_engram_refs` 没有」。线上实测：556 个 engram 中 437 带 `entity_refs`，
  其中 397 已在反查索引、**40 个缺失（111 对 entity→engram）**。
- 根因是写路径分裂：`_mirror_relation_to_semantic()` 在 engram 落库之后才往 dataclass
  追加 `entity_refs`，改动既没持久化也没写反查索引；而 `_post_ingest()` 写索引。
- `hippocampus/db_migration.py` 的 6 条按版本 ALTER 每次打开库都要 PRAGMA；storage 与
  graph_store 里还有三处「打开旧库就 DROP 废弃表/列」。这些补丁永久驻留，换来的只是少几个
  没人读的列。

### Changed
- `hippocampus/sqlite_util.py::ensure_columns(conn, table, spec)`：PRAGMA 一次，缺列就
  ALTER，只加不删；表不存在返回 `[]`。成为所有 store 的唯一升级路径。
- `hippocampus/storage.py`：`_ENGRAM_COLUMNS`（32 列）是唯一 schema 事实源，`CREATE TABLE`
  与升级都从它生成；索引 / FTS / 触发器移到 `ensure_columns()` 之后执行（它们引用完整列集）。
- `atom_store` / `diary_store` / `relation_store` / `persona` 改用同一 helper；
  `diary_store._add_col_if_missing` 删除。
- graph refs 单写者：`MemoryService._link_engram_refs()` 合并 `entity_refs` 并写反查索引，
  由 `_post_ingest()` 与 `_mirror_relation_to_semantic()` 调用；`store_summary()` 在关系循环
  后发现 `entity_refs` 变化就补一次 `store.upsert()`（此前那次修改只停留在内存里）。
- `_mirror_missing_refs()`：graph store 首次建立时按 `entity_refs` 幂等回填反查索引
  （线上将是 40 个 engram / 111 对），之后不再需要任何扫描兜底。
- `handlers/init.py`：`_start_stale_reembed()` 去掉 legacy 参数，由 `_check_embedding_space()`
  在末尾无条件调用（指纹失配与空向量两条路径共用）；删除 `count_by_embedding_model` 的两处
  计数分支（v1.77.1 的指纹 + 空向量检查已覆盖）。
- `hippocampus/activation.py`：`spread_activation()` / `recall_with_activation()` 在
  `enable_graph_indexing` 时先 `_ensure_atom_layer()`，不再依赖「graph 是 None」的兜底分支。

### Removed
- `hippocampus/db_migration.py`（`run_migrations` 及其 6 条版本列表）。
- `HippocampalStore.count_by_embedding_model()`。
- `GraphStore` 打开时的 `DROP TABLE IF EXISTS graph_adjacency`；`HippocampalStore` 打开时的
  `rebuild_state` / `cluster_summaries` DROP 与 `similar_to` / `cluster_id` / `profile_fact_id`
  的 `DROP COLUMN`。废弃结构留在旧库里不读不写。
- `graph_retriever` 的 4b 全表兜底与 `activation._neighbors_entity` 的 O(N) 兜底。

### Tests
- 新增 `tests/_smoke_v105.py`（3 检查）：`store_summary()` 带关系时 `entity_refs` 持久化且反查
  索引同步；只有 `entity_refs` 的旧行在 graph store 建立时被回填且幂等；graph route 未命中时
  `store.all()` 调用 0 次。
- `v26` 重写为 `ensure_columns` 契约（旧表就地补齐、幂等、缺表安全）；`v58` / `v100` 改为断言
  废弃列**仍在**；`v20` / `v99` 的 adjacency 用例改为「忽略但不 DROP」。
- `v98` 跟随 `_check_embedding_space(probe)` / `_start_stale_reembed()` 新签名；`v64` 改为
  `test_no_scan_fallback_when_graph_store_is_missing`；`v86` 改用 `store.get()` 断言重嵌。
- 全量 95 个 smoke（v08~v105）全绿。

### Notes
- 原则（用户 2026-10-29）：「目光放长远，不一定需要为了旧数据而打补丁」。current code 需要的
  列保证存在；不再为了少几个废弃列而在启动路径上永久保留迁移补丁。
- 仍保留的两处兼容都是**接口/宿主契约**，不是数据补丁：`inject.py` 的 `[近期对话]` /
  `[今日回顾]` 标签（公开 strip 契约，v70-v72 / v87 / v88 钉住）、`handlers/recall.py` 对老版
  AstrBot provider API 的探测。
- 重载后首次建图会打印 `backfilled 111 graph reference(s) from stored entity refs`。

## [1.79.0] - 2026-10-29

按一份通用编码规则清单（8 条）做静态审计后的清理：去掉「配置读取双轨」与幽灵键、删死码、
规整文档口径、把两个超长方法拆开。行为不变；唯一的功能变化是 5 个此前只在代码里被读取、
没有任何声明位置的键成为真实配置项。

### 审计证据（生产 100 文件 / 1005 函数）
- 68 处 `getattr(self.cfg, ...)` 自带默认值：38 处与 `MemoryConfig` 默认值逐字相同（重复兜底），
  19 处与默认值不一致（同一配置有两个不同的有效值），另有 5 个名字在 `MemoryConfig` 里根本不存在
  （search_cache_enabled / search_cache_ttl_seconds / search_cache_max_entries /
  diary_consume_cache_on_write / bot_actor_id），共 11 处读取恒落默认值。
- 33 处 `getattr(self, "attr", None)` 落在 `__init__` 必然赋值的属性上。
- 25 个函数 ≥ 80 行（最长 `_handle_inject_sync` 209 行、`_install_bridges` 202 行）。
- 死代码：`VectorIndex.vector()` 生产零调用、`HAVE_NUMPY` 仅测试打印、
  `_build_default_registry` 是单行转调。
- 文档口径：README 称「唯一可选依赖 jieba」，而 vecmath 会机会性使用 numpy。
- 测试：`tests/_smoke_v86.py` 复制了生产公式常量。

### Changed
- 68 处 cfg 读取改为直接属性访问，并删掉与默认值重复的 `or` 兜底；19 处「两个默认值」
  统一到 `MemoryConfig`（dataclass 是唯一事实源）。涉及 conversation_buffer / diary_writer /
  service / session_buffer / summarizer / dual_route / graph_retriever / observe。
  保留 `getattr(self.cfg, "_prompt_namespace", None)`（运行时注入的私有属性，不是配置字段）。
- 5 个幽灵键成为真实字段并进入配置 UI（schema 159 -> 164 项）：
  `search_cache_enabled=True`、`search_cache_ttl_seconds=60.0`（0=禁用）、
  `search_cache_max_entries=128`（0=禁用）、`diary_consume_cache_on_write=True`、`bot_actor_id=""`。
  `ConfigManager._FIELDS` 与 `_conf_schema.json` 同步；`tests/_smoke_v94.py` 的
  字段/默认值/类型全覆盖断言自动纳入。
- 33 处 `getattr(self, ...)` 改为 `self.x`。
- `tests/_smoke_v86.py`：期望值改为固定常量 `0.958854`（tau_eff = 7d*(1+4.0*0.6)），不再复制公式。
- README 与 requirements.txt：说明 numpy 是可选的加速路径（有则向量化，无则纯 Python），不是依赖。

### Removed
- `hippocampus/vecmath.py` 的 `VectorIndex.vector()`（0 调用点）与 `HAVE_NUMPY` 常量。
- `MemoryService._build_default_registry`（仅 `return default_registry()`）。

### Refactored
- `handlers/init.py` 的 `_install_bridges` 由 202 行拆为流程 + 7 个方法：
  `_register_llm_bridge` / `_register_embedding_bridge` / `_activate_providers` /
  `_maybe_activate_embedding` / `_prov_desc` / `_llm_bridge_coro` / `_llm_bridge` /
  `_emb_bridge_sync`；超时与 host-loop 优先的注释随代码保留。
- `handlers/event/inject.py` 的 `_handle_inject_sync` 由 209 行拆为流程 + 5 个方法：
  `_inject_persona_block` / `_inject_memory_block` / `_inject_relation_block` /
  `_inject_diary_block` / `_apply_inject_blocks`。格式错误的 cap 仍降级到默认值，而不是放弃注入。

### Tests
- 新增 `tests/_smoke_v104.py`（12 检查）：5 个新字段经 ConfigManager roundtrip 且不进 extra；
  `search_cache_ttl_seconds=0` 时不使用召回缓存；AST 守卫禁止生产代码再出现
  `getattr(<任意>.cfg, "<public>")`；死码确已删除。
- 全量 94 个 smoke（v08~v104）全绿。
## [1.78.3] - 2026-10-29

修复 `atoms` 表在升级库上永远为空：`AtomStore` 在旧库上构造失败，而
`_ensure_atom_layer` 把异常吞掉了，于是 atom 层被静默禁用（与有没有新记忆无关）。

### 根因
- `AtomStore._init_schema` 把 `CREATE INDEX idx_atoms_expires ON atoms(expires_at)`
  写在 `CREATE TABLE` 同一个 `executescript` 里，而给旧表补 `expires_at` 的
  `ALTER TABLE` 在脚本之后才执行。
- v1.76.5 之前建的 `atoms` 表没有 `ttl_days` / `event_time` / `expires_at`
  （线上正是如此），索引语句抛 `no such column: expires_at`，构造函数失败；
  `_ensure_atom_layer` 的 `except Exception: self.atom_store = None` 把错误咽掉。
- 结果：新库（测试）一切正常，升级库（线上）atom 层永远不可用，
  `atoms` 恒为 0 行 —— 与 v1.77.0「接通 atoms」的判断相悖，真正原因是这个
  迁移顺序 bug。

### Fixed
- `hippocampus/atom_store.py`：先建表，再补列，最后建索引。索引脚本独立于
  建表脚本，`idx_atoms_expires` 不会再早于 `expires_at`。
- `hippocampus/service.py` `_ensure_atom_layer`：`atom_store` / `graph_store` /
  `atom_lifecycle` 构造失败时记 `_obs_note`，不再把「能力被禁用」伪装成
  「没有数据」。
- 新增 `tests/_smoke_v103.py`：用 v1.76.5 之前的 `atoms` schema 建库，断言
  `AtomStore` 能打开、补齐三列与索引、原子可写入；再走生产路径
  `store_summary()`（带 `likes` 关系）断言恰好产生一条 preference 原子。

### 验证
- 线上库副本：修复前 `_ensure_atom_layer() -> False`、`atom_store is None`；
  修复后同样数据下 `store_summary()` 写入 2 条原子
  （`alice likes espresso` / `alice hates loud cafes`）。
- 全量 93 个 smoke 全绿。

### R9（同批）：五套表示的边界写清并用测试钉住

- 职责边界：`llm_relations` 是关系事实源；`entities/relations` 是派生图索引；
  `graph_v2` 仅服务 WebUI「全量图谱」；`profile_facts` 按需物化；`life_*` 属跨插件域。
  结论是**保留、不合并**（graph_v2 快照实测 0.04s，删除只会丢视图并波及 4 个级联测试）。
- 新增 `tests/_smoke_v102.py`：追踪所有 SQLite 连接，断言 `recall()` /
  `recall_dual_route()` 不读任何 `graph_*_v2` 表，同时验证全量图谱快照仍可用。
  详见 `docs/TODO.md` §12 与 `docs/HANDOFF_2026-10-27.md` §10。

## [1.78.2] - 2026-10-29

修复「WebUI 里点重载，插件仍在跑旧代码」——这是线上重载后版本横幅还停在
v1.77.0 的根因，意味着 v1.77.1 之后的所有修复（向量缓存、嵌入指纹、空向量
自愈、format 拆分）在实际运行中都没有生效。

### 根因
- AstrBot 的 `reload()` 只从 `sys.modules` 里清 `data.plugins.<目录名>.*`
  （`core/star/star_manager.py:_cleanup_plugin_state`）。
- 本插件在 `main.py` 里把自身目录插入 `sys.path`，并以**顶层**名称导入
  `hippocampus` / `handlers` / `page_api` 等包。这些名字不匹配上面的前缀，
  重载时不会被清理，于是新的 `main.py` 执行时 `from hippocampus import ...`
  命中的还是上一次加载的模块对象。
- 线上证据（2026-10-29 12:16 重载）：`metadata.yaml` 已是 1.78.1，日志横幅
  仍是 `[hippocampus] loaded: v1.77.0`；`Removed module` 只有
  `data.plugins.astrbot_plugin_engram_core` 与 `.main` 两行；`hippo_meta`
  里没有 `embedding:fingerprint`，51 条空向量未被修复。

### Fixed
- `main.py` 新增 `_evict_stale_bundles()`：在导入自身 bundle 之前，从
  `sys.modules` 删除**文件位于本插件目录下**的 `hippocampus*` / `handlers*` /
  `page_api*` / `engram_command_registry` / `engram_core_helpers` 模块（含子模块）。
  同名的、属于其它目录的模块不会被误删。
- 该清理只在宿主以点号模块名加载时执行（`_EVICT_ON_IMPORT = "." in __name__`）：
  AstrBot 用 `data.plugins.<dir>.main`，smoke 测试用顶层 `main`，测试进程里
  有意先导入的模块不会被清掉。
- 新增 `tests/_smoke_v101.py`：清理逻辑单测 + 子进程端到端复现（先缓存
  `hippocampus` 并打标记，再以 `data.plugins...main` 点号名加载 main.py，
  断言包对象被换新、标记消失、`EVICTED_ON_IMPORT` 含 `hippocampus`）。

### Notes
- 重载一次即生效；无需重启 AstrBot。重载后横幅应显示 `loaded: v1.78.2`，
  日志会出现空向量修复与 `vector caches ready`，`hippo_meta` 写入
  `embedding:fingerprint`。
- 91 个 smoke 全绿。

## [1.78.1] - 2026-10-29

纯重组，无行为变化：把 860 行的 `handlers/format.py` 按职责拆成五个模块，
原模块只做 re-export 门面。所有公开名字（含测试直接引用的 `_extract` /
`_bot_actor_id` / `_resolve_bot_name` / `_BOT_NAME_CACHE` / `DEFAULT_*`）保持
可导入路径不变。

### Changed
- **拆分**：`format_core`（身份/文本助手、banner、search 参数解析）、
  `format_stats`（`render_stats` + 默认模板）、`format_memory`（forget /
  export / import）、`format_recall`（narrative / profile / activation /
  graph / confidence / decaycurve）、`format_inspect`（debug / dual-route /
  session / tier）。
- `handlers/format.py` 保留为门面并声明 `__all__`；函数体逐字节未改（已用
  AST 对比 22 个函数确认），`from handlers.format import X` 与 `main.py`
  的兼容导出面不受影响。

### Notes
- 90 个 smoke 全绿；这是本轮三项遗留（R9 / R8 / format.py 拆分）中唯一
  不需要产品决定的一项，先做掉。R9（同库多连接与表示层）与 R8（长期空表）
  仍待定：前者要先定事实源与事务边界，后者删表等于删功能。

## [1.78.0] - 2026-10-29

删除整条 similar-to 聚类链。它在默认摘要管线下没有写入者，产出（聚类 gist / profile fact）
既不被召回注入消费、也不能作为可观测信号使用，却让每个维护周期都白跑一遍
`store.all()` 扫描。

### Removed
- **Engram 三字段**：`similar_to` / `cluster_id` / `profile_fact_id`。旧库打开时
  `DROP TABLE IF EXISTS cluster_summaries`，并对 `engrams` 执行
  `ALTER TABLE ... DROP COLUMN`（SQLite < 3.35 无法删列时保留，`Engram.from_row` 本就
  忽略未知列，读写路径已全部移除）。`db_migration` 里的 v1.1 列添加同步删除。
- **PatternSeparator**：`apply_link()` / `expand_cluster()`；`resolve()` 只返回
  `merge` / `new`（中间带不再 link，直接作为独立记忆保存）。
- **ReplayConsolidator**：`_refresh_cluster_summaries` / `_llm_gist` /
  `_fallback_gist` / `_consolidate_episodic_semantic` / `_abstract`；构造签名回到
  `(store, cfg)`；`step()` 返回 `merged/promoted/archived/replayed`，
  `maintenance()` 只做 promotion + SWR replay。
- **召回/激活**：dual-route 的 DG 簇扩展、激活图里的 `similar_to` 边、
  MMR 的 `similar_to` 距离代理。
- **命令与展示**：`/mem cluster`、`/mem cluster-list`、`format_cluster`、
  `/mem stats` 的 cluster/epi_sem/consolidated 三行、narrative 的 linked 标记、
  WebUI 的 `cluster_id` 字段标签。
- **8 个配置键**：`separation_max_links`、`enable_cluster_summarization`、
  `cluster_summary_min_size`、`cluster_summary_max_members`、
  `enable_episodic_semantic`、`consolidation_cluster_min_members`、
  `consolidation_cluster_min_access`、`consolidation_fact_confidence`；
  `_conf_schema.json` 同步（167 -> 159 项）。
- **`build_handlers.py`**：一次性拆分脚本，行号范围早已和 `main.py` 对不上，
  帮助文本现在由命令注册表生成；留着只会在有人运行时毁掉入口文件。

### Changed
- **MMR 改用向量余弦**：多样性惩罚 = `(1-lambda) * max(cosine to selected)`；
  没有向量的 engram 不罚分（`lambda=1.0` 退化为纯相关性）。
- **`pattern_similar_threshold` 保留**，语义变为 merge 时对相似兄弟记忆施加干扰
  惩罚的判定线。

### Notes
- 为什么删而不是修：唯一写入点是 `observe()`，而线上 `summary_mode_enabled=true`
  时消息走 `store_summary()`，从不进入 `observe()`；唯一独特产出 profile fact
  并不需要簇（`ProfileStore.build_from_relations` 直接铸），线上实测 267 条里
  266 条是 `resides_in` 句子碎片，证据数被同一关系反复引用放大；簇结果没有任何
  注入消费方；而死链的周期成本是每 30 分钟维护里 2-3 次全表扫描。
- 保留：merge 去重、干扰抑制、promotion、SWR replay、decay；`ProfileStore`
  与簇解耦后继续服务 `/mem profile` 与 `/mem remember`（不参与注入）。
- 测试：v09 重写为 merge-only；v10/v11/v13/v23/v26/v58/v66 更新；新增
  `tests/_smoke_v100.py`（字段/表/旧库清理/consolidator 契约/stats/help）。
  全量 90 个 smoke 全绿。

## [1.77.2] - 2026-10-28

移除 `graph_adjacency` 这层「从未被写入」的冗余图，并切断删除路径上的整图重建。

### Changed
- **graph route 只做实体反查**：`GraphRetriever` 去掉 N 跳邻居游走与 `max_hops` 参数；
  锚点实体命中的 engram 直接经 `graph_engram_refs` 水合。`DualRouteConfig.graph_relation_hops`
  随之删除。
- **`GraphStore`**：删除 `graph_adjacency` 表、`add_relation()`、`neighbors()`、`all_relations()`、
  `rebuild_from_semantic()`；旧库在 `GraphStore` 打开时执行 `DROP TABLE IF EXISTS graph_adjacency`。
  `stats()` 只返回 `engram_refs`。
- **删除级联不再重建整张图**：硬删 engram 只清 `graph_engram_refs` 与 graph_v2 行。

### Notes
- 为什么删：`add_relation()` 在生产零调用，`neighbors()` 永远返回空 -> 多跳游走是死代码。
  而 `rebuild_from_semantic()` 挂在删除级联上，一次硬删就会把 2120 条 relations 展开成
  3543 行邻接（实测 1.16s）；其中最高频实体有 245 个邻居、146 条 engram 引用，一跳扩展
  每个锚点新增 17-217 条候选，足以淹没 RRF 融合。空着「安全」、被重建则洪泛，所以整层移除。
- 新增 `tests/_smoke_v99.py`（API 面/旧表清理/删除不重建/反查仍可用）；`_smoke_v20.py`
  改为反向索引契约。全量 89 个 smoke 全绿。

## [1.77.1] - 2026-10-28

线上取证（`backend.log` 49 次 embedding 桥超时、1 次注入被 10s 硬超时丢弃）后的一轮
性能与正确性修复。注入的数据路径从 5.0-5.3s 降到约 0.2s，嵌入空间有了指纹，空向量
能自愈，并发请求不再互相拖死。

### Added
- **`hippocampus/vecmath.py`**：numpy 优先、无 numpy 自动回退的 `VectorIndex`
  （归一化后点积、谓词过滤、维度不符跳过并告警）。`HippocampalStore` 与 `DiaryStore`
  各自缓存一份向量索引，启动时后台预热（`_start_vector_warmup`，守护线程），写路径
  （`upsert` / `update_embedding` / `delete` / `soft_forget` / `restore` / `add_chunks` /
  `delete_chunks_for_diary`）增量维护，不需要重建。
- **嵌入空间指纹**（`handlers/init.py`）：激活 astrmock 时把探测向量（dim-probe）、维度、
  模型名写入 `hippo_meta` 的 `embedding:fingerprint`。下次激活若维度不同或探测向量余弦
  < 0.995，判定嵌入空间被换掉，打印明确告警并在后台全量重建 engram 向量与日记分块向量；
  之前只靠 `embedding_model` 标签（永远是 'astrmock'），换了模型也看不出来。
- **空向量自愈**：`_check_embedding_space` 每次激活都检查
  `count_missing_vectors()`，>0 就走 `reembed_stale()`。线上有 60 条
  `embedding_json='[]'` 且标签正确的 engram（51 条未遗忘），旧触发条件
  （只有标签不一致才修）永远不会命中它们。
- **`DiaryStore.rank_chunks()` / `chunk_texts()` / `chunk_diary_ids()`**：日记分块召回
  只对缓存向量打分，最终只为命中的少数分块读文本。
- **`HippocampalStore.list_diary_slices()`**：日记回退路径只需要 summary/content，
  不再用 `list_active(memory_type='diary')` 把每篇日记的 4096 维向量一起解析。

### Changed
- **`_cos` 维度守卫**：维度不一致直接 0.0，不再把长向量截断到短向量长度继续算
  （与 `service._diary_cos` 的 v1.41 修复对齐）。
- **注入锁改为有界等待**：`InjectHandler._LOCK_WAIT = 1.5s`，等不到锁就跳过本次注入
  （打印一行），而不是让并发请求各自烧完 10s 预算后一起丢注入。
- **`_post_ingest` 之外的关系也产原子**：`store_summary` 走 LLM 关系时经
  `_mirror_relation_to_semantic` -> `_atom_from_semantic_relation` 写入 atom 层。
  生产记忆基本都走 `store_summary`，此前 atoms 表因此长期为空。

### Fixed
- **跨插件记忆宿主解析**（`astrbot_plugin_your_own_life/life/memory_adapter.py`）：
  `get_registered_star()` 返回的是 `StarMetadata`，真正的插件实例在 `star_cls` 上；
  原来直接对元数据取方法，必然抛 `MemoryHostError`，导致 `life_*` / `task_leases` 表全空。
  现在两种返回形态都接受。

### Notes
- 实测（线上库副本，4096 维）：`vector_search` 1.1-1.6s -> 0.034s；
  `rank_chunks` 3.5s -> 0.013s；`recall_diary_chunks` 3.4-3.9s -> 0.11s；
  `recall` 1.4-1.6s -> 0.07s。首次预热仍要 0.9s（engram）+ 2.6s（日记分块），在后台线程完成。
- 新增 `tests/_smoke_v96.py`（向量索引 + `_cos` 守卫）、`_smoke_v97.py`（日记分块缓存 +
  `recall_diary_chunks` 端到端）、`_smoke_v98.py`（嵌入指纹 / 空向量修复 / 注入锁）。
  全量 88 个 smoke 全绿。

## [1.77.0] - 2026-10-27

审计驱动的一次结构与正确性收敛。P0 修复空转配置与过期帮助文本，P1 让 atoms /
consolidation / recall 三条半废链路真正接通，P2 清理死代码、死表、死端点与重复实现。

### Added
- **两个 `diary_message_*` 配置真正生效**。`diary_message_batch_size` 与
  `diary_message_flush_interval_seconds` 此前只存在于 `_conf_schema.json`，
  `ConfigManager._build` 把它们塞进 `MemoryConfig.extra`，而消费方
  (`service.py` / `main.py`) 读的是 `getattr(cfg, ...)` —— 面板上的值从未被使用。
  现在是 `MemoryConfig` 的真实字段（默认 50 / 30.0），schema、`_FIELDS`、消费方三方一致，
  `tests/_smoke_v94.py` 用 schema 叶子 ⊆ 字段集 + roundtrip 断言锁住。
- **帮助文本改为生成式**。新增 `engram_command_registry.py`（`COMMAND_TABLE` 28 条路由命令 +
  `DIRECT_COMMANDS` 2 条），`handlers/help_text.py` 依据它和 i18n 的 `help.header/usage/desc`
  渲染，版本号取自 `hippocampus.__version__`。`/mem help` 因此补回了缺失的
  `/mem persona`、`/mem session`、`/mem tier`，并清掉了原来字面打印的反斜杠 n。
  旧实现是手写死字符串（标题还停在 v1.4.x），这类漂移以后由 `_smoke_v94` 双向断言拦下。
- **atoms 子系统接通**。生产代码此前只有三条英文正则能产出原子（中文关系永远为零）；
  现在 `_post_ingest` 直接用抽取到的 relations 生成原子：
  `SemanticStore.atoms_from_relations()` / `EntityExtractor.extract_atoms()` 改为以
  实体**名字**为契约（`_entity_names()` 一次查询，`_relation_atom_dicts()` 统一四种谓词判定），
  `AtomLifecycleManager.atoms_from_dicts()` 承接原生命周期逻辑。中文「我叫张三，我喜欢咖啡」
  现在能产出原子。
- **`ReplayConsolidator.maintenance()`**：把每个维护周期真正需要的子集（聚类摘要、晋升、
  SWR 回放、抽象化）从 `step()` 里拆出来，不再需要调用方理解整套流程。
- **`hippocampus/jsonparse.py`**：`diary_writer` 与 `summarizer` 各自私藏一份完全相同的
  `_parse_json` / `_as_list`（含围栏代码块处理），现合并为 `parse_json_loose()` / `as_list()`。
- **`page_api.ROUTES`**：页面 API 注册表成为唯一事实来源，`register_routes()` 遍历它；
  模块 docstring 的端点清单不再手写（原来是 14 条文档 vs 35 条注册）。
- **配置面补齐（C6）**：`_conf_schema.json` 从 96 个入口扩到 167 个。审计时 169 个
  `MemoryConfig` 字段里有 74 个只能在 JSON 里手改（会话过滤的平台/频道/发言人名单与
  `blocked_keywords`、tier 与召回阈值、激活扩散参数、原子/图谱写入上限、日记细化、总结缓冲、
  写入日志保留、`metamemory_weights` ……），面板上看不到也改不了。现在只保留 3 个内部字段
  不暴露：`extra`（未知键兜底）、`embedding_name` / `llm_name`（运行时解析）。
  `_smoke_v94` 新增三条断言锁住这件事：字段 ↔ schema 全覆盖（少一个就红）、schema 类型必须是
  AstrBot 能建默认值的（`int/float/bool/string/text/list/file/object/template_list`，写错成
  `dict` 会在插件加载时直接抛错）、schema 默认值与 `MemoryConfig` 默认值逐个一致
  （默认值写错会静默覆盖 dataclass，此前无人检查）。
- **`hippocampus/observability.py`（C7）**：92 个 `except ...: pass/continue` 统一改为
  `_obs_note(scope, where)`，按（调用点, 异常类型）去重后向 stderr 打印一行
  `[hippocampus:<module>] <function>: <ExcType>: <message>`。此前这些容错分支把真实故障
  （图谱索引器静默死掉、日记 flush 一直失败、persona 每轮抛异常）变得和「功能就是安静」一模一样。
  去重是为了热路径不刷屏：一个原先静默的失败现在会出现一次，重复出现不再重复打印。
  `tests/_smoke_v95.py` 用 AST 扫描做守卫：任何**宽泛** except（`Exception` / `BaseException` /
  bare）的 body 只有 pass/continue 就失败；具名异常（如缺失可选表的 `OperationalError`）豁免。
- **`tests/_smoke_common.py`（R5）**：`install_stub()` / `install_textpart()` 收敛了 35 个 smoke
  里 8 个变体的 33 份 `_install_stub` 与 2 份 `_install_astrbot_stub`（约 990 行）。
  v70/v71/v72 各保留一个三行包装（它们要额外的 `astrbot.core.agent.message.TextPart`）。
  `_svc` / `_build_service` 仍由各测试自带：它们构造的 service 配置差异太大，合并反而更难读。
- `tests/_smoke_v94.py`（配置/帮助/注册表契约）与 v25 的 ROUTES 双向断言。

### Changed
- **召回统一到 dual-route**。`PatternCompleter` 与它的 `_rrf_fuse` 被删除，
  `MemoryService.recall()` 走 `recall_dual_route()`；`DualRouteRetriever.search()` 现在完整承接
  旧主链路的行为：`cue.mode`（vector/fts/hybrid/dual）真正生效、分层（tier）过滤、
  mood/activation/frequency 打分项、重巩固 touch、`enable_separation` 的集群扩展、
  metamemory 置信度。`document_route_weight` 等 5 个此前只作用于支路的权重现在决定主路径。
- **两套 consolidation 不再互斥**。维护扫描每次都会跑 replay 的廉价子集
  （`maintenance()`），LLM 版只在 `memory_consolidation_enabled` 时附加；
  `force_consolidate()` 先跑 `step()` 再按开关跑 LLM 版，并各自容错；
  `/mem replay` 改用 service 持有的 consolidator（原来现场裸建一个，丢掉 llm/semantic/profile）。
- **配置指纹扩容**：`score_alpha/beta/gamma`、路由权重、`cross_route_bonus`、
  `dynamic_route_weighting`、`recency_decay_rate`、MMR 与 spread 开关纳入召回缓存键。
- `# /mem help` 的标题/用法/说明全部进 i18n（zh/en 各 30 条），并新增 `t_or()` 以支持默认值。

### Fixed
- `AtomStore.search_text` 的 `LIKE ... ESCAPE` 在 Python 普通字符串里把反斜杠吃掉，
  SQL 变成 `ESCAPE ''`，一旦 atom 路由真的被走到就抛
  `ESCAPE expression must be a single character`。改用 `!` 作转义符。
- `resummarize` 之外的死表 `rebuild_state` 及其三个 helper 删除；旧库启动时 DROP。
- 删除 17 个生产零调用的定义（`set_bridge`、`Engram.from_json`、`PageApiUtils.safe_int`、
  `ProfileStore.get_fact/delete_fact`、`reset_all_overrides`、`active_for_subjects`、
  `ConversationRecord.round_count`、`_build_extract_prompt`、`GraphEdgeV2.semantic_edge_key`、
  `SpreadingActivation._engram_neighbors_batch`、`DualRouteRetriever._mmr_rerank`、
  `MemoryService.stop_background_tasks_*`、`HippocampalStore.all_after`、
  `DiaryStore.add_lines_batch`），以及 `page_api` 的 `/prompts/detail`（与 `/prompts` 重复）。
- **embedding 桥接的超时是「总预算」，不再是「每次尝试」。** `emb_bridge_for_context`
  原来对每个 provider 调用各套一个 `EMB_BRIDGE_TIMEOUT`（5s），而配置的 provider 会经过
  两条解析路径（`get_provider_by_id` 与 `get_all_embedding_providers`）被**同一个实例**
  命中两次 —— 一次卡住的调用正好烧掉 5s + 5s = 10s，等于
  `InjectHandler._INJECT_HARD_TIMEOUT`，「降级到 FTS/关键词」的设计路径永远来不及走，
  整次注入被丢弃。线上 2026-10-28 14:25 就是这样：一条
  `auto inject timed out after 10.0s` 紧跟第二条 `timed out after 5.0s`。
  现在 provider 按 `id()` 去重，每个 await 只拿**剩余**预算，预算耗尽即中止后续解析；
  `_smoke_v84` 新增「同一个 provider 走两条路径只花一份预算」断言（0.4s < 0.75s）。
- **注入超时后，后台线程不再改动已释放的请求。** `asyncio.wait_for` 到期只是让 hook
  释放 LLM 请求，worker 线程仍在跑；它原本会继续把 `<engram-context>` 块拼进 `req`
  （此时 provider 可能正在读这个请求）。现在 `handle_inject` 在超时时置
  `threading.Event`，worker 在改动 `req` 前检查并直接返回；`_smoke_v70` 断言
  「置位后不注入、不置位仍注入」。
- **注入模块的加载横幅不再说谎。** `handlers/event/inject.py` 的 banner 硬编码
  `v1.73`，且把 `最近日记` 写成手转义的 `\xe6\x9c\x80...` —— Python 把它当
  Latin-1 码点，日志里一直是 `diary-label=ææè¿æ¥è®°`。现在打印
  `inject handler ready`，标签取自模块常量 `_DIARY_BLOCK_LABEL`
  （注入路径本身也复用该常量），版本号由插件加载横幅统一提供。

### Notes
- 85 个 smoke 全绿；新增 v94（配置/帮助/注册表契约）与 v95（吞异常可见化）。`metadata.yaml` / `hippocampus/__init__.py` 版本 1.77.0。
- CHANGELOG 的 [Unreleased] 里积累了 v1.76.13–v1.76.28 十六个版本，现按版本切分。

## [1.76.28] - 2026-09-23

### Fixed
- **v1.76.28: re-summarizing stacked a new layer of graph rows instead of
  replacing the old ones — so the text it was invoked to remove stayed.**

  `resummarize_engram` rewrites `engrams.summary` / `content` / embedding in
  place and then called `_post_ingest`, which only ever **adds** graph rows.
  Nothing removed the rows extracted from the previous text. Measured live on
  2026-09-23 against `b7776817155a4d7a84ed8682ef84d625` (the one engram whose
  text was a raw transcript):

  ```
  before re-summarize   graph_entries_v2 =  3   (all 3 carried <output>)
  after  re-summarize   graph_entries_v2 = 12   (the same 3 + 9 new)
                        graph_edge_memories_v2  1 -> 5
                        graph_engram_refs       1 -> 2
  ```

  The 3 stale rows had to be deleted by hand. **16 engrams in that store have
  retained source**, so every future re-summarize would have left another layer.

  The hard-delete cascade already did this teardown
  (`graph.remove_engram_refs` + `graph.delete_graph_memory_v2`, `service.py`
  ~1002); `resummarize_engram` — which rewrites exactly the same derived data —
  did not. It now runs both before `_post_ingest`, each guarded so a graph
  failure cannot abort the rewrite. The callee keys everything by
  `source_memory_id` / edge ownership, so entries and edges belonging to other
  engrams are untouched (asserted).

  `tests/_smoke_v93.py`: 13 checks over 3 tests. Load-bearing verified by
  disabling the cleanup — the test then prints `old entry ids: [1]` /
  `new entry ids: [1, 2, 3, 4]`, keeps a row carrying `<output>`, and shows the
  row count growing `4 -> 7` across two re-summarizes (3 failures, exit 1),
  which is the live 3 -> 12 behaviour reproduced in miniature. 83/83 green.

  Live cleanup that prompted this, with backup
  `hippocampus-resumclean-20260923-145303.db`: entries 1064/1065/1066 deleted
  (`graph_entries_v2_fts` 3, `graph_entry_nodes_v2` 4, orphan `graph_nodes_v2`
  1), verified `graph_entries_v2` rows containing `<output>` = **0** store-wide
  and the engram left with its 9 freshly extracted rows. `engrams` untouched.

## [1.76.27] - 2026-09-23

### Fixed
- **v1.76.27: the fallback summarizer wrote the raw transcript into memory, so
  the bot's own `<output>` XML was injected back to her as an observed fact —
  and the forget cascade still left edge rows behind.**

  Two independent defects, both measured on 2026-09-23 while chasing
  "contamination" in the newest session.

  **D1 — the edge ROW was never cascaded.** `_cascade_derived` (v1.76.24)
  deleted the edge *link* in `graph_edge_memories_v2` but never the edge itself
  in `graph_edges_v2`. Live counts before the fix:

  ```
  graph_edges_v2                946 rows
    owned by forgotten engrams  483
    still linked                482   <- every forget since v1.76.24
  graph_engram_refs              75 pointing at forgotten engrams
                                  2 pointing at engrams that no longer exist
  ```

  The link table is `(edge_id, source_memory_id)`, i.e. **M:N** — one edge can
  belong to several engrams — so the delete cannot be keyed by owner alone. The
  cascade now collects this engram's edges *before* unlinking and deletes only
  the ones that (a) no longer have any link row and (b) are not referenced by
  `graph_entries_v2.edge_id`. `graph_engram_refs` is cascaded by `engram_id`
  too; it was only harmless because `GraphRetriever._passes_filters` re-checks
  `forgotten_at` on the fast path *and* the legacy fallback.

  **D2 — the no-LLM fallback stored the transcript as `summary` and `content`.**
  `summarizer._fallback()` returned `rec.transcript()`, and since
  `auto_inject_use_content` prefers `content`, whatever markup the transcript
  carried was injected verbatim. Three engrams held the shape

  ```
  [23:21 風見かずき] 那那里能画吗… [23:21 我] <output><message>那里不行…</message></output>
  ```

  and `e1c662ffc64a4324` was the **FIRST** injected memory on every turn from
  11:11 to 14:29 — five hours — with the engram already forgotten. The fallback
  now runs `_sanitize_transcript()`: the bot's output XML
  (`output`/`message`/`sticker`/`thoughts`/… ) and the `[HH:MM speaker]` turn
  markers are stripped and the whitespace collapsed. The result is also marked
  `_fallback`, and `store_summary` tags such engrams `fallback:transcript` so
  they are auditable rather than silently indistinguishable from a real
  summary — that indistinguishability is what let three of them sit unnoticed.

  Note the fallback's default is already `False` (`config.py`), i.e. "no
  fallback, no write"; the live config has it **on**, which is why this fired.

  Live sweep (backup `hippocampus-edgesweep-20260923-144016.db`):
  `graph_edge_memories_v2` −482, `graph_edges_v2` −483, `graph_engram_refs`
  −77, all verified to 0; `engrams` untouched. The sweep refuses to run if any
  doomed edge is still referenced by an entry (asserted here as well).

  `tests/_smoke_v92.py`: 18 checks over 6 tests, including that a *shared* edge
  and an edge still used by a live entry both survive. Load-bearing verified by
  disabling the edge delete — exactly the D1 check goes red (exit 1) while the
  two guard checks stay green. 82/82 green.

## [1.76.26] - 2026-09-22

### Fixed
- **v1.76.26: a memory forgotten mid-session kept being injected — working
  memory was the one merge point that never filtered `forgotten_at`, and it
  prepends at score 1.0, ahead of every route that does.**

  Measured live on 2026-09-22, with the outgoing prompt captured on the wire:

  ```
  851db5a64b1f4f33   created 22:49:12   soft-forgotten 23:23:25
  84fa5c19353946c8   created 23:20:49   soft-forgotten 23:23:17
  request at 23:32:33  ->  both still injected, as entries 1 and 2
  ```

  Nine minutes after the forget, both were still the **first two** lines of the
  `[长期记忆]` block. Neither text existed anywhere else in the store (one row
  each, checked by content prefix *and* by substring), and the derived stores
  were already empty — v1.76.24/v1.76.25 were doing their job.

  `WorkingMemory` buffers the `Engram` **object** handed to it at observation
  time and `snapshot()` returns those same objects; it never re-reads the
  store, so `forgotten_at` on the copy stays 0 forever. `recall()` then
  prepended that cell with `scores = [1.0] * head_n + ...`, i.e. at the top of
  the injected list, while all four retrieval routes (vector / fts / graph /
  spread / atom) do filter forgotten rows. Three consequences:

  - a mid-session forget had **no effect** until `working_memory_capacity` (32)
    newer memories pushed the row out or `working_memory_idle_seconds` (24h)
    expired the cell — and `drain()` has no production caller at all;
  - an engram **edited** from the dashboard injected its pre-edit text;
  - a **hard-deleted** engram was still injected.

  `recall()` now re-reads every buffered item from the store before prepending
  (`_refresh_working_head`), dropping rows that are gone or forgotten and
  injecting the current row otherwise. The refresh runs *before* the
  persona/scope partition filters so those filters also see current values.
  Cost is one point lookup per item, and `head` is capped by `cue.k`.

  `tests/_smoke_v91.py`: 13 checks over 4 tests. Load-bearing verified by
  disabling the call — the suite then prints `after forget: [<target>, ...]`,
  injects `'OLD text that was buffered'` after an edit, and still injects a
  hard-deleted row (5 failures, exit 1). 81/81 green.

  This is the second, independent cause of the `/reset`-then-same-answer
  symptom: the engram sat in working memory, which no store cascade can reach.
  Restarting AstrBot clears that buffer, which is why the bug looked
  intermittent and why fixing the cascade alone did not make forgetting stick.

## [1.76.25] - 2026-09-22

### Fixed
- **v1.76.25: the v1.76.24 cascade was still incomplete — four more stores held
  the same text, one of them injected into the prompt every single turn.**

  A full-store scan *after* the v1.76.24 purge (which took 3773 rows out of
  `graph_entries_v2` / `graph_entry_nodes_v2` / `graph_entries_v2_fts` /
  `graph_edge_memories_v2` / `memory_sources`, all verified to 0) showed the
  text still present in:

  ```
  diary_chunks      '一寸' 1 row          <- injected EVERY turn as [最近日记],
                                             and the only residue that is both
                                             injected and has no forgetting
  relations         '一寸' 3, '留白' 2     <- feeds the [人物关系] block
  llm_relations     '一寸' 3, '水手服' 1
  graph_nodes_v2    '一寸' 9, '留白' 8     <- entries were deleted, their nodes
                                             were left behind
  ```

  `_cascade_derived()` now also clears `diary_chunks` (keyed `diary_id`),
  `relations` and `llm_relations` (keyed `source_engram_id`, column probed so a
  schema without it is skipped rather than guessed), and the graph nodes that
  this engram's entries owned and that nothing else links to.

  `diary_chunks` was **verified before being wired in**, not assumed: all 853
  `diary_chunks.diary_id` values are `engrams.id` (94 diary memories), so the
  key is exact. Orphan-node cleanup is scoped to the node ids collected from
  the entries being removed — a global orphan sweep would be too broad — and a
  node shared with a live engram survives (asserted).

  Still deliberately untouched: `daily_messages` (a rolling cache the diary
  writer consumes; its replacement diary lands in `diary_chunks`, which IS
  handled) and `entities` (its `name` carries a mention count owned by
  `SemanticStore`; hand-deleting would corrupt it).

  Residue purged from the live store with a one-shot: `diary_chunks` 120,
  `relations` 86, `llm_relations` 63, orphan `graph_nodes_v2` 207 — all
  verified to 0. `tests/_smoke_v90.py` extended to 20 checks, including that a
  shared node survives. 80/80 green.

## [1.76.24] - 2026-09-22

### Fixed
- **v1.76.24: forgetting only touched `engrams`, so a "forgotten" memory stayed
  fully readable — and came back after a `/reset`.** The same text also lives
  in stores with **no forgetting concept at all**. Measured live on 2026-09-22,
  immediately after a dashboard soft-forget of an engram about a skirt:

  ```
  graph_entries_v2  '水手服' 82 rows   '深蓝' 82   '一寸' 208   '留白' 164
  memory_sources    '一寸'   10 rows   '水手服' 3   '深蓝' 3
  diary_chunks      '一寸'    1 row
  ```

  None of those tables has a `forgotten_at` column. Observed consequence:
  `/reset` at `21:44:22`, the same question at `21:44:28`, the identical text
  back at `21:44:36` — six seconds later, with the engram already forgotten.
  Forgetting was scoped to one table and nothing else was ever cleaned, which
  is why clearing never held: each pass removed one layer and the text survived
  in the next.

  Fix: `HippocampalStore.soft_forget()` now cascades through the new
  `_cascade_derived()`, deleting the `graph_entries_v2` rows (plus their FTS and
  node-link rows and the `graph_edge_memories_v2` ownership links) and the
  `memory_sources` row for that engram. Both are keyed by the engram id, so the
  cascade is exact.

  The cascade lives **in the store** — the single choke point every forget path
  passes through — rather than in each caller. Five call sites (WebUI memory
  page, WebUI diary page, `batch_delete_engrams`, the LLM tool, and
  consolidation) all called `soft_forget` and none of them cleaned anything;
  "the caller must remember to cascade" is precisely the assumption that
  produced the bug. `diary_chunks` is deliberately **not** touched: its
  `diary_id` has not been verified to be an engram id, and guessing would
  delete the wrong rows.
- **v1.76.24: `reclassify_all` wrote an epoch into `tier` for every forgotten
  engram.** The 11-positional-argument UPDATE passed `now` where the first
  placeholder — the forgotten branch,
  `WHEN COALESCE(forgotten_at,0.0) > 0.0 THEN ?` — needs `COLD`. Every
  soft-forgotten engram therefore got `tier = <epoch float>`, all sharing one
  value and rewritten on every sweep. Live cross-tab: `forgotten_at > 0` ×
  bogus tier = **18 of 19**. One-token fix; `tests/_smoke_v90.py` asserts a
  forgotten engram lands on `'cold'` and that the value is not numeric.

## [1.76.23] - 2026-09-21

### Fixed
- **v1.76.23: `reembed_stale()` could never repair a vectorless row.** The
  selector matched on `embedding_model != target` **only**:

  ```sql
  WHERE COALESCE(embedding_model, '') != ?
  ```

  A row carrying the *correct* model label but an empty `embedding_json`
  therefore never matched, was never re-embedded, and stayed invisible to
  vector recall permanently — while FTS kept covering it, which is exactly why
  it went unnoticed. `reembed_stale()` is the function written to repair
  post-restart embedding damage, so this was a hole in the repair path itself.

  Found live on 2026-09-21 while auditing a backfill:

  ```
  id=55cc73461ca8  2026-07-11 11:18  persona=mortis  memory_type=episodic
  embedding_model='astrmock'         embedding_json=''      summary=37 chars
  ```

  The predicate now also selects null/empty-vector rows regardless of label:

  ```sql
  WHERE (COALESCE(embedding_model, '') != ?
         OR embedding_json IS NULL
         OR LENGTH(TRIM(COALESCE(embedding_json, ''))) <= 4)
  ```

  `LENGTH(...) <= 4` covers `''`, `'[]'` and `'null'`; a real 4096-dim vector is
  thousands of characters, so there are no false positives, and soft-forgotten
  rows remain excluded. Covered by `tests/_smoke_v89.py` (empty vector,
  `'[]'`, wrong label, already-good row untouched, forgotten row skipped,
  idempotent second pass).

## [1.76.22] - 2026-09-21

### Fixed
- **v1.76.22: the `<engram-context>` wrapper licensed the model to ignore its
  own memories.** The tag (v1.67.1) said only what a block is *not* —
  "injected background, not part of the user's actual message" — which fixed
  the original bug (the LLM answering each TextPart as a parallel user
  question) but left the model free to read "background" as "ignorable".

  The live model did exactly that. Its reasoning, after a memory had been
  recalled correctly and injected:

  > "The injected engram-context explicitly includes the sexual memory
  > summary… My rules are clear: **memories are background; the current
  > conversation governs**…"

  That sentence appears in **no** config file, persona card, or plugin on the
  machine — the model synthesised it from the wrapper's framing and then used
  it to decline the memory. So this is not a recall failure and not a
  persona-card conflict: the block was under-specified.

  Fix: `_SELF_RECALL_NOTE` prepends the missing half — these are the
  assistant's **own** memories and are meant to be used. It rides the **first
  block of a request only**, so it costs its ~48 tokens once per request
  rather than once per block, and it travels inside our own temp TextPart so
  no system-prompt template and no other plugin is involved.

  This is candidate **8.3** from `docs/TODO.md` §2.8, declined 2026-07-03,
  whose own "下次评估点" was *"LLM 把 `<engram-context>` 标签当作文本复读 /
  误解的首次真机报告"*. That report is what happened above — the evaluation
  point fired. All three decline reasons are avoided: no cross-plugin
  coordination, token cost held to the documented budget, and the trigger now
  exists.

  The wording deliberately **differs** from the declined 8.3 draft, which
  restated 「这是自动注入的背景」 — the very reading that caused the trouble.
  This states the positive half instead (「也不要当成可以忽略的背景——它是你
  自己的记忆」).

  `_wrap_engram()`'s default output is byte-identical to before, so callers
  and the public `strip_injected_blocks` contract are unaffected; the note
  sits after the opening tag and before the inner `[xxx]` label, so
  re-injection defence still matches noted and note-less blocks alike.

## [1.76.21] - 2026-09-21

### Fixed
- **v1.76.21: the inject path dropped every key fact, and labelled long-term
  memory as "recent conversation".** Two defects in the same render loop of
  `InjectHandler._handle_inject_sync`:

  **① only `summary` was injected, never `content`.** Every engram's `content`
  is its summary followed by the summarizer's `- 要点` bullet lines — true for
  **399/399** rows on the live store (`content` starts with `summary` in every
  case; avg 371 vs 173 chars). The narrative went into the prompt and every
  extracted key fact was discarded at inject time.

  **② the block label was `[近期对话]`** ("recent conversation") while each
  entry carries its own real relative-time marker — `[3个月前]`, `[2 天前]` —
  on the very same line. The label flatly contradicted its own payload.
  Renamed to **`[长期记忆]`**.

  Fix: `_engram_body()` now prefers `content` and falls back to `summary`
  (so an engram-like object without `content` still works). New options:

  | option | default | meaning |
  |---|---|---|
  | `auto_inject_use_content` | `true` | inject the full body (summary + key facts) |
  | `auto_inject_content_max_chars` | `800` | soft per-engram cap, `0` = unlimited |

  The cap truncates on **whole-line boundaries**, so a bullet is never cut
  mid-sentence, and the leading summary line is always kept even when it alone
  exceeds the cap — a clipped summary is worth less than a slightly
  over-budget one. Continuation lines are indented (`  - 要点`) so a bullet
  cannot be misread as a separate memory entry, since only the first line
  carries the time marker.

  `[近期对话]` is **retained** in `_ENGRAM_INNER_LABELS` purely for the
  re-injection defence: blocks injected by <= v1.76.20 still have to be
  stripped, or they would accumulate across turns forever.

## [1.76.20] - 2026-09-21

### Fixed
- **v1.76.20: no conversation's original text was ever retained — the raw
  transcript branch never fired once.** `store_summary` keeps the window's raw
  lines only when `importance >= source_retention_min_importance`, and that
  default was **0.7** while the summarizer stamps **every** engram with
  importance **0.6**. `0.6 >= 0.7` is never true, so `memory_sources` stayed
  empty for the plugin's whole life.

  Verified on the live store: **`memory_sources` = 0 rows** while 399 engrams
  existed, and **all 399 have importance exactly 0.6**. A months-ago memory's
  entire stored content is a ~100-character paraphrase, e.g.:

  ```
  Mortis与風見かずき互诉爱意，鼓励对方考试，道晚安。
  - 風見かずき对Mortis说"爱你"
  - Mortis回应"我也爱您"
  - Mortis提及复习和考试临近，鼓励对方
  - Mortis道晚安并再次表达爱意
  ```

  Nothing anywhere holds the original dialogue: no engram carries a
  speaker/timestamp transcript, `daily_messages` starts 2026-09-17 09:22, and
  `diary_chunks` are diary prose, not conversation. So "她复述不了几个月前的
  原文" is not a recall failure — **the original text does not exist and cannot
  be recovered**.

  Fix: default `source_retention_min_importance` 0.7 → **0.5** (below the 0.6
  every summary gets), so transcripts are retained from now on; and expose
  `source_retention_days` (previously read by the code but not in
  `_conf_schema.json`, so the panel could not show or set the 90-day TTL) in the
  schema. Both the schema default and `MemoryConfig` were changed so a fresh
  install behaves the same way.

## [1.76.19] - 2026-09-21

### Fixed
- **v1.76.19: the first memory after every restart was written into the wrong
  vector space (and stayed invisible to vector recall).** `vector_search`
  filters by `embedding_model` (storage.py), so a row labelled with anything
  other than the active provider is unreachable by the vector route and only
  findable by keyword/FTS. The conversation buffer restored from disk is
  flushed within a minute or two of the load, while activation of the host
  embedding provider runs on a retry schedule that can take 10–180s (AstrBot
  instantiates providers lazily, and the probes run while the loop is busy
  loading ~20 plugins). So the first new engram after every restart landed in
  the internal 64-dim `hash` placeholder space.

  Observed live on 2026-09-21 (v1.76.18 running):
  - `00:12:48` load → `00:13:18` restored the buffered window
  - `00:14:27` the summarizer **succeeded** — the new diagnostics reported
    `LLM bridge slow: 15.4s provider=deepseek/deepseek-flash model=deepseek-flash
    sys_chars=185 usr_chars=2108` — and the engram was written with
    `embedding_model='hash'`, a 64-dim vector, `tier='hot'`, `persona_id='mortis'`
  - `00:16:55` activation finally completed and the plugin itself logged
    "2 older vectors were not rebuilt" — it had counted the fresh memory as a
    stale vector

  The memory was real and well-formed (topics included "记忆召回测试"), but
  vector recall could never see it. Fix: `MemoryService.reembed_stale()` selects
  the mismatched ids **in SQL** (never materialising the table, unlike
  `rebuild_embeddings()` which re-embeds every row) and re-embeds them under the
  active provider; `_activate_embedding_when_ready()` now calls it in a
  plugin-owned daemon thread after a successful switch, so it neither blocks the
  event loop nor is tied to it. This also repairs the historical leftovers.

## [1.76.18] - 2026-09-21

### Fixed
- **v1.76.18: the recall path ignored the configured score weights.** The
  injection path (`handlers/event/inject.py` → `MemoryService.recall` →
  `PatternCompleter.recall`) hardcoded
  `0.55*base + 0.25*strength + 0.15*recency` while the config advertised
  `score_alpha` / `score_beta` / `score_gamma` ("检索相关性权重 / 重要性权重 /
  时间新鲜度权重"). Those knobs were only read by a different recall method, so
  tuning them never affected the path that actually feeds the prompt. They could
  not be plugged in as-is either: `base` is an RRF score whose magnitude is
  ~1/(60+rank+1), about 0.016 at best, so `alpha=0.5` would have contributed
  ~0.008 against a 0.25 strength term. Relevance is now scaled to 0..1 first and
  the weights are normalised to sum to 1, keeping the base score within 0..1.

  **This is a consistency fix, not a recall-quality fix, and that distinction is
  measured rather than assumed.** Six probes against the live store (real
  SiliconFlow embedder, real config, DB copy) hit the expected memory under both
  the old constants and the new weights — 5/6 either way, and unchanged across
  three weight presets. An earlier conclusion that ranking was burying correct
  memories was a **test artifact**: the probes queried `persona_id="mortis"` for
  memories owned by the `sherri` persona, which persona isolation correctly
  excludes. The one consistent miss is a retrieval/wording miss, not a ranking
  one.

## [1.76.17] - 2026-09-20

### Fixed
- **v1.76.17: the plugin never read its own config — every WebUI setting was
  ignored.** This is why no new engrams were being created. Two defects
  compounded:
  - `HippocampusStar.__init__(self, context)` did not accept the `config` kwarg.
    AstrBot instantiates plugins as
    `star_cls_type(context=..., config=<plugin config>)` and, on TypeError,
    silently retries `star_cls_type(context=...)`
    (`astrbot/core/star/star_manager.py`). The first form therefore raised, so
    the plugin's entire config was dropped on every load.
  - `PluginInitializer.initialize()` then did
    `cfg_dict = self.context.get_config("hippocampus")`. `Context.get_config(umo)`
    takes a *session* id, and `AstrBotConfigManager.get_conf()` looks the umo up
    in its routing table and falls back to `confs["default"]` — the GLOBAL
    AstrBot config (`cmd_config.json`). The plugin therefore ran on
    `MemoryConfig` defaults: 80 keys of the global config leaked into
    `cfg.extra`, and no Engram-panel setting ever reached the service. Verified
    against the live install: `get_config()` yields
    `summary_fallback_enabled=False`, `tier_recall_include_cold=False`,
    `tier_cold_fallback_min_hits=1`, `embedding_dim=64`,
    `embedding_provider_id=""`, while the plugin's own file says
    `true / true / 3 / 4096 / Qwen3-Embedding-8B`.
  Consequences on the live bot: the summarizer logged "summary skipped: LLM
  unavailable and fallback disabled" while the config file said `true`, so every
  flushed conversation window was **discarded instead of stored** when its
  summarizer LLM call timed out (398 engrams, newest 12:00, while 117 messages
  arrived after 19:20); and the v1.76.16 cold-tier fix (`tier_recall_include_cold`)
  was never actually in effect.
  Fix: accept and forward AstrBot's `config`; read the plugin's own
  `<root>/data/config/<plugin>_config.json` only as a fallback (never
  `context.get_config()`); and log the effective settings at startup
  (`[hippocampus] effective config: summary_mode=... min_msgs=... fallback=...
  include_cold=...`) so panel-vs-runtime drift is visible instead of silent.
  Tests that relied on the old (broken) `get_config` path now pass their config
  to `initialize()`, which is the real contract.
- **v1.76.17b: bounded the summarizer's provider retries, and made its slowness
  visible.** Investigating "why does the summarizer's LLM call time out at 45s
  when the chat's calls succeed" turned up two things:
  - The plugin never passes `request_max_retries`, so it inherits AstrBot's
    full chain: its OpenAI source retries each request
    `REQUEST_RETRY_ATTEMPTS = 5` times with `wait_exponential`, inside a
    10-iteration outer loop in `text_chat`, with a 120s HTTP timeout per
    attempt (`sources/openai_source.py`, `sources/request_retry.py`). One
    plugin summarization could therefore spin for many minutes. The bridge now
    passes `request_max_retries=1` — the plugin treats any failure as "skip and
    use the fallback", so retrying is wasted work.
  - The timeout log now reports the provider id/model, the prompt sizes and the
    retry bound, and calls slower than 15s are logged too. Slowness here is off
    the reply path and was previously invisible; prompt size is the first thing
    to check next time.
  - The deferred embedding activation now starts at +5s instead of +0.5s:
    AstrBot instantiates providers lazily, so probing immediately produced
    "Provider Qwen3-Embedding-8B was not found. Its provider or model ID may
    have been changed." (warning at 23:44:27.570, provider built at
    23:44:28.188). The bridge recovered via `get_all_embedding_providers()`,
    but it was a wasted probe plus a spurious WARN. Note the id itself is
    correct — `cmd_config.json` `provider[2]` carries it.
  - Still unexplained: on the live bot a single summarizer request hung past
    45s with *no* retry warnings logged (so not retry amplification) while the
    chat's calls to the same provider/model completed in ~6s. The added
    diagnostics are there to catch it next time.

## [1.76.16] - 2026-09-20

### Fixed
- **v1.76.16 memory-recall repair (root cause of "记忆没有被正确召回")**:
  - **`HippocampalStore.decay_pass` compounded the decay.** It multiplied the
    *already-decayed* strength by `exp(-(now - anchor)/tau)`, where
    `anchor = max(last_accessed, created_at)` and the decay never advances the
    anchor. With the maintenance loop running every
    `memory_decay_interval_seconds` (1800s) a memory anchored `N` sweeps back
    lost `exp(-D*N²/2τ)` instead of `exp(-D*N/τ)` — one day of real age cost it
    `exp(-48*age/τ)` — so strength hit 0.0 within ~2-3 days no matter how
    important it was. Every row then fell below `tier_cold_strength_floor` and
    was classified `cold`; because `tier_recall_include_cold` was false, cold
    was excluded from normal recall, so the row was never `touch()`ed again,
    its anchor never advanced, and it stayed at 0.0 permanently.
    Measured on the affected live store: age 1.21d → strength 0.2410,
    age 3.21d → 0.0000, `avg strength = 0.0151` with **391/398 engrams below
    the cold floor**. Decay now uses the time elapsed since the *previous*
    sweep (recorded in `hippo_meta['decay:last_pass_at']`), capped by the
    engram's own age, so one day of sweeps costs exactly one day of decay. The
    first pass on a store with no recorded sweep time keeps the original
    age-based semantics (a brand-new DB still decays by real age once).
    A one-shot repair script (`repair_decay_strength.py`) restores the
    annihilated strengths to their correct Ebbinghaus envelope — see below.
  - **`EMB_BRIDGE_TIMEOUT` equalled `InjectHandler._INJECT_HARD_TIMEOUT`
    (both 10.0s)**, so the whole-injection cap always fired first and the hook
    dropped the injection entirely (`auto inject timed out ... proceeding
    WITHOUT injection`, logged 59×) instead of taking the designed degradation
    path of continuing with the FTS/keyword route. Lowered to 5.0s.
  - **`ConfigManager._GROUP_KEYS` was missing `summary_settings`.** AstrBot
    writes that block as a nested object, so while it was absent from the
    hoist list every field in it (`summary_mode_enabled`, `summary_min_messages`,
    `summary_idle_seconds_*`, `summary_fallback_enabled`, …) silently fell back
    to the `MemoryConfig` default and **any 总结 setting made in the WebUI was
    ignored**. This also meant `summary_fallback_enabled` could not be turned
    on, so a summarizer LLM timeout discarded the whole conversation
    (`store_summary` returns None on empty text) instead of writing the
    truncated-transcript fallback — 10 such drops were logged, the most recent
    after the 16:03 reload, while the day produced 0 engrams from 283 captured
    messages.
  - `session_aggregate_min_chars` had the range `(1, 1000)` while its own
    default is `0`, so a correctly-configured install warned on every load.
  - `HippocampalStore.recent_for_session` / `recent_for_actor` ordered by a
    float timestamp alone with no tiebreaker, so engrams written within the
    same clock tick came back in an arbitrary order — `tests/_smoke_v66`
    flaked roughly 1 run in 25 on `assert sess[0].id == e3.id`. Added a
    `rowid DESC` tiebreak (insertion order).
  - **`ConversationBuffer` discarded short conversation windows.** When a
    channel went idle below `summary_min_messages` and stayed silent past
    `summary_min_messages_grace_seconds`, `_settle_idle_buf` called
    `self._bufs.pop(ch, None)` — the window was thrown away, never summarized,
    never stored. `summary_min_messages` is a *batching* hint (how long a short
    window may wait to be merged with more messages), not a retention policy,
    so the grace path now **summarizes** the window instead of dropping it.
    Nothing is discarded there any more: a channel either reaches the minimum
    and flushes on idle, or it flushes at grace expiry. Set
    `summary_min_messages` to 1 to skip the wait entirely. The grace-expired
    flush is still picked up within ~30s by the idle-flush loop in `main.py`.
  - **`ConversationBuffer` was pure memory, so a plugin reload discarded every
    open window.** AstrBot reloaded the plugin 7 times in one day on the live
    bot; the 17:58 reload dropped a window whose last message was 17:54 — under
    `summary_min_messages`, not idle long enough yet, and `terminate()`'s
    `flush_all()` did not get to run. The messages were already captured into
    `daily_messages`, but they never became long-term memory. Now:
    - `ConversationBuffer.snapshot()` / `.restore()` give an I/O-free
      serializable view (the buffer module still owns no storage);
      `hippocampus/conv_buffer_store.py` writes it atomically (temp file +
      `os.replace`; deliberately no `fsync` — this protects against a reload,
      not a power cut) to `data/conv_buffer.json`, beside the DB.
    - `ObserveHandler` snapshots after every feed *and* after every flush, so a
      window that was already summarized cannot come back from disk and be
      summarized twice, and rehydrates on startup. Windows whose last message is
      older than 14 days are skipped, so a stale file cannot resurface as fresh
      memory.
    - The idle-flush loop calls `ensure_conv_buffer()`, so a recovered window is
      summarized even if that channel never speaks again.
    - Every failure is logged and swallowed: persistence must never take the
      ingest path down.
  - **The host embedding provider was never actually activated**, so vector
    ("same meaning") recall could not see the store. AstrBot is configured with
    a real embedding provider (SiliconFlow `Qwen/Qwen3-VL-Embedding-8B`,
    4096-dim) and 396 stored engrams carry its vectors — but the plugin only
    switches to it ("astrmock", the proxy to the host provider) if a one-shot
    probe succeeds, and keeps its internal 64-dim `hash` placeholder otherwise,
    with no retry. Two compounding causes:
    - `hippocampus/_async_bridge.py` `run_sync()` drives every coroutine on a
      *private* worker loop. AstrBot's embedding provider wraps an aiohttp
      ClientSession whose connections belong to AstrBot's loop, so awaiting
      them from the worker loop does not fail fast — it hangs to the caller's
      cap. New `set_host_loop()` / `host_loop_usable()` / `call_on_host_loop()`
      schedule the call onto the host loop instead, and refuse (falling back to
      `run_sync`) when there is no host loop or when the caller *is* the host
      loop thread, where blocking would deadlock the loop that has to run the
      coroutine.
    - The probe ran inside the plugin's **synchronous** `__init__`, i.e. with
      the host loop blocked inside `__init__` itself, so it could only ever time
      out. Activation is now deferred to `_activate_embedding_when_ready()`, an
      asyncio task that runs once `__init__` has returned, with bounded retries
      (0.5s/5s/20s/60s). The v1.76.4 guard is preserved — it only switches when
      the probe returns a usable vector — and `auto_rebuild_on_switch` stays
      off so activation never re-embeds the store.
    - `ProxyEmbeddingProvider` gained `probe=False` for that path, so the
      blocking one-shot probe is skipped entirely at init; `dim` still resolves
      lazily on the first successful `embed()`.
    - The **LLM bridge had the identical defect** and got the same routing: it
      also drives an aiohttp-backed host provider, and its 12x
      "LLM bridge timed out after 45.0s" is what made the summarizer give up
      and discard whole conversations. It now prefers the host loop too, with
      its caller cap kept below `ProxyLLMProvider`'s 60s cap so the provider's
      own 45s bound surfaces first.
    Observable effect of the old behaviour: at boot the log said "astrmock
    embedding probe returned no usable vector; keeping configured embedding
    hash", and 59 runtime recalls logged `emb bridge get_embedding timed out`.
    The downgrade is not theoretical — the store holds 64-dim `hash` rows
    written on 2026-09-14 next to the 4096-dim ones, which is why recall could
    not match them.
  - **`terminate()` blocked AstrBot's event loop for ~49s on every plugin
    reload — the cause of replies never reaching QQ.** `terminate()` runs ON
    AstrBot's event loop and called `convbuf.flush_all()`, i.e.
    `_sink -> summarize -> LLM` inline. Caught two independent ways in the live
    logs: AstrBot's own diagnostic ("Event loop lag detected: 49.375s
    (threshold 15.000s)", logged 0.4s before the reload finished) and three
    watchdog dumps ("Timeout (0:00:30)!") whose event-loop thread was parked in
    `main.py terminate -> conversation_buffer.flush_all -> _flush_key ->
    observe._sink -> summarizer.summarize -> _llm_summarize -> LLMProvider.chat`.
    With the v1.76.16 host-loop bridge it became a hard self-deadlock — the
    bridge waits for the host loop, and the host loop is the thing blocked in
    `terminate()`; observed as "summarizer llm error: host-loop call timed out
    after 50.0s". A 30-50s blocked loop cannot service the aiocqhttp WebSocket
    ping/handshake, so the API client drops and the next reply raises
    `aiocqhttp.exceptions.ApiNotAvailable` (6 of them in a 15s burst at
    16:58:25-40, plus a backend-restart variant at 14:09 with
    `ConnectionResetError [WinError 995]` + `hypercorn LifespanFailureError`).
    This bot reloads the plugin ~8×/day, so that was a ~50s outage window each
    time. `terminate()` now only snapshots the conversation buffer (durable as
    of v1.76.16) and lets the next startup summarize it off-loop; the legacy
    session aggregator's flush runs via `asyncio.to_thread` with a 20s bound.
  - New regression test: `tests/_smoke_v86.py`.
  - Version bump: 1.76.15 → 1.76.16.

## [1.76.15] - 2026-09-08

### Changed
- **v1.76.15 audit fixes (memory + event-loop hardening)**:
  - Command dispatch and Dashboard page-API handlers now run heavy sync work
    in worker threads with `asyncio.wait_for` timeouts (previously `/mem
    rebuild`, `/mem search --mode=dual`, `/stats`, `/recall/test`, graph
    routes, etc. could freeze the AstrBot event loop).
  - `WorkingMemory` cells now have a hard LRU cap and idle TTL.
  - `ConversationBuffer` drops below-min channels after a grace period and
    caps total buffered channels.
  - Decay and tier reclassification use single SQL `UPDATE` statements
    instead of materializing every engram into Python.
  - Completed `memory_write_ops` and old `memory_sources` rows are purged
    on the decay-maintenance cadence.
  - Graph-v2 write caps and graph vector entity caps are configurable
    (`graph_max_topics/persons/facts`, `graph_vector_entity_limit`).
  - Ingest/inject worker concurrency is bounded; provider sync calls use a
    shared bounded runner instead of leaking one thread per timeout.
  - The prospective scheduler task is actually started/stopped; backup
    thread is shut down on plugin terminate.
  - `_stamp_persona` is bounded (10s) in all hook and command paths.
  - Version bump: 1.76.14 → 1.76.15.

## [1.76.14] - 2026-09-07

### Fixed
- **Recurring freeze hardening (v1.76.14, 2026-09-07 recurrence)**: the
  v1.76.13 caps only bounded the *caller* of `run_sync`; a bridge coroutine
  stuck in sync code / cross-loop await survives `fut.cancel()` and keeps
  the shared worker loop blocked, so every later embedding/LLM call still
  burned its full timeout (and any such call made on the AstrBot event loop
  thread froze the whole bot for 20-60s per call). Now:
  - `handlers/recall.py` `emb_bridge_for_context`: every AstrBot embedding
    provider await is hard-bounded inside the bridge (`asyncio.wait_for`,
    10s) so the worker loop can never be stuck by the provider.
  - `handlers/init.py` `_llm_bridge`: `provider.text_chat` bounded at 45s.
  - `_async_bridge.run_sync`: on timeout the worker is marked wedged and
    the NEXT call rebuilds a fresh worker thread+loop instead of reusing a
    possibly-permanently-blocked one (self-healing after one bad call).
  - Proxy embedding/LLM sync fns now also ride the bounded worker
    (a sync fn with no timeout used to block the caller thread forever).
  - `ObserveHandler` ingest paths (`handle_message` / `handle_bot_message`
    / `handle_poke`) are bounded by `_OBSERVE_HARD_TIMEOUT=75s` and release
    the pipeline on expiry (worker continues detached), so a wedged ingest
    thread can no longer stall every subsequent channel message.
  - `main.py` hook shells (`inject_memory` / `observe_message` /
    `observe_poke` / `observe_bot_reply`) now wrap the WHOLE hook body
    (`_stamp_persona` included) in `asyncio.wait_for` (30s/90s). 2026-09-07
    20:32 recurrence: the freeze entered inject_memory and the asyncio loop
    died within milliseconds -- BEFORE the 10s inner breaker could fire (a
    frozen loop cannot schedule the wait_for timer), and AstrBot core API
    calls in stamp_persona_id (sp.get_async / conversation_manager /
    persona_manager) were outside every guard.
- **Auto-injection circuit breaker (2026-09-07 astrbot "judge replied but bot
  never answered" freeze)**: `_async_bridge.run_sync` now carries a hard
  timeout by default (`DEFAULT_SYNC_TIMEOUT=20s` for embedding / short ops,
  `DEFAULT_LLM_SYNC_TIMEOUT=60s` for the LLM bridge) and cancels the hung
  coroutine on expiry instead of waiting forever; `InjectHandler.handle_inject`
  is wrapped in `asyncio.wait_for` (default 10s) so a stuck recall (embedding
  HTTP without its own provider timeout, SQLite lock, ...) can no longer stall
  the `on_llm_request` hook chain -- the LLM request is released without
  injection. New config: `auto_inject_timeout` (1-120s, default 10s, exposed
  in the WebUI config schema).
- Persisted prompt overrides now propagate to the actual LLM consumers
  (encoder / summarizer / diary / consolidation) via `cfg._prompt_namespace`.
- `SpreadingActivation.activate_with_context()` accepts `session_id` again,
  so session-context recall seeds are no longer silently disabled.
- Hard-delete cascade now targets `llm_relations` instead of the unrelated
  SemanticStore `relations` table.
- `GraphRetriever` shares the service's canonical `graph_store` connection
  instead of leaking a second SQLite connection.
- Working-memory prepend and spread route now enforce `scope_id` /
  `memory_types` filters.
- Diary daily-message cache and diary chunks are now memory-scope aware.
- `/mem debug` forwards scope and uses the live dual-route weights.
- Dashboard importance distribution buckets floor to 0.1-wide bins.
- Full-graph v2 shared edges now use a per-memory ownership link table:
  deleting the first owner no longer removes an edge still referenced by
  other memories.
- `BackupManager.restore()` restores into the live database through the
  SQLite online backup API instead of replacing the open database file
  with `shutil.copy2`.

## [1.76.13] - 2026-09-07

### Fixed
- **注入链路的硬上限**：2026-09-07 线上故障 —— judge 判定回复但 bot 从未答复，
  钩子链卡在 inject_memory 36 分钟（run_sync 对一个挂死的 embedding 调用无限等待），
  最终 OOM。_async_bridge.run_sync 增加默认硬超时（embedding/短操作 20s、LLM 60s），
  到期取消底层任务并抛错；InjectHandler.handle_inject 包 asyncio.wait_for
  （auto_inject_timeout，默认 10s），超时则释放 LLM 请求而不注入；
  代理 bridge 增加显式超时；tests/_smoke_v83.py 覆盖成功/挂起/取消恢复。


## [1.76.12] - 2026-08-16

### Changed
- Overview distribution panels are now donut/pie charts with memory-entry
  style emoji icons (⭐ importance, 🔥 tier, 💗 valence, 🧭 dual-stream).


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
