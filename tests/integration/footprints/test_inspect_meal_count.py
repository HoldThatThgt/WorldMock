"""检查工具 rerun 的两项进食指标：在地点吃的一餐与食品收货同算进食，读同一组进食时刻
（docs/design/05-recording-engine.md「检查工具」）。

都经真实请求、引擎与 SQLite：样例环境副本 meal_environment 里「测试休息」写了 completion_effects.food，做完供一餐。
"""

from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scripts import inspect_run
from tests.life.conftest import advance_until_ready, do_at, purchase
from tests.life.environment_inputs import meal_environment, set_content
from tests.life.environment_runtime import act, events, every_five_minutes, finish_activity, reach, until_ended

#: rerun 报告里当日没有进食的标记。
NO_MEAL = "（当日无进食）"


def _rerun(engine, capsys):
    """只读打开真实运行库：返回复验指标、在地点吃的一餐与 rerun 打印的各行；运行库前后没有新事件。"""
    store = engine.context.store
    point = store.last_event_seq
    with inspect_run._connect(store.run_dir) as conn:
        metrics = inspect_run.rerun_metrics(conn)
        meals = inspect_run._location_meals(conn)
        inspect_run.cmd_rerun(conn, argparse.Namespace())
    assert store.last_event_seq == point
    return metrics, meals, capsys.readouterr().out.splitlines()


def _start_rest(engine):
    """在家点名「测试休息」；返回开始结果。"""
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    return rest


def _finished(engine, activity_id):
    """这项活动做到末端的那一条 activity_progress。"""
    [done] = [row for row in events(engine, "activity_progress")
              if row.activity_id == activity_id and row.data["content"]["completed"]]
    return done


def _lowest_until(engine, moment):
    """人物 1 在 moment 所在的本地日、不晚于 moment 的共同身体结算能量点（起点取 before、终点取 after）里的最低值。"""
    zone = ZoneInfo(engine.context.config.timezone)
    day = datetime.fromtimestamp(moment, zone).date()
    points = []
    for row in events(engine, "environment_event"):
        if row.data.get("kind") != "body_settlement" or row.agent_id != 1:
            continue
        settlement = row.data["body_settlement"]
        points += [(row.data["interval_start"], settlement["before"]["energy_kcal"]),
                   (row.data["interval_end"], settlement["after"]["energy_kcal"])]
    return min(energy for at, energy in points if at <= moment and datetime.fromtimestamp(at, zone).date() == day)


@pytest.mark.parametrize("effects,count", [
    ({"food": "meal"}, 2), ({"food": "snack"}, 2), ({"relaxation_relief": "low"}, 1),
], ids=["meal", "snack", "relief-only"])
def test_meal_count_adds_location_meals_to_food_receipts(engine_factory, tmp_path, capsys, effects, count):
    """同一次运行里点一份牛肉面并等到送达，在家做完「测试休息」：它的 food 写哪个档位都算一次进食，进食次数为 2；
    只写缓解、没写 food 时只有那份外卖，为 1。首次进食前最低能量取收货与这一餐中较早的那一刻之前的结算能量点。"""
    root = meal_environment(tmp_path / "meal")
    set_content(root, "c-rest", completion_effects=effects)
    engine = engine_factory(root)
    _, bought = act(engine, "noodles", purchase("牛肉面"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, "noodles")
    (order,) = engine.context.state.deliveries.values()
    rest = _start_rest(engine)
    until_ended(engine, rest.activity_id)
    reach(engine, order.expected_at)
    [received] = [row for row in events(engine, "environment_event") if row.data.get("kind") == "goods_received"]
    done = _finished(engine, rest.activity_id)
    metrics, meals, lines = _rerun(engine, capsys)
    assert metrics["meal_count"] == count and f"6. 进食次数：{count}" in lines
    assert meals == ([(1, done.occurred_at)] if "food" in effects else [])
    first = min([received.occurred_at, *(moment for _, moment in meals)])
    [row] = metrics["lowest_energy_before_meal"]
    assert row["ate"] is True and row["energy_kcal"] == _lowest_until(engine, first)


def test_a_day_with_only_a_location_meal_counts_as_eating(engine_factory, tmp_path, capsys):
    """只在家做完「测试休息」、没有任何收货的一天：做到一半时 rerun 把这一天写成「当日无进食」；做完之后进食次数为 1，
    这一天 ate 为真，最低能量取这一餐那一刻之前的结算能量点，rerun 的输出不再带「当日无进食」。"""
    engine = engine_factory(meal_environment(tmp_path / "meal"), mutate=every_five_minutes)
    rest = _start_rest(engine)
    assert advance_until_ready(engine)
    metrics, meals, lines = _rerun(engine, capsys)
    assert meals == [] and metrics["meal_count"] == 0
    assert [row["ate"] for row in metrics["lowest_energy_before_meal"]] == [False]
    assert any(line.startswith("5. ") and NO_MEAL in line for line in lines)
    until_ended(engine, rest.activity_id)
    done = _finished(engine, rest.activity_id)
    metrics, meals, lines = _rerun(engine, capsys)
    assert meals == [(1, done.occurred_at)] and metrics["meal_count"] == 1
    [row] = metrics["lowest_energy_before_meal"]
    assert row["ate"] is True and row["energy_kcal"] == _lowest_until(engine, done.occurred_at)
    assert not any(NO_MEAL in line for line in lines)
