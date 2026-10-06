"""当日目标三层报告进入真实 SQLite 截止点、命令导出和午夜结算产物。"""

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from tests.engine_fixture import (engine_factory)  # noqa: F401
from tests.life.conftest import (StubPlanDayClient, advance_next, context, settle_day_plans, start_request)  # noqa: F401
from tests.life.routing_fixtures import run_activity_out, selected_food, stage_food_holding, start_eating
from tests.life.test_day_planning import _goal, _late_engine

from agentsociety2.society import DirectToolCall
from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.cli import main
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import WORLD_SCHEMA_VERSION
from worldmock.life.run_identity import environment_identity
from worldmock.life.world_setup import load_runtime_scenario

CODE = "a" * 64
SCENARIO = ENVIRONMENT_ROOT / "scenarios" / "single-person.json"


def test_export_command_uses_authenticated_cutoff_and_manifest_code(tmp_path):
    run_dir = tmp_path / "run"
    semantics, _ = load_runtime_scenario(SCENARIO)
    manifest = {"schema_version": 3, "run_id": "run", "source_group_id": "run",
                "environment_identity": environment_identity(semantics),
                "agent_ids": [1], "code_sha256": CODE,
                "window": {"start": 100, "end": 300, "timezone": "UTC"}}
    initial = {"schema_version": WORLD_SCHEMA_VERSION, "version": 0, "current_time": 100}
    store = FootprintStore.create(run_dir, manifest, initial)
    goal = {"intent_kind": "工作", "goal": "推进报告", "registered_at": 110,
            "progressed_at": None, "progress_activity_id": None}
    plan = {"day_end": 86400, "goals": [goal]}
    store.commit({**initial, "version": 1, "current_time": 110}, [EventDraft(
        event_type="action_result", occurred_at=110, agent_id=1, action_id="plan",
        data={"status": "completed", "data": {"day_plan": plan}})])
    activity = {"activity_id": "work", "agent_id": 1, "kind": "work", "started_at": 110,
                "elapsed_seconds": 90, "ended_at": 200, "status": "completed", "location_id": "office"}
    settled = {"day_end": 86400, "goals": [dict(goal, progressed_at=200, progress_activity_id="work")]}
    store.commit({**initial, "version": 2, "current_time": 200}, [EventDraft(
        event_type="activity_progress", occurred_at=200, agent_id=1, activity_id="work",
        data={"activity": activity, "interval_start": 110, "interval_end": 200, "day_plan": settled})])
    store.commit({**initial, "version": 3, "current_time": 210}, [EventDraft(
        event_type="action_rejected", occurred_at=210, agent_id=1, action_id="closed",
        data={"error_code": "APP_NOT_BOUND"})])
    store.close()

    for cutoff, progressed, other_rejections in ((1, 0, 0), (2, 1, 0), (3, 1, 1)):
        destination = tmp_path / f"export-{cutoff}"
        assert main(["export", "--run-dir", str(run_dir), "--cutoff", str(cutoff),
                     "--output", str(destination)]) == 0
        payload = (destination / "quality.json").read_bytes()
        quality = json.loads(payload)
        exported_manifest = json.loads((destination / "manifest.json").read_bytes())
        assert exported_manifest["quality_report"]["sha256"] == hashlib.sha256(payload).hexdigest()
        metrics = quality["day_plan_metrics"][CODE]
        assert set(metrics) == {"habitual_consistency", "external_constraints", "deliberate_goals", "gates"}
        # 当日目标报告的键集不因个人事务改变：事务指标是紧随其后的顶层分组，没有事务事件时为 null，不写进
        # day_plan_metrics（docs/VERIFICATION.md#errands-workday-度量与下游-质量报告 ）。
        assert list(quality["day_plan_metrics"]) == [CODE] and "errand" not in json.dumps(quality["day_plan_metrics"])
        assert (quality["errand_metrics"], quality["conclusion_memories"]) == (None, None)
        assert metrics["deliberate_goals"]["summary"]["progressed_count"] == progressed
        constraints = metrics["external_constraints"]
        assert constraints["block_count"] == 0
        assert constraints["other_rejections"]["count"] == other_rejections
        expected_codes = {"APP_NOT_BOUND": 1} if other_rejections else {}
        assert constraints["other_rejections"]["by_error_code"] == expected_codes
        assert metrics["gates"]["external_constraint_block_count"]["pass"] is True
        assert quality["event_range"]["last_event_seq"] == cutoff


def _stage_midnight_round(engine, now):
    """显式场景事件只安排决策边界，不伪造目标或活动结算证据。"""
    state = engine.context.state.model_copy(deep=True)
    state.current_time = now
    state.pending_events = []
    state.agents[1] = state.agents[1].model_copy(
        update={"next_decision_at": now, "body_settled_at": now}, deep=True)
    state.version += 1
    engine.context.store.commit(state.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, source_kind="scenario",
        data={"kind": "test_midnight_decision"})])
    engine.context.state = state


