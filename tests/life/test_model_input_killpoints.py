"""模型可控输入触发引擎严格路径的四条杀 run 死点回归（unit-16 E1-E4）。

对照 tests/life 的真实引擎入口：E1（chat 对象不可交谈）、E3（send_message
超长/未知收件人）、E2（plan_day 超长 goal）修复后都必须是 action_rejected
业务拒绝（引擎可见、可重试、run 存活），而不是裸异常穿透 commit_decision_round。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from agentsociety2.society import DirectToolCall

from tests.life.conftest import context
from worldmock.life.models import (
    PlanDayRequest,
    PlanGoalInput,
    SendMessageRequest,
    StartBasicActivityRequest,
    StartSocialActivityRequest,
    deepseek_strict_schema,
)
from worldmock.life.schedule import PlanGoal


def _round(engine, round_id: str, requests: dict[int, tuple[str, object]]):
    """按就绪人物开轮提交；未指定动作者补 wait（commit 协议要求人人有动作）。"""
    ready = engine.scheduling_state().ready_agent_ids
    assert set(requests) <= set(ready)
    filled = dict(requests)
    for agent_id in ready:
        filled.setdefault(agent_id, ("start_basic_activity",
                          StartBasicActivityRequest(activity_type="wait",
                                                    planned_duration_seconds=60)))
    contexts = [context(engine, round_id, agent_id) for agent_id in ready]
    engine.begin_decision_round(contexts)
    for item in contexts:
        tool_name, request = filled[item.agent_id]
        engine.submit_action(DirectToolCall(item, f"action-{round_id}-{item.agent_id}",
                                            tool_name, request))
    return engine.commit_decision_round(round_id)


def _advance_until(engine, want: set[int], limit: int = 96) -> None:
    """推进直到 want 中任一人物获得决策机会（静默边界不提供决策）。"""
    for _ in range(limit):
        state = engine.scheduling_state()
        if set(state.ready_agent_ids) & want:
            return
        assert state.next_event_at is not None
        engine.advance(int(state.next_event_at.timestamp()) - engine.state.current_time,
                       state.next_event_at)
    raise AssertionError(f"agents {want} never became ready")


def _result_for(results, agent_id: int):
    return next(item for item in results if item.agent_id == agent_id)


def _rejected_event_seen(engine, error_code: str) -> bool:
    events = engine.store.read_events(engine.store.last_event_seq)
    return any(event.event_type == "action_rejected"
               and event.data.get("error_code") == error_code for event in events)


# ---------------------------------------------------------------------------
# E1：chat 对象睡着/游戏中 → CHAT_PARTNER_NOT_AVAILABLE 业务拒绝，run 存活
# ---------------------------------------------------------------------------


def test_chat_with_sleeping_partner_is_business_rejection_and_run_survives(engine_factory) -> None:
    """对象入睡后发起交谈：action_rejected（非裸 ValueError 杀 run），后续轮次可继续。"""
    engine = engine_factory("multi-person.json")
    _round(engine, "sleep-round", {
        1: ("start_basic_activity", StartBasicActivityRequest(
            activity_type="wait", planned_duration_seconds=60)),
        2: ("start_basic_activity", StartBasicActivityRequest(
            activity_type="sleep", planned_duration_seconds=3600)),
    })
    _advance_until(engine, {1})
    chat = StartSocialActivityRequest(kind="chat", partner_id="agent:2",
                                      duration_minutes=15)
    results = _round(engine, "chat-round", {1: ("start_social_activity", chat)})
    rejected = _result_for(results, 1)
    assert rejected.status.value == "rejected"
    assert rejected.error_code == "CHAT_PARTNER_NOT_AVAILABLE"
    assert _rejected_event_seen(engine, "CHAT_PARTNER_NOT_AVAILABLE")
    assert all(activity.kind.value != "chat" for activity in engine.state.activities.values())
    _advance_until(engine, {1})
    follow_up = _round(engine, "after-chat", {1: ("start_basic_activity",
                      StartBasicActivityRequest(activity_type="wait",
                                                planned_duration_seconds=60))})
    assert _result_for(follow_up, 1).status.value == "in_progress"  # 受理成功=run 存活


# ---------------------------------------------------------------------------
# E3：send_message 超长文本 / 未知收件人 → 业务拒绝，run 存活
# ---------------------------------------------------------------------------


def test_send_message_over_limit_text_is_business_rejection(engine_factory) -> None:
    """文本 301 字符：MESSAGE_TEXT_TOO_LONG 业务拒绝（原为裸 ValueError 杀 run）。"""
    engine = engine_factory("multi-person.json")
    _advance_until(engine, {1})
    message = SendMessageRequest(recipient_id="agent:2", text="饿" * 301)
    results = _round(engine, "msg-long", {1: ("send_message", message)})
    rejected = _result_for(results, 1)
    assert rejected.status.value == "rejected"
    assert rejected.error_code == "MESSAGE_TEXT_TOO_LONG"
    assert _rejected_event_seen(engine, "MESSAGE_TEXT_TOO_LONG")
    assert engine.state.agents[2].inbox == {}
    _advance_until(engine, {1})
    follow_up = _round(engine, "after-msg-long", {1: ("start_basic_activity",
                      StartBasicActivityRequest(activity_type="wait",
                                                planned_duration_seconds=60))})
    assert _result_for(follow_up, 1).status.value == "in_progress"  # 受理成功=run 存活


def test_send_message_unknown_recipient_is_business_rejection(engine_factory) -> None:
    """未知收件人：RECIPIENT_NOT_FOUND 业务拒绝（原死代码，裸 ValueError 抢先杀 run）。"""
    engine = engine_factory("multi-person.json")
    _advance_until(engine, {1})
    message = SendMessageRequest(recipient_id="agent:9", text="在吗")
    results = _round(engine, "msg-ghost", {1: ("send_message", message)})
    rejected = _result_for(results, 1)
    assert rejected.status.value == "rejected"
    assert rejected.error_code == "RECIPIENT_NOT_FOUND"
    assert _rejected_event_seen(engine, "RECIPIENT_NOT_FOUND")


# ---------------------------------------------------------------------------
# E2：plan_day 超长 goal → ACTION_REQUEST_INVALID 拒绝且不持久化（不再永久杀 run）
# ---------------------------------------------------------------------------


def test_plan_goal_capped_at_300_on_request_and_persistence_models() -> None:
    """人物请求模型与持久层同限：三百字符受理，再多一字拒绝。"""
    boundary = "目" * 300
    assert PlanGoalInput(intent_kind="工作", goal=boundary).goal == boundary
    assert PlanGoal(intent_kind="工作", goal=boundary, registered_at=100).goal == boundary
    with pytest.raises(ValidationError, match="exceeds 300 characters"):
        PlanGoalInput(intent_kind="工作", goal=boundary + "余")
    with pytest.raises(ValidationError, match="at most 300 characters"):
        PlanGoal(intent_kind="工作", goal=boundary + "余", registered_at=100)


def test_plan_day_schema_still_passes_deepseek_strict_gate() -> None:
    """model_validator 上限不进 Schema：PlanDayRequest 仍过 DeepSeek strict 门（maxLength 黑名单）。"""
    schema = deepseek_strict_schema(PlanDayRequest)
    assert schema["type"] == "object"


def _plan_goals(goal: str) -> tuple[PlanGoalInput, ...]:
    """构造八个不同意图的目标，以原生请求承载异常长度测试。"""
    from worldmock.life.schedule import INTENT_KINDS

    return tuple(PlanGoalInput.model_construct(intent_kind=kind, goal=goal)
                 for kind in INTENT_KINDS[:8])


def test_plan_day_oversized_goal_rejected_and_never_poisons_future_rounds(engine_factory) -> None:
    """超长 goal 提交（绕过构造期校验模拟异常载荷）被引擎再验证拒绝，不落 day_plan，后续轮次照常。"""
    from worldmock.life.narration import compose_user_message, render_facts
    from worldmock.life.projection import _plan_fact

    engine = engine_factory("multi-person.json")
    _advance_until(engine, {1})
    # model_construct 绕过请求模型构造期校验（对齐 MeetingMinutes 直调先例），
    # 模拟"已通过 agent 门但载荷异常"的最坏路径：引擎 _dispatch 再验证必须兜住。
    oversized = PlanDayRequest.model_construct(goals=_plan_goals( "备注" * 301))
    results = _round(engine, "plan-oversized", {1: ("plan_day", oversized)})
    rejected = _result_for(results, 1)
    assert rejected.status.value == "rejected"
    assert rejected.error_code == "ACTION_REQUEST_INVALID"
    assert engine.state.agents[1].day_plan is None  # 未持久化：不会在下一轮提示构建击穿预算
    assert _rejected_event_seen(engine, "ACTION_REQUEST_INVALID")

    _advance_until(engine, {1})
    healthy = PlanDayRequest(goals=_plan_goals( "备注" * 150))
    follow_up = _round(engine, "plan-healthy", {1: ("plan_day", healthy)})
    assert _result_for(follow_up, 1).status.value == "completed"
    assert engine.state.agents[1].day_plan is not None

    # 持久化后的计划经 facts 渲染在提示预算内（原杀点：42658 字符 > 40000）。
    agent = engine.state.agents[1]
    rendered = render_facts({"day_plan": _plan_fact(agent, engine.state.current_time),
                             "placeholder": "x"})
    assert len(rendered) < 40000
    compose_user_message("卡片", "场景", rendered)
