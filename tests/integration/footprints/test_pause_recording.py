"""暂停区间不进入持续应用使用、内容秒数或本人公开经历。"""

import json
from datetime import timedelta

from tests.life.conftest import advance_next, conclude_request, do_at, pause_request, report_request, travel, use_app
from tests.life.environment_runtime import act, finish_activity, run_to_end
from worldmock.recording_engine.export.export import export_run
from worldmock.recording_engine.validation.validation import validate_export


def test_actual_pause_and_resume_export_only_running_intervals(engine_factory, tmp_path):
    def setup(persona, config):
        config["stop_time"] = config["start_time"] + timedelta(hours=2)
        config["progress_report_interval_seconds"] = 60
    engine = engine_factory(mutate=setup)
    _, started = act(engine, "media", use_app("哔哩哔哩"))
    assert started.status == "in_progress"
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    media = engine.context.state.activities[started.activity_id]
    paused_at = engine.context.state.current_time
    _, paused = act(engine, "pause", pause_request(media.name), "pause_activity")
    assert paused.status == "completed"
    _, away = act(engine, "away", travel("公司"))
    finish_activity(engine, away, "away")
    _, home = act(engine, "home", travel("家"))
    finish_activity(engine, home, "home")
    resumed_at = engine.context.state.current_time
    assert resumed_at > paused_at
    _, resumed = act(engine, "resume", report_request(media.name), "report_status")
    assert resumed.status == "completed", resumed.error_code
    run_to_end(engine)
    store = engine.context.store
    snapshot = store.assets.put_json({"saved": True}, {"kind": "snapshot"})
    point = engine.checkpoint({"agent:1": snapshot})
    directory = tmp_path / "export"
    export_run(store, point.last_event_seq, directory)
    _, activities = validate_export(directory)
    actual = next(row for row in activities if row["activity_id"] == started.activity_id)
    assert len(actual["segments"]) == 2
    assert actual["segments"][0]["ended_at"] == paused_at
    assert actual["segments"][1]["started_at"] == resumed_at
    uses = [json.loads(line) for line in (directory / "app-usage.jsonl").read_bytes().splitlines()]
    [use] = [row for row in uses if row["fact_kind"] == "app_use" and row["activity_id"] == started.activity_id]
    assert use["segments"] == actual["segments"]
    consumed = [row for row in uses if row["fact_kind"] == "content_consumed"]
    assert consumed and all(row["interval_end"] <= paused_at or row["interval_start"] >= resumed_at for row in consumed)


def test_paused_location_activity_concluded_later_elsewhere_exports_its_running_interval(engine_factory, tmp_path):
    """暂停的地点活动之后在别处用 conclude_activity 结束：运行照常到截止，导出通过认证，这项活动只有暂停前的
    那一段进行区间，暂停到结束之间的时间不算进活动。"""
    def setup(persona, config):
        config["stop_time"] = config["start_time"] + timedelta(hours=2)
        config["reconsideration_interval_seconds"] = 300
    engine = engine_factory(mutate=setup)
    started_at = engine.context.state.current_time
    _, started = act(engine, "rest", do_at("家", "测试休息"))
    assert started.status == "in_progress"
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    rest = engine.context.state.activities[started.activity_id]
    paused_at = engine.context.state.current_time
    _, paused = act(engine, "pause", pause_request(rest.name), "pause_activity")
    assert paused.status == "completed"
    _, away = act(engine, "away", travel("五里公园", "驾车"))
    finish_activity(engine, away, "away")
    _, ended = act(engine, "conclude", conclude_request(rest.name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    concluded_at = engine.context.state.current_time
    assert concluded_at > paused_at > started_at
    closed = engine.context.state.activities[started.activity_id]
    assert (closed.status, closed.ended_at, closed.elapsed_seconds) == ("cancelled", concluded_at, paused_at - started_at)
    run_to_end(engine)
    store = engine.context.store
    snapshot = store.assets.put_json({"saved": True}, {"kind": "snapshot"})
    point = engine.checkpoint({"agent:1": snapshot})
    directory = tmp_path / "export"
    export_run(store, point.last_event_seq, directory)
    _, activities = validate_export(directory)
    actual = next(row for row in activities if row["activity_id"] == started.activity_id)
    assert [(row["started_at"], row["ended_at"]) for row in actual["segments"]] == [(started_at, paused_at)]
