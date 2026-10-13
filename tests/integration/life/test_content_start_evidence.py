"""真实开始结果与完整导出只使用当前内容、浏览和活动快照合同。"""

import json
from datetime import timedelta

import pytest

from tests.life.conftest import (
    advance_until_ready, conclude_request, context, do_at, pause_request, report_request, target_actions, travel,
    use_app,
)
from tests.life.current_world import stage_moment
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, act_group, events, finish_activity, run_to_end
from tests.life.prerequisite_inputs import (
    CLOCK_PREREQUISITE, OFFICE, app_content, dingtalk_environment, example_window, moment, weekday_hours,
)
from worldmock.errors import IntegrityError
from worldmock.footprints.app_usage import activity_snapshots
from worldmock.footprints.export import export_run, write_jsonl
from worldmock.footprints.validation import validate_export
from worldmock.life.readable import duration_phrase, iso_time, money_phrase
from worldmock.life.purchase_offers import quote_goods
from worldmock.life.result_projection import public_result


def short_window(persona, config):
    config["stop_time"] = config["start_time"] + timedelta(hours=1)


def snapshots(records):
    for record in records:
        data = record["data"]
        yield from activity_snapshots(data)
        nested = data.get("data", {}).get("activity")
        if isinstance(nested, dict):
            yield nested


def test_start_remaining_time_and_same_source_resume_keep_actual_full_content_duration(engine_factory):
    def frequent_feedback(persona, config):
        config["activity_feedback_seconds"] = 60
        config["progress_report_interval_seconds"] = 60
    engine = engine_factory(mutate=frequent_feedback)
    _, result = act(engine, "first-content", use_app("哔哩哔哩"))
    assert result.status == "in_progress"
    activity = engine.context.state.activities[result.activity_id]
    content = engine.context.catalog.contents[activity.data["content_id"]]
    assert public_result(result.model_dump(mode="json"), engine.context.inputs)["details"]["content"]["duration"] == (
        duration_phrase(content.duration_seconds))
    assert advance_until_ready(engine)
    position = engine.context.state.agents[1].content_progress[content.content_id].position_seconds
    assert 0 < position < content.duration_seconds
    _, paused = act(engine, "pause-content", pause_request(activity.name), "pause_activity")
    assert paused.status == "completed"
    _, resumed = act(engine, "resume-content", report_request(activity.name), "report_status")
    assert resumed.status == "completed"
    assert public_result(resumed.model_dump(mode="json"), engine.context.inputs)["details"]["planned"] == [
        {"name": activity.name, "duration": duration_phrase(content.duration_seconds - position)}]
    restored = engine.context.state.activities[result.activity_id]
    assert restored.data["content_id"] == content.content_id and restored.media_position_seconds == position
    assert advance_until_ready(engine)
    _, ended = act(engine, "end-original", conclude_request(activity.name), "conclude_activity")
    assert ended.status == "completed"
    partial = engine.context.state.agents[1].content_progress[content.content_id].position_seconds
    _, restarted = act(engine, "new-same-source", use_app("哔哩哔哩"))
    assert restarted.status == "in_progress"
    next_activity = engine.context.state.activities[restarted.activity_id]
    assert next_activity.activity_id != activity.activity_id and next_activity.data["content_id"] == content.content_id
    details = public_result(restarted.model_dump(mode="json"), engine.context.inputs)["details"]
    assert details["planned"][0]["duration"] == duration_phrase(content.duration_seconds - partial)
    assert details["content"]["duration"] == duration_phrase(content.duration_seconds)