def _submit(engine, name, request, round_id):
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", name, request))
    return engine.commit_decision_round(round_id)[0]


def test_real_engine_midnight_settlement_exports_old_day_goal_progress(engine_factory, tmp_path):
    """零点计划经系统登记入口写下的 day_plan_generated 是导出报告的目标登记来源；终点零点结算的末段推进计入旧日
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章「输入、调用、失败与记录」）。"""
    engine = engine_factory()
    midnight = engine.context.state.end_time
    _stage_midnight_round(engine, midnight - 120)
    client = StubPlanDayClient(goals=[{"intent_kind": "休息", "goal": "睡前休息", "remind_time": None,
                                        "around_time": None}])
    assert settle_day_plans(engine, client)[0]["goals"] == 1
    registered = engine.context.store.read_agent_events("environment_event", 1)[-1]
    assert registered.data["kind"] == "day_plan_generated"
    started = _submit(engine, "start_activity", start_request(
        targets=["自己"], actions=["interact"], ways=["等待"]), "midnight-rest")
    assert started.status == "in_progress"
    advance_next(engine)
    assert engine.context.state.current_time == midnight
    assert engine.context.state.agents[1].day_plan is None
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    evidence = next(item for item in events if item.event_type == "activity_progress"
                    and item.activity_id == started.activity_id)
    assert evidence.data["day_plan"]["goals"][0]["progressed_at"] == midnight
    assert evidence.data["activity"]["location_id"] == engine.context.state.agents[1].location_id

    destination = tmp_path / "midnight-export"
    export_run(engine.context.store, engine.context.store.last_event_seq, destination)
    quality = json.loads((destination / "quality.json").read_bytes())
    metrics = quality["day_plan_metrics"][engine.context.store.manifest["code_sha256"]]
    day = datetime.fromtimestamp(midnight - 1, ZoneInfo(engine.context.scenario.timezone)).date().isoformat()
    goals = metrics["deliberate_goals"]["per_agent_day"][f"{engine.context.state.run_id}:1:{day}"]
    assert goals["progressed_count"] == 1 and goals["family_progress_rate"] == 1
    assert goals["goals"][0]["registration_event_id"] == registered.event_id
    assert goals["goals"][0]["progress_event_id"] == evidence.event_id
    assert goals["goals"][0]["progressed_at"] == midnight


def test_exported_quality_counts_only_non_routine_goals_and_agrees_with_engine_progress(tmp_path):
    """导出的 quality.json 与引擎推进同一判定（docs/dev/SPEC-life-realism.md#dayrun-fidelity-人设与系统层-零点计划只写今天与平常不同的事 、8.2）：10:20–10:45 的进食推进大致时刻
    12:00 的例行午饭目标，大致时刻 23:00 的娱乐目标未推进；目标推进率只计非例行意图，例行意图的目标另计 routine_goals；
    导出的推进时刻与活动身份与引擎的推进证据相同。"""
    engine = _late_engine(tmp_path, "2026-01-05T10:20:00+08:00", "export-around")
    try:
        settle_day_plans(engine, StubPlanDayClient(goals=[_goal("用餐", "午饭提前到十点多吃", None, "12:00"),
                                                          _goal("娱乐", "睡前看会儿剧", None, "23:00")]))
        food = selected_food(engine)
        stage_food_holding(engine, food.food_id, 1.0)
        _, eating = start_eating(engine, food, "early-lunch")
        run_activity_out(engine, eating.activity_id, "early-lunch")
        lunch, evening = engine.context.state.agents[1].day_plan.goals
        assert (lunch.progress_activity_id, evening.progressed_at) == (eating.activity_id, None)
        destination = tmp_path / "export"
        export_run(engine.context.store, engine.context.store.last_event_seq, destination)
        quality = json.loads((destination / "quality.json").read_bytes())
        goals = quality["day_plan_metrics"][engine.context.store.manifest["code_sha256"]]["deliberate_goals"]
        day = goals["per_agent_day"][f"{engine.context.state.run_id}:1:2026-01-05"]
        assert [(item["intent_kind"], item["routine"], item["status"], item["progressed_at"],
                 item["progress_activity_id"]) for item in day["goals"]] == [
            ("用餐", True, "progressed", lunch.progressed_at, eating.activity_id), ("娱乐", False, "pending", None, None)]
        assert (day["goal_count"], day["progressed_count"], day["family_progress_rate"]) == (1, 0, 0)
        assert day["routine_goals"] == {"goal_count": 1, "progressed_count": 1, "discarded_count": 0,
                                        "family_progress_rate": 1}
        assert goals["summary"]["family_progress_rate"] == 0 and goals["summary"]["routine_goals"]["goal_count"] == 1
    finally:
        engine.close()
