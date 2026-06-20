"""ConfigManager: validated, fallback-aware access to MemoryConfig.

Split from PluginInitializer at v1.4.x B7. Owns:
  - per-field spec registry (type, range, choices, i18n label)
  - raw_dict -> MemoryConfig transformation with type coercion
  - per-field validation: type / range / choices violations fall back
    to the MemoryConfig default + emit a single-line warn
  - extra-field collection (fields not in _FIELDS land in MemoryConfig.extra)

Inspired by astrbot_plugin_livingmemory's ConfigManager (pydantic-style),
but adapted to hippocampus's dataclass schema (no pydantic dependency).
i18n labels (zh + en) are pre-extracted so B8 can swap them for t("...").
"""
from __future__ import annotations
import dataclasses
from typing import Any
from .config import MemoryConfig


@dataclasses.dataclass(frozen=True)
class _FieldSpec:
    """Per-field metadata for validation + i18n."""
    py_type: type  # int | float | bool | str | list | dict
    range: tuple[float, float] | None = None  # (min, max) for numeric
    choices: tuple | None = None  # allowed values for str enums
    label_zh: str = ""
    label_en: str = ""


# 67-field registry. Mirrors MemoryConfig exactly. If you add a field
# to MemoryConfig, add a matching _FieldSpec here.
_FIELDS: dict[str, _FieldSpec] = {
    # core
    "sqlite_path": _FieldSpec(str, label_zh="SQLite 存储路径", label_en="SQLite storage path"),
    "embedding_dim": _FieldSpec(int, (16, 4096), label_zh="向量维度", label_en="Embedding dimension"),
    "working_memory_capacity": _FieldSpec(int, (1, 10000), label_zh="工作记忆容量", label_en="Working memory capacity"),
    "pattern_separation_threshold": _FieldSpec(float, (0.0, 1.0), label_zh="模式分离阈值", label_en="Pattern separation threshold"),
    "pattern_similar_threshold": _FieldSpec(float, (0.0, 1.0), label_zh="相似判定阈值", label_en="Pattern similar threshold"),
    "recall_candidate_k": _FieldSpec(int, (1, 10000), label_zh="召回候选数", label_en="Recall candidate k"),
    "reconsolidation_lock_seconds": _FieldSpec(float, (0.0, 86400.0), label_zh="再固化锁窗口", label_en="Reconsolidation lock seconds"),
    "decay_tau_base": _FieldSpec(float, (1.0, 86400.0 * 365), label_zh="基础衰减 tau", label_en="Decay tau base (s)"),
    "decay_floor": _FieldSpec(float, (0.0, 1.0), label_zh="强度下限", label_en="Decay floor"),
    "consolidation_interval_seconds": _FieldSpec(float, (0.0, 86400.0), label_zh="巩固间隔", label_en="Consolidation interval (s)"),
    "consolidation_max_pairs": _FieldSpec(int, (1, 100000), label_zh="巩固合并对数上限", label_en="Consolidation max pairs"),
    "importance_floor_for_long_term": _FieldSpec(float, (0.0, 1.0), label_zh="长期记忆重要性下限", label_en="Importance floor for long term"),
    # v0.2
    "enable_semantic": _FieldSpec(bool, label_zh="启用语义检索", label_en="Enable semantic"),
    "enable_prospective": _FieldSpec(bool, label_zh="启用前瞻触发", label_en="Enable prospective"),
    # v1.4 session filter
    "enable_session_filter": _FieldSpec(bool, label_zh="启用会话过滤", label_en="Enable session filter"),
    "platform_allowlist": _FieldSpec(list, label_zh="平台白名单", label_en="Platform allowlist"),
    "platform_blocklist": _FieldSpec(list, label_zh="平台黑名单", label_en="Platform blocklist"),
    "channel_allowlist": _FieldSpec(list, label_zh="频道白名单", label_en="Channel allowlist"),
    "channel_blocklist": _FieldSpec(list, label_zh="频道黑名单", label_en="Channel blocklist"),
    "blocked_keywords": _FieldSpec(list, label_zh="屏蔽关键词", label_en="Blocked keywords"),
    "actor_allowlist": _FieldSpec(list, label_zh="用户白名单", label_en="Actor allowlist"),
    "enable_promotion": _FieldSpec(bool, label_zh="启用晋升", label_en="Enable promotion"),
    "promote_min_access": _FieldSpec(int, (0, 1000), label_zh="晋升最小访问数", label_en="Promote min access"),
    "promote_min_importance": _FieldSpec(float, (0.0, 1.0), label_zh="晋升最小重要性", label_en="Promote min importance"),
    "prospective_check_interval": _FieldSpec(float, (0.0, 86400.0), label_zh="前瞻检查间隔", label_en="Prospective check interval"),
    # v0.3
    "embedding_name": _FieldSpec(str, label_zh="Embedding provider", label_en="Embedding provider"),
    "llm_name": _FieldSpec(str, label_zh="LLM provider", label_en="LLM provider"),
    "embedding_provider_id": _FieldSpec(str, label_zh="AstrBot embedding Provider ID", label_en="AstrBot embedding provider id"),
    "llm_provider_id": _FieldSpec(str, label_zh="AstrBot LLM Provider ID", label_en="AstrBot LLM provider id"),
    "auto_rebuild_on_switch": _FieldSpec(bool, label_zh="切换 embedding 时自动重建", label_en="Auto rebuild on switch"),
    "rebuild_batch_size": _FieldSpec(int, (1, 10000), label_zh="重建批大小", label_en="Rebuild batch size"),
    "tokenizer_mode": _FieldSpec(str, choices=("char", "bigram", "jieba"), label_zh="FTS 分词模式", label_en="FTS tokenizer mode"),
    "dedup_enabled": _FieldSpec(bool, label_zh="启用写入去重", label_en="Enable write dedup"),
    "dedup_threshold": _FieldSpec(float, (0.0, 1.0), label_zh="去重 Jaccard 阈值", label_en="Dedup Jaccard threshold"),
    "dedup_candidate_k": _FieldSpec(int, (1, 1000), label_zh="去重候选数", label_en="Dedup candidate k"),
    "tiering_enabled": _FieldSpec(bool, label_zh="启用记忆分层(热/温/冷)", label_en="Enable memory tiering"),
    "tier_hot_max_age_days": _FieldSpec(float, (0.0, 3650.0), label_zh="热层最大天龄", label_en="Hot tier max age days"),
    "tier_hot_min_strength": _FieldSpec(float, (0.0, 1.0), label_zh="热层最小强度", label_en="Hot tier min strength"),
    "tier_warm_max_age_days": _FieldSpec(float, (0.0, 3650.0), label_zh="温层最大天龄", label_en="Warm tier max age days"),
    "tier_cold_strength_floor": _FieldSpec(float, (0.0, 1.0), label_zh="冷层强度下限", label_en="Cold tier strength floor"),
    "tier_recall_include_cold": _FieldSpec(bool, label_zh="召回总是含冷层", label_en="Always recall cold tier"),
    "tier_cold_fallback_min_hits": _FieldSpec(int, (0, 1000), label_zh="冷层兑底阈值", label_en="Cold fallback min hits"),
    "tier_maintenance_interval_seconds": _FieldSpec(float, (0.0, 86400.0), label_zh="分层重算周期秒", label_en="Tier maintenance interval seconds"),
    "cold_archive_path": _FieldSpec(str, label_zh="冷层归档文件路径", label_en="Cold archive path"),
    "cold_archive_min_age_days": _FieldSpec(float, (0.0, 3650.0), label_zh="冷层归档最小天龄", label_en="Cold archive min age days"),
    # v0.9
    "enable_separation": _FieldSpec(bool, label_zh="启用 DG 模式分离", label_en="Enable DG separation"),
    "separation_max_links": _FieldSpec(int, (0, 100), label_zh="分离链长度上限", label_en="Separation max links"),
    # v1.0
    "temporal_bucket_seconds": _FieldSpec(int, (60, 86400 * 30), label_zh="时间桶秒数", label_en="Temporal bucket seconds"),
    "interference_strength_drop": _FieldSpec(float, (0.0, 1.0), label_zh="干扰强度惩罚", label_en="Interference strength drop"),
    "reconsolidation_update_enabled": _FieldSpec(bool, label_zh="启用再固化更新", label_en="Reconsolidation update enabled"),
    "replay_boost": _FieldSpec(float, (0.0, 1.0), label_zh="SWR 重放加成", label_en="SWR replay boost"),
    # v1.1
    "activation_decay": _FieldSpec(float, (0.0, 1.0), label_zh="激活衰减率", label_en="Activation decay"),
    "activation_floor": _FieldSpec(float, (0.0, 1.0), label_zh="激活扩散下限", label_en="Activation floor"),
    "activation_max_depth": _FieldSpec(int, (0, 10), label_zh="激活最大深度", label_en="Activation max depth"),
    "activation_score_weight": _FieldSpec(float, (0.0, 10.0), label_zh="激活分权重", label_en="Activation score weight"),
    "frequency_recall_weight": _FieldSpec(float, (0.0, 10.0), label_zh="频次召回权重", label_en="Frequency recall weight"),
    "mood_congruence_enabled": _FieldSpec(bool, label_zh="启用心境一致性", label_en="Mood congruence enabled"),
    "mood_congruence_weight": _FieldSpec(float, (0.0, 10.0), label_zh="心境权重", label_en="Mood congruence weight"),
    "enable_cluster_summarization": _FieldSpec(bool, label_zh="启用聚类摘要", label_en="Enable cluster summarization"),
    "cluster_summary_min_size": _FieldSpec(int, (1, 1000), label_zh="聚类最小成员", label_en="Cluster summary min size"),
    "cluster_summary_max_members": _FieldSpec(int, (1, 1000), label_zh="聚类最大成员", label_en="Cluster summary max members"),
    "enable_profile": _FieldSpec(bool, label_zh="启用用户模型", label_en="Enable profile"),
    "profile_min_evidence": _FieldSpec(int, (1, 1000), label_zh="用户事实最小证据", label_en="Profile min evidence"),
    "profile_min_confidence": _FieldSpec(float, (0.0, 1.0), label_zh="用户事实最小置信", label_en="Profile min confidence"),
    "profile_fact_decay_days": _FieldSpec(float, (0.0, 86400.0), label_zh="用户事实衰减天数", label_en="Profile fact decay days"),
    # v1.2
    "metamemory_enabled": _FieldSpec(bool, label_zh="启用元记忆", label_en="Metamemory enabled"),
    "metamemory_high_threshold": _FieldSpec(float, (0.0, 1.0), label_zh="元记忆高置信阈值", label_en="Metamemory high threshold"),
    "metamemory_low_threshold": _FieldSpec(float, (0.0, 1.0), label_zh="元记忆低置信阈值", label_en="Metamemory low threshold"),
    "metamemory_weights": _FieldSpec(dict, label_zh="元记忆权重", label_en="Metamemory weights"),
    "enable_episodic_semantic": _FieldSpec(bool, label_zh="启用情节→语义巩固", label_en="Enable episodic semantic"),
    "consolidation_cluster_min_members": _FieldSpec(int, (1, 1000), label_zh="巩固聚类最小成员", label_en="Consolidation cluster min members"),
    "consolidation_cluster_min_access": _FieldSpec(int, (1, 10000), label_zh="巩固聚类最小访问", label_en="Consolidation cluster min access"),
    "consolidation_fact_confidence": _FieldSpec(float, (0.0, 1.0), label_zh="巩固事实置信", label_en="Consolidation fact confidence"),
    "decaycurve_buckets": _FieldSpec(int, (1, 200), label_zh="衰减曲线采样数", label_en="Decay curve buckets"),
    "decaycurve_width": _FieldSpec(int, (8, 200), label_zh="衰减曲线宽度", label_en="Decay curve width"),
    "extra": _FieldSpec(dict, label_zh="扩展字段", label_en="Extra fields"),
    # v1.4 B3
    "enable_atom_extraction": _FieldSpec(bool, label_zh="启用 atom 抽取", label_en="Enable atom extraction"),
    "enable_graph_indexing": _FieldSpec(bool, label_zh="启用图索引", label_en="Enable graph indexing"),
    "atom_decay_interval_seconds": _FieldSpec(float, (0.0, 86400.0), label_zh="atom 衰减间隔", label_en="Atom decay interval (s)"),
    "atom_gc_interval_seconds": _FieldSpec(float, (0.0, 86400.0), label_zh="atom gc 间隔", label_en="Atom gc interval (s)"),
    # --- v1.4.x B10: backup + migration ---
    "enable_backup": _FieldSpec(bool, label_zh="启用自动备份", label_en="Enable automatic backup"),
    "backup_interval_hours": _FieldSpec(float, (0.0, 8760.0), label_zh="备份间隔 (小时)", label_en="Backup interval (hours)"),
    "backup_keep_last": _FieldSpec(int, (0, 365), label_zh="保留最近 N 份", label_en="Keep last N backups"),
    "backup_keep_weekly": _FieldSpec(int, (0, 52), label_zh="每周保留 N 份", label_en="Keep weekly N"),
    "backup_keep_monthly": _FieldSpec(int, (0, 60), label_zh="每月保留 N 份", label_en="Keep monthly N"),
    # v1.5 auto injection
    "auto_inject_enabled": _FieldSpec(bool, label_zh="启用记忆自动注入", label_en="Auto inject enabled"),
    "auto_inject_top_k": _FieldSpec(int, (0, 50), label_zh="自动注入条数", label_en="Auto inject top k"),
    "auto_inject_position": _FieldSpec(str, label_zh="自动注入位置", label_en="Auto inject position"),
    "auto_inject_relative_time": _FieldSpec(bool, label_zh="注入相对时间", label_en="Inject relative time label"),
    # v1.6 session aggregation
    "session_aggregate_enabled": _FieldSpec(bool, label_zh="启用会话聚合", label_en="Session aggregate enabled"),
    "session_aggregate_max_messages": _FieldSpec(int, (1, 100), label_zh="会话聚合最大条数", label_en="Session aggregate max messages"),
    "session_aggregate_idle_seconds": _FieldSpec(float, (0.0, 86400.0), label_zh="会话聚合静默秒数", label_en="Session aggregate idle seconds"),
    "session_aggregate_min_chars": _FieldSpec(int, (1, 1000), label_zh="会话聚合最小字数", label_en="Session aggregate min chars"),
    # v1.17 B-1 conversation summarization
    "summary_mode_enabled": _FieldSpec(bool, label_zh="启用总结模式", label_en="Enable summary mode"),
    "per_message_ingest_debug": _FieldSpec(bool, label_zh="逐条入库(调试)", label_en="Per-message ingest (debug)"),
    "summary_idle_seconds_private": _FieldSpec(float, (0.0, 86400.0), label_zh="私聊冷却秒数", label_en="Private idle seconds"),
    "summary_idle_seconds_group": _FieldSpec(float, (0.0, 86400.0), label_zh="群聊冷却秒数", label_en="Group idle seconds"),
    "summary_max_messages": _FieldSpec(int, (0, 1000), label_zh="总结最大消息数", label_en="Summary max messages"),
    "summary_min_chars": _FieldSpec(int, (0, 1000), label_zh="总结最小字数", label_en="Summary min chars"),
    "summary_compress_ratio": _FieldSpec(float, (0.0, 1.0), label_zh="总结压缩比", label_en="Summary compress ratio"),
    "summary_compress_floor": _FieldSpec(int, (0, 5000), label_zh="总结字数下限", label_en="Summary compress floor"),
    "summary_compress_cap": _FieldSpec(int, (0, 5000), label_zh="总结字数上限", label_en="Summary compress cap"),
    "summary_idle_flush_interval_seconds": _FieldSpec(float, (5.0, 3600.0), label_zh="空闲扫描周期秒", label_en="Idle flush interval seconds"),
    # v1.19 B-2 relation layer
    "relation_supersede_hysteresis": _FieldSpec(float, (0.0, 1.0), label_zh="关系覆盖迟滞", label_en="Relation supersede hysteresis"),
    "relation_inject_top_n": _FieldSpec(int, (0, 50), label_zh="关系注入条数", label_en="Relation inject top n"),
    "relation_inject_min_confidence": _FieldSpec(float, (0.0, 1.0), label_zh="关系注入最低置信", label_en="Relation inject min confidence"),
    # v1.8 persona
    "enable_persona": _FieldSpec(bool, label_zh="启用用户画像", label_en="Enable persona"),
    "persona_inject_enabled": _FieldSpec(bool, label_zh="启用画像注入", label_en="Persona inject enabled"),
    "persona_max_source": _FieldSpec(int, (1, 1000), label_zh="画像取样条数", label_en="Persona max source"),
}

