"""能量不拦人：任何能量下活动照常受理、继续与恢复，结算在 0 截断，饥饿跨档在进行中活动里唤醒
（docs/dev/SPEC-time-and-energy.md 第三章）。"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from agentsociety2.agent.base import ReactLoopRequest
from agentsociety2.society import DirectToolCall

from custom.agents.life_agent import LifeAgent
from tests.life.conftest import advance_next, context, stage_day_plan, submit_round, travel_quote, wake_by_mail
from tests.life.test_work_review import _at_task_start
from worldmock.footprints.models import EventDraft
from worldmock.life.body import minimum_energy_cost
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (ActivityKind, ActivityStatus, PauseActivityRequest, PlanDayRequest,
                                   ReportStatusRequest, StartActivityRequest)
from worldmock.life.social_actions import CALL_MET, CHAT_MET

_ROOT = Path(__file__).resolve().parents[2]


def _set_energy(engine, energy_kcal: float) -> None:
    """@brief 将人物能量置于精确测试边界并持久提交。"""
    staged = engine.state.model_copy(deep=True)
    agent = staged.agents[1]
    body = agent.body.model_copy(update={"energy_kcal": energy_kcal}, deep=True)
    staged.agents[1] = agent.model_copy(
        update={"body": body, "next_decision_at": staged.current_time}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_energy_position"})])
    engine.state = staged


def _observed_action(engine, round_id: str, action: str, object_id: str | None = None):
    """@brief 从冻结观察的私有绑定取可读对象和动作并执行。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    binding = next(row for row in observed["interaction_bindings"]
                   if object_id is None or row["object_id"] == object_id
                   if any(item["canonical_action"] == action for item in row["actions"]))
    label = next(item["label"] for item in binding["actions"]
                 if item["canonical_action"] == action)
    request = StartActivityRequest(targets=[binding["object"]], actions=[label])
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "start_activity", request))
    return observed, engine.commit_decision_round(round_id)[0]


def test_zero_energy_idle_time_settles_after_instant_action(engine_factory) -> None:
    """零能量瞬时登记后，空白时段仍正常结算清醒身体。"""
    engine = engine_factory()
    _set_energy(engine, 0)
    decision = context(engine, "zero-energy-plan")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(
        decision, "action-zero-energy-plan", "plan_day", PlanDayRequest(goals=())))
    result = engine.commit_decision_round("zero-energy-plan")[0]
    assert result.status == "completed" and not engine.state.activities
    before = engine.state.agents[1].body
    advance_next(engine)
    after = engine.state.agents[1].body
    assert after.energy_kcal == 0
    assert after.sleep_pressure > before.sleep_pressure
    assert after.entertainment_need > before.entertainment_need
    events = engine.store.read_agent_events("environment_event", 1)
    assert any(item.data.get("kind") == "body_settlement" and not item.data["activity_ids"]
               for item in events)


