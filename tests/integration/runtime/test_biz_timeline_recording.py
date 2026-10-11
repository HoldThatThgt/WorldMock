"""时间线只投影真实内容活動的进度，标签使用人物可见名称。"""
from tests.life.conftest import stage_day_plan, use_app
from tests.life.environment_runtime import act, finish_activity, events
from worldmock.footprints.timeline_labels import timeline_label_report

def test_real_content_progress_renders_readable_timeline(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    _, started = act(engine, "watch", use_app("抖音"))
    finish_activity(engine, started, "watched")
    report = timeline_label_report(events(engine), engine.context.store.manifest)
    assert report["rows_checked"] > 0
    assert report["english_kind_names"] == report["internal_ids"] == 0
