"""@brief 人物卡片测试共用工厂：卡片由夹具人设表直接构造，字段可整体替换。"""

from __future__ import annotations

from tests.life.persona_inputs import persona_with
from worldmock.configuration.persona.persona_card import PersonaCard, TimeAnchors, TimeWindow, persona_card


def make_window(start: float, end: float) -> TimeWindow:
    """@brief 时段工厂。"""
    return TimeWindow(start_hour=start, end_hour=end)


def make_anchors(**windows) -> TimeAnchors:
    """@brief 夹具人物的时间锚点，按名字替换其中几个时段；下班、到家缺省为 null。"""
    base = persona_card(persona_with()).time_anchors
    return base.model_copy(update=windows)


def make_card(**overrides) -> PersonaCard:
    """@brief 夹具人设表构造出的卡片，字段可整体替换（替换值是卡片的结构对象，不再经人设表的检查）。"""
    return persona_card(persona_with()).model_copy(update=overrides)
