"""带入的记录：用内容 app 或在地点做事的开始请求，在这项活动仍在进行或在其中结束的每一段里都有记录；出行、购买和
暂停着的活动不带入。经真实请求、引擎与 SQLite，读观察里的进度层、待办层与记忆层。"""

from __future__ import annotations

import json

import pytest

from tests.footprints.recording import export_completed
from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import (
    SIDE_TOOL, advance_until_ready, conclude_request, do_at, pause_request, progress_calls, purchase, report_request,
    side_arguments, stage_day_plan, travel, use_app,
)
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import (
    act, act_group, events, every_five_minutes, finish_activity, observe_only, one_hour_every_five_minutes,
    until_ended,
)
from worldmock.life import sessions


def _keep(observed: dict) -> list[dict]:
    """进度层里打开 Keep 的那条 start_side_activity 记录（没有时为空）。"""
    return [row for row in progress_calls(observed, SIDE_TOOL) if row["arguments"]["target"] == ["Keep"]]


def _rest(observed: dict) -> list[dict]:
    """进度层里点名「测试休息」的那条 start_main_activity 记录（没有时为空）。"""
    return [row for row in progress_calls(observed, "start_main_activity")
            if row["arguments"] == {"action": "interact", "target": "家", "detail": "测试休息"}]


def _on_road(engine) -> bool:
    """人物此刻的这一段是一段路。"""
    return sessions.current_session(engine.context.state, 1).kind == "transit"


#: Keep 唯一的内容「测试慢跑」（1200 秒）做完后第二条结果里的那一行。
_RUN_DONE = {"title": "测试慢跑", "summary": "测试慢跑的测试用合成摘要。", "duration": "约 20 分钟", "finished": True}


def _first_result(record: dict) -> None:
    """记录显示的是第一条结果：进行中，带 planned 与开始的那条内容「测试慢跑」，没有 contents。"""
    result = record["result"]
    assert result["status"] == "in_progress" and "contents" not in result["details"]
    assert result["details"]["content"]["title"] == "测试慢跑" and result["details"]["planned"]


def _departed_with_keep(engine_factory, method: str = "步行", mutate=every_five_minutes):
    """在家同一次回复里出发去公司并打开 Keep；返回引擎与（出行，Keep）两个开始结果。"""
    engine = engine_factory(mutate=mutate)
    _, (trip, keep) = act_group(engine, "depart", travel("公司", method), use_app("Keep"))
    assert (trip.status, keep.status) == ("in_progress", "in_progress"), (trip.error_code, keep.error_code)
    return engine, trip, keep


def test_app_use_started_with_departure_is_recorded_on_the_road(engine_factory):
    """与出发同一次回复里打开的 app：路上第一次观察的进度层有这条 start_side_activity 记录，结果是带 content 的
    第一条，没有 self_note。"""
    engine, _, _ = _departed_with_keep(engine_factory)
    assert advance_until_ready(engine) and _on_road(engine)
    observed = observe_only(engine, "road")
    records = _keep(observed)
    assert len(records) == 1, observed["progress_layer"]["events"]
    assert records[0]["arguments"]["action"] == "interact" and "self_note" not in records[0]
    _first_result(records[0])


def test_app_use_started_before_departure_is_recorded_on_the_road(engine_factory):
    """出发之前就在用的 app，下一次决策出发：路上的进度层有这条记录，结果是第一条。"""
    engine = engine_factory(mutate=every_five_minutes)
    _, keep = act(engine, "keep", use_app("Keep"))
    assert keep.status == "in_progress", keep.error_code
    assert advance_until_ready(engine)
    _, trip = act(engine, "leave", travel("公司", "步行"))
    assert trip.status == "in_progress", trip.error_code
    assert advance_until_ready(engine) and _on_road(engine)
    (record,) = _keep(observe_only(engine, "road"))
    _first_result(record)


def test_app_use_still_running_on_arrival_is_recorded_in_the_new_stay(engine_factory):
    """路上打开、到达时还没放完的 app：到达后的停留里有这条记录（第一条结果）；放完之后同一条记录带 contents。"""
    engine, trip, keep = _departed_with_keep(engine_factory, "驾车")
    finish_activity(engine, trip, "commute")
    assert engine.context.state.agents[1].location_id == "office" and not _on_road(engine)
    assert engine.context.state.activities[keep.activity_id].status == "active"
    (record,) = _keep(observe_only(engine, "arrived"))
    _first_result(record)
    until_ended(engine, keep.activity_id)
    (done,) = _keep(observe_only(engine, "done"))
    assert done["occurred_at"] == record["occurred_at"]
    assert done["result"]["status"] == "completed" and done["result"]["details"] == {"contents": [_RUN_DONE]}


