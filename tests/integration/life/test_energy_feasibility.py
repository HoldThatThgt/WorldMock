"""能量不拦人：任何能量下活动照常受理、继续与恢复，结算在 0 截断，饥饿跨档在进行中活动里唤醒
（docs/design/04-simulation-engine.md 第三章）。"""

from __future__ import annotations

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT

import json
import math
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, context, settle_day_plans, stage_day_plan, start_activity,
                                 submit_round, target_actions, tool_targets, travel_quote, wake_by_mail, start_request,
                                 pause_request, report_request)
from tests.life.work_runtime import at_workplace, start_assigned_work
from tests.integration.life.test_acs_amended_runtime import perform, pump
from tests.integration.life.test_acs_runtime import request
from worldmock.life.travel import plan_trip
from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life import felt
from worldmock.life.body import minimum_energy_cost
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ActivityKind, ActivityStatus, ReviseDayPlanRequest
from worldmock.exchange.constants import CATEGORY_MET

_ROOT = Path(__file__).resolve().parents[3]


def _with_contact(document):
    document['agents'][0]['contacts'] = [{
        'contact_id': 'colleague', 'salutation': '同事', 'relation': '同事', 'closeness': 16,
        'channels': ['phone'], 'reply_policy': 'none'}]


def _set_energy(engine, energy_kcal: float) -> None:
    """@brief 将人物能量置于精确测试边界并持久提交。"""
    staged = engine.context.state.model_copy(deep=True)
    agent = staged.agents[1]
    body = agent.body.model_copy(update={"energy_kcal": energy_kcal}, deep=True)
    staged.agents[1] = agent.model_copy(
        update={"body": body, "next_decision_at": staged.current_time}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_energy_position"})])
    engine.context.state = staged


def _observed_action(engine, round_id: str, action: str, object_id: str | None = None):
    """从当前动作目录构造完整五字段请求，经全部就绪人物的真实事务受理。"""
    observed = engine.observe(context(engine, f"{round_id}-preview"))
    if action in {"wait", "sleep"}:
        value = request("自己", None, "等待" if action == "wait" else "睡觉")
    elif action == "travel_to":
        place = next(row.display_name for row in engine.context.scenario.locations if row.location_id == object_id)
        value = request(f"地点：{place}", None, "步行")
    else:
        identity = {"browse": "browse-feed", "call": "voice-call", "work": "office-work"}[action]
        catalog = engine.context.semantics.catalog
        selected = catalog.actions[identity]
        app = catalog.apps[selected.app_ids[0]].name if selected.app_ids else None
        to = "联系人：同事" if action == "call" else None
        value = request(selected.name, app, to=to)
    return observed, perform(engine, round_id, value)


def test_zero_energy_wait_settles_after_instant_action(engine_factory) -> None:
    """零能量瞬时修订当日目标后同一秒再决策，不产生空白区间；随后的等待照常结算清醒身体（时间与能量规格第一章规则 1、2）。
    当日计划由启动路径的零点计划登记（第四章）。"""
    engine = engine_factory()
    settle_day_plans(engine)
    _set_energy(engine, 0)
    decision = context(engine, "zero-energy-plan")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(
        decision, "action-zero-energy-plan", "revise_day_plan", ReviseDayPlanRequest(goals=[])))
    result = engine.commit_decision_round("zero-energy-plan")[0]
    assert result.status == "completed" and not engine.context.state.activities
    assert engine.scheduling_state().ready_agent_ids == (1,)
    with pytest.raises(ProtocolError, match="decision is due"):
        advance_next(engine)
    _, waited = _observed_action(engine, "zero-energy-wait", "wait")
    assert waited.status == "in_progress", waited.error_code
    before = engine.context.state.agents[1].body
    advance_next(engine)
    after = engine.context.state.agents[1].body
    assert after.energy_kcal == 0
    assert after.sleep_pressure > before.sleep_pressure
    assert after.entertainment_need > before.entertainment_need
    events = engine.context.store.read_agent_events("environment_event", 1)
    settlements = [item for item in events if item.data.get("kind") == "body_settlement"]
    assert settlements and all(item.data["activity_ids"] for item in settlements)
    assert waited.activity_id in settlements[-1].data["activity_ids"]


