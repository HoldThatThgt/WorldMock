"""@brief LifeEnv 的封闭请求、状态与结果模型。"""

from __future__ import annotations
from worldmock.life.social import SOCIAL_RESOURCE_CLAIMS
from worldmock.catalog.affordances import engine_affordances
from worldmock.life.vocabulary import way_options
from worldmock.life.vocabulary import NAMED_PURCHASE_WAYS

from worldmock.base_models import StrictModel, FrozenModel, CurvePoint, EfficiencyFactor
from worldmock.errors import reject_value
import logging
import re
from datetime import date, datetime
from enum import StrEnum
from collections.abc import Callable, Sequence
from typing import Annotated, Any, Literal

from pydantic import (ValidationError, BaseModel, ConfigDict, Field, StrictBool, WithJsonSchema, field_serializer,
                      field_validator, model_validator)

from worldmock.life.errand_state import (ERRAND_NAME_MAX_CHARS, ERRAND_QUANTITY_MAX, ERRAND_QUANTITY_MIN,
                                         ERRAND_TEXT_MAX_CHARS, MAX_ERRAND_CHANGES, ErrandState)
from worldmock.life.schedule import MAX_PLAN_GOALS, PLAN_GOAL_MAX_CHARS, DayPlan, IntentKind
from worldmock.life.social import Message
from worldmock.model_endpoint import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

# 世界状态以调度基础层的预排事件模型保存未来边界。
from worldmock.scheduling.events import ScheduledEvent

#: 世界状态结构版本：版本不符的检查点与导出直接拒绝，不迁移；版本 6 引入三级结构（Task→Session→Activity）与
#: WorldState.sessions；版本 7（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「仍须修复」）起活动快照 location_id
#: 固定为开始地点；
#: 版本 8（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第七章「版本」）新增 SessionState.opened_by_action_id 与
#: ActivityState.paused_at，观察改为七层数据；版本 9（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-事件检查点与恢复 ）新增只存未完成个人事务的
#: AgentState.errands。
WORLD_SCHEMA_VERSION = 10

#: 普通活动的派生时长上限。
DERIVED_DURATION_CAP_SECONDS = 86400
#: 消费活动的合法时长上界；越界拒绝，不截取。
CONSUME_DURATION_CAP_SECONDS = 14400

_LOGGER = logging.getLogger(__name__)









def _require_source_aware(value: datetime) -> None:
    """@brief 来源与时态声明的时刻必须明确时区。"""
    if value.tzinfo is None or value.utcoffset() is None:
        reject_value("Map timestamps must be timezone-aware")


class DataSource(FrozenModel):
    """@brief 一份事实或场景假设的来源、许可与适用边界。"""

    kind: Literal["fact", "scenario"]
    url: str = Field(pattern=r"\S")
    retrieved_at: datetime
    license: str = Field(pattern=r"\S")
    notice: str = Field(pattern=r"\S")
    fact_cutoff: date
    applicability: str = Field(pattern=r"\S")

    @model_validator(mode="after")
    def validate_source(self) -> "DataSource":
        """@brief 来源取得时间必须明确时区。"""
        _require_source_aware(self.retrieved_at)
        if self.fact_cutoff > self.retrieved_at.date():
            reject_value("Source fact cutoff cannot follow retrieval date")
        return self


class OpeningInterval(FrozenModel):
    """@brief 当地自然日起算的营业分钟区间；结束超过午夜表示跨日。"""

    opens: int = Field(ge=0, lt=1440)
    closes: int = Field(gt=0, le=2880)

    @model_validator(mode="after")
    def validate_interval(self) -> "OpeningInterval":
        """@brief 营业区间必须正向且最多延续二十四小时。"""
        if not self.opens < self.closes <= self.opens + 1440:
            reject_value("Opening interval must be positive and at most one day")
        return self


def purpose_only_schema(purpose: str | None = None) -> Callable[[dict[str, Any]], None]:
    """@brief 模型可见参数 Schema 的 description 只写用途（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第二章「删除与去重」）。

    去掉类 docstring 派生的开发说明（规则只在系统层写一次，数量上限由 Schema 与本地校验执行），purpose 非空时写这一句；
    工具参数对象本身不写说明，用途只在工具的 description。用作 ConfigDict 的 json_schema_extra。
    """

    def apply(schema: dict[str, Any]) -> None:
        """@brief 就地替换一个对象 Schema 的 description。"""
        schema.pop("description", None)
        if purpose is not None:
            schema["description"] = purpose

    return apply


class ToolRequestModel(FrozenModel):
    """@brief 工具参数严格保持 JSON 类型，不影响其他领域输入。"""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True,
                              json_schema_extra=purpose_only_schema())


class ModelIdentity(FrozenModel):
    """@brief 不包含凭据的冻结模型请求身份。"""

    provider: Literal["openai_compatible"]
    base_url: str
    requested_model: str
    thinking: Literal["disabled", "enabled"]
    tool_choice: Literal["required", "auto"]
    max_retries: Literal[0]
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_endpoint(self) -> "ModelIdentity":
        """@brief 冻结模型身份必须使用单一官方端点：DeepSeek strict 端点与配置的模型（主站地址即拒绝）。"""
        if self.base_url != DEEPSEEK_BASE_URL or self.requested_model != DEEPSEEK_MODEL:
            _LOGGER.error("Model identity endpoint does not match the configured DeepSeek model")
            raise ValueError("Model identity endpoint does not match the configured DeepSeek model")
        return self


class RunIdentity(FrozenModel):
    """@brief 由 runtime 显式分配并与场景交叉验证的运行身份。

    git_commit 与 git_dirty 记录启动时的检出状态，非 Git 检出时二者为 null；只记录，不参与恢复比对
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「运行身份」）。
    """

    run_id: str = Field(min_length=1)
    source_group_id: str = Field(min_length=1)
    agent_ids: tuple[int, ...]
    start_at: str = Field(min_length=1)
    end_at: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_identity: ModelIdentity
    git_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")
    git_dirty: StrictBool | None = None

    @model_validator(mode="after")
    def validate_git_state(self) -> "RunIdentity":
        """@brief Git 提交与未提交标志同时记录，或在非 Git 检出时同时为 null。"""
        if (self.git_commit is None) != (self.git_dirty is None):
            raise ValueError("git_commit and git_dirty must both be recorded or both be null")
        return self


class CounterfactualIntervention(FrozenModel):
    """@brief 对新反事实分支应用一次的封闭世界条件干预。"""

    kind: Literal["set_balance_fen"]
    agent_id: int = Field(gt=0)
    balance_fen: int = Field(ge=0)
    hypothesis: str = Field(min_length=1)
    source_event_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sources(self) -> "CounterfactualIntervention":
        """@brief 要求干预证据身份唯一且假设具有实际文本。"""
        if len(self.source_event_ids) != len(set(self.source_event_ids)):
            raise ValueError("counterfactual source event ids must be unique")
        if not self.hypothesis.strip():
            raise ValueError("counterfactual hypothesis must not be blank")
        return self


class CounterfactualReceipt(FrozenModel):
    """@brief 已提交反事实干预的事件和状态身份。"""

    intervention_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    event_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    before_balance_fen: int = Field(ge=0)
    after_balance_fen: int = Field(ge=0)
    world_state_version: int = Field(gt=0)


class ActivityKind(StrEnum):
    """@brief 人物能够显式启动的活动类型。"""

    WAIT = "wait"
    SLEEP = "sleep"
    WORK = "work"
    WATCH = "watch"
    PLAY_GAME = "play_game"
    TRAVEL = "travel"
    READ_MAIL = "read_mail"
    BROWSE = "browse"
    CONSUME = "consume"
    # 生活活动（corpora/lifestyle/）经 lifestyle:<activity_id> 子类型机制提交：
    # kind 统一为 LIFESTYLE，具体条目放在 target_id，MET 按 lifestyle 键查表。
    LIFESTYLE = "lifestyle"
    # 社交前台活动（修订合同社交最小可行版）：交谈（共同在场）与通话（联系人）。
    # 这两种 kind 不在可供性注册表内，受理/推进走 life.social_actions 专属通道，
    # 不经 activities.start_activity 的注册表 MET 查表。
    CHAT = "chat"
    CALL = "call"


