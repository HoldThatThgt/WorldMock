"""当日目标的大致时刻：真实引擎结算中只有在大致时刻前后进行的同家族活动才推进目标（docs/dev/SPEC-dayrun-fidelity.md 8.2）。

零点计划（确定性桩）登记带 around_time 的目标，人物在真实 LifeEngine 里睡觉、浏览或进食，边界结算按活动的实际开始时刻
与结算止点判定推进；同一份事件经 evaluation.goal_metrics 回放，目标推进与引擎一致（同一判定）。
"""

from __future__ import annotations

import pytest

from tests.life.conftest import StubPlanDayClient, advance_until_ready, settle_day_plans, start_activity
from tests.life.test_day_planning import _at, _goal, _late_engine, _start
from tests.life.test_meal_outcome import _food, _run_out, _stage_holding, _start_eating
from tests.life.test_same_moment_decisions import _act
from worldmock.evaluation.goal_metrics import day_plan_metrics
from worldmock.life.engine import LifeEngine

#: goal_metrics 回放时注入的代码摘要（质量导出从冻结清单注入，这里只作分组键）。
_CODE = "c" * 64


def _metrics_goals(engine: LifeEngine) -> dict[tuple[str, str], dict]:
    """同一份真实事件经 goal_metrics 回放出的全部目标实例，按（意图，文字）索引。"""
    events = [{**event.model_dump(mode="json"), "code_sha256": _CODE}
              for event in engine.store.read_events(engine.store.last_event_seq)]
    days = day_plan_metrics(events, engine.scenario.timezone)[_CODE]["deliberate_goals"]["per_agent_day"]
    return {(goal["intent_kind"], goal["goal"]): goal for day in days.values() for goal in day["goals"]}


def _assert_metrics_agree(engine: LifeEngine) -> None:
    """goal_metrics 对当前计划每个目标的推进结论（状态、推进时刻、活动）与引擎的推进证据相同。"""
    replayed = _metrics_goals(engine)
    for goal in engine.state.agents[1].day_plan.goals:
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
    engine = _late_engine(tmp_path, "2026-01-05T23:45:00+08:00", "around-sleep")
    try:
        settle_day_plans(engine)
        slept = _start(engine, "睡觉", "bedtime")
        boundary = engine.scheduling_state().next_event_at
        engine.advance(int(boundary.timestamp()) - engine.state.current_time, boundary)
        settle_day_plans(engine, StubPlanDayClient(goals=[_goal("睡眠", "照平常的点睡", None, around)]))
        [goal] = engine.state.agents[1].day_plan.goals
        assert goal.around_at == (None if around is None else _at(6, *map(int, around.split(":"))))
        assert advance_until_ready(engine) and engine.state.activities[slept].status == "completed"
        assert engine.state.activities[slept].started_at == _at(5, 23, 45)
        [goal] = engine.state.agents[1].day_plan.goals
        assert goal.progressed_at == progressed_at
        assert goal.progress_activity_id == (slept if progressed_at else None)
        _assert_metrics_agree(engine)
    finally:
        engine.close()


@pytest.mark.parametrize(("around", "progressed"), [("23:00", False), (None, True), ("09:00", True)])
def test_a_morning_browse_does_not_progress_an_evening_entertainment_goal(tmp_path, around, progressed):
    """08:40 的浏览不推进大致时刻为 23:00 的娱乐目标（进行区间与 [21:30, 24:00) 没有交集）；不带大致时刻的目标照现行判定
    被它推进，大致时刻 09:00 的也被它推进。"""
    engine = _late_engine(tmp_path, "2026-01-05T08:40:00+08:00", "around-browse")
    try:
        settle_day_plans(engine, StubPlanDayClient(goals=[_goal("娱乐", "睡前刷会儿", None, around)]))
        browse = _act(engine, "browse", "start_activity", start_activity("浏览", "商品目录"))
        assert browse.status == "in_progress", browse.error_code
        _run_out(engine, browse.activity_id, "browse")
        activity = engine.state.activities[browse.activity_id]
        assert activity.started_at == _at(5, 8, 40) and activity.ended_at < _at(5, 21, 30)
        [goal] = engine.state.agents[1].day_plan.goals
        assert (goal.progress_activity_id == browse.activity_id) is progressed
        _assert_metrics_agree(engine)
    finally:
        engine.close()


@pytest.mark.parametrize(("start", "progressed_at"), [("10:20", _at(5, 10, 35)), ("09:30", None)])
def test_a_lunch_goal_around_noon_is_progressed_by_eating_from_1020_not_from_0930(tmp_path, start, progressed_at):
    """大致时刻 12:00 的午餐目标被 10:20–10:45 的进食推进（进行区间与 [10:30, 13:30] 有交集，推进落在第一段越过 10:30 的
    结算止点），被 09:30–09:55 的进食不推进。"""
    engine = _late_engine(tmp_path, f"2026-01-05T{start}:00+08:00", "around-lunch")
    try:
        settle_day_plans(engine, StubPlanDayClient(goals=[_goal("用餐", "午饭在十二点前后吃", None, "12:00")]))
        food = _food(engine)
        _stage_holding(engine, food.product_id, 1.0)
        _, eating = _start_eating(engine, food, "lunch")
        _run_out(engine, eating.activity_id, "lunch")
        meal = engine.state.activities[eating.activity_id]
        hour, minute = map(int, start.split(":"))
        assert (meal.started_at, meal.ended_at) == (_at(5, hour, minute), _at(5, hour, minute + 25))
        [goal] = engine.state.agents[1].day_plan.goals
        assert (goal.around_at, goal.progressed_at) == (_at(5, 12), progressed_at)
        _assert_metrics_agree(engine)
    finally:
        engine.close()
