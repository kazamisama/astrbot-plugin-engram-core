# Engram Core 跨插件公开契约（Public API · v1）

本文档是 `astrbot_plugin_engram_core` 对外部 AstrBot 插件的稳定互操作契约（v1.75.0+；v1.76.0 扩展事件/短记/实体图）。只有本文件列出的方法属于公开 API；其余 `hippocampus` 内部对象与方法不承诺兼容。

## 获取宿主

```python
star = context.get_registered_star("astrbot_plugin_engram_core")
if star is None:
    # v2 硬依赖：宿主缺失时报 error，不做静默降级
```

公开方法全部定义在 `HippocampusStar`（以及其背后的 `MemoryService`）上，同步调用；失败时返回空值/`False` 并打印错误，不抛异常。

## `persona_id` 分区键

- `persona_id` 是全局分区键：外部插件写入与召回的记忆必须带 `persona_id`，宿主按该键隔离查询与租约。
- 外部生活事件统一使用 `channel_id="life:{persona_id}"`、`platform=来源插件名`（如 `your_own_life`）。
- 写入的日记行会带标签：`source:<插件>`、`day:<YYYY-MM-DD>`，以及可选的 `mood:` / `signature:` / `ref:<source_ref>`。

## 日记写入

### `store_diary_line(persona_id, date, content, *, mood="", signature="", source_refs=None, source="external") -> str`

稳定（v1.75.0+）。

- 把一条 bot 第一人称生活日记行持久化为 persona 分区的 `diary` 记忆（含 chunk 级嵌入，供日记召回）。
- 参数：`persona_id`（必填）、`date`（`YYYY-MM-DD`）、`content`（正文）、`mood`、`signature`、`source_refs`（`list[str]`，URL/来源引用）、`source`（来源插件标识）。
- 返回：新记忆的 `engram.id`；`persona_id` 或 `content` 为空、写入失败时返回 `""`。

```python
eid = star.store_diary_line(
    "shelly", "2026-08-12", "今天看了一篇关于 RAG 的文章。",
    mood="curious", signature="今天的风", source_refs=["https://example.com/1"],
    source="your_own_life",
)
```

## 记忆召回

### `query_recent_memory(persona_id, query="", k=5, since=0.0) -> list[dict]`

稳定（v1.75.0+）。

- `query` 非空时走 persona 限定的语义/向量/混合召回；为空时返回该 persona 最新 engram（按 `created_at` 倒序）。
- `k` 为返回条数上限；`since` 为 Unix 时间戳下限（0 = 不过滤）。
- 返回列表元素为稳定 dict：`id / persona_id / memory_type / content / summary / tags / created_at / importance / confidence`。
- 宿主不可用时返回 `[]`，由下游按硬依赖策略报 error，不静默降级。

### `query_memory(persona_id, query, k=5, memory_types=None) -> list[dict]`

稳定（v1.76.0+）。persona 限定的语义/向量/混合召回，`memory_types` 可过滤 `episodic / semantic / note / diary / event` 等；返回结构与 `query_recent_memory` 相同。

### `search(persona_id, query, k=5, memory_types=None) -> list[dict]`

稳定（v1.76.0+）。`query_memory` 的等价别名，供下游语义化调用。

## 事件与短记写入

### `store_event(persona_id, platform, session_id, ts, kind, payload=None, source="external") -> str`

稳定（v1.76.0+）。把一条生活事件（`kind` 如 `observe / change / think / express / recall / rollback`）持久化为 `memory_type="event"` 的 engram，payload 以 JSON 存入 content，标签含 `kind:` 与 `source:`。返回 engram id；失败返回 `""`。

### `add_note(persona_id, note, source="external") -> str`

稳定（v1.76.0+）。持久化一条生活短记（`memory_type="note"`）。`note` 支持 `summary / opinion / url / url_hash / category / tags / entities / importance`；标签含 `source:`、`url:`、`category:`、`hash:`。返回 engram id；失败返回 `""`。

## 实体图（分层维度模型）

实体与边按 `persona_id` 分区，`dimension` 取 `platform / url / person / project / community / topic`；`platform/url` 节点只能由系统写入，`same_as` 为身份合并候选（下游 owner 确认后生效）。

### `upsert_entity(persona_id, entity) -> str`

稳定（v1.76.0+）。`entity` 支持 `dimension / entity_id / name / canonical_url`；同维度同 `entity_id` 幂等累加 `seen_count`。返回实体行 id；参数缺失返回 `""`。

### `link_entities(persona_id, src_entity_id, relation, dst_entity_id, weight=1.0) -> bool`

稳定（v1.76.0+）。写入一条有向类型边（关系词表见下游 `docs/features.md` L2-02）；首次创建返回 `True`，重复出现更新权重与 `seen_count` 并返回 `False`。

### `list_entities(persona_id, limit=500) -> list[dict]` / `list_links(persona_id, limit=1000) -> list[dict]`

稳定（v1.76.0+）。只读列出 persona 的实体与边，供 WebUI 实体图与“我在哪见过 X”查询。

## 任务租约（多实例单写者）

租约存储在与记忆同一个 `hippocampus.db`，共享 SQLite 的多个实例可见同一张 `task_leases` 表。TTL 过期后可被立即重新认领。

### `claim_task(persona_id, task_kind, holder="", ttl_seconds=300) -> bool`

稳定（v1.75.0+）。认领 `(persona_id, task_kind)` 租约；成功返回 `True`，已被其他 holder 持有且未过期返回 `False`。`holder` 为空时默认 `engram:{persona_id}`。

### `renew_task(persona_id, task_kind, holder="", ttl_seconds=300) -> bool`

稳定（v1.75.0+）。仅当前 holder 可续期；不是 holder 返回 `False`。

### `release_task(persona_id, task_kind, holder="") -> bool`

稳定（v1.75.0+）。仅当前 holder 可释放；释放成功返回 `True`。

### `task_lease_owner(persona_id, task_kind) -> str`

稳定（v1.75.0+）。返回当前 holder（空串 = 空闲）。

```python
if not star.claim_task("shelly", "diary", holder="instance-a", ttl_seconds=300):
    # 另一实例持有租约：跳过并记录 skipped_duplicate
```

## 与既有协调协议的边界

- `engram_core_helpers.py`（`extra_user_content_parts` 注入协调，根标签 `<engram-context>`）仍是 v1 既有公开面，本文档不覆盖其细节，见 `README.md`。
- 未列出的 `hippocampus` 内部类（`MemoryService` 上的 `observe / recall / store_diary` 等）虽然存在，但不属于稳定跨插件契约；下游一律经本文件列出的方法调用。

## 契约版本策略

- 公开 API 以本文档为准，契约版本为 `Public API · v1`；方法级稳定性标注在各自小节。
- 新增方法 = minor bump（如 1.75.x）；破坏性变更必须先更新本文档并整族协调，再发 major 版本。
