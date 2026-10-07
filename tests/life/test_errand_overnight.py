"""个人事务跨夜可见，且不进记忆层、不被浓缩经真实请求、引擎与 SQLite（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-范围与状态 、2.6、2.8）。

正式场景建立的 LifeEngine 上，人物以收尾请求的 errands 记下事务。事务存在 AgentState.errands，不在 DayPlan 里：零点使
旧计划失效、零点计划登记新计划、revise_day_plan 整表替换与目标推进都不改动它，零点前后待办层的事务行逐字相同。待办层
不浓缩：记忆层超过 8000 字符窗口被截取时、整次请求达到上下文阈值而进度层被折叠时，待办层的事务行照旧。个人事务不进
记忆层：记下、更新、取消、累计、办成与提醒都不给记忆层添一句，记忆层与同一段生活不记事务时逐字节相同。
"""

from __future__ import annotations

import json

from agentsociety2.agent.base import ReactLoopRequest

from custom.agents.life_agent import LifeAgent
from tests.life.conftest import (StubPlanDayClient, activity_name, advance_next, conclude_request, context, errand_add,
                                 errand_cancel, errand_update, report_request, settle_day_plans, stage_day_plan,
                                 start_activity, wake_by_mail)
from tests.life.routing_fixtures import (
    DRINK_NAME,
    MEAL_ID,
    MEAL_NAME,
    SNACK_ID,
    SNACK_NAME,
    WAIT_ITEM,
    act,
    errand_events,
    errand_note,
    observe,
    routed,
    run_until,
    single_person_home,
)
from tests.life.test_errand_reminder import evening, moment
from tests.life.test_memory_layer import _act, _arrive, _chars, _memo, _replied, _walk
from worldmock.life import memory_layer, prompt_capacity
from worldmock.life.errand_state import ERRAND_EVENT_KINDS
from worldmock.life.memory_layer import MEMORY_WINDOW_CHARS, memory_view
from worldmock.life.models import ActivityStatus, PlanGoalInput, ReviseDayPlanRequest

#: 零点计划桩写下的唯一目标（休息，没有提醒与大致时刻；等待推进它）。
_REST = {"intent_kind": "休息", "goal": "接着歇一会儿", "remind_time": None, "around_time": None}


def _just_before(at: int):
    """下一边界恰是 at 且此刻没人要决策（再推进一次就到 at）。"""
    def done(current) -> bool:
        """此刻没有就绪人物，下一边界是 at。"""
        state = current.scheduling_state()
        return not state.ready_agent_ids and int(state.next_event_at.timestamp()) == at
    return done


def test_an_errand_stays_in_the_todo_layer_after_midnight_plan_generation_and_plan_revision(engine_factory) -> None:
    """1 月 5 日 23:41 收尾时记下两条事务（一条带备注与提醒钟点，换算到次日 18:00），随后接着等待。零点边界使旧计划失效
    （当日计划置空）时事务原样；零点计划登记一条休息目标后事务原样，待办层的事务行与零点前逐字相同（记下的日期仍是 1 月
    5 日，提醒钟点仍是 18:00）；等待推进了休息目标后事务原样；revise_day_plan 替换未推进的目标（加一条早饭目标，已推进的
    休息目标由环境保留）后事务仍原样，待办层的事务行照旧。整个过程只有两条记下事件，零点、计划登记、目标推进与修订都不写
    事务事件。"""
    engine = evening(engine_factory, "23:40")
    errand_note(engine, "note", [errand_add("周末补食材包", MEAL_NAME, memo="下班顺路去超市", remind_time="18:00"),
                          errand_add("早饭喝两份奶", SNACK_NAME, quantity=2)])
    held = dict(engine.context.state.agents[1].errands)
    rows = observe(engine, "noted")["todo_layer"]["errands"]
    assert [(row["name"], row["noted_on"], row["remind_time"]) for row in rows] == [
        ("周末补食材包", "2026-01-05", "18:00"), ("早饭喝两份奶", "2026-01-05", None)]
    assert act(engine, "wait", routed(WAIT_ITEM)).status == "in_progress"
    midnight = moment(engine, "2026-01-06 00:00")
    run_until(engine, _just_before(midnight))
    engine.advance(midnight - engine.context.state.current_time, engine.scheduling_state().next_event_at)
    assert engine.midnight_rhythm_pending and engine.context.state.agents[1].day_plan is None
    assert engine.context.state.agents[1].errands == held
    settle_day_plans(engine, StubPlanDayClient(goals=[_REST]))
    assert [goal.goal for goal in engine.context.state.agents[1].day_plan.goals] == [_REST["goal"]]
    assert engine.context.state.agents[1].errands == held
    assert observe(engine, "planned")["todo_layer"]["errands"] == rows
    run_until(engine, lambda current: current.context.state.agents[1].day_plan.goals[0].progressed_at is not None)
    assert engine.context.state.agents[1].errands == held
    breakfast = PlanGoalInput(intent_kind="用餐", goal="早饭喝杯牛奶", remind_time=None, around_time="07:30")
    revised = act(engine, "revise", ReviseDayPlanRequest(goals=[breakfast]), tool="revise_day_plan")
    assert revised.status == "completed", revised.error_code
    assert [goal.goal for goal in engine.context.state.agents[1].day_plan.goals] == [_REST["goal"], "早饭喝杯牛奶"]
    assert engine.context.state.agents[1].errands == held
    assert observe(engine, "revised")["todo_layer"]["errands"] == rows
    assert [event.data["kind"] for event in errand_events(engine)] == ["errand_registered", "errand_registered"]


