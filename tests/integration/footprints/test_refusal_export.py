"""活动进行中被拒绝过一次请求的运行，照常导出并通过认证。"""

from datetime import timedelta
import json

from tests.life.conftest import advance_until_ready, do_at, travel, use_app
from tests.life.environment_runtime import act, events, run_to_end
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export


def one_hour_with_frequent_reports(persona, config):
    """一小时的运行；每分钟给一次播报机会，让人物在活动进行中就能再提交请求。"""
    config["stop_time"] = config["start_time"] + timedelta(hours=1)
    config["progress_report_interval_seconds"] = 60


def test_run_with_a_refusal_during_an_activity_exports_and_certifies(engine_factory, tmp_path):
    engine = engine_factory(mutate=one_hour_with_frequent_reports)
    _, resting = act(engine, "rest-at-home", do_at("家", "测试休息"))
    assert resting.status == "in_progress", resting.error_code
    assert advance_until_ready(engine)
    running = engine.context.state.activities[resting.activity_id]
    assert running.status == "active" and 0 < running.elapsed_seconds < running.planned_duration_seconds
    # 槽位 1 正被「测试休息」占着，这时出发被拒绝；世界不变，拒绝事件带着进行中那项活动的快照。
    _, refused = act(engine, "leave-while-resting", travel("公司"))
    assert (refused.status, refused.error_code) == ("rejected", "RESOURCE_CONFLICT")
    unchanged = engine.context.state.activities[resting.activity_id]
    assert (unchanged.status, unchanged.elapsed_seconds, unchanged.planned_end_at) == (
        "active", running.elapsed_seconds, running.planned_end_at)
    assert [item.kind for item in engine.context.state.activities.values()] == ["interact"]
    (rejection,) = events(engine, "action_rejected")
    assert rejection.activity_id == resting.activity_id
    assert rejection.data["activity"]["activity_id"] == resting.activity_id
    assert (rejection.source_kind, rejection.visibility) == ("environment", "agent")
    run_to_end(engine)
    snapshot = engine.context.store.assets.put_json({"completed": True}, {"kind": "test_agent_snapshot"})
    point = engine.checkpoint({"agent:1": snapshot})
    export_run(engine.context.store, point.last_event_seq, tmp_path / "export")
    manifest, exported, activities = validate_export(tmp_path / "export")
    assert manifest["experiment_completed"] is True
    assert [row["event_id"] for row in exported if row["event_type"] == "action_rejected"] == [rejection.event_id]
    # 被拒绝的出发没有开始任何活动；进行中的那项活动照旧做完，时长不因这次拒绝多算或少算。
    assert [row["activity_type"] for row in activities if row["activity_type"] == "travel"] == []
    rested = next(row for row in activities if row["activity_id"] == resting.activity_id)
    assert rested["status"] == "completed" and rested["elapsed_seconds"] == running.planned_duration_seconds
    assert rejection.event_id in rested["source_event_ids"]
    usage = [json.loads(line) for line in (tmp_path / "export/app-usage.jsonl").read_bytes().splitlines()]
    assert all(row["activity_id"] != resting.activity_id for row in usage)


def test_refusal_while_an_app_is_in_use_leaves_its_app_use_row_to_the_real_settlements(engine_factory, tmp_path):
    engine = engine_factory(mutate=one_hour_with_frequent_reports)
    _, watching = act(engine, "watch", use_app("哔哩哔哩"))
    assert watching.status == "in_progress", watching.error_code
    assert advance_until_ready(engine)
    # 槽位 2 正被哔哩哔哩占着，这时再打开抖音被拒绝；拒绝事件带着哔哩哔哩那项活动的快照。
    _, refused = act(engine, "second-app", use_app("抖音"))
    assert (refused.status, refused.error_code) == ("rejected", "RESOURCE_CONFLICT")
    (rejection,) = events(engine, "action_rejected")
    assert rejection.data["activity"]["activity_id"] == watching.activity_id
    assert rejection.data["activity"]["data"]["app_id"] == "bilibili"
    run_to_end(engine)
    snapshot = engine.context.store.assets.put_json({"completed": True}, {"kind": "test_agent_snapshot"})
    point = engine.checkpoint({"agent:1": snapshot})
    export_run(engine.context.store, point.last_event_seq, tmp_path / "export")
    _, exported, activities = validate_export(tmp_path / "export")
    usage = [json.loads(line) for line in (tmp_path / "export/app-usage.jsonl").read_bytes().splitlines()]
    (used,) = [row for row in usage if row["fact_kind"] == "app_use"]
    watched = next(row for row in activities if row["activity_id"] == watching.activity_id)
    assert (used["activity_id"], used["app_id"]) == (watching.activity_id, "bilibili")
    # 使用区间只来自真实的正秒结算，拒绝既不加区间，也不是这一行最后的来源。
    assert used["segments"] == watched["segments"] and used["status"] == watched["status"]
    assert sum(row["ended_at"] - row["started_at"] for row in used["segments"]) == watched["elapsed_seconds"]
    progressed = [row["event_id"] for row in exported if row["activity_id"] == watching.activity_id
                  and row["event_type"] in {"activity_progress", "action_result"}]
    assert used["source_event_id"] in progressed and used["source_event_id"] != rejection.event_id
    assert not any(row["app_id"] == "douyin" for row in usage if row["fact_kind"] == "app_use")
