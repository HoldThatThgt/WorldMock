"""@brief LifeEnv 的封闭请求、状态与结果模型。"""

from __future__ import annotations
from worldmock.exchange.types import GoodsCategory

from worldmock.base_models import StrictModel, FrozenModel
from worldmock.errors import IntegrityError
import copy
import logging
import re
from worldmock.life.action_contract import (ACTIVITY_ACTIONS, MAIN_ACTIVITY_ACTIONS, SIDE_ACTIVITY_ACTIONS,
                                             START_TOOLS, activity_slot)
from worldmock.life.sleep_plan import parse_sleep_minutes
from datetime import date
from enum import StrEnum
from collections.abc import Callable, Sequence
from typing import Annotated, Any, Literal

from pydantic import (ValidationError, BaseModel, ConfigDict, Field, StrictBool, WithJsonSchema, field_serializer,
                      field_validator, model_validator)

from worldmock.life.errand_state import (ERRAND_NAME_MAX_CHARS, ERRAND_QUANTITY_MAX, ERRAND_QUANTITY_MIN,
                                         ERRAND_TEXT_MAX_CHARS, MAX_ERRAND_CHANGES, ErrandState)
from worldmock.life.schedule import MAX_PLAN_GOALS, PLAN_GOAL_MAX_CHARS, DayPlan, IntentKind

# 世界状态以调度基础层的预排事件模型保存未来边界。
from worldmock.scheduling.events import ScheduledEvent

#: 世界状态结构版本：版本不符的检查点与导出直接拒绝，不迁移。
WORLD_SCHEMA_VERSION = 15
#: 运行清单结构版本：顶层字段集合或 stage_model_identity 的键集变了才升；版本不符的运行不能打开、恢复或重放，
#: 导出认证同样拒绝，不迁移。
RUN_MANIFEST_SCHEMA_VERSION = 6

_LOGGER = logging.getLogger(__name__)









def purpose_only_schema(purpose: str | None = None) -> Callable[[dict[str, Any]], None]:
    """@brief 模型可见参数 Schema 的 description 只写用途（docs/design/06-agent.md「提示与工具」）。

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
    max_retries: int = Field(strict=True, ge=0, le=10)
    transport_max_retries: int = Field(strict=True, ge=0, le=10)
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RunIdentity(FrozenModel):
    """@brief 由 runtime 显式分配并与场景交叉验证的运行身份。

    git_commit 与 git_dirty 记录启动时的检出状态，非 Git 检出时二者为 null；只记录，不参与恢复比对
    （docs/design/06-agent.md「宿主模型协议」）。
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

    SLEEP = "sleep"
    TRAVEL = "travel"
    INTERACT = "interact"
    PURCHASE = "purchase"
    QUERY = "query"


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


class FulfillmentKind(StrEnum):
    """@brief 订单的履约方式。"""

    DELIVERY = "delivery"
    PICKUP = "pickup"
    STATION_PICKUP = "station_pickup"










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


class SessionStatus(StrEnum):
    """@brief 地点会话的生命周期状态闭集（docs/design/09-io-formats.md「世界、人物与活动」）。"""

    ACTIVE = "active"
    ENDED = "ended"


class SessionKind(StrEnum):
    """@brief 地点会话的种类：一段地点停留（stay）或两地之间的一段路（transit）。"""

    STAY = "stay"
    TRANSIT = "transit"


class SessionTaskKind(StrEnum):
    """@brief 活动关联的任务类型闭集（从原会话移到活动）。"""

    GOAL = "goal"
    NONE = "none"


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


class SameMomentStage(StrEnum):
    """@brief 同刻受限轮阶段闭集（docs/design/04-simulation-engine.md「原子性与恢复」）。

    未达同刻上限时为 open；达到上限后每个受限轮只开放一类动作，阶段依赖上一受限轮的结果，时钟推进复位为 open。
    暂停的活动不占槽位，阶梯不按槽位分流：resume_or_conclude 与 conclude_only 留在闭集中，
    现行阶梯不进入它们（docs/design/04-simulation-engine.md）。
    """

    OPEN = "open"
    CONCLUDE_PENDING = "conclude_pending"
    START_OR_RESUME = "start_or_resume"
    RESUME_OR_CONCLUDE = "resume_or_conclude"
    CONCLUDE_ONLY = "conclude_only"


