"""Agent-callable tools (v1.3).

LLM 主动调用 hippocampus 的入口：tool 把 recall/memorize 包装成
JSON Schema，真实 AstrBot 环境由 main.py 的 _register_agent_tools()
把 MemoryTool 转成 AstrBot 的 FunctionTool 注册到 Agent 上。

为什么独立模块：
- recall_long_term_memory / memorize_long_term_memory 是 *给 LLM 用的契约*，
  schema 和描述词要稳，不能让业务改动破坏 LLM 调用的兼容性
- 与 recall()/observe() 共享底层 service，不重复业务
- 可以在 v1.4+ 加 `forget_long_term_memory`、`summarize_topic` 等

Schema 设计原则（参考 livingmemory v2.3.5）：
- name 用 snake_case 复数（recall_long_term_memory）
- description 写 *何时调用*（用"when the user asks to..."句式）+ *怎么用*（"prefer short topic phrases"）
- parameters 必填项少，k 等可调项给默认值
- 返回值是 JSON 字符串（dict），不是 Engram 对象
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .service import MemoryService
    from .types import Engram


@dataclass
class MemoryTool:
    """A tool the LLM can call. Lightweight dataclass that maps cleanly to
    AstrBot's FunctionTool shape (name, description, parameters, run)."""
    name: str
    description: str
    parameters: dict
    handler: Callable[..., str]
    """Signature: (service, **kwargs) -> JSON string. Pure function, no I/O
    outside the service. The wrapping FunctionTool will inject the service."""

    def to_dict(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _forget_handler(service, *, engram_id: str, hard: bool = False) -> str:
    """Body of forget_long_term_memory.

    Default is soft_forget: marks the engram forgotten (forgotten_at=now,
    strength=0) but keeps the row for audit. Pass hard=True to physically
    remove the engram from the store. Returns a JSON status object.
    """
    if not (engram_id or "").strip():
        return json.dumps({"ok": False, "error": "engram_id is required"}, ensure_ascii=False)
    eid = engram_id.strip()
    try:
        e = service.store.get(eid)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "store lookup failed: " + str(exc)}, ensure_ascii=False)
    if e is None:
        return json.dumps({"ok": False, "engram_id": eid, "error": "engram not found"}, ensure_ascii=False)
    if e.forgotten_at and e.forgotten_at > 0:
        return json.dumps({
            "ok": True,
            "engram_id": eid,
            "mode": "noop",
            "note": "engram was already soft-forgotten; not removed again",
        }, ensure_ascii=False)
    if hard:
        try:
            service.store.delete(eid)
        except Exception as exc:
            return json.dumps({"ok": False, "error": "hard delete failed: " + str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "engram_id": eid, "mode": "hard"}, ensure_ascii=False)
    ok = service.store.soft_forget(eid)
    return json.dumps({"ok": bool(ok), "engram_id": eid, "mode": "soft"}, ensure_ascii=False)


def _list_recent_handler(service, *, actor_id: str, k: int = 10) -> str:
    """Body of list_recent_memories.

    Lists active (not soft-forgotten) engrams for an actor, newest first.
    Filters in Python: store.list_active(limit=200) gives us a recent-enough
    pool, and we narrow by actor_id and trim to k. Adequate for tool scale;
    a SQL-level actor filter is a B11 concern.
    """
    if not (actor_id or "").strip():
        return json.dumps({"ok": False, "error": "actor_id is required"}, ensure_ascii=False)
    pool_size = max(50, int(k) * 20)
    try:
        pool = service.store.list_active(limit=pool_size)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "store list failed: " + str(exc)}, ensure_ascii=False)
    aid = actor_id.strip()
    out = []
    for e in pool:
        if e.actor_id != aid:
            continue
        out.append({
            "id": e.id,
            "summary": e.summary or e.content or "",
            "created_at": e.created_at,
            "importance": e.importance,
            "memory_type": e.memory_type,
        })
        if len(out) >= int(k):
            break
    return json.dumps({
        "actor_id": aid,
        "k": int(k),
        "count": len(out),
        "items": out,
    }, ensure_ascii=False)


