"""@brief LifeEnv 的封闭请求、状态与结果模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, model_validator


class StrictModel(BaseModel):
    """@brief 拒绝未知字段的领域模型基类。"""

    model_config = ConfigDict(extra="forbid", frozen=False)


class FrozenModel(BaseModel):
    """@brief 拒绝未知字段且不可变的领域模型基类。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelIdentity(FrozenModel):
    """@brief 不包含凭据的冻结模型请求身份。"""

    provider: Literal["deepseek"]
    base_url: Literal["https://api.deepseek.com/beta"]
    requested_model: Literal["deepseek-flash"]
    thinking: Literal["disabled"]
    tool_choice: Literal["required"]
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


class ScheduledKind(StrEnum):
    """@brief 环境自主到期事件的封闭类型。"""

    MAIL_ARRIVAL = "mail_arrival"
    DELIVERY_READY = "delivery_ready"
    DELIVERY_ARRIVAL = "delivery_arrival"
    WORK_AVAILABLE = "work_available"
    TASK_EXPIRED = "task_expired"


class CurvePoint(FrozenModel):
    """@brief 分段线性曲线的一个输入输出节点。"""

    input_value: float = Field(ge=0)
    output_value: float = Field(ge=0)


class BodyState(StrictModel):
    """@brief 人物的可结算身体资源。"""

    energy_kcal: float = Field(ge=0)
    sleep_debt_hours: float = Field(ge=0)
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
    entertainment_preferences: dict[str, float] = Field(default_factory=dict)
    body: BodyState
    inventory: dict[str, dict[str, float]] = Field(default_factory=dict)
    active_effects: set[str] = Field(default_factory=set)
    current_activity_id: str | None = None
    next_decision_at: int = Field(ge=0)
    consecutive_rejections: int = Field(default=0, ge=0)
    decision_failed: bool = False
    content_progress: dict[str, ContentProgress] = Field(default_factory=dict)
    game_sessions: dict[str, GameProgress] = Field(default_factory=dict)
    read_message_ids: set[str] = Field(default_factory=set)
    visible_message_ids: set[str] = Field(default_factory=set)
    visible_task_ids: set[str] = Field(default_factory=set)
    visible_product_ids: set[str] = Field(default_factory=set)


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

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ActivityState":
        """@brief 要求终态具有结束时刻，暂停和活动态没有结束时刻。"""
        terminal = {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED,
                    ActivityStatus.FAILED, ActivityStatus.TRUNCATED}
        if (self.status in terminal) != (self.ended_at is not None):
            raise ValueError("activity ended_at disagrees with lifecycle status")
        if self.last_settled_at < self.started_at or self.elapsed_seconds > self.planned_duration_seconds:
            raise ValueError("activity progress interval is invalid")
        return self


class DeliveryState(StrictModel):
    """@brief 购买后等待履约的订单状态。"""

    order_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    product_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    destination_id: str = Field(min_length=1)
    fulfillment: FulfillmentKind
    created_at: int = Field(ge=0)
    expected_at: int = Field(gt=0)
    status: Literal["pending", "ready", "delivered", "cancelled"]
    total_price_fen: int = Field(gt=0)


class ScheduledEvent(StrictModel):
    """@brief 模拟时间到期时由环境处理的事件。"""

    scheduled_id: str = Field(min_length=1)
    kind: ScheduledKind
    due_at: int = Field(ge=0)
    sequence: int = Field(ge=0)
    agent_id: int | None = Field(default=None, gt=0)
    data: dict[str, Any] = Field(default_factory=dict)


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


