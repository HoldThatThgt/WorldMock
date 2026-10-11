"""@brief 卡片渲染：确定性、≤600 汉字、时刻照卡片里的原值写到分钟。"""

from __future__ import annotations

import pytest

from worldmock.agents.life_persona import persona_layer
from worldmock.input_models import Relationship, Situation, Tastes
from worldmock.profile.models import PERSONA_CARD_HANZI_BUDGET, HabitSignature, TimeAnchors, persona_card_struct
from worldmock.profile.textgen import (
    RenderBudgetExceededError,
    count_hanzi,
    hour_text,
    render_card,
    window_text,
)
from tests.profile.conftest import make_card, make_window

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
                         intimacy=10 + index, channels=["微信", "电话"])
            for index in range(4)),
        habits=tuple(
            HabitSignature(trigger=f"情境{index}", response=f"做一件较长但常规的事情{index}",
                           time_window=make_window(19.0, 20.0))
            for index in range(5)),
        tastes=Tastes(content_genres=[f"题材{index}" for index in range(6)],
                      food_preferences=[f"食物{index}" for index in range(6)],
                      media=[f"媒介{index}" for index in range(4)]),
        situation=Situation(
            busy_this_week="推进一个跨团队项目的收尾阶段并准备评审材料",
            cares_about="家里长辈的身体检查结果与自己的睡眠质量"))
    assert count_hanzi(render_card(card)) <= PERSONA_CARD_HANZI_BUDGET


def test_render_card_without_relationships_habits_or_tastes_uses_fallback_lines():
    """@brief 人设表的关系、习惯和口味三个数组都可以为空：各写一行固定说法，其余行不变。"""
    card = make_card(relationships=(), habits=(),
                     tastes=Tastes(content_genres=[], food_preferences=[], media=[]))
    lines = render_card(card).splitlines()
    assert lines[2] == "亲近的人：无。"
    assert lines[4] == "习惯：无明确习惯签名。"
    assert lines[5] == "口味：无特别偏好。"
    assert count_hanzi("\n".join(lines)) <= PERSONA_CARD_HANZI_BUDGET


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
            bedtime=make_window(23.5, 25.0), leave_work=None, arrive_home=None))
    assert "23点半到1点" in render_card(card)


_IDENTITY = ("产品经理，32岁，与伴侣合租两居室。\n本周在忙新版上线收尾；在意父母的体检结果。\n"
             "亲近的人：母亲（家人，很亲近，电话、微信联系）、老周（老友，较亲近，微信、见面联系）。\n")
_TAIL = "口味：爱看悬疑、纪录片；喜欢吃川菜、面食；常用长视频、短视频。\n压力时拼高达模型。"
#: 卡片文本对三类卡片的输出：时刻写到分钟，刻钟睡眠中点写「三刻」「一刻」，午夜及其后的时刻按钟面写「0点」「1点」。
_EXPECTED_RENDERED_CARDS = {
    "quarter_hour": _IDENTITY + (
        "约7点到7点半起床、8点到8点半出门；午餐12点到13点、晚餐18点半到19点半；就寝23点到0点，睡眠中点约3点三刻，"
        "需睡7.5小时。\n习惯：午饭后→散步一刻钟（12点半到13点）。\n") + _TAIL,
    "half_hour": _IDENTITY + (
        "约6点半到7点半起床、8点半到9点出门；午餐11点半到12点半、晚餐18点半到19点半；就寝22点半到23点半，"
        "睡眠中点约3点半，需睡7.5小时。\n"
        "习惯：下班到家→公园慢跑半小时（19点到20点）；睡前→读几页小说（22点半到23点）。\n") + _TAIL,
    "after_midnight": _IDENTITY + (
        "约8点到8点半起床、9点到9点半出门；午餐12点到13点、晚餐19点到20点；就寝0点半到1点半，睡眠中点约5点一刻，"
        "需睡7.5小时。\n习惯：睡不着→刷短视频（1点到2点）；睡前→读几页小说（23点半到0点）。\n") + _TAIL,
}


