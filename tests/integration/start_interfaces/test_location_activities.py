"""在地点做什么由人物点名：工具层逐单元列出、点名开始与系列接续、暂停恢复与单元占用、结束算做完、并行与不抽随机数。"""

from __future__ import annotations

import json

import pytest

from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import (
    MAIN_TOOL,
    action_identity,
    advance_until_ready,
    conclude_request,
    context,
    do_at,
    main_arguments,
    pause_request,
    progress_calls,
    purchase,
    report_request,
    sleep,
    stage_day_plan,
    travel,
    use_app,
)
from tests.life.environment_inputs import catalog_rows, copy_environment, meal_environment, write_catalog_rows
from tests.footprints.recording import export_completed
from tests.life.environment_runtime import (
    act,
    act_group,
    events,
    finish_activity,
    observe_only,
    one_hour,
    one_hour_every_five_minutes,
    reach,
    run_to_end,
    until_ended,
)
from worldmock.state.interaction.requests import StartMainActivityRequest
from worldmock.agents.prompt.prompt_schemas import validate_layer
from worldmock.foundation.randomness import random_stream
from worldmock.simulation_engine.observation.memory.days import published_today
from worldmock.simulation_engine.observation.memory.entries import short_name
from worldmock.agents.prompt.tool_groups import compact_tool_layer, expand_tool_layer

_ROW_KEYS = ["action", "detail", "available", "reason", "price", "duration", "quantity", "note"]
_SOURCE = {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}


def _attentive(persona, config):
    """每五分钟到一次重估间隔：人物在一件事做到一半时就有决策机会。"""
    config["reconsideration_interval_seconds"] = 300


def _episode(content_id: str, title: str, number: int, seconds: int) -> dict:
    """系列「工作日晚饭」绑定到家的一集。"""
    return {"content_id": content_id, "duration_seconds": seconds, "exertion": "light", "location_kinds": ["home"],
            "pushable": False, "relaxation_relief": "none", "series": {"episode": number, "title": "工作日晚饭"},
            "social_relief": "none", "summary": f"{title}的测试用合成摘要。", "summary_source": dict(_SOURCE),
            "title": title}


@pytest.fixture
def dinner_environment(tmp_path):
    """夹具副本：家多一个系列「工作日晚饭」（备菜 1200 秒、吃饭 1500 秒、洗碗 600 秒）。"""
    root = copy_environment(tmp_path / "dinner")
    rows = catalog_rows(root, "content-catalog.jsonl")
    rows += [_episode("d-1-prep", "备菜", 1, 1200), _episode("d-2-eat", "吃饭", 2, 1500),
             _episode("d-3-wash", "洗碗", 3, 600)]
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))
    return root


def _target(observed: dict, name: str) -> dict:
    """观察工具层里的一个目标行。"""
    return next(row for row in observed["tool_layer"]["targets"] if row["name"] == name)


def _decide_until(engine, moment: int) -> None:
    """逐边界推进到给定时刻，其间每次决策机会都只播报进行中的活动。"""
    reach(engine, moment)
    assert engine.context.state.current_time == moment


def test_current_location_lists_every_unit_then_sleep_with_eight_keys(engine_factory):
    """停留时当前地点逐单元列出且完整不截断，键序八字段，sleep 行排在其后且 detail 为 null。"""
    engine = engine_factory()
    observed = engine.observe(context(engine, "look"))
    home = _target(observed, "家")
    assert home == {"name": "家", "kind": "location", "phase": None, "state": "全天营业", "tools": [], "actions": [
        {"action": "interact", "detail": "测试闲聊", "available": True, "reason": None, "price": None,
         "duration": "约 10 分钟", "quantity": None, "note": None},
        {"action": "interact", "detail": "测试休息", "available": True, "reason": None, "price": None,
         "duration": "约 15 分钟", "quantity": None, "note": None},
        {"action": "sleep", "detail": None, "available": True, "reason": None, "price": None,
         "duration": None, "quantity": None, "note": "现在不欠觉"}]}
    assert all(list(row) == _ROW_KEYS for target in observed["tool_layer"]["targets"] for row in target["actions"])
    validate_layer(observed["tool_layer"])
    binding = next(row for row in observed["interaction_bindings"] if row["object"] == "家")
    assert [(row["action"], row["detail"]) for row in binding["actions"]] == [
        ("interact", "测试闲聊"), ("interact", "测试休息"), ("sleep", None)]
    assert binding["actions"][1]["content_ids"] == ["c-rest"] and binding["actions"][1]["episode_titles"] == []
    assert "content_ids" not in json.dumps(observed["tool_layer"], ensure_ascii=False)


