"""快做完的活动不能暂停：能否暂停的唯一判定、拒绝的文字，以及各处「下一步」写不写暂停。

手工世界的当前时刻是 100；槽位占用排在对象条件之前，经 group_refusal 直接核对。
"""

from __future__ import annotations

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL
from tests.unit.test_environment_engine_admission import (
    app_call,
    conflict,
    occupied,
    place_call,
    refusal,
    running,
    sleep_call,
    travel_call,
    world,
)
from worldmock.simulation_engine.activities.lifecycle.activity_control import open_activities
from worldmock.simulation_engine.activities.lifecycle import activity_control
from worldmock.simulation_engine.actions.admission import Blocked, Context
from worldmock.simulation_engine.observation.wording.guidance import Refusal, refusal_for_code
from worldmock.state.world.kinds import ActivityKind, ActivityStatus
from worldmock.state.world.activity import ActivityState

_NAME = "进行测试休息（1月5日 08:00 起）"
_PLACES = {"home": "Home", "store": "Store"}


def _ending_in(seconds: int, *, presence: bool = True, **update) -> ActivityState:
    """一项进行中的 interact，离计划结束还有给定秒数；presence 为真是在地点做事（槽位 1），否则是用内容 app（槽位 2）。

    当前内容是样例环境里真实的独立内容：在地点做事是「测试休息」（c-rest），用内容 app 是 Keep 的「测试慢跑」（c-run），
    做完都不再接续。"""
    slot = "first" if presence else "second"
    activity = running("interact", ActivityKind.INTERACT, slot, presence=presence)
    data = {**activity.data, "content_id": "c-rest" if presence else "c-run"}
    return activity.model_copy(
        update={"planned_end_at": 100 + seconds, "planned_duration_seconds": seconds, "data": data, **update})


def _control(activity: ActivityState, tool: str, *, inputs=None) -> Refusal | None:
    """只有这一项活动的手工世界里，控制工具点名它时的准入结论；本轮活动名是 _NAME，inputs 是准入上下文的环境输入。"""
    state = occupied(activity)
    context = Context(state, 1, open_activities(state, 1), _PLACES, {activity.activity_id: _NAME}, inputs)
    return activity_control.control_refusal(context, tool, activity)


@pytest.mark.parametrize("presence", [True, False])
@pytest.mark.parametrize("seconds,expected", [(1, False), (60, False), (179, False), (180, True), (181, True),
                                              (900, True)])
def test_an_interaction_is_pausable_with_three_minutes_left_and_not_with_one_second_less(presence, seconds, expected):
    """在地点做事守 180 秒：离计划结束还剩 180 秒可以暂停，179 秒不行；用内容 app 随时可以暂停，剩多久都一样。"""
    assert activity_control.PAUSE_MIN_REMAINING_SECONDS == 180
    activity = _ending_in(seconds, presence=presence)
    assert activity_control.pausable(occupied(activity), activity) is (expected or not presence)


def test_the_remaining_time_is_measured_from_the_current_moment_to_the_planned_end():
    """同一项 15 分钟的活动（计划结束在 1000）：时钟走到 820 还能暂停，821 起不能；恢复时重排的计划结束同样算。"""
    activity = running("interact", ActivityKind.INTERACT, "first", presence=True)
    states = {now: occupied(activity).model_copy(update={"current_time": now}) for now in (100, 820, 821, 999)}
    assert [activity_control.pausable(state, activity) for state in states.values()] == [True, True, False, False]
    resumed = activity.model_copy(update={"planned_end_at": 821 + 180})
    assert activity_control.pausable(states[821], resumed) is True


@pytest.mark.parametrize("status", [ActivityStatus.PAUSED, ActivityStatus.COMPLETED, ActivityStatus.CANCELLED,
                                    ActivityStatus.FAILED, ActivityStatus.TRUNCATED])
def test_paused_and_ended_interactions_are_not_pausable_however_much_time_is_left(status):
    """已暂停或已结束的活动不能暂停，剩多久都一样。"""
    activity = _ending_in(900, status=status)
    assert activity_control.pausable(occupied(activity), activity) is False


@pytest.mark.parametrize("action,kind,slot", [("travel", ActivityKind.TRAVEL, "first"),
                                              ("sleep", ActivityKind.SLEEP, "first"),
                                              ("purchase", ActivityKind.PURCHASE, "second"),
                                              ("query", ActivityKind.QUERY, "second")])
def test_travel_sleep_purchase_and_query_are_never_pausable(action, kind, slot):
    """出行、睡眠、购买和查询不能暂停，拒绝仍是该码的通用说法，与剩余时间无关。"""
    activity = running(action, kind, slot)
    assert activity.planned_end_at - 100 == 900
    assert activity_control.pausable(occupied(activity), activity) is False
    assert _control(activity, "pause_activity") == refusal_for_code("ACTIVITY_PAUSE_UNAVAILABLE")


