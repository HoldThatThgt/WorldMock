"""@brief 人物卡片的承载结构，以及由人设表直接构造卡片（docs/design/06-agent.md「人物身份与画像」）。

人物卡片是数据集输入：身份、关系、当下处境、口味与应对方式照搬 persona.toml，作息的每个时段成为一个时间锚点，
习惯签名的时段换成同一种时段对象。构造不调用模型，没有缺省值，不取整、不平移、不检查时间先后；
取值范围只在加载 persona.toml 时由 worldmock.input_models 检查一次，本模块的结构只规定形状。
卡片经确定性渲染成不超过 600 汉字的中文文本，并以结构化形式（去掉当下处境）保存在人物工作区。
"""

from __future__ import annotations

from pydantic import Field

from worldmock.foundation.models import FrozenModel
from worldmock.configuration.inputs.models import Identity, PersonaConfig, Relationship, Situation, Tastes

#: 卡片渲染汉字上限（docs/design/06-agent.md「人物身份与画像」，与 life/narration.py 同源常数；不互相
#: import，
#: 两处同时钉死同一规格行）。
PERSONA_CARD_HANZI_BUDGET = 600


class TimeWindow(FrozenModel):
    """@brief 时段：自午夜起的小时数，照人设表写的原值保存；大于 24 的小时数表示越过午夜，显示时按钟面读。"""

    start_hour: float
    end_hour: float


class TimeAnchors(FrozenModel):
    """@brief 时间锚点：起床、出门、午餐、晚餐、就寝五个时段，另加可空的下班、到家时段。

    七个键都必须出现；leave_work、arrive_home 为 null 表示人设表没有写、人物没有固定的下班或到家时刻。
    锚点之间不设任何先后关系。
    """

    wake: TimeWindow
    leave_home: TimeWindow
    lunch: TimeWindow
    dinner: TimeWindow
    bedtime: TimeWindow
    leave_work: TimeWindow | None
    arrive_home: TimeWindow | None


class HabitSignature(FrozenModel):
    """@brief 习惯签名 {trigger, response, time_window}（Mischel–Shoda if-then 签名）。"""

    trigger: str
    response: str
    time_window: TimeWindow


class PersonaCard(FrozenModel):
    """@brief 结构化人物卡片：由人设表逐字段构造（docs/design/06-agent.md「人物身份与画像」）。"""

    identity: Identity
    relationships: tuple[Relationship, ...]
    situation: Situation
    time_anchors: TimeAnchors
    chronotype_midsleep: float
    sleep_need_hours: float
    habits: tuple[HabitSignature, ...]
    tastes: Tastes
    coping: str


class PersonaCardStruct(PersonaCard):
    """@brief 人物 profile 中的结构化卡片 persona_card_struct：PersonaCard 去掉当下处境（situation 不进提示）。

    新建运行时由卡片导出（persona_card_struct），人设层读取时按本模型核对形状
    （docs/design/06-agent.md「提示与工具」）。
    """

    situation: None = Field(default=None, exclude=True)


def persona_card(persona: PersonaConfig) -> PersonaCard:
    """@brief 人设表 → 人物卡片：逐字段照搬，不调用模型、不补缺省值、不改写任何数。

    @param persona 已通过加载检查的人设表
    @return 人物卡片；没有写的下班、到家时段为 null
    """
    anchors = TimeAnchors(
        wake=_window(persona.wake_window), leave_home=_window(persona.leave_home_window),
        lunch=_window(persona.lunch_window), dinner=_window(persona.dinner_window),
        bedtime=_window(persona.bedtime_window), leave_work=_optional_window(persona.leave_work_window),
        arrive_home=_optional_window(persona.arrive_home_window))
    habits = tuple(HabitSignature(trigger=item.trigger, response=item.response,
                                  time_window=_window(item.time_window)) for item in persona.habits)
    return PersonaCard(
        identity=persona.identity, relationships=tuple(persona.relationships), situation=persona.situation,
        time_anchors=anchors, chronotype_midsleep=persona.chronotype_midsleep,
        sleep_need_hours=persona.sleep_need_hours, habits=habits, tastes=persona.tastes, coping=persona.coping)


def persona_card_struct(card: PersonaCard) -> dict:
    """@brief 卡片 → profile 保存的结构化字段（JSON 形状，去掉 situation）。"""
    return card.model_dump(mode="json", exclude={"situation"})


def _window(window: tuple[float, float]) -> TimeWindow:
    """@brief 人设表的 [起点, 终点] → 时段对象，数值原样保存。"""
    return TimeWindow(start_hour=window[0], end_hour=window[1])


def _optional_window(window: tuple[float, float] | None) -> TimeWindow | None:
    """@brief 可省略的时段（下班、到家）：没有写时为 None。"""
    return None if window is None else _window(window)
