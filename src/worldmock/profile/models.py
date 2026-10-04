"""@brief 画像编译结构合同（卡片结构 3）：PersonaCard、大五与世界层动力学（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」）。

PersonaCard 从 background_story 抽取（三次一致性口径），承载身份、关系、当下
处境、时间锚点（五个有序锚点与可空的下班、到家锚点，docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-下班与到家锚点 ）、时型、习惯签名、口味与应对方式；经确定性渲染成不超过 6
00
汉字的中文文本进入提示。大五仍抽取，但只进入 WorldDynamics（世界层动力学）：
神经质→情绪脉冲幅度与半衰期、外向性→社交需要增长率与交谈缓解量、开放性→
内部评分器多样性权重、宜人性→接受邀约与回复消息倾向、尽责性→内部评分器
现时偏好。大五分数、旧九参数与任何权重不得进入提示（渲染层黑名单保障）。
旧模板字段 occupation/routine_preference/consumption_preference/risk_preference
删除不保留别名（其内容并入卡片），编译旧档案明确报错。
"""

from __future__ import annotations

import hashlib
import math
from typing import Literal

from pydantic import (BaseModel, ConfigDict, Field, field_validator,
                      model_validator)

#: 卡片合同 schema 版本：3 在时间锚点上增必填可空的下班、到家锚点；更早的结构（含 v1 九参数动机档案）一律拒绝，
#: 不保留兼容。
SCHEMA_VERSION = 3
#: 卡片抽取提示版本（换提示词必须换版本号）。
PROMPT_VERSION = "card-v3"
#: 卡片渲染汉字上限（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「字符预算」，与 life/narration.py 同源常数；不互相
#: import，
#: 两处同时钉死同一规格行）。
PERSONA_CARD_HANZI_BUDGET = 600

BIG_FIVE_DIMENSIONS = ("openness", "conscientiousness", "extraversion",
                       "agreeableness", "neuroticism")
#: 已删除的旧模板字段（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」）：出现在输入档案即明确报错，不静默兼容。
LEGACY_TEMPLATE_FIELDS = frozenset({
    "occupation", "routine_preference", "consumption_preference", "risk_preference"})

#: 睡眠需要小时数区间（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」，合同常数；与 life/sleep.py 同口径）。
SLEEP_NEED_MIN_H = 7.0
SLEEP_NEED_MAX_H = 9.0
DEFAULT_SLEEP_NEED_H = 7.5
#: 时型（自由日睡眠中点）默认 04:00（Roenneberg MCTQ 典型中点，experiment_assumption）。
DEFAULT_CHRONOTYPE_MIDSLEEP_H = 4.0
#: 亲密度 0-30 整数刻度（Humanoid Agents closeness 0-30，
#: docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」联系人同刻度）。
INTIMACY_MIN = 0
INTIMACY_MAX = 30

#: 列表字段上限：约束抽取规模，配合渲染 600 汉字预算（合同常数）。
MAX_RELATIONSHIPS = 4
MAX_HABITS = 5
MAX_CONTENT_GENRES = 6
MAX_FOOD_PREFERENCES = 6
MAX_MEDIA = 4

_GRID_TOLERANCE = 1e-6


def value_on_grid(value: float, low: float, high: float, step: float) -> bool:
    """@brief 公共格点判定：值是否落在 [low, high] 内步进 step 的格点上。"""
    if not math.isfinite(value) or value < low - _GRID_TOLERANCE or value > high + _GRID_TOLERANCE:
        return False
    units = (value - low) / step
    return abs(units - round(units)) <= _GRID_TOLERANCE


def quantize(value: float, low: float, high: float, step: float) -> float:
    """@brief 吸附到 [low, high] 内最近格点（越界先钳制），返回规范十进制表示。"""
    clamped = min(max(float(value), low), high)
    units = round((clamped - low) / step)
    return round(low + units * step, 10)


