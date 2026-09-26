"""当日目标三层报告进入真实 SQLite 截止点、命令导出和午夜结算产物。"""

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from tests.life.conftest import StubPlanDayClient, advance_next, context, engine_factory, settle_day_plans

from agentsociety2.society import DirectToolCall
from worldmock.cli import main
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import StartActivityRequest

CODE = "a" * 64


def test_export_command_uses_authenticated_cutoff_and_manifest_code(tmp_path):
    run_dir = tmp_path / "run"
    manifest = {"run_id": "run", "agent_ids": [1], "code_sha256": CODE,
                "window": {"start": 100, "end": 300, "timezone": "UTC"}}
    store = FootprintStore.create(run_dir, manifest, {"version": 0, "current_time": 100})
    goal = {"intent_kind": "工作", "goal": "推进报告", "registered_at": 110,
            "progressed_at": None, "progress_activity_id": None}
    plan = {"day_end": 86400, "goals": [goal]}
    store.commit({"version": 1, "current_time": 110}, [EventDraft(
        event_type="action_result", occurred_at=110, agent_id=1, action_id="plan",
        data={"status": "completed", "data": {"day_plan": plan}})])
    activity = {"activity_id": "work", "agent_id": 1, "kind": "work", "started_at": 110,
                "elapsed_seconds": 90, "ended_at": 200, "status": "completed", "location_id": "office"}
    settled = {"day_end": 86400, "goals": [dict(goal, progressed_at=200, progress_activity_id="work")]}
    store.commit({"version": 2, "current_time": 200}, [EventDraft(
        event_type="activity_progress", occurred_at=200, agent_id=1, activity_id="work",
        data={"activity": activity, "interval_start": 110, "interval_end": 200, "day_plan": settled})])
    store.commit({"version": 3, "current_time": 210}, [EventDraft(
        event_type="action_rejected", occurred_at=210, agent_id=1, action_id="closed",
        data={"error_code": "MERCHANT_CLOSED"})])
    store.close()

    for cutoff, progressed, blocked in ((1, 0, 0), (2, 1, 0), (3, 1, 1)):
        destination = tmp_path / f"export-{cutoff}"
        assert main(["export", "--run-dir", str(run_dir), "--cutoff", str(cutoff),
                     "--output", str(destination)]) == 0
        payload = (destination / "quality.json").read_bytes()
        quality = json.loads(payload)
        exported_manifest = json.loads((destination / "manifest.json").read_bytes())
        assert exported_manifest["quality_report"]["sha256"] == hashlib.sha256(payload).hexdigest()
        metrics = quality["day_plan_metrics"][CODE]
        assert set(metrics) == {"habitual_consistency", "external_constraints", "deliberate_goals", "gates"}
        assert metrics["deliberate_goals"]["summary"]["progressed_count"] == progressed
        assert metrics["external_constraints"]["block_count"] == blocked
        assert metrics["gates"]["external_constraint_block_count"]["pass"] is (blocked == 0)
        assert quality["event_range"]["last_event_seq"] == cutoff


def _stage_midnight_round(engine, now):
    """显式场景事件只安排决策边界，不伪造目标或活动结算证据。"""
    state = engine.state.model_copy(deep=True)
    state.current_time = now
    state.pending_events = []
    state.agents[1] = state.agents[1].model_copy(
        update={"next_decision_at": now, "body_settled_at": now}, deep=True)
    state.version += 1
    engine.store.commit(state.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, source_kind="scenario",
        data={"kind": "test_midnight_decision"})])
    engine.state = state


def _submit(engine, name, request, round_id):
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", name, request))
    return engine.commit_decision_round(round_id)[0]


def test_real_engine_midnight_settlement_exports_old_day_goal_progress(engine_factory, tmp_path):
    """零点计划经系统登记入口写下的 day_plan_generated 是导出报告的目标登记来源；终点零点结算的末段推进计入旧日
    （docs/dev/SPEC-time-and-energy.md 第四章「输入、调用、失败与记录」）。"""
    engine = engine_factory()
    midnight = engine.state.end_time
    _stage_midnight_round(engine, midnight - 120)
    client = StubPlanDayClient(goals=[{"intent_kind": "休息", "goal": "睡前休息", "remind_time": None}])
    assert settle_day_plans(engine, client)[0]["goals"] == 1
    registered = engine.store.read_agent_events("environment_event", 1)[-1]
    assert registered.data["kind"] == "day_plan_generated"
    started = _submit(engine, "start_activity", StartActivityRequest(
        targets=["自己"], actions=["等待"]), "midnight-rest")
    assert started.status == "in_progress"
    advance_next(engine)
    assert engine.state.current_time == midnight
    assert engine.state.agents[1].day_plan is None
    events = engine.store.read_events(engine.store.last_event_seq)
    evidence = next(item for item in events if item.event_type == "activity_progress"
                    and item.activity_id == started.activity_id)
    assert evidence.data["day_plan"]["goals"][0]["progressed_at"] == midnight
    assert evidence.data["activity"]["location_id"] == engine.state.agents[1].location_id

    destination = tmp_path / "midnight-export"
    export_run(engine.store, engine.store.last_event_seq, destination)
    quality = json.loads((destination / "quality.json").read_bytes())
    metrics = quality["day_plan_metrics"][engine.store.manifest["code_sha256"]]
    day = datetime.fromtimestamp(midnight - 1, ZoneInfo(engine.scenario.timezone)).date().isoformat()
    goals = metrics["deliberate_goals"]["per_agent_day"][f"{engine.state.run_id}:1:{day}"]
    assert goals["progressed_count"] == 1 and goals["family_progress_rate"] == 1
    assert goals["goals"][0]["registration_event_id"] == registered.event_id
    assert goals["goals"][0]["progress_event_id"] == evidence.event_id
    assert goals["goals"][0]["progressed_at"] == midnight
