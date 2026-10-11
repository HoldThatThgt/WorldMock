"""@brief 单人人设与运行配置的严格 TOML 输入模型。"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from worldmock.base_models import DeepFrozenModel

_LOGGER = logging.getLogger(__name__)

#: 输入明确拒绝的已删除人设模板字段。
LEGACY_TEMPLATE_FIELDS = frozenset({
    "occupation", "routine_preference", "consumption_preference", "risk_preference",
})


class InputModel(DeepFrozenModel):
    """@brief 输入保留真实 TOML 类型并拒绝未知字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _invalid(reason: str) -> None:
    """@brief 输入异常以英文记录后交给所属文件的登记错误处理。"""
    _LOGGER.error("Environment input is invalid: %s", reason)
    raise ValueError(reason)


class BodyInitialConfig(InputModel):
    """@brief 人物声明的身体质量、能量与睡眠债初值。"""

    mass_kg: float = Field(gt=0, le=300, allow_inf_nan=False)
    initial_energy_kcal: float = Field(ge=0, le=2400, allow_inf_nan=False)
    initial_sleep_debt_hours: float = Field(ge=0, allow_inf_nan=False)


class WorkShift(InputModel):
    """@brief 当日半开班次与按分钟声明的休息时段。"""

    starts: int = Field(ge=0, lt=1440)
    ends: int = Field(gt=0, le=1440)
    breaks: list[list[int]]

    @model_validator(mode="after")
    def validate_shift(self) -> WorkShift:
        """@brief 班次和休息区间有正长度、升序且互不重叠。"""
        if self.starts >= self.ends:
            _invalid("work shift must satisfy 0 <= starts < ends <= 1440")
        if any(len(pair) != 2 or not self.starts <= pair[0] < pair[1] <= self.ends for pair in self.breaks):
            _invalid("work shift breaks must lie within the shift")
        if any(left[1] > right[0] for left, right in zip(self.breaks, self.breaks[1:])):
            _invalid("work shift breaks must be ascending and must not overlap")
        return self


