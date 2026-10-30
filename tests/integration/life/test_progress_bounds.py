"""进度层的界与带入的记录（docs/design/06-agent.md「进度层的下界与带入的记录」）：占槽位的本人活动不分种类都有带入
记录，这段停留或路程里做完的查询带着答案留到出发；送达、到站与办成只写序号大于已发布界的，推送、回调提醒、余额减少与
事务提醒只写序号大于上一次决策观察的，计划提醒只写提醒时刻晚于上一次决策的；控制请求与被拒的请求不进进度层，两层按
来源事件不相交。经真实请求、引擎与 SQLite，读观察里的进度层与已提交事件。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from tests.integration.life.test_callback_runtime import callback_environment, rule
from tests.life.conftest import (
    advance_next,
    advance_until_ready,
    conclude_request,
    do_at,
    pause_request,
    progress_calls,
    purchase,
    query,
    report_request,
    sleep,
    stage_day_plan,
    tool_of,
    travel,
    use_app,
)
from tests.life.environment_runtime import (
    act,
    act_group,
    events,
    every_five_minutes,
    finish_activity,
    observe_only,
    until_ended,
)
from worldmock.agents.prompt.prompt_schemas import validate_layer
from worldmock.foundation.time.readable import iso_time, local_date, money_phrase
from worldmock.simulation_engine.activities.lifecycle.activity_control import open_activities, slot_activities
from worldmock.simulation_engine.observation.layers import memory_layer
from worldmock.simulation_engine.observation.layers.progress_layer import FACT_EVENT_KINDS, NOTICE_EVENT_KINDS
from worldmock.simulation_engine.observation.layers.result_projection import public_result
from worldmock.simulation_engine.observation.memory.days import day_bound, published_today
from worldmock.state.interaction.actions import START_TOOLS
from worldmock.state.interaction.requests import ReviseDayPlanRequest
from worldmock.state.planning.schedule import PlanGoal


def visible_fee_rules(trigger: dict) -> list[dict]:
    """同一触发上的 1 元支出与一条带可见提示的足迹链：可见提示只能配足迹链（admission._check_notify），同一触发的净
    资金减少由此写成一条人物可见的 balance_decreased。"""
    return [rule("fee", trigger, {"type": "transfer", "direction": "expense", "amount_fen": 100}),
            rule("fee-hint", trigger, {"type": "footprint_chain", "steps": [
                {"kind": "payment", "app_pool": ["alipay"], "pick": "random"}]}, notify={"visible": True})]


#: 开车出发时触发的可见支出回调。
_DRIVING = {"class": "action", "key": "travel", "travel_method_id": "driving"}


def _layer_context(engine):
    """人物此刻的层上下文（与观察构造同一份）。"""
    return engine.observations._layer_context(engine.context.state.agents[1], [])


def _published(engine) -> int:
    """人物此刻的已发布界 P_k。"""
    return memory_layer.memory_view(_layer_context(engine), engine.context.store).published


def _records(observed: dict, request) -> list[dict]:
    """进度层里这条开始请求的 tool_call 记录（工具与参数原文相同）。"""
    arguments = request.model_dump(mode="json")
    return [row for row in progress_calls(observed, tool_of(request)) if row["arguments"] == arguments]


def _results(engine, action_id: str) -> list:
    """这条动作的各条 action_result 事件。"""
    return [row for row in events(engine, "action_result") if row.action_id == action_id]


def _projected(engine, event) -> dict:
    """一条 action_result 投影给人物的公开结果（这些用例里没有食材包）。"""
    return public_result(event.data, engine.context.inputs, {})


#: 每种占槽位的活动的开始请求。
_STARTS = {
    "sleep": lambda: sleep(30),
    "travel": lambda: travel("公司", "驾车"),
    "purchase": lambda: purchase("柠檬茶", prefer="饿了么"),
    "location-interact": lambda: do_at("家", "测试休息"),
    "app-interact": lambda: use_app("Keep"),
}


@pytest.mark.parametrize("kind", list(_STARTS))
def test_every_slot_activity_is_carried_whatever_its_kind(engine_factory, kind):
    """开始之后的观察里恰有一条这项活动的 tool_call，结果是第一条；活动结束、待收尾时同一条记录（occurred_at、arguments
    不变）的结果是终态结果；收尾之后的观察里没有它；每次观察的进度层都通过层校验。"""
    engine = engine_factory()
    request = _STARTS[kind]()
    _, started = act(engine, kind, request)
    assert started.status == "in_progress", started.error_code
    running = observe_only(engine, "running")
    records = _records(running, request)
    assert len(records) == 1, running["progress_layer"]
    first = records[0]
    (accepted,) = _results(engine, started.action_id)
    assert first["result"] == _projected(engine, accepted) and first["result"]["status"] == "in_progress"
    until_ended(engine, started.activity_id)
    ended = observe_only(engine, "ended")
    records = _records(ended, request)
    assert len(records) == 1, ended["progress_layer"]
    record = records[0]
    terminal = _results(engine, started.action_id)[-1]
    assert terminal.event_id != accepted.event_id
    assert (record["occurred_at"], record["arguments"]) == (first["occurred_at"], first["arguments"])
    assert record["result"] == _projected(engine, terminal)
    name = engine.context.state.activities[started.activity_id].name
    _, concluded = act(engine, "conclude", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    after = observe_only(engine, "concluded")
    assert _records(after, request) == []
    for observed in (running, ended, after):
        validate_layer(observed["progress_layer"])


def test_a_query_is_carried_while_running_and_its_answer_until_departure(engine_factory):
    """查询进行中：一条 tool_call，结果是第一条；做完以后再做两次别的决策，同一条记录的结果仍是带 details.query 答案的
    终态结果；出发之后的观察里没有它。"""
    engine = engine_factory()
    request = query("柠檬茶")
    _, asked = act(engine, "tea", request)
    (running,) = _records(observe_only(engine, "asking"), request)
    assert running["result"]["status"] == "in_progress" and "query" not in running["result"]["details"]
    until_ended(engine, asked.activity_id)
    _, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    until_ended(engine, chat.activity_id)
    name = engine.context.state.activities[chat.activity_id].name
    _, concluded = act(engine, "chat-done", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    later = observe_only(engine, "answered")
    records = _records(later, request)
    assert len(records) == 1, later["progress_layer"]
    answered = records[0]
    assert answered["occurred_at"] == running["occurred_at"]
    assert answered["result"] == _projected(engine, _results(engine, asked.action_id)[-1])
    assert answered["result"]["details"]["query"]["rows"]
    _, trip = act(engine, "leave", travel("公司", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    assert _records(observe_only(engine, "departed"), request) == []


def _overnight(persona, config):
    """运行从 1 月 5 日 23:00 开始、持续 10 小时（运行窗口落在整点上）。"""
    config["start_time"] = config["start_time"] + timedelta(hours=15)
    config["stop_time"] = config["start_time"] + timedelta(hours=10)


def _bound_today(engine) -> int:
    """此刻所在本地日的零点界 B_D。"""
    now, zone = engine.context.state.current_time, engine.context.config.timezone
    return day_bound(engine.context.store, local_date(now, zone), zone, now)


def _carried_activities(engine, observed: dict) -> list:
    """这次观察该带入的本人活动：占槽位的（进行中或待收尾），加上这段停留或路程里做完的查询。"""
    state = engine.context.state
    stay = int(datetime.fromisoformat(observed["progress_layer"]["session_started_at"]).timestamp())
    finished = [item for item in state.activities.values() if item.agent_id == 1 and item.kind == "query"
                and item.status == "completed" and item.ended_at >= stay]
    return [*slot_activities(open_activities(state, 1)), *finished]


def _carried_sources(engine, observed: dict) -> set[str]:
    """这次观察里带入记录的来源事件身份：该带入的活动的开始请求与各条结果。"""
    origins = {item.origin_action_id for item in _carried_activities(engine, observed)}
    store = engine.context.store
    return {event_id for event_id in observed["progress_event_ids"]
            if store.read_event(event_id).event_type in {"action_requested", "action_result"}
            and store.read_event(event_id).action_id in origins}


def test_overnight_sleep_is_carried_and_facts_before_midnight_stay_out_of_the_morning_progress_layer(engine_factory):
    """23:00 经外卖 app 买牛肉面，付款并收尾后同一秒睡 8 小时；外卖在睡眠中、零点之前送达。醒来那一秒：带入的睡眠记录是
    completed 的终态结果；进度层没有那次送达，待办层 orders 为空；带入记录以外的来源序号都大于今天的零点界。"""
    engine = engine_factory(mutate=_overnight)
    _, bought = act(engine, "takeaway", purchase("牛肉面"))
    assert bought.status == "in_progress", bought.error_code
    (order,) = engine.context.state.deliveries.values()
    finish_activity(engine, bought, "takeaway")
    request = sleep(480)
    _, slept = act(engine, "sleep", request)
    assert slept.status == "in_progress", slept.error_code
    until_ended(engine, slept.activity_id)
    delivered = engine.context.state.deliveries[order.order_id]
    assert delivered.status == "delivered" and local_date(delivered.received_at, "Asia/Shanghai") == "2026-01-05"
    woke = observe_only(engine, "woke")
    (record,) = _records(woke, request)
    assert record["result"]["status"] == "completed"
    assert not [row for row in woke["progress_layer"]["events"] if row["type"] == "delivery"]
    assert woke["todo_layer"]["orders"] == []
    bound, carried = _bound_today(engine), _carried_sources(engine, woke)
    others = [engine.context.store.read_event(event_id) for event_id in woke["progress_event_ids"]
              if event_id not in carried]
    assert all(event.event_seq > bound for event in others)


def test_a_delivery_is_in_the_progress_layer_until_the_next_decision_publishes_it(engine_factory):
    """08:00 买牛肉面外卖，付款并收尾后同一秒小睡 30 分钟；外卖在睡眠中送达（睡着时不给决策机会）：送达之后、下一次决策
    之前的观察里有那条 delivery，来源序号大于已发布界；醒来给睡眠收尾之后的观察里没有它，已发布界不小于送达事件的序号。"""
    engine = engine_factory()
    _, bought = act(engine, "takeaway", purchase("牛肉面"))
    (order,) = engine.context.state.deliveries.values()
    finish_activity(engine, bought, "takeaway")
    _, nap = act(engine, "nap", sleep(30))
    assert nap.status == "in_progress", nap.error_code
    while engine.context.state.deliveries[order.order_id].status != "delivered":
        assert not engine.scheduling_state().ready_agent_ids
        advance_next(engine)
    (arrival,) = [row for row in events(engine, "environment_event") if row.data.get("kind") == "delivery_arrival"]
    seen = observe_only(engine, "delivered")
    assert [row["type"] for row in seen["progress_layer"]["events"]].count("delivery") == 1
    assert arrival.event_id in seen["progress_event_ids"] and arrival.event_seq > _published(engine)
    until_ended(engine, nap.activity_id)
    name = engine.context.state.activities[nap.activity_id].name
    _, concluded = act(engine, "woke", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    after = observe_only(engine, "after")
    assert not [row for row in after["progress_layer"]["events"] if row["type"] == "delivery"]
    assert _published(engine) >= arrival.event_seq


def test_a_balance_notice_from_a_decision_commit_reaches_the_next_decision_only(engine_factory, tmp_path):
    """开车出发时触发一条可见的支出回调：出发那次决策的提交里有 balance_decreased（序号介于那次观察与提交末序号之间）；
    其后的观察里有一条 balance_decreased，写这次提交前后余额之差（含车费）；路上再播报一次之后的观察里没有它。"""
    engine = engine_factory(callback_environment(tmp_path, visible_fee_rules(_DRIVING)), mutate=every_five_minutes)
    before = engine.context.state.accounts["agent:1"]
    _, trip = act(engine, "drive", travel("公司", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    spent = before - engine.context.state.accounts["agent:1"]
    store = engine.context.store
    (observation,) = [row for row in events(engine, "observation") if row.decision_id == "decision-drive-1"]
    commit = store.commit_last_seq(_results(engine, trip.action_id)[0].event_seq)
    (notice,) = [row for row in events(engine, "environment_event") if row.data.get("kind") == "balance_decreased"]
    assert observation.event_seq < notice.event_seq <= commit
    assert advance_until_ready(engine)
    road = observe_only(engine, "road")
    assert [row for row in road["progress_layer"]["events"] if row["type"] == "balance_decreased"] == [
        {"occurred_at": iso_time(notice.occurred_at, engine.context.config.timezone), "type": "balance_decreased",
         "message": f"余额减少了 {money_phrase(spent)}"}]
    name = engine.context.state.activities[trip.activity_id].name
    _, reported = act(engine, "report", report_request(name), "report_status")
    assert reported.status == "completed", reported.error_code
    later = observe_only(engine, "later")
    assert not [row for row in later["progress_layer"]["events"] if row["type"] == "balance_decreased"]


def test_plan_reminder_shows_from_its_moment_until_the_next_decision(engine_factory):
    """今日计划一条目标 08:10 提醒；人物 08:00 开始歇一刻钟，提醒到点时：观察里有那条 plan_reminder；同一秒做一次决策之后
    再观察（仍是同一秒）：没有它；时钟推进之后也没有。"""
    engine = engine_factory()
    start = engine.context.state.current_time
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="休息", goal="喝口水", registered_at=start,
                                           remind_at=start + 600),))
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    assert advance_until_ready(engine) and engine.context.state.current_time == start + 600
    reminded = observe_only(engine, "reminded")
    assert [row for row in reminded["progress_layer"]["events"] if row["type"] == "plan_reminder"] == [
        {"occurred_at": iso_time(start + 600, engine.context.config.timezone), "type": "plan_reminder",
         "goal": "喝口水"}]
    name = engine.context.state.activities[rest.activity_id].name
    _, reported = act(engine, "report", report_request(name), "report_status")
    assert reported.status == "completed", reported.error_code
    assert engine.context.state.current_time == start + 600
    for observed in (observe_only(engine, "same-second"), _after_advance(engine)):
        assert not [row for row in observed["progress_layer"]["events"] if row["type"] == "plan_reminder"]


def _after_advance(engine) -> dict:
    """推进一个边界之后的一次观察。"""
    advance_next(engine)
    return observe_only(engine, "advanced")


def _check_tool_calls(engine, observed: dict) -> None:
    """进度层的 tool_call 都是开始工具，恰对应此刻占槽位的本人活动；没有任何记录带 self_note。"""
    calls = progress_calls(observed)
    assert {row["tool"] for row in calls} <= set(START_TOOLS)
    store = engine.context.store
    expected = [store.read_event(item.origin_request_event_id).data["request"]
                for item in slot_activities(open_activities(engine.context.state, 1))]
    assert sorted(json.dumps(row["arguments"], sort_keys=True) for row in calls) == sorted(
        json.dumps(item, sort_keys=True) for item in expected)
    assert "self_note" not in json.dumps(observed["progress_layer"], ensure_ascii=False)


def test_control_and_rejected_requests_never_enter_the_progress_layer(engine_factory):
    """依次修订计划、一次被拒的开始、开始并播报、暂停、恢复、收尾：每次之后的观察里 tool_call 都是开始工具，只对应占槽位
    的活动；被拒那次的原因出现在 rejected_today；没有任何记录带 self_note。"""
    engine = engine_factory(mutate=every_five_minutes)
    stage_day_plan(engine)
    plan = ReviseDayPlanRequest.model_validate({"goals": [
        {"intent_kind": "休息", "goal": "下午喝杯咖啡", "remind_time": None, "around_time": None}]})
    _, revised = act(engine, "plan", plan, "revise_day_plan")
    assert revised.status == "completed", revised.error_code
    _check_tool_calls(engine, observe_only(engine, "planned"))
    _, refused = act(engine, "refused", do_at("家", "不存在的对象"))
    assert refused.status == "rejected"
    observed = observe_only(engine, "refused")
    _check_tool_calls(engine, observed)
    assert [(row["times"], row["why"]) for row in observed["progress_layer"]["rejected_today"]] == [
        (1, refused.data["reason"][:60])]
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    for label, request, tool in (("report", report_request(name), "report_status"),
                                 ("pause", pause_request(name), "pause_activity"),
                                 ("resume", report_request(name), "report_status"),
                                 ("conclude", conclude_request(name), "conclude_activity")):
        assert advance_until_ready(engine)
        _, result = act(engine, label, request, tool)
        assert result.status == "completed", (label, result.error_code)
        _check_tool_calls(engine, observe_only(engine, label))


def _check_bounds(engine, observed: dict) -> bool:
    """一次观察的进度层来源：记忆事实序号大于已发布界，非记忆提示序号大于上一次决策观察与零点界；序号不超过已发布界的
    来源只属于带入记录，或是上一次决策提交里产生的非记忆提示；与同一时刻已发布的今天条目的来源只在带入记录上重叠。
    返回这次观察里是否出现了「上一次决策提交里产生的非记忆提示」。"""
    store, published = engine.context.store, _published(engine)
    (last,) = events(engine, "action_requested")[-1:]
    previous = max(row.event_seq for row in store.read_decision_observations(1, last.decision_id, last.occurred_at))
    notice_bound, carried = max(previous, _bound_today(engine)), _carried_sources(engine, observed)
    late_notice = False
    for event_id in observed["progress_event_ids"]:
        event = store.read_event(event_id)
        if event_id in carried:
            continue
        kind = event.data.get("kind")
        assert kind in FACT_EVENT_KINDS | NOTICE_EVENT_KINDS, (event.event_type, kind)
        assert event.event_seq > (published if kind in FACT_EVENT_KINDS else notice_bound), kind
        late_notice |= event.event_seq <= published
    _check_tool_calls_or_queries(engine, observed)
    now = engine.context.state.current_time
    today = published_today(engine.context.inputs, store, 1, now, store.last_event_seq)
    entry_sources = {event_id for entry in today.entries for event_id in entry.event_ids}
    shared = entry_sources & set(observed["progress_event_ids"])
    assert shared <= carried and not shared - carried
    return late_notice


def _check_tool_calls_or_queries(engine, observed: dict) -> None:
    """每条 tool_call 对应一项占槽位的本人活动，或这段停留或路程里做完的一项查询（开始请求原文相同）。"""
    store = engine.context.store
    requests = [store.read_event(item.origin_request_event_id) for item in _carried_activities(engine, observed)]
    for row in progress_calls(observed):
        assert any(request.data["request"] == row["arguments"] and request.data["tool_name"] == row["tool"]
                   for request in requests), row


def test_progress_records_lie_after_their_bounds_except_the_carried_ones(engine_factory, tmp_path):
    """脚本化的上午：外卖购买与送达、在家查询与休息、开车出发并打开 Keep（出发时触发一条可见的支出回调）、到达后收尾。
    每次观察：非 tool_call 记录的来源序号大于它那一种的界；序号不超过已发布界的来源只属于带入记录，或是上一次决策提交里
    产生的非记忆提示（开车出发之后的那次观察里这一分支确实出现）；每条 tool_call 对应一项占槽位的活动或一项做完的查询；
    同一时刻已发布条目的来源与进度层的来源只在带入记录上重叠。"""
    engine = engine_factory(callback_environment(tmp_path, visible_fee_rules(_DRIVING)), mutate=every_five_minutes)
    late = []
    _, bought = act(engine, "takeaway", purchase("牛肉面"))
    late.append(_check_bounds(engine, observe_only(engine, "buying")))
    finish_activity(engine, bought, "takeaway")
    _, asked = act(engine, "tea", query("柠檬茶"))
    until_ended(engine, asked.activity_id)
    late.append(_check_bounds(engine, observe_only(engine, "asked")))
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    (order,) = engine.context.state.deliveries.values()
    while engine.context.state.deliveries[order.order_id].status != "delivered":
        if engine.scheduling_state().ready_agent_ids:
            late.append(_check_bounds(engine, observe_only(engine, "resting")))
            name = engine.context.state.activities[rest.activity_id].name
            act(engine, f"report-{engine.context.state.current_time}", report_request(name), "report_status")
        else:
            advance_next(engine)
    late.append(_check_bounds(engine, observe_only(engine, "delivered")))
    until_ended(engine, rest.activity_id)
    name = engine.context.state.activities[rest.activity_id].name
    act(engine, "rest-done", conclude_request(name), "conclude_activity")
    _, (trip, keep) = act_group(engine, "depart", travel("公司", "驾车"), use_app("Keep"))
    assert (trip.status, keep.status) == ("in_progress", "in_progress"), (trip.error_code, keep.error_code)
    departed = _check_bounds(engine, observe_only(engine, "departed"))
    assert departed
    until_ended(engine, trip.activity_id)
    late.append(_check_bounds(engine, observe_only(engine, "arrived")))
    names = [item.name for item in engine.context.state.activities.values()
             if item.status not in {"active", "paused"} and item.concluded_at is None]
    act(engine, "arrived-done", conclude_request(*names), "conclude_activity")
    late.append(_check_bounds(engine, observe_only(engine, "settled")))
    assert not any(late)


def test_memory_view_returns_the_published_bound(engine_factory):
    """运行开始的第一次观察：已发布界是 max(0, B_D)；一次决策之后是那次决策第一条 action_result 所在提交的末序号；睡过零点
    后醒来的第一次观察是 B_D。"""
    engine = engine_factory(mutate=_overnight)
    observe_only(engine, "first")
    assert _published(engine) == max(0, _bound_today(engine))
    _, slept = act(engine, "sleep", sleep(480))
    assert slept.status == "in_progress", slept.error_code
    observe_only(engine, "asleep")
    first = _results(engine, slept.action_id)[0]
    assert _published(engine) == engine.context.store.commit_last_seq(first.event_seq)
    until_ended(engine, slept.activity_id)
    assert local_date(engine.context.state.current_time, engine.context.config.timezone) == "2026-01-06"
    observe_only(engine, "woke")
    assert _published(engine) == _bound_today(engine) > engine.context.store.commit_last_seq(first.event_seq)