#: 生活活动的 activity_type 文本合同：LIFESTYLE 活动的 target_id 前缀。
LIFESTYLE_TYPE_PREFIX = "lifestyle:"


class ActivityStatus(StrEnum):
    """@brief 活动生命周期状态。"""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TRUNCATED = "truncated"


class ActionStatus(StrEnum):
    """@brief 动作从提交到终态的状态。"""

    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ActivityChange(StrEnum):
    """@brief 进行中活动允许的显式变更。"""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    CHANGE_SPEED = "change_speed"


class FulfillmentKind(StrEnum):
    """@brief 订单的履约方式。"""

    DELIVERY = "delivery"
    PICKUP = "pickup"


class TravelMode(StrEnum):
    """@brief 冻结路线支持的出行方式。"""

    DRIVING = "driving"
    WALKING = "walking"


class ContractKind(StrEnum):
    """@brief 工作合同的计酬方式。"""

    PACKAGE = "package"
    HOURLY = "hourly"
    PIECE = "piece"








class BodyState(StrictModel):
    """@brief 人物的可结算身体资源与体感状态（修订合同身体语义，2026-09-18）。

    两过程睡眠（Daan/Beersma/Borbély 1984）：sleep_pressure 清醒时以 18.2h
    时间常数趋近 1、睡眠时以 4.2h 衰减，决定人物此刻困不困；sleep_deficit_hours
    为独立不足账（清醒累积、睡眠恢复，钳制 [0, 24]），驱动效率曲线。旧字段
    旧睡眠债字段已删除，不保留别名。sleep_need_hours 与 chronotype_midsleep
    为人物档案常量（默认 7.5h 与 04:00，experiment_assumption），随状态持久化
    使结算纯函数自包含。mood_valence 只影响体感呈现与记忆重要度加权，不直接
    改变任何动作可行性（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」）。
    """

    energy_kcal: float = Field(ge=0)
    sleep_pressure: float = Field(default=0.0, ge=0, le=1.2)
    sleep_deficit_hours: float = Field(default=0.0, ge=0, le=24)
    sleep_need_hours: float = Field(default=7.5, ge=7.0, le=9.0)
    chronotype_midsleep: float = Field(default=4.0, ge=0.0, lt=24.0)
    social_need: float = Field(default=0.0, ge=0, le=1)
    mood_valence: float = Field(default=0.0, ge=-1, le=1)
    neuroticism_norm: float = Field(default=0.0, ge=-1.0, le=1.0)
    entertainment_need: float = Field(ge=0, le=1)


class ContentProgress(StrictModel):
    """@brief 人物对一个内容项目的实际接触进度。"""

    item_id: str = Field(min_length=1)
    position_seconds: float = Field(ge=0)
    exposure_count: int = Field(ge=0)


class SessionStatus(StrEnum):
    """@brief 地点会话的生命周期状态闭集（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第七章「会话与活动的字段」）。"""

    ACTIVE = "active"
    ENDED = "ended"


class SessionKind(StrEnum):
    """@brief 地点会话的种类：一段地点停留（stay）或两地之间的一段路（transit）。"""

    STAY = "stay"
    TRANSIT = "transit"


class SessionTaskKind(StrEnum):
    """@brief 活动关联的任务类型闭集（从原会话移到活动）。"""

    GOAL = "goal"
    WORK_TASK = "work_task"
    NONE = "none"