def _golden_card(kind: str):
    """@brief 金标卡片：刻钟睡眠中点、半点窗口、午夜后开始的就寝与习惯窗口。"""
    if kind == "quarter_hour":
        return make_card(chronotype_midsleep=3.75, habits=(
            HabitSignature(trigger="午饭后", response="散步一刻钟", time_window=make_window(12.5, 13.0)),))
    if kind == "half_hour":
        return make_card(chronotype_midsleep=3.5, time_anchors=TimeAnchors(
            wake=make_window(6.5, 7.5), leave_home=make_window(8.5, 9.0), lunch=make_window(11.5, 12.5),
            dinner=make_window(18.5, 19.5), bedtime=make_window(22.5, 23.5), leave_work=None, arrive_home=None))
    return make_card(chronotype_midsleep=5.25, time_anchors=TimeAnchors(
        wake=make_window(8.0, 8.5), leave_home=make_window(9.0, 9.5), lunch=make_window(12.0, 13.0),
        dinner=make_window(19.0, 20.0), bedtime=make_window(24.5, 25.5), leave_work=None, arrive_home=None), habits=(
        HabitSignature(trigger="睡不着", response="刷短视频", time_window=make_window(25.0, 26.0)),
        HabitSignature(trigger="睡前", response="读几页小说", time_window=make_window(23.5, 24.0))))


@pytest.mark.parametrize("kind", sorted(_EXPECTED_RENDERED_CARDS))
def test_render_card_writes_clock_and_window_text_to_the_minute(kind):
    """@brief 卡片文本与人设层共用同一套时刻写法：刻钟、半点与午夜后开始的窗口都按钟面写到分钟。"""
    card = _golden_card(kind)
    assert render_card(card) == _EXPECTED_RENDERED_CARDS[kind]


@pytest.mark.parametrize(("hour", "text"), [
    (4.25, "4点一刻"), (12.1, "12点6分"), (18.3333, "18点20分"), (23.9999, "0点"), (24.0, "0点"), (25.0, "1点"),
    (4.1, "4点6分"), (18.25, "18点一刻"), (18.75, "18点三刻"), (0.5, "0点半"), (7.0, "7点"),
])
def test_hour_text_rounds_to_the_minute_then_wraps_around_the_day(hour, text):
    """@brief 时刻写法：先取整到分钟，再对一天取模；0 分不加，15、30、45 分写一刻、半、三刻，其余写「N分」。"""
    assert hour_text(hour) == text


def test_card_text_and_persona_layer_write_the_same_window_text():
    """@brief 同一张卡片（取值不在格点上）在卡片文本与人设层里写出相同的时段、睡眠中点与睡眠需要。"""
    card = make_card(chronotype_midsleep=4.1, sleep_need_hours=7.6, time_anchors=TimeAnchors(
        wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5), lunch=make_window(12.1, 12.2),
        dinner=make_window(19.0, 20.0), bedtime=make_window(24.5, 25.5),
        leave_work=make_window(18.0, 18.5), arrive_home=make_window(18.25, 18.75)))

    text = render_card(card)
    rhythm = persona_layer({"persona_card_struct": persona_card_struct(card)}, "林舟",
                           {"relationships": [], "preferences": []})["rhythm"]

    assert rhythm == {
        "wake": "7点到7点半", "leave_home": "8点到8点半", "lunch": "12点6分到12点12分",
        "leave_work": "18点到18点半", "arrive_home": "18点一刻到18点三刻", "dinner": "19点到20点",
        "bedtime": "0点半到1点半", "sleep_midpoint": "4点6分", "sleep_need": "7.6 小时"}
    assert text.splitlines()[3] == (
        "约7点到7点半起床、8点到8点半出门；午餐12点6分到12点12分、晚餐19点到20点；就寝0点半到1点半，"
        "睡眠中点约4点6分，需睡7.6小时；下班18点到18点半；到家18点一刻到18点三刻。")
    for name in ("wake", "leave_home", "lunch", "leave_work", "arrive_home", "dinner", "bedtime"):
        assert rhythm[name] in text
    assert "需睡8小时" in render_card(make_card(sleep_need_hours=8.0))


