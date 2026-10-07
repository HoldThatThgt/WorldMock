"""@brief 人物卡片的纯文本组装（记忆巩固与反思的人物段）。"""

from __future__ import annotations

from typing import Any, Mapping


def persona_card_text(profile: Mapping[str, Any], name: str | None) -> str:
    """@brief 只用人物卡或公开档案字段组装人物自己的经历与习惯。"""
    card = profile.get("persona_card")
    if isinstance(card, str) and card.strip():
        return card.strip()
    lines = [f"你是{name or '这个人'}。"]
    for key, lead in (
        ("background_story", "你的经历："),
        ("health_habits", "你的健康习惯："),
        ("social_style", "你的社交风格："),
    ):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{lead}{value.strip()}")
    return "\n".join(lines)

