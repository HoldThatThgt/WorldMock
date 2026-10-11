"""@brief 单人人设与运行配置的严格 TOML 输入模型。"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, Field, field_validator, model_validator

from worldmock.base_models import DeepFrozenModel

_LOGGER = logging.getLogger(__name__)

#: 输入明确拒绝的已删除人设模板字段。
LEGACY_TEMPLATE_FIELDS = frozenset({
    "occupation", "routine_preference", "consumption_preference", "risk_preference",
})
#: 就寝时段的上界（次日 06:00）；起点小于它减 24 时读作次日凌晨。
BEDTIME_HORIZON_HOURS = 30


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


class PersonaConfig(InputModel):
    """@brief 唯一人物的身份、生活事实、位置与初始资源。"""

    schema_version: Literal[1]
    agent_id: int = Field(gt=0)
    name: str = Field(min_length=1, pattern=r"\S")
    background_story: str
    health_habits: str | None = None
    social_style: str | None = None
    home_location: str = Field(min_length=1, pattern=r"\S")
    initial_location: str = Field(min_length=1, pattern=r"\S")
    initial_balance_fen: int = Field(ge=0)
    initial_relaxation_need: float = Field(ge=0, le=1, allow_inf_nan=False)
    initial_social_need: float = Field(ge=0, le=1, allow_inf_nan=False)
    body: BodyInitialConfig
    work_schedule: WorkSchedule | None = None
    chronotype_midsleep: float = Field(default=4.0, ge=0, lt=24, allow_inf_nan=False)
    sleep_need_hours: float = Field(default=7.5, ge=7, le=9, allow_inf_nan=False)
    lunch_window: list[float] | None = None
    dinner_window: list[float] | None = None
    leave_work_window: list[float] | None = None
    arrive_home_window: list[float] | None = None
    bedtime_window: list[float] | None = None

    @field_validator("background_story")
    @classmethod
    def validate_story(cls, value: str) -> str:
        """@brief 正文去掉首尾空白后至少一百字符。"""
        if len(value.strip()) < 100:
            _invalid("background_story must contain at least 100 characters")
        return value

    @model_validator(mode="after")
    def validate_windows(self) -> PersonaConfig:
        """@brief 先按午饭、晚饭、下班、到家、就寝检查每个时段自身的形状，再检查声明值之间的成对关系。"""
        names = ("lunch_window", "dinner_window", "leave_work_window", "arrive_home_window", "bedtime_window")
        for name in names:
            window = getattr(self, name)
            if window is not None:
                _check_window(name, window)
        _check_window_pairs(self)
        return self


def _check_window(name: str, window: list[float]) -> None:
    """@brief 检查有限两端点、日内起点和窗口跨度。"""
    maximum = BEDTIME_HORIZON_HOURS if name == "bedtime_window" else 24
    if len(window) != 2 or any(not math.isfinite(value) for value in window):
        _invalid(f"{name} must contain two finite hours")
    if not 0 <= window[0] < 24 or not window[0] < window[1] <= maximum:
        _invalid(f"{name} must satisfy 0 <= start < end <= {maximum}")
    if window[1] - window[0] > 12:
        _invalid(f"{name} span must not exceed 12 hours")


def _check_window_pairs(persona: PersonaConfig) -> None:
    """@brief 两个声明值不可能同时写进卡片时拒绝；次序固定，只报告第一条不成立的。"""
    lunch, dinner = persona.lunch_window, persona.dinner_window
    leave_work, arrive_home = persona.leave_work_window, persona.arrive_home_window
    bedtime = _bedtime_start(persona.bedtime_window)
    if leave_work and arrive_home and leave_work[0] > arrive_home[0]:
        _invalid("leave_work_window must not start after arrive_home_window")
    if dinner and bedtime is not None and bedtime < dinner[1]:
        _invalid("bedtime_window must not overlap dinner window")
    if lunch and dinner and lunch[1] > dinner[0]:
        _invalid("lunch_window must end before dinner_window starts")
    if lunch and bedtime is not None and lunch[0] > bedtime:
        _invalid("bedtime_window must not start before lunch_window")
    if leave_work and bedtime is not None and leave_work[0] > bedtime:
        _invalid("leave_work_window must not start after bedtime_window")
    if arrive_home and bedtime is not None and arrive_home[0] > bedtime:
        _invalid("arrive_home_window must not start after bedtime_window")


def _bedtime_start(window: list[float] | None) -> float | None:
    """@brief 就寝起点的次日读法：起点落在午夜到就寝上界之间时读作次日凌晨，比较时加 24；没有声明为 None。"""
    if window is None:
        return None
    return window[0] + 24 if window[0] < BEDTIME_HORIZON_HOURS - 24 else window[0]


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
    persona_validation_max_retries: int = Field(default=2, ge=0, le=5)
    reflection_validation_max_retries: int = Field(default=3, ge=0, le=5)
    day_plan_validation_max_retries: int = Field(default=3, ge=0, le=5)


class RunConfig(InputModel):
    """@brief 一次单人运行的时区、时间、唯一种子、节律与模型配置。"""

    schema_version: Literal[1]
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