def test_content_platform_returns_same_origin_goods_quotes_and_unavailable_closed_goods(engine_factory, tmp_path):
    root = copy_environment(tmp_path / "input")
    places = catalog_rows(root, "location.jsonl")
    for row in places:
        if row["kind"] == "restaurant":
            row["app_ids"] = sorted({*row["app_ids"], "bilibili"})
            row["opens"] = {"weekly": {}, "exceptions": {}}
    write_catalog_rows(root, "location.jsonl", places)
    goods = catalog_rows(root, "merchant-catalog.jsonl")
    goods.append({"goods_id": "streamer-book", "name": "视频学习书", "category": "goods",
                  "price_fen": 1200, "app_ids": ["bilibili"]})
    write_catalog_rows(root, "merchant-catalog.jsonl", sorted(goods, key=lambda row: row["goods_id"]))
    engine = engine_factory(root)
    _, result = act(engine, "content-with-goods", use_app("哔哩哔哩"))
    assert result.status == "in_progress"
    details = public_result(result.model_dump(mode="json"), engine.context.inputs)["details"]
    assert "content" in details
    rows = details["goods"]
    assert len({row["name"] for row in rows}) == len(rows)
    supplied = next(row for row in rows if row["name"] == "视频学习书")
    offer = quote_goods(engine.context.inputs, engine.context.state, 1, "streamer-book", None)
    assert supplied == {"name": "视频学习书", "price": "12 元", "delivery_fee": money_phrase(offer.delivery_fee_fen),
                       "expected_at": iso_time(offer.expected_at, engine.context.state.timezone),
                       "available": True, "reason": None}
    closed = next(row for row in rows if row["name"] == "牛肉面")
    assert closed["available"] is False and "while it is open" in closed["reason"]
    assert closed["delivery_fee"] is None and closed["expected_at"] is None


def test_parallel_travel_platform_catalog_uses_actual_departed_presence(engine_factory):
    engine = engine_factory()
    # 出发与浏览同组开始：出行那条动作的结果不带目录，浏览那条动作的结果按出发后的在场写商品可用性
    _, (departure, result) = act_group(engine, "depart-and-browse", travel("公司"), use_app("美团"))
    assert departure.status == result.status == "in_progress", (departure.error_code, result.error_code)
    assert "goods" not in departure.data
    details = public_result(result.model_dump(mode="json"), engine.context.inputs)["details"]
    tea = next(row for row in details["goods"] if row["name"] == "柠檬茶")
    assert tea["available"] is False
    assert tea["delivery_fee"] is None and tea["expected_at"] is None
    assert "after arriving" in tea["reason"]


@pytest.mark.parametrize("target", ["哔哩哔哩", "家", "美团"])
def test_current_started_activity_public_result_and_complete_export(engine_factory, tmp_path, target):
    engine = engine_factory(mutate=short_window)
    # 在家做的事由人物点名内容单元，内容 app 的内容由环境选择
    request = do_at("家", "测试休息") if target == "家" else use_app(target)
    observed, result = act(engine, "current-start", request)
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    inputs, data = engine.context.inputs, activity.data
    # 在地点做事占槽位 1、内容就是点名的单元并写进活动名；用内容 app 占槽位 2
    if target == "家":
        assert (data["content_id"], data["app_id"], activity.resource_claims) == ("c-rest", None, {"first"})
        assert activity.name.startswith("进行测试休息（")
    else:
        assert data["app_id"] is not None and activity.resource_claims == {"second"}
    details = public_result(result.model_dump(mode="json"), inputs)["details"]
    assert details["planned"] == [{"name": activity.name,
                                   "duration": duration_phrase(activity.planned_duration_seconds)}]
    content_id = data["content_id"]
    if content_id is None:
        assert "content" not in details
        assert details["app"] == "美团" and details["duration"] == duration_phrase(600)
        catalog = inputs.catalog
        identities = set(catalog.goods_by_app[data["app_id"]])
        identities.update(key for store in catalog.stores_by_app[data["app_id"]]
                          for key in catalog.goods_by_store[store])
        quotes = {key: quote_goods(inputs, engine.context.state, 1, key, None) for key in identities}
        assert details["goods"] == [{"name": catalog.goods[key].name,
            "price": money_phrase(catalog.goods[key].price_fen),
            "delivery_fee": money_phrase(quotes[key].delivery_fee_fen),
            "expected_at": iso_time(quotes[key].expected_at, inputs.config.timezone),
            "available": True, "reason": None} for key in sorted(identities)]
        assert details["goods"]
    else:
        content = inputs.catalog.contents[content_id]
        assert details["content"] == {"app": target if data["app_id"] else None,
            "title": content.title, "summary": content.summary,
            "duration": duration_phrase(content.duration_seconds), "pushed": False}
    assert "content" not in public_result({"occurred_at": result.occurred_at, "status": "completed",
        "message": "Completed", "data": result.data}, inputs)["details"]
    controls = engine.observe(context(engine, "control-observation"))["activity_bindings"]
    assert controls and all(row["title"] is None for row in controls)
    run_to_end(engine)
    # 真实做完时写下的第二条结果：有内容的活动带 contents，第一行是开始的那条内容；没有内容的不带
    (terminal,) = [event for event in events(engine, "action_result")
                   if event.action_id == result.action_id and event.data["status"] != "in_progress"]
    assert (terminal.data["status"], terminal.data["message"]) == ("completed", "Activity completed")
    finished = public_result(terminal.data, inputs)["details"]
    if content_id is None:
        assert "contents" not in finished
    else:
        started = inputs.catalog.contents[content_id]
        assert finished["contents"][0] == {"title": started.title, "summary": started.summary,
                                           "duration": duration_phrase(started.duration_seconds), "finished": True}
        assert all(list(row) == ["title", "summary", "duration", "finished"] for row in finished["contents"])
    rows = [event.model_dump(mode="json") for event in events(engine)]
    actual = list(snapshots(rows))
    assert actual and all("title" not in row for row in actual)
    assert {event["event_type"] for event in rows if list(snapshots([event]))} >= {
        "action_accepted", "action_result", "activity_progress"}
    assert all(("content_title" not in row if row["data"].get("content_id") is None else
                row["content_title"] == inputs.catalog.contents[row["data"]["content_id"]].title)
               for row in actual)
    asset = engine.context.store.assets.put_json({"completed": True}, {"kind": "test_agent_snapshot"})
    point = engine.checkpoint({"agent:1": asset})
    directory = tmp_path / "export"
    export_run(engine.context.store, point.last_event_seq, directory)
    _, exported, activities = validate_export(directory)
    assert all("title" not in row for row in [*snapshots(exported), *activities])
    for row in activities:
        if row["data"].get("content_id") is not None:
            assert row["content_title"] == inputs.catalog.contents[row["data"]["content_id"]].title