def test_zero_energy_observation_offers_every_action_without_energy_projection(engine_factory) -> None:
    """零能量不隐藏当前动作，自身等待睡觉与目录动作照常可用，观察不暴露能量可行性。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    observed = engine.observe(context(engine, "zero-energy-observation"))
    assert {way: (row["available"], row["reason"]) for way, row in target_actions(observed, "自己").items()} == {
        "等待": (True, None), "睡觉": (True, None)}
    feed = next(row for row in observed["tool_layer"]["targets"] if row["name"] == "刷推荐")
    assert all(row["available"] and row["duration"] for row in feed["actions"])
    assert "energy" not in json.dumps(observed["tool_layer"], ensure_ascii=False)
    assert "energy_feasibility" not in observed
    assert all("energy_projection" not in entry for entry in observed["affordance_contract"].values())
    assert observed["body"]["levels"]["appetite"] == "饿得发慌"


def test_routes_are_feasible_by_reachability_not_energy(engine_factory) -> None:
    """同一起终点的步行与驾车都按可达判定可行；费力标签区分方式，事件级路线不再带能量成本。"""
    engine = engine_factory(mutate=lambda d: d["agents"][0].update(owns_car=True, initial_driving_days=7))
    trips = {mode: plan_trip(engine.context.semantics.catalog.graph, 'home', 'office', mode)
             for mode in ('walking', 'driving')}
    costs = {mode: minimum_energy_cost(ActivityKind.TRAVEL, trip.duration_seconds,
                                      engine.context.scenario.body,
                                      engine.context.scenario.body.activity_met[f'travel:{mode}'])
             for mode, trip in trips.items()}
    assert costs["walking"] > costs["driving"]
    _set_energy(engine, (costs["walking"] + costs["driving"]) / 2)
    observed = engine.observe(context(engine, "route-energy"))
    assert 'energy_feasibility' not in observed
    assert all('minimum_energy_cost_kcal' not in item for item in observed['interaction_bindings'])
    office = next(item.name for item in engine.context.scenario.locations if item.location_id == "office")
    public = target_actions(observed, f"地点：{office}")
    assert public["驾车"]["available"] is True and public["步行"]["available"] is True
    assert (public["驾车"]["note"], public["步行"]["note"]) == ("费力程度：轻松", "费力程度：费力")
    assert f"地点：{office}" in tool_targets(observed)


@pytest.mark.parametrize("action", ["wait", "sleep", "browse"])
def test_zero_energy_start_is_accepted(engine_factory, action: str) -> None:
    """零能量时恢复类与非恢复类活动都照常受理（能量不参与受理）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    _, result = _observed_action(engine, f"zero-{action}", action)
    assert result.status == "in_progress", result.error_code


def test_derived_duration_beyond_energy_is_accepted_and_settles_at_zero(engine_factory) -> None:
    """由实际内容派生的完整时长超过能量预算时照常受理并结算至零。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 1)
    started = perform(engine, "course-low-energy", request("学课程", "哔哩哔哩"))
    assert started.error_code is None, started.data
    activity = engine.context.state.activities[started.activity_id]
    content = engine.context.semantics.catalog.contents[activity.data["content_id"]]
    assert activity.planned_duration_seconds == content.duration_seconds
    pump(engine, lambda: engine.context.state.activities[started.activity_id].status != "active")
    assert engine.context.state.activities[started.activity_id].status == "completed"
    assert engine.context.state.agents[1].body.energy_kcal == 0


def test_zero_energy_travel_and_work_are_accepted(engine_factory) -> None:
    """出行与分配工作在零能量下仍实际受理，内容与身体继续结算。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    _, travel = _observed_action(engine, "zero-travel", "travel_to", "office")
    assert travel.status == "in_progress", travel.data
    advance_next(engine)
    assert engine.context.state.agents[1].body.energy_kcal == 0
    worker = engine_factory()
    at_workplace(worker)
    _set_energy(worker, 0)
    content, work_id = start_assigned_work(worker, "zero-work")
    advance_next(worker)
    activity = worker.context.state.activities[work_id]
    progress = worker.context.state.agents[1].content_progress[content.content_id]
    assert progress.position_seconds == activity.elapsed_seconds > 0
    assert worker.context.state.agents[1].body.energy_kcal == 0


