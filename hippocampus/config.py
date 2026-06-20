from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class MemoryConfig:
    sqlite_path: str = "data/hippocampus.db"
    embedding_dim: int = 64
    working_memory_capacity: int = 32
    pattern_separation_threshold: float = 0.92
    pattern_similar_threshold: float = 0.75
    recall_candidate_k: int = 50
    reconsolidation_lock_seconds: float = 30.0
    decay_tau_base: float = 60 * 60 * 24 * 7.0
    decay_floor: float = 0.05
    consolidation_interval_seconds: float = 60.0
    consolidation_max_pairs: int = 200
    importance_floor_for_long_term: float = 0.3
    # --- v0.2: 语义/前瞻/升级 ---
    enable_semantic: bool = True
    enable_prospective: bool = True
    # v1.4: session filter (B2). All filters are no-ops when disabled.
    # If enable_session_filter is False, every message is captured (legacy behaviour).
    enable_session_filter: bool = False
    # Only capture messages from these platforms; empty = all platforms
    platform_allowlist: list[str] = field(default_factory=list)
    # Block these platforms entirely (overrides allowlist)
    platform_blocklist: list[str] = field(default_factory=list)
    # Only capture messages from these channel_ids (groups/chats); empty = all
    channel_allowlist: list[str] = field(default_factory=list)
    # Block these channel_ids (overrides allowlist)
    channel_blocklist: list[str] = field(default_factory=list)
    # Block messages whose content contains any of these substrings (case-insensitive)
    blocked_keywords: list[str] = field(default_factory=list)
    # Only capture messages from these actor_ids; empty = all actors
    actor_allowlist: list[str] = field(default_factory=list)
    enable_promotion: bool = True
    promote_min_access: int = 3
    promote_min_importance: float = 0.5
    prospective_check_interval: float = 5.0
    # --- v0.3: 用户可切换模型 ---
    embedding_name: str = "hash"
    llm_name: str = "rule"
    # AstrBot provider IDs（留空=用 AstrBot 当前默认 provider；走 astrmock 桥）
    embedding_provider_id: str = ""
    llm_provider_id: str = ""
    auto_rebuild_on_switch: bool = True
    rebuild_batch_size: int = 50
    # --- v1.10: configurable FTS tokenizer ---
    tokenizer_mode: str = "jieba"  # char | bigram | jieba
    # --- v1.11: text-layer near-duplicate dedup (memori-inspired) ---
    dedup_enabled: bool = True
    dedup_threshold: float = 0.9  # Jaccard >= this => near-duplicate
    dedup_candidate_k: int = 10   # FTS candidates to Jaccard-check
    # --- v0.9: 模式分离 (DG) ---
    enable_separation: bool = True
    separation_max_links: int = 5  # per engram,双向 similar_to 链总长度上限
    # --- v1.0: temporal context ---
    temporal_bucket_seconds: int = 3600  # 1 hour buckets; 60 = minutes, 86400 = days
    # --- v1.0: proactive interference cost ---
    interference_strength_drop: float = 0.05  # strength penalty when a similar engram links/merges
    # --- v1.0: reconsolidation update ---
    reconsolidation_update_enabled: bool = True  # allow new observations to update recalled engrams in lock window
    # --- v1.0: SWR replay ---
    replay_boost: float = 0.02  # strength boost per replay pass for high-strength items

    # --- v1.1: spreading activation over entity-relation-engram graph ---
    activation_decay: float = 0.55  # per-hop multiplicative decay of activation
    activation_floor: float = 0.05  # stop spreading below this activation
    activation_max_depth: int = 2   # how many hops through the semantic graph
    activation_score_weight: float = 0.18  # weight of activation in recall rerank
    # --- v1.1: mood-congruent recall (Bower 1981) ---
    mood_congruence_enabled: bool = True
    mood_congruence_weight: float = 0.10  # bonus when |cue_valence - engram_valence| is small
    # --- v1.1: cluster auto-summarization (REM / dream synthesis step) ---
    enable_cluster_summarization: bool = True
    cluster_summary_min_size: int = 2   # need at least N siblings to bother summarizing
    cluster_summary_max_members: int = 8  # cap how many we feed into the summarizer
    # --- v1.1: user self-model (neocortex analog) ---
    enable_profile: bool = True
    profile_min_evidence: int = 2       # relation must back a fact >= N times to promote
    profile_min_confidence: float = 0.6 # and the relation confidence must be at least this
    profile_fact_decay_days: float = 180.0  # facts not refreshed in N days drop confidence

    # --- v1.2: metamemory (feeling-of-knowing / recall confidence) ---
    metamemory_enabled: bool = True
    metamemory_high_threshold: float = 0.66   # >= this -> "high" confidence label
    metamemory_low_threshold: float = 0.33    # < this  -> "low" / tip-of-tongue
    metamemory_weights: dict = field(default_factory=lambda: {
        "stored": 0.20, "strength": 0.30, "retrieval": 0.30,
        "recency": 0.10, "access": 0.10,
    })
    # --- v1.2: episodic -> semantic consolidation (cluster abstraction) ---
    enable_episodic_semantic: bool = True
    consolidation_cluster_min_members: int = 3   # cluster must have >= N members to abstract
    consolidation_cluster_min_access: int = 2    # and total access_count >= N (it kept coming back)
    consolidation_fact_confidence: float = 0.7   # confidence of facts minted by consolidation
    # --- v1.2: forgetting-curve visualization ---
    decaycurve_buckets: int = 12   # how many time points to sample on the curve
    decaycurve_width: int = 32     # ASCII bar width
    extra: dict = field(default_factory=dict)

    # --- v1.4 B3: atom extraction (per-Engram MemoryAtom upsert) ---
    enable_atom_extraction: bool = True
    # --- v1.4 B4: graph fast-path (mirror entity_refs into GraphStore) ---
    enable_graph_indexing: bool = True
    # --- v1.4 B3: background maintenance loops ---
    # 0 = disabled. Caller is expected to call run_decay() / run_gc()
    # manually, or to invoke MemoryService.start_background_tasks().
    atom_decay_interval_seconds: float = 0.0
    atom_gc_interval_seconds: float = 0.0
    # --- v1.4.x B10: backup + migration ---
    enable_backup: bool = True
    backup_interval_hours: float = 24.0
    backup_keep_last: int = 7
    backup_keep_weekly: int = 1
    backup_keep_monthly: int = 1
    # --- v1.5: auto memory injection into LLM context (on_llm_request) ---
    auto_inject_enabled: bool = True
    auto_inject_top_k: int = 3
    auto_inject_position: str = "before"
    auto_inject_relative_time: bool = True  # prefix recalled memories with a zh relative-time label
    # --- v1.6: per-speaker conversation aggregation (optional) ---
    session_aggregate_enabled: bool = True
    session_aggregate_max_messages: int = 5
    session_aggregate_idle_seconds: float = 8.0
    session_aggregate_min_chars: int = 0
    # --- v1.8: natural-language user persona (narrative profile) ---
    enable_persona: bool = False
    persona_inject_enabled: bool = False
    persona_max_source: int = 20