class WorldState(StrictModel):
    """@brief 提交到 FootprintStore 的完整世界状态。"""

    version: int = Field(ge=0)
    current_time: int = Field(ge=0)
    end_time: int = Field(gt=0)
    run_id: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    scenario_sha256: str = Field(min_length=64, max_length=64)
    content_manifest_sha256: str = Field(min_length=64, max_length=64)
    agents: dict[int, AgentState]
    accounts: dict[str, int]
    merchant_stock: dict[str, int]
    activities: dict[str, ActivityState] = Field(default_factory=dict)
    deliveries: dict[str, DeliveryState] = Field(default_factory=dict)
    pending_events: list[ScheduledEvent] = Field(default_factory=list)
    processed_actions: dict[str, StoredAction] = Field(default_factory=dict)
    paid_task_ids: set[str] = Field(default_factory=set)
    accepted_submission_ids: set[str] = Field(default_factory=set)
    random_seed: int
    random_state: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_world(self) -> "WorldState":
        """@brief 核对时钟、账户、库存和人物当前活动引用。"""
        if self.current_time > self.end_time:
            raise ValueError("world current time exceeds experiment end")
        if any(value < 0 for value in self.accounts.values()):
            raise ValueError("world account balance cannot be negative")
        if any(value < 0 for value in self.merchant_stock.values()):
            raise ValueError("world merchant stock cannot be negative")
        for agent_id, agent in self.agents.items():
            if agent_id != agent.agent_id:
                raise ValueError("world agent mapping identity mismatch")
            if agent.current_activity_id and agent.current_activity_id not in self.activities:
                raise ValueError("agent current activity does not exist")
            if any(quantity < 0 for inventory in agent.inventory.values() for quantity in inventory.values()):
                raise ValueError("agent inventory quantity cannot be negative")
        return self


_TARGETED_ACTIVITY_KINDS = frozenset({
    ActivityKind.WORK,
    ActivityKind.WATCH,
    ActivityKind.PLAY_GAME,
    ActivityKind.READ_MAIL,
    ActivityKind.BROWSE,
})


class StartActivityRequest(FrozenModel):
    """@brief 领域内部统一启动一个有时长生活活动。"""

    activity_type: ActivityKind
    planned_duration_seconds: int = Field(gt=0, le=86_400)
    target_id: str | None = Field(
        default=None,
        description="Required for work, watch, play_game, read_mail, and browse.",
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
    """@brief 由模型显式启动等待或睡眠。"""

    activity_type: Literal["wait", "sleep"]
    planned_duration_seconds: int = Field(gt=0, le=86_400)


class StartTargetedActivityRequest(FrozenModel):
    """@brief 由模型显式启动需要目标的生活活动。"""

    activity_type: Literal["work", "watch", "play_game", "read_mail", "browse"]
    planned_duration_seconds: int = Field(gt=0, le=86_400)
    target_id: str = Field(pattern=r"\S")
    playback_speed: float = Field(ge=0.5, le=2)


class StartTravelRequest(FrozenModel):
    """@brief 由模型显式启动一次有目的地的出行。"""

    planned_duration_seconds: int = Field(gt=0, le=86_400)
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


class PurchaseRequest(FrozenModel):
    """@brief 购买一个冻结目录中的商品。"""

    product_id: str = Field(pattern=r"\S")
    quantity: int = Field(gt=0, le=100)
    fulfillment: Literal["delivery", "pickup"]
    destination_id: str = Field(pattern=r"\S")
    price_version: str = Field(pattern=r"\S")


class ConsumeRequest(FrozenModel):
    """@brief 使用已经交付并持有的商品。"""

    product_id: str = Field(pattern=r"\S")
    quantity: float = Field(gt=0, le=100)
    use_mode: Literal["eat", "use"]
    duration_seconds: int = Field(gt=0, le=14_400)


class MeetingMinutes(FrozenModel):
    """@brief AMI 会议整理任务公开要求的结构化成果。"""

    meeting_id: str = Field(pattern=r"\S")
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
    submission_id: str = Field(pattern=r"\S")
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
    SetActivityStateRequest | ChangePlaybackSpeedRequest | ContinueActivityRequest |
    PurchaseRequest | ConsumeRequest | SubmitWorkRequest | GameActionRequest,
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


REQUEST_MODELS: dict[str, type[FrozenModel]] = {
    "start_basic_activity": StartBasicActivityRequest,
    "start_targeted_activity": StartTargetedActivityRequest,
    "start_travel": StartTravelRequest,
    "set_activity_state": SetActivityStateRequest,
    "change_playback_speed": ChangePlaybackSpeedRequest,
    "continue_activity": ContinueActivityRequest,
    "purchase": PurchaseRequest,
    "consume": ConsumeRequest,
    "submit_work": SubmitWorkRequest,
    "game_action": GameActionRequest,
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
