"""多日运行里的睡眠与每日反思：睡过头的结果随检查点恢复、重跑可复现；含午睡的运行导出后没有记忆来源问题，
评估报告把就寝与小睡分开；从睡醒那一秒的检查点恢复，反思在恢复后的运行里只做一次。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.footprints.recording import artifact_rows
from tests.integration.reflection_fixtures import host_run, memory
from tests.integration.runtime_fixtures import write_agent_workspace
from tests.life.conftest import advance_next, oversleep_identity, sleep, stage_day_plan, travel
from tests.life.environment_runtime import act, finish_activity, run_to_end
from worldmock.evaluation.behavior_metrics import behavior_metrics_report
from worldmock.footprints.checkpoint import restore_branch, restore_directory, snapshot_directory
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from worldmock.life.engine import LifeEngine

_ZONE = ZoneInfo("Asia/Shanghai")


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    from worldmock.life import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


def _after_a_long_day(persona, config):
    """周一 07:00 开始、持续两天；人物惯常 08:00 起床，开始时已经醒了 23 小时。"""
    persona["sleep_need_hours"] = 8.0
    config["start_time"] = datetime.fromisoformat("2026-01-05T07:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-07T07:00:00+08:00")


def _evening(persona, config):
    """周一 20:00 开始、持续三天。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T20:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-08T20:00:00+08:00")


def _sleeps(engine) -> list[tuple]:
    """按入睡先后，每段睡眠的地点、写的时长、多睡的时长、起止与状态。"""
    rows = sorted((item for item in engine.context.state.activities.values() if item.kind == "sleep"),
                  key=lambda item: item.started_at)
    return [(item.location_id, item.data["declared_duration_seconds"], item.data["oversleep_seconds"],
             item.started_at, item.ended_at, item.status.value) for item in rows]


def _checkpoint(engine, root):
    """以占位的人物工作区提交一个共同检查点。"""
    workspace = write_agent_workspace(root / "agent")
    return engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})


def _short_night_office_nap_and_long_sleep(engine, checkpoint_root=None):
    """家里一觉写 220 分钟并睡过头，实际睡了 4 小时 07 分；驾车去公司后在办公室午睡 60 分钟；回家再睡 480 分钟。
    后两觉开始时往回 24 小时都已睡满 4 小时，按时醒。给了目录时在第一觉的中途提交一个共同检查点并返回它。"""
    _, late = act(engine, "late", sleep(220), action_id=oversleep_identity(engine, "late", 27 * 60))
    assert late.status == "in_progress", late.error_code
    for _ in range(3):
        advance_next(engine)
    assert engine.context.state.activities[late.activity_id].status == "active"
    saved = None if checkpoint_root is None else _checkpoint(engine, checkpoint_root)
    finish_activity(engine, late, "late")
    for label, place in (("to-office", "公司"), ("to-home", "家")):
        _, trip = act(engine, label, travel(place, "驾车"))
        finish_activity(engine, trip, label)
        _, rest = act(engine, f"sleep-{label}", sleep(60 if place == "公司" else 480))
        assert rest.status == "in_progress", rest.error_code
        finish_activity(engine, rest, f"sleep-{label}")
    return saved


def test_oversleep_survives_a_mid_sleep_checkpoint_and_a_rerun_draws_the_same_extra_time(engine_factory, tmp_path):
    """多日运行含家里的睡眠和办公室午睡：睡过头的那一觉从睡眠中途的检查点恢复后，醒来时刻与原运行相同、不重新抽取；
    用同一份环境重跑同一个脚本，每段睡眠的多睡时长与起止逐值相同。运行到截止后导出通过认证，评估报告的标识是
    behavior-metrics-4，每段睡眠带分类，办公室午睡记在小睡里。"""
    engine = engine_factory(mutate=_after_a_long_day)
    stage_day_plan(engine, submitted=True)
    start = engine.context.state.current_time
    saved = _short_night_office_nap_and_long_sleep(engine, tmp_path / "mid-sleep")
    first, nap, long = _sleeps(engine)
    assert first == ("home", 13200, 1620, start, start + 14820, "completed")
    assert nap[:3] == ("office", 3600, 0) and nap[4] - nap[3] == 3600
    assert long[:3] == ("home", 28800, 0) and long[4] - long[3] == 28800

    restore_branch(engine.context.store, saved.checkpoint_id, tmp_path / "branch").close()
    resumed = LifeEngine.open(engine.context.inputs, tmp_path / "branch")
    try:
        (asleep,) = [item for item in resumed.context.state.activities.values() if item.kind == "sleep"]
        assert asleep.status == "active" and asleep.data["oversleep_seconds"] == 1620
        assert asleep.planned_end_at == start + 14820
        while resumed.context.state.activities[asleep.activity_id].status == "active":
            assert resumed.scheduling_state().ready_agent_ids == ()
            advance_next(resumed)
        assert _sleeps(resumed) == [first]
        assert "session_open" in resumed.context.state.agents[1].pending_wake_reasons
    finally:
        resumed.close()

    again = engine_factory(mutate=_after_a_long_day)
    stage_day_plan(again, submitted=True)
    _short_night_office_nap_and_long_sleep(again)
    assert _sleeps(again) == [first, nap, long]

    recorded = run_to_end(engine)
    assert any(row.event_type == "run_ended" for row in recorded)
    assert not any(row.event_type == "run_failed" for row in recorded)
    _checkpoint(engine, tmp_path / "final")
    export_run(engine.context.store, engine.context.store.last_event_seq, tmp_path / "export")
    manifest, _ = validate_export(tmp_path / "export")
    events = artifact_rows(tmp_path / "export", "events")
    assert manifest["experiment_completed"] is True
    (report,) = behavior_metrics_report(events, timezone=engine.context.config.timezone).values()
    assert report["schema_version"] == "behavior-metrics-4"
    schedule = report["reports"]["sleep_schedule"]
    assert {row["category"] for row in schedule["sessions"]} <= {"bedtime", "nap", "unfinished"}
    by_start = {row["started_at"]: row["category"] for row in schedule["sessions"]}
    assert (by_start[first[3]], by_start[nap[3]], by_start[long[3]]) == ("bedtime", "nap", "bedtime")
    assert schedule["naps"]["count"] >= 1 and schedule["summary"]["session_count"] >= 2
    assert report["reports"]["wake_reason_distribution"]["counts"]["session_open"] >= 3


