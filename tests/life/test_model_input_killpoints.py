"""模型可控输入在当前工具合同下转成可恢复的业务拒绝。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.errors import BusinessRuleError
from worldmock.life.decision_coordinator import DecisionCoordinator
from worldmock.life.models import (
    ActionStatus,
    ActivityKind,
    ActivityStatus,
    AgentState,
    PlanDayRequest,
    PlanGoalInput,
    SendMessageActionRequest,
    WorldState,
    deepseek_strict_schema,
)
from worldmock.life.schedule import PlanGoal
from worldmock.life.session_actions import ActionHandlers


def _pending(tool_name: str = "start_activity", request: dict | None = None) -> SimpleNamespace:
    """构造与冻结观察对应的最小动作身份。"""
    return SimpleNamespace(
        action_id="action-current", agent_id=1, decision_id="decision-current",
        observed_at=100, requested_event_id="request-current",
        reused=False, tool_name=tool_name, request=request or {},
    )


class _MessageHarness(ActionHandlers):
    """仅运行可读收件人绑定和既有通讯领域处理器；人物没有进行中的活动（不在驾车途中，发消息不受出行方式限制）。"""

    def __init__(self, bindings: list[dict]) -> None:
        self.context = SimpleNamespace()
        self.bindings = bindings
        self.context.scenario = SimpleNamespace(locations=(), routes=(), agents=(
            SimpleNamespace(agent_id=1, contacts=()), SimpleNamespace(agent_id=2, contacts=())))
        from tests.data_paths import ENVIRONMENT_ROOT
        from worldmock.life.world_setup import load_runtime_scenario

        self.context.semantics = load_runtime_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")[0]
        self.context.rules = SimpleNamespace(actions={})
        self.context.state = SimpleNamespace(agents={1: SimpleNamespace(agent_id=1),
                                             2: SimpleNamespace(agent_id=2)}, activities={}, current_time=100)

    def _frozen_observation(self, pending: SimpleNamespace) -> dict:
        """返回本轮唯一冻结收件人清单。"""
        return {"interaction_bindings": self.bindings}

    def _send_message(self, state, pending, request):
        """复用生产通讯校验及业务拒绝映射。"""
        return ActionHandlers._send_message(self, state, pending, request)


def test_sleeping_chat_partner_is_business_rejection_without_state_change():
    from worldmock.life.social_actions import start_chat
    from worldmock.life.models import ActivityState
    from tests.life.test_content_select import registered_content_world
    semantics,state=registered_content_world()
    now=state.current_time
    state.agents[2].location_id=state.agents[1].location_id
    sleep=ActivityState(activity_id="sleeping",agent_id=2,kind="sleep",status="active",location_id="home",started_at=now,
        last_settled_at=now,elapsed_seconds=0,planned_end_at=now+900,planned_duration_seconds=900,
        origin_action_id="sleep",origin_decision_id="d",origin_request_event_id="r")
    state.activities[sleep.activity_id]=sleep
    chat=ActivityState(activity_id="chatting",agent_id=1,kind="action",status="active",location_id="home",started_at=now,
        last_settled_at=now,elapsed_seconds=0,planned_end_at=now+600,planned_duration_seconds=600,
        origin_action_id="chat",origin_decision_id="d2",origin_request_event_id="r2",
        data={"category":"social","app_id":None,"counterpart":"agent:2"})
    before=state.model_dump(mode="json")
    with pytest.raises(BusinessRuleError,match="CHAT_PARTNER_NOT_AVAILABLE"):
        start_chat(state,chat,"message")
    assert state.model_dump(mode="json")==before


@pytest.mark.parametrize("recipient_name,text,bindings,error_code", [
    ("小王", "饿" * 301, [{"object": "小王", "kind": "person", "object_id": "agent:2"}],
     "MESSAGE_TEXT_TOO_LONG"),
    ("陌生人", "在吗", [{"object": "陌生人", "kind": "person", "object_id": "agent:9"}],
     "RECIPIENT_NOT_FOUND"),
    ("未展示的人", "在吗", [], "RECIPIENT_NOT_FOUND"),
])
def test_send_message_rejects_invalid_text_or_recipient(
    recipient_name: str, text: str, bindings: list[dict], error_code: str,
) -> None:
    """人物只提交可读收件人；超长正文与未知收件人都成为业务拒绝。"""
    harness = _MessageHarness(bindings)
    original = harness.context.state
    with pytest.raises(BusinessRuleError, match=error_code):
        harness._send_message_action(
            harness.context.state, _pending("send_message"),
            SendMessageActionRequest(recipient_name=recipient_name, text=text))
    assert harness.context.state is original


def test_plan_goal_keeps_semantic_300_character_limit() -> None:
    """目标内容的领域上限保留，且工具 Schema 仍符合 strict 约束。"""
    boundary = "目" * 300
    assert PlanGoalInput(intent_kind="工作", goal=boundary, remind_time=None, around_time=None).goal == boundary
    assert PlanGoal(intent_kind="工作", goal=boundary, registered_at=100).goal == boundary
    with pytest.raises(ValidationError, match="exceeds 300 characters"):
        PlanGoalInput(intent_kind="工作", goal=boundary + "余", remind_time=None, around_time=None)
    with pytest.raises(ValidationError, match="at most 300 characters"):
        PlanGoal(intent_kind="工作", goal=boundary + "余", registered_at=100)
    assert deepseek_strict_schema(PlanDayRequest)["type"] == "object"


def test_oversized_plan_goal_is_business_rejection_before_world_mutation() -> None:
    """绕过人物请求构造校验的超长目标在领域分派入口仍被拒绝（plan_day 已不是人物工具，人物只经
    revise_day_plan 改当日目标；时间与能量规格第四章「计划与提醒」）。"""
    payload = {"goals": [{"intent_kind": "工作", "goal": "备注" * 301, "remind_time": None, "around_time": None}]}
    state = SimpleNamespace()
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        ActionHandlers.dispatch(object(), state, _pending("revise_day_plan", payload))


def test_business_rejection_emits_result_and_keeps_world_available(monkeypatch) -> None:
    """业务拒绝留下结果并计入同刻轮数；没有进行中活动时在同一秒再给决策机会（时间与能量规格第一章规则 2）。"""
    monkeypatch.setattr("worldmock.life.decision_coordinator.apply_mood_pulse", lambda body, pulse: body)
    agent = AgentState.model_construct(agent_id=1, next_decision_at=100,
                                       body=SimpleNamespace())
    state = WorldState.model_construct(current_time=100, agents={1: agent}, activities={})
    pending = _pending(request={"answering": None,
                                "items": [{"target": "聊天", "way": "进行", "app":None, "to":"人物：小王", "pay_app":None}]})

    class RejectingActions:
        storefront=SimpleNamespace(drain_receipts=lambda:[])
        """提供决策协调实际需要的领域动作边界。"""

        def dispatch(self, state, pending):
            """模拟聊天领域的明确业务拒绝。"""
            raise BusinessRuleError("CHAT_PARTNER_NOT_AVAILABLE")

        def _frozen_observation(self, pending):
            """提供本轮实际聊天对象，公开拒绝从原逐项请求定位目标。"""
            return {"tool_layer": {"layer": "工具层", "targets": [
                {"name": "聊天", "kind": "action", "phase": None, "state": "睡着", "actions": [
                    {"action": "interact", "way": "进行", "app":None, "available": False,
                     "reason": "The other person cannot currently participate in a chat.",
                     "price": None, "duration": None, "quantity": None, "note": None}], "tools": []}],
                "callable_index": {"navigation_apps": [], "payment_apps": [], "goods": [], "query_categories": []}}}

        def drain_drafts(self):
            """本用例没有待提交通讯事件。"""
            return []

    coordinator = DecisionCoordinator(
        SimpleNamespace(scenario=SimpleNamespace(max_same_moment_decisions=2, activity_feedback_seconds=60)),
        RejectingActions(), None)
    changed, result, drafts = coordinator._resolve(state, pending)
    assert result.status == ActionStatus.REJECTED
    assert changed.agents[1].next_decision_at == 100
    assert changed.agents[1].same_moment_rounds == 1
    assert result.data["same_moment_round"] == 1 and result.data["next_decision_at"] == 100
    assert [item.event_type for item in drafts] == ["action_rejected", "action_result"]
    assert drafts[0].data["error_code"] == "CHAT_PARTNER_NOT_AVAILABLE"
    item, = result.data["items"]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", "items", 0, "target"]
    assert item["written"] == "聊天" and item["error_code"] == "CHAT_PARTNER_NOT_AVAILABLE"
    assert drafts[0].data["items"] == result.data["items"]
