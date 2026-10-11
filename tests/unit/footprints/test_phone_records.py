"""手机记录的投影与事件合同：引擎代为使用的 app 的使用行、订单通知、地点停留。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from tests.unit.footprints.test_app_usage import START, _app, _event
from worldmock.errors import IntegrityError
from worldmock.footprints import invariants
from worldmock.footprints.app_usage import project_app_usage
from worldmock.footprints.location_stays import project_location_stays, validate_location_stays
from worldmock.footprints.models import Event
from worldmock.life.models import ActivityState


def owner(identity, kind, status="active", ended_at=None):
    """引擎代为使用 app 的那项活动的快照：出行或购买。"""
    data = {"action": kind, **({"order_ids": []} if kind == "purchase" else {})}
    return ActivityState(
        activity_id=identity, agent_id=1, kind=kind, status=status, location_id="home", started_at=START,
        last_settled_at=START, elapsed_seconds=0, planned_end_at=START + 1800, planned_duration_seconds=1800,
        ended_at=ended_at, destination_id="office" if kind == "travel" else None,
        travel_method_id="driving" if kind == "travel" else None,
        resource_claims={"first" if kind == "travel" else "second"}, origin_action_id=f"start-{identity}",
        origin_decision_id="decision", origin_request_event_id="request", data=data).model_dump(mode="json")


def system(sequence, data, *, at, activity_id=None, causes=("accepted",), version=None):
    """一条系统可见的环境事件，经真实事件模型校验。"""
    return Event(event_id=f"event-{sequence}", event_seq=sequence, run_id="run", event_type="environment_event",
                 occurred_at=at, recorded_at="2026-01-05T08:00:00+00:00", wall_elapsed_ms=sequence,
                 state_version_before=0, state_version_after=sequence if version is None else version, agent_id=1,
                 activity_id=activity_id, visibility="system", caused_by=list(causes),
                 data=data).model_dump(mode="json")


def front(sequence, activity_id, app_id, step, start, end, **fields):
    """一段前台区间在关闭那一秒写下的事件；时刻写成相对 START 的秒数。"""
    data = {"kind": "app_foreground", "app_id": app_id, "step": step, "interval_start": START + start,
            "interval_end": START + end}
    return system(sequence, data, at=START + end, activity_id=activity_id, **fields)


def notice(sequence, title, at=60, **changes):
    data = {"kind": "order_notification", "app_id": "luckin", "order_ids": ["order-1"], "title": title,
            "summary": "冰美式已发货", **changes}
    return system(sequence, data, at=START + at, causes=("payment",))


def accepted(sequence, activity):
    return _event(sequence, "action_accepted", {"activity": activity}, activity_id=activity["activity_id"])


def uses(events):
    return [(row["activity_id"], row["app_id"], row["status"],
             [(part["started_at"] - START, part["ended_at"] - START) for part in row["segments"]],
             row["source_event_id"]) for row in project_app_usage(events) if row["fact_kind"] == "app_use"]


def test_foreground_intervals_of_one_activity_and_app_merge_when_adjacent_and_stay_apart_otherwise():
    trip = owner("trip", "travel")
    done = _event(5, "action_result", {"activity": owner("trip", "travel", "completed", START + 1800)},
                  at=START + 1800, activity_id="trip")
    events = [accepted(1, trip), front(2, "trip", "amap", "navigation", 0, 660),
              front(3, "trip", "amap", "navigation", 660, 900), front(4, "trip", "amap", "navigation", 1200, 1800),
              done]
    # 首尾相接的并成一段，不相接的分段；状态取所属活动最后的状态，来源是最后一条贡献区间的前台事件。
    assert uses(events) == [("trip", "amap", "completed", [(0, 900), (1200, 1800)], "event-4")]
    row = project_app_usage(events)[0]
    assert set(row) == {"fact_kind", "agent_id", "activity_id", "app_id", "source_event_id", "segments", "status"}
    assert row["agent_id"] == 1


def test_one_purchase_with_two_apps_is_two_rows_and_direct_payment_is_one_merged_segment():
    buying = owner("buy", "purchase", "completed", START + 200)
    paid = [accepted(1, buying), front(2, "buy", "luckin", "ordering", 0, 185),
            front(3, "buy", "alipay", "payment", 185, 200)]
    assert uses(paid) == [("buy", "luckin", "completed", [(0, 185)], "event-2"),
                          ("buy", "alipay", "completed", [(185, 200)], "event-3")]
    direct = [accepted(1, buying), front(2, "buy", "luckin", "ordering", 0, 185),
              front(3, "buy", "luckin", "payment", 185, 200)]
    assert uses(direct) == [("buy", "luckin", "completed", [(0, 200)], "event-3")]
    # 直付订单不是最后付款的那张：它的下单 app 先下单，隔着别的 app 再回来付款，是同一行里的两段。
    apart = [accepted(1, buying), front(2, "buy", "luckin", "ordering", 0, 100),
             front(3, "buy", "alipay", "payment", 100, 115), front(4, "buy", "luckin", "payment", 115, 130)]
    assert uses(apart) == [("buy", "luckin", "completed", [(0, 100), (115, 130)], "event-4"),
                           ("buy", "alipay", "completed", [(100, 115)], "event-3")]


@pytest.mark.parametrize("status", ["cancelled", "truncated", "failed"])
def test_automatic_app_use_takes_the_final_status_of_the_activity_it_belongs_to(status):
    buying = owner("buy", "purchase")
    ended = _event(3, "action_result", {"activity": owner("buy", "purchase", status, START + 90)},
                   at=START + 90, activity_id="buy")
    assert uses([accepted(1, buying), front(2, "buy", "luckin", "ordering", 0, 90), ended]) == [
        ("buy", "luckin", status, [(0, 90)], "event-2")]


def test_app_use_rows_come_first_in_the_order_of_their_first_source_event_then_the_other_facts():
    trip, watching = owner("trip", "travel"), _app("phone")
    events = [accepted(1, trip), front(2, "trip", "amap", "navigation", 0, 660),
              _event(3, "action_accepted", {"activity": watching}, at=START + 660, activity_id="phone"),
              notice(4, "订单已发货", at=700),
              front(5, "trip", "amap", "navigation", 1200, 1800),
              accepted(6, owner("buy", "purchase")), front(7, "buy", "luckin", "ordering", 1800, 1900, version=7)]
    rows = project_app_usage(events)
    assert [(row["fact_kind"], row["activity_id"], row["app_id"]) for row in rows] == [
        ("app_use", "trip", "amap"), ("app_use", "phone", "bilibili"), ("app_use", "buy", "luckin"),
        ("order_notification", None, "luckin")]
    assert rows[0]["source_event_id"] == "event-5"
    assert rows[3] == {"fact_kind": "order_notification", "agent_id": 1, "activity_id": None, "app_id": "luckin",
                       "source_event_id": "event-4", "occurred_at": START + 700, "order_ids": ["order-1"],
                       "title": "订单已发货", "summary": "冰美式已发货"}


FIELDS = "System app event fields are outside the current contract"
INTERVAL = "App foreground step or interval is invalid"
NOTICE = "Order notification text or order set is invalid"


def changed(event, **data):
    row = deepcopy(event)
    row["data"].update(data)
    return row


def without(event, key):
    row = deepcopy(event)
    del row["data"][key]
    return row


FRONT = front(2, "trip", "amap", "navigation", 0, 660)
SENT = notice(2, "订单已发货")


@pytest.mark.parametrize("event,reason", [
    (changed(FRONT, action="travel"), FIELDS),
    (without(FRONT, "step"), FIELDS),
    ({**FRONT, "activity_id": None}, FIELDS),
    ({**FRONT, "visibility": "agent"}, "System app event must be a system-visible environment event"),
    ({**FRONT, "source_kind": "model"}, "System app event must be a system-visible environment event"),
    ({**FRONT, "caused_by": []}, "System app event requires one actual cause event"),
    ({**FRONT, "caused_by": ["accepted", "another"]}, "System app event requires one actual cause event"),
    (changed(FRONT, interval_start=START + 660), INTERVAL),
    (changed(FRONT, interval_start=START + 700), INTERVAL),
    (changed(FRONT, interval_end=START + 600), INTERVAL),
    (changed(FRONT, interval_start=float(START)), INTERVAL),
    (changed(FRONT, step="browse"), INTERVAL),
    (changed(FRONT, app_id=""), "System app event requires an actual app identity"),
    (changed(SENT, amount_fen=100), FIELDS),
    (without(SENT, "summary"), FIELDS),
    ({**SENT, "visibility": "agent"}, "System app event must be a system-visible environment event"),
    ({**SENT, "caused_by": []}, "System app event requires one actual cause event"),
    (changed(SENT, title="支付成功"), NOTICE),
    (changed(SENT, title="订单已取消"), NOTICE),
    (changed(SENT, summary=""), NOTICE),
    (changed(SENT, order_ids=[]), NOTICE),
    (changed(SENT, order_ids=["order-2", "order-1"]), NOTICE),
    (changed(SENT, order_ids=["order-1", "order-1"]), NOTICE),
    (changed(SENT, order_ids=["order-1", ""]), NOTICE),
    (changed(SENT, app_id=None), "System app event requires an actual app identity"),
])
def test_foreground_and_order_notice_events_outside_their_contract_are_refused(event, reason):
    with pytest.raises(IntegrityError, match=f"^{reason}$"):
        project_app_usage([accepted(1, owner("trip", "travel")), event])
    assert uses([accepted(1, owner("trip", "travel")), FRONT]) == [("trip", "amap", "active", [(0, 660)], "event-2")]


@pytest.mark.parametrize("title", ["订单已下单", "订单已发货", "订单已送达"])
def test_the_three_order_notice_titles_are_accepted(title):
    (row,) = project_app_usage([notice(1, title)])
    assert (row["fact_kind"], row["title"], row["activity_id"]) == ("order_notification", title, None)


def test_overlapping_foreground_intervals_and_intervals_of_an_unknown_activity_are_refused():
    trip = owner("trip", "travel")
    with pytest.raises(IntegrityError, match=f"^{INTERVAL}$"):
        project_app_usage([accepted(1, trip), front(2, "trip", "amap", "navigation", 0, 660),
                           front(3, "trip", "amap", "navigation", 600, 900)])
    with pytest.raises(IntegrityError, match="^Automatic app use disagrees with its foreground events$"):
        project_app_usage([front(1, "ghost", "amap", "navigation", 0, 660)])


def stay(sequence, kind, session, location, at, entry=None, causes=("cause",)):
    data = {"kind": kind, "session_id": session, "location_id": location, **({"entry_kind": entry} if entry else {})}
    return system(sequence, data, at=START + at, causes=causes)


def day_of_stays():
    """运行开始在家；出发到公司；途中提前结束一次出行回到公司；截止时还在公司。"""
    return [stay(1, "location_entered", "s1", "home", 0, "run_start", ()), stay(2, "location_left", "s1", "home", 1200),
            stay(3, "location_entered", "s3", "office", 3000, "arrival"),
            stay(4, "location_left", "s3", "office", 6000),
            stay(5, "location_entered", "s5", "office", 6600, "travel_ended_early")]


def test_stays_pair_enter_and_leave_by_session_and_keep_the_order_of_entering():
    rows = project_location_stays(day_of_stays())
    assert [(row["session_id"], row["location_id"], row["entry_kind"], row["entered_at"] - START,
             None if row["left_at"] is None else row["left_at"] - START) for row in rows] == [
        ("s1", "home", "run_start", 0, 1200), ("s3", "office", "arrival", 3000, 6000),
        ("s5", "office", "travel_ended_early", 6600, None)]
    assert [(row["enter_source_event_id"], row["leave_source_event_id"]) for row in rows] == [
        ("event-1", "event-2"), ("event-3", "event-4"), ("event-5", None)]
    assert all(set(row) == {"agent_id", "session_id", "location_id", "entry_kind", "entered_at", "left_at",
                            "enter_source_event_id", "leave_source_event_id"} and row["agent_id"] == 1 for row in rows)
    assert project_location_stays([accepted(1, owner("trip", "travel"))]) == []


@pytest.mark.parametrize("extra", [
    stay(6, "location_entered", "s5", "office", 7000, "arrival"),
    stay(6, "location_left", "s9", "office", 7000),
    stay(6, "location_left", "s3", "office", 7000),
    stay(6, "location_left", "s5", "home", 7000),
])
def test_stay_events_that_do_not_pair_are_refused(extra):
    with pytest.raises(IntegrityError, match="^Location stays overlap or are out of order$"):
        project_location_stays([*day_of_stays(), extra])


STAY_EVENT = "Location stay event fields or source are invalid"


@pytest.mark.parametrize("kind,change", [
    ("location_entered", {"data": {"extra": 1}}), ("location_entered", {"drop": "entry_kind"}),
    ("location_entered", {"data": {"entry_kind": "teleport"}}), ("location_entered", {"data": {"session_id": ""}}),
    ("location_entered", {"visibility": "agent"}), ("location_entered", {"source_kind": "scenario"}),
    ("location_entered", {"caused_by": []}), ("location_entered", {"caused_by": ["a", "b"]}),
    ("location_entered", {"data": {"entry_kind": "run_start"}}), ("location_entered", {"agent_id": None}),
    ("location_entered", {"resource_changes": {"account:agent:1": -1, "account:external:scenario": 1}}),
    ("location_left", {"data": {"entry_kind": "arrival"}}), ("location_left", {"drop": "location_id"}),
    ("location_left", {"data": {"location_id": " "}}), ("location_left", {"visibility": "evaluation"}),
    ("location_left", {"caused_by": []}), ("location_left", {"caused_by": ["a", "b"]}),
])
def test_stay_events_outside_their_contract_cannot_be_written_or_read(kind, change):
    row = stay(1, kind, "s3", "office", 3000, "arrival" if kind == "location_entered" else None)
    Event.model_validate(row)
    row["data"].update(change.get("data", {}))
    row["data"].pop(change.get("drop"), None)
    row.update({key: value for key, value in change.items() if key not in {"data", "drop"}})
    with pytest.raises(ValidationError, match=STAY_EVENT):
        Event.model_validate(row)


def certified():
    """与 day_of_stays 相符的事件、出行活动行与最终检查点。"""
    departures = {"left-1": ("home", 1200), "left-2": ("office", 6000)}
    events = day_of_stays()
    events[1]["caused_by"], events[2]["caused_by"] = ["left-1"], ["progress-1"]
    events[3]["caused_by"], events[4]["caused_by"] = ["left-2"], ["stopped"]
    causes = [{"event_id": key, "event_type": "action_result", "agent_id": 1, "data": {
        "status": "in_progress", "data": {"activities": [
            {"kind": "travel", "started_at": START + at, "location_id": place}]}}}
        for key, (place, at) in departures.items()]
    causes.append({"event_id": "progress-1", "event_type": "activity_progress", "agent_id": 1,
                   "activity_id": "trip-1", "data": {}})
    causes.append({"event_id": "stopped", "event_type": "action_result", "agent_id": 1,
                   "data": {"status": "completed"}})
    trips = [{"activity_id": "trip-1", "activity_type": "travel", "started_at": START + 1200,
              "ended_at": START + 3000, "location_id": "home", "destination_id": "office",
              "elapsed_seconds": 1800, "planned_duration_seconds": 1800}]
    sessions = {"s1": ("stay", "home", 0, 1200), "s2": ("transit", None, 1200, 3000),
                "s3": ("stay", "office", 3000, 6000), "s4": ("transit", None, 6000, 6600),
                "s5": ("stay", "office", 6600, 7200)}
    state = {"sessions": {key: {"session_id": key, "agent_id": 1, "kind": kind, "location_id": place,
                                "started_at": START + start, "ended_at": START + end}
                          for key, (kind, place, start, end) in sessions.items()}}
    return [*causes, *events], trips, state, {"start": START, "end": START + 7200}


def check(events, trips, state, window, rows=None):
    validate_location_stays(project_location_stays(events) if rows is None else rows, events, trips, state, window)


def test_stays_that_agree_with_the_trips_and_the_final_checkpoint_are_certified():
    check(*certified())


ORDER, TRAVEL, CHECKPOINT = ("Location stays overlap or are out of order",
                             "Location stay disagrees with its actual travel",
                             "Location stays disagree with the final checkpoint")


@pytest.mark.parametrize("fault,reason", [
    ("no_rows", ORDER), ("first_not_run_start", ORDER), ("two_run_starts", ORDER), ("late_run_start", ORDER),
    ("open_stay_in_the_middle", ORDER), ("left_before_entering", ORDER), ("entered_before_the_last_left", ORDER),
    ("arrival_elsewhere", TRAVEL), ("arrival_at_another_time", TRAVEL), ("trip_not_finished", TRAVEL),
    ("trip_from_elsewhere", TRAVEL), ("trip_started_later", TRAVEL), ("arrival_without_trip", TRAVEL),
    ("arrival_by_another_person", TRAVEL), ("early_end_elsewhere", TRAVEL), ("early_end_not_a_result", TRAVEL),
    ("departure_not_accepted", TRAVEL), ("departure_from_elsewhere", TRAVEL), ("departure_without_cause", TRAVEL),
    ("missing_session", CHECKPOINT), ("extra_session", CHECKPOINT), ("other_place", CHECKPOINT),
    ("other_start", CHECKPOINT), ("other_end", CHECKPOINT), ("open_stay_not_at_the_cutoff", CHECKPOINT),
])
def test_stays_that_disagree_with_order_travel_or_checkpoint_are_refused(fault, reason):
    events, trips, state, window = certified()
    rows = project_location_stays(events)
    by_id = {event["event_id"]: event for event in events}
    if fault == "no_rows":
        rows = []
    elif fault == "first_not_run_start":
        rows[0]["entry_kind"] = "arrival"
    elif fault == "two_run_starts":
        rows[1]["entry_kind"] = "run_start"
    elif fault == "late_run_start":
        window["start"] -= 60
    elif fault == "open_stay_in_the_middle":
        rows[1]["left_at"] = None
    elif fault == "left_before_entering":
        rows[1]["left_at"] = rows[1]["entered_at"] - 1
    elif fault == "entered_before_the_last_left":
        rows[2]["entered_at"] = rows[1]["left_at"] - 1
    elif fault == "arrival_elsewhere":
        trips[0]["destination_id"] = "park"
    elif fault == "arrival_at_another_time":
        trips[0]["ended_at"] += 1
    elif fault == "trip_not_finished":
        trips[0]["elapsed_seconds"] -= 1
    elif fault == "trip_from_elsewhere":
        trips[0]["location_id"] = "park"
    elif fault == "trip_started_later":
        trips[0]["started_at"] += 1
    elif fault == "arrival_without_trip":
        by_id["progress-1"]["activity_id"] = "ghost"
    elif fault == "arrival_by_another_person":
        by_id["progress-1"]["agent_id"] = 2
    elif fault == "early_end_elsewhere":
        rows[2]["location_id"] = "home"
    elif fault == "early_end_not_a_result":
        by_id["stopped"]["event_type"] = "activity_progress"
    elif fault == "departure_not_accepted":
        by_id["left-1"]["data"]["status"] = "rejected"
    elif fault == "departure_from_elsewhere":
        by_id["left-2"]["data"]["data"]["activities"][0]["location_id"] = "home"
    elif fault == "departure_without_cause":
        by_id["event-2"]["caused_by"] = ["ghost"]
    elif fault == "missing_session":
        del state["sessions"]["s3"]
    elif fault == "extra_session":
        state["sessions"]["s9"] = {**state["sessions"]["s5"], "session_id": "s9"}
    elif fault == "other_place":
        state["sessions"]["s3"]["location_id"] = "park"
    elif fault == "other_start":
        state["sessions"]["s3"]["started_at"] += 1
    elif fault == "other_end":
        state["sessions"]["s3"]["ended_at"] += 1
    else:
        state["sessions"]["s5"]["ended_at"] -= 60
    with pytest.raises(IntegrityError, match=f"^{reason}$"):
        check(events, trips, state, window, rows)


def exported(identity, kind, spans, status="completed", **fields):
    """导出活动行里自查要读的字段：种类、状态、真实执行区间与占的槽位；时刻写成相对 START 的秒数。"""
    return {"activity_id": identity, "agent_id": 1, "activity_type": kind, "status": status,
            "segments": [{"started_at": START + start, "ended_at": START + end} for start, end in spans],
            "resource_claims": ["first"] if kind == "travel" else ["second"], **fields}


def use_row(activity_id, app_id, spans, status="completed"):
    return {"fact_kind": "app_use", "agent_id": 1, "activity_id": activity_id, "app_id": app_id,
            "source_event_id": "event", "status": status,
            "segments": [{"started_at": START + start, "ended_at": START + end} for start, end in spans]}


def by_activity(*rows):
    return {row["activity_id"]: row for row in rows}


AUTOMATIC = "Automatic app use disagrees with its foreground events"
NAVIGATION = "Navigation app use overlaps a side activity or leaves its trip"
PHASES = "Purchase app use disagrees with its order phases"


@pytest.mark.parametrize("fault", ["none", "segments", "status", "row_without_events", "events_without_row",
                                   "outside_the_purchase"])
def test_automatic_app_use_rows_must_equal_the_merged_foreground_intervals_of_their_activity(fault):
    activities = by_activity(exported("trip", "travel", [(0, 1800)]), exported("buy", "purchase", [(1800, 2000)]))
    events = [front(1, "trip", "amap", "navigation", 0, 660), front(2, "trip", "amap", "navigation", 660, 900),
              front(3, "buy", "luckin", "ordering", 1800, 1985), front(4, "buy", "alipay", "payment", 1985, 2000)]
    rows = [use_row("trip", "amap", [(0, 900)]), use_row("buy", "luckin", [(1800, 1985)]),
            use_row("buy", "alipay", [(1985, 2000)])]
    if fault == "segments":
        rows[0] = use_row("trip", "amap", [(0, 660), (660, 900)])
    elif fault == "status":
        rows[1]["status"] = "cancelled"
    elif fault == "row_without_events":
        rows.append(use_row("trip", "baidu-map", [(900, 1000)]))
    elif fault == "events_without_row":
        del rows[2]
    elif fault == "outside_the_purchase":
        events[3] = front(4, "buy", "alipay", "payment", 1985, 2010)
        rows[2] = use_row("buy", "alipay", [(1985, 2010)])
    if fault == "none":
        invariants._automatic_app_use(rows, activities, invariants._foreground_events(events))
        return
    with pytest.raises(IntegrityError, match=f"^{AUTOMATIC}$"):
        invariants._automatic_app_use(rows, activities, invariants._foreground_events(events))


@pytest.mark.parametrize("fault", ["none", "two_apps", "another_step", "after_arrival", "over_a_purchase",
                                   "over_an_app", "navigation_on_a_purchase"])
def test_navigation_intervals_stay_inside_their_trip_and_clear_of_side_activities(fault):
    trip, buying = exported("trip", "travel", [(0, 1800)]), exported("buy", "purchase", [(660, 900)])
    watching = exported("phone", "interact", [(1000, 1200)])
    resting = exported("rest", "interact", [(0, 1800)], resource_claims=["first"])
    events = [front(1, "trip", "amap", "navigation", 0, 660), front(2, "trip", "amap", "navigation", 1200, 1800)]
    if fault == "two_apps":
        events[1] = front(2, "trip", "baidu-map", "navigation", 1200, 1800)
    elif fault == "another_step":
        events[1] = front(2, "trip", "amap", "payment", 1200, 1800)
    elif fault == "after_arrival":
        events[1] = front(2, "trip", "amap", "navigation", 1200, 1801)
    elif fault == "over_a_purchase":
        events[0] = front(1, "trip", "amap", "navigation", 0, 661)
    elif fault == "over_an_app":
        events[1] = front(2, "trip", "amap", "navigation", 1199, 1800)
    elif fault == "navigation_on_a_purchase":
        events.append(front(3, "buy", "amap", "navigation", 660, 900))
    activities = by_activity(trip, buying, watching, resting)
    if fault == "none":
        # 占槽位 1 的在地点做事不妨碍导航；购买的下单与支付区间不归这一条检查。
        events.append(front(3, "buy", "luckin", "ordering", 660, 900))
        invariants._navigation_intervals(activities, invariants._foreground_events(events))
        return
    with pytest.raises(IntegrityError, match=f"^{NAVIGATION}$"):
        invariants._navigation_intervals(activities, invariants._foreground_events(events))


def record(identity, app_id, payment_app_id, seconds, started, ended):
    """最终检查点里一条商品履约记录上与付款段有关的字段；时刻写成相对 START 的秒数，None 表示没写。"""
    return {"order_id": identity, "app_id": app_id, "payment_app_id": payment_app_id,
            "payment_duration_seconds": seconds,
            "payment_started_at": None if started is None else START + started,
            "payment_ended_at": None if ended is None else START + ended}


def three_orders(stopped_at=None):
    """12:00:00 开始、抽到 200 秒的购买：盒马、瑞幸咖啡、美团各一张订单，付款 15、12、18 秒，美团直付。"""
    stop = 200 if stopped_at is None else stopped_at
    written = lambda start, end: (start if start <= stop else None, end if end <= stop else None)
    orders = {"o1": record("o1", "hema", "alipay", 15, *written(155, 170)),
              "o2": record("o2", "luckin", "wechat", 12, *written(170, 182)),
              "o3": record("o3", "meituan", None, 18, *written(182, 200))}
    purchase = exported("buy", "purchase", [(0, stop)], status="completed" if stopped_at is None else "cancelled",
                        started_at=START, planned_duration_seconds=200, ended_at=START + stop,
                        data={"order_ids": ["o1", "o2", "o3"]})
    phases = [("hema", "ordering", 0, 51), ("luckin", "ordering", 51, 102), ("meituan", "ordering", 102, 155),
              ("alipay", "payment", 155, 170), ("wechat", "payment", 170, 182), ("meituan", "payment", 182, 200)]
    events = [front(index, "buy", app, step, start, min(end, stop))
              for index, (app, step, start, end) in enumerate(phases, 1) if start < stop]
    return by_activity(purchase), events, orders


def check_purchase(activities, events, orders):
    invariants._purchase_intervals(activities, invariants._foreground_events(events), orders)


@pytest.mark.parametrize("stopped_at", [None, 175, 170, 100, 0])
def test_ordering_and_payment_intervals_of_the_three_order_example_agree_with_the_orders(stopped_at):
    """完整走完，以及在第二个付款段中途、恰在它开始那一秒、下单途中、受理那一秒被结束的购买都相符。"""
    check_purchase(*three_orders(stopped_at))


@pytest.mark.parametrize("fault", [
    "payment_start_not_written", "payment_end_written_early", "payment_seconds_out_of_range",
    "payment_interval_missing", "payment_interval_by_another_app", "payment_interval_shifted",
    "ordering_by_an_app_of_no_order", "ordering_after_the_first_payment", "another_step_on_the_purchase",
    "cut_payment_interval_runs_to_its_planned_end",
])
def test_ordering_and_payment_intervals_that_disagree_with_the_orders_are_refused(fault):
    cut = fault == "cut_payment_interval_runs_to_its_planned_end"
    activities, events, orders = three_orders(175 if cut else None)
    if fault == "payment_start_not_written":
        orders["o2"]["payment_started_at"] = None
    elif fault == "payment_end_written_early":
        orders["o3"]["payment_ended_at"] = START + 199
    elif fault == "payment_seconds_out_of_range":
        orders["o1"].update(payment_duration_seconds=21, payment_started_at=START + 149)
    elif fault == "payment_interval_missing":
        del events[4]
    elif fault == "payment_interval_by_another_app":
        events[3] = front(4, "buy", "wechat", "payment", 155, 170)
    elif fault == "payment_interval_shifted":
        events[5] = front(6, "buy", "meituan", "payment", 183, 200)
    elif fault == "ordering_by_an_app_of_no_order":
        events[0] = front(1, "buy", "eleme", "ordering", 0, 51)
    elif fault == "ordering_after_the_first_payment":
        events[2] = front(3, "buy", "meituan", "ordering", 102, 156)
    elif fault == "another_step_on_the_purchase":
        events[0] = front(1, "buy", "hema", "navigation", 0, 51)
    else:
        events[4] = front(5, "buy", "wechat", "payment", 170, 182)
    with pytest.raises(IntegrityError, match=f"^{PHASES}$"):
        check_purchase(activities, events, orders)


def test_store_order_paid_directly_has_no_payment_interval_and_needs_none():
    orders = {"o1": record("o1", None, None, 0, None, None)}
    purchase = exported("buy", "purchase", [(0, 120)], started_at=START, planned_duration_seconds=120,
                        ended_at=START + 120, data={"order_ids": ["o1"]})
    check_purchase(by_activity(purchase), [], orders)
    with pytest.raises(IntegrityError, match=f"^{PHASES}$"):
        check_purchase(by_activity(purchase), [front(1, "buy", "alipay", "payment", 100, 120)], orders)


def trip_order(identity, fulfillment, status, expected, **fields):
    """一条已付款的外卖或快递记录：付款在 START + 200，预计到达写成相对 START 的秒数。"""
    arrived = START + expected if status != "pending" else None
    return {"order_id": identity, "agent_id": 1, "app_id": "luckin", "group_key": "group", "paid_at": START + 200,
            "expected_at": START + expected, "fulfillment": fulfillment, "status": status, "arrived_at": arrived,
            **fields}


def sent(sequence, title, at, orders, cause, **changes):
    row = notice(sequence, title, at=at, order_ids=list(orders))
    row["caused_by"] = [cause]
    row.update(changes)
    return row


def order_world(last=4000):
    """一次购买里瑞幸咖啡的一趟外卖（已送达）和一趟还在路上的快递，再加一条到店记录；last 是导出的最后时刻。"""
    orders = {"o1": trip_order("o1", "delivery", "delivered", 900),
              "o2": trip_order("o2", "station_pickup", "pending", 20200, group_key="parcel"),
              "o3": trip_order("o3", "pickup", "delivered", 200, app_id=None, group_key="store")}
    state = {"deliveries": orders, "activities": {"buy": {"activity_id": "buy", "kind": "purchase",
                                                          "data": {"order_ids": ["o1", "o2", "o3"]}}}}
    paid = system(1, {"kind": "purchase_payment", "activity_id": "buy", "order_ids": ["o1", "o2"],
                      "amount_fen": 100}, at=START + 200)
    arrival = system(4, {"kind": "delivery_arrival", "scheduled_id": "delivery-o1", "orders": [
        {"order_id": "o1", "status": "delivered"}]}, at=START + 900, causes=("event-1",))
    events = [paid, sent(2, "订单已下单", 200, ["o1", "o2"], "event-1"),
              sent(3, "订单已发货", 270, ["o1"], "event-1"), arrival,
              sent(5, "订单已送达", 900, ["o1"], "event-4"),
              sent(6, "订单已发货", 2200, ["o2"], "event-1"),
              system(7, {"kind": "time_trigger", "key": "hourly", "scheduled_id": "end"}, at=START + last, causes=())]
    return events, state


COUNT = "Order notification count disagrees with actual payment, shipment or delivery"
CONTENT = "Order notification disagrees with its actual order event"


def test_order_notices_of_a_delivered_takeaway_and_a_parcel_on_its_way_are_certified():
    invariants._order_notifications(*order_world())
    # 快递的发货时刻晚于导出里最后一条事件的时刻：这一趟还不该有发货通知。
    events, state = order_world(last=2100)
    with pytest.raises(IntegrityError, match=f"^{COUNT}$"):
        invariants._order_notifications(events, state)
    del events[5]
    invariants._order_notifications(events, state)


@pytest.mark.parametrize("fault,reason", [
    ("shipped_missing", COUNT), ("delivered_for_the_parcel", COUNT), ("placed_for_the_store_order", COUNT),
    ("notice_of_an_unpaid_order", COUNT), ("shipped_at_another_second", CONTENT),
    ("shipped_caused_by_the_arrival", CONTENT), ("placed_by_another_person", CONTENT),
    ("delivered_with_money", CONTENT),
])
def test_order_notices_that_disagree_with_payment_shipment_or_delivery_are_refused(fault, reason):
    events, state = order_world()
    if fault == "shipped_missing":
        del events[2]
    elif fault == "delivered_for_the_parcel":
        events.insert(6, sent(8, "订单已送达", 2300, ["o2"], "event-4"))
    elif fault == "placed_for_the_store_order":
        events.insert(2, sent(8, "订单已下单", 200, ["o3"], "event-1"))
    elif fault == "notice_of_an_unpaid_order":
        state["deliveries"]["o1"]["paid_at"] = state["deliveries"]["o2"]["paid_at"] = None
    elif fault == "shipped_at_another_second":
        events[2] = sent(3, "订单已发货", 271, ["o1"], "event-1")
    elif fault == "shipped_caused_by_the_arrival":
        events[5]["caused_by"] = ["event-4"]
    elif fault == "placed_by_another_person":
        events[1]["agent_id"] = 2
    else:
        events[4]["resource_changes"] = {"account:agent:1": -1, "account:external:scenario": 1}
    with pytest.raises(IntegrityError, match=f"^{reason}$"):
        invariants._order_notifications(events, state)
