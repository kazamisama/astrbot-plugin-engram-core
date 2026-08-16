"""LLM memory consolidation (livingmemory-inspired, v1.76.5).

Groups low-importance, sufficiently-old active memories and asks the LLM to
merge each group into one denser memory. Originals are soft-forgotten
(archive) or hard-deleted according to config.
"""
from __future__ import annotations
import time
from typing import Any

from .storage import _cos


class MemoryConsolidationManager:
    def __init__(self, service) -> None:
        self.service = service
        self._last_run_at = 0.0
        self.min_interval_seconds = 6 * 3600.0

    def run(self, *, force: bool = False) -> dict[str, Any]:
        svc = self.service
        cfg = svc.cfg
        if not bool(getattr(cfg, "memory_consolidation_enabled", False)):
            return {"skipped": True, "reason": "disabled"}
        now = time.time()
        if not force and now - self._last_run_at < self.min_interval_seconds:
            return {"skipped": True, "reason": "cooldown"}
        self._last_run_at = now

        max_importance = float(getattr(cfg, "memory_consolidation_max_importance", 0.5) or 0.0)
        min_age_days = int(getattr(cfg, "memory_consolidation_min_age_days", 7) or 0)
        cutoff = now - min_age_days * 86400.0
        candidates = []
        for e in svc.store.all(limit=10_000_000):
            if float(getattr(e, "forgotten_at", 0.0) or 0.0) > 0.0:
                continue
            if float(getattr(e, "importance", 0.5) or 0.5) >= max_importance:
                continue
            if float(getattr(e, "created_at", now) or now) >= cutoff:
                continue
            if (getattr(e, "summary", "") or "").strip():
                candidates.append(e)
        if not candidates:
            return {"candidates": 0, "groups": 0, "merged": 0, "archived": 0, "failed": 0}

        granularity = str(getattr(cfg, "memory_consolidation_granularity", "session") or "session")
        max_candidates = int(getattr(cfg, "memory_consolidation_max_candidates", 1000) or 0)
        if max_candidates > 0 and len(candidates) > max_candidates:
            candidates = candidates[:max_candidates]
        groups = self._group_candidates(candidates, granularity)
        min_per = int(getattr(cfg, "memory_consolidation_min_memories_per_group", 3) or 2)
        groups = [g for g in groups if len(g) >= min_per]
        groups.sort(key=len, reverse=True)
        max_groups = int(getattr(cfg, "memory_consolidation_max_groups", 5) or 0)
        keep_original = str(getattr(cfg, "memory_consolidation_keep_original", "archive") or "archive")
        stats = {"candidates": len(candidates), "groups": 0, "merged": 0,
                 "archived": 0, "deleted": 0, "failed": 0}
        for group in groups[:max_groups]:
            try:
                merged = self._merge_group(group)
                if not merged:
                    stats["failed"] += 1
                    continue
                identity = {
                    "session_id": getattr(group[0], "session_id", "") or "",
                    "actor_id": getattr(group[0], "actor_id", "") or "",
                    "platform": getattr(group[0], "platform", "") or "",
                    "channel_id": getattr(group[0], "channel_id", "") or "",
                    "persona_id": getattr(group[0], "persona_id", "") or "",
                    "scope_id": getattr(group[0], "scope_id", "") or "",
                    "memory_type": "episodic",
                }
                merged["topics"] = list(dict.fromkeys(
                    [t for e in group for t in (getattr(e, "topics", None) or [])][:8]))
                new_e = svc.store_summary(merged, identity)
                if new_e is None:
                    # Never archive/delete originals when the merged write failed.
                    stats["failed"] += 1
                    continue
                removed_ids = [e.id for e in group]
                if keep_original == "archive":
                    for eid in removed_ids:
                        if svc.store.soft_forget(eid):
                            stats["archived"] += 1
                else:
                    for eid in removed_ids:
                        if svc.store.delete(eid):
                            stats["deleted"] += 1
                stats["groups"] += 1
                stats["merged"] += len(group)
                svc._invalidate_search_cache()
            except Exception as exc:
                stats["failed"] += 1
                print("[hippocampus] memory consolidation group failed: " + repr(exc))
        return stats

    def _group_candidates(self, candidates: list, granularity: str) -> list[list]:
        if granularity == "semantic":
            return self._group_semantic(candidates)
        grouped: dict[tuple, list] = {}
        for e in candidates:
            key = (getattr(e, "session_id", "") or "",
                   getattr(e, "persona_id", "") or "",
                   getattr(e, "scope_id", "") or "")
            grouped.setdefault(key, []).append(e)
        return list(grouped.values())

    def _group_semantic(self, candidates: list, threshold: float = 0.72) -> list[list]:
        """Union-find connected components over embedding similarity."""
        n = len(candidates)
        if n < 2:
            return [[e] for e in candidates]
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        for i in range(n):
            for j in range(i + 1, n):
                ci, cj = candidates[i], candidates[j]
                # Never bridge persona / scope partitions during semantic
                # consolidation.
                if ((getattr(ci, "persona_id", "") or ""),
                        (getattr(ci, "scope_id", "") or "")) != (
                        (getattr(cj, "persona_id", "") or ""),
                        (getattr(cj, "scope_id", "") or "")):
                    continue
                a = ci.embedding or []
                b = cj.embedding or []
                if a and b and len(a) == len(b) and _cos(a, b) >= threshold:
                    union(i, j)
        groups: dict[int, list] = {}
        for idx, e in enumerate(candidates):
            groups.setdefault(find(idx), []).append(e)
        return list(groups.values())

    def _merge_group(self, group: list) -> dict | None:
        from .llm import RuleLLMProvider
        if isinstance(self.service.llm, RuleLLMProvider):
            return None
        lines = "\n".join(f"- {e.summary}" for e in group)
        from .prompts import get_prompt
        _pn = getattr(self.service.cfg, "_prompt_namespace", None)
        system = get_prompt("consolidation_system", namespace=_pn)
        user = get_prompt("consolidation_user", namespace=_pn).replace("{lines}", lines)
        try:
            raw = self.service.llm.chat(system, user, temperature=0.2, max_tokens=1200)
        except Exception as exc:
            print("[hippocampus] consolidation llm error: " + repr(exc))
            return None
        if not raw:
            return None
        import json as _json
        import re as _re
        try:
            data = _json.loads(raw)
        except Exception:
            m = _re.search(r"\{.*\}", raw, _re.S)
            if not m:
                return None
            try:
                data = _json.loads(m.group(0))
            except Exception:
                return None
        if not isinstance(data, dict) or not (data.get("summary") or "").strip():
            return None
        facts = data.get("key_facts") or []
        if not isinstance(facts, list):
            facts = [facts]
        return {
            "summary": str(data.get("summary", "")).strip(),
            "key_facts": [str(x).strip() for x in facts if str(x).strip()],
            "topics": data.get("topics") or [],
            "participants": [],
            "relations": data.get("relations") or [],
            "importance": min(1.0, max(0.0, float(data.get("importance", 0.5) or 0.5))),
        }
