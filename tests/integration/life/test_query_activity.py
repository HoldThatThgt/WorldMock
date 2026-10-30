"""查询的一生：名称怎样认、占槽位 2 与冲突、时长、用哪个 app、不能暂停、做完自己结束并请人物决策、提前结束与截止、
人在路上（docs/design/04-simulation-engine.md「出行、内容与查询」）。全部经真实请求、引擎与 SQLite。

夹具环境 2026 年 1 月 5 日 08:00 人在家：一心面馆只登记了美团，三杯咖啡登记了饿了么和美团，四季便利与二味小炒没有
登记 app；平台笔记本只在哔哩哔哩里卖，纸巾只有四季便利卖。
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.life.conftest import (
    SIDE_TOOL,
    action_identity,
    action_rows,
    advance_next,
    advance_until_ready,
    conclude_request,
    context,
    do_at,
    pause_request,
    progress_calls,
    purchase,
    query,
    sleep,
    target_tools,
    tool_targets,
    travel,
    use_app,
)
from tests.life.current_world import stage_moment
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, act_group, events, finish_activity, run_to_end, settle_decision
from worldmock.agents.prompt.prompt_schemas import validate_layer
from worldmock.foundation.randomness import random_stream
from worldmock.foundation.time.readable import duration_phrase
from worldmock.simulation_engine.observation.layers.result_projection import public_result

ZONE = ZoneInfo("Asia/Shanghai")
#: 运行开始的时刻：2026 年 1 月 5 日（周一）08:00。
START = int(datetime(2026, 1, 5, 8, 0, tzinfo=ZONE).timestamp())
CATEGORIES = {"零食": "snack", "食品": "meal", "饮料": "drink", "商品": "goods"}
NEITHER = " Neither start request was started."
UNKNOWN_FIX = "Use an exact known goods name or one of the goods categories."
UNKNOWN_STEP = "Choose a known product or one of the goods categories."
PAUSE_REASON = "This activity cannot be paused at its current phase or location."


def _attentive(persona, config):
    """每 60 秒到一次重估间隔：查询进行中人物就有决策机会。"""
    config["reconsideration_interval_seconds"] = 60


def _started(engine, label, *targets, prefer=None):
    """当前时刻提交一次查询，断言受理，返回开始的查询活动。"""
    _, result = act(engine, label, query(*targets, prefer=prefer))
    assert result.status == "in_progress", (result.error_code, result.message)
    return engine.context.state.activities[result.activity_id]


def _drawn_seconds(engine, label) -> int:
    """测试里独立算出的查询时长：按 query-duration:<动作身份> 的随机流在 300 到 600 秒之间取整数。"""
    key = f"query-duration:{action_identity(label)}"
    return random_stream(engine.context.state.random_state, key).randint(300, 600)


def _label_lasting(engine, seconds: int) -> str:
    """一个标签：act 以 action-<标签> 为动作身份提交查询时，抽到的时长恰是 seconds 秒。"""
    return next(label for label in (f"q{index}" for index in itertools.count())
                if _drawn_seconds(engine, label) == seconds)


def _to_end(engine, activity_id):
    """逐边界推进到这项活动不再进行；途中人物有决策机会时照实播报。"""
    while engine.context.state.activities[activity_id].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"report-{engine.context.state.current_time}")
        else:
            advance_next(engine)


def _mid_query(engine_factory, *targets, prefer=None, **create):
    """每 60 秒一次重估：开始一项查询，推进到它进行中的下一次决策机会，返回引擎与查询活动。"""
    engine = engine_factory(mutate=_attentive, **create)
    activity = _started(engine, "query", *targets, prefer=prefer)
    assert advance_until_ready(engine)
    assert engine.context.state.activities[activity.activity_id].status == "active"
    return engine, activity


def _rejected(result, code):
    """断言一条请求被拒、码是给定的那一个。"""
    assert (result.status, result.error_code) == ("rejected", code), result.message


@pytest.mark.parametrize("written", list(CATEGORIES))
def test_each_goods_category_starts_a_query(engine_factory, written):
    """四个类别名各自开始一项查询：活动数据记下类别键，活动名用类别的中文名，受理结果不记归一。"""
    engine = engine_factory()
    _, result = act(engine, "category", query(written))
    activity = engine.context.state.activities[result.activity_id]
    assert result.status == "in_progress" and "resolved" not in result.data
    assert activity.data["targets"] == [{"kind": "category", "category": CATEGORIES[written]}]
    assert activity.name == f"查询{written}（1月5日 08:00 起）"


def test_spaced_category_name_is_accepted_and_the_normalisation_is_recorded(engine_factory):
    """带空白或全角空格写的类别名照样受理：受理结果与第一条结果的 resolved 记下所写与规范名称，活动名用规范名称。"""
    engine = engine_factory()
    _, result = act(engine, "spaced", query("　食 品 ", "饮料"))
    assert result.status == "in_progress"
    assert result.data["resolved"] == [{"written": "　食 品 ", "as": "食品"}]
    activity = engine.context.state.activities[result.activity_id]
    assert activity.name == "查询食品、饮料（1月5日 08:00 起）"
    details = progress_calls(engine.observe(context(engine, "during")), SIDE_TOOL)[0]["result"]["details"]
    assert details["resolved"] == [{"written": "　食 品 ", "as": "食品"}]


def test_category_wins_over_goods_of_the_same_name_and_repeats_count_once(engine_factory, tmp_path):
    """副本里加一件叫「零食」的商品：所写「零食」按类别认。「薯片」「薯片」「零食」只留两项，活动名按所写次序。"""
    root = copy_environment(tmp_path / "snack-named")
    rows = catalog_rows(root, "merchant-catalog.jsonl")
    rows.append({"category": "snack", "goods_id": "snack-box", "name": "零食", "price_fen": 900,
                 "store_kinds": ["convenience_store"]})
    write_catalog_rows(root, "merchant-catalog.jsonl", sorted(rows, key=lambda row: row["goods_id"]))
    engine = engine_factory(root)
    assert "零食" in tool_targets(engine.observe(context(engine, "shown")))
    activity = _started(engine, "same-name", "零食")
    assert activity.data["targets"] == [{"kind": "category", "category": "snack"}]
    repeated = _started(engine_factory(), "repeated", "薯片", "薯片", "零食")
    assert repeated.data["targets"] == [{"kind": "goods", "goods_id": "chips"}, {"kind": "category", "category": "snack"}]
    assert repeated.name == "查询薯片、零食（1月5日 08:00 起）"


def test_activity_name_lists_the_canonical_names_in_the_written_order(engine_factory):
    """活动名是「查询」加各项的规范名称、去重后以「、」相连，再加开始时刻。"""
    assert _started(engine_factory(), "tea", "柠檬茶").name == "查询柠檬茶（1月5日 08:00 起）"
    assert _started(engine_factory(), "mixed", "食品", "柠檬茶", "食品").name == "查询食品、柠檬茶（1月5日 08:00 起）"
    assert _started(engine_factory(), "full", "饮 料").name == "查询饮料（1月5日 08:00 起）"


def _unknown(written: str, position: int = 0) -> dict:
    """查询写错名称时那一条明细的完整写法。"""
    reason = f"No goods or goods category is named {written}; the goods categories are 零食, 食品, 饮料, 商品."
    return {"path": ["tool_calls", 0, "function", "arguments", "target", position], "written": written,
            "error_code": "SUPPLY_NOT_FOUND", "allowed_values": ["零食", "食品", "饮料", "商品"],
            "expected": "An exact known goods name or one of the goods categories is required.", "why": reason,
            "candidates": []}


@pytest.mark.parametrize("written", ["不存在的东西", "家", "三杯咖啡", "哔哩哔哩"])
def test_unknown_names_get_one_refusal_and_change_nothing(engine_factory, written):
    """不存在的名称、地点名、有内容的内容 app 名都得 SUPPLY_NOT_FOUND：原因列出四个类别名，改法与下一步逐字，明细定位到
    数组里的那一项、不带候选；没有新活动，时钟、随机状态、身体与本人这一秒的同刻轮数都不变。"""
    engine = engine_factory()
    before = engine.context.state.model_copy(deep=True)
    _, result = act(engine, "unknown", query(written))
    _rejected(result, "SUPPLY_NOT_FOUND")
    item = _unknown(written)
    assert result.message == result.data["reason"] == item["why"]
    assert (result.data["fix"], result.data["next_step"]) == (UNKNOWN_FIX, UNKNOWN_STEP)
    assert result.data["items"] == [item]
    state = engine.context.state
    assert state.activities == before.activities and state.deliveries == before.deliveries
    assert (state.current_time, state.random_state, state.accounts) == (
        before.current_time, before.random_state, before.accounts)
    person, earlier = state.agents[1], before.agents[1]
    assert (person.same_moment_rounds, person.body) == (earlier.same_moment_rounds, earlier.body)


def test_only_the_first_wrong_name_in_written_order_is_reported(engine_factory):
    """两项都写错时只报按数组次序的第一项，path 末位是它在所写数组里的下标。"""
    engine = engine_factory()
    _, result = act(engine, "two-wrong", query("柠檬茶", "第一个错名", "食品", "第二个错名"))
    _rejected(result, "SUPPLY_NOT_FOUND")
    assert result.data["items"] == [_unknown("第一个错名", 1)]
    assert not engine.context.state.activities


def test_query_after_a_finished_activity_requires_its_conclusion_first(engine_factory):
    """「测试闲聊」做完待收尾时提交查询：ACTIVITY_CONCLUSION_REQUIRED，点名那项活动，没有新活动。"""
    engine = engine_factory()
    _, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    _to_end(engine, chat.activity_id)
    name = engine.context.state.activities[chat.activity_id].name
    _, result = act(engine, "too-soon", query("柠檬茶"))
    _rejected(result, "ACTIVITY_CONCLUSION_REQUIRED")
    assert result.message == f"Conclude the finished activities {name} first."
    assert [item.kind for item in engine.context.state.activities.values()] == ["interact"]


def test_query_holds_slot_two_against_every_other_side_start(engine_factory):
    """查询的 resource_claims 恒为 {"second"}；它进行中开始购买、打开 app、再查一次都得 RESOURCE_CONFLICT，
    点名查询，下一步只写结束。"""
    engine, running = _mid_query(engine_factory, "柠檬茶")
    assert running.resource_claims == {"second"}
    name = running.name
    for label, request in (("buy", purchase("薯片")), ("app", use_app("哔哩哔哩")), ("again", query("饮料"))):
        _, result = act(engine, label, request)
        _rejected(result, "RESOURCE_CONFLICT")
        assert result.message == f"The second activity slot is occupied by {name}."
        assert result.data["next_step"] == f"Conclude {name} first."
    assert [item.kind for item in engine.context.state.activities.values()] == ["query"]


def test_running_app_with_three_minutes_left_blocks_a_query_and_may_be_paused(engine_factory):
    """槽位 2 被离结束不少于 180 秒的内容 app 使用占着：查询被拒，下一步写结束或暂停。"""
    engine = engine_factory(mutate=_attentive)
    _, app = act(engine, "app", use_app("哔哩哔哩"))
    assert app.status == "in_progress"
    assert advance_until_ready(engine)
    using = engine.context.state.activities[app.activity_id]
    assert using.planned_end_at - engine.context.state.current_time >= 180
    _, result = act(engine, "blocked", query("柠檬茶"))
    _rejected(result, "RESOURCE_CONFLICT")
    assert result.message == f"The second activity slot is occupied by {using.name}."
    assert result.data["next_step"] == f"Conclude or pause {using.name} first."


def test_query_runs_beside_a_location_activity(engine_factory):
    """做着地点里的事时可以开始查询；两者也可以在同一次回复里一起开始，两项活动同一秒开始、各占一个槽位。"""
    engine = engine_factory(mutate=_attentive)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    beside = _started(engine, "beside", "柠檬茶")
    assert beside.resource_claims == {"second"}
    assert engine.context.state.activities[rest.activity_id].status == "active"
    _, (location, side) = act_group(engine_factory(), "together", do_at("家", "测试休息"), query("食品"))
    assert (location.status, side.status) == ("in_progress", "in_progress")
    group = engine_factory()
    _, (first, second) = act_group(group, "pair", do_at("家", "测试休息"), query("食品"))
    started = [group.context.state.activities[item.activity_id] for item in (first, second)]
    assert [item.started_at for item in started] == [START, START]
    assert [item.resource_claims for item in started] == [{"first"}, {"second"}]


def test_query_and_sleep_exclude_each_other(engine_factory):
    """查询进行中睡觉被拒，下一步是结束槽位 2 的活动；睡眠与查询同一次回复被拒，两项都不开始。"""
    engine, _ = _mid_query(engine_factory, "柠檬茶")
    _, result = act(engine, "sleep", sleep(60))
    _rejected(result, "RESOURCE_CONFLICT")
    assert result.message == "Sleep requires the second activity slot to be empty."
    assert result.data["next_step"] == "Conclude the second activity first."
    group = engine_factory()
    _, results = act_group(group, "together", sleep(60), query("柠檬茶"))
    for item in results:
        _rejected(item, "RESOURCE_CONFLICT")
        assert item.message == "Sleep requires the second activity slot to be empty." + NEITHER
        assert item.data["fix"] == "Submit start_main_activity alone to sleep."
    assert not group.context.state.activities


def test_duration_is_drawn_once_and_frozen_on_the_activity(engine_factory):
    """几条查询的计划时长都等于测试里独立算出的 randint(300, 600)，计划结束是受理时刻加它。"""
    for label in ("d1", "d2", "d3", "d4"):
        engine = engine_factory()
        activity = _started(engine, label, "柠檬茶")
        seconds = _drawn_seconds(engine, label)
        assert 300 <= seconds <= 600
        assert (activity.planned_duration_seconds, activity.planned_end_at) == (seconds, START + seconds)


def test_a_query_cannot_be_paused_and_the_tool_layer_says_why(engine_factory):
    """pause_activity 点名查询：ACTIVITY_PAUSE_UNAVAILABLE，原因与改法逐字；工具层这项活动的 pause_activity 一行不可用、
    原因一字不差，report_status 与 conclude_activity 两行可用。"""
    engine, running = _mid_query(engine_factory, "柠檬茶")
    tools = target_tools(engine.observe(context(engine, "look")), running.name)
    assert tools["pause_activity"] == {"tool": "pause_activity", "available": False, "reason": PAUSE_REASON}
    assert tools["report_status"]["available"] and tools["conclude_activity"]["available"]
    _, result = act(engine, "pause", pause_request(running.name), "pause_activity")
    _rejected(result, "ACTIVITY_PAUSE_UNAVAILABLE")
    assert result.message == PAUSE_REASON and result.data["fix"] == "Conclude this activity instead."
    assert engine.context.state.activities[running.activity_id].status == "active"


def _app_and_scope(engine, label, *targets, prefer=None):
    """提交一次查询，返回（这几分钟的 app，答案是否只列经它能买的）。"""
    activity = _started(engine, label, *targets, prefer=prefer)
    return activity.data["app_id"], activity.data["scoped"]


def _drawn_app(engine, label, candidates) -> str:
    """测试里独立算出的 app：候选按 app_id 升序，按 query-app:<动作身份> 等概率取一个。"""
    key = f"query-app:{action_identity(label)}"
    return random_stream(engine.context.state.random_state, key).choice(sorted(candidates))


@pytest.mark.parametrize("prefer", [None, "不存在的 app", "支付宝", "饿了么"])
def test_unusable_prefer_lets_the_environment_draw_the_app(engine_factory, prefer):
    """prefer 用不上（null、不存在、功能 app、一件都不卖的下单 app）：在卖所查商品的下单 app 里独立算出的那一个在前台，
    答案列出全部途径，没有拒绝事件。"""
    for label, targets, candidates in (("tea", ("柠檬茶",), {"eleme", "meituan"}),
                                       ("noodles", ("牛肉面",), {"meituan"}),
                                       ("both", ("柠檬茶", "平台笔记本"), {"bilibili", "eleme", "meituan"})):
        engine = engine_factory()
        if prefer == "饿了么" and "eleme" in candidates:
            continue
        assert _app_and_scope(engine, label, *targets, prefer=prefer) == (_drawn_app(engine, label, candidates), False)
        assert [row for row in events(engine, "action_result") if row.data["status"] == "rejected"] == []


def test_usable_prefer_is_the_app_and_goods_no_app_sells_leave_the_phone_empty(engine_factory):
    """prefer 用得上：app 是它，答案只列经它能买的；写近似的「美 团」规整后同样用得上。纸巾只有没上 app 的店卖：
    手机上没有 app，答案列出全部途径。"""
    assert _app_and_scope(engine_factory(), "tea", "柠檬茶", prefer="饿了么") == ("eleme", True)
    assert _app_and_scope(engine_factory(), "spaced", "食品", prefer="美 团") == ("meituan", True)
    assert _app_and_scope(engine_factory(), "book", "平台笔记本", prefer="哔哩哔哩") == ("bilibili", True)
    assert _app_and_scope(engine_factory(), "tissue", "纸巾", prefer="美团") == (None, False)
    assert _app_and_scope(engine_factory(), "tissue-null", "纸巾") == (None, False)


def test_selling_counts_registration_not_opening_hours(engine_factory, tmp_path):
    """登记了美团的两家店受理那一刻都打烊：美团照样在前台，答案只列经它能买的。"""
    root = copy_environment(tmp_path / "meituan-closed")
    rows = catalog_rows(root, "location.jsonl")
    for row in rows:
        if row["location_id"] in {"cafe", "noodle-1"}:
            row["opens"] = {"weekly": {"tue": [["00:00", "24:00"]]}}
    write_catalog_rows(root, "location.jsonl", rows)
    assert _app_and_scope(engine_factory(root), "closed", "柠檬茶", prefer="美团") == ("meituan", True)


def test_running_query_shows_only_its_plan_and_the_tool_layer_shows_progress(engine_factory):
    """第一条结果只有 planned，时长短语与抽到的时长相符，没有 query；中途被叫起时进度层显示的仍是这一条，工具层有这项
    活动，写着已做与还剩。"""
    engine = engine_factory(mutate=_attentive)
    _, result = act(engine, "q", query("柠檬茶"))
    seconds = _drawn_seconds(engine, "q")
    first = progress_calls(engine.observe(context(engine, "first")), SIDE_TOOL)[0]["result"]
    name = engine.context.state.activities[result.activity_id].name
    assert first["status"] == "in_progress" and first["details"] == {
        "planned": [{"name": name, "duration": duration_phrase(seconds)}]}
    assert advance_until_ready(engine)
    observed = engine.observe(context(engine, "middle"))
    assert progress_calls(observed, SIDE_TOOL)[0]["result"] == first
    target = tool_targets(observed)[name]
    assert target["kind"] == "activity" and target["phase"] == "进行中"
    assert target["state"].startswith("已做") and "还剩" in target["state"]


def test_finished_query_concludes_itself_without_a_conclusion_event(engine_factory):
    """推进到计划结束：completed，concluded_at 等于 ended_at，没有待收尾的活动，工具层不再列它，这次推进没有写
    activity_concluded。"""
    engine = engine_factory()
    activity = _started(engine, "q", "柠檬茶")
    while engine.context.state.current_time < activity.planned_end_at:
        advance_next(engine)
    done = engine.context.state.activities[activity.activity_id]
    assert (done.status, done.boundary_type, done.ended_at) == ("completed", "planned_end", activity.planned_end_at)
    assert done.concluded_at == done.ended_at
    observed = engine.observe(context(engine, "after"))
    assert activity.name not in tool_targets(observed)
    assert not [row for row in observed["activity_bindings"] if row["phase"] == "刚结束"]
    kinds = [row.data.get("kind") for row in events(engine, "environment_event")]
    assert "activity_concluded" not in kinds


def test_finishing_query_wakes_the_person_beside_a_long_activity(engine_factory):
    """人物同时做着「测试休息」：查询做完的那一秒 ready_agent_ids 含本人，这一秒身体结算记录的唤醒原因含 planned_end。"""
    engine = engine_factory()
    _, (rest, side) = act_group(engine, "pair", do_at("家", "测试休息"), query("柠檬茶"))
    end = engine.context.state.activities[side.activity_id].planned_end_at
    assert end < engine.context.state.activities[rest.activity_id].planned_end_at
    while engine.context.state.current_time < end:
        assert not engine.scheduling_state().ready_agent_ids
        advance_next(engine)
    assert engine.scheduling_state().ready_agent_ids == (1,)
    settled = [row for row in events(engine, "environment_event")
               if row.data.get("kind") == "body_settlement" and row.occurred_at == end]
    assert settled and "planned_end" in settled[-1].data["wake_reasons"]
    assert engine.context.state.activities[rest.activity_id].status == "active"


def test_query_ending_with_the_main_activity_still_needs_the_main_conclusion(engine_factory):
    """查询与「测试休息」在同一秒做完：这次决策里购买得 ACTIVITY_CONCLUSION_REQUIRED，只点名地点里的那件事；
    收尾之后同一秒再决策，购买被受理。"""
    engine = engine_factory(mutate=_attentive)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    end = engine.context.state.activities[rest.activity_id].planned_end_at
    while not 300 <= end - engine.context.state.current_time <= 600:
        assert advance_until_ready(engine)
        if not 300 <= end - engine.context.state.current_time <= 600:
            settle_decision(engine, f"wait-{engine.context.state.current_time}")
    label = _label_lasting(engine, end - engine.context.state.current_time)
    activity = _started(engine, label, "柠檬茶")
    assert activity.planned_end_at == end
    _to_end(engine, rest.activity_id)
    assert engine.context.state.current_time == end
    rest_name = engine.context.state.activities[rest.activity_id].name
    _, blocked = act(engine, "buy-early", purchase("柠檬茶"))
    _rejected(blocked, "ACTIVITY_CONCLUSION_REQUIRED")
    assert blocked.message == f"Conclude the finished activities {rest_name} first."
    _, concluded = act(engine, "conclude", conclude_request(rest_name), "conclude_activity")
    assert concluded.status == "completed"
    _, bought = act(engine, "buy", purchase("柠檬茶"))
    assert bought.status == "in_progress" and engine.context.state.current_time == end


def test_concluded_query_has_no_answer_and_frees_slot_two(engine_factory):
    """conclude_activity 结束进行中的查询：活动 cancelled、没有 answer；这条查询请求的最新结果是 cancelled，
    details.query 恰为 app 与「没有查完」；那条结束请求自己的 outcome 是「提前结束」；两者都只在事件里，收尾之后的
    观察里都不在进度层；同一秒再决策，购买被受理。"""
    engine, running = _mid_query(engine_factory, "零食")
    app = {"eleme": "饿了么", "meituan": "美团", "bilibili": "哔哩哔哩"}[running.data["app_id"]]
    _, concluded = act(engine, "stop", conclude_request(running.name), "conclude_activity")
    assert concluded.status == "completed"
    stopped = engine.context.state.activities[running.activity_id]
    assert (stopped.status, stopped.interruption_reason) == ("cancelled", "agent_cancelled")
    assert "answer" not in stopped.data and stopped.concluded_at == engine.context.state.current_time
    results = events(engine, "action_result")
    terminal = [row for row in results if row.action_id == running.origin_action_id][-1]
    record = public_result(terminal.data, engine.context.inputs, {})
    assert record["status"] == "cancelled" and record["details"]["query"] == {"app": app, "state": "没有查完"}
    own = public_result(next(row for row in results if row.action_id == concluded.action_id).data,
                        engine.context.inputs, {})["details"]
    assert own["outcome"] == [{"activity": running.name, "outcome": "提前结束"}] and "query" not in own
    observed = engine.observe(context(engine, "after"))
    assert not progress_calls(observed, SIDE_TOOL) and not progress_calls(observed, "conclude_activity")
    validate_layer(observed["progress_layer"])
    _, bought = act(engine, "buy", purchase("薯片"))
    assert bought.status == "in_progress"


def _hour(persona, config):
    """运行从 1 月 5 日 08:00 开始、持续一小时（起止都要在整点）。"""
    config["stop_time"] = config["start_time"] + timedelta(hours=1)


def test_run_end_truncates_a_running_query_but_one_ending_with_the_run_finishes(engine_factory):
    """计划结束晚于截止的查询被截断，没有答案，终态结果的 details.query 是「没有查完」；计划结束恰等于截止时刻的
    查询正常做完并带答案。"""
    stop = START + 3600
    label = _label_lasting(engine_factory(), 450)
    cut = engine_factory(mutate=_hour)
    stage_moment(cut, stop - 400)
    activity = _started(cut, label, "柠檬茶")
    run_to_end(cut)
    truncated = cut.context.state.activities[activity.activity_id]
    assert (truncated.status, truncated.ended_at) == ("truncated", stop) and "answer" not in truncated.data
    assert _terminal_details(cut, activity)["query"] == {"app": _app_name(truncated), "state": "没有查完"}
    whole = engine_factory(mutate=_hour)
    stage_moment(whole, stop - 450)
    finished = _started(whole, label, "柠檬茶")
    run_to_end(whole)
    done = whole.context.state.activities[finished.activity_id]
    assert (done.status, done.ended_at) == ("completed", stop) and done.data["answer"]["rows"]
    assert _terminal_details(whole, finished)["query"]["rows"]


def _app_name(activity) -> str | None:
    """查询这几分钟的 app 的完整名称。"""
    return {"eleme": "饿了么", "meituan": "美团", "bilibili": "哔哩哔哩", None: None}[activity.data["app_id"]]


def _terminal_details(engine, activity) -> dict:
    """开始这项查询的那条动作最后一条结果投影给人物的 details。"""
    rows = [row for row in events(engine, "action_result") if row.action_id == activity.origin_action_id]
    return public_result(rows[-1].data, engine.context.inputs, {})["details"]


def test_query_finishing_at_local_midnight_answers_after_the_day_plan(engine_factory):
    """计划结束恰为本地零点的查询照常做完带答案；零点计划登记之后人物得到决策，唤醒原因是 planned_end。"""
    def late(persona, config):
        config["start_time"] = datetime(2026, 1, 5, 23, 0, tzinfo=ZONE)
        config["stop_time"] = datetime(2026, 1, 6, 8, 0, tzinfo=ZONE)

    midnight = int(datetime(2026, 1, 6, tzinfo=ZONE).timestamp())
    engine = engine_factory(mutate=late)
    stage_moment(engine, midnight - 600)
    activity = _started(engine, _label_lasting(engine, 600), "饮料")
    assert activity.planned_end_at == midnight
    while engine.context.state.current_time < midnight:
        advance_next(engine)
    done = engine.context.state.activities[activity.activity_id]
    assert done.status == "completed" and done.concluded_at == midnight and "answer" in done.data
    assert [row for row in events(engine, "environment_event") if row.data.get("kind") == "day_plan_generated"]
    assert "2026-01-06" in engine.context.state.agents[1].plan_submitted_dates
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert "planned_end" in engine.context.state.agents[1].pending_wake_reasons


def _road(persona, config):
    """每秒都到播报间隔：人物刚出发就有决策机会。"""
    config["progress_report_interval_seconds"] = 1


def test_a_query_cannot_start_on_the_road(engine_factory):
    """路上提交查询被拒：ACTIVITY_PRECONDITION_UNMET，原因与下一步逐字，未满足条件是 stay_location。"""
    engine = engine_factory(mutate=_road)
    _, departure = act(engine, "go", travel("公司"))
    assert departure.status == "in_progress"
    assert advance_until_ready(engine)
    _, result = act(engine, "on-road", query("柠檬茶"))
    _rejected(result, "ACTIVITY_PRECONDITION_UNMET")
    assert result.message == "A query cannot start while travelling."
    assert result.data["next_step"] == "Start the query after arriving at a location."
    assert result.data["unmet"] == ["stay_location"]


def test_a_query_cannot_start_with_departure(engine_factory):
    """出发与查询同一次回复：两条都不开始，原因是查询不能与出发同批再加整组那一句，人物仍在家。"""
    engine = engine_factory()
    _, results = act_group(engine, "together", travel("公司"), query("柠檬茶"))
    for item in results:
        _rejected(item, "ACTIVITY_PRECONDITION_UNMET")
        assert item.message == "A query cannot start with departure." + NEITHER
        assert item.data["next_step"] == "Finish or conclude the query before leaving."
        assert item.data["unmet"] == ["stay_location"]
    assert not engine.context.state.activities and engine.context.state.agents[1].location_id == "home"


def test_departure_waits_until_the_running_query_is_concluded(engine_factory):
    """查询进行中出发被拒并点名查询；用 conclude_activity 结束它之后同一秒再决策，出发被受理。"""
    engine, running = _mid_query(engine_factory, "柠檬茶")
    _, refused = act(engine, "leave", travel("公司"))
    _rejected(refused, "ACTIVITY_PRECONDITION_UNMET")
    assert refused.message == f"Conclude {running.name} before departure."
    assert refused.data["next_step"] == f"Conclude {running.name}."
    assert refused.data["unmet"] == ["activities_before_departure"]
    _, stopped = act(engine, "stop", conclude_request(running.name), "conclude_activity")
    assert stopped.status == "completed"
    _, departure = act(engine, "leave-now", travel("公司"))
    assert departure.status == "in_progress"


def test_query_burns_like_idle_time_and_needs_grow_naturally(engine_factory):
    """只有查询在进行的那一段，身体结算与同样长的空闲逐值相同，放松与社交需要照自然增长；活动数据里没有 exertion。"""
    from worldmock.simulation_engine.body.body import last_flows, settle_shared_body
    from worldmock.state.world.agent import BodyState

    engine = engine_factory()
    activity = _started(engine, "q", "柠檬茶")
    assert "exertion" not in activity.data
    _to_end(engine, activity.activity_id)
    rows = [row.data for row in events(engine, "environment_event") if row.data.get("kind") == "body_settlement"
            and row.data["activity_ids"] == [activity.activity_id]]
    assert rows
    for data in rows:
        record = data["body_settlement"]
        before = BodyState.model_validate(record["before"])
        idle = settle_shared_body(before, (), data["interval_end"] - data["interval_start"], engine.context.body)
        last_flows()
        assert record["after"] == idle.after.model_dump(mode="json")
        assert idle.after.relaxation_need > before.relaxation_need and idle.after.social_need > before.social_need


def test_callable_index_categories_are_the_names_a_query_accepts(engine_factory):
    """工具层 callable_index.goods 每行的 category 写进 target 都被受理；「商品」一行数 2 个，不含只在 app 里卖的
    平台笔记本，查询「商品」的答案里却有它；工具层没有任何 action 为 query 的动作行。"""
    engine = engine_factory()
    observed = engine.observe(context(engine, "index"))
    rows = observed["tool_layer"]["callable_index"]["goods"]
    assert {row["category"] for row in rows} == set(CATEGORIES)
    assert next(row["count"] for row in rows if row["category"] == "商品") == "2 个"
    assert not [row for target in observed["tool_layer"]["targets"] for row in target["actions"]
                if row["action"] == "query"]
    for row in rows:
        _started(engine_factory(), f"index-{CATEGORIES[row['category']]}", row["category"])
    goods = engine_factory()
    activity = _started(goods, "goods", "商品")
    _to_end(goods, activity.activity_id)
    answered = goods.context.state.activities[activity.activity_id].data["answer"]["rows"]
    assert "online-notebook" in {row["goods_id"] for row in answered}
    assert not action_rows(observed, "柠檬茶", "query")


def test_test_helpers_never_conclude_a_query(engine_factory):
    """对查询的受理结果调用 finish_activity，再用 run_to_end 跑到截止：全程没有点名这项查询的 conclude_activity 请求，
    运行正常结束。"""
    engine = engine_factory(mutate=_hour)
    _, result = act(engine, "q", query("柠檬茶"))
    finished = finish_activity(engine, result, "q")
    assert finished.status == "completed" and finished.concluded_at == finished.ended_at
    run_to_end(engine)
    named = [row for row in events(engine, "action_requested") if row.data["tool_name"] == "conclude_activity"
             and any(item["activity_name"] == finished.name for item in row.data["request"]["items"])]
    assert named == [] and [row for row in events(engine, "run_ended")]
