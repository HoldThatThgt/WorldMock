"""时间线只投影真实内容活動的进度，标签使用人物可见名称。"""
from tests.life.conftest import stage_day_plan, use_app
from tests.life.environment_runtime import act, conclude_app_use, events
from worldmock.recording_engine.validation.frozen_environment import frozen_display_names
from worldmock.recording_engine.projections.presentation.timeline_labels import timeline_label_report

def test_real_content_progress_renders_readable_timeline(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    _, started = act(engine, "watch", use_app("抖音"))
    conclude_app_use(engine, started.activity_id, "watched")
    store = engine.context.store
    report = timeline_label_report(events(engine), store.manifest, frozen_display_names(store.run_dir, store.manifest))
    assert report["rows_checked"] > 0
    assert report["english_kind_names"] == report["internal_ids"] == 0
