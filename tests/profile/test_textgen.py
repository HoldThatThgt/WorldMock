"""@brief 卡片渲染：确定性、≤600 汉字、黑名单（大五与随机浮点不出现）。"""

from __future__ import annotations

import re

import pytest

from worldmock.profile.models import (
    PERSONA_CARD_HANZI_BUDGET,
    HabitSignature,
    Relationship,
    Situation,
    Tastes,
    TimeAnchors,
)
from worldmock.profile.textgen import (
    RenderBudgetExceededError,
    count_hanzi,
    render_card,
    window_text,
)
from tests.profile.conftest import make_card, make_window

#: 黑名单：大五标签、旧九参数名、百分比与多小数随机浮点不得出现在渲染文本。
BLACKLIST_TERMS = ("开放性", "尽责性", "外向性", "宜人性", "神经质",
                   "big_five", "tau", "beta", "λ", "权重", "%", "weights")


def test_render_is_deterministic():
    card = make_card()
    assert render_card(card) == render_card(card)


def test_render_within_hanzi_budget():
    text = render_card(make_card())
    assert 0 < count_hanzi(text) <= PERSONA_CARD_HANZI_BUDGET


def test_maxed_out_card_still_within_budget():
    """@brief 满配卡片（4 关系/5 习惯/6 题材/6 饮食/4 媒介）仍不超 600 汉字。"""
    card = make_card(
        relationships=tuple(
            Relationship(name_or_appellation=f"朋友{index}", role="老友",
                         intimacy=10 + index, channels=("微信", "电话"))
            for index in range(4)),
        habits=tuple(
            HabitSignature(trigger=f"情境{index}", response=f"做一件较长但常规的事情{index}",
                           time_window=make_window(19.0, 20.0))
            for index in range(5)),
        tastes=Tastes(content_genres=tuple(f"题材{index}" for index in range(6)),
                      food_preferences=tuple(f"食物{index}" for index in range(6)),
                      media=tuple(f"媒介{index}" for index in range(4))),
        situation=Situation(
            busy_this_week="推进一个跨团队项目的收尾阶段并准备评审材料",
            cares_about="家里长辈的身体检查结果与自己的睡眠质量"))
    assert count_hanzi(render_card(card)) <= PERSONA_CARD_HANZI_BUDGET


def test_render_blacklist_excludes_big_five_and_random_floats():
    """@brief 黑名单断言：大五术语/旧参数名/百分比/多小数浮点不出现。"""
    text = render_card(make_card())
    for term in BLACKLIST_TERMS:
        assert term not in text, f"blacklisted term {term!r} leaked into card render"
    assert not re.search(r"\d+\.\d{2,}", text), "multi-decimal float leaked into render"


def test_render_carries_card_content():
    """@brief 渲染承载卡片内容：身份、关系、锚点、习惯、口味、应对逐块出现。"""
    card = make_card()
    text = render_card(card)
    assert "产品经理" in text and "32岁" in text
    assert "母亲" in text and "很亲近" in text
    assert "7点" in text and "12点到13点" in text and "23点到0点" in text
    assert "下班到家→公园慢跑半小时" in text
    assert "悬疑" in text and "川菜" in text
    assert "压力时拼高达模型" in text


def test_over_budget_card_raises_honestly():
    """@brief 超预算是输入侧问题：如实抛错，不静默截断。"""
    from worldmock.profile import textgen
    original = textgen.PERSONA_CARD_HANZI_BUDGET
    textgen.PERSONA_CARD_HANZI_BUDGET = 10
    try:
        with pytest.raises(RenderBudgetExceededError, match="exceeds budget"):
            render_card(make_card())
    finally:
        textgen.PERSONA_CARD_HANZI_BUDGET = original


def test_cross_midnight_bedtime_renders_as_next_day():
    card = make_card(
        time_anchors=TimeAnchors(
            wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5),
            lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
            bedtime=make_window(23.5, 25.0)))
    assert "23点半到1点" in render_card(card)


def test_window_ends_at_or_after_midnight_render_as_clock_hours():
    """@brief 两端都按钟面写：到达或越过午夜（≥24）的一端按次日小时写，午夜后才开始的窗口也不写成「25点」
    （docs/dev/SPEC-persona-prompt.md「人设层」：作息为可读短语）。"""
    assert window_text(make_window(25.0, 26.0)) == "1点到2点"
    assert window_text(make_window(24.0, 25.5)) == "0点到1点半"
    assert window_text(make_window(22.0, 24.0)) == "22点到0点"
    assert window_text(make_window(23.5, 24.5)) == "23点半到0点半"
    assert window_text(make_window(7.0, 7.5)) == "7点到7点半"


def test_intimacy_bands_render():
    assert "很亲近" in render_card(make_card())
    assert "疏远" in render_card(make_card(relationships=(
        Relationship(name_or_appellation="点头之交", role="邻居", intimacy=2,
                     channels=("见面",)),)))


def test_count_hanzi_counts_only_cjk():
    assert count_hanzi("中文abc123，。") == 2
