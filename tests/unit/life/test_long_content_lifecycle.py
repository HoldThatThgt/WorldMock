"""真实长内容受理、跨日推进与暂停恢复不得添加一天上限。"""

from datetime import timedelta
import json
import shutil

from tests.data_paths import ENVIRONMENT_ROOT
from tests.life.conftest import pause_request, report_request, sleep, stage_day_plan, use_app
from tests.life.environment_runtime import act, events, finish_activity, reach
from worldmock.resource_engine.catalog.identity import refresh_manifest_identity


def long_content_engine(engine_factory, tmp_path, duration):
    """通过真实发布目录登记唯一应用内容，再创建足够长的实际运行窗口。"""
    root = tmp_path / "long-content"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    path = root / "catalog/content-catalog.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["content_id"] == "c-run":
            row.update(duration_seconds=duration, exertion="light")
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8", newline="\n")
    refresh_manifest_identity(root / "catalog")

    def mutate(persona, config):
        config["stop_time"] = config["start_time"] + timedelta(days=4)
        config.update(activity_feedback_seconds=1800, progress_report_interval_seconds=1800)

    engine = engine_factory(environment_root=root, mutate=mutate)
    stage_day_plan(engine)
    return engine


def test_long_content_stays_active_after_one_day_and_completes_only_at_its_real_end(engine_factory, tmp_path):
    """跨过旧一天边界仍保留真实进度，只有目录声明的完整时长才产生完成事实。"""
    duration = 90000
    engine = long_content_engine(engine_factory, tmp_path, duration)
    started_at = engine.context.state.current_time
    _, started = act(engine, "long-content", use_app(engine.context.catalog.apps["keep"].name))
    assert started.status == "in_progress"
    activity_id = started.activity_id
    assert engine.context.state.activities[activity_id].planned_duration_seconds == duration
    reach(engine, started_at + 86400)
    activity = engine.context.state.activities[activity_id]
    assert activity.status == "active"
    assert activity.elapsed_seconds == activity.media_position_seconds == 86400
    progress = engine.context.state.agents[1].content_progress["c-run"]
    assert progress.position_seconds == 86400 and progress.completed_rounds == 0
    reach(engine, started_at + duration)
    activity = engine.context.state.activities[activity_id]
    assert activity.status == "completed" and activity.ended_at == started_at + duration
    assert activity.elapsed_seconds == activity.media_position_seconds == duration
    progress = engine.context.state.agents[1].content_progress["c-run"]
    assert progress.position_seconds == 0 and progress.completed_rounds == 1
    completed = [event.data["content"] for event in events(engine, "activity_progress")
                 if event.activity_id == activity_id and event.data.get("content", {}).get("completed")]
    assert len(completed) == 1 and completed[0]["position_after_seconds"] == duration


def test_pause_and_resume_keep_the_full_long_content_remainder_without_counting_sleep(engine_factory, tmp_path):
    """暂停后的实际睡眠不消费内容，恢复仍采用超过一天的真实剩余时长。"""
    duration = 172861
    engine = long_content_engine(engine_factory, tmp_path, duration)
    started_at = engine.context.state.current_time
    _, started = act(engine, "long-resume", use_app(engine.context.catalog.apps["keep"].name))
    assert started.status == "in_progress"
    reach(engine, started_at + 1800)
    activity = engine.context.state.activities[started.activity_id]
    _, paused = act(engine, "pause-long", pause_request(activity.name), "pause_activity")
    assert paused.status == "completed"
    _, sleeping = act(engine, "sleep-while-paused", sleep())
    assert sleeping.status == "in_progress"
    finish_activity(engine, sleeping, "sleep-finished")
    paused_activity = engine.context.state.activities[started.activity_id]
    assert paused_activity.status == "paused"
    assert paused_activity.elapsed_seconds == paused_activity.media_position_seconds == 1800
    resumed_at = engine.context.state.current_time
    assert resumed_at > started_at + 1800
    _, resumed = act(engine, "resume-long", report_request(activity.name), "report_status")
    assert resumed.status == "completed"
    current = engine.context.state.activities[started.activity_id]
    assert current.status == "active" and current.planned_duration_seconds == duration
    assert current.planned_end_at == resumed_at + duration - 1800
    assert current.data["start_position_seconds"] == 1800
    reach(engine, resumed_at + 1800)
    current = engine.context.state.activities[started.activity_id]
    assert current.elapsed_seconds == current.media_position_seconds == 3600
    assert engine.context.state.agents[1].content_progress["c-run"].position_seconds == 3600
