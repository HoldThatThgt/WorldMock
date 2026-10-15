"""饭点的食欲加成只在饿了时才加（docs/design/02-exchange-engine.md「身体共同结算」）：都经真实引擎与 SQLite。

样例环境副本里「测试休息」改成两小时，人设改初始能量、运行配置把开始与截止移到同一整点；act 开始「测试休息」，
until_ended 推进（其间的决策只播报），读那一秒落库的共同身体结算记录与观察。样例环境的人物午饭 12:00–13:00、
晚饭 18:30–19:30，饥饿锚定热量 1,200 千卡。
"""

from __future__ import annotations

import pytest

from tests.life.conftest import do_at
from tests.life.environment_inputs import copy_environment, set_content
from tests.life.environment_runtime import act, events, until_ended

#: 一小时的秒数。
HOUR = 3600


def _rest_engine(engine_factory, tmp_path, hour, energy=None):
    """「测试休息」两小时的副本引擎：运行从 2026-01-05 的 hour 点开始（截止移到次日同一整点），energy 给出时改初始能量。"""
    root = copy_environment(tmp_path / "rest")
    set_content(root, "c-rest", duration_seconds=7200)

    def mutate(persona, config):
        """人设与运行配置：初始能量与开始钟点。"""
        if energy is not None:
            persona["body"]["initial_energy_kcal"] = energy
        config["start_time"] = config["start_time"].replace(hour=hour)
        config["stop_time"] = config["stop_time"].replace(hour=hour)

    return engine_factory(root, mutate=mutate)


def _rest_until_done(engine):
    """开始「测试休息」并推进到它做完；返回开始那一刻与开始时观察到的饥饿感受。"""
    start = engine.context.state.current_time
    observed, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    until_ended(engine, rest.activity_id)
    return start, observed["environment_layer"]["body"]["appetite"]


def _settlements(engine):
    """人物 1 每一秒的共同身体结算记录，按时刻索引。"""
    return {row.occurred_at: row.data for row in events(engine, "environment_event")
            if row.data.get("kind") == "body_settlement" and row.agent_id == 1}


def _appetite_observed_at(engine, moment):
    """那一秒落库的第一次观察里环境层的饥饿感受。"""
    return next(row.data["environment_layer"]["body"]["appetite"]
                for row in engine.context.store.read_agent_events("observation", 1) if row.occurred_at == moment)


@pytest.mark.parametrize("energy,woken,noon_appetite", [(1800.0, False, "不饿"), (1000.0, True, "饿")],
                         ids=["fed", "hungry"])
def test_crossing_the_lunch_window_edges_wakes_only_a_hungry_person(engine_factory, tmp_path, energy, woken,
                                                                     noon_appetite):
    """11:00 开始做两小时的「测试休息」：吃饱（1,800 千卡）的人跨过午饭时段的起点 12:00 与终点 13:00 时，两次共同身体
    结算的唤醒原因都不含 felt_band_change，12:00 读「不饿」；饿着（1,000 千卡）的人两次都含，12:00 读「饿」。"""
    engine = _rest_engine(engine_factory, tmp_path, 11, energy)
    start, _ = _rest_until_done(engine)
    settled = _settlements(engine)
    for moment in (start + HOUR, start + 2 * HOUR):
        assert ("felt_band_change" in settled[moment]["wake_reasons"]) is woken, moment
    assert _appetite_observed_at(engine, start + HOUR) == noon_appetite


def test_a_fed_person_gets_no_decision_at_the_end_of_the_dinner_window(engine_factory, tmp_path):
    """样例环境的初始能量，18:00 开始做两小时的「测试休息」：晚饭时段起点 18:30 的结算只因播报唤醒；终点 19:30 的结算
    不提供决策，没有唤醒原因。"""
    engine = _rest_engine(engine_factory, tmp_path, 18)
    start, _ = _rest_until_done(engine)
    settled = _settlements(engine)
    assert settled[start + HOUR // 2]["wake_reasons"] == ["progress_report_due"]
    assert (settled[start + 3 * HOUR // 2]["decision_offered"], settled[start + 3 * HOUR // 2]["wake_reasons"]) == (
        False, [])


def test_falling_below_the_anchor_inside_a_meal_window_is_a_felt_band_change(engine_factory, tmp_path):
    """能量 1,210 千卡，12:00 开始做「测试休息」：开始时读「不饿」；在午饭时段里能量跌破 1,200 千卡的那一秒（早于
    12:30 的播报）结算带 felt_band_change，结算后能量在 1,199 到 1,200 千卡之间，那一秒的观察读「有点想吃」。"""
    engine = _rest_engine(engine_factory, tmp_path, 12, 1210.0)
    start, appetite = _rest_until_done(engine)
    assert appetite == "不饿"
    settled = _settlements(engine)
    moment = min(at for at, row in settled.items() if at > start and "felt_band_change" in row["wake_reasons"])
    assert moment < start + HOUR // 2
    assert 1199 < settled[moment]["body_settlement"]["after"]["energy_kcal"] < 1200
    assert _appetite_observed_at(engine, moment) == "有点想吃"
