"""@brief LifeEnv 的封闭请求、状态与结果模型。"""

from __future__ import annotations

import logging
from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (BaseModel, ConfigDict, Field, WithJsonSchema, field_serializer,
                      field_validator, model_validator)

from worldmock.life.schedule import MAX_PLAN_GOALS, PLAN_GOAL_MAX_CHARS, DayPlan, IntentKind
from worldmock.life.social import Message

# 预排事件类型与载荷模型已迁至 scheduling.events；此处再导出维持既有导入路径。
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

#: 世界状态结构版本（docs/dev/SPEC-life-realism.md「产品行为」）：旧检查点缺该字段即拒绝加载。
#: 版本 5 保存同人活动集合的共同身体时刻与已交付预警，直接拒绝旧检查点。
WORLD_SCHEMA_VERSION = 5

_LOGGER = logging.getLogger(__name__)


class StrictModel(BaseModel):
    """@brief 拒绝未知字段的领域模型基类。"""

    model_config = ConfigDict(extra="forbid", frozen=False)


class FrozenModel(BaseModel):
    """@brief 拒绝未知字段且不可变的领域模型基类。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelIdentity(FrozenModel):
    """@brief 不包含凭据的冻结模型请求身份。"""

    provider: Literal["openai_compatible"]
    base_url: Literal["https://openrouter.ai/api/v1"]
    requested_model: Literal["deepseek-v4.1-flash"]
    thinking: Literal["disabled", "enabled"]
    tool_choice: Literal["required", "auto"]
    max_retries: Literal[0]
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RunIdentity(FrozenModel):
    """@brief 由 runtime 显式分配并与场景交叉验证的运行身份。"""

    run_id: str = Field(min_length=1)
    source_group_id: str = Field(min_length=1)
    agent_ids: tuple[int, ...]
    start_at: str = Field(min_length=1)
    end_at: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_identity: ModelIdentity


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


#: start_basic_activity 的 activity_type 文本合同：wait/sleep 或 lifestyle:<id>。
LIFESTYLE_TYPE_PREFIX = "lifestyle:"
BASIC_ACTIVITY_TYPE_PATTERN = r"^(?:wait|sleep|lifestyle:[a-z0-9][a-z0-9-]*)$"


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


class CurvePoint(FrozenModel):
    """@brief 分段线性曲线的一个输入输出节点。"""

    input_value: float = Field(ge=0)
    output_value: float = Field(ge=0)


class EfficiencyFactor(FrozenModel):
    """@brief 声明式效率因子：输入资源名 + 输出乘数曲线（引擎侧配置）。"""

    input_resource: str = Field(min_length=1)
    curve: tuple[CurvePoint, ...] = Field(min_length=1)


class BodyState(StrictModel):
    """@brief 人物的可结算身体资源与体感状态（修订合同身体语义，2026-09-18）。

    两过程睡眠（Daan/Beersma/Borbély 1984）：sleep_pressure 清醒时以 18.2h
    时间常数趋近 1、睡眠时以 4.2h 衰减，决定人物此刻困不困；sleep_deficit_hours
    为独立不足账（清醒累积、睡眠恢复，钳制 [0, 24]），驱动效率曲线。旧字段
    旧睡眠债字段已删除，不保留别名。sleep_need_hours 与 chronotype_midsleep
    为人物档案常量（默认 7.5h 与 04:00，experiment_assumption），随状态持久化
    使结算纯函数自包含。mood_valence 只影响体感呈现与记忆重要度加权，不直接
    改变任何动作可行性（docs/dev/SPEC-life-runtime.md「其他体感分级」）。
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


class GameProgress(StrictModel):
    """@brief 可通过命令日志重建的游戏进度。"""

    session_id: str = Field(min_length=1)
    game_id: str = Field(min_length=1)
    commands: list[str] = Field(default_factory=list)
    moves: int = Field(ge=0)
    score: int
    won: bool = False
    lost: bool = False
    admissible_commands: tuple[str, ...] = ()
    public_state_sha256: str = Field(min_length=64, max_length=64)