class NavigationForeground(StrictModel):
    """@brief 导航 app 正在前台的那一段：所属的出行、这趟出行选定的导航 app 和这一段回到前台的时刻。"""

    #: 所属出行：该人物一项进行中的出行活动。
    activity_id: str = Field(min_length=1)
    #: 这趟出行选定的导航 app。
    app_id: str = Field(min_length=1)
    #: 这一段回到前台的时刻：不早于出行开始，不晚于当前时刻。
    opened_at: int = Field(ge=0)


class AgentState(StrictModel):
    """@brief 唯一人物的生活状态与幂等节律簿记。"""

    agent_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    balance_account_id: str = Field(min_length=1)
    body: BodyState
    body_settled_at: int = Field(ge=0)
    next_decision_at: int = Field(ge=0)
    same_moment_rounds: int = Field(default=0, ge=0)
    same_moment_stage: SameMomentStage = SameMomentStage.OPEN
    content_progress: dict[str, ContentProgress] = Field(default_factory=dict)
    last_decision_time: int | None = Field(default=None, ge=0)
    felt_bands_at_last_decision: dict[str, str] = Field(default_factory=dict)
    pending_wake_reasons: list[str] = Field(default_factory=list)
    day_plan: DayPlan | None = None
    plan_submitted_dates: set[str] = Field(default_factory=set)
    errands: dict[str, ErrandState] = Field(default_factory=dict)
    discomfort_started_at: int | None = Field(default=None, ge=0)
    discomfort_pulse_hours: float = Field(default=0.0, ge=0)
    callback_states: dict[str, int] = Field(default_factory=dict)
    callback_once_keys: set[str] = Field(default_factory=set)
    callback_reminder_days: dict[str, str] = Field(default_factory=dict)
    #: 导航正在前台的那一段；空表示不在前台。它不放进出行活动的私有数据，活动快照是人物可见的。
    navigation_foreground: NavigationForeground | None = None

    @field_serializer("plan_submitted_dates", "callback_once_keys", when_used="json")
    def serialize_agent_sets(self, value: set[str]) -> list[str]:
        """@brief 集合按稳定次序保存，恢复不依赖进程哈希种子。"""
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
    media_position_seconds: float = Field(default=0, ge=0)
    route_progress_meters: float = Field(default=0, ge=0)
    next_interaction_at: int | None = Field(default=None, ge=0)
    destination_id: str | None = None
    # 出行活动所用出行方式的身份（自带的步行是 walking，其余是数据集的身份）；其他活动为空。
    travel_method_id: str | None = None
    resource_claims: set[str] = Field(default_factory=set)
    origin_action_id: str = Field(min_length=1)
    origin_decision_id: str = Field(min_length=1)
    origin_request_event_id: str = Field(min_length=1)
    boundary_type: str | None = None
    interruption_reason: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    # 生命周期（docs/design/04-simulation-engine.md「动作、名称与并行」）：终态且 concluded_at 为空即待收尾；
    # report_anchor_at 为播报锚点（开始、恢复与对该活动播报时写入）；session_ids 为先后所属的地点会话。
    concluded_at: int | None = Field(default=None, ge=0)
    report_anchor_at: int | None = Field(default=None, ge=0)
    # 暂停时刻（docs/design/06-agent.md「进度、待办与记忆」）：暂停时写入、恢复时清空，待办层按它排序。
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
    """@brief 活动的任务类型与引用配对：none 时无引用，goal 必带引用且意图等于引用。"""
    if kind == SessionTaskKind.NONE and reference is not None:
        raise ValueError("task_ref must be null when task_kind is none")
    if kind != SessionTaskKind.NONE and reference is None:
        raise ValueError("task_ref required for goal")
    if kind == SessionTaskKind.GOAL and intent != reference:
        _LOGGER.error("Goal activity intent disagrees with its task reference")
        raise ValueError("goal activity intent must match task_ref")