def test_other_locations_only_offer_travel_by_each_travel_method(engine_factory):
    """别的地点只有出行各方式的行（步行在前，夹具的公共交通、驾车、打车按身份升序），看不到那里能做什么。"""
    engine = engine_factory()
    observed = engine.observe(context(engine, "look"))
    office = _target(observed, "公司")
    assert [(row["action"], row["detail"]) for row in office["actions"]] == [
        ("travel", "步行"), ("travel", "公共交通"), ("travel", "驾车"), ("travel", "打车")]
    assert [(row["price"], row["duration"], row["quantity"], row["note"]) for row in office["actions"]] == [
        ("0 元", "约 32 分钟", None, "费力"), ("2 元", "约 8 分钟", None, "轻松"),
        ("5 元", "约 5 分钟", None, "轻松"), ("14 元", "约 5 分钟", None, "轻松")]
    locations = [row for row in observed["tool_layer"]["targets"] if row["kind"] == "location" and row["name"] != "家"]
    assert locations and all({row["action"] for row in target["actions"]} == {"travel"} for target in locations)


def test_compact_rendering_groups_units_and_round_trips(engine_factory):
    """提示里的紧凑渲染把当前地点的单元归成一组、睡眠另成一组，并可无损还原成完整目标行。"""
    engine = engine_factory()
    layer = engine.observe(context(engine, "look"))["tool_layer"]
    compact = compact_tool_layer(layer)
    groups = [group for group in compact["groups"] if any(row["name"] == "家" for row in group["targets"])]
    assert groups == [
        {"kind": "location", "action": "interact", "shared": {"state": "全天营业", "available": True},
         "targets": [{"name": "家", "detail": "测试闲聊", "duration": "约 10 分钟"},
                     {"name": "家", "detail": "测试休息", "duration": "约 15 分钟"}]},
        {"kind": "location", "action": "sleep", "shared": {"state": "全天营业", "available": True, "note": "现在不欠觉"},
         "targets": [{"name": "家"}]}]
    assert expand_tool_layer(compact)["targets"] == layer["targets"]
    validate_layer(compact)


def _unit_note(observed: dict, unit: str) -> str | None:
    """观察里「家」下这件事那一行的 note。"""
    return next(row for row in _target(observed, "家")["actions"] if row["detail"] == unit)["note"]


def test_tool_layer_notes_say_which_unit_or_episode_provides_a_meal(engine_factory, tmp_path):
    """meal_environment 上观察「家」：「测试休息」一行写「供一餐」，「做晚饭」一行写「第 1 集，共 3 集；第 2 集供一餐」；
    系列做到第三集时这一行以「第 3 集，共 3 集」开头、以「第 2 集供一餐」结尾，第三集已有进度时中间多一句「上次没做完，
    接着做」。工具层通过层校验，紧凑渲染能还原。"""
    engine = engine_factory(meal_environment(tmp_path / "meal"), mutate=_attentive)
    observed, dinner = act(engine, "dinner", do_at("家", "做晚饭"))
    assert (_unit_note(observed, "测试休息"), _unit_note(observed, "做晚饭")) == ("供一餐", "第 1 集，共 3 集；第 2 集供一餐")
    assert _unit_note(observed, "测试闲聊") is None
    start = engine.context.state.current_time
    _decide_until(engine, start + 3300)
    assert engine.context.state.activities[dinner.activity_id].data["content_id"] == "dinner-3-wash"
    switched = observe_only(engine, "washing")
    assert _unit_note(switched, "做晚饭") == "第 3 集，共 3 集；第 2 集供一餐"
    _decide_until(engine, start + 3600)
    washing = observe_only(engine, "washing")
    assert _unit_note(washing, "做晚饭") == "第 3 集，共 3 集；上次没做完，接着做；第 2 集供一餐"
    for layer in (observed["tool_layer"], washing["tool_layer"]):
        validate_layer(layer)
        compact = compact_tool_layer(layer)
        validate_layer(compact)
        assert expand_tool_layer(compact)["targets"] == layer["targets"]


def test_no_location_offers_interact_or_sleep_while_on_the_road(engine_factory):
    """人在路上时不列任何地点的 interact 与 sleep 行；到了没有单元也不可睡的地点，那里没有可做的行。"""
    engine = engine_factory(mutate=_attentive)
    _, trip = act(engine, "go", travel("五里公园", "步行"))
    assert trip.status == "in_progress" and advance_until_ready(engine)
    assert engine.context.state.activities[trip.activity_id].status == "active"
    observed = engine.observe(context(engine, "on-road"))
    locations = [row for row in observed["tool_layer"]["targets"] if row["kind"] == "location"]
    assert not any(row["action"] in {"interact", "sleep"} for target in locations for row in target["actions"])
    assert _target(observed, "家")["actions"] == []
    finish_activity(engine, trip, "go")
    assert engine.context.state.agents[1].location_id == "park"
    arrived = engine.observe(context(engine, "arrived"))
    assert _target(arrived, "五里公园")["actions"] == []
    assert {row["action"] for row in _target(arrived, "家")["actions"]} == {"travel"}


