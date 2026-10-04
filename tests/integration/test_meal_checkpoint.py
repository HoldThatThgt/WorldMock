"""实际餐食被中断并恢复后，付款、库存和食用事实不重复；活动按点名控制，结束的活动当秒收尾。"""

import json
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.society import DirectToolCall  # noqa: E402

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_pending, conclude_request,
                                 context, stage_day_plan, wake_by_mail, start_request, pause_request, report_request)
from tests.life.test_dayrun_restaurants import RESTAURANT, MEAL, SUPPLY  # noqa: E402
from tests.life.test_scheduling import _custom_engine  # noqa: E402
from worldmock.footprints.experiences import project_verified_experiences  # noqa: E402
from worldmock.footprints.export import export_run  # noqa: E402
from worldmock.life import sessions  # noqa: E402
from worldmock.life.engine import LifeEngine  # noqa: E402
from worldmock.life.models import ActivityStatus, SessionKind
from worldmock.life.opening_hours import next_opening_change, opening_status


def _action(engine, name: str, tool: str, request):
    """@brief 以真实观察、动作登记和结算运行一个决策。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    result = engine.commit_decision_round(name)[0]
    assert result.status in {"completed", "in_progress"}, result.error_code
    return observed, result


def _start(engine, name: str, kind: str, object_id: str | None, way: str):
    """@brief 从冻结观察选取可读对象与它列出的方式（动词取同一项私有绑定），让引擎推导领域参数。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    binding, verb = next((row, item["verb"]) for row in observed["interaction_bindings"]
                         if row["kind"] == kind and (object_id is None or row["object_id"] == object_id)
                         for item in row["actions"] if item["label"] == way)
    request = start_request(targets=[binding["object"]], actions=[verb], ways=[way])
    engine.submit_action(DirectToolCall(decision, f"action-{name}", "start_activity", request))
    result = engine.commit_decision_round(name)[0]
    assert result.status in {"completed", "in_progress"}, result.error_code
    return observed, result


def _run_out(engine, activity_id: str) -> None:
    """@brief 只经真实时间边界推进持续活动到完成，并在结束那一秒收尾（时间与能量规格第七章「活动一结束就要结束它」）。

    中途的决策机会以按活动名的播报继续；活动完成后人物当秒就绪，收尾前时钟不推进。
    """
    for step in range(64):
        if engine.context.state.activities[activity_id].status == ActivityStatus.COMPLETED:
            assert engine.scheduling_state().ready_agent_ids == (1,)
            assert conclude_pending(engine, f"conclude-{activity_id}").status == "completed"
            assert engine.context.state.activities[activity_id].concluded_at == engine.context.state.current_time
            return
        if engine.scheduling_state().ready_agent_ids:
            _action(engine, f"continue-{activity_id}-{step}", "report_status",
                    report_request(activity_name(engine, activity_id), summary="继续进行。"))
        else:
            advance_next(engine)
    raise AssertionError("Activity did not complete within its event boundaries")