class DeliveryState(StrictModel):
    """@brief 商品履约记录：一份商品一条，保存它的配送组、所属订单的付款、配送与恰好一次收货状态。

    一次购买里 app_id 相同的记录是一张订单，付款字段（payment_app_id、payment_duration_seconds、
    payment_started_at、payment_ended_at）在同一张订单的记录上取值相同；group_key 相同的记录是一个配送组。
    """

    order_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    goods_id: str = Field(min_length=1)
    category: GoodsCategory
    store_id: str | None
    #: 下单的内容 app；到店当场买为空。同一次购买里取值相同的记录是一张订单。
    app_id: str | None
    #: 这张订单抽到的支付 app；空表示直付。
    payment_app_id: str | None
    #: 配送组的键，运费按它合并。
    group_key: str = Field(min_length=1)
    #: 这张订单的付款时长：app_id 与 payment_app_id 都为空的到店直付为 0，其余为 10 至 20 秒。
    payment_duration_seconds: int = Field(strict=True, ge=0, le=20)
    #: 付款段真实开始的时刻；付款时长为 0 的订单恒为空。
    payment_started_at: int | None = Field(default=None, ge=0)
    #: 付款段真实走完的时刻；付款时长为 0 的订单恒为空，被截断的付款段也为空。
    payment_ended_at: int | None = Field(default=None, ge=0)
    quantity: int = Field(strict=True, ge=1, le=1)
    unit_price_fen: int = Field(gt=0)
    #: 运费：每个配送组的第一条记录承担整组运费，其余为 0。
    delivery_fee_fen: int = Field(ge=0)
    total_price_fen: int = Field(ge=0)
    destination_id: str = Field(min_length=1)
    delivery_address_id: str = Field(min_length=1)
    fulfillment: FulfillmentKind
    created_at: int = Field(ge=0)
    #: 真实扣款时刻：一次购买的全部记录同时写入，等于购买完成的时刻。
    paid_at: int | None = Field(default=None, ge=0)
    #: 预计到手或到达的时刻：到店记录是购买开始时刻加实际购买时长，外卖和快递是受理时冻结的预计到达。
    expected_at: int = Field(ge=0)
    arrived_at: int | None = Field(default=None, ge=0)
    received_at: int | None = Field(default=None, ge=0)
    status: Literal["cancelled", "pending", "ready_for_pickup", "delivered"]
    path: tuple[str, ...]
    trip_id: str | None
    distance_meters: int = Field(ge=0)
    prep_seconds: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_payment_phase(self) -> "DeliveryState":
        """@brief 付款时长为 0 只属于到店直付，其余订单为 10 至 20 秒；没有付款段的记录不带付款时刻。"""
        seconds = self.payment_duration_seconds
        direct_at_store = self.app_id is None and self.payment_app_id is None
        valid = seconds == 0 if direct_at_store else 10 <= seconds <= 20
        if not valid:
            _LOGGER.error("Order payment duration disagrees with its payment method")
            raise ValueError("Order payment duration disagrees with its payment method")
        if seconds == 0 and (self.payment_started_at is not None or self.payment_ended_at is not None):
            _LOGGER.error("Order without a payment phase carries payment times")
            raise ValueError("Order without a payment phase carries payment times")
        return self

    @model_validator(mode="after")
    def validate_receipt_time(self) -> "DeliveryState":
        """@brief 未支付和取消无收货，已送达具有真实付款及收货时刻。"""
        shape = (self.arrived_at is not None, self.received_at is not None)
        required = {"cancelled": (False, False), "pending": (False, False),
                    "ready_for_pickup": (True, False), "delivered": (True, True)}
        paid_required = self.status in {"ready_for_pickup", "delivered"}
        invalid_payment = (paid_required and self.paid_at is None
                           or self.status == "cancelled" and self.paid_at is not None)
        if shape != required[self.status] or invalid_payment:
            _LOGGER.error("Order status and payment or receipt timestamp disagree")
            raise ValueError("Order status and payment or receipt timestamp disagree")
        if self.paid_at is not None and self.paid_at < self.created_at:
            _LOGGER.error("Order payment precedes creation")
            raise ValueError("Order payment precedes creation")
        if self.arrived_at is not None and self.arrived_at < self.paid_at:
            _LOGGER.error("Order arrival precedes payment")
            raise ValueError("Order arrival precedes payment")
        if self.received_at is not None and self.received_at < self.arrived_at:
            _LOGGER.error("Order receipt precedes arrival")
            raise ValueError("Order receipt precedes arrival")
        if self.status == "ready_for_pickup" and self.fulfillment != FulfillmentKind.STATION_PICKUP:
            _LOGGER.error("Only station pickup orders may be ready for pickup")
            raise ValueError("Only station pickup orders may be ready for pickup")
        return self