class SessionState(StrictModel):
    """@brief 一段地点停留或一段路；由系统随出发与到达开始和结束，人物不手动开始或结束会话。

    :param kind: stay 停留时带 location_id；transit 路上时带 origin_id 与 destination_id。
    :param status: active 或 ended；ended 必须含 ended_at。
    :param activity_ids: 归入本会话的活动（暂停的活动回到开始地点时归入那里的新会话）。
    :param name: 人物可读名称，停留「<地点>（<M月D日> HH:MM 起）」，路上「去<目的地>的路上（…起）」；结束后不改名。
    :param opened_by_action_id: 由决策引起的切换（出发、途中结束出行）记下引起切换的动作身份，进度层以该动作结果的
        事件序号为下界；运行开始与到达为 None，下界取开始时刻（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「进度层」）。
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


class SameMomentStage(StrEnum):
    """@brief 同刻受限轮阶段闭集（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「规则」3 与「实现约定」）。

    未达同刻上限时为 open；达到上限后每个受限轮只开放一类动作，阶段依赖上一受限轮的结果，时钟推进复位为 open。
    暂停的活动不占名额以后，阶梯不再按名额分流：resume_or_conclude 与 conclude_only 留在闭集中（世界结构版本仍为 9），
    现行阶梯不再进入它们（docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-裁决记录 R2）。
    只开放等待的阶段随世界结构版本 8 删除（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「失败后强制等待与跨决策预算」）。
    """

    OPEN = "open"
    CONCLUDE_PENDING = "conclude_pending"
    START_OR_RESUME = "start_or_resume"
    RESUME_OR_CONCLUDE = "resume_or_conclude"
    CONCLUDE_ONLY = "conclude_only"


class AgentState(StrictModel):
    """@brief 一个人物的全部生活状态。"""

    agent_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    balance_account_id: str = Field(min_length=1)
    skill_levels: dict[str, float] = Field(default_factory=dict)
    body: BodyState
    inventory: dict[str, dict[str, float]] = Field(default_factory=dict)
    active_effects: set[str] = Field(default_factory=set)
    body_settled_at: int = Field(ge=0)
    delivered_warning_signatures: set[str] = Field(default_factory=set)
    next_decision_at: int = Field(ge=0)
    # 连续业务拒绝次数：只作动机层挫败感的输入，不参与唤醒与冷却。
    consecutive_rejections: int = Field(default=0, ge=0)
    # 同刻决策（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「实现约定」）：本秒已进行的决策轮数与受限轮阶段，时钟推进归零。
    same_moment_rounds: int = Field(default=0, ge=0)
    same_moment_stage: SameMomentStage = SameMomentStage.OPEN
    content_progress: dict[str, ContentProgress] = Field(default_factory=dict)
    read_message_ids: set[str] = Field(default_factory=set)
    visible_message_ids: set[str] = Field(default_factory=set)
    visible_task_ids: set[str] = Field(default_factory=set)
    # 决策节律簿记（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「决策节律：结算边界与唤醒条件分离」）
    # ：滞回参照=上次实际提供决策时的分级。
    last_decision_time: int | None = Field(default=None, ge=0)
    felt_bands_at_last_decision: dict[str, str] = Field(default_factory=dict)
    people_at_last_decision: frozenset[str] = frozenset()
    known_task_ids: set[str] = Field(default_factory=set)
    pending_wake_reasons: list[str] = Field(default_factory=list)
    # 当日目标只记录人物自我承诺和实际推进证据，不驱动活动执行。
    day_plan: DayPlan | None = None
    plan_submitted_dates: set[str] = Field(default_factory=set)
    # 未完成的个人事务（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-范围与状态 「存放」）：键为事务身份；完成或取消即移出，终态只在事件里；
    # 不放进 DayPlan，零点置空与修订整表替换都不波及它。
    errands: dict[str, ErrandState] = Field(default_factory=dict)
    # 社交最小可行版（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」）：收件箱与已读消息。
    inbox: dict[str, Message] = Field(default_factory=dict)
    read_inbox_ids: set[str] = Field(default_factory=set)
    # 发给场景联系人的消息（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「联系人往来」），联系人回复请求的往来由它与收件箱组成。
    outbox: dict[str, Message] = Field(default_factory=dict)
    # 情绪与不适脉冲簿记（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」）。
    mood_updated_at: int | None = Field(default=None, ge=0)
    discomfort_started_at: int | None = Field(default=None, ge=0)
    discomfort_pulse_hours: float = Field(default=0.0, ge=0)
    # 工作材料增量揭示账（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「观察投影行为规则」）：
    # 自上次决策以来新读到的区间，单次 ≤2000 字符。
    material_reveals: list[dict[str, Any]] = Field(default_factory=list)

    @field_serializer(
        "active_effects",
        "read_message_ids",
        "visible_message_ids",
        "visible_task_ids",
        "known_task_ids",
        "delivered_warning_signatures",
        "plan_submitted_dates",
        "read_inbox_ids",
        "people_at_last_decision",
        when_used="json",
    )
    def serialize_agent_sets(self, value) -> list[str]:
        """@brief 按稳定顺序编码人物集合，使跨进程状态字节一致。"""
        return sorted(value)


class ActivityState(StrictModel):
    """@brief 一个活动实例的权威进度。"""

    activity_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    # 活动开始时的地点；出行活动记录出发地，目的地另由 destination_id 表达。
    location_id: str = Field(min_length=1)
    kind: ActivityKind
    status: ActivityStatus
    started_at: int = Field(ge=0)
    ended_at: int | None = Field(default=None, ge=0)
    planned_end_at: int = Field(gt=0)
    last_settled_at: int = Field(ge=0)
    elapsed_seconds: int = Field(ge=0)
    planned_duration_seconds: int = Field(gt=0)
    effective_work_seconds: float = Field(default=0, ge=0)
    media_position_seconds: float = Field(default=0, ge=0)
    route_progress_meters: float = Field(default=0, ge=0)
    next_interaction_at: int | None = Field(default=None, ge=0)
    target_id: str | None = None
    destination_id: str | None = None
    travel_mode: TravelMode | None = None
    playback_speed: float = Field(default=1, ge=0.5, le=2)
    resource_claims: set[str] = Field(default_factory=set)
    origin_action_id: str = Field(min_length=1)
    origin_decision_id: str = Field(min_length=1)
    origin_request_event_id: str = Field(min_length=1)
    boundary_type: str | None = None
    interruption_reason: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    # 生命周期（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第七章「活动的生命周期」）：终态且 concluded_at 为空即待收尾；
    # report_anchor_at 为播报锚点（开始、恢复与对该活动播报时写入）；session_ids 为先后所属的地点会话。
    concluded_at: int | None = Field(default=None, ge=0)
    report_anchor_at: int | None = Field(default=None, ge=0)
    # 暂停时刻（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「待办层」）：暂停时写入、恢复时清空，待办层按它排序。
    paused_at: int | None = Field(default=None, ge=0)
    session_ids: list[str] = Field(default_factory=list)
    name: str = ""
    # 从原会话移来的任务关联与人物自述：summary 写入 progress_note，前一条推入 note_history。
    task_kind: SessionTaskKind = SessionTaskKind.NONE
    task_ref: str | None = None
    plan_goal_intent_kind: IntentKind | None = None
    progress_note: str = Field(default="", max_length=500)
    note_updated_at: int | None = Field(default=None, ge=0)
    note_history: list[dict] = Field(default_factory=list)

    @field_serializer("resource_claims", when_used="json")
    def serialize_resource_claims(self, value: set[str]) -> list[str]:
        """@brief 按稳定顺序编码活动资源集合。"""
        return sorted(value)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ActivityState":
        """@brief 要求终态具有结束时刻，暂停和活动态没有结束时刻；收尾只属于非截断终态。"""
        terminal = {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED,
                    ActivityStatus.FAILED, ActivityStatus.TRUNCATED}
        if (self.status in terminal) != (self.ended_at is not None):
            raise ValueError("activity ended_at disagrees with lifecycle status")
        if (not self.started_at <= self.last_settled_at <= self.planned_end_at
                or self.elapsed_seconds > self.planned_duration_seconds
                or self.planned_end_at < self.started_at
                or self.ended_at is not None and self.ended_at < self.last_settled_at):
            _LOGGER.error("Activity progress interval is invalid")
            raise ValueError("activity progress interval is invalid")
        if self.concluded_at is not None and (self.status not in CONCLUDABLE_TERMINAL_STATUSES
                                              or self.concluded_at < self.ended_at):
            _LOGGER.error("Activity conclusion disagrees with its lifecycle")
            raise ValueError("activity concluded_at disagrees with lifecycle status")
        _validate_task_reference(self.task_kind, self.task_ref, self.plan_goal_intent_kind)
        return self


#: 需要人物收尾的终态（截断不收尾）：状态属于此集合且 concluded_at 为空的活动即待收尾。
CONCLUDABLE_TERMINAL_STATUSES = frozenset({ActivityStatus.COMPLETED, ActivityStatus.FAILED,
                                           ActivityStatus.CANCELLED})


def _validate_task_reference(kind: SessionTaskKind, reference: str | None, intent: str | None) -> None:
    """@brief 活动的任务类型与引用配对：none 时无引用，goal/work_task 必带引用，goal 的意图等于引用。"""
    if kind == SessionTaskKind.NONE and reference is not None:
        raise ValueError("task_ref must be null when task_kind is none")
    if kind != SessionTaskKind.NONE and reference is None:
        raise ValueError("task_ref required for goal and work_task")
    if kind == SessionTaskKind.GOAL and intent != reference:
        _LOGGER.error("Goal activity intent disagrees with its task reference")
        raise ValueError("goal activity intent must match task_ref")


class DeliveryState(StrictModel):
    """@brief 注册食品购买后的固定价订单和图上履约证据。"""

    order_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    food_id: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    app_id: str | None
    seller_account_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    unit_price_fen: int = Field(gt=0)
    delivery_fee_fen: int = Field(ge=0)
    total_price_fen: int = Field(ge=0)
    destination_id: str = Field(min_length=1)
    fulfillment: FulfillmentKind
    recipient_id: str = Field(min_length=1)
    request_message_id: str | None = None
    created_at: int = Field(ge=0)
    expected_at: int = Field(ge=0)
    status: Literal["pending", "ready", "delivered", "cancelled"]
    path: tuple[str, ...]
    #: 到店无行程；配送使用图上现有出行方式的稳定行程身份。
    trip_id: str | None
    distance_meters: int = Field(ge=0)
    prep_seconds: int = Field(ge=0)


class StoredAction(StrictModel):
    """@brief 动作幂等判断所需的请求与结果。"""

    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    request_sha256: str = Field(min_length=64, max_length=64)
    receipt: dict[str, Any]
    result: dict[str, Any] | None = None


class PendingAction(StrictModel):
    """@brief 一个决策轮中尚未结算的动作。"""

    run_id: str = Field(min_length=1)
    round_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    observed_at: int = Field(ge=0)
    world_state_version: int = Field(ge=0)
    order: int = Field(ge=0)
    tool_name: str = Field(min_length=1)
    request: dict[str, Any]
    request_sha256: str = Field(min_length=64, max_length=64)
    requested_event_id: str = Field(min_length=1)
    reused: bool = False
    work_review: dict[str, Any] | None = None
    work_review_event_id: str | None = None


class DecisionRoundState(StrictModel):
    """@brief 决策轮的预期人物和已登记请求。"""

    round_id: str = Field(min_length=1)
    expected_agent_ids: list[int]
    observed_version: int = Field(ge=0)
    status: Literal["open", "committed", "aborted"] = "open"
    pending_actions: list[PendingAction] = Field(default_factory=list)


class SameMomentPresentation(StrictModel):
    """@brief 一个人物本秒的呈现暂存（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「同一秒内的呈现与「刚才」」）。

    stamps 为本秒各轮实际呈现的对象到时刻映射，时钟推进时才并入 presentation_state。
    """

    stamps: dict[str, int]


class WorldState(StrictModel):
    """@brief 提交到 FootprintStore 的完整世界状态。"""

    # 结构版本必填（无缺省）：旧检查点缺该字段即拒绝加载（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「产品行为」）。
    schema_version: Literal[WORLD_SCHEMA_VERSION]
    version: int = Field(ge=0)
    current_time: int = Field(ge=0)
    end_time: int = Field(gt=0)
    run_id: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    scenario_sha256: str = Field(min_length=64, max_length=64)
    content_manifest_sha256: str = Field(min_length=64, max_length=64)
    agents: dict[int, AgentState]
    accounts: dict[str, int]
    activities: dict[str, ActivityState] = Field(default_factory=dict)
    sessions: dict[str, SessionState] = Field(default_factory=dict)
    deliveries: dict[str, DeliveryState] = Field(default_factory=dict)
    #: 场景联系人实际收到的物品（联系人身份 → 商品 → 份数）：直接配送与当面给予都记在这里。
    contact_holdings: dict[str, dict[str, float]] = Field(default_factory=dict)
    pending_events: list[ScheduledEvent] = Field(default_factory=list)
    processed_actions: dict[str, StoredAction] = Field(default_factory=dict)
    paid_task_ids: set[str] = Field(default_factory=set)
    #: 呈现派生量（docs/CONTRACTS.md#contracts-worldmock-接口合同「派生量字段」）：按（人物, 对象标识）记录上次呈现
    #: 时刻（UTC 纪元秒），是曝光轮转的判据，不含任何偏好推断。
    presentation_state: dict[int, dict[str, int]] = Field(default_factory=dict)
    #: 按人物暂存的本秒呈现留痕：同一秒各轮沿用该秒首轮的呈现窗口，时钟推进时逐人并入 presentation_state 并清空。
    same_moment_presentation: dict[int, SameMomentPresentation] = Field(default_factory=dict)
    random_seed: int
    random_state: str = Field(min_length=1)

    @field_serializer("paid_task_ids", when_used="json")
    def serialize_world_sets(self, value: set[str]) -> list[str]:
        """@brief 按稳定顺序编码世界结算集合。"""
        return sorted(value)

    @model_validator(mode="after")
    def validate_world(self) -> "WorldState":
        """@brief 核对时钟、账户、库存与人物活动集合。"""
        if self.current_time > self.end_time:
            raise ValueError("world current time exceeds experiment end")
        if any(value < 0 for value in self.accounts.values()):
            raise ValueError("world account balance cannot be negative")
        for agent_id, agent in self.agents.items():
            if agent_id != agent.agent_id:
                raise ValueError("world agent mapping identity mismatch")
            if agent.body_settled_at > self.current_time:
                _LOGGER.error("Agent body settlement is ahead of world time")
                raise ValueError("agent body settlement is ahead of world time")
            if any(quantity < 0 for inventory in agent.inventory.values() for quantity in inventory.values()):
                raise ValueError("agent inventory quantity cannot be negative")
        _validate_activity_collection(self)
        _validate_session_collection(self)
        return self


def active_activities(state: WorldState, agent_id: int) -> tuple[ActivityState, ...]:
    """@brief 按开始时刻与身份稳定返回人物全部正在执行的活动。"""
    return tuple(sorted((activity for activity in state.activities.values()
                         if activity.agent_id == agent_id and activity.status == ActivityStatus.ACTIVE),
                        key=lambda activity: (activity.started_at, activity.activity_id)))


def paused_activities(state: WorldState, agent_id: int) -> tuple[ActivityState, ...]:
    """@brief 按开始时刻与身份稳定返回人物全部暂停活动。"""
    return tuple(sorted((activity for activity in state.activities.values()
                         if activity.agent_id == agent_id and activity.status == ActivityStatus.PAUSED),
                        key=lambda activity: (activity.started_at, activity.activity_id)))


def _validate_activity_collection(state: WorldState) -> None:
    """@brief 恢复时拒绝身份、时间或同人资源占用矛盾。"""
    for activity_id, activity in state.activities.items():
        agent = state.agents.get(activity.agent_id)
        if agent is None or activity_id != activity.activity_id:
            _LOGGER.error("World activity identity or owner is invalid")
            raise ValueError("world activity identity or owner is invalid")
        if (activity.started_at > state.current_time or activity.last_settled_at > state.current_time
                or activity.last_settled_at > agent.body_settled_at
                or activity.ended_at is not None and activity.ended_at > state.current_time
                or activity.elapsed_seconds > activity.last_settled_at - activity.started_at
                or activity.status == ActivityStatus.ACTIVE
                and activity.last_settled_at != state.current_time):
            _LOGGER.error("World activity settlement time is invalid")
            raise ValueError("world activity settlement time is invalid")
        if activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED} and (
                activity.planned_end_at - activity.last_settled_at
                != activity.planned_duration_seconds - activity.elapsed_seconds):
            _LOGGER.error("World activity remaining duration is invalid")
            raise ValueError("world activity remaining duration is invalid")
        if activity.resource_claims != _expected_activity_claims(activity):
            _LOGGER.error("World activity resource claims disagree with the registry")
            raise ValueError("world activity resource claims disagree with the registry")
    for agent_id in state.agents:
        claimed: set[str] = set()
        for activity in active_activities(state, agent_id):
            if claimed & activity.resource_claims:
                _LOGGER.error("World active activities claim the same resource")
                raise ValueError("world active activities claim the same resource")
            claimed.update(activity.resource_claims)


def _validate_session_collection(state: WorldState) -> None:
    """@brief 恢复时拒绝会话身份、归属或时序矛盾：每人至多一个进行中会话，活动只指向已有会话。"""
    active_owners: list[int] = []
    for session_id, session in state.sessions.items():
        if (session_id != session.session_id or session.agent_id not in state.agents
                or session.started_at > state.current_time
                or any(activity_id not in state.activities for activity_id in session.activity_ids)):
            _LOGGER.error("World session identity, owner or activities are invalid")
            raise ValueError("world session identity, owner or activities are invalid")
        if session.status == SessionStatus.ACTIVE:
            active_owners.append(session.agent_id)
    if len(active_owners) != len(set(active_owners)):
        _LOGGER.error("An agent has more than one active session")
        raise ValueError("an agent has more than one active session")
    if any(session_id not in state.sessions for activity in state.activities.values()
           for session_id in activity.session_ids):
        _LOGGER.error("An activity refers to an unknown session")
        raise ValueError("an activity refers to an unknown session")


def _expected_activity_claims(activity: ActivityState) -> set[str]:
    """@brief 从唯一注册表和社交活动声明恢复每项权威资源占用。"""
    if activity.kind in {ActivityKind.CHAT, ActivityKind.CALL}:

        return set(SOCIAL_RESOURCE_CLAIMS)

    mode = activity.travel_mode.value if activity.travel_mode is not None else None
    content_kind = activity.data.get("content_kind") if activity.kind == ActivityKind.WATCH else None
    try:
        return set(engine_affordances().resource_claims(activity.kind.value, travel_mode=mode,
                                                         content_kind=content_kind))
    except (KeyError, ValueError) as error:
        _LOGGER.error("World activity kind or travel mode has no resource declaration")
        raise ValueError("world activity resource declaration is invalid") from error


_TARGETED_ACTIVITY_KINDS = frozenset({
    ActivityKind.WORK,
    ActivityKind.WATCH,
    ActivityKind.PLAY_GAME,
    ActivityKind.READ_MAIL,
    ActivityKind.BROWSE,
    ActivityKind.LIFESTYLE,
})


class ActivityStartParams(FrozenModel):
    """@brief 领域内部统一启动一个有时长生活活动。"""

    activity_type: ActivityKind
    planned_duration_seconds: int = Field(gt=0, le=DERIVED_DURATION_CAP_SECONDS)
    target_id: str | None = Field(
        default=None,
        description="Required for work, watch, play_game, read_mail, browse, and lifestyle.",
    )
    destination_id: str | None = Field(
        default=None,
        description="Required for travel.",
    )
    travel_mode: TravelMode | None = Field(
        default=None,
        description="Required for travel.",
    )
    playback_speed: float = Field(default=1, ge=0.5, le=2)

    @model_validator(mode="after")
    def validate_target(self) -> "ActivityStartParams":
        """@brief 校验不同活动需要的业务目标。"""
        if self.activity_type in _TARGETED_ACTIVITY_KINDS and not self.target_id:
            raise ValueError("target_id is required for the selected activity")
        if self.activity_type == ActivityKind.TRAVEL:
            if not self.destination_id or self.travel_mode is None:
                raise ValueError("destination_id and travel_mode are required for travel")
        return self


class ChangeActivityRequest(FrozenModel):
    """@brief 领域内部统一暂停、恢复、退出或变更播放速度。"""

    activity_id: str = Field(min_length=1)
    operation: ActivityChange
    playback_speed: float | None = Field(
        default=None,
        ge=0.5,
        le=2,
        description="Required only when operation is change_speed.",
    )
    resume_cap_seconds: int | None = Field(
        default=None,
        ge=60,
        description="Resume only: the longest remaining run from now (work deadline and contract limit).",
    )

    @model_validator(mode="after")
    def validate_speed(self) -> "ChangeActivityRequest":
        """@brief 只允许变速操作携带速度。"""
        needs_speed = self.operation == ActivityChange.CHANGE_SPEED
        if needs_speed != (self.playback_speed is not None):
            raise ValueError("playback_speed is required only for change_speed")
        return self


class PurchaseItem(FrozenModel):
    """@brief 同一订单明确选择的店铺、食品、数量和收货人。"""

    store_id: str = Field(pattern=r"\S")
    food_id: str = Field(pattern=r"\S")
    quantity: int = Field(gt=0, le=100)
    recipient_id: str = Field(pattern=r"\S")
    request_message_id: str | None = None


class PurchaseOrderRequest(FrozenModel):
    """@brief 购买 1-5 件同一商家、同一履约方式的商品，一次付款一次配送。

    结算时任一件不可购则整单拒绝，不自动删件（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「商务与进食」）。
    items 以 WithJsonSchema 内联子模型 Schema（strict 合同禁止嵌套引用
    键，docs/CONTRACTS.md#contracts-worldmock-接口合同「人物工具与管理接口」认可的唯一内联机制）；1-5 件由结算域校验
    （DeepSeek strict 禁 minItems）。
    """

    items: Annotated[
        tuple[PurchaseItem, ...],
        WithJsonSchema({
            "type": "array",
            "items": PurchaseItem.model_json_schema(mode="validation"),
        }),
    ]
    fulfillment: Literal["delivery", "pickup"]
    destination_id: str = Field(pattern=r"\S")
    app_id: str | None


class ConsumeItem(FrozenModel):
    """@brief 一次进食中的一件已持有食品与数量（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「商务与进食」：1-3 件）。"""

    product_id: str = Field(pattern=r"\S")
    quantity: float = Field(gt=0, le=100)


class ConsumeRequest(FrozenModel):
    """@brief 使用已经交付并持有的商品。

    多件（1-3）仅限食品同地点一次进食：用餐时长取最长者、热量与效果累加；
    非食品使用仍为单件（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「商务与进食」）。单件路径沿用域内时长合同
    （duration_seconds 必须等于商品目录时长）。items 以 WithJsonSchema
    内联子模型 Schema（strict 合同禁止嵌套引用键）；1-3 件由结算域校验
    （DeepSeek strict 禁 minItems）。
    """

    items: Annotated[
        tuple[ConsumeItem, ...],
        WithJsonSchema({
            "type": "array",
            "items": ConsumeItem.model_json_schema(mode="validation"),
        }),
    ]
    use_mode: Literal["eat", "use"]
    duration_seconds: int = Field(ge=60, le=CONSUME_DURATION_CAP_SECONDS)


class PlanGoalInput(FrozenModel):
    """@brief 当日目标条目：意图、目标文字与两个必填可空的场景本地 HH:MM——remind_time（要在那一刻被提醒时填写，
    否则为 null）与 around_time（打算在几点前后做，docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进
     ；不提醒、不要求晚于登记时刻）。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema(
        "今日计划的一个目标：意图、目标文字、提醒时刻与大致时刻。"))
    intent_kind: IntentKind
    goal: str = Field(pattern=r"\S")
    remind_time: Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")] | None
    around_time: Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")] | None

    @field_validator("goal")
    @classmethod
    def validate_goal_length(cls, goal: str) -> str:
        """@brief 限制目标字符数，同时保持受支持的严格工具 Schema。"""
        if len(goal) > PLAN_GOAL_MAX_CHARS:
            _LOGGER.error("Day goal exceeds its character limit")
            raise ValueError(f"day goal exceeds {PLAN_GOAL_MAX_CHARS} characters")
        return goal


