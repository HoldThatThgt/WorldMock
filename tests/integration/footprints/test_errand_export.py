"""@brief 当前收货证据、提醒与跨夜事务经真实引擎完成，再按同批导出事实重算质量。"""

import json
from datetime import datetime

from tests.life.conftest import conclude_request, errand_add, errand_cancel, errand_update, report_request
from tests.integration.life.test_acs_amended_runtime import perform, pump
from tests.integration.life.test_acs_runtime import request
from worldmock.evaluation.errand_metrics import conclusion_memories, errand_metrics
from worldmock.footprints.export import export_run
from worldmock.footprints.models import Event
from worldmock.footprints.quality import build_quality_report
from worldmock.footprints.validation import validate_export
from worldmock.life.models import active_activities
from worldmock.life.activity_control import is_pending_conclusion


_MEMORIES = [
    {"type": "routine", "text": "睡前总要先喝点热的再洗漱。", "importance": 0.3, "keywords": ["睡前"]},
    {"type": "reflection", "text": "明天记得买牛肉面。", "importance": 0.5, "keywords": ["晚饭"]},
    {"type": "goal", "text": "明天下班再买薯片。", "importance": 0.4, "keywords": ["下班"]},
]
_DAY_PLAN_KEYS = {"habitual_consistency", "external_constraints", "deliberate_goals", "gates"}


def late_evening(document):
    """@brief 明确夜间运行窗口，工作时段在窗口外，输入不借旧持有物。"""
    document.update(start_at="2026-01-05T22:30:00+08:00", end_at="2026-01-06T01:00:00+08:00",
                    messages=[], payrolls=[], bills=[])


def moment(value):
    """@brief 测试时间与场景同源的带偏移整秒时间。"""
    return int(datetime.fromisoformat(value).timestamp())


def events(engine):
    """@brief 当前实际 SQLite 的完整严格事件序列。"""
    return engine.context.store.read_events(engine.context.store.last_event_seq)


def note(engine, label, errands, memories=()):
    """@brief 在真实等待收尾中记下事务及可选主观记忆，事务不充当事实证据。"""
    pump(engine, lambda: 1 in engine.scheduling_state().ready_agent_ids)
    running = [item for item in engine.context.state.activities.values() if item.agent_id == 1
               and (item.status == "active" or is_pending_conclusion(item))]
    if not running:
        started = perform(engine, f"{label}-wait", request("自己", None, "等待"))
        assert started.error_code is None
        running = active_activities(engine.context.state, 1)
    result = perform(engine, label, conclude_request(*(item.name for item in running), errands=errands,
                                                     memories=[list(memories) for _ in running]), "conclude_activity")
    assert result.error_code is None, result.data
    return result


def final_export(engine, directory):
    """@brief 经真实共同时间终点与检查点生成可认证导出。"""
    pump(engine, lambda: engine.context.state.current_time == engine.context.state.end_time)
    snapshots = {f"agent:{identity}": engine.context.store.assets.put_json(agent.model_dump(mode="json"),
                 {"kind": "test_agent_snapshot"}) for identity, agent in engine.context.state.agents.items()}
    point = engine.checkpoint(snapshots)
    export_run(engine.context.store, point.last_event_seq, directory)
    return validate_export(directory)