class StoredAction(StrictModel):
    """@brief 一条已结算动作的回执与即时结果：本秒的拒绝计数读它，活动终结时核对开始动作仍在
    （docs/design/04-simulation-engine.md「原子性与恢复」）。"""

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


class DecisionRoundState(StrictModel):
    """@brief 决策轮的预期人物和已登记请求。"""

    round_id: str = Field(min_length=1)
    expected_agent_ids: list[int]
    observed_version: int = Field(ge=0)
    status: Literal["open", "committed", "aborted"] = "open"
    pending_actions: list[PendingAction] = Field(default_factory=list)


class SameMomentPresentation(StrictModel):
    """@brief 一个人物本秒的呈现暂存（docs/design/04-simulation-engine.md「原子性与恢复」）。

    stamps 为本秒各轮实际呈现的对象到时刻映射，时钟推进时才并入 presentation_state。
    """

    stamps: dict[str, int]


#: 条目只替换、从不原地修改的两个世界集合（docs/design/09-io-formats.md「世界、人物与活动」）：深复制世界时换新字典、共享条目。
_SHARED_ENTRY_FIELDS = frozenset({"activities", "processed_actions"})


class WorldState(StrictModel):
    """@brief 提交到 FootprintStore 的完整世界状态。"""

    # 结构版本必填（无缺省）：旧检查点缺该字段即拒绝加载（docs/design/09-io-formats.md「单一状态归属」）。
    schema_version: Literal[WORLD_SCHEMA_VERSION]
    version: int = Field(ge=0)
    current_time: int = Field(ge=0)
    end_time: int = Field(gt=0)
    run_id: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    persona_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    agents: dict[int, AgentState]
    accounts: dict[str, int]
    #: 活动按加入世界的先后保存；JSON 里是按这个先后排列的数组（docs/design/09-io-formats.md「世界、人物与活动」）。
    activities: dict[str, ActivityState] = Field(default_factory=dict)
    sessions: dict[str, SessionState] = Field(default_factory=dict)
    deliveries: dict[str, DeliveryState] = Field(default_factory=dict)
    pending_events: list[ScheduledEvent] = Field(default_factory=list)
    #: 规则仍会读取的已结算动作：本秒的结果与进行中或暂停活动的开始动作
    #: （docs/design/04-simulation-engine.md「原子性与恢复」）。
    processed_actions: dict[str, StoredAction] = Field(default_factory=dict)
    #: 呈现派生量（docs/design/09-io-formats.md「世界、人物与活动」）：按（人物, 对象标识）记录上次呈现
    #: 时刻（UTC 纪元秒），是曝光轮转的判据，不含任何偏好推断。
    presentation_state: dict[int, dict[str, int]] = Field(default_factory=dict)
    #: 按人物暂存的本秒呈现留痕：同一秒各轮沿用该秒首轮的呈现窗口，时钟推进时逐人并入 presentation_state 并清空。
    same_moment_presentation: dict[int, SameMomentPresentation] = Field(default_factory=dict)
    weather_by_date: dict[str, str] = Field(default_factory=dict)
    run_start_callbacks_applied: bool = False
    run_stop_callbacks_applied: bool = False
    random_seed: int
    random_state: str = Field(min_length=1)

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> "WorldState":
        """@brief 深复制世界：activities 与 processed_actions 换新字典、共享条目，其余字段照常深复制。

        条目是不可变值，变化一律换成新条目（docs/design/09-io-formats.md「世界、人物与活动」），所以候选世界与已提交世界
        共享没变的条目时互不影响。model_copy(deep=True) 与 copy.deepcopy 都经过这里。条目类不定义 __deepcopy__：
        model_copy(update=..., deep=True) 会把更新写进 __deepcopy__ 返回的对象，返回共享条目就会改坏已提交世界。
        @param memo copy.deepcopy 的备忘表；model_copy 不传时新建一张，整份世界共用
        @return 新的世界实例
        """
        memo = {} if memo is None else memo
        copied = self.__copy__()
        for name, value in self.__dict__.items():
            copied.__dict__[name] = dict(value) if name in _SHARED_ENTRY_FIELDS else copy.deepcopy(value, memo)
        return copied

    @field_serializer("activities", when_used="json")
    def serialize_activities(self, value: dict[str, ActivityState]) -> list[ActivityState]:
        """@brief 活动按加入世界的先后写成数组：规范 JSON 只给对象的键排序，恢复后的先后与不中断的运行相同。"""
        return list(value.values())

    @field_validator("activities", mode="before")
    @classmethod
    def activities_from_array(cls, value: Any) -> dict[str, Any]:
        """@brief 数组按先后还原为以 activity_id 为键的映射；不是活动对象数组或身份重复即拒绝，不接受映射写法。"""
        if not isinstance(value, list):
            _LOGGER.error("World activities are not an array of activity objects")
            raise ValueError("world activities must be an array of activity objects")
        mapping: dict[str, Any] = {}
        for item in value:
            identity = item.activity_id if isinstance(item, ActivityState) else (
                item.get("activity_id") if isinstance(item, dict) else None)
            if not isinstance(identity, str):
                _LOGGER.error("World activities are not an array of activity objects")
                raise ValueError("world activities must be an array of activity objects")
            if identity in mapping:
                _LOGGER.error("World activities repeat the activity identity %s", identity)
                raise ValueError("world activities repeat an activity identity")
            mapping[identity] = item
        return mapping

    @model_validator(mode="after")
    def validate_world(self) -> "WorldState":
        """@brief 核对时钟、账户、库存、人物活动集合，以及各张订单的记录付款字段一致。"""
        if self.current_time > self.end_time:
            raise ValueError("world current time exceeds experiment end")
        expected = {f"agent:{agent_id}" for agent_id in self.agents} | {"external:scenario"}
        if len(self.agents) != 1 or set(self.accounts) != expected:
            _LOGGER.error("World must contain exactly one agent and its two accounts")
            raise ValueError("World must contain exactly one agent and its two accounts")
        if any(self.accounts[f"agent:{agent_id}"] < 0 for agent_id in self.agents):
            _LOGGER.error("Agent account balance cannot be negative")
            raise ValueError("Agent account balance cannot be negative")
        for agent_id, agent in self.agents.items():
            if agent_id != agent.agent_id:
                raise ValueError("world agent mapping identity mismatch")
            if agent.body_settled_at > self.current_time:
                _LOGGER.error("Agent body settlement is ahead of world time")
                raise ValueError("agent body settlement is ahead of world time")
        _validate_activity_collection(self)
        _validate_session_collection(self)
        _validate_order_collection(self)
        _validate_navigation_foreground(self)
        return self


