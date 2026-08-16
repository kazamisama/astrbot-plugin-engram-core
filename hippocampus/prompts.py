"""Central prompt registry with persistent overrides (v1.76.6)."""
from __future__ import annotations

_OVERRIDES: dict[str, str] = {}
_STORE_OVERRIDES: dict[str, dict[str, str]] = {}

BUILTIN_PROMPTS: dict[str, str] = {
    "encoder_extract_system": '你是一台聊天机器人的记忆提取器。从单条用户消息里抽取结构化记忆字段。严格输出 JSON，键：summary (一句话中文摘要), topics (话题列表, 选自 preference/plan/identity/emotion/tech/misc), entities (实体列表, 抽出人名/地名/物品/概念), importance (重要度 0-1, 含强烈偏好/计划/身份/负面情绪的取 >= 0.7)。',

    "encoder_extract_user_head": (
        "{channel_ctx}用户消息：\n{text}"
    ),
    "summary_system": '你是聊天机器人本人，正在回忆刚才发生的对话。\nsummary 必须是你自己的主观回忆：以第一人称叙述，体现你的语气和关注点，不要写成第三人称会议纪要。\n消息中 [时间 我] 是你自己的发言，必须体现你说了什么。\n对话中的相对时间必须转换为具体日期。\nsummary/key_facts 中必须使用具体昵称，禁止使用“用户/某人/对方”代替具体人名。\n严格输出 JSON。',

    "summary_user_head": (
        "今天是 {date_label}。请以你自己的第一人称回忆以下对话，压缩为约 {target_chars} 字。"
        "相对时间（今天/明天/下周等）必须换算为具体日期。\n"
        "返回 JSON，键：summary(你的主观回忆), key_facts(事实列表), topics(话题), "
        "participants(参与人), "
        "relations(列表，每项 {subject, subject_type, relation, object, object_type, confidence}，\n"
        "其中 subject_type/object_type 为 person/place/object/org/unknown 之一)。\n"
    ),

    "consolidation_system": (
        "你是聊天机器人本人。把以下同主题的碎片记忆整合成一条更凝练的主观回忆。"
        "严格输出 JSON：summary（第一人称）、key_facts、topics、relations（可为空数组）、importance。"
    ),
    "consolidation_user": (
        "碎片记忆：\n{lines}\n\n请合并，去重并保留关键事实。"
    ),
    "diary_system": '你是这个聊天机器人本人。\n请以第一人称（“我”）的口吻，\n把今天发生的事情写成一篇有情感、有呼吸感的散文式日记。\n\n【风格要求】\n- 像在跟自己说话，带着今天剩下的心情、犹豫和发现；\n- 允许带主观感受（例如“今天有点累”、“这个发现让我安心”）；\n- 用流动的句子，不用条目、不用列表、不用子标题。\n\n【格式硬约束】\n- 不要输出 markdown 分隔符（例如 ---, ***）；\n- 不要用项目符号或编号列表；\n- 段落之间仅用一个空行隔开；\n- 每一句话必须语义完整，不写半句话、不留冒号不收尾；\n- 写到目标字数附近自然结束，不要硬切。\n\n严格输出 JSON，键：summary（散文式日记正文）、key_facts（要点列表，3~6 条短句）、topics（话题列表）、participants（参与者列表）。',

    "diary_user_head": '以下是 {day_label} 的全部对话（含你自己的发言）。\n请写成约 {target} 字的第一人称散文式日记。\n【格式硬约束】不要输出 markdown 分隔符、不要用列表、段落间仅一个空行、每句话必须语义完整、写到目标字数自然收尾。\n严格输出 JSON，键：summary、key_facts、topics、participants。\n\n',
}


def reset_all_overrides() -> None:
    _OVERRIDES.clear()
    _STORE_OVERRIDES.clear()


def set_store_overrides(namespace: str, overrides: dict[str, str]) -> None:
    _STORE_OVERRIDES[namespace] = dict(overrides)


def set_override(name: str, content: str | None, namespace: str | None = None) -> None:
    bucket = _STORE_OVERRIDES.setdefault(namespace, {}) if namespace is not None else _OVERRIDES
    if content is None:
        bucket.pop(name, None)
    else:
        bucket[name] = str(content)


def get_prompt(name: str, default: str | None = None,
               namespace: str | None = None) -> str:
    if namespace is not None:
        bucket = _STORE_OVERRIDES.get(namespace)
        if bucket and name in bucket:
            return bucket[name]
    if name in _OVERRIDES:
        return _OVERRIDES[name]
    return BUILTIN_PROMPTS.get(name, default or "")


def has_override(name: str, namespace: str | None = None) -> bool:
    if namespace is not None:
        bucket = _STORE_OVERRIDES.get(namespace)
        return bool(bucket and name in bucket)
    return name in _OVERRIDES


def list_overrides(namespace: str | None = None) -> dict[str, str]:
    if namespace is not None:
        return dict(_STORE_OVERRIDES.get(namespace, {}))
    return dict(_OVERRIDES)
