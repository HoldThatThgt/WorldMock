"""真实引擎里基础代谢按人物体重结算：已提交的身体结算事件、能量分录与饥饿带来的决策机会。"""

from __future__ import annotations

import pytest

from tests.life.conftest import advance_next, context, sleep, use_app
from tests.life.environment_runtime import act, events, finish_activity


def _body(mass_kg: float, energy_kcal: float | None = None):
    """夹具人设只换体重（必要时换初始能量），其余输入不变。"""
    def mutate(persona, config):
        persona["body"]["mass_kg"] = mass_kg
        if energy_kcal is not None:
            persona["body"]["initial_energy_kcal"] = energy_kcal
    return mutate


def _settlements(engine, activity_id: str) -> list[dict]:
    """某个活动进行期间已提交的身体结算事件正文。"""
    return [row.data for row in events(engine, "environment_event")
            if row.data.get("kind") == "body_settlement" and row.data["activity_ids"] == [activity_id]]


def _energy_drain(data: dict) -> list[float]:
    """一条身体结算事件里实际扣掉的能量分录。"""
    return [flow["amount"] for flow in data["resource_flows"]
            if flow["resource"] == "energy_kcal" and flow["direction"] == "drain"
            and flow["cause"] == "settle:shared:energy_kcal"]


@pytest.mark.parametrize("mass_kg", [55.0, 65.0, 70.0])
def test_committed_body_settlements_charge_the_body_mass_per_hour(engine_factory, mass_kg):
    """一段静坐强度的目录浏览和一段睡眠：每条已提交的 body_settlement 的基础代谢等于体重乘小时数、额外消耗为 0，
    能量分录等于基础加额外；睡眠不折减基础代谢。65 公斤夹具人物的数值就是修订前的每小时 65 千卡。"""
    engine = engine_factory(mutate=_body(mass_kg))
    assert engine.context.body.mass_kg == mass_kg
    _, browsing = act(engine, "browse", use_app("美团"))
    assert browsing.status == "in_progress", browsing.error_code
    finish_activity(engine, browsing, "browse")
    _, sleeping = act(engine, "sleep", sleep())
    assert sleeping.status == "in_progress", sleeping.error_code
    night = engine.context.state.activities[sleeping.activity_id]
    while engine.context.state.current_time < min(night.planned_end_at, night.started_at + 3 * 3600):
        advance_next(engine)

    idle, asleep = _settlements(engine, browsing.activity_id), _settlements(engine, sleeping.activity_id)
    assert sum(row["body_settlement"]["duration_seconds"] for row in idle) == 600
    assert sum(row["body_settlement"]["duration_seconds"] for row in asleep) >= 3 * 3600
    for row in (*idle, *asleep):
        settlement = row["body_settlement"]
        hours = settlement["duration_seconds"] / 3600
        assert settlement["basal_energy_kcal"] == mass_kg * hours
        assert settlement["extra_energy_kcal"] == 0
        assert _energy_drain(row) == [settlement["basal_energy_kcal"] + settlement["extra_energy_kcal"]]
        burned = settlement["before"]["energy_kcal"] - settlement["after"]["energy_kcal"]
        assert burned == pytest.approx(mass_kg * hours)
        if mass_kg == 65.0:
            assert settlement["basal_energy_kcal"] == 65 * hours
    elapsed = engine.context.state.current_time - int(engine.context.config.start_time.timestamp())
    assert engine.context.state.agents[1].body.energy_kcal == pytest.approx(1800 - mass_kg * elapsed / 3600)


def _first_hunger_wake(engine_factory, mass_kg: float) -> tuple[int, dict]:
    """相同起始能量、相同活动下第一次因体感分级变化得到决策机会的时刻（距开始的秒数）与当时的体感。"""
    engine = engine_factory(mutate=_body(mass_kg, 1075.0))
    observed, watching = act(engine, "watch", use_app("哔哩哔哩"))
    assert watching.status == "in_progress", watching.error_code
    assert observed["environment_layer"]["body"]["appetite"] == "不饿"
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    woken = [row for row in _settlements(engine, watching.activity_id) if row["decision_offered"]]
    assert [row["wake_reasons"] for row in woken] == [["felt_band_change"]]
    start = int(engine.context.config.start_time.timestamp())
    return woken[0]["interval_end"] - start, engine.observe(context(engine, "woken"))["environment_layer"]["body"]


def test_a_heavier_body_gets_its_hunger_decision_earlier(engine_factory):
    """相同起始能量、相同内容下，70 公斤人物因为「有点想吃」得到决策机会的时刻早于 55 公斤人物，65 公斤居中。"""
    waits = {mass_kg: _first_hunger_wake(engine_factory, mass_kg) for mass_kg in (55.0, 65.0, 70.0)}
    assert all(body["appetite"] == "有点想吃" for _, body in waits.values())
    assert waits[70.0][0] < waits[65.0][0] < waits[55.0][0]
    assert (waits[55.0][0], waits[70.0][0]) == (852, 671)