def test_zero_energy_observation_offers_every_action_without_energy_projection(engine_factory) -> None:
    """零能量时工具与动作照常列出：观察不带能量可行性，观看、浏览与自身动作都在，合同不带能量投影。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    decision = context(engine, "zero-energy-observation")
    observed = engine.observe(decision)
    wrapped = [{"turn": 0, "action": "observe", "ok": True, "data": observed}]
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, decision.observed_at, wrapped))
    tool_names = {item["function"]["name"] for item in agent.build_react_tool_schemas(readonly=False)}
    assert "start_activity" in tool_names
    objects = {item["object"]: item["actions"] for item in observed["facts"]["interactive_objects"]}
    assert {"等待", "睡觉"} <= set(objects["自己"])
    assert "观看" in objects["内容：Sintel"] and "浏览" in objects["商品目录"]
    assert "energy_feasibility" not in observed
    assert all("energy_projection" not in entry for entry in observed["affordance_contract"].values())
    assert observed["body"]["levels"]["appetite"] == "饿得发慌"


def test_routes_are_feasible_by_reachability_not_energy(engine_factory) -> None:
    """同一起终点的步行与驾车都按可达判定可行；费力标签区分方式，事件级路线不再带能量成本。"""
    engine = engine_factory()
    routes = [item for item in engine.scenario.routes
              if item.origin_id == "home" and item.destination_id == "office"]
    costs = {item.mode.value: minimum_energy_cost(
        ActivityKind.TRAVEL, travel_quote(engine, item.mode.value, item.duration_seconds),
        engine.scenario.body, engine.scenario.body.activity_met[f"travel:{item.mode.value}"])
        for item in routes}
    assert costs["walking"] > costs["driving"]
    _set_energy(engine, (costs["walking"] + costs["driving"]) / 2)
    observed = engine.observe(context(engine, "route-energy"))
    event_rows = [item for item in observed["routes"] if item["destination_id"] == "office"]
    assert event_rows and all("energy_feasible" not in item and "minimum_energy_cost_kcal" not in item
                              for item in event_rows)
    office = next(item.name for item in engine.scenario.locations if item.location_id == "office")
    public = {item["mode"]: item for item in observed["facts"]["routes"] if item["destination"] == office}
    assert public["driving"]["feasible"] is True and public["walking"]["feasible"] is True
    assert (public["driving"]["effort"], public["walking"]["effort"]) == ("轻松", "费力")


@pytest.mark.parametrize("action", ["wait", "sleep", "watch", "browse"])
def test_zero_energy_start_is_accepted(engine_factory, action: str) -> None:
    """零能量时恢复类与非恢复类活动都照常受理（能量不参与受理）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    _, result = _observed_action(engine, f"zero-{action}", action)
    assert result.status == "in_progress", result.error_code


def test_derived_duration_beyond_energy_is_accepted_and_settles_at_zero(engine_factory) -> None:
    """系统派生的完整时长远超剩余能量时照常受理；结算把能量截到 0，全程无 ValueError。"""
    engine = engine_factory()
    stage_day_plan(engine)
    duration = engine.scenario.activity_feedback_seconds
    budget = minimum_energy_cost(ActivityKind.BROWSE, duration, engine.scenario.body)
    _set_energy(engine, budget / 2)
    _, result = _observed_action(engine, "browse-low-energy", "browse")
    assert result.status == "in_progress"
    while engine.state.activities[result.activity_id].status == ActivityStatus.ACTIVE:
        advance_next(engine)
    assert engine.state.activities[result.activity_id].status == ActivityStatus.COMPLETED
    assert engine.state.agents[1].body.energy_kcal == 0


def test_zero_energy_travel_and_work_are_accepted(engine_factory) -> None:
    """零能量时出行与工作都受理；工作照常累计有效时长（能量只压低效率）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    _, travel = _observed_action(engine, "zero-travel", "travel_to", "office")
    assert travel.status == "in_progress", travel.error_code
    advance_next(engine)
    assert engine.state.agents[1].body.energy_kcal == 0
    worker = engine_factory()
    _at_task_start(worker)
    stage_day_plan(worker)
    _set_energy(worker, 0)
    task = worker.scenario.tasks[0]
    _, work = _observed_action(worker, "zero-work", "work", task.task_id)
    assert work.status == "in_progress", work.error_code
    advance_next(worker)
    activity = worker.state.activities[work.activity_id]
    assert activity.elapsed_seconds > 0 and activity.effective_work_seconds > 0
    assert worker.state.agents[1].body.energy_kcal == 0


def _session_of(engine, activity_id: str) -> str:
    """@brief 取活动所在会话的可读名称。"""
    return next(item.session_name for item in engine.state.sessions.values()
                if activity_id in item.activity_ids)


def test_zero_energy_resume_is_accepted(engine_factory) -> None:
    """零能量时暂停的观看照常恢复：恢复只重验地点与占用，不查能量。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    decision = context(engine, "watch-and-wait")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    request = StartActivityRequest(targets=["内容：Sintel", "自己"], actions=["观看", "等待"])
    engine.submit_action(DirectToolCall(decision, "action-watch-and-wait", "start_activity", request))
    assert engine.commit_decision_round("watch-and-wait")[0].status == "in_progress"
    watch = next(item.activity_id for item in engine.state.activities.values() if item.kind == ActivityKind.WATCH)
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    paused = _act(engine, "pause-watch", "pause_activity",
                  PauseActivityRequest(task=[_session_of(engine, watch)], summary=["先停一下。"]))
    assert paused.status == "completed" and engine.state.activities[watch].status == ActivityStatus.PAUSED
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    assert engine.state.agents[1].body.energy_kcal == 0
    resumed = _act(engine, "resume-watch", "report_status",
                   ReportStatusRequest(task=[_session_of(engine, watch)], summary=["接着看。"]))
    assert resumed.status == "completed", resumed.error_code
    assert engine.state.activities[watch].status == ActivityStatus.ACTIVE