def test_an_exported_run_reports_errands_and_recertifies_value_for_value(engine_factory, tmp_path):
    engine = engine_factory(mutate=late_evening)
    note(engine, "note", [errand_add("买饮料", "柠檬茶", remind_time="22:55"),
                          errand_add("买晚饭", "牛肉面"), errand_add("买零食", "薯片")])
    perform(engine, "wait-reminder", request("自己", None, "等待"))
    pump(engine, lambda: engine.context.state.current_time >= moment("2026-01-05T22:55:00+08:00"))
    note(engine, "reflect", [], _MEMORIES)
    purchased = perform(engine, "buy", request("柠檬茶", "美团", "购买", payment="微信"))
    assert purchased.error_code is None, purchased.data
    order_id = purchased.data["orders"][0]["order_id"]
    pump(engine, lambda: engine.context.state.deliveries[order_id].status == "delivered")
    assert engine.context.state.current_time < moment("2026-01-06T00:00:00+08:00")
    pump(engine, lambda: engine.context.state.current_time >= moment("2026-01-06T00:00:00+08:00"))
    note(engine, "cancel", [errand_cancel("买零食", "明天再买。")])
    manifest, rows, activities = final_export(engine, tmp_path / "export")
    quality = json.loads((tmp_path / "export/quality.json").read_bytes())
    metrics = quality["errand_metrics"]
    assert metrics["registered_count"] == 3 and metrics["completed_count"] == 1
    assert metrics["cancelled_count"] == metrics["open_at_cutoff_count"] == 1
    assert metrics["carried_overnight_count"] == 2
    assert metrics["visible_observation_count"] == metrics["expected_observation_count"] > 0
    assert metrics["reminded_errand_count"] == metrics["acted_after_reminder_count"] == 1
    assert metrics["completed_without_evidence_count"] == 0
    assert quality["conclusion_memories"] == {"total_count": 3, "routine_count": 1, "errand_overlap_count": 2}
    assert quality["schema_version"] == 6
    assert all(set(group) == _DAY_PLAN_KEYS for group in quality["day_plan_metrics"].values())
    assert quality == build_quality_report([Event.model_validate(row) for row in rows], activities,
                                          manifest["frozen_run_manifest"])
    assert errand_metrics(rows, engine.context.scenario.timezone) == metrics
    assert conclusion_memories(rows) == quality["conclusion_memories"]
    completed = next(event for event in rows if event["data"].get("kind") == "errand_completed")
    [source_id] = completed["caused_by"]
    source = next(event for event in rows if event["event_id"] == source_id)
    assert source["data"]["kind"] == "goods_received" and source["agent_id"] == completed["agent_id"]
    assert source["data"]["order_id"] == order_id and source["event_seq"] < completed["event_seq"]


def test_acting_after_a_re_armed_reminder_is_counted_from_the_stored_events(engine_factory):
    def window(document):
        late_evening(document)
        document["start_at"] = "2026-01-05T23:20:00+08:00"
    engine = engine_factory(mutate=window)
    note(engine, "note", [errand_add("睡前零食", "薯片", remind_time="23:50")])
    waited = perform(engine, "wait", request("自己", None, "等待"))
    first, second = moment("2026-01-05T23:50:00+08:00"), moment("2026-01-06T00:10:00+08:00")
    pump(engine, lambda: engine.context.state.current_time >= first)
    note(engine, "update", [errand_update("睡前零食", remind_time="00:10")])
    pump(engine, lambda: engine.context.state.current_time >= second)
    reminders = [event.occurred_at for event in events(engine) if event.data.get("kind") == "errand_reminded"]
    assert reminders == [first, second]
    rows = [event.model_dump(mode="json") for event in events(engine)]
    before = errand_metrics(rows, engine.context.scenario.timezone)
    assert (before["reminded_errand_count"], before["acted_after_reminder_count"]) == (1, 0)
    note(engine, "end-wait", [])
    purchased = perform(engine, "snack", request("薯片", "美团", "购买", payment="微信"))
    assert purchased.error_code is None, purchased.data
    rows = [event.model_dump(mode="json") for event in events(engine)]
    after = errand_metrics(rows, engine.context.scenario.timezone)
    assert (after["reminded_errand_count"], after["acted_after_reminder_count"]) == (1, 1)
    assert (after["registered_count"], after["open_at_cutoff_count"]) == (1, 1)


def test_a_conclusion_that_only_notes_an_errand_is_no_memory_write_point(engine_factory):
    engine = engine_factory(mutate=late_evening)
    note(engine, "note", [errand_add("买饮料", "柠檬茶")])
    actual = events(engine)
    [concluded] = [event for event in actual if event.event_type == "action_requested"
                   and event.data["tool_name"] == "conclude_activity"]
    assert [row["name"] for row in concluded.data["request"]["errands"]] == ["买饮料"]
    assert [(item["memories"], item["views"]) for item in concluded.data["request"]["items"]] == [([], [])]
    assert not [event for event in actual if event.event_type == "memory_updated"]
    report = build_quality_report(actual, [], engine.context.store.manifest)
    assert report["memory_references"]["problem_count"] == 0
    assert report["conclusion_memories"] is None
    assert (report["errand_metrics"]["registered_count"], report["errand_metrics"]["open_at_cutoff_count"]) == (1, 1)
