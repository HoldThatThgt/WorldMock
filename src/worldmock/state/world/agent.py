"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations
from worldmock.foundation.models import StrictModel
from typing import Any, Literal
from pydantic import Field, field_serializer, field_validator, model_validator
from worldmock.state.planning.errands import ErrandState
from worldmock.state.planning.schedule import DayPlan

from worldmock.state.decisions.day_session import DaySessionState
from worldmock.state.decisions.suspended import SuspendedLoopState
from worldmock.state.world.kinds import SessionKind, SessionStatus
from worldmock.state.logging import _LOGGER










class BodyState(StrictModel):
    """@brief 身体资源保留能量、双过程睡眠、放松需要与社交需要。"""

    energy_kcal: float = Field(ge=0, le=2400)
    sleep_pressure: float = Field(ge=0, le=1.2)
    sleep_deficit_hours: float = Field(ge=0, le=24)
    sleep_need_hours: float = Field(ge=7.0, le=9.0)
    chronotype_midsleep: float = Field(ge=0.0, lt=24.0)
    social_need: float = Field(ge=0, le=1)
    relaxation_need: float = Field(ge=0, le=1)
    social_need_growth_per_hour: float = Field(ge=0.02, le=0.10)



class ContentProgress(StrictModel):
    """@brief 人物对一个内容项目的实际接触进度。"""

    content_id: str = Field(min_length=1)
    position_seconds: float = Field(ge=0)
    completed_rounds: int = Field(ge=0)
    last_consumed_at: int = Field(ge=0)



class AppPush(StrictModel):
    """@brief 当前地点或路途会话内的一条应用推送及其消费状态。"""

    push_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    issued_at: int = Field(ge=0)
    status: Literal["pending", "opened", "released"]



class SessionState(StrictModel):
    """@brief 一段地点停留或一段路；由系统随出发与到达开始和结束，人物不手动开始或结束会话。

    :param kind: stay 停留时带 location_id；transit 路上时带 origin_id 与 destination_id。
    :param status: active 或 ended；ended 必须含 ended_at。
    :param activity_ids: 归入本会话的活动（暂停的活动回到开始地点时归入那里的新会话）。
    :param name: 人物可读名称，停留「<地点>（<M月D日> HH:MM 起）」，路上「去<目的地>的路上（…起）」；结束后不改名。
    :param opened_by_action_id: 由决策引起的切换（出发、途中结束出行）记下引起切换的动作身份，进度层以该动作结果的
        事件序号为下界；运行开始与到达为 None，下界取开始时刻（docs/design/06-agent.md「进度、待办与记忆」）。
    """

    session_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    kind: SessionKind
    location_id: str | None = None
    origin_id: str | None = None
    destination_id: str | None = None
    status: SessionStatus
    started_at: int = Field(ge=0)
    ended_at: int | None = Field(default=None, ge=0)
    activity_ids: list[str] = Field(default_factory=list)
    name: str = Field(pattern=r"\S")
    opened_by_action_id: str | None = Field(default=None, min_length=1)
    #: 本会话已发出的应用推送；已释放条目仍保留，阻止同应用再次推送。
    app_pushes: dict[str, AppPush] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_session(self) -> "SessionState":
        """@brief 验证种类与地点字段配对、生命周期终态与时间顺序。"""
        stay_fields = self.location_id is not None and self.origin_id is None and self.destination_id is None
        transit_fields = self.location_id is None and self.origin_id is not None and self.destination_id is not None
        if not (stay_fields if self.kind == SessionKind.STAY else transit_fields):
            _LOGGER.error("Session place fields disagree with its kind")
            raise ValueError("session place fields disagree with its kind")
        if (self.status == SessionStatus.ENDED) != (self.ended_at is not None):
            raise ValueError("session ended_at disagrees with its status")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at must not be before started_at")
        return self



class NavigationForeground(StrictModel):
    """@brief 导航 app 正在前台的那一段：所属的出行、这趟出行选定的导航 app 和这一段回到前台的时刻。"""

    #: 所属出行：该人物一项进行中的出行活动。
    activity_id: str = Field(min_length=1)
    #: 这趟出行选定的导航 app。
    app_id: str = Field(min_length=1)
    #: 这一段回到前台的时刻：不早于出行开始，不晚于当前时刻。
    opened_at: int = Field(ge=0)



