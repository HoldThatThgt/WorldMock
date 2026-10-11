"""手机记录跨共同检查点：导航在前台的中途、购买付款段的中途写检查点，恢复或重放的分支接着写出的前台区间
与不间断的父运行相同，检查点之前的不重写，已经做过的抽取不重做。"""

import pytest

from tests.integration.runtime_fixtures import branch_of
from tests.life.conftest import advance_until_ready, purchase, stage_day_plan, travel, use_app
from tests.life.current_world import stage_moment
from tests.life.environment_runtime import act, reach
from tests.life.phone_records import (foreground, moment, notices, phone_environment, phone_window,
                                      returning_label)
from worldmock.life.purchase_phases import ORDERING, PAYMENT, activity_phases


def at_store(persona, config):
    """人物从四季便利开始：纸巾当场买，牛肉面走外卖，平台笔记本走快递。"""
    persona["initial_location"] = "四季便利"


@pytest.mark.parametrize("kind", ["resume", "replay"])
def test_branch_from_a_checkpoint_while_navigation_is_in_front_keeps_the_open_interval(
        engine_factory, tmp_path, kind):
    """路上用完 app、导航抽中回到前台之后写共同检查点：分支沿用人物状态里记下的那一段，到达时写出的区间与
    不间断的父运行相同，回到前台的那一次抽取不重做。"""
    parent = engine_factory(phone_environment(tmp_path / "input"),
                            mutate=phone_window((8, 0), 2, progress_report_interval_seconds=660))
    stage_moment(parent, moment(8, 20))
    observed, trip = act(parent, "commute", travel("公司", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    assert advance_until_ready(parent) and parent.context.state.current_time == moment(8, 31)
    _, watching = act(parent, returning_label(parent, "commute", "watch", True), use_app("哔哩哔哩"))
    assert watching.status == "in_progress", watching.error_code
    reach(parent, moment(8, 40))
    opened = parent.context.state.agents[1].navigation_foreground
    assert opened.model_dump() == {"activity_id": trip.activity_id, "app_id": "amap", "opened_at": moment(8, 40)}
    before = foreground(parent)
    assert before == [("amap", "navigation", moment(8, 20), moment(8, 31))]
    child = branch_of(parent, tmp_path, kind, observed["observation_event_id"])
    try:
        assert child.context.state.agents[1].navigation_foreground == opened
        assert foreground(child) == before
        reach(parent, moment(8, 50))
        reach(child, moment(8, 50))
        whole = [*before, ("amap", "navigation", moment(8, 40), moment(8, 50))]
        assert foreground(parent) == foreground(child) == whole
        assert parent.context.state.agents[1].navigation_foreground is None
        assert child.context.state.agents[1].navigation_foreground is None
        assert child.context.state.activities[trip.activity_id].status == "completed"
    finally:
        child.close()


@pytest.mark.parametrize("kind", ["resume", "replay"])
def test_branch_from_a_checkpoint_inside_a_payment_phase_writes_the_same_intervals_and_notices(
        engine_factory, tmp_path, kind):
    """购买的第一个付款段刚开始时写共同检查点：之前写下的下单区间在分支里不重写，之后的支付区间和付款那一秒
    的通知与不间断的父运行逐条相同；各段由受理时冻结的付款方式和付款时长算出，分支不重抽。"""
    parent = engine_factory(mutate=at_store)
    stage_day_plan(parent)
    observed, result = act(parent, "three", purchase("纸巾", "牛肉面", "平台笔记本"))
    assert result.status == "in_progress", result.error_code
    activity = parent.context.state.activities[result.activity_id]
    phases = activity_phases(parent.context.state, activity)
    ordering = [phase for phase in phases if phase.step == ORDERING]
    paying = [phase for phase in phases if phase.step == PAYMENT]
    assert ordering and len(paying) >= 2 and paying[0].started_at > activity.started_at
    reach(parent, paying[0].started_at)
    # 检查点落在第一个付款段里：下单段都已写出，付款段和通知一条都还没有。
    before = foreground(parent, result.activity_id)
    assert before == [(phase.app_id, ORDERING, phase.started_at, phase.ended_at) for phase in ordering]
    assert notices(parent) == []
    child = branch_of(parent, tmp_path, kind, observed["observation_event_id"])
    try:
        assert foreground(child) == foreground(parent) == before
        assert activity_phases(child.context.state, child.context.state.activities[result.activity_id]) == phases
        reach(parent, activity.planned_end_at)
        reach(child, activity.planned_end_at)
        assert child.context.state.activities[result.activity_id].status == "completed"
        whole = [(phase.app_id, phase.step, phase.started_at, phase.ended_at) for phase in phases]
        assert foreground(parent) == foreground(child) == whole
        sent = notices(parent)
        assert notices(child) == sent and {row[0] for row in sent} == {activity.planned_end_at}
        assert {row[1] for row in sent} == {"订单已下单", "支付成功"}
    finally:
        child.close()
