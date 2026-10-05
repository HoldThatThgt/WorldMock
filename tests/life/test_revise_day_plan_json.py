"""@brief 修订入口的重复意图与超限：真实 JSON、引擎边界与宿主协议纠错都逐条定位、同批报告（个人事务与工作安排规格 1.3）。

每个重复的意图同时定位首次出现与其后每次出现的条目（goals[i].intent_kind，written 为该意图，allowed_values 为全部意图）；
条数超过八条定位在 goals（written 为提交的 goals），与重复意图在同一次校验里一并报告。经引擎的用例走真实请求、结算与
SQLite 事件；宿主一侧用直接导入的 LifeAgent 跑真实 ReAct 循环，参数是模型返回的 JSON 字符串。只调请求模型的两例是
补充证据。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agentsociety2.agent.base import ReactLoopRequest
from agentsociety2.society import DirectToolCall
from custom.agents.life_agent import LifeAgent
from custom.agents.life_feedback import FEEDBACK_INSTRUCTION
from tests.integration.test_life_agent_protocol import (_context, _Environment, _GroundedCorrectionDispatcher,
                                                        _ModelResponse, _observations, _wire_agent)
from tests.life.conftest import StubPlanDayClient, context, settle_day_plans
from worldmock.life import prompt_capacity
from worldmock.life.error_contract import ERROR_KEYS, ITEM_KEYS
from worldmock.life.models import PlanGoalInput, ReviseDayPlanRequest
from worldmock.life.schedule import INTENT_KINDS

#: 请求参数根在错误明细里的完整位置前缀。
_ROOT = ["tool_calls", 0, "function", "arguments"]
#: 条数超限的英文原因（定位在 goals）。
_CAPACITY = "day goals require at most eight entries"


def _reason(kind: str) -> str:
    """规格 1.3 逐字规定的重复意图英文原因。"""
    return f"intent_kind '{kind}' may occur only once; keep one goal for this intent and merge these entries into it"


def _goal(kind: str, text: str) -> dict:
    """一条不带提醒与大致时刻的目标（模型提交的 JSON 形状）。"""
    return {"intent_kind": kind, "goal": text, "remind_time": None, "around_time": None}


def _goals(*kinds: str) -> list[dict]:
    """按给定意图顺序排出的目标清单，目标文字各不相同。"""
    return [_goal(kind, f"第{index + 1}条：{kind}") for index, kind in enumerate(kinds)]


def _forged(goals: list[dict]) -> ReviseDayPlanRequest:
    """绕过请求模型自身校验构造的修订请求：条目各自合法，清单级约束留给引擎边界判定。"""
    return ReviseDayPlanRequest.model_construct(goals=[PlanGoalInput(**goal) for goal in goals])


def _planned_engine(engine_factory):
    """已登记当日计划（一条工作目标）的真实引擎，人物此刻可以决策。"""
    engine = engine_factory()
    assert settle_day_plans(engine, StubPlanDayClient(goals=[_goal("工作", "推进报告")]))[0]["goals"] == 1
    return engine


def _revise(engine, round_id: str, request: ReviseDayPlanRequest):
    """单人物真实轮：冻结观察、登记修订请求并结算，返回动作结果。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "revise_day_plan", request))
    return engine.commit_decision_round(round_id)[0]


def _stored_result(engine, round_id: str) -> dict:
    """SQLite 里这次修订的 action_result 事件正文。"""
    [event] = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
               if event.event_type == "action_result" and event.action_id == f"action-{round_id}"]
    return event.data


def _located(items: list[dict]) -> list[tuple]:
    """错误明细逐条的（去掉参数根的位置, 原值, 原因）。"""
    assert all(item["path"][:4] == _ROOT for item in items)
    return [(tuple(item["path"][4:]), item["written"], item["why"]) for item in items]


@pytest.mark.parametrize(("kinds", "repeated"), [
    (("休息", "休息"), [(0, "休息"), (1, "休息")]),
    (("休息", "休息", "休息"), [(0, "休息"), (1, "休息"), (2, "休息")]),
    (("休息", "工作", "休息", "用餐", "工作"), [(0, "休息"), (1, "工作"), (2, "休息"), (4, "工作")]),
], ids=["twice", "three-times", "two-intents-interleaved"])
def test_duplicate_intent_in_provider_json_returns_nested_validation_error(kinds, repeated):
    """JSON 反序列化后意图是字符串：重复条目不使校验崩溃，首次与其后每次出现都按下标升序定位，原因是规格原句。"""
    arguments = json.dumps({"goals": _goals(*kinds)}, ensure_ascii=False)
    with pytest.raises(ValidationError) as caught:
        ReviseDayPlanRequest.model_validate_json(arguments)
    errors = caught.value.errors()
    assert [(error["loc"], error["input"]) for error in errors] == [
        (("goals", index, "intent_kind"), kind) for index, kind in repeated]
    assert [str(error["ctx"]["error"]) for error in errors] == [_reason(kind) for _, kind in repeated]
    assert all("tasks" not in error["msg"] for error in errors)