@pytest.mark.parametrize("unit,content_id,seconds", [("测试闲聊", "c-chat", 600), ("测试休息", "c-rest", 900)])
def test_naming_a_unit_starts_exactly_that_content(engine_factory, unit, content_id, seconds):
    """点名独立内容开始这一条：活动占槽位 1，活动名用单元名，开始结果的内容标题是实际那条内容。"""
    engine = engine_factory()
    _, result = act(engine, "named", do_at("家", unit))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert activity.name == f"进行{unit}（1月5日 08:00 起）" and activity.resource_claims == {"first"}
    assert activity.data["content_id"] == content_id and activity.data["app_id"] is None
    assert activity.data["draw_index"] == 0 and activity.data["pinned_by_push_id"] is None
    assert activity.data["start_position_seconds"] == 0.0 and activity.planned_duration_seconds == seconds
    assert result.data["activities"][0]["content_title"] == unit
    until_ended(engine, result.activity_id)
    record = progress_calls(engine.observe(context(engine, "after")), "start_main_activity")[0]
    assert record["arguments"] == {"action": "interact", "target": "家", "detail": unit}


def test_start_result_shows_the_content_that_actually_started(engine_factory):
    """开始结果的 details.content 是实际开始的那条内容：地点内容的 app 为 null，没有推送。"""
    engine = engine_factory(mutate=_attentive)
    act(engine, "named", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    record = progress_calls(engine.observe(context(engine, "during")), "start_main_activity")[0]
    content = record["result"]["details"]["content"]
    assert content == {"app": None, "title": "测试休息", "summary": "测试休息的测试用合成摘要。",
                       "duration": "约 15 分钟", "pushed": False}
    assert record["result"]["details"]["planned"] == [{"name": "进行测试休息（1月5日 08:00 起）", "duration": "约 15 分钟"}]
    assert "contents" not in record["result"]["details"]


def test_naming_draws_no_random_number_and_same_seed_selects_the_same_content(engine_factory):
    """点名不消耗随机流：开始地点活动前后其他随机键的抽取结果不变；同种子两次运行选到的内容相同。"""
    first, second = engine_factory(), engine_factory()
    keys = ("function-app:probe", "purchase-duration:probe", f"activity-{action_identity('named')}:0")
    before = [random_stream(first.context.state.random_state, key).random() for key in keys]
    seed_state = first.context.state.random_state
    for engine in (first, second):
        _, result = act(engine, "named", do_at("家", "测试休息"))
        assert engine.context.state.activities[result.activity_id].data["content_id"] == "c-rest"
    assert first.context.state.random_state == second.context.state.random_state == seed_state
    assert [random_stream(first.context.state.random_state, key).random() for key in keys] == before
    other = engine_factory()
    _, chat = act(other, "named", do_at("家", "测试闲聊"))
    assert other.context.state.activities[chat.activity_id].data["content_id"] == "c-chat"


def test_series_starts_at_its_representative_and_continues_to_the_end(engine_factory, dinner_environment):
    """点名系列从代表集开始，做完自动接下一集，整个系列做完后停止；活动名用系列名，不用各集标题。"""
    engine = engine_factory(environment_root=dinner_environment)
    observed, result = act(engine, "dinner", do_at("家", "工作日晚饭"))
    row = next(item for item in _target(observed, "家")["actions"] if item["detail"] == "工作日晚饭")
    assert (row["duration"], row["note"]) == ("约 20 分钟", "第 1 集，共 3 集")
    assert [item["detail"] for item in _target(observed, "家")["actions"]] == [
        "测试闲聊", "测试休息", "工作日晚饭", None]
    start = engine.context.state.current_time
    activity = engine.context.state.activities[result.activity_id]
    assert activity.name == "进行工作日晚饭（1月5日 08:00 起）" and activity.data["content_id"] == "d-1-prep"
    assert result.data["activities"][0]["content_title"] == "备菜" and activity.planned_end_at == start + 1200
    _decide_until(engine, start + 1200)
    eating = engine.context.state.activities[result.activity_id]
    assert eating.status == "active" and eating.data["content_id"] == "d-2-eat"
    assert eating.planned_end_at == start + 2700 and eating.resource_claims == {"first"}
    _decide_until(engine, start + 2700)
    washing = engine.context.state.activities[result.activity_id]
    assert washing.status == "active" and washing.data["content_id"] == "d-3-wash"
    _decide_until(engine, start + 3300)
    done = engine.context.state.activities[result.activity_id]
    assert done.status == "completed" and done.ended_at == start + 3300
    assert [item["content_id"] for item in done.data["played"]] == ["d-1-prep", "d-2-eat", "d-3-wash"]
    progress = engine.context.state.agents[1].content_progress
    assert {key: progress[key].completed_rounds for key in ("d-1-prep", "d-2-eat", "d-3-wash")} == {
        "d-1-prep": 1, "d-2-eat": 1, "d-3-wash": 1}
    record = progress_calls(engine.observe(context(engine, "done")), "start_main_activity")[0]
    assert record["result"]["details"]["contents"] == [
        {"title": title, "summary": f"{title}的测试用合成摘要。", "duration": duration, "finished": True}
        for title, duration in (("备菜", "约 20 分钟"), ("吃饭", "约 25 分钟"), ("洗碗", "约 10 分钟"))]


def test_concluding_a_series_counts_the_whole_unit_and_the_next_start_is_episode_one(engine_factory,
                                                                                    dinner_environment):
    """第二集做到一半时结束：整个系列记为做完一轮，三集都是位置 0、轮数 1；工具层这一行是第一集的全长与「第 1 集，
    共 3 集」，再点名从第一集开头做起。"""
    engine = engine_factory(environment_root=dinner_environment, mutate=_attentive)
    _, first = act(engine, "dinner", do_at("家", "工作日晚饭"))
    start = engine.context.state.current_time
    _decide_until(engine, start + 1800)
    activity = engine.context.state.activities[first.activity_id]
    assert activity.data["content_id"] == "d-2-eat" and activity.media_position_seconds == 600
    _, ended = act(engine, "stop", conclude_request(activity.name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    progress = engine.context.state.agents[1].content_progress
    episodes = ("d-1-prep", "d-2-eat", "d-3-wash")
    assert {key: (row.position_seconds, row.completed_rounds) for key, row in progress.items()
            if key in episodes} == {key: (0.0, 1) for key in episodes}
    observed, again = act(engine, "again", do_at("家", "工作日晚饭"))
    row = next(item for item in _target(observed, "家")["actions"] if item["detail"] == "工作日晚饭")
    assert (row["available"], row["duration"], row["note"]) == (True, "约 20 分钟", "第 1 集，共 3 集")
    restarted = engine.context.state.activities[again.activity_id]
    assert restarted.data["content_id"] == "d-1-prep" and restarted.data["start_position_seconds"] == 0.0
    assert restarted.planned_duration_seconds == 1200 and again.data["activities"][0]["content_title"] == "备菜"


def test_episode_title_and_unknown_detail_are_corrected_without_substitution(engine_factory, dinner_environment):
    """写了某一集的标题或写错：环境复核合并为 ACTION_REQUEST_INVALID，系列排在候选最前，不替人物更改。"""
    engine = engine_factory(environment_root=dinner_environment)
    _, result = act(engine, "episode", do_at("家", "备菜"))
    assert result.status == "rejected" and result.error_code == "ACTION_REQUEST_INVALID"
    assert not engine.context.state.activities
    item = result.data["items"][0]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", "detail"] and item["written"] == "备菜"
    assert item["error_code"] == "NAME_NOT_FOUND"
    assert item["allowed_values"] == ["测试闲聊", "测试休息", "工作日晚饭"]
    assert item["why"] == "家 offers no interact option named 备菜; changing spelling cannot create one."
    assert item["candidates"][0] == {"tool": "start_main_activity", "note": "约 20 分钟", "arguments": {
        "action": "interact", "target": "家", "detail": "工作日晚饭"}}
    assert result.data["reason"] == item["why"] and result.data["fix"] == "Use an exact known name or a candidate."
    _, elsewhere = act(engine, "elsewhere", do_at("公司", "季度销售数据整理"))
    assert elsewhere.data["items"][0]["error_code"] == "ACTION_NOT_OFFERED"
    assert elsewhere.data["items"][0]["allowed_values"] == ["travel"]
    _, normal = act(engine, "normalized", do_at("家", "工作日 晚饭"))
    assert normal.status == "in_progress" and normal.data["resolved"] == [{"written": "工作日 晚饭", "as": "工作日晚饭"}]


def test_doing_something_blocks_departure_and_another_unit_by_slot(engine_factory):
    """正在地点做事时出发、睡觉或点名另一个单元不自动暂停，按同槽位占用拒绝，工具层各行同样标注。"""
    engine = engine_factory(mutate=_attentive)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    reason = f"The first activity slot is occupied by {name}."
    observed, other = act(engine, "other", do_at("家", "测试闲聊"))
    assert other.status == "rejected" and other.error_code == "RESOURCE_CONFLICT" and other.message == reason
    assert other.data["next_step"] == f"Conclude or pause {name} first."
    rows = _target(observed, "家")["actions"]
    assert [(row["available"], row["reason"]) for row in rows] == [(False, reason)] * 3
    assert _target(observed, "公司")["actions"][0]["reason"] == reason
    activity = _target(observed, name)
    assert activity["kind"] == "activity" and [tool["tool"] for tool in activity["tools"]] == [
        "report_status", "pause_activity", "conclude_activity"]
    _, leave = act(engine, "leave", travel("公司", "步行"))
    assert leave.status == "rejected" and leave.message == reason
    _, nap = act(engine, "nap", sleep(30))
    assert nap.status == "rejected" and nap.message == reason
    assert engine.context.state.activities[rest.activity_id].status == "active"


def test_paused_unit_is_held_until_resumed_or_concluded(engine_factory):
    """暂停让出槽位 1；被暂停活动占着的单元再点名被拒并点名该活动，其他单元可以开始；用 report_status 恢复。"""
    engine = engine_factory(mutate=_attentive)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed"
    observed, held = act(engine, "again", do_at("家", "测试休息"))
    reason = f"测试休息 is held by the paused activity {name}."
    assert held.status == "rejected" and held.error_code == "RESOURCE_CONFLICT" and held.message == reason
    assert held.data["next_step"] == f"Resume {name} with report_status, or conclude it."
    assert held.data["unmet"] == ["content_unit"] and held.data["fix"] is None
    rows = {row["detail"]: row for row in _target(observed, "家")["actions"]}
    assert (rows["测试休息"]["available"], rows["测试休息"]["reason"]) == (False, reason)
    assert rows["测试闲聊"]["available"] is True and rows[None]["available"] is True
    paused_row = observed["todo_layer"]["paused_activities"][0]
    assert paused_row["name"] == name and [tool["tool"] for tool in paused_row["tools"]] == [
        "report_status", "conclude_activity"]
    position = engine.context.state.agents[1].content_progress["c-rest"].position_seconds
    _, resumed = act(engine, "resume", report_request(name), "report_status")
    assert resumed.status == "completed"
    activity = engine.context.state.activities[rest.activity_id]
    assert activity.status == "active" and activity.resource_claims == {"first"}
    assert activity.data["start_position_seconds"] == position == 300
    assert activity.planned_end_at == engine.context.state.current_time + 600


def test_pausing_frees_slot_one_for_departure_and_the_unit_resumes_only_at_home(engine_factory):
    """暂停后可以出发；在别处不能恢复；回到开始地点用 report_status 恢复，从共享进度继续。"""
    engine = engine_factory(mutate=_attentive)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    act(engine, "pause", pause_request(name), "pause_activity")
    _, trip = act(engine, "leave", travel("五里公园", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    finish_activity(engine, trip, "leave")
    _, away = act(engine, "resume-away", report_request(name), "report_status")
    assert away.status == "rejected" and away.error_code == "ACTIVITY_PRECONDITION_UNMET"
    _, back = act(engine, "back", travel("家", "驾车"))
    assert back.status == "in_progress", back.error_code
    finish_activity(engine, back, "back")
    _, resumed = act(engine, "resume", report_request(name), "report_status")
    assert resumed.status == "completed"
    activity = engine.context.state.activities[rest.activity_id]
    assert activity.status == "active" and activity.data["start_position_seconds"] == 300
    assert activity.planned_end_at - engine.context.state.current_time == 600


def test_location_activity_runs_alongside_purchase_and_app(engine_factory):
    """在地点做事的同时开始购买和用内容 app，地点活动不中断，进度与结束时刻不受影响。"""
    engine = engine_factory(mutate=_attentive)
    start = engine.context.state.current_time
    _, (rest, basket) = act_group(engine, "pair", do_at("家", "测试休息"), purchase("薯片"))
    assert (rest.status, basket.status) == ("in_progress", "in_progress")
    finish_activity(engine, basket, "basket")
    assert advance_until_ready(engine)
    assert engine.context.state.activities[rest.activity_id].status == "active"
    _, video = act(engine, "video", use_app("哔哩哔哩"))
    assert video.status == "in_progress", video.error_code
    claims = sorted(next(iter(item.resource_claims)) for item in engine.context.state.activities.values()
                    if item.status == "active")
    assert claims == ["first", "second"]
    reach(engine, start + 900)
    done = engine.context.state.activities[rest.activity_id]
    assert done.status == "completed" and done.ended_at == start + 900 and done.elapsed_seconds == 900
    assert done.media_position_seconds == 900
    assert engine.context.state.agents[1].content_progress["c-rest"].completed_rounds == 1
    received = [row for row in events(engine, "environment_event") if row.data.get("kind") == "goods_received"]
    assert not received or all(row.data["goods_id"] == "chips" for row in received)


def _home_unit(content_id: str, title: str) -> dict:
    """绑定到家的一条十分钟的独立内容。"""
    return {"content_id": content_id, "duration_seconds": 600, "exertion": "light", "location_kinds": ["home"],
            "pushable": False, "relaxation_relief": "none", "social_relief": "none",
            "summary": f"{title}的测试用合成摘要。", "summary_source": dict(_SOURCE), "title": title}


def _host_feedback(requests: list[dict]) -> dict:
    """宿主在本次决策内对第一条回复给出的纠错：第二次模型请求末尾那条 tool 消息的正文。"""
    message = requests[1]["messages"][-1]
    assert message["role"] == "tool"
    return json.loads(message["content"])


@pytest.mark.parametrize("elsewhere", [False, True], ids=["later-at-home", "elsewhere"])
def test_paused_unit_concluded_later_counts_as_done_and_starts_from_the_beginning(engine_factory, elsewhere):
    """暂停在 300 秒处的地点活动，之后在原地或在别处用 conclude_activity 结束：这件事记为做完一轮，它占着的单元
    可以再点名，从头做起、计划全长。"""
    engine = engine_factory(mutate=_attentive)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed"
    paused_at = engine.context.state.current_time
    assert engine.context.state.agents[1].content_progress["c-rest"].position_seconds == 300
    _, other = act(engine, "other", travel("五里公园", "驾车") if elsewhere else do_at("家", "测试闲聊"))
    assert other.status == "in_progress", other.error_code
    finish_activity(engine, other, "other")
    assert engine.context.state.current_time > paused_at
    assert (engine.context.state.agents[1].location_id == "park") is elsewhere
    observed, ended = act(engine, "conclude", conclude_request(name), "conclude_activity")
    offered = {tool["tool"]: (tool["available"], tool["reason"])
               for tool in observed["todo_layer"]["paused_activities"][0]["tools"]}
    assert offered["conclude_activity"] == (True, None)
    assert ended.status == "completed", ended.error_code
    closed = engine.context.state.activities[rest.activity_id]
    assert closed.status == "cancelled" and closed.concluded_at == engine.context.state.current_time
    assert closed.elapsed_seconds == 300
    progress = engine.context.state.agents[1].content_progress["c-rest"]
    assert (progress.position_seconds, progress.completed_rounds) == (0.0, 1)
    if elsewhere:
        _, back = act(engine, "back", travel("家", "驾车"))
        assert back.status == "in_progress", back.error_code
        finish_activity(engine, back, "back")
    observed, again = act(engine, "again", do_at("家", "测试休息"))
    unit = next(row for row in _target(observed, "家")["actions"] if row["detail"] == "测试休息")
    assert (unit["available"], unit["reason"], unit["duration"], unit["note"]) == (True, None, "约 15 分钟", None)
    assert again.status == "in_progress", again.error_code
    restarted = engine.context.state.activities[again.activity_id]
    assert restarted.activity_id != rest.activity_id and restarted.data["content_id"] == "c-rest"
    assert restarted.data["start_position_seconds"] == 0.0 and restarted.planned_duration_seconds == 900


def _resting_five_minutes(engine_factory, mutate=_attentive):
    """在家点名「测试休息」，推进到开始后五分钟的那次决策机会；返回引擎与开始结果。"""
    engine = engine_factory(mutate=mutate)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    assert advance_until_ready(engine)
    assert engine.context.state.activities[rest.activity_id].elapsed_seconds == 300
    return engine, rest


def test_concluded_unit_starts_again_at_full_length(engine_factory):
    """「测试休息」做 5 分钟后结束：共享进度位置 0、轮数 1；工具层这一行可用、全长「约 15 分钟」、没有说明；再点名
    从头开始，计划 900 秒。"""
    engine, rest = _resting_five_minutes(engine_factory)
    name = engine.context.state.activities[rest.activity_id].name
    _, ended = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    progress = engine.context.state.agents[1].content_progress["c-rest"]
    assert (progress.position_seconds, progress.completed_rounds) == (0.0, 1)
    observed, again = act(engine, "again", do_at("家", "测试休息"))
    unit = next(row for row in _target(observed, "家")["actions"] if row["detail"] == "测试休息")
    assert (unit["available"], unit["reason"], unit["duration"], unit["note"]) == (True, None, "约 15 分钟", None)
    assert again.status == "in_progress", again.error_code
    restarted = engine.context.state.activities[again.activity_id]
    assert restarted.data["start_position_seconds"] == 0.0 and restarted.planned_duration_seconds == 900


def test_concluding_a_finished_activity_does_not_add_a_round(engine_factory):
    """自然做完时轮数是 1；人物来收尾之后仍是 1，位置仍是 0。"""
    engine = engine_factory()
    start = engine.context.state.current_time
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    reach(engine, start + 900)
    done = engine.context.state.activities[rest.activity_id]
    assert done.status == "completed" and done.concluded_at is None
    assert engine.context.state.agents[1].content_progress["c-rest"].completed_rounds == 1
    _, concluded = act(engine, "conclude", conclude_request(done.name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    assert concluded.data["outcome"] == [{"activity": done.name, "outcome": "完成"}]
    progress = engine.context.state.agents[1].content_progress["c-rest"]
    assert (progress.position_seconds, progress.completed_rounds) == (0.0, 1)


def test_run_cut_off_keeps_location_progress(engine_factory):
    """运行截止时做到一半的地点活动被截断：共享进度原样留着，位置是做到的秒数、轮数 0。"""
    engine = engine_factory(mutate=one_hour)
    _, trip = act(engine, "commute", travel("公司", "驾车"))
    finish_activity(engine, trip, "commute")
    _, work = act(engine, "work", do_at("公司", "季度销售数据整理"))
    assert work.status == "in_progress", work.error_code
    run_to_end(engine)
    activity = engine.context.state.activities[work.activity_id]
    assert activity.status == "truncated" and 0 < activity.elapsed_seconds < 32400
    progress = engine.context.state.agents[1].content_progress["topic-sales"]
    assert (progress.position_seconds, progress.completed_rounds) == (float(activity.elapsed_seconds), 0)


def test_concluded_location_activity_stays_cancelled_and_writes_no_extra_event(engine_factory):
    """被结束的地点活动仍是取消：状态 cancelled、interruption_reason agent_cancelled，收尾结果的终局是「提前结束」；
    这次提交里这项活动的事件恰是一条 activity_concluded 和一条第二条结果，不写 activity_progress。"""
    engine, rest = _resting_five_minutes(engine_factory)
    name = engine.context.state.activities[rest.activity_id].name
    before = engine.context.store.last_event_seq
    _, ended = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert ended.status == "completed" and ended.data["outcome"] == [{"activity": name, "outcome": "提前结束"}]
    closed = engine.context.state.activities[rest.activity_id]
    assert (closed.status, closed.boundary_type, closed.interruption_reason) == (
        "cancelled", "cancelled", "agent_cancelled")
    own = [row for row in events(engine) if row.event_seq > before and row.activity_id == rest.activity_id]
    assert [(row.event_type, row.data.get("kind"), row.data.get("status")) for row in own] == [
        ("environment_event", "activity_concluded", None), ("action_result", None, "cancelled")]
    assert own[1].action_id == rest.action_id and own[1].data["message"] == "Activity cancelled"


@pytest.mark.parametrize("request_builder,unfinished", [
    (lambda: do_at("家", "测试休息"), False), (lambda: use_app("Keep"), True)], ids=["location", "app"])
def test_unfinished_half_is_only_in_the_full_line_of_app_content(engine_factory, request_builder, unfinished):
    """做 5 分钟后结束、离开这一段：记忆层「今天」里在地点做的事的结束行以「，提前结束」收尾；用内容 app 的结束行不写
    「结束使用」「提前结束」也没有「未完」，条目全文的完整行以「（《测试慢跑》未完）」收尾。"""
    engine = engine_factory(mutate=_attentive)
    _, started = act(engine, "started", request_builder())
    assert started.status == "in_progress", started.error_code
    assert advance_until_ready(engine)
    name = engine.context.state.activities[started.activity_id].name
    _, ended = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    _, trip = act(engine, "leave", travel("五里公园", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    memory = engine.observe(context(engine, "road"))["memory_layer"]
    label = short_name(name)
    (sentence,) = [line for item in memory["今天"] for line in item.get("核验", ()) if line.startswith(f"{label} ")]
    today = published_today(engine.context.inputs, engine.context.store, 1, engine.context.state.current_time,
                            engine.context.store.last_event_seq)
    (full,) = [line.full for entry in today.entries for line in entry.lines
               if line.kind == "ending" and line.text == sentence]
    if unfinished:
        assert not any(word in sentence for word in ("结束使用", "提前结束", "未完"))
        assert full.endswith("（《测试慢跑》未完）")
    else:
        assert sentence.endswith("，提前结束") and "未完" not in full


def test_long_office_content_concluded_midway_starts_again_at_full_length(engine_factory):
    """办公室九小时的「季度销售数据整理」做一段后结束：再点名是从头的 32400 秒。"""
    engine = engine_factory(mutate=_attentive)
    _, trip = act(engine, "commute", travel("公司", "驾车"))
    finish_activity(engine, trip, "commute")
    _, work = act(engine, "work", do_at("公司", "季度销售数据整理"))
    assert work.status == "in_progress", work.error_code
    assert engine.context.state.activities[work.activity_id].planned_duration_seconds == 32400
    assert advance_until_ready(engine)
    name = engine.context.state.activities[work.activity_id].name
    _, ended = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    _, again = act(engine, "again", do_at("公司", "季度销售数据整理"))
    assert again.status == "in_progress", again.error_code
    restarted = engine.context.state.activities[again.activity_id]
    assert restarted.data["start_position_seconds"] == 0.0 and restarted.planned_duration_seconds == 32400


def test_export_certifies_a_run_with_a_concluded_location_activity(engine_factory, tmp_path):
    """结束地点活动、再点名、跑到截止：检查点、导出与认证通过；再点名之后的进度事件算重复，content_contact 的
    repeat_watch 不小于 1。"""
    engine, rest = _resting_five_minutes(engine_factory, one_hour_every_five_minutes)
    name = engine.context.state.activities[rest.activity_id].name
    _, ended = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    _, again = act(engine, "again", do_at("家", "测试休息"))
    assert again.status == "in_progress", again.error_code
    directory = tmp_path / "export"
    export_completed(engine, directory)
    quality = json.loads((directory / "quality.json").read_bytes())
    assert quality["content_contact"]["repeat_watch"] >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("place,allowed", [("家", []), ("五里公园", ["travel"])], ids=["origin", "destination"])
async def test_interact_submitted_on_the_road_is_not_offered_by_host_or_environment(engine_factory, place, allowed):
    """人在路上时对出发地或目的地写 interact：名称解析阶段就是 ACTION_NOT_OFFERED，宿主与环境给出同一条明细，
    不开始任何活动。"""
    engine = engine_factory(mutate=_attentive)
    stage_day_plan(engine, submitted=True)
    _, trip = act(engine, "go", travel("五里公园", "步行"))
    assert trip.status == "in_progress" and advance_until_ready(engine)
    name = engine.context.state.activities[trip.activity_id].name
    _, refused = act(engine, "bypass", do_at(place, "测试休息"))
    assert refused.status == "rejected" and refused.error_code == "ACTION_REQUEST_INVALID"
    _, requests, _, results = await decide(engine, "host", [
        ToolResponse(MAIN_TOOL, main_arguments("interact", place, "测试休息")),
        ToolResponse("report_status", report_request(name).model_dump(mode="json"), "call-valid")])
    assert results[0].status == "completed", results[0].error_code
    feedback = _host_feedback(requests)
    assert feedback["error_code"] == "ACTION_NOT_OFFERED"
    (item,) = feedback["items"]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", "action"]
    assert (item["written"], item["error_code"], item["allowed_values"]) == ("interact", "ACTION_NOT_OFFERED", allowed)
    assert all(row["arguments"]["action"] == "travel" for row in item["candidates"])
    assert bool(item["candidates"]) is bool(allowed)
    assert refused.data["items"] == [item]
    assert list(engine.context.state.activities) == [trip.activity_id]
    assert [row.data["tool_name"] for row in events(engine, "action_requested")] == [
        MAIN_TOOL, MAIN_TOOL, "report_status"]


@pytest.mark.asyncio
async def test_detail_matching_two_units_after_normalisation_is_ambiguous(engine_factory, tmp_path):
    """两个单元名只差全角与半角括号（登记允许）：写第三种写法时归一后对应两个单元，宿主与环境都给 NAME_AMBIGUOUS，
    allowed_values 是当前地点的全部单元名，候选是这两个单元；照原样写其中一个仍然开始那一个。"""
    root = copy_environment(tmp_path / "brackets")
    rows = catalog_rows(root, "content-catalog.jsonl")
    rows += [_home_unit("c-dinner-a", "晚饭(一)"), _home_unit("c-dinner-b", "晚饭（一）")]
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))
    engine = engine_factory(root)
    stage_day_plan(engine, submitted=True)
    _, requests, _, results = await decide(engine, "host", [
        ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "晚饭 (一)")),
        ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "晚饭(一)"), "call-valid")])
    feedback = _host_feedback(requests)
    assert feedback["error_code"] == "NAME_AMBIGUOUS"
    (item,) = feedback["items"]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", "detail"] and item["written"] == "晚饭 (一)"
    assert item["error_code"] == "NAME_AMBIGUOUS"
    assert item["allowed_values"] == ["测试闲聊", "晚饭(一)", "晚饭（一）", "测试休息"]
    assert item["why"] == "The name 晚饭 (一) identifies several options of 家." == feedback["reason"]
    assert [row["arguments"] for row in item["candidates"]] == [
        main_arguments("interact", "家", "晚饭(一)"), main_arguments("interact", "家", "晚饭（一）")]
    assert results[0].status == "in_progress", results[0].error_code
    started = engine.context.state.activities[results[0].activity_id]
    assert started.data["content_id"] == "c-dinner-a" and started.name.startswith("进行晚饭(一)（")
    bypass = engine_factory(root)
    _, refused = act(bypass, "bypass", do_at("家", "晚饭 (一)"))
    assert refused.status == "rejected" and refused.error_code == "ACTION_REQUEST_INVALID"
    assert refused.data["items"] == [item] and not bypass.context.state.activities


def test_interact_without_a_detail_is_refused_by_the_environment_too(engine_factory):
    """绕过宿主提交 detail 为 null 的 interact：环境用同一个请求模型复核，定位到 detail，不替人物补。"""
    engine = engine_factory()
    bad = StartMainActivityRequest.model_construct(action="interact", target="家", detail=None)
    _, refused = act(engine, "no-detail", bad, MAIN_TOOL)
    assert refused.status == "rejected" and refused.error_code == "ACTION_REQUEST_INVALID"
    assert [(row["path"], row["written"], row["why"]) for row in refused.data["items"]] == [
        (["tool_calls", 0, "function", "arguments", "detail"], None, "detail is required when action is interact")]
    assert refused.data["fix"] == "Correct every reported parameter." and not engine.context.state.activities