def test_zero_energy_resume_is_accepted(engine_factory) -> None:
    """零能量下暂停的实际内容恢复，地点和占用重验不会增加能量准入门槛。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    started = perform(engine, "course", request("学课程", "哔哩哔哩"))
    identity = started.activity_id
    wake_by_mail(engine, offset=60)
    advance_next(engine)
    paused = perform(engine, "pause-course", pause_request(activity_name(engine, identity)), "pause_activity")
    assert paused.error_code is None and engine.context.state.activities[identity].status == "paused"
    resumed = perform(engine, "resume-course", report_request(activity_name(engine, identity)), "report_status")
    assert resumed.error_code is None, resumed.data
    assert engine.context.state.activities[identity].status == "active"
    assert engine.context.state.agents[1].body.energy_kcal == 0


def _act(engine, round_id: str, tool: str, value):
    """全部就绪人物经真实轮次提交，返回本人回执。"""
    return perform(engine, round_id, value, tool)


def test_checkpoint_with_overdrawn_activity_restores(engine_factory) -> None:
    """零能量下进行中的观看在结算中透支到 0；这样的共同检查点照常恢复并继续推进。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _set_energy(engine, 0)
    _, watch = _observed_action(engine, "overdrawn-watch", "browse")
    assert watch.status == "in_progress"
    planned_end = engine.context.state.activities[watch.activity_id].planned_end_at
    assert engine.scheduling_state().next_event_at.timestamp() < planned_end
    advance_next(engine)
    assert engine.context.state.agents[1].body.energy_kcal == 0
    assert engine.context.state.activities[watch.activity_id].status == ActivityStatus.ACTIVE
    reference = engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "overdraft_restore_test"})
    assert engine.checkpoint({"agent:1": reference}).checkpoint_id
    run_dir = engine.context.store.run_dir
    engine.close()
    restored = LifeEngine.open(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json", run_dir)
    try:
        assert restored.context.state.activities[watch.activity_id].status == ActivityStatus.ACTIVE
        assert restored.context.state.agents[1].body.energy_kcal == 0
        perform(restored, "restore-report", report_request(activity_name(restored, watch.activity_id)), "report_status")
        advance_next(restored)
        assert restored.context.state.agents[1].body.energy_kcal == 0
    finally:
        restored.close()


def _stage_morning(engine, energy_kcal: float, seconds_of_day: int = 8 * 3600) -> int:
    """@brief 把全部人物放到首日 seconds_of_day（默认 08:00）、困意稳定在「清醒」，人物 1 的能量取指定值，返回当前时刻。"""
    staged = engine.context.state.model_copy(deep=True)
    now = int(engine.context.scenario.start_at.timestamp()) + seconds_of_day
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
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system",
        data={"kind": "test_hunger_position"})])
    engine.context.state = staged
    return now


@pytest.mark.parametrize(("energy", "floor", "before", "after"), [
    (405.0, 400.0, "很饿", "饿得难受"),
    (104.0, 100.0, "饿得难受", "饿得发慌"),
])
@pytest.mark.parametrize("action", ["browse", "call"])
def test_hunger_band_crossing_wakes_during_activity(engine_factory, energy, floor, before, after, action) -> None:
    """观看或通话进行中，饥饿跨过 400、100 千卡保底档的那一秒提供决策，原因含 felt_band_change。"""
    engine = engine_factory(mutate=_with_contact)
    stage_day_plan(engine)
    now = _stage_morning(engine, energy)
    observed, started = _observed_action(engine, f"hungry-{action}", action)
    assert started.status == "in_progress", started.error_code
    assert observed["body"]["levels"]["appetite"] == before
    met = CATEGORY_MET["social" if action == "call" else "entertainment"][True]
    burn = engine.context.scenario.body.basal_kcal_per_hour + (met - 1) * engine.context.scenario.body.mass_kg
    crossing = now + math.floor((energy - floor) * 3600 / burn) + 1
    assert engine.scheduling_state().next_event_at.timestamp() == crossing
    advance_next(engine)
    assert engine.context.state.current_time == crossing
    assert engine.context.state.activities[started.activity_id].status == ActivityStatus.ACTIVE
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert "felt_band_change" in engine.context.state.agents[1].pending_wake_reasons
    decision = context(engine, f"after-{action}")
    engine.begin_decision_round([decision])
    assert engine.observe(decision)["body"]["levels"]["appetite"] == after