def _search_by_entity_handler(service, *, entity_name: str, k: int = 10) -> str:
    """Body of search_by_entity_memory.

    Resolves the entity by name (case-insensitive, exact or LIKE), then
    walks engram.entity_refs for matches. Filters in Python from
    store.list_active(). Adequate for tool scale; SQL-level join is a
    B11 concern.
    """
    if not (entity_name or "").strip():
        return json.dumps({"ok": False, "error": "entity_name is required"}, ensure_ascii=False)
    name = entity_name.strip()
    ent = None
    try:
        ent = service.semantic.find_entity_by_name(name)
    except Exception:
        ent = None
    if ent is None:
        try:
            matches = service.semantic.search_entities(name, limit=1)
        except Exception:
            matches = []
        if not matches:
            return json.dumps({
                "ok": False,
                "entity_name": name,
                "error": "entity not found",
            }, ensure_ascii=False)
        ent = matches[0]
    eid_target = ent.id
    pool_size = max(50, int(k) * 20)
    try:
        pool = service.store.list_active(limit=pool_size)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "store list failed: " + str(exc)}, ensure_ascii=False)
    out = []
    for e in pool:
        if eid_target in (e.entity_refs or []):
            out.append({
                "id": e.id,
                "summary": e.summary or e.content or "",
                "created_at": e.created_at,
                "importance": e.importance,
                "memory_type": e.memory_type,
            })
            if len(out) >= int(k):
                break
    return json.dumps({
        "entity_name": name,
        "resolved_entity": {"id": ent.id, "name": ent.name},
        "k": int(k),
        "count": len(out),
        "items": out,
    }, ensure_ascii=False)


def _recall_handler(service, *, query: str, k: int = 5) -> str:
    """Body of recall_long_term_memory. Returns a JSON string of hits."""
    from .types import Cue
    cue = Cue(text=query, k=int(k), actor_id=None, channel_id=None)
    res = service.recall(cue)
    hits = []
    for i, (e, s) in enumerate(zip(res.engrams, res.scores)):
        hits.append({
            "id": e.id,
            "summary": e.summary or e.content or "",
            "score": round(float(s), 4),
            "created_at": e.created_at,
            "confidence": (res.confidences[i] if res.confidences else None),
        })
    return json.dumps({
        "query": query,
        "k": int(k),
        "count": len(hits),
        "hits": hits,
    }, ensure_ascii=False)


def _memorize_handler(service, *, content: str, actor_id: str = "agent", importance: float = 0.5) -> str:
    """Body of memorize_long_term_memory. Synthesizes an engram from agent text.

    Uses the same observe() pipeline so the engram gets the standard
    valence / stream / temporal_bucket treatment, but skips the session/actor
    extraction (the agent provided them).
    """
    if not (content or "").strip():
        return json.dumps({"ok": False, "error": "content is required"}, ensure_ascii=False)
    e = service.observe(
        session_id="agent",
        actor_id=actor_id or "agent",
        platform="agent",
        channel_id="agent",
        content=content,
    )
    if importance:
        try:
            e.importance = max(0.0, min(1.0, float(importance)))
            service.store.upsert(e)
        except Exception:
            pass
    return json.dumps({
        "ok": True,
        "engram_id": e.id,
        "summary": e.summary or e.content or "",
        "importance": e.importance,
        "stored_at": e.created_at,
    }, ensure_ascii=False)