class AgentState(StrictModel):
    """@brief 一个人物的全部生活状态。"""

    agent_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    balance_account_id: str = Field(min_length=1)
    skill_levels: dict[str, float] = Field(default_factory=dict)
    qualifications: tuple[str, ...] = ()
    # 内容口味权重（docs/dev/SPEC-life-realism.md「画像编译」）：卡片口味 x 条目 genre[] 的确定性匹配，
    # 取代已删除的随机偏好浮点；引擎路径在卡片持久化入库前为空映射（缺项 0）。
    taste_weights: dict[str, float] = Field(default_factory=dict)
    body: BodyState
    inventory: dict[str, dict[str, float]] = Field(default_factory=dict)
    active_effects: set[str] = Field(default_factory=set)
    body_settled_at: int = Field(ge=0)
    delivered_warning_signatures: set[str] = Field(default_factory=set)
    next_decision_at: int = Field(ge=0)
    consecutive_rejections: int = Field(default=0, ge=0)
    decision_failed: bool = False
    content_progress: dict[str, ContentProgress] = Field(default_factory=dict)
    game_sessions: dict[str, GameProgress] = Field(default_factory=dict)
    read_message_ids: set[str] = Field(default_factory=set)
    visible_message_ids: set[str] = Field(default_factory=set)
    visible_task_ids: set[str] = Field(default_factory=set)
    visible_supply_ids: set[str] = Field(default_factory=set)
    # 决策节律簿记（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）：滞回参照=上次实际提供决策时的分级。
    last_decision_time: int | None = Field(default=None, ge=0)
    felt_bands_at_last_decision: dict[str, str] = Field(default_factory=dict)
    people_at_last_decision: frozenset[str] = frozenset()
    known_task_ids: set[str] = Field(default_factory=set)
    pending_wake_reasons: list[str] = Field(default_factory=list)
    # 当日目标只记录人物自我承诺和实际推进证据，不驱动活动执行。
    day_plan: DayPlan | None = None
    plan_submitted_dates: set[str] = Field(default_factory=set)
    # 社交最小可行版（docs/dev/SPEC-life-runtime.md「社交最小可行版」）：收件箱与已读消息。
    inbox: dict[str, Message] = Field(default_factory=dict)
    read_inbox_ids: set[str] = Field(default_factory=set)
    # 情绪与不适脉冲簿记（docs/dev/SPEC-life-runtime.md「其他体感分级」）。
    mood_updated_at: int | None = Field(default=None, ge=0)
    discomfort_started_at: int | None = Field(default=None, ge=0)
    discomfort_pulse_hours: float = Field(default=0.0, ge=0)
    # 供给可见期按实际呈现记账；商品身份不能授权另一门店的供给。
    supply_browsed_at: dict[str, int] = Field(default_factory=dict)
    supply_browsed_location: dict[str, str] = Field(default_factory=dict)
    # 工作材料增量揭示账（docs/dev/SPEC-life-runtime.md「观察投影行为规则」）：
    # 自上次决策以来新读到的区间，单次 ≤2000 字符。
    material_reveals: list[dict[str, Any]] = Field(default_factory=list)

    @field_serializer(
        "active_effects",
        "read_message_ids",
        "visible_message_ids",
        "visible_task_ids",
        "visible_supply_ids",
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

    @field_serializer("resource_claims", when_used="json")
    def serialize_resource_claims(self, value: set[str]) -> list[str]:
        """@brief 按稳定顺序编码活动资源集合。"""
        return sorted(value)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ActivityState":
        """@brief 要求终态具有结束时刻，暂停和活动态没有结束时刻。"""
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
        return self


class DeliveryState(StrictModel):
    """@brief 购买后等待履约的订单状态。"""

    order_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    product_id: str = Field(min_length=1)
    supply_id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    seller_account_id: str = Field(min_length=1)
    price_version: str = Field(min_length=1)
    route_id: str | None
    source: dict[str, Any]
    quantity: int = Field(gt=0)
    destination_id: str = Field(min_length=1)
    fulfillment: FulfillmentKind
    created_at: int = Field(ge=0)
    expected_at: int = Field(gt=0)
    status: Literal["pending", "ready", "delivered", "cancelled"]
    total_price_fen: int = Field(ge=0)


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


class SupplyQuoteState(StrictModel):
    """@brief 保存本次实际生效的门店报价和来源，恢复时不重新推断初值。"""

    price_fen: int = Field(gt=0)
    price_version: str = Field(pattern=r"\S")
    effective_from: int = Field(ge=0)
    effective_to: int | None = Field(ge=0)
    source: dict[str, Any]

    @model_validator(mode="after")
    def validate_interval(self) -> "SupplyQuoteState":
        """@brief 拒绝空或倒置的报价生效区间。"""
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            _LOGGER.error("Supply quote effective interval is invalid")
            raise ValueError("supply quote effective interval is invalid")
        from worldmock.life.map_models import DataSource

        try:
            DataSource.model_validate(self.source)
        except ValueError as error:
            _LOGGER.error("Supply quote source is invalid: %s", error)
            raise ValueError("supply quote source is invalid") from error
        return self


class WorldState(StrictModel):
    """@brief 提交到 FootprintStore 的完整世界状态。"""

    # 结构版本必填（无缺省）：旧检查点缺该字段即拒绝加载（docs/dev/SPEC-life-realism.md「产品行为」）。
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
    supply_stock: dict[str, int]
    supply_quotes: dict[str, SupplyQuoteState | None]
    opening_exceptions: dict[str, dict[str, list[tuple[int, int]]]]
    activities: dict[str, ActivityState] = Field(default_factory=dict)
    deliveries: dict[str, DeliveryState] = Field(default_factory=dict)
    pending_events: list[ScheduledEvent] = Field(default_factory=list)
    processed_actions: dict[str, StoredAction] = Field(default_factory=dict)
    paid_task_ids: set[str] = Field(default_factory=set)
    #: 呈现派生量（docs/CONTRACTS.md「派生量字段」）：按（人物, 对象标识）记录上次呈现
    #: 时刻（UTC 纪元秒），是曝光轮转的判据，不含任何偏好推断。
    presentation_state: dict[int, dict[str, int]] = Field(default_factory=dict)
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
        if any(value < 0 for value in self.supply_stock.values()):
            _LOGGER.error("World supply stock cannot be negative")
            raise ValueError("world supply stock cannot be negative")
        if self.supply_stock.keys() != self.supply_quotes.keys():
            _LOGGER.error("World supply stock and quote identities differ")
            raise ValueError("world supply stock and quote identities differ")
        for agent_id, agent in self.agents.items():
            if agent_id != agent.agent_id:
                raise ValueError("world agent mapping identity mismatch")
            if agent.body_settled_at > self.current_time:
                _LOGGER.error("Agent body settlement is ahead of world time")
                raise ValueError("agent body settlement is ahead of world time")
            if any(quantity < 0 for inventory in agent.inventory.values() for quantity in inventory.values()):
                raise ValueError("agent inventory quantity cannot be negative")
            _validate_supply_visibility(agent, self)
        _validate_activity_collection(self)
        _validate_opening_exceptions(self.opening_exceptions)
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


def _expected_activity_claims(activity: ActivityState) -> set[str]:
    """@brief 从唯一注册表和社交活动声明恢复每项权威资源占用。"""
    if activity.kind in {ActivityKind.CHAT, ActivityKind.CALL}:
        from worldmock.life.social_actions import SOCIAL_RESOURCE_CLAIMS

        return set(SOCIAL_RESOURCE_CLAIMS)
    from worldmock.catalog.affordances import default_affordances

    mode = activity.travel_mode.value if activity.travel_mode is not None else None
    try:
        return set(default_affordances().resource_claims(activity.kind.value, travel_mode=mode))
    except (KeyError, ValueError) as error:
        _LOGGER.error("World activity kind or travel mode has no resource declaration")
        raise ValueError("world activity resource declaration is invalid") from error


def _validate_supply_visibility(agent: AgentState, state: WorldState) -> None:
    """@brief 每条可见供给必须有自己的呈现时刻与地点，不得借另一条供给延长有效期。"""
    identities = set(agent.visible_supply_ids)
    if (identities != set(agent.supply_browsed_at) or identities != set(agent.supply_browsed_location)
            or not identities <= state.supply_stock.keys()
            or any(not 0 <= value <= state.current_time for value in agent.supply_browsed_at.values())
            or not set(agent.supply_browsed_location.values()) <= state.opening_exceptions.keys()):
        _LOGGER.error("Supply visibility records have inconsistent identities or timestamps")
        raise ValueError("supply visibility records are inconsistent")


def _validate_opening_exceptions(exceptions: dict) -> None:
    """@brief 恢复时核验日期和营业分钟区间，不接受损坏或互相矛盾的世界事实。"""
    from worldmock.life.map_models import OpeningInterval

    try:
        for dates in exceptions.values():
            for key, intervals in dates.items():
                if date.fromisoformat(key).isoformat() != key:
                    raise ValueError("opening exception date must use canonical ISO format")
                values = [
                    OpeningInterval(opens=start, closes=end) for start, end in intervals]
                ordered = sorted(values, key=lambda item: item.opens)
                if any(left.closes > right.opens for left, right in zip(ordered, ordered[1:])):
                    raise ValueError("opening exception intervals overlap")
    except ValueError as error:
        _LOGGER.error("World opening exceptions are invalid: %s", error)
        raise ValueError("world opening exceptions are invalid") from error


_TARGETED_ACTIVITY_KINDS = frozenset({
    ActivityKind.WORK,
    ActivityKind.WATCH,
    ActivityKind.PLAY_GAME,
    ActivityKind.READ_MAIL,
    ActivityKind.BROWSE,
    ActivityKind.LIFESTYLE,
})


class StartActivityRequest(FrozenModel):
    """@brief 领域内部统一启动一个有时长生活活动。"""

    activity_type: ActivityKind
    planned_duration_seconds: int = Field(gt=0, le=86_400)
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
    def validate_target(self) -> "StartActivityRequest":
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

    @model_validator(mode="after")
    def validate_speed(self) -> "ChangeActivityRequest":
        """@brief 只允许变速操作携带速度。"""
        needs_speed = self.operation == ActivityChange.CHANGE_SPEED
        if needs_speed != (self.playback_speed is not None):
            raise ValueError("playback_speed is required only for change_speed")
        return self


class StartBasicActivityRequest(FrozenModel):
    """@brief 由模型显式启动等待、睡眠或一种生活活动。

    activity_type 为 "wait"、"sleep" 或 "lifestyle:<activity_id>"（id 取自
    当前观察暴露的生活活动目录条目）；lifestyle 子类型在引擎层解析为
    ActivityKind.LIFESTYLE + target_id，MET 走 body 的 lifestyle 键。
    """

    activity_type: str = Field(pattern=BASIC_ACTIVITY_TYPE_PATTERN)
    planned_duration_seconds: int = Field(ge=60, le=86_400)


class StartTargetedActivityRequest(FrozenModel):
    """@brief 由模型显式启动需要目标的生活活动。"""

    activity_type: Literal["work", "watch", "play_game", "read_mail", "browse"]
    planned_duration_seconds: int = Field(ge=60, le=86_400)
    target_id: str = Field(pattern=r"\S")
    playback_speed: float = Field(ge=0.5, le=2)


def _normalize_cursor(value: str | None) -> str | None:
    """@brief 首屏游标的可空表示归一：字符串 "null"/"None" 与纯空白同 JSON null 处理。

    模型常把合同要求的首屏 `null` 写成字面文本；它们在请求边界归一为 None，
    与候选消费点对空串的既有容忍同语义（presentation 首页判定）。其余文本
    原样放行，载荷合法性由游标解析点负责。
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped in ("null", "None"):
        return None
    return value


class BrowseActivitiesRequest(FrozenModel):
    """@brief 由模型下钻读取一个活动品类目录（activity_summary 的键）。

    纯查询：不启动活动、不消耗内容、不进 content_progress，条目清单只
    作为动作反馈（action_result）返回，供下一决策选取具体目标。
    """

    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    #: 下一页游标；它是候选与呈现合同里唯一的可空字段——首屏提交 `null`。字段本身在
    #: strict 合同下必须显式必填（类型可空），因此无缺省值
    #: （docs/CONTRACTS.md「工具面」的 browse_activities 行）。
    cursor: str | None = Field()

    @field_validator("cursor", mode="after")
    @classmethod
    def _cursor_null_literals_mean_first_page(cls, value: str | None) -> str | None:
        """@brief 字符串 "null"/"None" 与纯空白在请求边界归一为首屏 None。"""
        return _normalize_cursor(value)


class StartTravelRequest(FrozenModel):
    """@brief 由模型显式启动一次有目的地的出行。"""

    planned_duration_seconds: int = Field(ge=60, le=86_400)
    destination_id: str = Field(pattern=r"\S")
    travel_mode: Literal["driving", "walking"]


class SetActivityStateRequest(FrozenModel):
    """@brief 由模型显式暂停、恢复或取消已有活动。"""

    activity_id: str = Field(pattern=r"\S")
    operation: Literal["pause", "resume", "cancel"]


class ChangePlaybackSpeedRequest(FrozenModel):
    """@brief 由模型显式修改正在观看内容的播放速度。"""

    activity_id: str = Field(pattern=r"\S")
    playback_speed: float = Field(ge=0.5, le=2)


class ContinueActivityRequest(FrozenModel):
    """@brief 在反馈边界保持当前活动连续执行到下一边界。"""

    activity_id: str = Field(pattern=r"\S")


class PurchaseItem(FrozenModel):
    """@brief 一笔订单中明确选定的门店供给与数量。"""

    supply_id: str = Field(pattern=r"\S")
    quantity: int = Field(gt=0, le=100)
    price_version: str = Field(pattern=r"\S")


class PurchaseOrderRequest(FrozenModel):
    """@brief 购买 1-5 件同一商家、同一履约方式的商品，一次付款一次配送。

    结算时任一件不可购则整单拒绝，不自动删件（docs/dev/SPEC-life-realism.md「商务与进食」）。
    items 以 WithJsonSchema 内联子模型 Schema（strict 合同禁止嵌套引用
    键，docs/CONTRACTS.md「人物工具与管理接口」认可的唯一内联机制）；1-5 件由结算域校验
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


class DiscoverPlacesRequest(FrozenModel):
    """@brief 登记地点与门店供给发现，查询结果在结算后进入下一次观察。"""

    product_id: str | None = Field(pattern=r"\S")
    business_kind: str | None = Field(pattern=r"\S")
    travel_mode: Literal["walking", "driving"]
    max_travel_seconds: int | None = Field(gt=0, le=86_400)
    cursor: str | None = Field(pattern=r"\S")

    @field_validator("cursor", mode="after")
    @classmethod
    def _cursor_null_literals_mean_first_page(cls, value: str | None) -> str | None:
        """@brief 字符串 "null"/"None" 在请求边界归一为首屏 None（空白仍由 \\S 模式拒绝）。"""
        return _normalize_cursor(value)


class ConsumeItem(FrozenModel):
    """@brief 一次进食中的一件已持有食品与数量（docs/dev/SPEC-life-realism.md「商务与进食」：1-3 件）。"""

    product_id: str = Field(pattern=r"\S")
    quantity: float = Field(gt=0, le=100)


class ConsumeRequest(FrozenModel):
    """@brief 使用已经交付并持有的商品。

    多件（1-3）仅限食品同地点一次进食：用餐时长取最长者、热量与效果累加；
    非食品使用仍为单件（docs/dev/SPEC-life-realism.md「商务与进食」）。单件路径沿用域内时长合同
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
    duration_seconds: int = Field(ge=60, le=14_400)


class PlanGoalInput(FrozenModel):
    """@brief 人物提交的当日目标；不接受时刻、时长或环境推进字段。"""

    intent_kind: IntentKind
    goal: str = Field(pattern=r"\S")

    @model_validator(mode="after")
    def validate_goal_length(self) -> "PlanGoalInput":
        """@brief 限制目标字符数，同时保持受支持的严格工具 Schema。"""
        if len(self.goal) > PLAN_GOAL_MAX_CHARS:
            _LOGGER.error("Day goal exceeds its character limit")
            raise ValueError(f"day goal exceeds {PLAN_GOAL_MAX_CHARS} characters")
        return self


class PlanDayRequest(FrozenModel):
    """@brief 提交当日有序目标；允许空清单，同一意图最多一条。"""

    goals: Annotated[
        tuple[PlanGoalInput, ...],
        WithJsonSchema({
            "type": "array",
            "items": PlanGoalInput.model_json_schema(mode="validation"),
        }),
    ]

    @model_validator(mode="after")
    def validate_goals(self) -> "PlanDayRequest":
        """@brief 请求入口核验容量与意图唯一性。"""
        kinds = [goal.intent_kind for goal in self.goals]
        if len(self.goals) > MAX_PLAN_GOALS or len(kinds) != len(set(kinds)):
            _LOGGER.error("Day goals exceed capacity or contain duplicate intents")
            raise ValueError("day goals must contain at most eight unique intents")
        return self


class ReviseDayPlanRequest(PlanDayRequest):
    """@brief 用请求中的目标替换全部未推进条目，已推进条目由环境保留。"""


class SendMessageRequest(FrozenModel):
    """@brief 向同世界人物或场景联系人发送瞬时消息（docs/dev/SPEC-life-runtime.md「社交最小可行版」）。"""

    recipient_id: str = Field(pattern=r"\S")
    text: str


class StartSocialActivityRequest(FrozenModel):
    """@brief 发起交谈（共同在场）或通话（联系人，10-60 分钟，docs/dev/SPEC-life-runtime.md「社交最小可行版」）。"""

    kind: Literal["chat", "call"]
    partner_id: str = Field(pattern=r"\S")
    duration_minutes: int = Field(gt=0, le=120)


ParallelActivityRequest = Annotated[
    StartBasicActivityRequest | StartTargetedActivityRequest | StartTravelRequest |
    ConsumeRequest | StartSocialActivityRequest,
    Field(union_mode="left_to_right"),
]


class StartParallelActivitiesRequest(FrozenModel):
    """@brief 原子启动至少两项现有持续活动请求，不接受其他工具。"""

    activities: Annotated[
        tuple[ParallelActivityRequest, ...],
        WithJsonSchema({
            "type": "array",
            "items": {"anyOf": [model.model_json_schema(mode="validation") for model in (
                StartBasicActivityRequest, StartTargetedActivityRequest, StartTravelRequest,
                ConsumeRequest, StartSocialActivityRequest)]},
        }),
    ]

    @model_validator(mode="after")
    def validate_count(self) -> "StartParallelActivitiesRequest":
        """@brief 防止一个组合请求退化为零项或单项。"""
        if len(self.activities) < 2:
            _LOGGER.error("Parallel activity request has fewer than two activities")
            raise ValueError("parallel activities require at least two items")
        return self


def activity_start_tool(request: ParallelActivityRequest) -> str:
    """@brief 以请求模型的唯一类型返回原有单项启动工具名。"""
    tools = {
        StartBasicActivityRequest: "start_basic_activity",
        StartTargetedActivityRequest: "start_targeted_activity",
        StartTravelRequest: "start_travel",
        ConsumeRequest: "consume",
        StartSocialActivityRequest: "start_social_activity",
    }
    return tools[type(request)]


class MeetingMinutes(FrozenModel):
    """@brief AMI 会议整理任务公开要求的结构化成果。"""

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


class GameActionRequest(FrozenModel):
    """@brief 向人物自己的 TextWorld 会话发送一个命令。"""

    game_session_id: str = Field(pattern=r"\S")
    command: str = Field(pattern=r"\S")

    @model_validator(mode="after")
    def validate_command_length(self) -> "GameActionRequest":
        """@brief 保留服务端不支持表达的命令长度上限。"""
        if len(self.command) > 200:
            raise ValueError("game command exceeds 200 characters")
        return self


ActionRequest = Annotated[
    StartBasicActivityRequest | StartTargetedActivityRequest | StartTravelRequest |
    StartParallelActivitiesRequest |
    SetActivityStateRequest | ChangePlaybackSpeedRequest | ContinueActivityRequest |
    PurchaseOrderRequest | ConsumeRequest | SubmitWorkRequest | GameActionRequest |
    BrowseActivitiesRequest | DiscoverPlacesRequest | PlanDayRequest | ReviseDayPlanRequest |
    SendMessageRequest | StartSocialActivityRequest,
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
    """@brief 拒绝原因文本：业务错误码加完整链式底层原因，只清除十六进制身份。

    拒绝消息经 recent_action_results 摘要进入 facts/scene（docs/dev/SPEC-life-runtime.md「观察投影行为规则」），模型据此
    修正重试；原因文本来自 BusinessRuleError 抛点的链式底层异常（骨架校验
    等），不含任何十六进制事件/请求身份。
    """
    import re

    reason = str(error)
    cause = error.__cause__
    if cause is not None:
        detail = re.sub(r"[0-9a-fA-F]{8,}", "-", " ".join(str(cause).split()))
        reason = f"{reason}: {detail}"
    return reason


REQUEST_MODELS: dict[str, type[FrozenModel]] = {
    "start_basic_activity": StartBasicActivityRequest,
    "start_targeted_activity": StartTargetedActivityRequest,
    "start_travel": StartTravelRequest,
    "start_parallel_activities": StartParallelActivitiesRequest,
    "set_activity_state": SetActivityStateRequest,
    "change_playback_speed": ChangePlaybackSpeedRequest,
    "continue_activity": ContinueActivityRequest,
    "purchase": PurchaseOrderRequest,
    "consume": ConsumeRequest,
    "submit_work": SubmitWorkRequest,
    "game_action": GameActionRequest,
    "browse_activities": BrowseActivitiesRequest,
    "discover_places": DiscoverPlacesRequest,
    "plan_day": PlanDayRequest,
    "revise_day_plan": ReviseDayPlanRequest,
    "send_message": SendMessageRequest,
    "start_social_activity": StartSocialActivityRequest,
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
