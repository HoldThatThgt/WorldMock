"""@brief 人物卡片的纯文本组装（记忆巩固与反思的人物段）。"""

from __future__ import annotations

from typing import Any, Mapping
from worldmock.foundation.errors import IntegrityError


def persona_card_text(profile: Mapping[str, Any], name: str | None) -> str:
    """@brief 只用人物卡或公开档案字段组装人物自己的经历与习惯。"""
    card = profile.get("persona_card")
    if isinstance(card, str) and card.strip():
        return card.strip()
    raise IntegrityError("Persona card text is required")