@pytest.mark.parametrize("shape", ["accepted", "plural", "terminal_nested", "progress"])
def test_complete_export_rejects_retired_activity_title_in_every_real_snapshot(engine_factory, tmp_path, shape):
    engine = engine_factory(mutate=short_window)
    _, result = act(engine, "actual-content", do_at("家", "测试休息"))
    assert result.status == "in_progress"
    run_to_end(engine)
    asset = engine.context.store.assets.put_json({"completed": True}, {"kind": "test_agent_snapshot"})
    point = engine.checkpoint({"agent:1": asset})
    directory = tmp_path / "export"
    manifest = export_run(engine.context.store, point.last_event_seq, directory)
    rows = [json.loads(line) for line in (directory / "events.jsonl").read_bytes().splitlines()]
    if shape == "accepted":
        record = next(row for row in rows if row["event_type"] == "action_accepted" and "activity" in row["data"])
        snapshot = record["data"]["activity"]
    elif shape == "plural":
        record = next(row for row in rows if row["data"].get("data", {}).get("activities"))
        snapshot = record["data"]["data"]["activities"][0]
    elif shape == "terminal_nested":
        record = next(row for row in rows if "activity" in row["data"].get("data", {}))
        snapshot = record["data"]["data"]["activity"]
    else:
        record = next(row for row in rows if row["event_type"] == "activity_progress")
        snapshot = record["data"]["activity"]
    snapshot["title"] = "Retired generated label"
    manifest["artifacts"]["events"] = write_jsonl(directory / "events.jsonl", rows)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IntegrityError, match="retired title field"):
        validate_export(directory)


GO_TO_OFFICE = f"Go to {OFFICE}."
AT_OFFICE = f"Nothing in 钉钉 can start here now: it requires presence at {OFFICE}"
HOURS = "08:00–10:00, 17:00–20:00"
#: 规格完整例子里被拒的各行：本地时刻、停留地点、原因、下一步、未满足的前提成员。
CLOCK_REFUSALS = [
    ((12, 8, 10), "home", f"{AT_OFFICE}.", GO_TO_OFFICE, ["content_location"]),
    ((12, 10, 0), "office", f"Nothing in 钉钉 can start now: it is available {HOURS} today.",
     "Choose another action.", ["content_time"]),
    ((12, 12, 30), "office", f"Nothing in 钉钉 can start now: it is available {HOURS} today.",
     "Choose another action.", ["content_time"]),
    ((12, 12, 30), "home", f"{AT_OFFICE} during {HOURS} today.", GO_TO_OFFICE, ["content_location", "content_time"]),
    ((17, 9, 0), "office", "Nothing in 钉钉 can start now: it is not available today.",
     "Choose another action.", ["content_time"]),
    ((17, 9, 0), "home", f"{AT_OFFICE} and is not available today.", GO_TO_OFFICE,
     ["content_location", "content_time"]),
]