def active_activities(state: WorldState, agent_id: int) -> tuple[ActivityState, ...]:
    """@brief 按开始时刻与身份稳定返回人物全部正在执行的活动。"""
    return tuple(sorted((activity for activity in state.activities.values()
                         if activity.agent_id == agent_id and activity.status == ActivityStatus.ACTIVE),
                        key=lambda activity: (activity.started_at, activity.activity_id)))


def active_side_activities(state: WorldState, agent_id: int) -> tuple[ActivityState, ...]:
    """@brief 人物进行中的槽位 2 活动：状态为进行中、占槽位 2 的购买、查询或用 app。导航在不在前台只看它。"""
    return tuple(activity for activity in active_activities(state, agent_id)
                 if "second" in activity.resource_claims)


def _validate_navigation_foreground(state: WorldState) -> None:
    """@brief 导航在前台时，所指的必须是该人物进行中的出行，打开时刻在出行开始与当前时刻之间，且没有进行中的槽位 2 活动。"""
    for agent_id, agent in state.agents.items():
        foreground = agent.navigation_foreground
        if foreground is None:
            continue
        trip = state.activities.get(foreground.activity_id)
        travelling = (trip is not None and trip.agent_id == agent_id and trip.kind == ActivityKind.TRAVEL
                      and trip.status == ActivityStatus.ACTIVE)
        if (not travelling or not trip.started_at <= foreground.opened_at <= state.current_time
                or active_side_activities(state, agent_id)):
            _LOGGER.error("Navigation foreground disagrees with active activities")
            raise ValueError("navigation foreground disagrees with active activities")


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
        occupants = (item for item in state.activities.values() if item.agent_id == agent_id
                     and (item.status == ActivityStatus.ACTIVE
                          or item.status in CONCLUDABLE_TERMINAL_STATUSES and item.concluded_at is None))
        for activity in occupants:
            if claimed & activity.resource_claims:
                _LOGGER.error("World active activities claim the same resource")
                raise ValueError("world active activities claim the same resource")
            claimed.update(activity.resource_claims)
        sleeping = any(item.kind == ActivityKind.SLEEP for item in active_activities(state, agent_id))
        if sleeping and "second" in claimed:
            _LOGGER.error("Sleep requires the second activity slot to be empty")
            raise ValueError("Sleep requires the second activity slot to be empty")


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