def _validate_plan_goals(goals: Sequence[PlanGoalInput], model_name: str) -> Sequence[PlanGoalInput]:
    """@brief 零点计划与修订两个入口共用的清单校验：条数超限与重复意图在同一次校验里一并报告。

    条数超过八条定位在 goals 本身（原值为提交的各条目）；每个重复的意图同时定位首次出现与其后每次出现的条目
    （goals[i].intent_kind，原值为该意图），按下标升序。不因超限提前返回（docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库-计划重复意图 ）。

    :param goals: 已逐条通过条目校验的目标清单。
    :param model_name: 发起校验的请求模型名，作为校验错误的标题。
    """
    errors: list[Any] = []
    if len(goals) > MAX_PLAN_GOALS:
        _LOGGER.error("Day plan exceeds eight goals")
        errors.append({"type": "value_error", "loc": (), "input": [goal.model_dump(mode="json") for goal in goals],
                       "ctx": {"error": ValueError("day goals require at most eight entries")}})
    positions: dict[str, list[int]] = {}
    for index, goal in enumerate(goals):
        positions.setdefault(goal.intent_kind, []).append(index)
    repeated = sorted((index, kind) for kind, indexes in positions.items() if len(indexes) > 1 for index in indexes)
    for index, kind in repeated:
        message = (f"intent_kind '{kind}' may occur only once; keep one goal for this intent "
                   "and merge these entries into it")
        errors.append({"type": "value_error", "loc": (index, "intent_kind"), "input": kind,
                       "ctx": {"error": ValueError(message)}})
    if repeated:
        _LOGGER.error("Day plan contains duplicate intents")
    if errors:
        raise ValidationError.from_exception_data(model_name, errors)
    return goals