# Public i18n label dict: field name -> {zh, en}. Stable API for B8
# to consume before B8 introduces t("config.field_name").
LABELS: dict[str, dict[str, str]] = {
    name: {"zh": spec.label_zh, "en": spec.label_en}
    for name, spec in _FIELDS.items()
}


class ConfigManager:
    """Validated view over a raw user config dict.

    Usage:
        cm = ConfigManager(raw_dict_from_astrbot)
        cfg = cm.memory_config  # MemoryConfig, fully populated
    """

    # Group keys in _conf_schema.json that AstrBot renders as nested
    # `type: object` dicts. Their inner keys are hoisted to the top
    # level so the flat _FIELDS registry can validate them.
    _GROUP_KEYS = (
        "provider_settings",
        "storage_settings",
        "memory_settings",
        "backup_settings",
    )

    def __init__(self, raw: dict | None = None) -> None:
        self._raw: dict = self._flatten(raw or {})
        self._memory_config: MemoryConfig | None = None
        self._build()

    @classmethod
    def _flatten(cls, raw: dict) -> dict:
        """Flatten the grouped _conf_schema.json shape into a flat dict.

        Only the known group keys are unwrapped; every other top-level
        key (legacy flat configs, dict-valued fields like
        metamemory_weights / extra) passes through untouched. Top-level
        keys win over nested keys on collision.
        """
        flat: dict = {}
        for k, v in raw.items():
            if k in cls._GROUP_KEYS and isinstance(v, dict):
                for nk, nv in v.items():
                    flat.setdefault(nk, nv)
        for k, v in raw.items():
            if not (k in cls._GROUP_KEYS and isinstance(v, dict)):
                flat[k] = v
        return flat

    def _build(self) -> None:
        # 1. Collect values per-field with fallback + validation
        kwargs: dict[str, Any] = {}
        for fname, spec in _FIELDS.items():
            default = getattr(MemoryConfig(), fname, None)
            if fname not in self._raw:
                # Missing field -> use MemoryConfig default, no warn
                # (this is the normal "user didn't set it" case)
                kwargs[fname] = default
                continue
            value = self._raw[fname]
            kwargs[fname] = self._coerce(fname, value, default, spec)
        # 2. Collect extras: fields in _raw but not in _FIELDS
        known = set(_FIELDS)
        extras = {k: v for k, v in self._raw.items() if k not in known}
        if extras:
            # Merge with any extras already in MemoryConfig default
            base_extra = kwargs.get("extra") or {}
            if not isinstance(base_extra, dict):
                base_extra = {}
            kwargs["extra"] = {**base_extra, **extras}
        # 3. Construct. If MemoryConfig itself rejects something (extra
        # unknown kwarg), fall back to default + warn.
        try:
            self._memory_config = MemoryConfig(**kwargs)
        except TypeError as e:
            print(f"[hippocampus] MemoryConfig(**kwargs) failed: {e!r}, "
                  f"falling back to default MemoryConfig")
            self._memory_config = MemoryConfig()

    def _coerce(self, fname: str, value: Any, default: Any,
                spec: _FieldSpec) -> Any:
        """Type-coerce / range-check / choices-check. On any failure,
        return `default` and emit a single-line warn."""
        if value is None:
            return default
        # Type coercion
        try:
            if spec.py_type is bool:
                if isinstance(value, bool):
                    coerced = value
                elif isinstance(value, (int, float)):
                    coerced = bool(value)
                elif isinstance(value, str):
                    if value.lower() in ("true", "1", "yes", "on"):
                        coerced = True
                    elif value.lower() in ("false", "0", "no", "off"):
                        coerced = False
                    else:
                        raise ValueError(f"cannot parse bool from {value!r}")
                else:
                    raise ValueError(f"unsupported type for bool: {type(value).__name__}")
            elif spec.py_type is int:
                coerced = int(value)
            elif spec.py_type is float:
                coerced = float(value)
            elif spec.py_type is str:
                coerced = str(value)
            elif spec.py_type is list:
                if isinstance(value, list):
                    coerced = list(value)
                else:
                    raise ValueError(f"expected list, got {type(value).__name__}")
            elif spec.py_type is dict:
                if isinstance(value, dict):
                    coerced = dict(value)
                else:
                    raise ValueError(f"expected dict, got {type(value).__name__}")
            else:
                coerced = value
        except (ValueError, TypeError) as e:
            print(f"[hippocampus] config field {fname!r} type coercion "
                  f"failed: {value!r} -> {spec.py_type.__name__} ({e!r}), "
                  f"using default {default!r}")
            return default
        # Range check (numeric)
        if spec.range is not None and isinstance(coerced, (int, float)) \
                and not isinstance(coerced, bool):
            lo, hi = spec.range
            if coerced < lo or coerced > hi:
                print(f"[hippocampus] config field {fname!r} value {coerced!r} "
                      f"out of range [{lo}, {hi}], using default {default!r}")
                return default
        # Choices check (str enums)
        if spec.choices is not None and coerced not in spec.choices:
            print(f"[hippocampus] config field {fname!r} value {coerced!r} "
                  f"not in choices {spec.choices}, using default {default!r}")
            return default
        return coerced

    @property
    def memory_config(self) -> MemoryConfig:
        return self._memory_config

    def get(self, key: str, default: Any = None) -> Any:
        """Dot-path get over the underlying MemoryConfig.

        Only flat keys are supported (matches _FIELDS structure); nested
        paths like 'a.b' are not yet meaningful for MemoryConfig.
        """
        cfg = self._memory_config
        if cfg is None:
            return default
        return getattr(cfg, key, default)
