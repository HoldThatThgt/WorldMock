"""@brief 宿主侧人设层：由人物姓名、场景联系人与环境下发的看法偏好构建（docs/dev/SPEC-persona-prompt.md 第一章「人设层」）。

没有结构化卡片的人物只填姓名，作息、口味、应对为 null，习惯为空列表；关系按场景联系人逐项列出
（亲密度 null、渠道为空，场景联系人的亲密度与渠道不进提示），看法与偏好只来自环境下发的 persona_views。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def persona_layer(profile: Mapping[str, Any], name: str | None, views: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 构建人设层对象；键序即渲染顺序。

    @param profile 人物公开档案（场景人物声明的 JSON 形状，含 contacts）
    @param name 人物姓名
    @param views 观察私有证据 persona_views：{relationships: [{person, view}], preferences: [...]}
    @return 人设层 JSON 对象
    """
    latest = {item["person"]: item["view"] for item in views["relationships"]}
    relationships = [{"person": contact["salutation"], "role": contact["relation"], "closeness": None,
                      "channels": [], "view": latest.get(contact["salutation"])}
                     for contact in profile.get("contacts") or ()]
    return {
        "layer": "人设层",
        "identity": {"name": name, "age": None, "occupation": None, "living_arrangement": None},
        "rhythm": None,
        "habits": [],
        "tastes": None,
        "coping": None,
        "relationships": relationships,
        "preferences": [dict(item) for item in views["preferences"]],
    }
