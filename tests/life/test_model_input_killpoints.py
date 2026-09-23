"""模型可控输入在当前工具合同下转成可恢复的业务拒绝。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.errors import BusinessRuleError
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    ActionStatus,
    ActivityKind,
    ActivityStatus,
    AgentState,
    PlanDayRequest,
    PlanGoalInput,
    SendMessageActionRequest,
    StartSocialActivityRequest,
    WorldState,
    deepseek_strict_schema,
)
from worldmock.life.schedule import PlanGoal
from worldmock.life.session_actions import SessionActionMixin


def _pending(tool_name: str = "start_activity", request: dict | None = None) -> SimpleNamespace:
    """构造与冻结观察对应的最小动作身份。"""
    return SimpleNamespace(
        action_id="action-current", agent_id=1, decision_id="decision-current",
        observed_at=100, requested_event_id="request-current", work_review_event_id=None,
        reused=False, tool_name=tool_name, request=request or {},
    )


class _MessageHarness(SessionActionMixin):
    """仅运行可读收件人绑定和既有通讯领域处理器。"""

    def __init__(self, bindings: list[dict]) -> None:
        self.bindings = bindings
        self.state = SimpleNamespace(agents={1: SimpleNamespace(agent_id=1),
                                             2: SimpleNamespace(agent_id=2)}, current_time=100)

    def _frozen_observation(self, pending: SimpleNamespace) -> dict:
        """返回本轮唯一冻结收件人清单。"""
        return {"interaction_bindings": self.bindings}

    def _agent_config(self, agent_id: int) -> SimpleNamespace:
        """本用例没有场景联系人。"""
        return SimpleNamespace(contacts=())

    def _send_message(self, state, pending, request):
        """复用生产通讯校验及业务拒绝映射。"""
        return LifeEngine._send_message(self, state, pending, request)


def test_sleeping_chat_partner_is_business_rejection_without_state_change() -> None:
    """聊天对象正在睡眠时，领域 ValueError 应转成稳定业务拒绝。"""
    initiator = SimpleNamespace(agent_id=1, name="小李", location_id="home")
    partner = SimpleNamespace(agent_id=2, name="小王", location_id="home")
    sleep = SimpleNamespace(activity_id="sleeping", agent_id=2, kind=ActivityKind.SLEEP,
                            status=ActivityStatus.ACTIVE, started_at=50,
                            origin_action_id="earlier-action")
    state = SimpleNamespace(current_time=100, agents={1: initiator, 2: partner},
                            activities={sleep.activity_id: sleep})
    request = StartSocialActivityRequest(kind="chat", partner_id="agent:2", duration_minutes=15)
    with pytest.raises(BusinessRuleError, match="CHAT_PARTNER_NOT_AVAILABLE"):
        LifeEngine._start_social_activity(object(), state, _pending(), request)
    assert state.activities[sleep.activity_id] is sleep


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
    original = harness.state
    with pytest.raises(BusinessRuleError, match=error_code):
        harness._send_message_action(
            harness.state, _pending("send_message"),
            SendMessageActionRequest(recipient_name=recipient_name, text=text))
    assert harness.state is original


def test_plan_goal_keeps_semantic_300_character_limit() -> None:
    """目标内容的领域上限保留，且工具 Schema 仍符合 strict 约束。"""
    boundary = "目" * 300
    assert PlanGoalInput(intent_kind="工作", goal=boundary).goal == boundary
    assert PlanGoal(intent_kind="工作", goal=boundary, registered_at=100).goal == boundary
    with pytest.raises(ValidationError, match="exceeds 300 characters"):
        PlanGoalInput(intent_kind="工作", goal=boundary + "余")
    with pytest.raises(ValidationError, match="at most 300 characters"):
        PlanGoal(intent_kind="工作", goal=boundary + "余", registered_at=100)
    assert deepseek_strict_schema(PlanDayRequest)["type"] == "object"


def test_oversized_plan_goal_is_business_rejection_before_world_mutation() -> None:
    """绕过人物请求构造校验的超长目标在领域分派入口仍被拒绝。"""
    payload = {"goals": [{"intent_kind": "工作", "goal": "备注" * 301}]}
    state = SimpleNamespace()
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        LifeEngine._dispatch(object(), state, _pending("plan_day", payload))


def test_business_rejection_emits_result_and_keeps_world_available(monkeypatch) -> None:
    """连续业务拒绝进入反馈冷却并留下一次后续决策机会。"""
    monkeypatch.setattr("worldmock.life.engine.apply_mood_pulse", lambda body, pulse: body)
    agent = AgentState.model_construct(agent_id=1, next_decision_at=100,
                                       consecutive_rejections=1, body=SimpleNamespace())
    state = WorldState.model_construct(current_time=100, agents={1: agent}, activities={})
    pending = _pending()

    class RejectingEngine:
        """仅隔离实际拒绝处理路径所需的领域分派边界。"""

        scenario = SimpleNamespace(max_consecutive_rejections=2,
                                   activity_feedback_seconds=60)

        def _dispatch(self, state, pending):
            """模拟既有领域处理器的明确业务拒绝。"""
            raise BusinessRuleError("CHAT_PARTNER_NOT_AVAILABLE")

        def _after_rejection(self, state, pending, error):
            """执行生产拒绝节律，不替换反馈结果逻辑。"""
            return LifeEngine._after_rejection(self, state, pending, error)

        def _drain_pending_drafts(self):
            """本用例没有通讯送达事件。"""
            return []

    changed, result, drafts = LifeEngine._resolve(RejectingEngine(), state, pending)
    assert result.status == ActionStatus.REJECTED
    assert changed.agents[1].next_decision_at == 160
    assert changed.agents[1].consecutive_rejections == 2
    assert [item.event_type for item in drafts] == ["action_rejected", "action_result"]
    assert drafts[0].data["error_code"] == "CHAT_PARTNER_NOT_AVAILABLE"
