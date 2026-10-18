"""导航 app 在不在前台：提交前后两个世界决定关闭、打开与一半概率的回到前台。"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.life.models import (ActivityState, AgentState, BodyState, NavigationForeground, WorldState,
                                   WORLD_SCHEMA_VERSION, active_side_activities)
from worldmock.life.navigation_foreground import foreground_event, navigation_app, settle_navigation
from worldmock.life.randomness import random_stream

#: 出发时刻：当地 2026-01-05 08:20:00。
DEPART = 1767572400
TRIP = "activity-action-drive"
CATALOG = SimpleNamespace(apps_by_travel_method={"driving": ("amap",), "walking": (), "bus": ("amap", "baidu-map")})


def activity(identity, action, started, now, **fields):
    """一项活动：出行占槽位 1，购买和用 app 占槽位 2；状态缺省为进行中，结算到 now。"""
    status, ended = fields.get("status", "active"), fields.get("ended_at")
    settled = now if ended is None else ended
    data = {"action": action, "accepted_event_id": f"accepted-{identity}"}
    if action == "interact":
        data["app_id"] = "bilibili"
    return ActivityState(
        activity_id=identity, agent_id=1, location_id="home", kind=action, status=status, started_at=started,
        ended_at=ended, planned_end_at=started + 1800, last_settled_at=settled, elapsed_seconds=settled - started,
        planned_duration_seconds=1800, destination_id="office" if action == "travel" else None,
        travel_method_id=fields.get("method", "driving") if action == "travel" else None,
        resource_claims={"first" if action == "travel" else "second"}, origin_action_id=identity,
        origin_decision_id="decision", origin_request_event_id="request", data=data,
        concluded_at=fields.get("concluded_at"))


def world(now, *activities, foreground=None):
    """一个只有唯一人物的世界；导航状态与活动集合按给定值写入并经世界校验。"""
    body = BodyState(energy_kcal=1800.0, sleep_pressure=0.2, sleep_deficit_hours=0.0, sleep_need_hours=7.5,
                     chronotype_midsleep=4.0, relaxation_need=0.2, social_need=0.2,
                     social_need_growth_per_hour=0.06)
    agent = AgentState(agent_id=1, name="林舟", location_id="home", balance_account_id="agent:1", body=body,
                       body_settled_at=now, next_decision_at=now, navigation_foreground=foreground)
    return WorldState(schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=now, end_time=DEPART + 86400,
                      run_id="navigation", timezone="Asia/Shanghai", persona_sha256="0" * 64,
                      config_sha256="1" * 64, agents={1: agent}, accounts={"agent:1": 0, "external:scenario": 0},
                      activities=list(activities), random_seed=7,
                      random_state="navigation-tests")


def later(state, now, *activities):
    """待提交的世界：时钟与活动换成给定值，人物的导航状态原样带过来，由规则去改。"""
    agent = state.agents[1].model_copy(update={"body_settled_at": now})
    return state.model_copy(update={"current_time": now, "agents": {1: agent},
                                    "activities": {item.activity_id: item for item in activities}})


def opened(at, trip=TRIP, app="amap"):
    return NavigationForeground(activity_id=trip, app_id=app, opened_at=at)


def returns(trip, side):
    """这一对出行与槽位 2 活动的那一位：键是 navigation-return:{出行活动}:{槽位 2 活动}。"""
    return random_stream("navigation-tests", f"navigation-return:{trip}:{side}").getrandbits(1) == 1


def side_named(wanted):
    """按固定种子挑一个槽位 2 活动的身份，使它停止执行时抽到给定的那一位。"""
    return next(f"activity-action-side-{index}" for index in range(64)
                if returns(TRIP, f"activity-action-side-{index}") is wanted)


def settled(before, after):
    state, events = settle_navigation(before, after, CATALOG)
    return state.agents[1].navigation_foreground, [
        (event.occurred_at, event.activity_id, event.data["app_id"], event.data["step"],
         event.data["interval_start"], event.data["interval_end"]) for event in events]


def test_departure_without_a_side_activity_opens_navigation_that_second_and_writes_nothing():
    before = world(DEPART)
    after = later(before, DEPART, activity(TRIP, "travel", DEPART, DEPART))
    assert settled(before, after) == (opened(DEPART), [])
    # 写下的世界通过校验，导航状态不在出行活动的私有数据里。
    committed = settle_navigation(before, after, CATALOG)[0]
    WorldState.model_validate(committed.model_dump(mode="json"))
    assert "navigation" not in str(committed.activities[TRIP].model_dump(mode="json"))


@pytest.mark.parametrize("already_running", [True, False])
def test_departure_with_a_side_activity_keeps_navigation_in_the_background_without_a_zero_interval(already_running):
    side = activity("activity-action-watch", "interact", DEPART - 300 if already_running else DEPART, DEPART)
    before = world(DEPART, side) if already_running else world(DEPART)
    after = later(before, DEPART, activity(TRIP, "travel", DEPART, DEPART), side)
    assert settled(before, after) == (None, [])


def test_starting_a_side_activity_on_the_road_closes_navigation_with_one_interval():
    now = DEPART + 660
    trip = activity(TRIP, "travel", DEPART, now)
    before = world(now, trip, foreground=opened(DEPART))
    after = later(before, now, trip, activity("activity-action-watch", "interact", now, now))
    assert settled(before, after) == (None, [(now, TRIP, "amap", "navigation", DEPART, now)])


@pytest.mark.parametrize("status", ["completed", "cancelled", "failed", "paused"])
@pytest.mark.parametrize("wanted", [True, False])
def test_a_side_activity_that_stops_on_the_road_draws_once_for_the_return(status, wanted):
    stop, side_id = DEPART + 1200, side_named(wanted)
    kind = "purchase" if status == "failed" else "interact"
    running = activity(side_id, kind, DEPART + 660, DEPART + 900)
    before = world(DEPART + 900, activity(TRIP, "travel", DEPART, DEPART + 900), running)
    ended = None if status == "paused" else stop
    stopped = activity(side_id, kind, DEPART + 660, stop, status=status, ended_at=ended)
    after = later(before, stop, activity(TRIP, "travel", DEPART, stop), stopped)
    foreground, events = settled(before, after)
    assert events == [] and foreground == (opened(stop) if wanted else None)
    # 同一运行种子、同一对活动身份重复计算，结果相同。
    assert settled(before, after) == (foreground, [])


def test_each_side_activity_has_its_own_key_and_a_missed_draw_is_followed_by_another():
    first, second = side_named(False), side_named(True)
    assert first != second and returns(TRIP, first) is False and returns(TRIP, second) is True
    stop = DEPART + 900
    trip = activity(TRIP, "travel", DEPART, stop)
    missed = activity(first, "interact", DEPART, stop, status="completed", ended_at=stop, concluded_at=stop)
    before = world(stop - 60, activity(TRIP, "travel", DEPART, stop - 60), activity(first, "interact", DEPART, stop - 60))
    assert settled(before, later(before, stop, trip, missed)) == (None, [])
    again = world(stop + 60, activity(TRIP, "travel", DEPART, stop + 60), missed,
                  activity(second, "interact", stop, stop + 60))
    done = activity(second, "interact", stop, stop + 120, status="completed", ended_at=stop + 120)
    after = later(again, stop + 120, activity(TRIP, "travel", DEPART, stop + 120), missed, done)
    assert settled(again, after) == (opened(stop + 120), [])


def test_starting_another_side_activity_in_the_second_of_the_return_writes_no_zero_interval():
    now = DEPART + 1200
    trip = activity(TRIP, "travel", DEPART, now)
    before = world(now, trip, foreground=opened(now))
    after = later(before, now, trip, activity("activity-action-buy", "purchase", now, now))
    assert settled(before, after) == (None, [])


def test_side_activity_and_trip_ending_in_the_same_second_draw_nothing_and_close_nothing():
    stop, side_id = DEPART + 1800, side_named(True)
    before = world(stop - 60, activity(TRIP, "travel", DEPART, stop - 60),
                   activity(side_id, "interact", DEPART, stop - 60))
    after = later(before, stop, activity(TRIP, "travel", DEPART, stop, status="completed", ended_at=stop),
                  activity(side_id, "interact", DEPART, stop, status="completed", ended_at=stop))
    assert settled(before, after) == (None, [])


@pytest.mark.parametrize("status", ["completed", "cancelled", "truncated"])
def test_arrival_early_end_and_run_cutoff_each_close_navigation_once(status):
    stop = DEPART + 1800 if status == "completed" else DEPART + 700
    before = world(stop - 60, activity(TRIP, "travel", DEPART, stop - 60), foreground=opened(DEPART))
    after = later(before, stop, activity(TRIP, "travel", DEPART, stop, status=status, ended_at=stop))
    assert settled(before, after) == (None, [(stop, TRIP, "amap", "navigation", DEPART, stop)])
    closed = settle_navigation(before, after, CATALOG)[0]
    assert settled(closed, later(closed, stop, *closed.activities.values())) == (None, [])


def test_concluding_an_already_finished_side_activity_is_no_stop_and_draws_nothing():
    now, side_id = DEPART + 900, side_named(True)
    trip = activity(TRIP, "travel", DEPART, now)
    waiting = activity(side_id, "interact", DEPART, now, status="completed", ended_at=now - 60)
    before = world(now, trip, waiting)
    concluded = waiting.model_copy(update={"concluded_at": now})
    assert settled(before, later(before, now, trip, concluded)) == (None, [])
    assert settled(before, later(before, now + 60, activity(TRIP, "travel", DEPART, now + 60), concluded)) == (None, [])


def test_a_trip_whose_method_no_app_is_bound_to_never_has_navigation():
    side_id = side_named(True)
    walk = activity(TRIP, "travel", DEPART, DEPART, method="walking")
    before = world(DEPART)
    assert settled(before, later(before, DEPART, walk)) == (None, [])
    running = world(DEPART + 60, activity(TRIP, "travel", DEPART, DEPART + 60, method="walking"),
                    activity(side_id, "interact", DEPART, DEPART + 60))
    stopped = activity(side_id, "interact", DEPART, DEPART + 120, status="completed", ended_at=DEPART + 120)
    after = later(running, DEPART + 120, activity(TRIP, "travel", DEPART, DEPART + 120, method="walking"), stopped)
    assert settled(running, after) == (None, [])
    assert navigation_app(running, running.activities[TRIP], CATALOG) is None


def test_navigation_app_is_one_bound_app_chosen_once_per_trip_by_the_activity_key():
    trip = activity(TRIP, "travel", DEPART, DEPART, method="bus")
    state = world(DEPART, trip)
    expected = random_stream("navigation-tests", f"function-app:{TRIP}").choice(("amap", "baidu-map"))
    assert navigation_app(state, trip, CATALOG) == expected
    chosen = {navigation_app(state, activity(f"activity-action-{index}", "travel", DEPART, DEPART, method="bus"),
                             CATALOG) for index in range(32)}
    assert chosen == {"amap", "baidu-map"}
    # 整趟不换：途中回到前台时选的仍是同一个 app。
    side_id = side_named(True)
    before = world(DEPART + 60, activity(TRIP, "travel", DEPART, DEPART + 60, method="bus"),
                   activity(side_id, "interact", DEPART, DEPART + 60))
    stopped = activity(side_id, "interact", DEPART, DEPART + 120, status="completed", ended_at=DEPART + 120)
    after = later(before, DEPART + 120, activity(TRIP, "travel", DEPART, DEPART + 120, method="bus"), stopped)
    assert settled(before, after) == (opened(DEPART + 120, app=expected), [])


def test_foreground_event_is_a_system_record_caused_by_the_acceptance_of_its_activity():
    trip = activity(TRIP, "travel", DEPART, DEPART + 600)
    event = foreground_event(trip, "amap", "navigation", (DEPART, DEPART + 600))
    assert (event.event_type, event.visibility, event.source_kind) == ("environment_event", "system", "environment")
    assert (event.agent_id, event.activity_id, event.occurred_at) == (1, TRIP, DEPART + 600)
    assert event.caused_by == [f"accepted-{TRIP}"] and event.resource_changes == {}
    assert event.data == {"kind": "app_foreground", "app_id": "amap", "step": "navigation",
                          "interval_start": DEPART, "interval_end": DEPART + 600}


def test_active_side_activities_are_the_running_purchases_and_app_uses_only():
    now = DEPART + 600
    state = world(now, activity(TRIP, "travel", DEPART, now), activity("activity-action-buy", "purchase", now, now))
    assert [item.activity_id for item in active_side_activities(state, 1)] == ["activity-action-buy"]
    paused = activity("activity-action-watch", "interact", DEPART, now, status="paused")
    assert active_side_activities(world(now, activity(TRIP, "travel", DEPART, now), paused), 1) == ()


@pytest.mark.parametrize("fault", ["trip_ended", "another_activity", "before_departure", "after_now", "side_running"])
def test_world_state_refuses_a_navigation_foreground_that_disagrees_with_the_activities(fault):
    now = DEPART + 600
    trip = activity(TRIP, "travel", DEPART, now)
    activities, foreground = [trip], opened(DEPART)
    if fault == "trip_ended":
        activities = [activity(TRIP, "travel", DEPART, now, status="completed", ended_at=now)]
    elif fault == "another_activity":
        foreground = opened(DEPART, trip="activity-action-other")
    elif fault == "before_departure":
        foreground = opened(DEPART - 1)
    elif fault == "after_now":
        foreground = opened(now + 1)
    else:
        activities.append(activity("activity-action-watch", "interact", now, now))
    with pytest.raises(ValidationError, match="navigation foreground disagrees with active activities"):
        world(now, *activities, foreground=foreground)
    assert world(now, trip, foreground=opened(now)).agents[1].navigation_foreground == opened(now)