def _long_stay(engine, label: str, filler: str, changes: tuple = ()) -> None:
    """开始等待、被邮件唤醒后收尾：收尾写八条各 551 字的长记忆，可同时记下事务变更。"""
    _, waited = _act(engine, f"{label}-wait", "start_activity", start_activity("等待", "自己"))
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    memories = [_memo("routine", f"{index}" + filler * 550) for index in range(8)]
    _, done = _act(engine, f"{label}-conclude", "conclude_activity", conclude_request(
        activity_name(engine, waited.activity_id), summary="歇好了", memories=[memories], errands=changes))
    assert done.status == "completed", done.error_code


def test_an_errand_stays_when_the_memory_layer_overflows_its_window(engine_factory, monkeypatch) -> None:
    """在家收尾时写下八条长记忆并记下一条事务，出门到公司再写八条后回家：在路上这次观察的记忆层不截取时超过 8000 字符，
    实际被截取到窗口之内（仍占窗口的大半）；同一观察的待办层事务行与刚记下时逐字相同，事务的名称与备注不在记忆层里——
    待办层不占记忆层的字符窗口，也不随记忆层截取。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _long_stay(engine, "home", "家", (errand_add("周末补食材包", MEAL_NAME, memo="下班顺路去超市", remind_time="18:00"),))
    rows = observe(engine, "noted")["todo_layer"]["errands"]
    assert [row["name"] for row in rows] == ["周末补食材包"]
    _arrive(engine, "arrive", _walk(engine, "walk").activity_id)
    _long_stay(engine, "office", "班")
    _walk(engine, "back", "home")
    layer_context = engine.observations._layer_context(engine.context.state.agents[1], [])
    monkeypatch.setattr(memory_layer, "MEMORY_WINDOW_CHARS", 10 ** 6)
    untrimmed = memory_view(layer_context, engine.context.store).layer
    monkeypatch.setattr(memory_layer, "MEMORY_WINDOW_CHARS", MEMORY_WINDOW_CHARS)

    observed = observe(engine, "road")

    trimmed = observed["memory_layer"]
    assert _chars(untrimmed) > MEMORY_WINDOW_CHARS >= _chars(trimmed) > MEMORY_WINDOW_CHARS // 2
    assert trimmed != untrimmed and trimmed["earlier_today"][-1] == untrimmed["earlier_today"][-1]
    assert observed["todo_layer"]["errands"] == rows
    text = json.dumps(trimmed, ensure_ascii=False)
    assert "周末补食材包" not in text and "下班顺路去超市" not in text


def test_a_condensed_request_still_carries_the_todo_layer_errands(engine_factory, monkeypatch) -> None:
    """记下一条事务后接着等待（这一决策像真实宿主那样记下了行动模型请求，此前的进度记录因而可以浓缩）：由下一次冻结观察
    准备的真实 LifeAgent 发出行动请求时整次请求达到上下文阈值，进度层被折叠成 condensed 记录才回到阈值以下；发出的请求
    里待办层与观察中的待办层逐字相同（事务行照旧），除进度层外的各层都不改。"""
    engine = single_person_home(engine_factory)
    errand_note(engine, "note", [errand_add("周末补食材包", MEAL_NAME, memo="下班顺路去超市", remind_time="18:00")])
    assert _replied(engine, "wait", "start_activity", routed(WAIT_ITEM), "接着等一会儿。").status == "in_progress"
    decision = context(engine, "condensed")
    observed = engine.observe(decision)
    host = LifeAgent()
    host._model_name = prompt_capacity.MODEL_ALIAS
    request = ReactLoopRequest(0, decision.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                          "data": observed}])
    host.prepare_react_loop(request)
    messages = host.build_react_messages(request)
    tools = host.build_react_tool_schemas(readonly=False)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1000)
    monkeypatch.setattr(prompt_capacity, "request_token_count", lambda candidate: 800 if _folded(candidate) else 900)

    sent = host._model_request(messages, tools)

    layers, original = json.loads(sent["messages"][1]["content"]), json.loads(messages[1]["content"])
    assert _folded(sent) and not _folded({"messages": messages})
    assert _named(layers, "待办层") == _named(original, "待办层") == observed["todo_layer"]
    assert [row["name"] for row in _named(layers, "待办层")["errands"]] == ["周末补食材包"]
    assert [layer for layer in layers if layer["layer"] != "进度层"] == [
        layer for layer in original if layer["layer"] != "进度层"]


def _named(layers: list[dict], name: str) -> dict:
    """user 消息层数组里名为 name 的层。"""
    return next(layer for layer in layers if layer["layer"] == name)


def _folded(request: dict) -> bool:
    """请求 user 消息里的进度层是否已有折叠出的 condensed 记录。"""
    layers = json.loads(request["messages"][1]["content"])
    return any(record["type"] == "condensed" for record in _named(layers, "进度层")["events"])


def _home_session(engine_factory, noted: list[dict], reported: list[dict]) -> tuple:
    """@brief 同一段真实购买、等待、收货和出行，仅控制请求附带的事务变更不同。"""
    from tests.life.test_acs_amended_runtime import perform, pump
    from tests.life.test_acs_runtime import request
    from worldmock.life.activity_control import is_pending_conclusion

    engine = single_person_home(engine_factory)
    def waits():
        return [item.name for item in engine.context.state.activities.values() if item.agent_id == 1
                and item.kind == "wait" and (item.status == "active" or is_pending_conclusion(item))]
    errand_note(engine, "note", noted)
    purchased = perform(engine, "meal", request(MEAL_NAME, "美团", "购买", payment="微信"))
    assert purchased.error_code is None, purchased.data
    [meal] = purchased.data["orders"]
    waited = perform(engine, "wait-receipt", request("自己", None, "等待"))
    assert waited.error_code is None, waited.data
    at = wake_by_mail(engine, 1, offset=540)
    assert at == moment(engine, "2026-01-05 12:40")
    pump(engine, lambda: engine.context.state.current_time == at and 1 in engine.scheduling_state().ready_agent_ids)
    name = activity_name(engine, waited.activity_id)
    result = perform(engine, "report", report_request(name, summary="继续等。", errands=reported), "report_status")
    assert result.error_code is None, result.data
    pump(engine, lambda: engine.context.state.deliveries[meal["order_id"]].status == "delivered")
    assert waits()
    assert perform(engine, "finish-meal-wait", conclude_request(*waits()), "conclude_activity").error_code is None
    tea = perform(engine, "tea", request(DRINK_NAME, "美团", "购买", payment="微信"))
    assert tea.error_code is None, tea.data
    [order] = tea.data["orders"]
    pump(engine, lambda: engine.context.state.deliveries[order["order_id"]].status == "delivered")
    assert waits()
    assert perform(engine, "finish-tea-wait", conclude_request(*waits()), "conclude_activity").error_code is None
    _walk(engine, "walk")
    return engine, observe(engine, "road")


def test_the_memory_layer_writes_no_line_for_errands(engine_factory) -> None:
    """@brief 两遍同样的真实购买与收货，一遍登记、提醒、更新、取消及推进事务，结束地点后的记忆仍逐字相同。"""
    noted = [errand_add("获得那碗牛肉面", MEAL_NAME, memo="午饭后趁热吃"),
             errand_add("周末补食材包", SNACK_NAME, memo="下班顺路去超市"),
             errand_add("囤一箱冰红茶", DRINK_NAME, quantity=3, remind_time="12:40")]
    reported = [errand_cancel("周末补食材包", "柜子里还剩一包"), errand_update("囤一箱冰红茶", memo="等打折再囤")]
    engine, road = _home_session(engine_factory, noted, reported)
    _, plain = _home_session(engine_factory, [], [])

    assert {event.data["kind"] for event in errand_events(engine)} == set(ERRAND_EVENT_KINDS)
    assert engine.context.state.agents[1].errands and len(engine.context.state.agents[1].errands) == 1
    layer = road["memory_layer"]
    assert layer["earlier_today"] and layer == plain["memory_layer"]
    text = json.dumps(layer, ensure_ascii=False)
    for written in ("获得那碗牛肉面", "午饭后趁热吃", "周末补食材包", "下班顺路去超市", "柜子里还剩一包", "囤一箱冰红茶",
                    "等打折再囤", "办成"):
        assert written not in text