def _validate_order_collection(state: WorldState) -> None:
    """@brief 恢复时拒绝购买活动列出未知的商品履约记录，或同一张订单的记录付款字段不一致。

    订单是一次购买里 app_id 相同的全部记录：它们的支付 app、付款时长和付款段的真实起止必须相同。
    """
    for activity in state.activities.values():
        if activity.kind != ActivityKind.PURCHASE:
            continue
        payments: dict[str | None, tuple] = {}
        for identity in activity.data.get("order_ids", ()):
            order = state.deliveries.get(identity)
            if order is None:
                _LOGGER.error("Purchase activity refers to an unknown order record")
                raise ValueError("Purchase activity refers to an unknown order record")
            payment = (order.payment_app_id, order.payment_duration_seconds,
                       order.payment_started_at, order.payment_ended_at)
            if payments.setdefault(order.app_id, payment) != payment:
                _LOGGER.error("Records of one order disagree on its payment fields")
                raise ValueError("Records of one order disagree on its payment fields")


def _expected_activity_claims(activity: ActivityState) -> set[str]:
    """@brief 两个槽位的占用只从真实动作与所用内容 app 恢复，不从环境加载。"""
    action = activity.data.get("action")
    if action not in ACTIVITY_ACTIONS:
        _LOGGER.error("Activity action is missing or unknown")
        raise ValueError("Activity action is missing or unknown")
    return {activity_slot(action, activity.data.get("app_id"))}


class ChangeActivityRequest(FrozenModel):
    """@brief 已有活动的暂停、恢复或退出请求。"""

    activity_id: str = Field(min_length=1)
    operation: ActivityChange


