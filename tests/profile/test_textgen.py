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
    assert "7点" in text and "12点到13点" in text and "23点到24点" in text
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
#: c3b24da 的 render_card 对三类卡片的输出（由 `git show c3b24da:src/worldmock/profile/textgen.py` 算出后写死）：
#: 刻钟睡眠中点仍写整点、半点窗口、午夜后开始的窗口仍写「24点半」「25点」。
_C3B24DA_RENDERS = {
    "quarter_hour": _IDENTITY + (
        "约7点到7点半起床、8点到8点半出门；午餐12点到13点、晚餐18点半到19点半；就寝23点到24点，睡眠中点约3点，"
        "需睡7.5小时。\n习惯：午饭后→散步一刻钟（12点半到13点）。\n") + _TAIL,
    "half_hour": _IDENTITY + (
        "约6点半到7点半起床、8点半到9点出门；午餐11点半到12点半、晚餐18点半到19点半；就寝22点半到23点半，"
        "睡眠中点约3点半，需睡7.5小时。\n"
        "习惯：下班到家→公园慢跑半小时（19点到20点）；睡前→读几页小说（22点半到23点）。\n") + _TAIL,
    "after_midnight": _IDENTITY + (
        "约8点到8点半起床、9点到9点半出门；午餐12点到13点、晚餐19点到20点；就寝24点半到1点半，睡眠中点约5点，"
        "需睡7.5小时。\n习惯：睡不着→刷短视频（25点到2点）；睡前→读几页小说（23点半到24点）。\n") + _TAIL,
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


@pytest.mark.parametrize("kind", sorted(_C3B24DA_RENDERS))
def test_render_card_is_byte_identical_to_the_pre_persona_layer_renderer(kind):
    """@brief render_card 不动（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求-人设层「人设层」）：刻钟、半点与
    午夜后开始的窗口渲染出的卡片文本与 c3b24da 逐字节相同；刻钟与钟面写法只属于人设层的 hour_text / window_text。"""
    card = _golden_card(kind)
    assert render_card(card) == _C3B24DA_RENDERS[kind]


def _with_work_anchors(card, leave_work, arrive_home):
    """@brief 同一张卡片换上给定的下班、到家锚点（经 TimeAnchors 校验重建，其余五个锚点不变）。"""
    anchors = TimeAnchors.model_validate({**card.time_anchors.model_dump(), "leave_work": leave_work,
                                          "arrive_home": arrive_home})
    return card.model_copy(update={"time_anchors": anchors})


def test_non_null_work_anchors_each_append_a_clause_and_count_towards_the_budget():
    """@brief 画像文本（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-下班与到家锚点 ）：下班、到家各自非空时在作息行「需睡N小时」之后按此顺序各追加
    一个分句，其余文本逐字节不变；追加的分句计入 600 汉字预算——不带分句恰好 600 汉字的卡片照常渲染，带上下班分句即如实
    报错、不截断。"""
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
    full = make_card(tastes=Tastes(content_genres=("悬疑", "纪" * (spare + 3)), food_preferences=("川菜", "面食"),
                                   media=("长视频", "短视频")))
    assert count_hanzi(render_card(full)) == PERSONA_CARD_HANZI_BUDGET
    with pytest.raises(RenderBudgetExceededError, match="606 hanzi > 600"):
        render_card(_with_work_anchors(full, leave, None))


def test_the_arrive_home_clause_counts_towards_the_budget_alone_and_with_the_leave_work_clause():
    """@brief 追加分句计入 600 汉字预算（规格 4.1，裁决 W5）对到家分句同样成立：不带分句恰好 600 汉字的卡片，只带到家分句
    时如实报 606、两个分句都带时报 612，不截断也不放行；每个分句恰好多 6 个汉字。"""
    spare = PERSONA_CARD_HANZI_BUDGET - count_hanzi(render_card(make_card()))
    full = make_card(tastes=Tastes(content_genres=("悬疑", "纪" * (spare + 3)), food_preferences=("川菜", "面食"),
                                   media=("长视频", "短视频")))
    assert count_hanzi(render_card(full)) == PERSONA_CARD_HANZI_BUDGET
    leave, arrive = make_window(18.0, 18.5), make_window(19.0, 19.5)
    roomy = make_card()
    assert count_hanzi(render_card(_with_work_anchors(roomy, None, arrive))) == count_hanzi(render_card(roomy)) + 6
    with pytest.raises(RenderBudgetExceededError, match="606 hanzi > 600"):
        render_card(_with_work_anchors(full, None, arrive))
    with pytest.raises(RenderBudgetExceededError, match="612 hanzi > 600"):
        render_card(_with_work_anchors(full, leave, arrive))


def test_window_ends_at_or_after_midnight_render_as_clock_hours():
    """@brief 两端都按钟面写：到达或越过午夜（≥24）的一端按次日小时写，午夜后才开始的窗口也不写成「25点」
    （docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「人设层」：作息为可读短语）。"""
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