def test_pause_refusal_names_the_activity_and_says_it_ends_in_under_three_minutes(frozen_inputs):
    """不足三分钟的暂停拒绝：现有的码，原因点名活动并说明不足三分钟，下一步与改法都是结束它或等它做完；恰好三分钟时
    暂停成立。结束不受这条限制；已暂停的活动再暂停仍按请求不合约定拒绝。当前内容是独立内容，做完不再接续。"""
    inputs = frozen_inputs.record(ENVIRONMENT_ROOT)["inputs"]
    refused = _control(_ending_in(179), "pause_activity", inputs=inputs)
    assert refused == Refusal("ACTIVITY_PAUSE_UNAVAILABLE",
                              f"The activity {_NAME} ends in under three minutes and cannot be paused.",
                              f"Conclude {_NAME} or let it finish.",
                              fix="Conclude this activity or let it finish instead.")
    assert _control(_ending_in(179, presence=False), "pause_activity", inputs=inputs) is None
    assert _control(_ending_in(180), "pause_activity") is None
    assert _control(_ending_in(179), "conclude_activity") is None
    assert _control(_ending_in(1), "conclude_activity") is None
    assert _control(_ending_in(179), "report_status") is None
    paused = _control(_ending_in(900, status=ActivityStatus.PAUSED), "pause_activity")
    assert paused == refusal_for_code("ACTION_REQUEST_INVALID")


@pytest.mark.parametrize("call", [travel_call(), place_call(), sleep_call()])
def test_slot_conflict_offers_pausing_only_while_the_occupant_can_still_be_paused(call):
    """同槽位占用的下一步读同一个判定：占用者离结束不足三分钟时只写结束，恰好三分钟时写结束或暂停。"""
    short = occupied(_ending_in(179))
    assert refusal(short, [call]) == Blocked(conflict("first", "Conclude"), (MAIN_TOOL,))
    named = refusal(short, [call], {"active": _NAME})
    assert named == Blocked(conflict("first", "Conclude", _NAME), (MAIN_TOOL,))
    assert refusal(occupied(_ending_in(180)), [call]) == Blocked(conflict("first", "Conclude or pause"), (MAIN_TOOL,))


def test_second_slot_conflict_follows_the_same_limit():
    """用内容 app 时再开一个 app：app 使用随时可以暂停，离这一条结束还剩多久下一步都写结束或暂停。"""
    assert refusal(occupied(_ending_in(179, presence=False)), [app_call()]) == Blocked(
        conflict("second", "Conclude or pause"), (SIDE_TOOL,))
    assert refusal(occupied(_ending_in(180, presence=False)), [app_call()]) == Blocked(
        conflict("second", "Conclude or pause"), (SIDE_TOOL,))


@pytest.mark.parametrize("seconds,step", [(1, "Conclude or pause"), (179, "Conclude or pause"),
                                          (180, "Conclude or pause"), (900, "Conclude or pause")])
def test_sleep_constraint_offers_pausing_only_while_the_second_slot_occupant_can_still_be_paused(seconds, step):
    """槽位 2 的 app 使用还在放时去睡觉：睡眠约束的下一步读同一个判定，app 使用随时可以暂停，所以剩多久都写结束或暂停。"""
    blocked = refusal(occupied(_ending_in(seconds, presence=False)), [sleep_call()])
    assert blocked == Blocked(Refusal("RESOURCE_CONFLICT", "Sleep requires the second activity slot to be empty.",
                                      f"{step} the second activity first."), (MAIN_TOOL,))


def test_slot_limit_next_step_only_names_occupants_that_can_still_be_paused():
    """槽位不够的下一步：都能暂停时写结束或暂停，快做完的那一项只能结束，混合时暂停只点名还能暂停的。"""
    ending = _ending_in(179).model_copy(update={"name": "快做完"})
    lasting = _ending_in(900, presence=False).model_copy(update={"activity_id": "other", "name": "还早"})
    state = world()
    state.activities = {ending.activity_id: ending}
    assert activity_control.slot_refusal(state, open_activities(state, 1), 2, {}).next_step == "Conclude one of 快做完 first."
    state.activities = {lasting.activity_id: lasting}
    assert activity_control.slot_refusal(state, open_activities(state, 1), 2, {}).next_step == "Conclude or pause one of 还早 first."
    state.activities = {ending.activity_id: ending, lasting.activity_id: lasting}
    both = activity_control.slot_refusal(state, open_activities(state, 1), 1, {})
    assert both.next_step == "Conclude one of 快做完、还早, or pause 还早, first."


def test_resume_conflict_offers_pausing_only_while_the_running_activity_can_still_be_paused():
    """恢复暂停的活动而原槽位有进行中的活动：下一步同样读这个判定。"""
    held = _ending_in(900, status=ActivityStatus.PAUSED).model_copy(update={"activity_id": "held", "name": "Held"})
    for seconds, step in ((179, "Conclude"), (180, "Conclude or pause")):
        state = occupied(_ending_in(seconds))
        state.activities[held.activity_id] = held
        blocked = activity_control.control_refusal(Context(state, 1, open_activities(state, 1), _PLACES, {}), "report_status", held)
        assert blocked == Refusal("RESOURCE_CONFLICT", "This activity conflicts with the running activities Activity.",
                                  f"{step} Activity first.", ("resource_claims",))