class AgentState(StrictModel):
    """@brief 单个人物的生活状态、挂起循环与幂等节律簿记。"""

    #: 人物编号。
    agent_id: int = Field(gt=0)
    #: 人物姓名。
    name: str = Field(min_length=1)
    #: 当前锚定或停留的地点标识。
    location_id: str = Field(min_length=1)
    #: 所属资金账户标识。
    balance_account_id: str = Field(min_length=1)
    #: 当前身体数值状态。
    body: BodyState
    #: 最近一次身体结算时钟（UTC 纪元秒）。
    body_settled_at: int = Field(ge=0)
    #: 下一次计划唤醒决策时刻（UTC 纪元秒）。
    next_decision_at: int = Field(ge=0)
    #: 挂起搜索等待恢复时的主循环上下文快照。
    suspended_loop: SuspendedLoopState | None = None
    #: 当日日会话连续上下文状态；跨午夜零点重置与反事实干预时置为 None。
    day_session: DaySessionState | None = None
    #: 各内容的消费位置与完成轮数映射。
    content_progress: dict[str, ContentProgress] = Field(default_factory=dict)
    #: 上一次决策发生的时刻（UTC 纪元秒）。
    last_decision_time: int | None = Field(default=None, ge=0)
    #: 上一次决策时的身体体感分级快照。
    felt_bands_at_last_decision: dict[str, str] = Field(default_factory=dict)
    #: 待消费的唤醒原因列表。
    pending_wake_reasons: list[str] = Field(default_factory=list)
    #: 当日计划清单；未提交或已失效时为 None。
    day_plan: DayPlan | None = None
    #: 已提交过当日计划的本地日期集合。
    plan_submitted_dates: set[str] = Field(default_factory=set)
    #: 尚未完结的个人事务映射。
    errands: dict[str, ErrandState] = Field(default_factory=dict)
    #: 身体不适连续开始时刻（UTC 纪元秒）。
    discomfort_started_at: int | None = Field(default=None, ge=0)
    #: 累计已触发的不适脉冲小时数。
    discomfort_pulse_hours: float = Field(default=0.0, ge=0)
    #: 回调状态当前整数值映射。
    callback_states: dict[str, int] = Field(default_factory=dict)
    #: 已执行过的一次性回调键集合。
    callback_once_keys: set[str] = Field(default_factory=set)
    #: 状态提醒规则上次触发的本地日期映射。
    callback_reminder_days: dict[str, str] = Field(default_factory=dict)
    #: 导航正在前台的那一段；空表示不在前台。它不放进出行活动的私有数据，活动快照是人物可见的。
    navigation_foreground: NavigationForeground | None = None
    #: 商业住宿地点当前有效退房纪元秒（location_id -> checkout_epoch）。
    lodging_stays: dict[str, int] = Field(default_factory=dict)

    @field_validator("lodging_stays", mode="before")
    @classmethod
    def validate_lodging_stays(cls, value: Any) -> Any:
        """@brief 核对商业住宿有效期按非空白地点标识与正整数退房纪元秒映射。"""
        if not isinstance(value, dict):
            _LOGGER.error("Agent lodging_stays must be a mapping")
            raise ValueError("lodging_stays must be a mapping")
        for location_id, checkout_epoch in value.items():
            if not isinstance(location_id, str) or not location_id.strip():
                _LOGGER.error("Agent lodging_stays location_id key must be a non-blank string")
                raise ValueError("lodging_stays location_id keys must be non-blank strings")
            if type(checkout_epoch) is not int or checkout_epoch <= 0:
                _LOGGER.error(
                    "Agent lodging_stays checkout_epoch for %s must be a positive int", location_id)
                raise ValueError("lodging_stays checkout_epoch values must be positive integers")
        return value

    @field_serializer("plan_submitted_dates", "callback_once_keys", when_used="json")
    def serialize_agent_sets(self, value: set[str]) -> list[str]:
        """@brief 集合按稳定次序保存，恢复不依赖进程哈希种子。"""
        return sorted(value)
