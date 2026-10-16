"""导出清单 7：截止时还在放的 app 使用，活动行带进度事件快照里的条数；终结之后的活动行带完整的放过列表。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tests.footprints.recording import artifact_rows, export_completed
from tests.life.conftest import advance_next, conclude_request, use_app
from tests.life.environment_inputs import five_episode_environment
from tests.life.environment_runtime import act, events, settle_decision
from worldmock.footprints.export import export_run

ZONE = ZoneInfo("Asia/Shanghai")


def _row(directory, activity_id: str) -> dict:
    """导出的 activities.jsonl 里这项活动的那一行。"""
    (row,) = [row for row in artifact_rows(directory, "activities") if row["activity_id"] == activity_id]
    return row


def test_app_use_running_at_the_cutoff_exports_its_played_count(engine_factory, tmp_path):
    """五集副本 08:00 打开哔哩哔哩，一集接一集，08:26:40 第五集开始。08:30 的进度播报唤醒之前截止导出：这项活动的行
    照抄 08:30 进度事件的快照，状态进行中，只有条数 4、没有放过列表。收尾、跑到截止后再导出并认证：这项活动的行带
    完整的放过列表（第一至第四集，按放的次序），没有条数。"""
    engine = engine_factory(five_episode_environment(tmp_path / "five"))
    _, opened = act(engine, "watch", use_app("哔哩哔哩"))
    assert opened.status == "in_progress", opened.error_code
    report_at = int(datetime(2026, 1, 5, 8, 30, tzinfo=ZONE).timestamp())
    while not (engine.context.state.current_time == report_at and engine.scheduling_state().ready_agent_ids):
        assert engine.context.state.current_time < report_at
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"report-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    carrying = [row for row in events(engine) if row.data.get("activity", {}).get("activity_id") == opened.activity_id]
    assert (carrying[-1].event_type, carrying[-1].occurred_at) == ("activity_progress", report_at)
    store = engine.context.store
    manifest = export_run(store, store.last_event_seq, tmp_path / "running")
    assert manifest["schema_version"] == 8 and manifest["experiment_completed"] is False
    running = _row(tmp_path / "running", opened.activity_id)
    played = engine.context.state.activities[opened.activity_id].data["played"]
    assert running["status"] == "active" and "played" not in running["data"]
    assert running["data"]["played_count"] == len(played) == 4

    name = engine.context.state.activities[opened.activity_id].name
    _, concluded = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    export_completed(engine, tmp_path / "complete")
    ended = _row(tmp_path / "complete", opened.activity_id)
    final = engine.context.state.activities[opened.activity_id].data["played"]
    assert "played_count" not in ended["data"] and ended["data"]["played"] == final
    assert [item["content_id"] for item in final] == ["c-drama-1", "c-drama-2", "c-drama-3", "c-drama-4"]