def test_app_use_still_running_after_the_trip_is_ended_midway_is_recorded_in_the_new_stay(engine_factory):
    """出发并打开 app，路上用 conclude_activity 只结束出行：人回到出发地，新停留的观察里有这条记录，结果是第一条；
    放完后带 contents。"""
    engine, trip, keep = _departed_with_keep(engine_factory)
    assert advance_until_ready(engine) and _on_road(engine)
    name = engine.context.state.activities[trip.activity_id].name
    _, ended = act(engine, "turn-back", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    assert engine.context.state.agents[1].location_id == "home" and not _on_road(engine)
    assert engine.context.state.activities[keep.activity_id].status == "active"
    (record,) = _keep(observe_only(engine, "back-home"))
    _first_result(record)
    until_ended(engine, keep.activity_id)
    (done,) = _keep(observe_only(engine, "done"))
    assert done["result"]["details"] == {"contents": [_RUN_DONE]}


def _paused_rest(engine_factory):
    """在家点名「测试休息」，做 5 分钟后暂停；返回引擎、开始结果与活动名。"""
    engine = engine_factory(mutate=every_five_minutes)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed", paused.error_code
    return engine, rest, name


def _go(engine, place: str, label: str) -> None:
    """驾车去一个地点并在到达后给出行收尾。"""
    _, trip = act(engine, label, travel(place, "驾车"))
    assert trip.status == "in_progress", trip.error_code
    finish_activity(engine, trip, label)


def _paused_rows(observed: dict) -> list[str]:
    """待办层里暂停着的活动名。"""
    return [row["name"] for row in observed["todo_layer"]["paused_activities"]]


def test_location_activity_resumed_in_a_later_stay_is_recorded_there(engine_factory):
    """在家暂停后出门再回来，用 report_status 恢复：恢复之后这段停留里开始请求的记录在（第一条结果）；做完后带
    contents。"""
    engine, rest, name = _paused_rest(engine_factory)
    _go(engine, "五里公园", "out")
    _go(engine, "家", "back")
    assert not _rest(observe_only(engine, "before-resume"))
    _, resumed = act(engine, "resume", report_request(name), "report_status")
    assert resumed.status == "completed", resumed.error_code
    (record,) = _rest(observe_only(engine, "resumed"))
    assert record["result"]["status"] == "in_progress" and record["result"]["details"]["content"]["title"] == "测试休息"
    until_ended(engine, rest.activity_id)
    (done,) = _rest(observe_only(engine, "done"))
    assert done["result"]["status"] == "completed"
    assert done["result"]["details"]["contents"] == [
        {"title": "测试休息", "summary": "测试休息的测试用合成摘要。", "duration": "约 15 分钟", "finished": True}]


def test_paused_activity_concluded_elsewhere_is_recorded_where_it_ended(engine_factory):
    """暂停的地点活动在别处被结束：结束它的那一段里有开始请求的记录，结果是 cancelled 的第二条，带 contents。"""
    engine, rest, name = _paused_rest(engine_factory)
    _go(engine, "五里公园", "out")
    assert not _rest(observe_only(engine, "at-park"))
    _, ended = act(engine, "conclude", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    (record,) = _rest(observe_only(engine, "ended"))
    assert (record["result"]["status"], record["result"]["message"]) == ("cancelled", "Activity cancelled")
    assert record["result"]["details"] == {"contents": [
        {"title": "测试休息", "summary": "测试休息的测试用合成摘要。", "duration": "约 15 分钟", "finished": False}]}


def test_carried_record_shows_contents_after_finishing_on_the_road(engine_factory):
    """步行去公司（约 32 分钟）的路上放完 Keep（20 分钟）：同一条带入的记录从第一条结果换成带 contents 的第二条。"""
    engine, _, keep = _departed_with_keep(engine_factory)
    assert advance_until_ready(engine) and _on_road(engine)
    (record,) = _keep(observe_only(engine, "road"))
    _first_result(record)
    until_ended(engine, keep.activity_id)
    assert _on_road(engine)
    (done,) = _keep(observe_only(engine, "finished-on-road"))
    assert (done["occurred_at"], done["arguments"]) == (record["occurred_at"], record["arguments"])
    assert (done["result"]["status"], done["result"]["message"]) == ("completed", "Activity completed")
    assert done["result"]["details"] == {"contents": [_RUN_DONE]}


def _with_text(response: ToolResponse, text: str) -> ToolResponse:
    """给脚本化的回复加上正文：它会成为这次决策第一条请求的 self_note。"""
    response.document["choices"][0]["message"]["content"] = text
    response.choices[0].message.content = text
    return response


@pytest.mark.asyncio
async def test_carried_record_has_no_self_note(engine_factory):
    """经真实宿主：一次决策只打开 Keep，回复带正文，这条 action_requested 有自述，出发前的观察里这条记录带 self_note；
    下一次决策出发，路上带入的同一条记录没有 self_note。"""
    engine = engine_factory(mutate=every_five_minutes)
    stage_day_plan(engine, submitted=True)
    note = "先刷一会儿 Keep。"
    _, _, _, results = await decide(engine, "keep", [_with_text(
        ToolResponse(SIDE_TOOL, side_arguments("interact", "Keep")), note)])
    assert results[0].status == "in_progress", results[0].error_code
    requested = events(engine, "action_requested")[-1]
    assert requested.action_id == results[0].action_id and requested.data["self_note"] == note
    assert advance_until_ready(engine)
    (home,) = _keep(observe_only(engine, "home"))
    assert home["self_note"] == note
    _, trip = act(engine, "leave", travel("公司", "步行"))
    assert trip.status == "in_progress", trip.error_code
    assert advance_until_ready(engine) and _on_road(engine)
    (record,) = _keep(observe_only(engine, "road"))
    assert "self_note" not in record and record["arguments"] == home["arguments"]


def test_departure_and_purchase_records_are_not_carried(engine_factory):
    """出发加快递购买的开始组：路上的进度层没有这两条开始请求的记录；付款后的购买句照旧在记忆层出发地那一段。"""
    engine = engine_factory(mutate=every_five_minutes)
    _, (trip, bought) = act_group(engine, "depart-and-buy", travel("公司", "步行"), purchase("平台笔记本"))
    assert (trip.status, bought.status) == ("in_progress", "in_progress"), (trip.error_code, bought.error_code)
    until_ended(engine, bought.activity_id)
    assert _on_road(engine) and engine.context.state.activities[bought.activity_id].status == "completed"
    observed = observe_only(engine, "road")
    assert not progress_calls(observed, "start_main_activity") and not progress_calls(observed, SIDE_TOOL)
    (home,) = observed["memory_layer"]["earlier_today"]
    assert [line for line in home["settled"] if "买了平台笔记本 1 份" in line], home["settled"]


@pytest.mark.parametrize("case", ["paused-in-an-earlier-stay", "paused-after-being-carried"])
def test_paused_activity_is_not_carried_until_resumed(engine_factory, case):
    """暂停着的活动不带入，只列在待办层。在别的段里暂停着：别处的观察里没有它的记录。带入之后在这一段里又暂停：
    开始请求的记录离开进度层，暂停请求的记录在，待办层有它；再恢复后记录重新出现。"""
    engine, rest, name = _paused_rest(engine_factory)
    _go(engine, "五里公园", "out")
    away = observe_only(engine, "away")
    assert not _rest(away) and _paused_rows(away) == [name]
    if case == "paused-in-an-earlier-stay":
        return
    _go(engine, "家", "back")
    _, resumed = act(engine, "resume", report_request(name), "report_status")
    assert resumed.status == "completed", resumed.error_code
    assert _rest(observe_only(engine, "resumed"))
    assert advance_until_ready(engine)
    _, paused = act(engine, "pause-again", pause_request(name), "pause_activity")
    assert paused.status == "completed", paused.error_code
    held = observe_only(engine, "held")
    assert not _rest(held) and _paused_rows(held) == [name]
    assert progress_calls(held, "pause_activity")
    _, again = act(engine, "resume-again", report_request(name), "report_status")
    assert again.status == "completed", again.error_code
    (record,) = _rest(observe_only(engine, "resumed-again"))
    assert record["result"]["status"] == "in_progress"


@pytest.fixture
def keep_as_long_as_the_drive(engine_factory, tmp_path):
    """夹具副本：Keep 的「测试慢跑」改成恰好等于开车去公司的计划时长（由一次探测得到）。"""
    probe = engine_factory()
    _, trip = act(probe, "probe", travel("公司", "驾车"))
    seconds = probe.context.state.activities[trip.activity_id].planned_duration_seconds
    root = copy_environment(tmp_path / "keep-drive")
    rows = catalog_rows(root, "content-catalog.jsonl")
    for row in rows:
        if row["content_id"] == "c-run":
            row["duration_seconds"] = seconds
    write_catalog_rows(root, "content-catalog.jsonl", rows)
    return root


def test_activity_finishing_at_the_arrival_second_is_told_by_the_memory_layer(engine_factory,
                                                                             keep_as_long_as_the_drive):
    """Keep 恰在到达那一秒放完：第二条结果算在路上那一段，到达后的进度层没有它的记录；记忆层路上那一段的条目里有
    带标题的结束句。"""
    engine = engine_factory(keep_as_long_as_the_drive)
    _, (trip, keep) = act_group(engine, "depart", travel("公司", "驾车"), use_app("Keep"))
    assert (trip.status, keep.status) == ("in_progress", "in_progress"), (trip.error_code, keep.error_code)
    activities = engine.context.state.activities
    assert activities[trip.activity_id].planned_end_at == activities[keep.activity_id].planned_end_at
    assert advance_until_ready(engine)
    assert engine.context.state.agents[1].location_id == "office"
    assert engine.context.state.activities[keep.activity_id].status == "completed"
    observed = observe_only(engine, "arrived")
    assert not _keep(observed) and not _on_road(engine)
    name = engine.context.state.activities[keep.activity_id].name
    (road,) = observed["memory_layer"]["earlier_today"][-1:]
    (sentence,) = [line for line in road["settled"] if line.startswith(name)]
    assert sentence.endswith("，按计划结束，《测试慢跑》")


def _stored_observation(engine, observed: dict):
    """这次观察落库的观察事件。"""
    return next(event for event in events(engine, "observation")
                if event.event_id == observed["observation_event_id"])


def test_carried_record_sources_resolve_and_the_export_certifies(engine_factory, tmp_path):
    """路上观察事件的 progress_event_ids 含带入记录的请求与第一条结果，放完后再含第二条；跑到截止导出，认证通过，
    质量报告没有 invalid_observed_result_reference。"""
    engine, _, keep = _departed_with_keep(engine_factory, mutate=one_hour_every_five_minutes)
    activity = engine.context.state.activities[keep.activity_id]
    request, first = activity.origin_request_event_id, activity.data["accepted_event_id"]
    assert advance_until_ready(engine) and _on_road(engine)
    sources = _stored_observation(engine, observe_only(engine, "road")).data["progress_event_ids"]
    assert {request, first} <= set(sources)
    until_ended(engine, keep.activity_id)
    second = [row.event_id for row in events(engine, "action_result") if row.action_id == keep.action_id][-1]
    assert second != first
    sources = _stored_observation(engine, observe_only(engine, "finished")).data["progress_event_ids"]
    assert {request, first, second} <= set(sources)
    directory = tmp_path / "export"
    export_completed(engine, directory)
    quality = json.loads((directory / "quality.json").read_bytes())
    assert not [item for item in quality["memory_references"]["problems"]
                if item["code"] == "invalid_observed_result_reference"]


def test_carried_record_sorts_first(engine_factory):
    """路上的进度层里带入的记录排在首位：它的 occurred_at 是出发那一秒，不晚于这段路的 session_started_at，排在路上
    提交的播报记录之前。"""
    engine, trip, _ = _departed_with_keep(engine_factory)
    assert advance_until_ready(engine) and _on_road(engine)
    name = engine.context.state.activities[trip.activity_id].name
    _, reported = act(engine, "report", report_request(name), "report_status")
    assert reported.status == "completed", reported.error_code
    layer = observe_only(engine, "road")["progress_layer"]
    first, second = layer["events"][:2]
    assert first["tool"] == SIDE_TOOL and first["arguments"]["target"] == ["Keep"]
    assert first["occurred_at"] <= layer["session_started_at"] and second["tool"] == "report_status"
