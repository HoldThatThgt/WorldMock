"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations
from worldmock.foundation.models import StrictModel
from worldmock.foundation.errors import IntegrityError
import copy
from worldmock.exchange_engine.conditions.activity_rules import ACTIVITY_ACTIONS, activity_slot
from typing import Any, Literal
from pydantic import Field, field_serializer, field_validator, model_validator
from worldmock.state.planning.events import ScheduledEvent

from worldmock.state.world.kinds import ActivityKind, ActivityStatus, CONCLUDABLE_TERMINAL_STATUSES, SessionStatus
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.agent import AgentState, SessionState
from worldmock.state.world.orders import DeliveryState
from worldmock.state.decisions.state import SameMomentPresentation, StoredAction
from worldmock.state.logging import _LOGGER

#: 世界状态结构版本：版本不符的检查点与导出直接拒绝，不迁移。
WORLD_SCHEMA_VERSION = 15



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