def test_paid_meal_pause_checkpoint_resume_preserves_actual_consumption(tmp_path: Path):
    """经真实购买与消费入口，恢复只吃剩余量，消费分录与投影严格守恒。

    暂停的餐食留在餐馆的停留会话里占着名额，按活动名在开始地点恢复；主动结束的等待在导出中为 cancelled，
    餐食的暂停、恢复与收尾各留一条生命周期记录，导出的活动状态与最终检查点逐项一致。
    """
    def lunch(document):
        document["start_at"] = "2026-01-05T08:00:00+08:00"

    engine = _custom_engine("white-collar-12-weeks.json", lunch, tmp_path, "meal-checkpoint")
    try:
        stage_day_plan(engine)
        _, trip = _start(engine, "travel", "location", RESTAURANT, "步行")
        _run_out(engine, trip.activity_id)
        location = next(item for item in engine.context.scenario.locations if item.location_id == RESTAURANT)
        opening = engine.context.state.current_time
        if opening_status(location, opening, engine.context.scenario.timezone) == "closed":
            opening = next_opening_change(location, opening, engine.context.scenario.timezone)
        assert opening is not None
        for step in range(16):
            if engine.context.state.current_time >= opening:
                break
            _, waiting = _start(engine, f"opening-{step}", "self", None, "等待")
            _run_out(engine, waiting.activity_id)
        assert engine.context.state.current_time >= opening
        stock = engine.context.state.supply_stock[SUPPLY]
        balance = engine.context.state.accounts["agent:1"]
        _, bought = _start(engine, "buy", "supply", SUPPLY, "到店")
        paid = bought.data["orders"][0]["total_price_fen"]
        assert advance_until_ready(engine)
        duration = engine.context.rules.products[MEAL].use_duration_seconds
        _, meal = _start(engine, "eat", "holding", MEAL, "食用")
        halfway = wake_by_mail(engine, offset=duration // 2)
        while engine.context.state.current_time < halfway:
            advance_next(engine)
        eaten = engine.context.state.activities[meal.activity_id].data["consumed"][MEAL]
        assert 0 < eaten < 1
        _action(engine, "pause", "pause_activity", pause_request(
            activity_name(engine, meal.activity_id), summary="这顿饭吃到一半，先停一下。"))
        # 暂停后没有进行中活动：同一秒开始等待，时钟才推进（时间与能量规格第一章规则 1、2）；暂停的餐食仍占一个名额。
        _, waited = _start(engine, "wait-paused-meal", "self", None, "等待")
        restaurant = sessions.current_session(engine.context.state, 1)
        assert (restaurant.kind, restaurant.location_id) == (SessionKind.STAY, RESTAURANT)
        assert {meal.activity_id, waited.activity_id} <= set(restaurant.activity_ids)
        wake = wake_by_mail(engine, offset=60)
        snapshot = engine.context.state.model_dump(mode="json")
        run_dir = engine.context.store.run_dir
        snapshots = {"agent:1": engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "meal_test"})}
        engine.checkpoint(snapshots)
        cutoff = engine.context.store.last_event_seq
        facts_before = project_verified_experiences(engine.context.store.read_events(cutoff), agent_id=1, cutoff_seq=cutoff)
        engine.close()
        engine = LifeEngine.open(tmp_path / "scenarios" / "custom.json", run_dir)
        assert engine.context.state.model_dump(mode="json") == snapshot
        while engine.context.state.current_time < wake:
            advance_next(engine)
        assert engine.context.state.activities[meal.activity_id].data["consumed"][MEAL] == eaten
        _action(engine, "end-wait", "conclude_activity", conclude_request(
            activity_name(engine, waited.activity_id), summary="不等了，回去吃饭。"))
        _action(engine, "resume", "report_status", report_request(
            activity_name(engine, meal.activity_id), summary="继续吃完这顿饭。"))
        _run_out(engine, meal.activity_id)
        assert engine.context.state.activities[meal.activity_id].data["consumed"][MEAL] == 1
        assert engine.context.state.agents[1].inventory[RESTAURANT][MEAL] == pytest.approx(0)
        assert engine.context.state.supply_stock[SUPPLY] == stock - 1
        assert engine.context.state.accounts["agent:1"] == balance - paid
        assert len(engine.context.state.deliveries) == 1
        events = engine.context.store.read_events(engine.context.store.last_event_seq)
        facts = project_verified_experiences(events, agent_id=1, cutoff_seq=events[-1].event_seq)
        assert facts[:len(facts_before)] == facts_before
        consumed = [row for row in facts if row["kind"] == "consumed"]
        assert sum(row["interval_quantity"] for row in consumed) == pytest.approx(1)
        assert consumed[-1]["cumulative_quantity"] == 1
        progresses = [row for row in events if row.event_type == "activity_progress"
                      and row.activity_id == meal.activity_id]
        assert sum(row.data["interval_end"] - row.data["interval_start"] for row in progresses) == duration
        energy_gains = [flow["amount"] for row in progresses for flow in row.data.get("resource_flows", [])
                        if flow["resource"] == "energy_kcal" and flow["cause"] == "food:intake"]
        expected = sum(row["interval_quantity"] * next(
            event.data["consumption"]["food_efficiency"] for event in progresses
            if event.event_id == row["source_event_id"]) for row in consumed)
        assert sum(energy_gains) == pytest.approx(expected * engine.context.rules.products[MEAL].energy_kcal_per_unit)
        lifecycle = [row.data["kind"] for row in events if row.event_type == "environment_event"
                     and row.activity_id == meal.activity_id]
        assert lifecycle == ["activity_paused", "activity_resumed", "activity_concluded"]
        snapshots = {"agent:1": engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "meal_test"})}
        engine.checkpoint(snapshots)
        export_run(engine.context.store, engine.context.store.last_event_seq, tmp_path / "export")
        assert (tmp_path / "export" / "verified-experiences.jsonl").is_file()
        exported = {row["activity_id"]: row for row in map(
            json.loads, (tmp_path / "export" / "activities.jsonl").read_text(encoding="utf-8").splitlines())}
        final = engine.context.state.activities
        assert [(exported[key]["status"], exported[key]["concluded_at"]) for key in (meal.activity_id,
                                                                                     waited.activity_id)] == [
            (ActivityStatus.COMPLETED, final[meal.activity_id].concluded_at),
            (ActivityStatus.CANCELLED, final[waited.activity_id].concluded_at)]
        assert final[waited.activity_id].interruption_reason == "agent_cancelled"
    finally:
        engine.close()