class PlanDayRequest(FrozenModel):
    """@brief 当日有序目标清单：零到八条，同一意图最多一条；零点计划的输出与修订请求共用这一形状。"""

    goals: Annotated[
        tuple[PlanGoalInput, ...],
        WithJsonSchema({
            "type": "array",
            "items": PlanGoalInput.model_json_schema(mode="validation"),
        }),
    ]

    @field_validator("goals")
    @classmethod
    def validate_goals(cls, goals: tuple[PlanGoalInput, ...]) -> tuple[PlanGoalInput, ...]:
        """@brief 零点计划的输出经共用校验核验容量与意图唯一性，超限与重复意图逐条定位、同批报告。"""
        return _validate_plan_goals(goals, cls.__name__)


class ReviseDayPlanRequest(ToolRequestModel):
    """@brief 严格修订请求逐项定位重复意图，已推进目标由环境保留。"""

    goals: Annotated[list[PlanGoalInput], WithJsonSchema(
        {"type": "array", "items": PlanGoalInput.model_json_schema(mode="validation")})]

    @field_validator("goals")
    @classmethod
    def validate_goals(cls, goals: list[PlanGoalInput]) -> list[PlanGoalInput]:
        """@brief 修订请求经同一共用校验核验容量与意图唯一性，超限与重复意图逐条定位、同批报告。"""
        return _validate_plan_goals(goals, cls.__name__)


class SendMessageRequest(FrozenModel):
    """@brief 向同世界人物或场景联系人发送瞬时消息（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」）。"""

    recipient_id: str = Field(pattern=r"\S")
    text: str


class StartSocialActivityRequest(FrozenModel):
    """@brief 发起交谈（共同在场）或通话（联系人）；时长由场景反馈间隔派生后截取：交谈 1–120 分钟，通话 10–60 分钟
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）。"""

    kind: Literal["chat", "call"]
    partner_id: str = Field(pattern=r"\S")
    duration_minutes: int = Field(gt=0, le=120)