def _act(engine, round_id: str, tool: str, request):
    """@brief 以真实观察与登记执行一个决策轮并返回结果。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", tool, request))
    return engine.commit_decision_round(round_id)[0]


def test_checkpoint_with_overdrawn_activity_restores(engine_factory) -> None:
    """零能量下进行中的观看在结算中透支到 0；这样的共同检查点照常恢复并继续推进。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    _, watch = _observed_action(engine, "overdrawn-watch", "watch")
    assert watch.status == "in_progress"
    planned_end = engine.state.activities[watch.activity_id].planned_end_at
    assert engine.scheduling_state().next_event_at.timestamp() < planned_end
    advance_next(engine)
    assert engine.state.agents[1].body.energy_kcal == 0
    assert engine.state.activities[watch.activity_id].status == ActivityStatus.ACTIVE
    reference = engine.store.assets.put_json({"agent_id": 1}, {"kind": "overdraft_restore_test"})
    assert engine.checkpoint({"agent:1": reference}).checkpoint_id
    run_dir = engine.store.run_dir
    engine.close()
    restored = LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)
    try:
        assert restored.state.activities[watch.activity_id].status == ActivityStatus.ACTIVE
        assert restored.state.agents[1].body.energy_kcal == 0
        advance_next(restored)
        assert restored.state.agents[1].body.energy_kcal == 0
    finally:
        restored.close()


def _stage_morning(engine, energy_kcal: float) -> int:
    """@brief 把全部人物放到首日 08:00、困意稳定在「清醒」，人物 1 的能量取指定值，返回当前时刻。"""
    staged = engine.state.model_copy(deep=True)
    now = int(engine.scenario.start_at.timestamp()) + 8 * 3600
    staged.current_time = now
    staged.pending_events = [item for item in staged.pending_events if item.due_at > now]
    for agent_id, agent in staged.agents.items():
        update = {"sleep_pressure": 0.2, "entertainment_need": 0.0, "social_need": 0.0}
        if agent_id == 1:
            update["energy_kcal"] = energy_kcal
        body = agent.body.model_copy(update=update, deep=True)
        staged.agents[agent_id] = agent.model_copy(
            update={"body": body, "body_settled_at": now, "next_decision_at": now}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system",
        data={"kind": "test_hunger_position"})])
    engine.state = staged
    return now


@pytest.mark.parametrize(("energy", "floor", "before", "after"), [
    (405.0, 400.0, "很饿", "饿得难受"),
    (104.0, 100.0, "饿得难受", "饿得发慌"),
])
@pytest.mark.parametrize("action", ["watch", "call"])
def test_hunger_band_crossing_wakes_during_activity(engine_factory, energy, floor, before, after, action) -> None:
    """观看或通话进行中，饥饿跨过 400、100 千卡保底档的那一秒提供决策，原因含 felt_band_change。"""
    engine = engine_factory()
    stage_day_plan(engine)
    now = _stage_morning(engine, energy)
    observed, started = _observed_action(engine, f"hungry-{action}", action)
    assert started.status == "in_progress", started.error_code
    assert observed["body"]["levels"]["appetite"] == before
    met = CALL_MET if action == "call" else engine.scenario.body.activity_met["watch"]
    burn = engine.scenario.body.basal_kcal_per_hour + (met - 1) * engine.scenario.body.mass_kg
    crossing = now + math.floor((energy - floor) * 3600 / burn) + 1
    assert engine.scheduling_state().next_event_at.timestamp() == crossing
    advance_next(engine)
    assert engine.state.current_time == crossing
    assert engine.state.activities[started.activity_id].status == ActivityStatus.ACTIVE
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert "felt_band_change" in engine.state.agents[1].pending_wake_reasons
    decision = context(engine, f"after-{action}")
    engine.begin_decision_round([decision])
    assert engine.observe(decision)["body"]["levels"]["appetite"] == after