def build_recall_tool() -> MemoryTool:
    return MemoryTool(
        name="recall_long_term_memory",
        description=(
            "Recall long-term memory when the current context is insufficient. "
            "Use concise, focused recall keywords instead of copying the full user message. "
            "Call this when the user asks you to recall prior facts, preferences, agreements, or older context, "
            "or when resolving ambiguous references requires checking memory. "
            "Prefer short topic phrases, named entities, preferences, commitments, or past events as recall keywords. "
            "If the first recall is not enough, refine the keywords and recall again."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Concise recall keywords for long-term memory. Prefer key entities, topics, preferences, commitments, or past events instead of copying the full user message.",
                },
                "k": {
                    "type": "integer",
                    "description": "Maximum number of memory items to return for one recall. Keep this small unless more evidence is needed.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        handler=_recall_handler,
    )


def build_memorize_tool() -> MemoryTool:
    return MemoryTool(
        name="memorize_long_term_memory",
        description=(
            "Memorize durable long-term memory when the user explicitly asks to remember something, "
            "or when stable preferences, identity details, agreements, or project context appear. "
            "Write concise factual memory, not the full conversation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Concise factual memory to store. Avoid duplicating existing memory; prefer specific, stable facts over verbose prose.",
                },
                "actor_id": {
                    "type": "string",
                    "description": "Who this memory is about. Defaults to 'agent' if omitted.",
                    "default": "agent",
                },
                "importance": {
                    "type": "number",
                    "description": "Optional 0..1 importance hint that survives decay better. Use sparingly.",
                    "default": 0.5,
                },
            },
            "required": ["content"],
        },
        handler=_memorize_handler,
    )


def build_forget_tool() -> MemoryTool:
    return MemoryTool(
        name="forget_long_term_memory",
        description=(
            "Forget a specific long-term memory by engram_id. "
            "Use when the user asks to forget, retract, or remove a previously stored fact. "
            "Default is soft forget: the row is marked forgotten but kept for audit. "
            "Pass hard=true to physically remove the engram; prefer soft unless the user asks for hard deletion."
        ),
        parameters={
            "type": "object",
            "properties": {
                "engram_id": {
                    "type": "string",
                    "description": "The engram id (32-char hex) to forget. Obtain via recall_long_term_memory or list_recent_memories first.",
                },
                "hard": {
                    "type": "boolean",
                    "description": "If true, physically delete the engram row. Default false (soft forget, keeps audit trail).",
                    "default": False,
                },
            },
            "required": ["engram_id"],
        },
        handler=_forget_handler,
    )


def build_list_recent_tool() -> MemoryTool:
    return MemoryTool(
        name="list_recent_memories",
        description=(
            "List the most recent active memories for a specific user/actor. "
            "Use when the user asks 'what do you remember about me', 'what have I said recently', "
            "or needs a quick review of their own recent history. "
            "Filter by actor_id (the user/chat identifier); does not cross users."
        ),
        parameters={
            "type": "object",
            "properties": {
                "actor_id": {
                    "type": "string",
                    "description": "Whose recent memories to list. Required; the agent should know this from the conversation context.",
                },
                "k": {
                    "type": "integer",
                    "description": "Maximum number of items to return. Keep this small (default 10) for fast review.",
                    "default": 10,
                },
            },
            "required": ["actor_id"],
        },
        handler=_list_recent_handler,
    )


def build_search_by_entity_tool() -> MemoryTool:
    return MemoryTool(
        name="search_by_entity_memory",
        description=(
            "Search long-term memory for engrams that reference a specific entity by name. "
            "Use when the user asks 'what do I know about X', 'find facts about X', "
            "or when a topic/relation is best anchored on a named entity. "
            "Entity lookup is case-insensitive and matches partial names via LIKE."
        ),
        parameters={
            "type": "object",
            "properties": {
                "entity_name": {
                    "type": "string",
                    "description": "The entity name to look up. Case-insensitive; partial matches accepted.",
                },
                "k": {
                    "type": "integer",
                    "description": "Maximum number of engrams to return. Keep small (default 10) for focused review.",
                    "default": 10,
                },
            },
            "required": ["entity_name"],
        },
        handler=_search_by_entity_handler,
    )


def all_tools() -> list[MemoryTool]:
    return [
        build_recall_tool(),
        build_memorize_tool(),
        build_forget_tool(),
        build_list_recent_tool(),
        build_search_by_entity_tool(),
    ]