class MeetingMinutes(FrozenModel):
    """@brief AMI 会议整理任务公开要求的结构化成果。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema("会议纪要的摘要、决定与行动项。"))
    summary: str = Field(pattern=r"\S")
    decisions: tuple[Annotated[str, Field(pattern=r"\S")], ...]
    action_items: tuple[Annotated[str, Field(pattern=r"\S")], ...]

    @model_validator(mode="after")
    def validate_text(self) -> "MeetingMinutes":
        """@brief 拒绝空白摘要、决定或行动项。"""
        values = (self.summary, *self.decisions, *self.action_items)
        if any(not value.strip() for value in values):
            raise ValueError("meeting minutes text must not be blank")
        return self


class SubmitWorkRequest(FrozenModel):
    """@brief 提交符合当前 AMI 合同公开 Schema 的会议纪要。"""

    task_id: str = Field(pattern=r"\S")
    deliverable: Annotated[
        MeetingMinutes,
        WithJsonSchema(MeetingMinutes.model_json_schema(mode="validation")),
    ]


#: 会话进展自述的字符上限（DeepSeek strict 禁 maxLength；由 model_validator 执行）。
SESSION_SUMMARY_MAX_CHARS = 500

#: 购买偏好的唯一中文闭集。
PURCHASE_PREFERENCES = ("最快拿到", "到手价最低")
#: prefer 的字段级「格式与来源」说明（参数 Schema 的一部分，不是工具 description），并说明由系统定方式的「购买」；
#: 与其他参数说明一样只写用途（一句中文，不带拉丁字母单词与规则说法，tests/integration/test_tool_description_references.py）。
_PREFER_FORMAT = "购买时选择最快拿到或到手价最低，不购买时写空值。"


class ActivityRequestItem(ToolRequestModel):
    """@brief 一次活动的目标、方式、来源和收货人。"""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, validate_by_alias=True,
                              serialize_by_alias=True, json_schema_extra=purpose_only_schema())
    target: str = Field(pattern=r"\S")
    way: str = Field(pattern=r"\S")
    from_: str | None = Field(alias="from")
    to: str | None


class StartActivityRequest(ToolRequestModel):
    """@brief 一至两项活动原子受理，方式唯一推导内部动作，购买偏好必填可空。"""

    items: Annotated[list[ActivityRequestItem], WithJsonSchema(
        {"type": "array", "items": ActivityRequestItem.model_json_schema(mode="validation")})]
    answering: str | None
    prefer: Annotated[Literal["最快拿到", "到手价最低"] | None, WithJsonSchema(
        {"type": ["string", "null"], "enum": [*PURCHASE_PREFERENCES, None], "description": _PREFER_FORMAT})]

    @field_validator("items")
    @classmethod
    def validate_items(cls, items: list[ActivityRequestItem]) -> list[ActivityRequestItem]:
        """@brief 限制原子批次一至两项，重复目标必须有不同收货人。"""
        if not 1 <= len(items) <= 2:
            _LOGGER.error("Start activity requires one or two items")
            raise ValueError("items requires one or two activity objects; acceptance is atomic")
        pairs = [(item.target, item.to) for item in items]
        if len(set(pairs)) != len(pairs):
            _LOGGER.error("Start activity repeats a target and recipient")
            raise ValueError("a target may repeat only with a different recipient")
        return items

    @field_validator("prefer")
    @classmethod
    def validate_prefer(cls, prefer: str | None, info) -> str | None:
        """@brief 方式合法时要求购买偏好，非购买必须空值。"""

        items = info.data.get("items")
        options = [way_options(item.way) for item in items] if items is not None else []
        if items is None or any(not option for option in options):
            return prefer
        purchase = any(option & NAMED_PURCHASE_WAYS for option in options)
        if (purchase and prefer is None) or (not purchase and prefer is not None):
            _LOGGER.error("Purchase preference does not match the activity ways")
            raise ValueError("purchases require a declared preference; non-purchases require null")
        return prefer


class SubmitWorkActionRequest(ToolRequestModel):
    """@brief 按人物可读任务名称提交工作成果。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema())
    task_name: str = Field(pattern=r"\S")
    deliverable: Annotated[MeetingMinutes, WithJsonSchema(MeetingMinutes.model_json_schema(mode="validation"))]


class SendMessageActionRequest(ToolRequestModel):
    """@brief 按人物可读收件人名称发送人物撰写的消息。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema())
    recipient_name: str = Field(pattern=r"\S")
    text: str


class RecallTaskSummaryRequest(ToolRequestModel):
    """@brief 按本轮可见的任务、当日目标或活动名称只读回看进展（会话名不在七层中出现，不可回看）。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema())
    task_name: str = Field(pattern=r"\S")


#: 控制工具每项活动名的「格式与来源」说明。字段级说明是参数 Schema 的一部分，不是工具 description：只写格式与来源，
#: 不写规则与上限（上限由本地校验执行，docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第六章）。
_ACTIVITY_NAME_FORMAT = "活动名：照工具层、待办层或进度层写出的名称原样抄写，包括「（9月28日 12:05 起）」这样的开始时刻。"


def _validate_control_items(tool: str, items: list[Any]) -> list[Any]:
    """@brief 控制工具 items 的上限：一至两项、活动名互不相同（DeepSeek strict 禁 minItems 与 maxItems，由本地校验
    兜住）；作为 items 的字段校验，协议纠错的原因落在 items 上。"""
    names = [item.activity_name for item in items]
    if not 1 <= len(items) <= 2:
        _LOGGER.error("Activity control request does not carry one or two items")
        raise ValueError(f"{tool} takes one or two items")
    if len(set(names)) != len(names):
        _LOGGER.error("Activity control request names the same activity twice")
        duplicates = [{"type": "value_error", "loc": (index, "activity_name"), "input": name,
                       "ctx": {"error": ValueError(f"{tool} activity names must be distinct")}}
                      for index, name in enumerate(names) if name in names[:index]]
        raise ValidationError.from_exception_data(tool, duplicates)
    return items


def _validate_summary(summary: str) -> str:
    """@brief 自述不超过 500 字符（DeepSeek strict 禁 maxLength，由本地校验兜住；非空白由 Schema 的 pattern 约束）。"""
    if len(summary) > SESSION_SUMMARY_MAX_CHARS:
        _LOGGER.error("Activity control summary exceeds its character limit")
        raise ValueError("must be within 500 characters")
    return summary


#: HH:MM 钟点的 Schema 格式（与当日目标的 remind_time 同名同形）。
_CLOCK_PATTERN = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
#: 个人事务变更的交叉约束（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 「每条变更」）：按变更种类必须非空的键与必须为 null 的键；
#: 其余键（新增的 memo、remind_time，更新的 memo、remind_time）可空。
_ERRAND_REQUIRED = {"新增": ("outcome", "item", "quantity"), "更新": (), "取消": ("reason",)}
_ERRAND_NULL = {"新增": ("reason",), "更新": ("outcome", "item", "quantity", "reason"),
                "取消": ("outcome", "item", "quantity", "memo", "remind_time")}


def _errand_text(value: str | None, limit: int, what: str) -> str | None:
    """@brief 事务文字的字符上限（strict 端点禁 maxLength，由本地校验执行，上限只写在原因里）；null 原样通过。

    :param what: 英文原因里的字段说法（errand name、errand memo、errand cancel reason）。
    """
    if value is not None and len(value) > limit:
        _LOGGER.error("Errand change text exceeds its character limit")
        raise ValueError(f"{what} must be within {limit} characters")
    return value


