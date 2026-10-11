"""停留进出事件：由提交前后世界里会话的变化得出，起因取同一次提交里的真实事件。"""

import pytest

from tests.unit.life.test_navigation_foreground import DEPART, TRIP, activity, world
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life import sessions
from worldmock.life.stay_records import run_start_event, stay_events

NAMES = {"home": "家", "office": "公司"}


def result(action_id, at):
    """一条动作结果草稿：受理出发，或结束出行。"""
    return EventDraft(event_type="action_result", occurred_at=at, agent_id=1, action_id=action_id,
                      data={"status": "in_progress"})


def progress(activity_id, at):
    """一条活动进度草稿：出行走完的那一秒。"""
    return EventDraft(event_type="activity_progress", occurred_at=at, agent_id=1, activity_id=activity_id,
                      data={"interval_start": DEPART, "interval_end": at})


def facts(events):
    return [(event.occurred_at, event.data, event.caused_by) for event in events]


def at_home():
    """运行开始：人物停留在家。"""
    return sessions.open_stay(world(DEPART), 1, NAMES)


def on_the_road(state, now=DEPART):
    """出发：结束在家的停留，开一段去公司的路，出行活动归入这段路。"""
    trip = activity(TRIP, "travel", DEPART, now)
    departed = sessions.begin_transit(state, 1, "office", NAMES, opened_by_action_id="action-drive")
    departed = departed.model_copy(update={"current_time": now, "activities": {TRIP: trip}})
    return sessions.assign(departed, "session-1-0002", [TRIP])


def test_run_start_is_one_uncaused_system_record_of_the_stay_the_person_is_already_in():
    event = run_start_event(at_home())
    assert (event.event_type, event.visibility, event.source_kind) == ("environment_event", "system", "environment")
    assert (event.agent_id, event.occurred_at, event.caused_by, event.resource_changes) == (1, DEPART, [], {})
    assert event.data == {"kind": "location_entered", "session_id": "session-1-0001", "location_id": "home",
                          "entry_kind": "run_start"}


def test_departure_leaves_the_stay_in_progress_caused_by_the_accepted_departure():
    home = at_home()
    accepted = result("action-drive", DEPART)
    (left,) = stay_events(home, on_the_road(home), [result("action-other", DEPART), accepted])
    assert (left.visibility, left.source_kind, left.agent_id, left.activity_id) == ("system", "environment", 1, None)
    assert facts([left]) == [(DEPART, {"kind": "location_left", "session_id": "session-1-0001",
                                       "location_id": "home"}, [accepted.event_id])]
    # 没有会话变化的提交不写任何停留事件。
    assert stay_events(home, home, [accepted]) == []


def test_arrival_enters_the_destination_caused_by_the_progress_that_finished_the_trip():
    arrive = DEPART + 1800
    road = on_the_road(at_home(), arrive - 60)
    done = activity(TRIP, "travel", DEPART, arrive, status="completed", ended_at=arrive)
    person = road.agents[1].model_copy(update={"location_id": "office"})
    arriving = road.model_copy(update={"current_time": arrive, "agents": {1: person}, "activities": {TRIP: done}})
    arrived = sessions.end_transit(arriving, 1, NAMES)
    finished = progress(TRIP, arrive)
    (entered,) = stay_events(road, arrived, [progress("activity-action-watch", arrive), finished])
    assert facts([entered]) == [(arrive, {"kind": "location_entered", "session_id": "session-1-0003",
                                          "location_id": "office", "entry_kind": "arrival"}, [finished.event_id])]
    # 到达时刻恰好是运行截止：会话随即结束，进入照常写，截止不写离开。
    ended = sessions.end_all(arrived)
    assert facts(stay_events(road, ended, [finished])) == facts([entered])
    assert stay_events(arrived, ended, []) == []


def test_ending_the_trip_early_enters_the_origin_again_caused_by_that_conclusion():
    stop = DEPART + 600
    road = on_the_road(at_home(), stop)
    cancelled = activity(TRIP, "travel", DEPART, stop, status="cancelled", ended_at=stop)
    stopped = sessions.end_transit(road.model_copy(update={"activities": {TRIP: cancelled}}), 1, NAMES,
                                   opened_by_action_id="action-stop")
    concluded = result("action-stop", stop)
    (entered,) = stay_events(road, stopped, [concluded])
    assert facts([entered]) == [(stop, {"kind": "location_entered", "session_id": "session-1-0003",
                                        "location_id": "home", "entry_kind": "travel_ended_early"},
                                 [concluded.event_id])]


def test_stay_records_without_their_cause_in_the_commit_are_run_faults():
    home = at_home()
    with pytest.raises(IntegrityError, match="^Stay record has no cause event in this commit$"):
        stay_events(home, on_the_road(home), [result("action-other", DEPART)])
    road = on_the_road(home)
    with pytest.raises(IntegrityError, match="^Departure has no stay in progress to leave$"):
        stay_events(sessions.end_all(home), road, [result("action-drive", DEPART)])
    # 多出一段没有动作身份的停留，而同一秒没有走完的出行。
    arrived = sessions.end_transit(road, 1, NAMES)
    with pytest.raises(IntegrityError, match="^Arrival has no finished trip in this commit$"):
        stay_events(road, arrived, [progress(TRIP, DEPART)])