class PlanGoalInput(FrozenModel):
    """@brief 当日目标条目：意图、目标文字与两个必填可空的场景本地 HH:MM——remind_time（要在那一刻被提醒时填写，
    否则为 null）与 around_time（打算在几点前后做，docs/design/04-simulation-engine.md
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
    （goals[i].intent_kind，原值为该意图），按下标升序。不因超限提前返回（docs/design/04-simulation-engine.md）。

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


def _cross_errors(model_name: str, problems: list[tuple[str, Any, str]]) -> ValidationError:
    """@brief 把交叉约束的各条违背写成逐字段定位的校验错误：（字段，所写原值，英文原因）各一条。"""
    _LOGGER.error("Start request violates its cross-field constraints")
    return ValidationError.from_exception_data(model_name, [
        {"type": "value_error", "loc": (field,), "input": written, "ctx": {"error": ValueError(reason)}}
        for field, written, reason in problems])


class StartMainActivityRequest(ToolRequestModel):
    """@brief 开始一项占槽位 1 的活动：出行、在当前地点做一件事，或睡觉。"""

    action: Literal[MAIN_ACTIVITY_ACTIONS] = Field(
        description="做什么：travel 出行，interact 在当前地点做一件事，sleep 睡觉。")
    target: str = Field(pattern=r"\S", description=(
        "travel 写目的地地点的完整名称；interact 写当前所在地点的完整名称；sleep 写要睡的分钟数，只写数字，例如 470。"))
    detail: Annotated[str, Field(pattern=r"\S")] | None = Field(description=(
        "travel 写出行方式的名称，照工具层该目的地下列出的写；interact 写要做的那件事的名称，"
        "照工具层当前地点下列出的写；sleep 写 null。"))

    @model_validator(mode="after")
    def validate_action_arguments(self) -> "StartMainActivityRequest":
        """@brief 出行与做事必须写 detail；睡眠的 target 是 10 到 720 的分钟数、detail 必须为 null，各条违背各报一条。"""
        problems: list[tuple[str, Any, str]] = []
        if self.action != "sleep":
            if self.detail is None:
                problems.append(("detail", None, f"detail is required when action is {self.action}"))
        else:
            try:
                parse_sleep_minutes(self.target)
            except ValueError as error:
                problems.append(("target", self.target, str(error)))
            if self.detail is not None:
                problems.append(("detail", self.detail, "detail must be null when action is sleep"))
        if problems:
            raise _cross_errors(type(self).__name__, problems)
        return self


class StartSideActivityRequest(ToolRequestModel):
    """@brief 开始一项占槽位 2 的活动：购买商品、查询商品，或使用一个内容 app。"""

    action: Literal[SIDE_ACTIVITY_ACTIONS] = Field(
        description="做什么：purchase 购买商品，interact 使用内容 app，query 查询商品。")
    target: list[Annotated[str, Field(pattern=r"\S")]] = Field(description=(
        "名称数组。purchase 逐份写商品的完整名称，同一个名称写两次就是两份；interact 只写一个内容 app 的完整名称；"
        "query 写商品的完整名称或商品类别的名称。"))
    #: 想用来下单或在其中查询的 app 名称原文，必填、没有缺省值；没有想法写 null（模型的工具格式不允许可省略的字段）。
    prefer: Annotated[str, Field(pattern=r"\S")] | None = Field(
        description="purchase 写想用来下单的内容 app 的完整名称，query 写想在其中查询的内容 app 的完整名称，"
                    "没有想法写 null；interact 写 null。")

    @model_validator(mode="after")
    def validate_action_arguments(self) -> "StartSideActivityRequest":
        """@brief 名称数组至少一项；用内容 app 时恰好一项且 prefer 必须为 null；购买与查询不设上限并允许同名重复。
        每条违背各报一条。"""
        problems: list[tuple[str, Any, str]] = []
        if not self.target:
            problems.append(("target", self.target, "target requires at least one name"))
        if self.action == "interact" and len(self.target) != 1:
            problems.append(("target", self.target, "target takes exactly one app name when action is interact"))
        if self.action == "interact" and self.prefer is not None:
            problems.append(("prefer", self.prefer, "prefer must be null when action is interact"))
        if problems:
            raise _cross_errors(type(self).__name__, problems)
        return self


class RecallTaskSummaryRequest(ToolRequestModel):
    """@brief 按本轮可见的任务、当日目标或活动名称只读回看进展（会话名不在七层中出现，不可回看）。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema())
    task_name: str = Field(pattern=r"\S")


#: 控制工具每项活动名的「格式与来源」说明。字段级说明是参数 Schema 的一部分，不是工具 description：只写格式与来源，
#: 不写规则与上限（上限由本地校验执行，docs/design/04-simulation-engine.md）。
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
    if len(summary) > 500:
        _LOGGER.error("Activity control summary exceeds its character limit")
        raise ValueError("must be within 500 characters")
    return summary


#: HH:MM 钟点的 Schema 格式（与当日目标的 remind_time 同名同形）。
_CLOCK_PATTERN = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
#: 个人事务变更的交叉约束（docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）：按变更种类必须非空的键与必须为 null 的键；
#: 其余键（新增的 memo、remind_time，更新的 memo、remind_time）可空。
_ERRAND_REQUIRED = {"新增": ("item", "quantity"), "更新": (), "取消": ("reason",)}
_ERRAND_NULL = {"新增": ("reason",), "更新": ("item", "quantity", "reason"),
                "取消": ("item", "quantity", "memo", "remind_time")}


def _errand_text(value: str | None, limit: int, what: str) -> str | None:
    """@brief 事务文字的字符上限（strict 端点禁 maxLength，由本地校验执行，上限只写在原因里）；null 原样通过。

    :param what: 英文原因里的字段说法（errand name、errand memo、errand cancel reason）。
    """
    if value is not None and len(value) > limit:
        _LOGGER.error("Errand change text exceeds its character limit")
        raise ValueError(f"{what} must be within {limit} characters")
    return value