class ErrandChange(ToolRequestModel):
    """@brief 控制工具请求级 errands 的一条个人事务变更（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 ）：封闭对象，八个键全部必填，
    按变更种类的交叉约束与字符、份数上限由本地校验执行（上限写在原因里，Schema 只给类型与格式）；没有「完成」。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema(
        "一条个人事务的变更：做什么、名称、要办成的事、备注、提醒钟点与取消原因。"))
    change: Literal["新增", "更新", "取消"] = Field(description="这条变更做什么，从列出的三种里选一种。")
    name: str = Field(pattern=r"\S", description="事务的名称，新增时自己起，之后照待办层里的写法写。")
    outcome: Annotated[Literal["取得", "吃掉"] | None, WithJsonSchema(
        {"type": ["string", "null"], "enum": ["取得", "吃掉", None],
         "description": "要办成的结果，从列出的两种里选一种。"})]
    item: Annotated[str, Field(pattern=r"\S")] | None = Field(description="要取得或吃掉的商品名称。")
    quantity: int | None = Field(description="要办成的份数。")
    memo: Annotated[str, Field(pattern=r"\S")] | None = Field(description="用自己的话写打算怎么办、去哪办。")
    remind_time: Annotated[str, Field(pattern=_CLOCK_PATTERN)] | None = Field(description="想被提醒去办这件事的钟点。")
    reason: Annotated[str, Field(pattern=r"\S")] | None = Field(description="不办了的原因。")

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        """@brief 事务名称不超过 30 字符。"""
        return _errand_text(name, ERRAND_NAME_MAX_CHARS, "errand name")

    @field_validator("memo")
    @classmethod
    def validate_memo(cls, memo: str | None) -> str | None:
        """@brief 备注不超过 120 字符。"""
        return _errand_text(memo, ERRAND_TEXT_MAX_CHARS, "errand memo")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, reason: str | None) -> str | None:
        """@brief 取消原因不超过 120 字符。"""
        return _errand_text(reason, ERRAND_TEXT_MAX_CHARS, "errand cancel reason")

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, quantity: int | None) -> int | None:
        """@brief 份数在 1 至 9 之间（null 由交叉约束判定）。"""
        if quantity is not None and not ERRAND_QUANTITY_MIN <= quantity <= ERRAND_QUANTITY_MAX:
            _LOGGER.error("Errand change quantity is out of range")
            raise ValueError(f"errand quantity must be between {ERRAND_QUANTITY_MIN} and {ERRAND_QUANTITY_MAX}")
        return quantity

    @model_validator(mode="after")
    def check_shape(self) -> "ErrandChange":
        """@brief 按变更种类核对交叉约束：新增必须写完成依据、取消必须写原因，其余按表必须为 null；每个违背的键各报一条、
        定位到该键（外层再加 errands 与条目下标），原值照写。"""
        required, null = _ERRAND_REQUIRED[self.change], _ERRAND_NULL[self.change]
        errors: list[Any] = []
        for key in type(self).model_fields:
            value = getattr(self, key)
            if key in required and value is None:
                message = f"{key} is required when change is {self.change}"
            elif key in null and value is not None:
                message = f"{key} must be null when change is {self.change}"
            else:
                continue
            errors.append({"type": "value_error", "loc": (key,), "input": value,
                           "ctx": {"error": ValueError(message)}})
        if errors:
            _LOGGER.error("Errand change violates the constraints of its change kind")
            raise ValidationError.from_exception_data(type(self).__name__, errors)
        return self


#: 三个控制工具请求级 errands 的字段 Schema（内联，不用 $ref）与字段说明。
_ERRANDS_SCHEMA = {"type": "array", "items": ErrandChange.model_json_schema(mode="validation")}
_ERRANDS_PURPOSE = "要新增、更新或取消的个人事务，一条一个对象，没有就写空列表。"


def _validate_errands(tool: str, errands: list[Any]) -> list[Any]:
    """@brief 控制工具 errands 的条数上限：一次请求至多四条变更（strict 端点禁 maxItems，由本地校验执行，可为空列表）；
    重名、同一事务被两条点名等与未完成事务相关的判定在 errands.change_issues（宿主与环境同一冻结观察）。"""
    if len(errands) > MAX_ERRAND_CHANGES:
        _LOGGER.error("Activity control request carries too many errand changes")
        raise ValueError(f"{tool} takes at most four errand changes")
    return errands


class ActivityProgressItem(ToolRequestModel):
    """@brief 播报或暂停的一项：活动名与人物自述。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema("一项活动：活动名与进展。"))
    activity_name: str = Field(pattern=r"\S", description=_ACTIVITY_NAME_FORMAT)
    summary: str = Field(pattern=r"\S", description="用自己的话写这项活动到此刻的进展。")

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, summary: str) -> str:
        """@brief 自述长度受控。"""
        return _validate_summary(summary)


#: 播报与暂停的 items 字段 Schema（内联，不用 $ref）。
_PROGRESS_ITEMS_SCHEMA = {"type": "array", "items": ActivityProgressItem.model_json_schema(mode="validation")}


class ReportStatusRequest(ToolRequestModel):
    """@brief 逐项播报进行中活动的人物自述；点名暂停的活动即在其开始地点恢复它（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要
    第六章：一项一个对象）。

    :param items: 本次播报的一至两项，每项写活动名与进展自述。
    :param errands: 请求级的个人事务变更，零至四条（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 ）。
    """

    model_config = ConfigDict(json_schema_extra=purpose_only_schema())
    items: Annotated[list[ActivityProgressItem], WithJsonSchema(_PROGRESS_ITEMS_SCHEMA)] = Field(
        description="要播报进展的活动，一项一个对象。")
    errands: Annotated[list[ErrandChange], WithJsonSchema(_ERRANDS_SCHEMA)] = Field(description=_ERRANDS_PURPOSE)

    @field_validator("items")
    @classmethod
    def validate_items(cls, items: list[ActivityProgressItem]) -> list[ActivityProgressItem]:
        """@brief 保证一至两项、活动名互不相同。"""
        return _validate_control_items("report_status", items)

    @field_validator("errands")
    @classmethod
    def validate_errands(cls, errands: list[ErrandChange]) -> list[ErrandChange]:
        """@brief 一次请求至多四条事务变更。"""
        return _validate_errands("report_status", errands)


class PauseActivityRequest(ToolRequestModel):
    """@brief 逐项写下人物自述并暂停所选进行中活动；暂停让出名额，恢复要有空名额，路上的活动不能暂停（一项一个对象）。

    :param items: 本次暂停的一至两项，每项写活动名与进展自述。
    :param errands: 请求级的个人事务变更，零至四条（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 ）。
    """

    model_config = ConfigDict(json_schema_extra=purpose_only_schema())
    items: Annotated[list[ActivityProgressItem], WithJsonSchema(_PROGRESS_ITEMS_SCHEMA)] = Field(
        description="要暂停的进行中活动，一项一个对象。")
    errands: Annotated[list[ErrandChange], WithJsonSchema(_ERRANDS_SCHEMA)] = Field(description=_ERRANDS_PURPOSE)

    @field_validator("items")
    @classmethod
    def validate_items(cls, items: list[ActivityProgressItem]) -> list[ActivityProgressItem]:
        """@brief 保证一至两项、活动名互不相同。"""
        return _validate_control_items("pause_activity", items)

    @field_validator("errands")
    @classmethod
    def validate_errands(cls, errands: list[ErrandChange]) -> list[ErrandChange]:
        """@brief 一次请求至多四条事务变更。"""
        return _validate_errands("pause_activity", errands)


#: 一次 conclude_activity 写入的记忆与看法合计上限、单条文字字符上限与关键词个数上限（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动
#: 第七章「finish 并入 conclude_activity」；DeepSeek strict 禁 maxItems/maxLength，由本地校验兜住）。
CONCLUSION_MEMORIES_MAX = 8
MEMORY_TEXT_MAX_CHARS = 1000
MEMORY_KEYWORDS_MAX = 12
#: 记忆文字中不允许出现的十六进制身份（八位以上）。
_MEMORY_HEX_IDENTITY = re.compile(r"[0-9a-fA-F]{8,}")


def validate_memory_text(text: str) -> str:
    """@brief 进入主观记忆的文字的共用校验：不超过 1000 字符且不含八位以上十六进制身份。

    收尾记忆、看法与就寝反思的洞见三处共用这一个函数（docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库-就寝反思经严格工具提交 ），不区分条目来源。

    :param text: 模型写下的一条记忆文字。
    :returns: 通过校验的原文。
    :raises ValueError: 文字超长或含十六进制身份；英文原因由调用方的校验错误带给模型。
    """
    if len(text) > MEMORY_TEXT_MAX_CHARS:
        _LOGGER.error("Memory text exceeds its character limit")
        raise ValueError("memory text exceeds 1000 characters")
    if _MEMORY_HEX_IDENTITY.search(text):
        _LOGGER.error("Memory text contains a hexadecimal identifier")
        raise ValueError("memory text contains a hexadecimal identifier")
    return text


def _validate_memory_keywords(keywords: list[str]) -> list[str]:
    """@brief 关键词上限在具体字段校验以保留完整位置。"""
    if len(keywords) > MEMORY_KEYWORDS_MAX:
        _LOGGER.error("Conclusion memory has too many keywords")
        raise ValueError("memory has more than twelve keywords")
    return keywords


