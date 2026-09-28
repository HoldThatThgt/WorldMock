"""@brief 宿主侧人设层：由人物 profile 的结构化卡片、姓名、场景联系人与环境下发的看法偏好构建
（docs/dev/SPEC-persona-prompt.md 第一章「人设层」，裁决 #33、#35）。

- 有结构化卡片（profile["persona_card_struct"]，新建运行时保存、不含当下处境）的人物：身份、作息（刻钟写对）、至多五条
  习惯、口味与应对取自卡片；卡片关系与场景联系人按名称精确合并（卡片 name_or_appellation 等于联系人称谓），对不上的
  各占一项；只来自场景联系人的项亲密度为 null、渠道为空（联系人的亲密度与渠道不进提示）。600 汉字上限只约束卡片派生的字段。
- 没有结构化卡片的人物只填姓名，作息、口味、应对为 null，习惯为空列表，关系按场景联系人逐项列出。
- 看法与偏好只来自环境下发的 persona_views（成功的 conclude_activity 写下的 relationship 与 preference 记忆，同一对象
  取最后一条）；看法写到对应人物的关系项上，不在卡片与联系人中的已知人物（同世界人物）另占一项，角色、亲密度为 null、
  渠道为空（不填编造的值）。
- 只读问答取最近一次生活决策保存在人物工作区的人设层与记忆层快照（裁决 #38）；尚无决策时用卡片与空看法构建人设层，
  记忆层两个列表为空。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from custom.agents.life_prompt import empty_memory_layer
from worldmock.life.narration import PERSONA_CARD_HANZI_BUDGET, RenderBudgetExceededError, count_hanzi
from worldmock.profile.models import PersonaCardStruct
from worldmock.profile.textgen import hour_text, intimacy_text, window_text

#: 只读问答快照在人物工作区中的文件名（每次生活决策构建请求后写入）。
PROMPT_SNAPSHOT_FILE = "life_prompt_snapshot.json"


def persona_layer(profile: Mapping[str, Any], name: str | None, views: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 构建人设层对象；键序即渲染顺序。

    @param profile 人物公开档案（场景人物声明的 JSON 形状，含 contacts，可有 persona_card_struct）
    @param name 人物姓名
    @param views 观察私有证据 persona_views：{relationships: [{person, view}], preferences: [...]}
    @return 人设层 JSON 对象
    @exception RenderBudgetExceededError 卡片派生的字段超过 600 汉字
    """
    card = card_struct(profile)
    latest = {item["person"]: item["view"] for item in views["relationships"]}
    derived = _card_fields(card)
    layer = {
        "layer": "人设层",
        "identity": {"name": name, **derived["identity"]},
        "rhythm": derived["rhythm"], "habits": derived["habits"], "tastes": derived["tastes"],
        "coping": derived["coping"],
        "relationships": _relationships(derived["relationships"], profile.get("contacts") or (), latest),
        "preferences": [dict(item) for item in views["preferences"]],
    }
    hanzi = count_hanzi(json.dumps(derived, ensure_ascii=False))
    if hanzi > PERSONA_CARD_HANZI_BUDGET:
        logging.error("Persona card fields exceed the hanzi budget: %d", hanzi)
        raise RenderBudgetExceededError(f"persona card fields exceed budget: {hanzi} hanzi > "
                                        f"{PERSONA_CARD_HANZI_BUDGET} (input-side problem, not truncated)")
    return layer


def card_struct(profile: Mapping[str, Any]) -> PersonaCardStruct | None:
    """@brief profile 中的结构化卡片（按 PersonaCardStruct 校验）；没有卡片的人物为 None。"""
    raw = profile.get("persona_card_struct")
    return None if raw is None else PersonaCardStruct.model_validate(raw)


def known_persons(profile: Mapping[str, Any], people: list[str]) -> frozenset[str]:
    """@brief relationship 记忆可写的对象：卡片关系名、场景联系人称谓与同世界人物姓名（不论是否同处）。"""
    card = card_struct(profile)
    names = {item.name_or_appellation for item in card.relationships} if card is not None else set()
    names.update(contact["salutation"] for contact in profile.get("contacts") or ())
    return frozenset(names | set(people))


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
    persona = persona_layer(profile, name, {"relationships": [], "preferences": []})
    return persona, empty_memory_layer()


def _card_fields(card: PersonaCardStruct | None) -> dict[str, Any]:
    """@brief 卡片派生的字段（没有卡片时身份其余字段、作息、口味与应对为 null，习惯与关系为空）。"""
    if card is None:
        return {"identity": {"age": None, "occupation": None, "living_arrangement": None}, "rhythm": None,
                "habits": [], "tastes": None, "coping": None, "relationships": []}
    anchors = card.time_anchors
    return {
        "identity": {"age": card.identity.age, "occupation": card.identity.occupation,
                     "living_arrangement": card.identity.living_arrangement},
        "rhythm": {"wake": window_text(anchors.wake), "leave_home": window_text(anchors.leave_home),
                   "lunch": window_text(anchors.lunch), "dinner": window_text(anchors.dinner),
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


def _relationships(card_items: list[dict], contacts: Any, latest: dict[str, str]) -> list[dict[str, Any]]:
    """@brief 卡片关系在前（卡片顺序），场景联系人中对不上的随后（场景顺序），只有看法的已知人物最后（角色为 null）；
    各带最新看法。"""
    items = [dict(item) for item in card_items]
    listed = {item["person"] for item in items}
    for contact in contacts:
        if contact["salutation"] not in listed:
            items.append({"person": contact["salutation"], "role": contact["relation"], "closeness": None,
                          "channels": []})
            listed.add(contact["salutation"])
    items.extend({"person": person, "role": None, "closeness": None, "channels": []}
                 for person in latest if person not in listed)
    return [{**item, "view": latest.get(item["person"])} for item in items]