class ErrandChange(ToolRequestModel):
    """@brief 控制工具请求级 errands 的一条个人事务变更：封闭对象，七个键全部必填，
    按变更种类的交叉约束与字符、份数上限由本地校验执行（上限写在原因里，Schema 只给类型与格式）；没有「完成」。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema(
        "一条个人事务的变更：做什么、名称、要办成的事、备注、提醒钟点与取消原因。"))
    change: Literal["新增", "更新", "取消"] = Field(description="这条变更做什么，从列出的三种里选一种。")
    name: str = Field(pattern=r"\S", description="事务的名称，新增时自己起，之后照待办层里的写法写。")
    item: Annotated[str, Field(pattern=r"\S")] | None = Field(description="要取得的商品名称。")
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
    """@brief 逐项播报进行中活动的人物自述；点名暂停的活动即在其开始地点恢复它（docs/design/04-simulation-engine.md
    ：一项一个对象）。

    :param items: 本次播报的一至两项，每项写活动名与进展自述。
    :param errands: 请求级的个人事务变更，零至四条（docs/design/04-simulation-engine.md）。
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
    """@brief 逐项写下人物自述并暂停所选进行中活动；暂停让出槽位，恢复要有空槽位，路上的活动不能暂停（一项一个对象）。

    :param items: 本次暂停的一至两项，每项写活动名与进展自述。
    :param errands: 请求级的个人事务变更，零至四条（docs/design/04-simulation-engine.md）。
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


#: 一次 conclude_activity 写入的记忆与看法合计上限、单条文字字符上限与关键词个数上限（docs/design/04-simulation-engine.md
#: ；DeepSeek strict 禁 maxItems/maxLength，由本地校验兜住）。
CONCLUSION_MEMORIES_MAX = 8
MEMORY_TEXT_MAX_CHARS = 1000
MEMORY_KEYWORDS_MAX = 12
#: 记忆文字中不允许出现的十六进制身份（八位以上）。
_MEMORY_HEX_IDENTITY = re.compile(r"[0-9a-fA-F]{8,}")


def validate_memory_text(text: str) -> str:
    """@brief 进入主观记忆的文字的共用校验：不超过 1000 字符且不含八位以上十六进制身份。

    收尾记忆、看法与就寝反思的洞见三处共用这一个函数（docs/design/06-agent.md），不区分条目来源。

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
    作为 subject 存储（请求里叫 about、存储里叫 subject，指同一件事，docs/design/00-overview.md「数据质量原则」）。"""

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
    """@brief 逐项写下总结、记忆与看法并结束所选活动（进行中、暂停中或刚结束待收尾），进行中与待收尾的结束后释放槽位；一项一个对象
    （docs/design/04-simulation-engine.md）。

    :param items: 本次结束的一至两项；全部项的 memories 与 views 合计不超过八条。
    :param errands: 请求级的个人事务变更，零至四条（docs/design/04-simulation-engine.md）。
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
    StartMainActivityRequest | StartSideActivityRequest | RecallTaskSummaryRequest |
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
    （docs/design/04-simulation-engine.md「动作、名称与并行」），链式底层异常（骨架校验等）可能是请求模型的校验转储。
    """
    reason = str(error)
    cause = error.__cause__
    if cause is not None:
        detail = re.sub(r"[0-9a-fA-F]{8,}", "-", " ".join(str(cause).split()))
        reason = f"{reason}: {detail}"
    return reason


REQUEST_MODELS: dict[str, type[FrozenModel]] = {
    **dict(zip(START_TOOLS, (StartMainActivityRequest, StartSideActivityRequest), strict=True)),
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


def validated_payload(state: WorldState) -> dict:
    """@brief 序列化并按加载口径复校世界状态，域不变量违规即拒绝。

    @param state 待写入的世界状态实例
    @return 已通过 WorldState.model_validate 复校的 JSON 载荷
    @exception IntegrityError 五条域不变量任一不成立
    """
    payload = state.model_dump(mode="json")
    try:
        WorldState.model_validate(payload)
    except ValueError as error:
        raise IntegrityError(
            f"World state invariant violated before persisting: {error}") from error
    return payload