class ConclusionMemory(ToolRequestModel):
    """@brief 结束活动时写下的一条普通记忆（看法与偏好另写在 views）；没有对象字段，来源与认知状态（subjective）由系统
    固定，模型不填任何身份。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema("一条想记住的事：类型、文字、重要程度与关键词。"))
    type: Literal["commitment", "goal", "conflict", "resolution", "identity", "routine", "reflection"] = Field(
        description="这条记忆的类型，从列出的七种里选一种；对人的看法与对事物的偏好另外写。")
    text: str = Field(pattern=r"\S", description="用自己的话写下想记住的事，不写编号或身份串。")
    importance: float = Field(ge=0, le=1, description="这件事对自己的重要程度，0 到 1 之间的数。")
    keywords: list[Annotated[str, Field(pattern=r"\S")]] = Field(description="便于日后回想的几个关键词。")

    @field_validator("text")
    @classmethod
    def validate_text(cls, text: str) -> str:
        """@brief 在文字字段上校验长度与身份泄漏。"""
        return validate_memory_text(text)

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, keywords: list[str]) -> list[str]:
        """@brief 在关键词字段上校验总数。"""
        return _validate_memory_keywords(keywords)


class ConclusionView(ToolRequestModel):
    """@brief 结束活动时写下的一条看法（relationship，对某人）或偏好（preference，对某物）；about 写对象，宿主写入记忆时
    作为 subject 存储（请求里叫 about、存储里叫 subject，指同一件事，接口合同「记忆字段」）。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema("一条看法或偏好：类型、对象、文字、重要程度与关键词。"))
    type: Literal["relationship", "preference"] = Field(
        description="看法的类型：对某个人的看法，或对某样东西、地方、事情的偏好。")
    about: str = Field(pattern=r"\S", description="看法或偏好的对象：人照人设层里的写法写名字或称呼，东西、地方或事情"
                                                  "写它的名称。")
    text: str = Field(pattern=r"\S", description="用自己的话写下这条看法或偏好，不写编号或身份串。")
    importance: float = Field(ge=0, le=1, description="这条看法或偏好对自己的重要程度，0 到 1 之间的数。")
    keywords: list[Annotated[str, Field(pattern=r"\S")]] = Field(description="便于日后回想的几个关键词。")

    @field_validator("text")
    @classmethod
    def validate_text(cls, text: str) -> str:
        """@brief 在文字字段上校验长度与身份泄漏。"""
        return validate_memory_text(text)

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, keywords: list[str]) -> list[str]:
        """@brief 在关键词字段上校验总数。"""
        return _validate_memory_keywords(keywords)


class ActivityConclusionItem(ToolRequestModel):
    """@brief 结束的一项：活动名、总结、普通记忆与看法（两者都必填，可以为空列表）。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema("结束的一项活动：活动名、总结、想记住的事与看法。"))
    activity_name: str = Field(pattern=r"\S", description=_ACTIVITY_NAME_FORMAT)
    summary: str = Field(pattern=r"\S", description="用自己的话写这项活动的总结。")
    memories: Annotated[
        list[ConclusionMemory],
        WithJsonSchema({"type": "array", "items": ConclusionMemory.model_json_schema(mode="validation")}),
    ] = Field(description="关于这项活动、以后做决定用得上的新情况，一条一个对象，没有就写空列表。")
    views: Annotated[
        list[ConclusionView],
        WithJsonSchema({"type": "array", "items": ConclusionView.model_json_schema(mode="validation")}),
    ] = Field(description="这项活动让你形成的对人的看法或对事物的偏好，一条一个对象，没有就写空列表。")

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, summary: str) -> str:
        """@brief 总结长度受控。"""
        return _validate_summary(summary)


class ConcludeActivityRequest(ToolRequestModel):
    """@brief 逐项写下总结、记忆与看法并结束所选活动（进行中、暂停中或刚结束待收尾），进行中与待收尾的结束后释放名额；一项一个对象
    （docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第六章）。

    :param items: 本次结束的一至两项；全部项的 memories 与 views 合计不超过八条。
    :param errands: 请求级的个人事务变更，零至四条（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 ）。
    """

    model_config = ConfigDict(json_schema_extra=purpose_only_schema())
    items: Annotated[
        list[ActivityConclusionItem],
        WithJsonSchema({"type": "array", "items": ActivityConclusionItem.model_json_schema(mode="validation")}),
    ] = Field(description="要结束的活动，一项一个对象。")
    errands: Annotated[list[ErrandChange], WithJsonSchema(_ERRANDS_SCHEMA)] = Field(description=_ERRANDS_PURPOSE)

    @field_validator("items")
    @classmethod
    def validate_items(cls, items: list[ActivityConclusionItem]) -> list[ActivityConclusionItem]:
        """@brief 保证一至两项、活动名互不相同，全部项的记忆与看法合计不超过八条。"""
        _validate_control_items("conclude_activity", items)
        if sum(len(item.memories) + len(item.views) for item in items) > CONCLUSION_MEMORIES_MAX:
            _LOGGER.error("Conclude activity carries too many memories and views")
            raise ValueError("conclude_activity memories and views exceed eight entries in total")
        return items

    @field_validator("errands")
    @classmethod
    def validate_errands(cls, errands: list[ErrandChange]) -> list[ErrandChange]:
        """@brief 一次请求至多四条事务变更。"""
        return _validate_errands("conclude_activity", errands)


ActionRequest = Annotated[
    StartActivityRequest | RecallTaskSummaryRequest |
    ReportStatusRequest | PauseActivityRequest | ConcludeActivityRequest |
    ReviseDayPlanRequest,
    Field(union_mode="left_to_right"),
]


class ActionReceipt(FrozenModel):
    """@brief 决策阶段成功登记动作的回执。"""

    action_id: str = Field(min_length=1)
    round_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    status: Literal["submitted"] = "submitted"
    request_sha256: str = Field(min_length=64, max_length=64)


class ActionResult(FrozenModel):
    """@brief 结算后返回给人物的权威动作结果。"""

    action_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    status: ActionStatus
    occurred_at: int = Field(ge=0)
    activity_id: str | None = None
    error_code: str | None = None
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


def rejection_reason_text(error: Exception) -> str:
    """@brief 拒绝的英文日志文本：业务错误码加完整链式底层原因，只清除十六进制身份。

    只写进运行日志，不进入结果：拒绝结果的 message 与 details 取 life.guidance 生成的公开原因
    （docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第四章「错误合同」），链式底层异常（骨架校验等）可能是请求模型的校验转储。
    """
    reason = str(error)
    cause = error.__cause__
    if cause is not None:
        detail = re.sub(r"[0-9a-fA-F]{8,}", "-", " ".join(str(cause).split()))
        reason = f"{reason}: {detail}"
    return reason


REQUEST_MODELS: dict[str, type[FrozenModel]] = {
    "start_activity": StartActivityRequest,
    "submit_work": SubmitWorkActionRequest,
    "send_message": SendMessageActionRequest,
    "recall_task_summary": RecallTaskSummaryRequest,
    "report_status": ReportStatusRequest,
    "pause_activity": PauseActivityRequest,
    "conclude_activity": ConcludeActivityRequest,
    "revise_day_plan": ReviseDayPlanRequest,
}


_DEEPSEEK_UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "oneOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "default",
})


def deepseek_strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """@brief 直接生成并核验 DeepSeek strict 接受的 Pydantic Schema。

    :param model: 唯一工具参数模型。
    :returns: 未经过删除或改写的模型验证 Schema。
    """
    schema = model.model_json_schema(mode="validation")
    _validate_deepseek_schema_node(schema, "$")
    return schema


def _validate_deepseek_schema_node(value: Any, path: str) -> None:
    """@brief 递归拒绝服务端不支持或对象字段不完整的 Schema。"""
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_deepseek_schema_node(item, f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return
    unsupported = sorted(set(value) & _DEEPSEEK_UNSUPPORTED_SCHEMA_KEYS)
    if unsupported:
        raise ValueError(f"DeepSeek strict schema uses unsupported keys at {path}: {unsupported}")
    if value.get("type") == "object":
        _validate_deepseek_object(value, path)
    for key, item in value.items():
        _validate_deepseek_schema_node(item, f"{path}.{key}")


def _validate_deepseek_object(value: dict[str, Any], path: str) -> None:
    """@brief 要求每个对象封闭且全部属性显式必填。"""
    properties = value.get("properties")
    required = value.get("required")
    if not isinstance(properties, dict) or set(required or []) != set(properties):
        raise ValueError(f"DeepSeek strict object properties are not all required at {path}")
    if value.get("additionalProperties") is not False:
        raise ValueError(f"DeepSeek strict object is not closed at {path}")
