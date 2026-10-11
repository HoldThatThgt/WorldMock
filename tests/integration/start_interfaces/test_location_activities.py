"""在地点做什么由人物点名：工具层逐单元列出、点名开始与系列接续、暂停恢复与单元占用、并行与不抽随机数。"""

from __future__ import annotations

import json

import pytest

from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import (
    MAIN_TOOL, advance_until_ready, conclude_request, context, do_at, main_arguments, pause_request, progress_calls,
    purchase, report_request, sleep, stage_day_plan, travel, use_app,
)
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, act_group, events, finish_activity, reach
from worldmock.life.models import StartMainActivityRequest
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.randomness import random_stream
from worldmock.life.tool_groups import compact_tool_layer, expand_tool_layer
from worldmock.life.world_query import QueryWorldRequest, query_world

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
    """别的地点只有出行各方式的行（步行在前，夹具的公共交通、驾车、打车按身份升序），看不到那里能做什么；
    query_world 按动作筛选 travel 返回各方式的行。"""
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
    request = QueryWorldRequest(name="公司", action=None, category=None, available_only=False, offset=0)
    match = query_world(observed, request)["matches"][0]
    assert [row["detail"] for row in match["actions"]] == ["步行", "公共交通", "驾车", "打车"]
    assert all(row["action"] == "travel" for row in match["actions"])
    travelling = QueryWorldRequest(name=None, action="travel", category=None, available_only=False, offset=0)
    by_action = query_world(observed, travelling)["matches"]
    assert {row["name"] for row in by_action} == {row["name"] for row in locations}
    assert all([item["detail"] for item in row["actions"]] == ["步行", "公共交通", "驾车", "打车"] for row in by_action)
    only = QueryWorldRequest(name=None, action="interact", category=None, available_only=False, offset=0)
    names = {row["name"] for row in query_world(observed, only)["matches"]}
    assert "家" in names and "公司" not in names and "快递驿站" not in names


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
    finish_activity(engine, result, "named")
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


def test_naming_draws_no_random_number_and_same_seed_selects_the_same_content(engine_factory):
    """点名不消耗随机流：开始地点活动前后其他随机键的抽取结果不变；同种子两次运行选到的内容相同。"""
    first, second = engine_factory(), engine_factory()
    keys = ("function-app:probe", "purchase-duration:probe", "activity-action-named:0")
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


def test_series_resumes_the_unfinished_episode_then_starts_a_new_round(engine_factory, dinner_environment):
    """中途结束后再点名，从没做完的一集的原位置继续；整个系列做完后再点名开始新一轮。"""
    engine = engine_factory(environment_root=dinner_environment, mutate=_attentive)
    _, first = act(engine, "dinner", do_at("家", "工作日晚饭"))
    start = engine.context.state.current_time
    _decide_until(engine, start + 1800)
    name = engine.context.state.activities[first.activity_id].name
    _, ended = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert ended.status == "completed"
    progress = engine.context.state.agents[1].content_progress
    assert progress["d-1-prep"].completed_rounds == 1 and progress["d-2-eat"].position_seconds == 600
    observed, again = act(engine, "resume", do_at("家", "工作日晚饭"))
    row = next(item for item in _target(observed, "家")["actions"] if item["detail"] == "工作日晚饭")
    assert (row["duration"], row["note"]) == ("约 15 分钟", "第 2 集，共 3 集；上次没做完，接着做")
    resumed = engine.context.state.activities[again.activity_id]
    assert resumed.data["content_id"] == "d-2-eat" and resumed.data["start_position_seconds"] == 600
    assert resumed.planned_duration_seconds == 900 and again.data["activities"][0]["content_title"] == "吃饭"
    finished = finish_activity(engine, again, "resume")
    assert finished is not None
    done = engine.context.state.activities[again.activity_id]
    assert done.status == "completed" and done.elapsed_seconds == 1500
    assert [item["content_id"] for item in done.data["played"]] == ["d-2-eat", "d-3-wash"]
    observed, round_two = act(engine, "new-round", do_at("家", "工作日晚饭"))
    row = next(item for item in _target(observed, "家")["actions"] if item["detail"] == "工作日晚饭")
    assert (row["duration"], row["note"]) == ("约 20 分钟", "第 1 集，共 3 集")
    assert engine.context.state.activities[round_two.activity_id].data["content_id"] == "d-1-prep"


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
def test_paused_unit_is_concluded_later_and_then_continues_from_its_position(engine_factory, elsewhere):
    """暂停的地点活动之后在原地或在别处都可以用 conclude_activity 结束；结束后它占着的单元可以再点名，
    从上次的位置接着做。"""
    engine = engine_factory(mutate=_attentive)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed"
    paused_at = engine.context.state.current_time
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
    if elsewhere:
        _, back = act(engine, "back", travel("家", "驾车"))
        assert back.status == "in_progress", back.error_code
        finish_activity(engine, back, "back")
    observed, again = act(engine, "again", do_at("家", "测试休息"))
    unit = next(row for row in _target(observed, "家")["actions"] if row["detail"] == "测试休息")
    assert (unit["available"], unit["reason"], unit["duration"], unit["note"]) == (
        True, None, "约 10 分钟", "上次没做完，接着做")
    assert again.status == "in_progress", again.error_code
    resumed = engine.context.state.activities[again.activity_id]
    assert resumed.activity_id != rest.activity_id and resumed.data["content_id"] == "c-rest"
    assert resumed.data["start_position_seconds"] == 300 and resumed.planned_duration_seconds == 600


def test_unit_with_under_a_minute_left_starts_for_the_remainder_and_completes_the_round(engine_factory):
    """一件事只剩不到一分钟时：工具层照剩余时长列为可用，点名就从原位置接着做完这一轮，之后再点名是新的一轮。"""
    def near_the_end(persona, config):
        config["reconsideration_interval_seconds"] = 570

    engine = engine_factory(mutate=near_the_end)
    _, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    assert chat.status == "in_progress" and advance_until_ready(engine)
    name = engine.context.state.activities[chat.activity_id].name
    _, stopped = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert stopped.status == "completed", stopped.error_code
    assert engine.context.state.agents[1].content_progress["c-chat"].position_seconds == 570
    observed, again = act(engine, "again", do_at("家", "测试闲聊"))
    unit = next(row for row in _target(observed, "家")["actions"] if row["detail"] == "测试闲聊")
    assert (unit["available"], unit["reason"], unit["duration"], unit["note"]) == (
        True, None, "不到 1 分钟", "上次没做完，接着做")
    assert again.status == "in_progress", again.error_code
    activity = engine.context.state.activities[again.activity_id]
    assert activity.planned_duration_seconds == 30 and activity.data["start_position_seconds"] == 570
    reach(engine, activity.planned_end_at)
    done = engine.context.state.activities[again.activity_id]
    assert done.status == "completed" and done.elapsed_seconds == 30
    progress = engine.context.state.agents[1].content_progress["c-chat"]
    assert (progress.completed_rounds, progress.position_seconds) == (1, 0)
    finish_activity(engine, again, "again")
    fresh, _ = act(engine, "new-round", do_at("家", "测试闲聊"))
    unit = next(row for row in _target(fresh, "家")["actions"] if row["detail"] == "测试闲聊")
    assert (unit["duration"], unit["note"]) == ("约 10 分钟", None)


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
