"""实际餐食被中断并恢复后，付款、库存和食用事实不重复。"""

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.life.conftest import advance_next, advance_until_ready, stage_day_plan, wake_by_mail
from tests.life.test_dayrun_restaurants import RESTAURANT, MEAL, SUPPLY, _action, _finish, _start
from tests.life.test_scheduling import _custom_engine
from worldmock.footprints.experiences import project_verified_experiences
from worldmock.footprints.export import export_run
from worldmock.life.engine import LifeEngine
from worldmock.life.models import PauseActivityRequest, ReportStatusRequest


def test_paid_meal_pause_checkpoint_resume_preserves_actual_consumption(tmp_path: Path):
    """经真实购买与消费入口，恢复只吃剩余量，消费分录与投影严格守恒。"""
    def lunch(document):
        document["start_at"] = "2026-01-05T08:00:00+08:00"

    engine = _custom_engine("white-collar-12-weeks.json", lunch, tmp_path, "meal-checkpoint")
    try:
        stage_day_plan(engine)
        _, trip = _start(engine, "travel", "location", RESTAURANT, "步行前往")
        _finish(engine, trip.activity_id)
        opening = int(datetime(2026, 1, 5, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        for step in range(16):
            if engine.state.current_time >= opening:
                break
            _, waiting = _start(engine, f"opening-{step}", "self", None, "等待")
            _finish(engine, waiting.activity_id)
        assert engine.state.current_time >= opening
        stock = engine.state.supply_stock[SUPPLY]
        balance = engine.state.accounts["agent:1"]
        _, bought = _start(engine, "buy", "supply", SUPPLY, "到店购买")
        paid = bought.data["orders"][0]["total_price_fen"]
        assert advance_until_ready(engine)
        duration = engine.rules.products[MEAL].use_duration_seconds
        _, meal = _start(engine, "eat", "holding", MEAL, "食用")
        halfway = wake_by_mail(engine, offset=duration // 2)
        while engine.state.current_time < halfway:
            advance_next(engine)
        eaten = engine.state.activities[meal.activity_id].data["consumed"][MEAL]
        assert 0 < eaten < 1
        session = next(item for item in engine.state.sessions.values()
                       if meal.activity_id in item.activity_ids)
        _action(engine, "pause", "pause_activity", PauseActivityRequest(
            task=[session.session_name], summary=["这顿饭吃到一半，先停一下。"]))
        wake = wake_by_mail(engine, offset=60)
        snapshot = engine.state.model_dump(mode="json")
        run_dir = engine.store.run_dir
        snapshots = {"agent:1": engine.store.assets.put_json({"agent_id": 1}, {"kind": "meal_test"})}
        engine.checkpoint(snapshots)
        cutoff = engine.store.last_event_seq
        facts_before = project_verified_experiences(engine.store.read_events(cutoff), agent_id=1, cutoff_seq=cutoff)
        engine.close()
        engine = LifeEngine.open(tmp_path / "scenarios" / "custom.json", run_dir)
        assert engine.state.model_dump(mode="json") == snapshot
        while engine.state.current_time < wake:
            advance_next(engine)
        assert engine.state.activities[meal.activity_id].data["consumed"][MEAL] == eaten
        _action(engine, "resume", "report_status", ReportStatusRequest(
            task=[session.session_name], summary=["继续吃完这顿饭。"]))
        _finish(engine, meal.activity_id)
        assert engine.state.activities[meal.activity_id].data["consumed"][MEAL] == 1
        assert engine.state.agents[1].inventory[RESTAURANT][MEAL] == pytest.approx(0)
        assert engine.state.supply_stock[SUPPLY] == stock - 1
        assert engine.state.accounts["agent:1"] == balance - paid
        assert len(engine.state.deliveries) == 1
        events = engine.store.read_events(engine.store.last_event_seq)
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
        assert sum(energy_gains) == pytest.approx(expected * engine.rules.products[MEAL].energy_kcal_per_unit)
        snapshots = {"agent:1": engine.store.assets.put_json({"agent_id": 1}, {"kind": "meal_test"})}
        engine.checkpoint(snapshots)
        export_run(engine.store, engine.store.last_event_seq, tmp_path / "export")
        assert (tmp_path / "export" / "verified-experiences.jsonl").is_file()
    finally:
        engine.close()