def dingtalk_engine(engine_factory, tmp_path, *contents, **window):
    """钉钉例子的真实引擎：夹具副本加钉钉与给定内容，运行从 10 月 12 日（周一）08:00 开始。"""
    return engine_factory(dingtalk_environment(tmp_path / "input", *contents), mutate=example_window(**window))


def dingtalk_row(observed):
    return target_actions(observed, "钉钉")["interact"]


def app_row(reason=None, duration=None, note=None):
    """工具层内容 app 一行的八个字段。"""
    return {"action": "interact", "detail": None, "available": reason is None, "reason": reason, "price": None,
            "duration": duration, "quantity": None, "note": note}


@pytest.mark.parametrize("when,location,reason,next_step,unmet", CLOCK_REFUSALS,
                         ids=["home-0810", "office-1000", "office-1230", "home-1230", "saturday-office",
                              "saturday-home"])
def test_clock_in_refusal_repeats_the_tool_layer_reason_and_changes_nothing(
        engine_factory, tmp_path, when, location, reason, next_step, unmet):
    """登记了内容的 app 此刻没有合格内容：工具层这一行的 reason 与提交后的拒绝一字不差；拒绝不改世界、内容进度、
    推送状态和随机状态，不产生活动，也不退成没有 content_id 的目录浏览，同刻轮数加一。"""
    engine = dingtalk_engine(engine_factory, tmp_path, start_day=when[0])
    stage_moment(engine, moment(*when), location)
    before = engine.context.state.model_copy(deep=True)
    observed, result = act(engine, "clock", use_app("钉钉"))
    assert dingtalk_row(observed) == app_row(reason)
    assert result.status == "rejected" and result.error_code == "CONTENT_PREREQUISITE_UNMET"
    assert result.message == result.data["reason"] == reason
    assert (result.data["next_step"], result.data["fix"], result.data["unmet"]) == (next_step, None, unmet)
    assert result.data["items"] == [{
        "path": ["tool_calls", 0, "function", "arguments", "target"], "written": ["钉钉"],
        "error_code": "CONTENT_PREREQUISITE_UNMET", "allowed_values": [], "expected": reason, "why": reason,
        "candidates": []}]
    after = engine.context.state
    assert after.activities == before.activities == {} and after.deliveries == before.deliveries
    assert after.agents[1].content_progress == before.agents[1].content_progress == {}
    assert after.sessions == before.sessions and after.random_state == before.random_state
    assert after.accounts == before.accounts and after.current_time == before.current_time
    assert (result.data["same_moment_round"], after.agents[1].same_moment_rounds) == (1, 1)
    assert before.agents[1].same_moment_rounds == 0
    assert not events(engine, "action_accepted") and not events(engine, "activity_progress")


@pytest.mark.parametrize("when,ended", [((12, 8, 55, 0), (12, 8, 56, 0)), ((12, 9, 59, 59), (12, 10, 0, 59)),
                                        ((12, 17, 35, 0), (12, 17, 36, 0))],
                         ids=["morning", "last-second-of-the-window", "evening"])
def test_clock_in_at_the_office_starts_the_content_and_finishes_past_the_window_end(
        engine_factory, tmp_path, when, ended):
    """在办公室、在时段内：钉钉可用，引擎挑中打卡并做完，完成轮数加一；在时段末开始的内容跨过时段结束仍做完。"""
    engine = dingtalk_engine(engine_factory, tmp_path)
    stage_moment(engine, moment(*when), "office")
    observed, result = act(engine, "clock", use_app("钉钉"))
    assert dingtalk_row(observed) == app_row(duration="约 1 分钟")
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert (activity.data["app_id"], activity.data["content_id"]) == ("dingtalk", "dingtalk-clock")
    assert activity.data["pinned_by_push_id"] is None and activity.resource_claims == {"second"}
    assert (activity.started_at, activity.planned_end_at) == (moment(*when), moment(*ended))
    finish_activity(engine, result, "clock")
    final = engine.context.state.activities[result.activity_id]
    assert final.status == "completed" and final.ended_at == moment(*ended)
    progress = engine.context.state.agents[1].content_progress["dingtalk-clock"]
    assert (progress.completed_rounds, progress.position_seconds, progress.last_consumed_at) == (1, 0.0, moment(*ended))
    consumed = [row.data["content"] for row in events(engine, "activity_progress")
                if row.activity_id == result.activity_id and row.data.get("content")]
    assert consumed and consumed[-1]["completed"] is True and consumed[-1]["position_after_seconds"] == 60


