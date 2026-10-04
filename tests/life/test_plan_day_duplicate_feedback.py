"""@brief 零点计划入口的重复意图与超限：附回文本逐条列位置与原因，与修订入口共用同一校验（个人事务与工作安排规格 1.3）。

经 settle_day_plans 的两例走真实的零点计划后台任务、引擎登记与 SQLite 事件：首个 plan_day 响应事件的错误文本含全部
位置，第二次请求原样附回该文本，最终登记合并后的计划。只调 parse_plan_day 与 prepare_plan_day_correction 的两例是
补充证据。
"""

from __future__ import annotations

import json

import pytest

from tests.life.conftest import StubPlanDayClient, plan_day_response, settle_day_plans
from worldmock.life import day_planning
from worldmock.life.schedule import INTENT_KINDS

#: 条数超限的英文原因（定位在 goals）。
_CAPACITY = "day goals require at most eight entries"


def _reason(kind: str) -> str:
    """规格 1.3 逐字规定的重复意图英文原因。"""
    return f"intent_kind '{kind}' may occur only once; keep one goal for this intent and merge these entries into it"


def _feedback(*positions: tuple[int, str], capacity: bool = False) -> str:
    """附回文本的完整原文：超限时先是 goals 一条，其后逐条「位置: 原因」列出重复意图的每个条目，以分号相连。"""
    rows = [f"goals: Value error, {_CAPACITY}"] if capacity else []
    rows += [f"goals.{index}.intent_kind: Value error, {_reason(kind)}" for index, kind in positions]
    return "; ".join(rows)


def _work_goals() -> list[dict]:
    """同一天三个时段的工作目标：同属一个意图，模型应合并成一条。"""
    return [{"intent_kind": "工作", "goal": text, "remind_time": None, "around_time": clock}
            for text, clock in (("补齐昨日拜访跟进", "09:00"), ("整理试用方案", "10:30"), ("确认拜访进度", "15:00"))]


def _plan_day_events(engine, event_type: str) -> list:
    """SQLite 里本人的零点计划模型证据事件（按提交顺序）。"""
    return [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
            if event.event_type == event_type and event.agent_id == 1
            and event.data.get("purpose") == day_planning.PLAN_DAY_PURPOSE]


def test_repeated_work_goals_are_located_and_correction_requires_merging():
    """多时段工作仍属于同一意图，原始响应保留，反馈定位首次与其后每次出现的条目。"""
    response = plan_day_response(_work_goals())
    with pytest.raises(day_planning.PlanDayOutputError) as caught:
        day_planning.parse_plan_day(response, 1790611200, "Asia/Shanghai")
    feedback = str(caught.value)
    assert feedback == _feedback((0, "工作"), (1, "工作"), (2, "工作"))
    assert "tasks" not in feedback
    request = day_planning.prepare_plan_day({"facts": []}, "deepseek-flash")
    corrected = day_planning.prepare_plan_day_correction(request, response, caught.value)
    message = json.loads(corrected["messages"][-1]["content"])
    assert message["VALIDATION_ERROR"] == feedback
    assert message["PREVIOUS_INVALID_RESPONSE"] == response
    assert corrected["tools"] == request["tools"]
    assert corrected["tool_choice"] == "required"


def test_one_merged_work_goal_keeps_every_task_and_passes():
    """合并后的完整计划通过原校验，工作任务内容无需删除。"""
    text = "上午补齐昨日拜访跟进并整理试用方案，下午确认拜访进度。"
    goal = {"intent_kind": "工作", "goal": text, "remind_time": None, "around_time": "09:00"}
    parsed = day_planning.parse_plan_day(plan_day_response([goal]), 1790611200, "Asia/Shanghai")
    assert len(parsed.goals) == 1 and parsed.goals[0].goal == text


def test_plan_day_duplicate_intents_are_fed_back_with_every_position_then_registered(engine_factory):
    """零点计划经真实后台任务：重复意图的首个响应事件记下全部位置与原因，第二次请求在原始消息之后附回同一文本与前次
    响应，合并后的计划登记；每个重复意图既定位首次出现也定位其后各次。"""
    engine = engine_factory()
    goals = [*_work_goals(), {"intent_kind": "用餐", "goal": "午饭吃点热的", "remind_time": None, "around_time": "12:00"}]
    duplicate = plan_day_response(goals)
    merged = [{"intent_kind": "工作", "goal": "上午补齐跟进并整理方案，下午确认进度", "remind_time": None,
               "around_time": "09:00"}, goals[-1]]
    client = StubPlanDayClient(goals=merged, responses=[duplicate])
    assert settle_day_plans(engine, client)[0]["goals"] == 2
    responses = _plan_day_events(engine, "model_response")
    assert [event.data["attempt"] for event in responses] == [1, 2] and responses[1].data["error"] is None
    feedback = responses[0].data["error"]
    assert feedback == _feedback((0, "工作"), (1, "工作"), (2, "工作"))
    assert len(client.calls) == 2 and client.calls[1]["messages"][:-1] == client.calls[0]["messages"]
    requests = _plan_day_events(engine, "model_request")
    assert [event.data["request"] for event in requests] == client.calls
    attached = json.loads(requests[1].data["request"]["messages"][-1]["content"])
    assert attached["VALIDATION_ERROR"] == feedback and attached["PREVIOUS_INVALID_RESPONSE"] == duplicate
    assert [(goal.intent_kind, goal.goal) for goal in engine.context.state.agents[1].day_plan.goals] == [
        (goal["intent_kind"], goal["goal"]) for goal in merged]


def test_plan_day_reports_over_capacity_together_with_duplicates(engine_factory):
    """零点计划经真实后台任务：九条且含重复时，超限（定位在 goals）与重复意图的每个位置在同一条附回文本里，不因超限
    提前返回；九条各不相同时只报超限；八条各不相同的清单登记。"""
    engine = engine_factory()
    rows = [{"intent_kind": kind, "goal": f"今天的{kind}", "remind_time": None, "around_time": None}
            for kind in INTENT_KINDS[:9]]
    repeated = [*rows[:8], {**rows[0], "goal": "再来一条"}]
    client = StubPlanDayClient(goals=rows[:8], responses=[plan_day_response(repeated), plan_day_response(rows)])
    assert settle_day_plans(engine, client)[0]["goals"] == 8
    errors = [event.data["error"] for event in _plan_day_events(engine, "model_response")]
    kind = INTENT_KINDS[0]
    assert errors == [_feedback((0, kind), (8, kind), capacity=True), _feedback(capacity=True), None]
    attached = [json.loads(call["messages"][-1]["content"])["VALIDATION_ERROR"] for call in client.calls[1:]]
    assert attached == errors[:2]
    assert [goal.intent_kind for goal in engine.context.state.agents[1].day_plan.goals] == list(INTENT_KINDS[:8])
