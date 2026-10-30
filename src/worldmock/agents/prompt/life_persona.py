"""@brief 宿主侧人设层只由必需的结构化人物卡片和姓名构建，整次运行逐字节不变。

身份、作息、习惯、口味、应对与人物关系（称呼、角色、亲疏、联系渠道）都来自卡片，卡片派生字段受六百汉字上限约束。
收尾写下的看法与偏好不进人设层：当天以「看法·对象：」写在记忆层「今天」的条目里，过了零点只在回顾里（按日期查到「未整理」的那一天时，检索退回的原始条目里也有）。
relationship 看法可写的对象来自卡片关系名，以及观察私有证据 persona_views 里已有关系看法的人物。
只读问答使用最近决策保存的人设层与记忆层快照；尚无快照时使用卡片人设层与空记忆层。
工作安排只由环境层给出；卡片由人设表直接构造，作息值是人设表的原值，不取整，时刻显示写到分钟。
规则见 docs/design/06-agent.md「提示与工具」。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from worldmock.agents.prompt.life_prompt import empty_memory_layer
from worldmock.foundation.errors import IntegrityError
from worldmock.agents.prompt.narration import PERSONA_CARD_HANZI_BUDGET, RenderBudgetExceededError, count_hanzi
from worldmock.configuration.persona.persona_card import PersonaCardStruct, TimeWindow
from worldmock.configuration.persona.persona_text import hour_text, intimacy_text, window_text

#: 只读问答快照在人物工作区中的文件名（每次生活决策构建请求后写入）。
PROMPT_SNAPSHOT_FILE = "life_prompt_snapshot.json"


def persona_layer(profile: Mapping[str, Any], name: str | None) -> dict[str, Any]:
    """@brief 构建人设层对象；键序即渲染顺序，全部取自卡片与姓名。

    @param profile 含必需 persona_card_struct 的人物公开档案
    @param name 人物姓名
    @return 人设层 JSON 对象
    @exception RenderBudgetExceededError 卡片派生的字段超过 600 汉字
    """
    card = card_struct(profile)
    derived = _card_fields(card)
    layer = {
        "layer": "人设层",
        "identity": {"name": name, **derived["identity"]},
        "rhythm": derived["rhythm"], "habits": derived["habits"], "tastes": derived["tastes"],
        "coping": derived["coping"], "relationships": derived["relationships"],
    }
    hanzi = count_hanzi(json.dumps(derived, ensure_ascii=False))
    if hanzi > PERSONA_CARD_HANZI_BUDGET:
        logging.error("Persona card fields exceed the hanzi budget: %d", hanzi)
        raise RenderBudgetExceededError(f"persona card fields exceed budget: {hanzi} hanzi > "
                                        f"{PERSONA_CARD_HANZI_BUDGET} (input-side problem, not truncated)")
    return layer


def card_struct(profile: Mapping[str, Any]) -> PersonaCardStruct:
    """@brief 核验必需的结构化人物卡片，缺失时立即失败。"""
    raw = profile.get("persona_card_struct")
    if raw is None:
        raise IntegrityError("Persona card structure is required")
    return PersonaCardStruct.model_validate(raw)


def known_persons(profile: Mapping[str, Any], relationship_names: list[str]) -> frozenset[str]:
    """@brief relationship 记忆可写的对象只来自卡片关系名或已经形成的关系看法。"""
    card = card_struct(profile)
    names = {item.name_or_appellation for item in card.relationships}
    return frozenset(names | set(relationship_names))


def write_prompt_snapshot(workspace: Path, persona: dict[str, Any], memory: dict[str, Any]) -> None:
    """@brief 保存本次生活决策的人设层与记忆层快照，供只读问答使用。"""
    content = json.dumps({"persona": persona, "memory": memory}, ensure_ascii=False, separators=(",", ":"))
    (workspace / PROMPT_SNAPSHOT_FILE).write_text(content, encoding="utf-8")


def readonly_layers(workspace: Path | None, profile: Mapping[str, Any], name: str | None) -> tuple[dict, dict]:
    """@brief 只读问答的人设层与记忆层：最近一次生活决策的快照；尚无快照时为卡片人设层与空记忆层。"""
    path = workspace / PROMPT_SNAPSHOT_FILE if workspace is not None else None
    if path is not None and path.is_file():
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        return snapshot["persona"], snapshot["memory"]
    persona = persona_layer(profile, name)
    return persona, empty_memory_layer()


def _card_fields(card: PersonaCardStruct) -> dict[str, Any]:
    """@brief 从已核验的结构化人物卡片派生公开字段。"""
    anchors = card.time_anchors
    return {
        "identity": {"age": card.identity.age, "occupation": card.identity.occupation,
                     "living_arrangement": card.identity.living_arrangement},
        "rhythm": {"wake": window_text(anchors.wake), "leave_home": window_text(anchors.leave_home),
                   "lunch": window_text(anchors.lunch),
                   "leave_work": _optional_window(anchors.leave_work),
                   "arrive_home": _optional_window(anchors.arrive_home),
                   "dinner": window_text(anchors.dinner),
                   "bedtime": window_text(anchors.bedtime), "sleep_midpoint": hour_text(card.chronotype_midsleep),
                   "sleep_need": f"{card.sleep_need_hours:g} 小时"},
        "habits": [{"trigger": item.trigger, "response": item.response, "time_window": window_text(item.time_window)}
                   for item in card.habits],
        "tastes": {"content_genres": list(card.tastes.content_genres),
                   "food_preferences": list(card.tastes.food_preferences), "media": list(card.tastes.media)},
        "coping": card.coping,
        "relationships": [{"person": item.name_or_appellation, "role": item.role,
                           "closeness": intimacy_text(item.intimacy), "channels": list(item.channels)}
                          for item in card.relationships],
    }


def _optional_window(window: TimeWindow | None) -> str | None:
    """@brief 可空锚点（下班、到家）的人设层写法：有窗口时同其他锚点写成可读短语，没有固定时刻（null）时为 None。"""
    return None if window is None else window_text(window)