def test_second_clock_in_of_the_day_adds_another_completed_round(engine_factory, tmp_path):
    """上班卡与下班卡是同一条内容的两轮：引擎不区分，完成轮数从 1 到 2。"""
    engine = dingtalk_engine(engine_factory, tmp_path)
    for label, when in (("morning", (12, 8, 55)), ("evening", (12, 17, 35))):
        stage_moment(engine, moment(*when), "office")
        _, result = act(engine, label, use_app("钉钉"))
        assert result.status == "in_progress", result.error_code
        finish_activity(engine, result, label)
    assert engine.context.state.agents[1].content_progress["dingtalk-clock"].completed_rounds == 2


REPORT = app_content("dingtalk-report", "周报填写", CLOCK_PREREQUISITE, duration_seconds=1800)
LUNCH = app_content("dingtalk-lunch", "午间打卡", {"opens": weekday_hours(("12:00", "13:00"))}, duration_seconds=120)


def test_paused_prerequisite_content_resumes_outside_its_window(engine_factory, tmp_path):
    """前提只卡开始：暂停后在时段外恢复被受理，接着做同一条内容，不重新挑。"""
    engine = dingtalk_engine(engine_factory, tmp_path, REPORT, progress_report_interval_seconds=60)
    stage_moment(engine, moment(12, 9, 50), "office")
    _, started = act(engine, "report", use_app("钉钉"))
    assert started.status == "in_progress", started.error_code
    assert advance_until_ready(engine)
    name = engine.context.state.activities[started.activity_id].name
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed", paused.error_code
    position = engine.context.state.agents[1].content_progress["dingtalk-report"].position_seconds
    assert 0 < position < 1800
    stage_moment(engine, moment(12, 10, 30), "office")
    observed, resumed = act(engine, "resume", report_request(name), "report_status")
    # 此刻新开一次会被拒绝，恢复不判定前提
    assert dingtalk_row(observed)["available"] is False
    assert resumed.status == "completed", resumed.error_code
    current = engine.context.state.activities[started.activity_id]
    assert current.status == "active" and current.data["content_id"] == "dingtalk-report"
    assert current.media_position_seconds == position
    assert current.planned_end_at == moment(12, 10, 30) + 1800 - position


def test_unfinished_content_waits_for_its_prerequisite_and_then_continues_from_its_position(engine_factory, tmp_path):
    """提前结束留下进度的内容：不合格时不被挑中，也不挡住同一个 app 的其他内容；合格后从原位置接着做。"""
    engine = dingtalk_engine(engine_factory, tmp_path, REPORT, LUNCH, progress_report_interval_seconds=60)
    stage_moment(engine, moment(12, 8, 55), "office")
    _, first = act(engine, "report", use_app("钉钉"))
    assert engine.context.state.activities[first.activity_id].data["content_id"] == "dingtalk-report"
    assert advance_until_ready(engine)
    name = engine.context.state.activities[first.activity_id].name
    _, ended = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    position = engine.context.state.agents[1].content_progress["dingtalk-report"].position_seconds
    assert 0 < position < 1800
    stage_moment(engine, moment(12, 12, 30), "office")
    observed, noon = act(engine, "noon", use_app("钉钉"))
    assert dingtalk_row(observed) == app_row(duration="约 2 分钟")
    assert engine.context.state.activities[noon.activity_id].data["content_id"] == "dingtalk-lunch"
    finish_activity(engine, noon, "noon")
    assert engine.context.state.agents[1].content_progress["dingtalk-report"].position_seconds == position
    stage_moment(engine, moment(12, 17, 35), "office")
    observed, again = act(engine, "evening", use_app("钉钉"))
    assert dingtalk_row(observed) == app_row(duration=duration_phrase(1800 - position))
    resumed = engine.context.state.activities[again.activity_id]
    assert resumed.activity_id != first.activity_id and resumed.data["content_id"] == "dingtalk-report"
    assert resumed.data["start_position_seconds"] == position
    assert resumed.planned_duration_seconds == 1800 - position
