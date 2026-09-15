"""LifeEngine 到认证导出与训练窗口的真实链路测试。"""

from __future__ import annotations

import json
from pathlib import Path

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context
from worldmock.delivery.windows import prepare_windows
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.validation import validate_export
from worldmock.life.models import StartBasicActivityRequest


def _stage_final_interval(engine, seconds: int) -> None:
    """把隔离运行置于终点前的明确区间，并移除越过时钟的后台事件。"""
    staged = engine.state.model_copy(deep=True)
    staged.current_time = staged.end_time - seconds
    staged.pending_events = []
    staged.agents[1] = staged.agents[1].model_copy(
        update={"next_decision_at": staged.current_time}, deep=True)
    staged.version += 1
    draft = EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                       visibility="system", source_kind="scenario",
                       data={"kind": "test_final_interval", "seconds": seconds})
    engine.store.commit(staged.model_dump(mode="json"), [draft])
    engine.state = staged


def test_completed_activity_exports_terminal_outcome_and_training_windows(engine_factory, tmp_path: Path) -> None:
    """真实活动终态回链原动作，并通过 export、认证和三类训练窗口消费。"""
    engine = engine_factory()
    _stage_final_interval(engine, 120)
    ctx = context(engine, "final-wait")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    action_id = "action-final-wait"
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=120)
    engine.submit_action(DirectToolCall(ctx, action_id, "start_basic_activity", request))
    started = engine.commit_decision_round("final-wait")[0]
    assert started.status == "in_progress"
    activity_id = started.activity_id

    advance_next(engine)
    assert engine.state.current_time == engine.state.end_time
    terminal = [event for event in engine.store.read_agent_events("action_result", 1)
                if event.action_id == action_id][-1]
    requested = [event for event in engine.store.read_agent_events("action_requested", 1)
                 if event.action_id == action_id][0]
    assert terminal.data["status"] == "completed"
    assert terminal.activity_id == activity_id
    assert terminal.decision_id == ctx.decision_id
    assert requested.event_id in terminal.caused_by
    assert terminal.data["activity"]["ended_at"] == engine.state.end_time

    agent_ref = engine.store.assets.put_json({"agent_id": 1}, {"kind": "export_chain_test"})
    checkpoint = engine.checkpoint({"agent:1": agent_ref})
    export_dir = tmp_path / "life-export"
    manifest = export_run(engine.store, checkpoint.last_event_seq, export_dir)
    validated, events, activities = validate_export(export_dir)
    windows = prepare_windows(export_dir)

    assert manifest["experiment_completed"] is True
    assert validated["checkpoint"]["checkpoint_id"] == checkpoint.checkpoint_id
    activity = next(item for item in activities if item["activity_id"] == activity_id)
    assert activity["status"] == "completed"
    assert activity["segments"] == [{"started_at": engine.state.end_time - 120,
                                     "ended_at": engine.state.end_time}]
    action = next(item for item in windows["action"]
                  if item["target"]["request"]["action_id"] == action_id)
    outcomes = action["target"]["outcomes"]
    assert any(item["event_id"] == terminal.event_id and item["data"]["status"] == "completed"
               for item in outcomes)
    assert observation["observation_event_id"] in action["provenance"]["source_event_ids"]
    assert windows["intent"] and windows["dreaming"] and events[-1]["event_type"] == "checkpoint_created"


def test_truncated_activity_exports_experiment_end_reason(engine_factory, tmp_path: Path) -> None:
    """实验终点截断原因进入权威状态、终态事件和认证导出质量。"""
    engine = engine_factory()
    _stage_final_interval(engine, 60)
    ctx = context(engine, "truncated-wait")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    action_id = "action-truncated-wait"
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=120)
    engine.submit_action(DirectToolCall(ctx, action_id, "start_basic_activity", request))
    started = engine.commit_decision_round("truncated-wait")[0]
    activity_id = started.activity_id

    advance_next(engine)
    activity = engine.state.activities[activity_id]
    assert activity.status == "truncated"
    assert activity.interruption_reason == "experiment_end"
    terminal = [event for event in engine.store.read_agent_events("action_result", 1)
                if event.action_id == action_id][-1]
    assert terminal.data["activity"]["interruption_reason"] == "experiment_end"

    agent_ref = engine.store.assets.put_json({"agent_id": 1}, {"kind": "truncated_export_test"})
    checkpoint = engine.checkpoint({"agent:1": agent_ref})
    export_dir = tmp_path / "truncated-export"
    export_run(engine.store, checkpoint.last_event_seq, export_dir)
    _, _, activities = validate_export(export_dir)
    quality = json.loads((export_dir / "quality.json").read_text())

    exported = next(item for item in activities if item["activity_id"] == activity_id)
    assert exported["interruption_reason"] == "experiment_end"
    assert quality["activities"]["interruptions_missing_reason"] == 0
    assert quality["activities"]["interruption_reason_counts"] == {"experiment_end": 1}