def test_hunger_band_crossing_wakes_during_chat(engine_factory) -> None:
    """同处人物的当前线下社交按类别代谢结算，跨饥饿档时仍提供进行中决策。"""
    def co_present(document):
        document["agents"][1]["home_location_id"] = document["agents"][0]["home_location_id"]
    engine = engine_factory("multi-person.json", mutate=co_present)
    for identity in engine.context.state.agents:
        stage_day_plan(engine, identity)
    now = _stage_morning(engine, 405.0)
    partner = engine.context.state.agents[2].name
    chat = perform(engine, "hungry-chat", request("聊天", None, to=f"人物：{partner}"))
    assert chat.error_code is None, chat.data
    assert engine.context.state.activities[chat.activity_id].kind == ActivityKind.ACTION
    body = engine.context.scenario.body
    burn = body.basal_kcal_per_hour + (CATEGORY_MET["social"][False] - 1) * body.mass_kg
    crossing = now + math.floor(5.0 * 3600 / burn) + 1
    pump(engine, lambda: engine.context.state.current_time >= crossing)
    assert engine.context.state.current_time == crossing
    assert engine.context.state.activities[chat.activity_id].status == "active"
    assert "felt_band_change" in engine.context.state.agents[1].pending_wake_reasons
    observed = engine.observe(context(engine, "after-chat"))
    assert observed["body"]["levels"]["appetite"] == "饿得难受"


def test_hunger_crossing_just_before_lunch_end_wakes_during_wait(engine_factory) -> None:
    """13:20 起等待，第 480 秒（午餐窗口 13:30 结束之前）升到「饿得难受」：那一秒提供决策，原因含 felt_band_change。

    窗口终点餐时项跳变，分级又降回「很饿」；预测不晚于实际，不能把这段跨档推迟到窗口终点而跳过
    （docs/design/04-simulation-engine.md 第三章「让人物趁早吃」、第八章「节律」）。
    """
    engine = engine_factory(mutate=lambda document: document["agents"][0].update(lunch_window=[12.0, 13.5]))
    stage_day_plan(engine)
    reference_hour = 13 + 20 / 60 + 479.5 / 3600
    anchor = felt.APPETITE_ANCHOR_KCAL_DEFAULT
    burn = engine.context.scenario.body.basal_kcal_per_hour + 0.3 * engine.context.scenario.body.mass_kg
    energy = anchor - (0.85 - 0.15) / felt.appetite_circadian_multiplier(reference_hour) * (anchor - 400)
    energy += burn * 479.5 / 3600
    now = _stage_morning(engine, energy, 13 * 3600 + 20 * 60)
    observed, started = _observed_action(engine, "hungry-wait", "wait")
    assert started.status == "in_progress", started.error_code
    assert observed["body"]["levels"]["appetite"] == "很饿"
    body, windows = engine.context.scenario.body, engine.meal_windows_by_agent()[1]
    burn = body.basal_kcal_per_hour + (body.activity_met["wait"] - 1) * body.mass_kg
    at_lunch_end = energy - burn * 600 / 3600
    assert windows.lunch is not None and windows.lunch[1] == 13.5
    assert felt.appetite_level(felt.compute_appetite(at_lunch_end, 13.5, windows), at_lunch_end,
                               body.emergency_threshold_kcal) == "很饿"
    assert engine.scheduling_state().next_event_at.timestamp() == now + 480
    advance_next(engine)
    assert engine.context.state.current_time == now + 480
    assert engine.context.state.activities[started.activity_id].status == ActivityStatus.ACTIVE
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert "felt_band_change" in engine.context.state.agents[1].pending_wake_reasons
    decision = context(engine, "after-hungry-wait")
    engine.begin_decision_round([decision])
    assert engine.observe(decision)["body"]["levels"]["appetite"] == "饿得难受"


def test_energy_gate_symbols_are_gone_from_production_code() -> None:
    """src/ 与 custom/ 中不再有能量闸门的错误码、观察键与排除原因码（本测试以它们为泄漏样本）。"""
    leaks = ("INSUFFICIENT_ENERGY", "energy_feasibility", "ELIGIBILITY_ENERGY_INSUFFICIENT")
    hits = [f"{path.relative_to(_ROOT)}: {name}" for base in ("src", "custom")
            for path in sorted((_ROOT / base).rglob("*.py")) if "__pycache__" not in path.parts
            for name in leaks if name in path.read_text(encoding="utf-8")]
    assert hits == []
