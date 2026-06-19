# astrbot-plugin-hippocampus

> 类海马体长期记忆框架 — 4 层分桶 + DG 模式分离 + FTS5 hybrid + 用户可切u6362模型

把整个 `hippocampus/` 核心包打包进来,**自包含**,不依赖任何外部 Python 包。
仅依赖 AstrBot 本体(`astrbot.api`)。

## 目录

```
astrbot-plugin-hippocampus/
  metadata.yaml          # AstrBot 插件元数据
  _conf_schema.json      # 配置 schema(给 AstrBot 配置 UI 用)
  requirements.txt       # 零依赖
  main.py                # 插件入口(直接 import astrbot)
  hippocampus/           # 核心包(已嵌入,自包含)
  README.md              # 本文件
```

## 启动 banner

插件加载后会打印一行状态:

```
[hippocampus] loaded: v1.1.0, embedding=hash, llm=rule, engrams=0, embeddings=[hash, astrmock, openai], llms=[rule, astrmock, openai] | type /mem help
```

便于一眼看到当前生效的 embedding/llm provider 和已注册的可选项。

## 部署

把这个目录复制到 AstrBot 的 `data/plugins/` 下,目录名建议保留 `astrbot-plugin-hippocampus`。

AstrBot 启动时会自动扫描 `data/plugins/*/metadata.yaml` 加载本插件。

## 配置

在 AstrBot WebUI → 插件管理 → astrbot-plugin-hippocampus → 配置 里改:

| 字段 | 默认 | 含义 |
|---|---|---|
| `sqlite_path` | `data/hippocampus.db` | SQLite 存储路径 |
| `embedding_name` | `hash` | 初始 embedding provider |
| `llm_name` | `rule` | 初始 llm provider |
| `openai_api_key` | `""` | OpenAI key,非空时自动注册 |
| `openai_embedding_model` | `text-embedding-3-small` | |
| `openai_llm_model` | `gpt-4o-mini` | |
| `auto_rebuild_on_switch` | `True` | 切 embedding 自动重算 |
| `enable_semantic` / `enable_prospective` / `enable_promotion` | `True` | 三层开关 |

## 命令

| 命令 | 说明 |
|---|---|
| `/recall <query>` | 召回相关记忆(自动走 hybrid) |
| `/mem help` | 列出所有命令 |
| `/mem model` | 列出当前 + 可用模型 |
| `/mem model use embedding <name>` | 切换 embedding |
| `/mem model use llm <name>` | 切换 llm |
| `/mem rebuild` | 手动重算全量 embedding |
| `/mem prospective` | 看待办触发器 |
| `/mem stats` | 库内统计(engram / 实体 / trigger / 当前 model) |
| `/mem forget <id>` | 删 engram(支持完整 id 或 id 前缀) |
| `/mem search <query> [--mode=vector\|fts\|hybrid]` | 显式指定召回模式跑一次 |
| `/mem export <path>` | 把当前库导出为 JSON 文件 |
| `/mem import <path>` | 从 JSON 导入 engram/entity/relation/trigger(embedding 占位,需 `/mem rebuild`) |
| `/mem graph <entity>` | 围绕某个实体拉出 entity + relations + 来源 engram |
| `/mem cluster <id>` | 查 engram 的 similar_to 簇(双向, DG 模式分离) |
| `/mem narrative <topic>` | 按 entity/topic 串成自传式叙事 |
| `/mem replay` | 手动触发 SWR 复现(带 decay/GC/promote) |
| `/mem valence` | 情绪价分布直方图 |
| `/mem streams` | what / where_when 两流拆分 |
| `/mem confidence <query>` | 召回并显示每条记忆的元记忆置信度(v1.2) |
| `/mem decaycurve [id\|all]` | 用 Ebbinghaus 模型画 strength 衰减曲线(v1.2) |
| `/mem consolidate` | 手动触发巩固,含情节→语义抽象(v1.2) |

### `/mem search` 模式说明

- `vector` — 纯向量召回(只看 embedding 余弦)
- `fts` — 纯 SQLite FTS5 关键词命中
- `hybrid` — 两者融合,默认

`--mode` 不传或非法值都回落到 `hybrid`。

### `/mem export` / `/mem import` 格式

JSON 顶层结构:

```json
{
  "engrams":   [ {"id": "...", "summary": "...", "embedding": null, "...": "..."} ],
  "entities":  [ {"id": "...", "name": "...", "type": "...", "...": "..."} ],
  "relations": [ {"src": "...", "dst": "...", "rel": "...", "conf": 0.8} ],
  "triggers":  [ {"id": "...", "fire_at": 0, "payload": "..."} ]
}
```

