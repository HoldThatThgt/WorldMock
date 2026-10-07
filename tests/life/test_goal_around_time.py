"""当日目标的大致时刻：真实引擎结算中只有在大致时刻前后进行的同家族活动才推进目标（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进 ）。

零点计划（确定性桩）登记带 around_time 的目标，人物在真实 LifeEngine 里睡觉、浏览或进食，边界结算按活动的实际开始时刻
与结算止点判定推进；同一份事件经 evaluation.goal_metrics 回放，目标推进与引擎一致（同一判定）。
"""

from __future__ import annotations

import pytest

from tests.life.conftest import StubPlanDayClient, advance_until_ready, settle_day_plans, start_activity, wake_by_mail, conclude_request
from tests.life.routing_fixtures import run_activity_out, buy_goods, run_until
from tests.life.test_day_planning import _at, _goal, _start
from tests.life.test_same_moment_decisions import _act
from tests.life.test_memory_layer import _late_memory_engine
from worldmock.evaluation.goal_metrics import day_plan_metrics
from worldmock.life.engine import LifeEngine

#: goal_metrics 回放时注入的代码摘要（质量导出从冻结清单注入，这里只作分组键）。
_CODE = "c" * 64


def _metrics_goals(engine: LifeEngine) -> dict[tuple[str, str], dict]:
    """同一份真实事件经 goal_metrics 回放出的全部目标实例，按（意图，文字）索引。"""
    events = [{**event.model_dump(mode="json"), "code_sha256": _CODE}
              for event in engine.context.store.read_events(engine.context.store.last_event_seq)]
    days = day_plan_metrics(events, engine.context.scenario.timezone)[_CODE]["deliberate_goals"]["per_agent_day"]
    return {(goal["intent_kind"], goal["goal"]): goal for day in days.values() for goal in day["goals"]}


def _assert_metrics_agree(engine: LifeEngine) -> None:
    """goal_metrics 对当前计划每个目标的推进结论（状态、推进时刻、活动）与引擎的推进证据相同。"""
    replayed = _metrics_goals(engine)
    for goal in engine.context.state.agents[1].day_plan.goals:
        instance = replayed[(goal.intent_kind, goal.goal)]
        assert instance["around_at"] == goal.around_at
        assert (instance["status"] == "progressed") is (goal.progressed_at is not None)
        assert (instance["progressed_at"], instance["progress_activity_id"]) == (
            goal.progressed_at, goal.progress_activity_id)


@pytest.mark.parametrize(("around", "progressed_at"), [
    ("23:30", None), (None, _at(6, 0, 15)), ("00:30", _at(6, 0, 15)),
])
def test_sleep_begun_before_midnight_does_not_progress_a_sleep_goal_around_2330(tmp_path, around, progressed_at):
    """零点前开始、零点后结算的睡眠不推进大致时刻为 23:30 的睡眠目标：它在当日内的进行区间是 00:00 起的那一段，与
    [22:00, 24:00) 没有交集；不带大致时刻的目标沿用现行判定，被零点后的第一段结算推进；大致时刻 00:30 时这段睡眠正在
    窗口内，照样推进。"""
    engine = _late_memory_engine(tmp_path, "2026-01-05T23:45:00+08:00", "around-sleep")
    try:
        settle_day_plans(engine)
        slept = _start(engine, "睡觉", "bedtime")
        boundary = engine.scheduling_state().next_event_at
        engine.advance(int(boundary.timestamp()) - engine.context.state.current_time, boundary)
        settle_day_plans(engine, StubPlanDayClient(goals=[_goal("睡眠", "照平常的点睡", None, around)]))
        [goal] = engine.context.state.agents[1].day_plan.goals
        assert goal.around_at == (None if around is None else _at(6, *map(int, around.split(":"))))
        assert advance_until_ready(engine) and engine.context.state.activities[slept].status == "completed"
        assert engine.context.state.activities[slept].started_at == _at(5, 23, 45)
        [goal] = engine.context.state.agents[1].day_plan.goals
        assert goal.progressed_at == progressed_at
        assert goal.progress_activity_id == (slept if progressed_at else None)
        _assert_metrics_agree(engine)
    finally:
        engine.close()


@pytest.mark.parametrize(("around", "progressed"), [("23:00", False), (None, True), ("09:00", True)])
def test_morning_app_use_does_not_progress_an_evening_entertainment_goal(tmp_path, around, progressed):
    """08:40 使用应用不推进大致时刻为 23:00 的娱乐目标；不带大致时刻或大致时刻为 09:00 时则推进。"""
    engine = _late_memory_engine(tmp_path, "2026-01-05T08:40:00+08:00", "around-app")
    try:
        settle_day_plans(engine, StubPlanDayClient(goals=[_goal("娱乐", "睡前刷会儿", None, around)]))
        app = _act(engine, "app", "start_activity", start_activity("进行", "刷推荐", app=["抖音"]))
        assert app.status == "in_progress", app.error_code
        wake_by_mail(engine, offset=120)
        assert advance_until_ready(engine)
        name = engine.context.state.activities[app.activity_id].name
        assert _act(engine, "app-end", "conclude_activity", conclude_request(name)).error_code is None
        activity = engine.context.state.activities[app.activity_id]
        assert activity.started_at == _at(5, 8, 40) and activity.ended_at < _at(5, 21, 30)
        [goal] = engine.context.state.agents[1].day_plan.goals
        assert (goal.progress_activity_id == app.activity_id) is progressed
        _assert_metrics_agree(engine)
    finally:
        engine.close()


@pytest.mark.parametrize(("start", "progressed"), [("10:20", True), ("09:30", False)])
def test_a_lunch_goal_around_noon_is_progressed_by_an_actual_receipt_inside_the_window(tmp_path, start, progressed):
    """@brief 午餐只按本人真实收货时刻推进，实际订单与评估回放在时间窗内外得到同一结论。"""
    engine = _late_memory_engine(tmp_path, f"2026-01-05T{start}:00+08:00", "around-lunch")
    try:
        settle_day_plans(engine, StubPlanDayClient(goals=[_goal("用餐", "午饭在十二点前后吃", None, "12:00")]))
        bought = buy_goods(engine, "lunch", goods_id="beef-noodles", app="美团", pay_app="支付宝")
        assert bought.error_code is None, bought.data
        [order] = bought.data["orders"]
        run_until(engine, lambda current: current.context.state.deliveries[order["order_id"]].status == "delivered")
        [goal] = engine.context.state.agents[1].day_plan.goals
        assert goal.around_at == _at(5, 12)
        assert goal.progressed_at == (order["expected_at"] if progressed else None)
        assert goal.progress_activity_id is None
        _assert_metrics_agree(engine)
    finally:
        engine.close()