class WorkSchedule(InputModel):
    """@brief 按地点显示名声明的工作安排，未声明的星期不当班。"""

    workplace: str = Field(min_length=1, pattern=r"\S")
    weekly: dict[str, WorkShift]
    exceptions: dict[str, WorkShift | list[WorkShift]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_schedule(self) -> WorkSchedule:
        """@brief 星期使用既有零至六，日期覆盖与休息日期不得冲突。"""
        if set(self.weekly) - set("0123456"):
            _invalid("work_schedule weekly contains an invalid weekday")
        for value in self.exceptions:
            if not _canonical_date(value):
                _invalid("work_schedule exception dates must use canonical ISO format")
        if any(isinstance(shift, (list, tuple)) and shift for shift in self.exceptions.values()):
            _invalid("work_schedule exceptions must contain a shift or an empty list")
        return self


def _canonical_date(value: str) -> bool:
    """@brief 判断日期是否为规范的 ISO 日历日期。"""
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        _LOGGER.error("Calendar date is invalid: %s", value)
        return False


def _hour_window(window: list[float]) -> list[float]:
    """@brief 时段只检查一条：恰好两个有限的小时数，起点小于终点；不检查范围、跨度或与其他时段的关系。"""
    if len(window) != 2 or any(not math.isfinite(value) for value in window) or not window[0] < window[1]:
        _invalid("must be two finite hours with start < end")
    return window


#: 时段：自午夜起的小时数 [起点, 终点]，越过午夜的时刻写成大于 24 的小时数。
HourWindow = Annotated[list[float], AfterValidator(_hour_window)]
#: 口味数组与联系渠道数组里的一项：非空白文字，用词由数据集决定。
NonBlankText = Annotated[str, Field(pattern=r"\S")]


class Identity(InputModel):
    """@brief 人物卡片的身份：年龄、职业、居住方式。年龄不进任何计算，只要求是严格整数，不设范围。"""

    age: int
    occupation: str = Field(min_length=1, max_length=20)
    living_arrangement: str = Field(min_length=1, max_length=30)


class Situation(InputModel):
    """@brief 人物卡片的当下处境：本周在忙什么、在意什么。"""

    busy_this_week: str = Field(min_length=1, max_length=60)
    cares_about: str = Field(min_length=1, max_length=60)


class Relationship(InputModel):
    """@brief 人物卡片的一条关系：姓名或称谓、角色、亲密度（0 到 30）与一到三个联系渠道。

    渠道的用词不设闭集，每项只要求非空白；卡片文本把关系写成「渠道、渠道联系」，所以至少一项，至多三项是提示容量的条数上限。
    """

    name_or_appellation: str = Field(min_length=1, max_length=12)
    role: str = Field(min_length=1, max_length=12)
    intimacy: int = Field(ge=0, le=30)
    channels: list[NonBlankText] = Field(min_length=1, max_length=3)


class HabitDeclaration(InputModel):
    """@brief 人设表里的一条习惯签名：情境、反应与写成两个小时数的时段。"""

    trigger: str = Field(min_length=1, max_length=30)
    response: str = Field(min_length=1, max_length=40)
    time_window: HourWindow


class Tastes(InputModel):
    """@brief 人物卡片的口味：内容题材、饮食喜好、常用媒介，三个数组都可以为空。"""

    content_genres: list[NonBlankText] = Field(max_length=6)
    food_preferences: list[NonBlankText] = Field(max_length=6)
    media: list[NonBlankText] = Field(max_length=4)


class PersonaConfig(InputModel):
    """@brief 唯一人物的人设表：位置与初始资源、工作安排，以及由数据集直接填写的完整人物卡片。

    卡片字段没有缺省值；下班、到家两个时段不写表示没有固定时刻。时段之间不做任何先后或重叠检查。
    """

    schema_version: Literal[2]
    agent_id: int = Field(gt=0)
    name: str = Field(min_length=1, pattern=r"\S")
    background_story: str = Field(min_length=1)
    health_habits: str | None = None
    social_style: str | None = None
    home_location: str = Field(min_length=1, pattern=r"\S")
    initial_location: str = Field(min_length=1, pattern=r"\S")
    initial_balance_fen: int = Field(ge=0)
    initial_relaxation_need: float = Field(ge=0, le=1, allow_inf_nan=False)
    initial_social_need: float = Field(ge=0, le=1, allow_inf_nan=False)
    social_need_growth_per_hour: float = Field(ge=0.02, le=0.10, allow_inf_nan=False)
    body: BodyInitialConfig
    work_schedule: WorkSchedule | None = None
    chronotype_midsleep: float = Field(ge=0, lt=24, allow_inf_nan=False)
    sleep_need_hours: float = Field(ge=7, le=9, allow_inf_nan=False)
    wake_window: HourWindow
    leave_home_window: HourWindow
    lunch_window: HourWindow
    dinner_window: HourWindow
    bedtime_window: HourWindow
    leave_work_window: HourWindow | None = None
    arrive_home_window: HourWindow | None = None
    identity: Identity
    situation: Situation
    relationships: list[Relationship] = Field(max_length=4)
    habits: list[HabitDeclaration] = Field(max_length=5)
    tastes: Tastes
    coping: str = Field(min_length=1, max_length=60)


class CalendarConfig(InputModel):
    """@brief 运行日历沿用特殊日期和排班核对事实。"""

    spring_festival_dates: list[str]
    makeup_work_dates: list[str]
    overtime_opportunity_dates: list[str]
    sick_leave_date: str
    personal_leave_date: str
    social_dinner_date: str

    @model_validator(mode="after")
    def validate_dates(self) -> CalendarConfig:
        """@brief 日期规范且同一日期列表无重复。"""
        groups = (self.spring_festival_dates, self.makeup_work_dates, self.overtime_opportunity_dates)
        for values in groups:
            if len(set(values)) != len(values) or any(not _canonical_date(value) for value in values):
                _invalid("calendar date lists must contain unique canonical ISO dates")
        for value in (self.sick_leave_date, self.personal_leave_date, self.social_dinner_date):
            if not _canonical_date(value):
                _invalid("calendar dates must use canonical ISO format")
        return self


class ModelConfig(InputModel):
    """@brief 非密模型身份、凭据引用、权威元数据地址与各调用预算。"""

    provider: Literal["openai_compatible"]
    base_url: str = Field(min_length=1)
    requested_model: str = Field(min_length=1, pattern=r"\S")
    api_key_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    metadata_url: str = Field(min_length=1)
    context_window: int = Field(ge=262144)
    max_retries: int = Field(default=0, ge=0, le=10)
    transport_max_retries: int = Field(default=3, ge=0, le=10)
    max_react_turns: int = Field(default=16, ge=1, le=64)
    reflection_validation_max_retries: int = Field(default=3, ge=0, le=5)
    day_plan_validation_max_retries: int = Field(default=3, ge=0, le=5)


class RunConfig(InputModel):
    """@brief 一次单人运行的时区、时间、唯一种子、节律与模型配置；结构版本是 2，写其他值的文件直接拒绝。"""

    schema_version: Literal[2]
    seed: int
    timezone: str = Field(min_length=1)
    start_time: datetime
    stop_time: datetime
    activity_feedback_seconds: int = Field(ge=60, le=86400)
    reconsideration_interval_seconds: int = Field(default=5400, gt=0, le=86400)
    progress_report_interval_seconds: int = Field(default=1800, gt=0)
    candidate_presentation_limit: int = Field(default=20, gt=0, le=1000)
    rotation_quota_ratio: float = Field(default=0.15, gt=0, lt=1, allow_inf_nan=False)
    rotation_stale_seconds: int = Field(gt=0, le=31536000)
    calendar: CalendarConfig
    model: ModelConfig

    @model_validator(mode="after")
    def validate_times(self) -> RunConfig:
        """@brief 带偏移原生日期时间按自身偏移整点，偏移与运行时区一致。"""
        for name in ("start_time", "stop_time"):
            value = getattr(self, name)
            if value.utcoffset() is None:
                _invalid(f"{name} must be timezone-aware")
            if value.minute or value.second or value.microsecond:
                _invalid("start_time and stop_time must fall on whole hours")
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            _invalid("run time interval or timezone is invalid")
        if self.stop_time <= self.start_time or any(
            value.utcoffset() != value.astimezone(zone).utcoffset() for value in (self.start_time, self.stop_time)
        ):
            _invalid("run time interval or timezone is invalid")
        return self


class ModelMetadata(InputModel):
    """@brief 已从实际模型服务核验的上下文容量和输出上限。"""

    requested_model: str
    metadata_url: str
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_output(self) -> ModelMetadata:
        """@brief 输出上限不得超过输入加输出的总容量。"""
        if self.max_output_tokens > self.context_window:
            _invalid("model output limit exceeds the context window")
        return self