`embedding` 字段在导出时保留原值(若存在);导入时统一置空,需运行 `/mem rebuild` 重新生成。

## 开发期 Smoke 测试

`astrbot_plugin/_smoke_v08.py` 是独立 smoke,验证 stats / forget / emb_bridge / search / export / import / graph / banner 全部路径,**不依赖 AstrBot 环境**(用 mock 注入 astrbot.api 模块)。

```bash
PYTHONPATH=astrbot_plugin python astrbot_plugin/_smoke_v10.py
```

## 桥接 AstrBot 自身 LLM/Embedding

`main.py` 里 `_install_bridges` 默认注册了一个 `astrmock` provider 桥接 AstrBot 当前 LLM。
要桥接 AstrBot 自己的 embedding,改 `_install_bridges`,示例:

```python
def my_emb(text: str) -> list[float]:
    # 调你 AstrBot 版本的 embedding API
    return ...

self.service.register_embedding(
    "astrmock",
    ProxyEmbeddingProvider("astrmock", my_emb))
```

然后用户 `/mem model use embedding astrmock` 切到 AstrBot embedding。

## DG 模式分离 (v0.9)

DG (dentate gyrus) 仿生:每条 observe 进入时,计算与当前 session 缓存里记忆的余弦相似度:

- `≥ pattern_separation_threshold` (0.92) → **merge**。新记忆合并到原来的,embedding / strength 同步到新。
- `≥ pattern_similar_threshold` (0.75) → **link**。双向加入 `similar_to` 链,单侧上限 `separation_max_links` (5)。
- 低于阈值 → **new**。

Recall 时,任一条 top-k 命中会顺便拉出它的 1-hop `similar_to` 同胞,以 0.95× 该根记忆分返回。深度限 1 是避免联合爆炸。

调节参数都在 `config.py`:

- `enable_separation: bool = True` — 全局杀开关
- `separation_max_links: int = 5` — 单侧链长上限
- `pattern_separation_threshold: float = 0.92` — merge 阈值
- `pattern_similar_threshold: float = 0.75` — link 阈值

## v1.0 生物学增强

v1.0 上了一整套海马体环路:

- **Valence + Intensity**—每条 engram 带情绪价(正/负)和强度(高/低兴奋)。负向记忆自动加 importance(负面偏见),高强度也加 importance。
- **Two-stream tagging**—`what`(身份/偏好/事实)与 `where_when`(时间/地点/计划)。对应腹侧/背侧海马体两流。
- **Temporal context**—离散时间桶(temporal_bucket),默认 1 小时一档。`cfg.temporal_bucket_seconds` 可调。
- **Schema bias**—新观察命中已知高频 entity(提及≥3 次)时,importance 被提升。反映 Bartlett 的 schema-driven 编码。
- **Proactive interference**—link/merge 时相邻 engram 被扣 `interference_strength_drop`(默认 0.05)。越类似越干扰。
- **Reconsolidation update window**—recall 后进入 lock 窗口, 如果同 session 内新观察与被 recall 的 engram 相似,则覆盖原 engram 的 content/summary/valence(Bartlett 重构式回忆)。`reconsolidation_update_enabled` 可关。
- **SWR replay boost**—定期 consolidate 会抽 top-64 高强度 engram 加强度、+1 access_count, 模仿睡眠 NREM 阶段的 sharp-wave ripple。`consolidator.step()` 现在返回 `replayed` 计数。
- **Soft forget + ghost trace**—`store.soft_forget(id)` 不删除, 仅标 `forgotten_at`, 语义图 / 结构查询仍可参考。`gc_pass(floor, min_age)` 才是硬删。
- **Ebbinghaus decay sweep**—`store.decay_pass(tau_base, floor)` 一次性对所有活跃 engram 应用 `strength *= exp(-dt/(tau*(1+4*importance)))`。
- **Active forgetting (GC)**—`store.gc_pass(floor, min_age)` 删除 strength < floor 且 access_count == 0 且 存活超过 24h 的。
- **Autobiographical narrative**—`/mem narrative <topic>` 按 entity/topic 拼出时序链, 带 valence/stream/link 标记。

调节参数(都在 `config.py`):

- `enable_separation: bool = True` — DG 全局开关
- `separation_max_links: int = 5` — similar_to 链长上限
- `temporal_bucket_seconds: int = 3600` — 时间桶尺度
- `interference_strength_drop: float = 0.05` — 主动干扰成本
- `reconsolidation_update_enabled: bool = True` — 重固化更新开关
- `replay_boost: float = 0.02` — SWR 复现强度增量