def _with_work_anchors(card, leave_work, arrive_home):
    """@brief 同一张卡片换上给定的下班、到家锚点，其余五个锚点不变。"""
    anchors = card.time_anchors.model_copy(update={"leave_work": leave_work, "arrive_home": arrive_home})
    return card.model_copy(update={"time_anchors": anchors})


def test_non_null_work_anchors_each_append_a_clause_and_count_towards_the_budget():
    """@brief 卡片文本（docs/design/06-agent.md「人物身份与画像」）：下班、到家各自非空时在作息行「需睡N小时」之后按此顺序
    各追加一个分句，其余文本逐字节不变；追加的分句计入 600 汉字预算——不带分句恰好 600 汉字的卡片照常渲染，带上下班分句
    即如实报错、不截断。"""
    card = make_card()
    plain = render_card(card)
    leave, arrive = make_window(18.0, 18.5), make_window(19.0, 19.5)
    cases = {(leave, None): "需睡7.5小时；下班18点到18点半。", (None, arrive): "需睡7.5小时；到家19点到19点半。",
             (leave, arrive): "需睡7.5小时；下班18点到18点半；到家19点到19点半。"}
    for (leave_work, arrive_home), ending in cases.items():
        text = render_card(_with_work_anchors(card, leave_work, arrive_home))
        assert text == plain.replace("需睡7.5小时。", ending)
        assert text.splitlines()[3].endswith(ending)
    assert render_card(_with_work_anchors(card, None, None)) == plain
    spare = PERSONA_CARD_HANZI_BUDGET - count_hanzi(plain)
    full = make_card(tastes=Tastes(content_genres=["悬疑", "纪" * (spare + 3)], food_preferences=["川菜", "面食"],
                                   media=["长视频", "短视频"]))
    assert count_hanzi(render_card(full)) == PERSONA_CARD_HANZI_BUDGET
    with pytest.raises(RenderBudgetExceededError, match="606 hanzi > 600"):
        render_card(_with_work_anchors(full, leave, None))


def test_the_arrive_home_clause_counts_towards_the_budget_alone_and_with_the_leave_work_clause():
    """@brief 追加分句计入 600 汉字预算对到家分句同样成立：不带分句恰好 600 汉字的卡片，只带到家分句
    时如实报 606、两个分句都带时报 612，不截断也不放行；每个分句恰好多 6 个汉字。"""
    spare = PERSONA_CARD_HANZI_BUDGET - count_hanzi(render_card(make_card()))
    full = make_card(tastes=Tastes(content_genres=["悬疑", "纪" * (spare + 3)], food_preferences=["川菜", "面食"],
                                   media=["长视频", "短视频"]))
    assert count_hanzi(render_card(full)) == PERSONA_CARD_HANZI_BUDGET
    leave, arrive = make_window(18.0, 18.5), make_window(19.0, 19.5)
    roomy = make_card()
    assert count_hanzi(render_card(_with_work_anchors(roomy, None, arrive))) == count_hanzi(render_card(roomy)) + 6
    with pytest.raises(RenderBudgetExceededError, match="606 hanzi > 600"):
        render_card(_with_work_anchors(full, None, arrive))
    with pytest.raises(RenderBudgetExceededError, match="612 hanzi > 600"):
        render_card(_with_work_anchors(full, leave, arrive))


def test_window_ends_at_or_after_midnight_render_as_clock_hours():
    """@brief 两端都按钟面写：到达或越过午夜（≥24）的一端按次日小时写，午夜后才开始的窗口也不写成「25点」。"""
    assert window_text(make_window(25.0, 26.0)) == "1点到2点"
    assert window_text(make_window(24.0, 25.5)) == "0点到1点半"
    assert window_text(make_window(22.0, 24.0)) == "22点到0点"
    assert window_text(make_window(23.5, 24.5)) == "23点半到0点半"
    assert window_text(make_window(7.0, 7.5)) == "7点到7点半"


def test_intimacy_bands_render():
    assert "很亲近" in render_card(make_card())
    assert "疏远" in render_card(make_card(relationships=(
        Relationship(name_or_appellation="点头之交", role="邻居", intimacy=2,
                     channels=["见面"]),)))


def test_count_hanzi_counts_only_cjk():
    assert count_hanzi("中文abc123，。") == 2