def test_nine_goals_with_a_duplicate_report_capacity_and_positions_in_one_validation():
    """九条且含重复：goals 一条超限错误（原值为提交的各条目）加重复意图的每个位置，同一次校验返回。"""
    goals = _goals(*INTENT_KINDS[:8], INTENT_KINDS[0])
    with pytest.raises(ValidationError) as caught:
        ReviseDayPlanRequest.model_validate_json(json.dumps({"goals": goals}, ensure_ascii=False))
    errors = caught.value.errors()
    assert [error["loc"] for error in errors] == [("goals",), ("goals", 0, "intent_kind"), ("goals", 8, "intent_kind")]
    assert errors[0]["input"] == goals and str(errors[0]["ctx"]["error"]) == _CAPACITY
    assert [error["input"] for error in errors[1:]] == [INTENT_KINDS[0]] * 2


def test_engine_rejects_a_duplicate_revision_locating_first_and_later_entries(engine_factory):
    """修订入口经真实引擎：重复意图以 ACTION_REQUEST_INVALID 拒绝，items 给出首次与其后各次出现的完整 path、written
    （该意图）与 allowed_values（全部意图）；计划不变，SQLite 里的结果事件带同一份明细；合并后的修订受理。"""
    engine = _planned_engine(engine_factory)
    plan = engine.context.state.agents[1].day_plan
    rejected = _revise(engine, "duplicate", _forged(_goals("休息", "工作", "休息", "休息")))
    assert (rejected.status.value, rejected.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    items = rejected.data["items"]
    assert _located(items) == [(("goals", index, "intent_kind"), "休息", _reason("休息")) for index in (0, 2, 3)]
    for item in items:
        assert set(item) == set(ITEM_KEYS) and item["error_code"] == "ACTION_REQUEST_INVALID"
        assert item["allowed_values"] == list(INTENT_KINDS) and item["expected"] and item["candidates"] == []
    assert rejected.message == "; ".join([_reason("休息")] * 3) == rejected.data["reason"]
    assert engine.context.state.agents[1].day_plan == plan
    stored = _stored_result(engine, "duplicate")
    assert stored["error_code"] == "ACTION_REQUEST_INVALID" and stored["data"]["items"] == items
    merged = ReviseDayPlanRequest(goals=[PlanGoalInput(**goal) for goal in _goals("休息", "工作")])
    assert _revise(engine, "merged", merged).status.value == "completed"
    assert [goal.intent_kind for goal in engine.context.state.agents[1].day_plan.goals] == ["休息", "工作"]


def test_engine_reports_over_capacity_and_duplicates_in_the_same_rejection(engine_factory):
    """超限加重复经真实引擎同批返回：goals 条目的 written 是提交的 goals，其后是重复意图的每个位置；不因超限提前返回。"""
    engine = _planned_engine(engine_factory)
    plan = engine.context.state.agents[1].day_plan
    goals = _goals(*INTENT_KINDS[:8], INTENT_KINDS[3])
    rejected = _revise(engine, "over-capacity", _forged(goals))
    assert (rejected.status.value, rejected.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    kind = INTENT_KINDS[3]
    assert _located(rejected.data["items"]) == [
        (("goals",), goals, _CAPACITY),
        (("goals", 3, "intent_kind"), kind, _reason(kind)),
        (("goals", 8, "intent_kind"), kind, _reason(kind)),
    ]
    assert rejected.data["items"][0]["allowed_values"] == []
    assert all(item["allowed_values"] == list(INTENT_KINDS) for item in rejected.data["items"][1:])
    assert rejected.message == "; ".join([_CAPACITY, _reason(kind), _reason(kind)])
    assert engine.context.state.agents[1].day_plan == plan
    assert _stored_result(engine, "over-capacity")["data"]["items"] == rejected.data["items"]


def test_engine_keeps_at_most_eight_goals_with_one_goal_per_intent(engine_factory):
    """合同不变：九条各不相同的意图只报 goals 超限一条；八条各不相同的意图受理并原序登记。"""
    engine = _planned_engine(engine_factory)
    nine = _goals(*INTENT_KINDS[:9])
    rejected = _revise(engine, "nine", _forged(nine))
    assert rejected.error_code == "ACTION_REQUEST_INVALID"
    assert _located(rejected.data["items"]) == [(("goals",), nine, _CAPACITY)]
    eight = ReviseDayPlanRequest(goals=[PlanGoalInput(**goal) for goal in _goals(*INTENT_KINDS[:8])])
    assert _revise(engine, "eight", eight).status.value == "completed"
    assert [goal.intent_kind for goal in engine.context.state.agents[1].day_plan.goals] == list(INTENT_KINDS[:8])


async def _host_round(monkeypatch, responses: list[_ModelResponse]) -> tuple[_Environment, list[dict]]:
    """直接导入的 LifeAgent 在一次冻结观察（已有当日计划）上跑真实 ReAct 循环，返回环境与发出的模型请求。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)
    observed = _observations()
    observed[0]["data"]["todo_layer"]["day_plan"] = {"date": "2026-01-05", "goals": []}
    dispatcher = _GroundedCorrectionDispatcher(responses=responses)
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, len(responses) + 1, memory=False)
    result = await agent.run_react_loop(ReactLoopRequest(0, _context().observed_at, observed))
    assert result == "revise_day_plan registered"
    return environment, dispatcher.requests


async def test_host_corrects_duplicate_intents_from_real_json_arguments(monkeypatch):
    """宿主一侧：模型以 JSON 字符串提交重复意图与超限加重复，宿主不崩溃、不登记，两次都回 ARGUMENTS_INVALID 协议纠错
    （逐条完整 path、written 与 allowed_values），合并后的清单才登记到环境。"""
    duplicate = _goals("休息", "工作", "休息")
    over = _goals(*INTENT_KINDS[:8], INTENT_KINDS[0])
    merged = _goals("休息", "工作")
    environment, requests = await _host_round(monkeypatch, [
        _ModelResponse(f"call-{index}", "revise_day_plan", json.dumps({"goals": goals}, ensure_ascii=False))
        for index, goals in enumerate((duplicate, over, merged))])
    first, second = (json.loads(request["messages"][-1]["content"]) for request in requests[1:])
    for feedback in (first, second):
        assert list(feedback) == [*ERROR_KEYS, "instruction"] and feedback["instruction"] == FEEDBACK_INSTRUCTION
        assert feedback["error_code"] == "ARGUMENTS_INVALID" and feedback["unmet"] == []
        assert all(item["error_code"] == "ARGUMENTS_INVALID" for item in feedback["items"])
    assert _located(first["items"]) == [(("goals", index, "intent_kind"), "休息", _reason("休息")) for index in (0, 2)]
    assert all(item["allowed_values"] == list(INTENT_KINDS) for item in first["items"])
    assert first["reason"] == "; ".join([_reason("休息")] * 2)
    kind = INTENT_KINDS[0]
    assert _located(second["items"]) == [(("goals",), over, _CAPACITY),
                                         (("goals", 0, "intent_kind"), kind, _reason(kind)),
                                         (("goals", 8, "intent_kind"), kind, _reason(kind))]
    errors = [item.error for item in environment.evidence if item.phase == "response" and item.error is not None]
    assert [error.split(":", 1)[0] for error in errors] == ["ARGUMENTS_INVALID"] * 2
    [call] = environment.calls
    assert call.tool_name == "revise_day_plan"
    assert [(goal.intent_kind, goal.goal) for goal in call.request.goals] == [
        (goal["intent_kind"], goal["goal"]) for goal in merged]


#: 宿主对「与保留的已推进目标合并后超过八条」的英文原因（定位在 goals）。
_MERGED_CAPACITY = "At most eight goals are allowed including preserved progressed goals."


def _progressed(engine, *kinds: str) -> tuple:
    """已推进的当日目标（每次修订都原样保留在计划前缀），每个意图一条。"""
    from worldmock.life.schedule import PlanGoal

    now = engine.context.state.current_time
    return tuple(PlanGoal(intent_kind=kind, goal=f"{kind}做过了", registered_at=now - 60, progressed_at=now,
                          progress_activity_id=f"activity-{kind}") for kind in kinds)


async def _real_host_round(monkeypatch, engine, round_id: str, revisions: list[list[dict]]) -> tuple:
    """真实引擎、SQLite 与 LifeEnv 上的一次决策：直接导入的 LifeAgent 对冻结观察跑真实 ReAct 循环，脚本模型依次以 JSON
    字符串提交给定的修订，登记后结算。返回（实际发出的模型请求, 这次修订的结算结果）。脚本用尽后宿主还要模型再答一次
    （最后一份修订也被纠错）即失败，失败信息带宿主附回的那份纠错。"""
    from custom.envs.life_env import LifeEnv
    from tests.integration.test_same_second_continuation import _Script, _person, _reply

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)
    environment = LifeEnv()
    environment.engine = engine
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = await environment.observe(decision)
    model = _Script([_reply(f"call-{index}", "revise_day_plan", {"goals": goals}, "改一下今天的计划。")
                     for index, goals in enumerate(revisions)])
    person = _person(environment, decision, model)
    assert type(person).__module__ == "custom.agents.life_agent"
    try:
        result = await person.run_react_loop(ReactLoopRequest(0, decision.observed_at, [
            {"turn": 0, "action": "observe", "ok": True, "data": observed}]))
    except IndexError:
        pytest.fail("the host corrected the last scripted revision instead of registering it: "
                    + model.requests[-1]["messages"][-1]["content"])
    assert result == "revise_day_plan registered"
    [settled] = engine.commit_decision_round(round_id)
    return model.requests, settled


@pytest.mark.parametrize("kept", [0, 2], ids=["no-preserved-goal", "two-preserved-goals"])
async def test_host_and_engine_accept_a_revision_that_fills_the_plan_to_exactly_eight(
        engine_factory, monkeypatch, kept: int):
    """至多八条的合同不变（规格 1.3 末条）：恰好八条不算超限。真实引擎上预置 kept 条已推进目标，模型提交其余意图的
    8 - kept 条；宿主不纠错（只发出一次模型请求），环境受理并结算，计划是保留的已推进目标在前、新目标按提交顺序在后，
    合计恰好八条。"""
    from tests.life.conftest import stage_day_plan

    engine = engine_factory()
    stage_day_plan(engine, goals=_progressed(engine, *INTENT_KINDS[:kept]))
    submitted = _goals(*INTENT_KINDS[kept:8])
    requests, settled = await _real_host_round(monkeypatch, engine, f"eight-{kept}", [submitted])
    assert len(requests) == 1
    assert (settled.status.value, settled.error_code) == ("completed", None)
    plan = engine.context.state.agents[1].day_plan.goals
    assert [goal.intent_kind for goal in plan] == list(INTENT_KINDS[:8])
    assert [goal.progressed_at is not None for goal in plan] == [True] * kept + [False] * (8 - kept)
    assert [goal.goal for goal in plan[kept:]] == [goal["goal"] for goal in submitted]


async def test_host_corrects_a_revision_that_overflows_with_the_preserved_goals(engine_factory, monkeypatch):
    """与保留的已推进目标合并后超过八条（规格 1.3 末条；接口合同宿主错误码表 ARGUMENTS_INVALID「修订今日计划超过八条」）：
    真实引擎上已有两条已推进目标，七条新目标本身合乎请求模型。环境对这份请求以 PLAN_GOALS_INVALID 业务拒绝、计划不变；
    宿主在同样的计划上先行纠错——不登记，附回 ARGUMENTS_INVALID，一条明细定位在 goals，原因写明连同保留的已推进目标至多
    八条；两侧明细的 path 相同，written 都是提交的 goals（接口合同「当日目标请求」：超限时为提交的 goals），不是条数；
    模型改交六条后才登记（这次决策只有一条 action_requested），结算受理，计划恰好八条。"""
    from tests.life.conftest import stage_day_plan

    engine = engine_factory()
    stage_day_plan(engine, goals=_progressed(engine, *INTENT_KINDS[:2]))
    plan = engine.context.state.agents[1].day_plan
    overflow = _goals(*INTENT_KINDS[2:9])
    refused = _revise(engine, "engine-nine", ReviseDayPlanRequest(goals=[PlanGoalInput(**goal) for goal in overflow]))
    assert (refused.status.value, refused.error_code) == ("rejected", "PLAN_GOALS_INVALID")
    [refused_item] = refused.data["items"]
    assert (refused_item["path"], refused_item["written"]) == ([*_ROOT, "goals"], overflow)
    assert engine.context.state.agents[1].day_plan == plan

    requests, settled = await _real_host_round(monkeypatch, engine, "host-nine", [overflow, overflow[:6]])

    assert len(requests) == 2
    feedback = json.loads(requests[1]["messages"][-1]["content"])
    assert list(feedback) == [*ERROR_KEYS, "instruction"] and feedback["instruction"] == FEEDBACK_INSTRUCTION
    assert (feedback["error_code"], feedback["reason"]) == ("ARGUMENTS_INVALID", _MERGED_CAPACITY)
    assert feedback["unmet"] == []
    [item] = feedback["items"]
    assert set(item) == set(ITEM_KEYS) and item["allowed_values"] == []
    assert (item["path"], item["error_code"], item["why"]) == ([*_ROOT, "goals"], "ARGUMENTS_INVALID", _MERGED_CAPACITY)
    assert (item["path"], item["written"]) == (refused_item["path"], refused_item["written"]) == (
        [*_ROOT, "goals"], overflow)
    registered = [event.data["request"] for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                  if event.event_type == "action_requested" and event.decision_id == "decision-host-nine-1"]
    assert [request["goals"] for request in registered] == [overflow[:6]]
    assert (settled.status.value, settled.error_code) == ("completed", None)
    merged = engine.context.state.agents[1].day_plan.goals
    assert [goal.intent_kind for goal in merged] == list(INTENT_KINDS[:8])
    assert [goal.progressed_at is not None for goal in merged] == [True] * 2 + [False] * 6