@pytest.mark.asyncio
async def test_run_with_naps_exports_clean_memory_sources_and_a_resume_at_wake_up_reflects_once(engine_factory,
                                                                                                tmp_path):
    """多日运行含午睡，人物每一步都走真实的 LifeAgent：从睡醒那一秒、第一次决策之前的检查点恢复，反思在恢复后的运行里
    只做一次，归属日期与原运行相同；原运行每个本地日至多一条反思登记，运行到截止后导出的质量报告没有记忆来源问题。"""
    engine = engine_factory(mutate=_evening)
    stage_day_plan(engine, submitted=True)
    workspace = tmp_path / "people" / "agent_0001"
    run = await host_run(engine, workspace)
    await run.rest_and_note("晚上在家歇了一会儿，心里踏实。")
    await run.sleep(480)
    woke = engine.context.state.current_time
    saved = engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})

    await run.conclude()
    await run.rest_and_note("早上又歇了一会儿。")
    await run.sleep(60)
    await run.conclude(memory("午前眯了一会儿，精神好些了。"))
    await run.sleep(600)
    await run.conclude()
    await run.sleep(600)
    await run.conclude()
    filed = [event.data["reflection"]["date"] for event in run.reflections()]
    assert filed == ["2026-01-05", "2026-01-06"]
    days = [datetime.fromtimestamp(event.occurred_at, _ZONE).date() for event in run.reflections()]
    assert len(set(days)) == 2 and run.reflections()[0].occurred_at == woke

    restore_branch(engine.context.store, saved.checkpoint_id, tmp_path / "branch").close()
    resumed = LifeEngine.open(engine.context.inputs, tmp_path / "branch")
    try:
        assert resumed.context.state.current_time == woke
        restored = tmp_path / "restored" / "agent_0001"
        restore_directory(saved.snapshots["agent:1"], resumed.context.store.assets, restored)
        branch = await host_run(resumed, restored, label="resumed", create=False)
        assert branch.reflections() == []
        await branch.conclude()
        await branch.start("interact", "家", "测试休息")
        await branch.conclude()
        (once,) = branch.reflections()
        assert once.occurred_at == woke and once.data["reflection"]["date"] == "2026-01-05"
        assert [kind for kind, _ in branch.model.requests].count("reflection") == 1
    finally:
        resumed.close()

    recorded = run_to_end(engine)
    assert any(row.event_type == "run_ended" for row in recorded)
    engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    export_run(engine.context.store, engine.context.store.last_event_seq, tmp_path / "export")
    manifest, _ = validate_export(tmp_path / "export")
    events = artifact_rows(tmp_path / "export", "events")
    assert manifest["experiment_completed"] is True
    quality = json.loads((tmp_path / "export" / "quality.json").read_text(encoding="utf-8"))
    memory_references = quality["memory_references"]
    assert memory_references["problems"] == [] and memory_references["problem_count"] == 0
    assert memory_references["write_points_checked"] >= 4
    exported = [row for row in events if row["event_type"] == "memory_updated" and row["data"]["reflection"]]
    assert [row["data"]["reflection"]["date"] for row in exported] == filed
    assert quality["decision_rhythm"]["wake_reason_counts"]["session_open"] >= 4