def test_hunger_band_crossing_wakes_during_chat(engine_factory) -> None:
    """两人同处时交谈进行中（代谢当量取 CHAT_MET），饥饿跨过 400 千卡保底档的那一秒同样提供决策。"""
    engine = engine_factory("multi-person.json")
    stage_day_plan(engine, 1)
    stage_day_plan(engine, 2)
    now = _stage_morning(engine, 405.0)
    wait = ("start_activity", StartActivityRequest(targets=["自己"], actions=["等待"]))
    requests = {1: ("start_activity", StartActivityRequest(targets=["人物：李凡"], actions=["交谈"])), 2: wait}
    _, results = submit_round(engine, requests, "hungry-chat")
    chat = next(item for item in results if item.agent_id == 1)
    assert chat.status == "in_progress", chat.error_code
    assert engine.state.activities[chat.activity_id].kind == ActivityKind.CHAT
    body = engine.scenario.body
    burn = body.basal_kcal_per_hour + (CHAT_MET - 1) * body.mass_kg
    crossing = now + math.floor(5.0 * 3600 / burn) + 1
    while 1 not in (ready := engine.scheduling_state().ready_agent_ids):
        if ready:
            submit_round(engine, {agent_id: wait for agent_id in ready}, f"filler-{engine.state.current_time}")
        else:
            advance_next(engine)
    assert engine.state.current_time == crossing
    assert engine.state.activities[chat.activity_id].status == ActivityStatus.ACTIVE
    assert "felt_band_change" in engine.state.agents[1].pending_wake_reasons
    assert engine.state.agents[1].felt_bands_at_last_decision["appetite"] == "很饿"
    decision = context(engine, "after-chat", 1)
    engine.begin_decision_round([decision])
    assert engine.observe(decision)["body"]["levels"]["appetite"] == "饿得难受"


def test_energy_gate_symbols_are_gone_from_production_code() -> None:
    """src/ 与 custom/ 中不再有能量闸门的错误码、观察键与排除原因码（本测试以它们为泄漏样本）。"""
    leaks = ("INSUFFICIENT_ENERGY", "energy_feasibility", "ELIGIBILITY_ENERGY_INSUFFICIENT")
    hits = [f"{path.relative_to(_ROOT)}: {name}" for base in ("src", "custom")
            for path in sorted((_ROOT / base).rglob("*.py")) if "__pycache__" not in path.parts
            for name in leaks if name in path.read_text(encoding="utf-8")]
    assert hits == []


def test_motivation_internal_still_weighs_energy(engine_factory) -> None:
    """内部动机评分（只供评估）仍按能量取舍：零能量时不再给按能量限时的生活活动候选，能量充足时给；
    恢复类的睡眠候选两种情形都在。"""
    engine = engine_factory()
    stage_day_plan(engine)
    rich = engine._observation_data(engine.state.agents[1])["motivation_internal"]
    _set_energy(engine, 0)
    poor = engine._observation_data(engine.state.agents[1])["motivation_internal"]
    assert rich is not None and poor is not None
    families = {item["family"] for item in rich["candidates"]}
    starved = {item["family"] for item in poor["candidates"]}
    assert "lifestyle" in families and "sleep" in families
    assert "lifestyle" not in starved and "sleep" in starved
