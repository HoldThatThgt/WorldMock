"""真实开始结果与完整导出只使用当前内容、浏览和活动快照合同。"""

import json
from datetime import timedelta

import pytest

from tests.life.conftest import (
    advance_until_ready, conclude_request, context, do_at, pause_request, report_request, travel, use_app,
)
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, act_group, events, run_to_end
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
    offer = quote_goods(engine.context.inputs, engine.context.state, 1, "streamer-book")
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
        quotes = {key: quote_goods(inputs, engine.context.state, 1, key) for key in identities}
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