## 升级核心包

本目录的 `hippocampus/` 是发布时的快照。如要同步上游改进,直接 `Copy-Item -Recurse ../hippocampus ./hippocampus` 覆盖(注意:先备份,再覆盖)。

## v1.1 联想激活网络 + 自我模型 + 心境一致性 + 聚类摘要

v1.1 在 v1.0 的 SWR / DG / two-stream / valence 基座上,接出语义图的真正"可激活"层:

- **Spreading activation** — Collins & Loftus 1975 风格,在 entity-relation-engram 图上做深度受限的激活传播。从 `SpreadingActivation.activate(seeds, depth, decay, floor)` 起步,支持 entity 名 / engram id 作为 seed。
- **User self-model (neocortex analog)** — `ProfileStore` 是一张 `profile_facts(actor_id, predicate, value, confidence, evidence_count, source_*)` 表。从 relations 自动抽取稳定事实,evidence 与 confidence 走加权平均;手动 `service.remember_fact(...)` 可直接落盘。
- **Mood-congruent recall** — Bower 1981:`Cue.valence_hint` 非空时,engram 召回分被一个 `(1 - |hint - engram.valence|)` 的小项加权。`cfg.mood_congruence_weight` 控制强度。
- **Cluster auto-summarization (REM/dream synthesis)** — `ReplayConsolidator._refresh_cluster_summaries` 在 SWR pass 开头按 similar_to clique 聚合,LLM 优先产出 80 字内 gist,无 LLM 时回退到 top-3 summary 拼接。`cluster_id` 反写回 engram 行,方便后续模块按聚类定位。
- **recall_with_activation** — `MemoryService.recall_with_activation(cue, seeds=[...])` 把激活图作为 `Cue.activation` 传进重排,带权加到 base score。

### 调节参数 (`config.py` v1.1)

- `activation_decay: float = 0.55` — 每跳衰减乘子
- `activation_floor: float = 0.05` — 激活低于此值停止传播
- `activation_max_depth: int = 2` — 最多跳数
- `activation_score_weight: float = 0.18` — 激活在 recall 重排里的权重
- `mood_congruence_enabled: bool = True` — 心境一致性总开关
- `mood_congruence_weight: float = 0.10` — 心境匹配分加成
- `enable_cluster_summarization: bool = True` — 聚类摘要总开关
- `cluster_summary_min_size: int = 2` — 至少 N 个成员才生成 gist
- `cluster_summary_max_members: int = 8` — 喂给 LLM 的成员数上限
- `enable_profile: bool = True` — 用户自我模型总开关
- `profile_min_evidence: int = 2` — 至少被多少 engram 支撑才晋升
- `profile_min_confidence: float = 0.6` — 平均关系置信度阈值
- `profile_fact_decay_days: float = 180.0` — 事实多久没刷新就衰减

### 新增命令

| 命令 | 说明 |
|---|---|
| `/mem profile [actor]` | 查看/构建用户自我画像(没事实时会先 `build_profile`) |
| `/mem activate <seed1> [seed2 ...]` | 从 seed entity / engram 展开联想激活网络 |
| `/mem remember <predicate> <value> [actor]` | 手动写入一条画像事实 |
| `/mem cluster-list` | 列出所有已生成的聚类 gist |

### 新增模块

- `hippocampus/activation.py` — `SpreadingActivation`
- `hippocampus/profile.py` — `ProfileStore`, `ProfileFact`

### 迁移

v1.0 数据库可平滑升级:`HippocampalStore._migrate_v11` 会幂等添加 `engrams.cluster_id` / `engrams.profile_fact_id` 两列,并在 `_init_schema` 中 `CREATE TABLE IF NOT EXISTS` 出 `profile_facts` / `cluster_summaries`。

### v1.1 烟测

`astrbot_plugin/_smoke_v11.py` 覆盖 profile upsert / build / spreading activation / recall_with_activation / mood-congruence / cluster gist / profile decay / main.py helpers / v0.9+v1.0 回归。独立运行,只 mock `astrbot.api`。

```bash
PYTHONPATH=astrbot_plugin python astrbot_plugin/_smoke_v11.py
```

## v1.2 版本治理 + 导出格式校验

把"插件版本"和"导出格式版本"收敛到单一事实源,消除此前 `@register("...","0.5.0")` / export `"version":"0.7"` / `metadata.yaml: 1.1.0` 三处各说各话的隐患。