def parse_clock(value: str) -> float:
    """@brief "HH:MM" → 自午夜起的小时数（"23:30" → 23.5）；非字符串/格式错误即 ValueError。"""
    if not isinstance(value, str):
        raise ValueError(f"invalid clock value {value!r}, expected HH:MM string")
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid clock value {value!r}, expected HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 24 and 0 <= minute <= 59):
        raise ValueError(f"invalid clock value {value!r}")
    return hour + minute / 60.0


class _Frozen(BaseModel):
    """@brief 拒绝未知字段且不可变，序列化即合同。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeWindow(_Frozen):
    """@brief 时间窗口：自午夜起的小时数，半开区间 [start, end)。

    就寝窗口允许跨午夜（end 可大于 24，如 23–25 表示到次日 01:00）；锚点
    回绕归一（TimeAnchors.normalize_wraparound）后 start 亦可大于 24
    （25 表示次日 01:00），上限 30 小时（次日 06:00 地平线）。窗口跨度
    上限 12 小时防止病态区间。
    """

    start_hour: float = Field(ge=0.0, lt=30.0)
    end_hour: float = Field(gt=0.0, le=30.0)

    @model_validator(mode="after")
    def validate_window(self) -> "TimeWindow":
        """@brief 0 ≤ start < 30、跨度 (0, 12]；小时吸附 0.5 格点保证确定性渲染。"""
        for name, value in (("start_hour", self.start_hour), ("end_hour", self.end_hour)):
            if not value_on_grid(value, 0.0, 30.0, 0.5):
                raise ValueError(f"TimeWindow.{name}={value} is off the 0.5-hour grid")
        if not self.start_hour < self.end_hour:
            raise ValueError(f"window must satisfy start < end, got {self.start_hour}..{self.end_hour}")
        if self.end_hour - self.start_hour > 12.0:
            raise ValueError(f"window span {self.end_hour - self.start_hour}h exceeds 12h")
        return self

    @classmethod
    def from_clock_range(cls, value: str) -> "TimeWindow":
        """@brief "HH:MM-HH:MM" → TimeWindow；跨午夜（end < start）自动 +24。

        非字符串（含 null）输入是抽取 payload 合同违规：抛 ValueError
        （而非 AttributeError），使其落入抽取器无效 payload 重试路径。
        """
        if not isinstance(value, str):
            raise ValueError(f"invalid time range {value!r}, "
                             f"expected HH:MM-HH:MM string")
        parts = value.strip().split("-")
        if len(parts) != 2:
            raise ValueError(f"invalid time range {value!r}, expected HH:MM-HH:MM")
        start = parse_clock(parts[0])
        end = parse_clock(parts[1])
        if end <= start:
            end += 24.0
        rounded_start = quantize(start, 0.0, 24.0, 0.5)
        rounded_end = quantize(end, 0.0, 30.0, 0.5)
        if rounded_start == rounded_end and 0 < end - start <= 0.5:
            # 短窗口不能因最近格点吸附变成零长窗口。
            rounded_start = math.floor(start * 2) / 2
            rounded_end = math.ceil(end * 2) / 2
        return cls(start_hour=rounded_start, end_hour=rounded_end)


def settle_work_anchors(
        leave_home: TimeWindow, bedtime: TimeWindow, leave_work: TimeWindow | None,
        arrive_home: TimeWindow | None) -> tuple[TimeWindow | None, TimeWindow | None, tuple[str, ...]]:
    """@brief 下班与到家锚点按固定顺序核对（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-下班与到家锚点 ）：不合顺序约束或越过当日午夜的置 null。

    先核 leave_work（出门起点 ≤ 下班起点 ≤ 就寝起点），再核 arrive_home（仍有效的下班起点，下班为 null 时取出门起点，
    ≤ 到家起点 ≤ 就寝起点）；就寝取已回绕归一的窗口，与午餐、晚餐不设先后。首版不支持跨午夜的下班与到家：先后合格
    的锚点，窗口终点还须不晚于当日 24 点（与场景窗口 0 ≤ 起点 < 终点 ≤ 24 同一口径）。卡片校验器与抽取的降级共用
    本函数，所以「校验器接受」恰好等于「降级不改变任何值」。
    @return (核对后的下班, 核对后的到家, 逐个被置 null 的锚点各一条英文说明，按核对顺序)
    """
    high = bedtime.start_hour
    problems: list[str] = []
    problem = _work_anchor_problem("leave_work", leave_work, ("leave_home", leave_home), high)
    if problem is not None:
        problems.append(problem)
        leave_work = None
    lower = ("leave_home", leave_home) if leave_work is None else ("leave_work", leave_work)
    problem = _work_anchor_problem("arrive_home", arrive_home, lower, high)
    if problem is not None:
        problems.append(problem)
        arrive_home = None
    return leave_work, arrive_home, tuple(problems)


def _work_anchor_problem(name: str, window: TimeWindow | None, lower: tuple[str, TimeWindow],
                         high: float) -> str | None:
    """@brief 一个下班或到家锚点的英文问题说明：起点不在下界起点与就寝起点之间，或（先后合格时）窗口终点越过当日
    24 点；锚点为 null 或合格时为 None。
    @param lower (下界锚点名, 下界窗口)
    @param high 归一后的就寝起点
    """
    if window is None:
        return None
    label, bound = lower
    if not bound.start_hour <= window.start_hour <= high:
        return f"{name} start {window.start_hour:g} is outside {label} {bound.start_hour:g}..bedtime {high:g}"
    if window.end_hour > 24.0:
        return f"{name} window {window.start_hour:g}..{window.end_hour:g} runs past midnight"
    return None


class Identity(_Frozen):
    """@brief 身份：年龄、职业、居住方式。"""

    age: int = Field(ge=16, le=100)
    occupation: str = Field(min_length=1, max_length=20)
    living_arrangement: str = Field(min_length=1, max_length=30)


class Relationship(_Frozen):
    """@brief 关系：姓名或称谓、角色、亲密度（0-30）、可用渠道。"""

    name_or_appellation: str = Field(min_length=1, max_length=12)
    role: str = Field(min_length=1, max_length=12)
    intimacy: int = Field(ge=INTIMACY_MIN, le=INTIMACY_MAX)
    channels: tuple[str, ...] = Field(min_length=1, max_length=3)

    @field_validator("channels")
    @classmethod
    def validate_channels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """@brief 渠道非空字符串。"""
        if any(not channel.strip() for channel in value):
            raise ValueError("relationship channels must not be blank")
        return value


class Situation(_Frozen):
    """@brief 当下处境：本周在忙什么、在意什么。"""

    busy_this_week: str = Field(min_length=1, max_length=60)
    cares_about: str = Field(min_length=1, max_length=60)


class TimeAnchors(_Frozen):
    """@brief 时间锚点：起床、出门、午餐、晚餐、就寝五个有序窗口，另加可空的下班、到家窗口。

    构造时做确定性回绕归一：单调递增序列后出现的回绕小值（如 …18.5, 1.0
    的「凌晨一点才睡」）按跨午夜惯例 +24 读作次日锚点（1.0→25.0），与
    TimeWindow.from_clock_range 的跨午夜 +24 同构，归一结果落进卡片值。
    +24 后越过 30 小时地平线的是同日乱序而非回绕，仍由顺序校验拒绝。
    leave_work、arrive_home 必填无缺省，null 表示没有固定时刻或故事未提及；两者不进回绕归一的名字元组，
    也不进五锚点的全序校验，另由 validate_work_anchor_order 按 settle_work_anchors 的四条顺序约束与当日午夜上界
    校验（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-下班与到家锚点 ）。
    """

    wake: TimeWindow
    leave_home: TimeWindow
    lunch: TimeWindow
    dinner: TimeWindow
    bedtime: TimeWindow
    leave_work: TimeWindow | None
    arrive_home: TimeWindow | None

    @model_validator(mode="before")
    @classmethod
    def normalize_wraparound(cls, values: object) -> object:
        """@brief 回绕归一：递增序列尾部的回绕小锚点 +24 落成次日锚点（写进字段值）。

        模型对「凌晨一点才睡」稳定输出 1.0-2.0 式同日窗口（提示层约束修不动）；
        跟在大值锚点后的小值唯一合理读法是次日。+24 后 start ≥ 30 或
        end > 30 的序列不是回绕而是同日乱序：原样放行交 validate_order 按
        原错误拒绝；结构不完整的载荷同样原样放行交字段校验报结构错误。
        """
        if not isinstance(values, dict):
            return values
        names = ("wake", "leave_home", "lunch", "dinner", "bedtime")
        raw: list[tuple[float, float]] = []
        for name in names:
            item = values.get(name)
            if isinstance(item, TimeWindow):
                raw.append((item.start_hour, item.end_hour))
            elif isinstance(item, dict):
                try:
                    raw.append((float(item["start_hour"]), float(item["end_hour"])))
                except (KeyError, TypeError, ValueError):
                    return values
            else:
                return values
        shifts = [0.0] * len(raw)
        previous: float | None = None
        for index, (start, end) in enumerate(raw):
            while previous is not None and start + shifts[index] < previous:
                shifts[index] += 24.0
            if start + shifts[index] >= 30.0 or end + shifts[index] > 30.0:
                return values
            previous = start + shifts[index]
        if not any(shifts):
            return values
        rewritten = dict(values)
        for name, (start, end), shift in zip(names, raw, shifts):
            if shift:
                rewritten[name] = TimeWindow(start_hour=start + shift,
                                             end_hour=end + shift)
        return rewritten

    @model_validator(mode="after")
    def validate_order(self) -> "TimeAnchors":
        """@brief 日内顺序锚点不倒挂：起床 ≤ 出门 ≤ 午餐 ≤ 晚餐 ≤ 就寝（按起点）。

        回绕小值已在归一化为次日锚点；此处拒绝的是归一救不回的同日乱序。
        """
        ordered = (self.wake, self.leave_home, self.lunch, self.dinner, self.bedtime)
        starts = [window.start_hour for window in ordered]
        if starts != sorted(starts):
            raise ValueError(f"time anchors out of order: {starts}")
        return self

    @model_validator(mode="after")
    def validate_work_anchor_order(self) -> "TimeAnchors":
        """@brief 下班与到家的顺序约束与当日午夜上界：settle_work_anchors 报出任一条违反即拒绝（对归一后的出门与就寝
        核对）。"""
        problems = settle_work_anchors(self.leave_home, self.bedtime, self.leave_work, self.arrive_home)[2]
        if problems:
            raise ValueError("work anchors out of order: " + "; ".join(problems))
        return self


class HabitSignature(_Frozen):
    """@brief 习惯签名 {trigger, response, time_window}（Mischel–Shoda if-then 签名）。"""

    trigger: str = Field(min_length=1, max_length=30)
    response: str = Field(min_length=1, max_length=40)
    time_window: TimeWindow


class Tastes(_Frozen):
    """@brief 口味：内容题材、饮食喜好、常用媒介。"""

    content_genres: tuple[str, ...] = Field(max_length=MAX_CONTENT_GENRES)
    food_preferences: tuple[str, ...] = Field(max_length=MAX_FOOD_PREFERENCES)
    media: tuple[str, ...] = Field(max_length=MAX_MEDIA)

    @field_validator("content_genres", "food_preferences", "media")
    @classmethod
    def validate_entries(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """@brief 条目非空字符串且去重（确定性：保持首次出现顺序）。"""
        seen: list[str] = []
        for entry in value:
            if not entry.strip():
                raise ValueError("taste entries must not be blank")
            if entry not in seen:
                seen.append(entry)
        return tuple(seen)


class BigFiveProfile(_Frozen):
    """@brief 大五人格 1-7 整数刻度（只进世界层动力学，不进提示）。"""

    openness: int = Field(ge=1, le=7)
    conscientiousness: int = Field(ge=1, le=7)
    extraversion: int = Field(ge=1, le=7)
    agreeableness: int = Field(ge=1, le=7)
    neuroticism: int = Field(ge=1, le=7)


class PersonaCard(_Frozen):
    """@brief 结构化人物卡片（画像编译 v2 核心合同，docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」）。"""

    identity: Identity
    relationships: tuple[Relationship, ...] = Field(min_length=1, max_length=MAX_RELATIONSHIPS)
    situation: Situation
    time_anchors: TimeAnchors
    chronotype_midsleep: float = Field(ge=0.0, lt=24.0)
    sleep_need_hours: float = Field(default=DEFAULT_SLEEP_NEED_H,
                                    ge=SLEEP_NEED_MIN_H, le=SLEEP_NEED_MAX_H)
    habits: tuple[HabitSignature, ...] = Field(max_length=MAX_HABITS)
    tastes: Tastes
    coping: str = Field(min_length=1, max_length=60)

    @field_validator("chronotype_midsleep", "sleep_need_hours")
    @classmethod
    def validate_hour_grid(cls, value: float) -> float:
        """@brief 时型 0.25 小时格点、睡眠需要 0.25 小时格点（确定性）。"""
        if not value_on_grid(value, 0.0, 24.0, 0.25):
            raise ValueError(f"hour value {value} is off the 0.25-hour grid")
        return value


class PersonaCardStruct(PersonaCard):
    """@brief 人物 profile 中的结构化卡片 persona_card_struct：PersonaCard 去掉当下处境（situation 不进提示）。

    新建运行时由编译卡片导出（persona_card_struct），恢复时按本模型校验；其余字段与校验沿用 PersonaCard
    （docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章「人设层」）。
    """

    situation: None = Field(default=None, exclude=True)


def persona_card_struct(card: PersonaCard) -> dict:
    """@brief 编译卡片 → profile 保存的结构化字段（JSON 形状，去掉 situation）。"""
    return card.model_dump(mode="json", exclude={"situation"})


class WorldDynamics(_Frozen):
    """@brief 大五 → 世界层动力学参数（只供引擎内部使用，不进提示，docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」）。

    神经质→mood_pulse_amplitude/mood_halflife_minutes；外向性→
    social_need_growth_per_hour/conversation_relief_per_hour；开放性→
    diversity_weight；宜人性→invitation_acceptance/reply_tendency；
    尽责性→present_preference。数值映射规则集中声明在
    catalog/rules/persona-mapping.toml [world_dynamics.*]（experiment_assumption）。
    """

    mood_pulse_amplitude: float = Field(ge=0.0, le=1.0)
    mood_halflife_minutes: float = Field(ge=10.0, le=180.0)
    social_need_growth_per_hour: float = Field(gt=0.0, le=0.5)
    conversation_relief_per_hour: float = Field(ge=0.0, le=1.0)
    diversity_weight: float = Field(ge=0.0, le=1.0)
    invitation_acceptance: float = Field(ge=0.0, le=1.0)
    reply_tendency: float = Field(ge=0.0, le=1.0)
    present_preference: float = Field(ge=0.0, le=1.0)


class Consensus(_Frozen):
    """@brief 三次一致性共识统计：运行次数与逐字段平均众数占比。"""

    runs: int = Field(ge=1)
    agreement: float = Field(ge=0.0, le=1.0)


class PersonaSource(_Frozen):
    """@brief 档案溯源：background_story 指纹、提取模型、提示版本与共识。"""

    background_story_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    consensus: Consensus


class CompiledPersona(_Frozen):
    """@brief 编译产物：卡片 + 大五 + 世界层动力学（schema v3；更早的结构明确拒绝）。"""

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    card: PersonaCard
    big_five: BigFiveProfile
    world_dynamics: WorldDynamics
    source: PersonaSource
    warnings: tuple[str, ...] = ()


def background_story_sha256(background_story: str) -> str:
    """@brief background_story 的 sha256 指纹（source.background_story_sha256）。"""
    return hashlib.sha256(background_story.encode("utf-8")).hexdigest()


def normalized_scores(big_five: BigFiveProfile, center: float, span: float) -> dict[str, float]:
    """@brief (score-center)/span 把 1-7 刻度归一到 [-1,1]，供映射层线性组合。"""
    return {dimension: (getattr(big_five, dimension) - center) / span
            for dimension in BIG_FIVE_DIMENSIONS}
