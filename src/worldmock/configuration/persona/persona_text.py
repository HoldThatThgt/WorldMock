"""@brief PersonaCard → 提示用中文卡片文本（确定性组装，零 LLM，≤600 汉字）。

渲染只做确定性文本组装，不调用模型，时刻照卡片里的原值写到分钟（docs/design/06-agent.md「人物身份与画像」）。
超出 600 汉字是输入侧问题：如实抛错，不静默截断。
"""

from __future__ import annotations

from worldmock.configuration.inputs.models import Relationship
from worldmock.configuration.persona.persona_card import (
    PERSONA_CARD_HANZI_BUDGET,
    HabitSignature,
    PersonaCard,
    TimeWindow,
)


class RenderBudgetExceededError(ValueError):
    """@brief 卡片渲染超出 600 汉字预算：输入侧问题，不截断。"""


def count_hanzi(text: str) -> int:
    """@brief 统一汉字计数口径：只计 CJK 基本区字符（与 life/narration.py 同法）。"""
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def render_card(card: PersonaCard) -> str:
    """@brief 卡片 → 中文文本（同卡片同文本；超 600 汉字如实抛错）。

    @param card 结构化人物卡片
    @return 不超过 600 汉字的中文卡片文本
    @exception RenderBudgetExceededError 渲染结果超汉字预算
    """
    lines = [
        _identity_line(card),
        _situation_line(card),
        _relationship_line(card),
        _rhythm_line(card),
        _habit_line(card),
        _taste_line(card),
        f"压力时{card.coping}。",
    ]
    text = "\n".join(lines)
    hanzi = count_hanzi(text)
    if hanzi > PERSONA_CARD_HANZI_BUDGET:
        raise RenderBudgetExceededError(
            f"persona card render exceeds budget: {hanzi} hanzi > "
            f"{PERSONA_CARD_HANZI_BUDGET} (input-side problem, not truncated)")
    return text


#: 一天的分钟数：时刻先取整到分钟，再对一天取模。
MINUTES_PER_DAY = 1440
#: 整点后的分钟数 → 中文说法（刻钟与半点；其余分钟写「N分」）。
_MINUTE_WORDS = {0: "", 15: "一刻", 30: "半", 45: "三刻"}


def hour_text(hour: float) -> str:
    """@brief 小时数 → 写到分钟的中文时刻，人设层与卡片文本共用。

    先取整到分钟（恰在两个整数正中时取偶数），再对一天取模：7.0→"7点"、7.25→"7点一刻"、7.5→"7点半"、
    7.75→"7点三刻"、12.1→"12点6分"；23.9999 与 24.0 都写成"0点"，25.0 写成"1点"。
    """
    minutes = round(hour * 60) % MINUTES_PER_DAY
    whole, minute = divmod(minutes, 60)
    word = _MINUTE_WORDS.get(minute, f"{minute}分")
    return f"{whole}点{word}"


def window_text(window: TimeWindow) -> str:
    """@brief 时段 → "<起点>到<终点>"，两端都按钟面写到分钟（25→"1点"、24→"0点"），人设层与卡片文本共用。"""
    return f"{hour_text(window.start_hour)}到{hour_text(window.end_hour)}"


def _identity_line(card: PersonaCard) -> str:
    """@brief 身份行：职业、年龄、居住方式。"""
    identity = card.identity
    return f"{identity.occupation}，{identity.age}岁，{identity.living_arrangement}。"


def _situation_line(card: PersonaCard) -> str:
    """@brief 当下处境行：本周在忙什么、在意什么。"""
    return f"本周在忙{card.situation.busy_this_week}；在意{card.situation.cares_about}。"


def intimacy_text(intimacy: int) -> str:
    """@brief 亲密度 0-30 → 四档中文（≥22 很亲近 / ≥14 较亲近 / ≥7 一般 / 疏远）。"""
    if intimacy >= 22:
        return "很亲近"
    if intimacy >= 14:
        return "较亲近"
    if intimacy >= 7:
        return "一般"
    return "疏远"


def _relationship_item(relationship: Relationship) -> str:
    """@brief 单条关系：称谓（角色，亲密度档，渠道）。"""
    channels = "、".join(relationship.channels)
    return (f"{relationship.name_or_appellation}（{relationship.role}，"
            f"{intimacy_text(relationship.intimacy)}，{channels}联系）")


def _relationship_line(card: PersonaCard) -> str:
    """@brief 关系行：亲近的人列表；人设表没有写任何关系时写「无」。"""
    if not card.relationships:
        return "亲近的人：无。"
    items = "、".join(_relationship_item(item) for item in card.relationships)
    return f"亲近的人：{items}。"


def _rhythm_line(card: PersonaCard) -> str:
    """@brief 作息行：时间锚点、睡眠中点与睡眠需要，时刻写到分钟；下班、到家锚点各自非空时按此顺序各追加一个分句
    （「；下班<时段>」「；到家<时段>」），都为 null 时没有这两个分句（docs/design/06-agent.md「人物身份与画像」）。"""
    anchors = card.time_anchors
    work = (("下班", anchors.leave_work), ("到家", anchors.arrive_home))
    clauses = "".join(f"；{label}{window_text(window)}" for label, window in work if window is not None)
    return (f"约{window_text(anchors.wake)}起床、{window_text(anchors.leave_home)}出门；"
            f"午餐{window_text(anchors.lunch)}、晚餐{window_text(anchors.dinner)}；"
            f"就寝{window_text(anchors.bedtime)}，睡眠中点约{hour_text(card.chronotype_midsleep)}，"
            f"需睡{card.sleep_need_hours:g}小时{clauses}。")


def _habit_item(habit: HabitSignature) -> str:
    """@brief 单条习惯签名：trigger→response（窗口）。"""
    return f"{habit.trigger}→{habit.response}（{window_text(habit.time_window)}）"


def _habit_line(card: PersonaCard) -> str:
    """@brief 习惯行：if-then 签名列表。"""
    if not card.habits:
        return "习惯：无明确习惯签名。"
    items = "；".join(_habit_item(habit) for habit in card.habits)
    return f"习惯：{items}。"


def _taste_line(card: PersonaCard) -> str:
    """@brief 口味行：内容题材、饮食喜好、常用媒介（空条目跳过）。"""
    parts = []
    if card.tastes.content_genres:
        parts.append(f"爱看{'、'.join(card.tastes.content_genres)}")
    if card.tastes.food_preferences:
        parts.append(f"喜欢吃{'、'.join(card.tastes.food_preferences)}")
    if card.tastes.media:
        parts.append(f"常用{'、'.join(card.tastes.media)}")
    return f"口味：{'；'.join(parts)}。" if parts else "口味：无特别偏好。"