- **单一版本源** — `hippocampus/__init__.py` 新增 `__version__`(插件版本)与 `EXPORT_FORMAT_VERSION`(导出 JSON 结构版本,与插件版本解耦)。`main.py` 的 `@register` 与 `/mem export` 都引用它们,改版本只需改一处。
- **banner 带版本** — 启动 banner 现在打印 `v<__version__>`,运维一眼看到生效版本。
- **import 格式守卫** — `/mem import` 读取 payload 的 `version` 字段:与当前 `EXPORT_FORMAT_VERSION` 不一致时在结果里追加 `[warn: export format vX != current vY]`,不阻断导入;旧 dump 缺 `version` 字段则静默兼容。

> 注意:`EXPORT_FORMAT_VERSION` 只在导出 JSON 的结构真正变化时才需要 bump,跟插件版本 `__version__` 各走各的。

### v1.2 烟测

`astrbot_plugin/_smoke_v12.py` 覆盖版本单一事实源一致性(metadata / `__version__` / `@register` 三方对齐)、export 写入正确格式版本、import 往返 + 旧版本告警 + 缺字段兼容。独立运行,只 mock `astrbot.api`。

```bash
PYTHONPATH=astrbot_plugin python astrbot_plugin/_smoke_v12.py
```

## v1.2 元记忆 + 情节→语义巩固 + 遗忘曲线

v1.2 在 v1.1 的激活网络 / 自我模型 / 心境一致性基座上,补上"我对这条记忆有多确定"的元层,以及把反复出现的情节抽象成稳定语义的巩固步骤。

- **元记忆置信度 (feeling-of-knowing)** — 每条召回结果带一个 [0,1] 的置信度,由 `存储置信度 / strength / 本次检索相对分 / recency / access` 加权得到(`hippocampus/metamemory.py`)。`RecallResult.confidences` 与 `engrams` 对齐;工作记忆命中项置信度恒为 1.0。低置信 + 非零命中 = tip-of-tongue(似曾相识但说不准)。
- **情节→语义巩固 (systems consolidation)** — `ReplayConsolidator` 在每次 SWR pass 末尾,把"反复回来"的 cluster(成员数 + 总 access 达阈值)里最稳的 `(predicate, object)` 关系抽象成一条 profile fact,并把来源 engram 的 `profile_fact_id` 反写回去。cluster 通过 `similar_to` 连通分量现算,不依赖聚类摘要先跑。`step()` 返回值新增 `abstracted` 计数。
- **遗忘曲线可视化** — `/mem decaycurve [id|all]` 用与 `DecayScheduler` 完全相同的 `strength * exp(-dt/(tau*(1+4*importance)))` 公式把未来 strength 投影成 ASCII 曲线,直接把抽象的衰减参数变成肉眼可见。

### 调节参数 (`config.py` v1.2)

- `metamemory_enabled: bool = True` — 元记忆总开关;关掉后 `RecallResult.confidences` 为 None
- `metamemory_high_threshold: float = 0.66` — ≥ 此值标 high
- `metamemory_low_threshold: float = 0.33` — < 此值标 low / tip-of-tongue
- `metamemory_weights: dict` — FOK 五项权重(stored/strength/retrieval/recency/access)
- `enable_episodic_semantic: bool = True` — 情节→语义巩固总开关
- `consolidation_cluster_min_members: int = 3` — cluster 至少 N 个成员才抽象
- `consolidation_cluster_min_access: int = 2` — 且总 access_count ≥ N(确实反复回来)
- `consolidation_fact_confidence: float = 0.7` — 巩固铸出事实的置信度下限
- `decaycurve_buckets: int = 12` / `decaycurve_width: int = 32` — 曲线采样点 / 条宽

### 新增模块 / 命令

- `hippocampus/metamemory.py` — `recall_confidence` / `confidence_label` / `is_tip_of_tongue`
- `/mem confidence <query>` / `/mem decaycurve [id|all]` / `/mem consolidate`

### 迁移

`HippocampalStore._migrate_v12` 幂等添加 `engrams.confidence` 列(默认 0.5)。导出格式版本 `EXPORT_FORMAT_VERSION` 保持 `1.1` 不变:新列在 `Engram.from_row` 里走默认值,旧 dump 可直接导入。

### v1.2 烟测

`astrbot_plugin/_smoke_v13.py` 覆盖 confidence 列往返 + 迁移幂等、recall 置信度对齐、metamemory 开关、tip-of-tongue、情节→语义巩固 + 反链 + 开关、遗忘曲线渲染、main.py helpers/stats、v1.1 回归。独立运行,只 mock `astrbot.api`。

```bash
PYTHONPATH=astrbot_plugin python astrbot_plugin/_smoke_v13.py
```
