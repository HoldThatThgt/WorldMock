"""@brief 七条独立足迹约束与真实商品收货、驿站到站所有权的纯反例。"""

from copy import deepcopy

import pytest

from tests.unit.footprints.test_app_usage import START, _app, _event, _progress, _push, _travel
from tests.unit.footprints.test_purchases import result_event
from tests.life.purchase_worlds import ENVIRONMENT, at_time, registered_world
from tests.integration.life.test_acs_system_core import parcel_request, place, system_world
from tests.integration.life.test_commerce import order_request
from worldmock.errors import IntegrityError
from worldmock.footprints.app_usage import project_app_usage
from worldmock.footprints.export import reconstruct_activities
from worldmock.footprints.invariants import validate_footprint_facts
from worldmock.footprints.models import Event, EventDraft
from worldmock.footprints.purchases import project_purchase_facts
from worldmock.life.basket import purchase_order
from worldmock.life.parcel import collect_parcels
from worldmock.scheduling.effects import process_scheduled_events


def persisted(draft, sequence):
    """@brief 真实领域草稿补齐存储分配的严格全序，因果始终指向真正事件身份。"""
    return Event(**draft.model_dump(), event_seq=sequence, run_id="pure-food",
                 recorded_at="2026-01-05T08:00:00+00:00", wall_elapsed_ms=sequence,
                 state_version_before=0, state_version_after=1).model_dump(mode="json")


def app_material(events):
    """@brief 从同一实际事件流重算活动及六种应用事实。"""
    for event in events:
        snapshot = event["data"].get("activity")
        if snapshot is not None and event["event_type"] == "activity_progress":
            snapshot["elapsed_seconds"] = event["data"]["interval_end"] - snapshot["started_at"]
    activities = reconstruct_activities([Event.model_validate(event) for event in events])
    rows = project_app_usage(events)
    validate_footprint_facts(events, activities, [], rows, {"deliveries": {}})
    return activities, rows


def current_app_events():
    """@brief 一次完整内容完成产生五种应用事实，钉住来源是真实先前推送。"""
    activity = _app(pin="push")
    return [_push(1), _event(2, "action_accepted", {"activity": activity}, activity_id="phone"),
            _progress(3, deepcopy(activity), completed=True, rounds=1)]


def receipt_material(recipient="agent:1"):
    """@brief 通过真实目录、购买与排期交付构造付款人和收货人的独立事实。"""
    semantics, state = registered_world(scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    destination = "home" if recipient.startswith("agent:") else "office"
    purchased = purchase_order(state, semantics, 1, order_request(destination=destination, recipient=recipient))
    result = result_event(purchased, state.current_time)
    changed = purchased.state.model_copy(deep=True)
    changed.pending_events = [item.model_copy(update={"caused_by": [result["event_id"]]})
                              for item in changed.pending_events]
    final, drafts, _ = process_scheduled_events(at_time(changed, purchased.orders[0].expected_at),
                                               purchased.orders[0].expected_at, semantics)
    events = [result, *(persisted(draft, sequence) for sequence, draft in enumerate(drafts, 2))]
    rows = project_purchase_facts(events)
    state = final.model_dump(mode="json")
    validate_footprint_facts(events, [], rows, [], state)
    return events, rows, state


def station_material(tmp_path, collected=True):
    """@brief 长途真实到站与本人现场领取分别生成独立来源，不重放通知。"""
    semantics, state = system_world(tmp_path, shipping=90001)
    purchased = purchase_order(state, semantics, 1, parcel_request(semantics, state, recipient="agent:2"))
    result = result_event(purchased, state.current_time)
    changed = purchased.state.model_copy(deep=True)
    changed.pending_events = [item.model_copy(update={"caused_by": [result["event_id"]]})
                              for item in changed.pending_events]
    final, drafts, _ = process_scheduled_events(at_time(changed, purchased.orders[0].expected_at),
                                               purchased.orders[0].expected_at, semantics)
    events = [result, *(persisted(draft, sequence) for sequence, draft in enumerate(drafts, 2))]
    if collected:
        place(final, 2, purchased.orders[0].destination_id)
        outcome = collect_parcels(final, semantics, 2, purchased.orders[0].destination_id)
        cause = EventDraft(event_type="action_result", occurred_at=final.current_time, agent_id=2,
                           action_id="collection", data={"status": "completed", "data": {
                               "orders": [order.model_dump(mode="json") for order in outcome.orders]}})
        events.append(persisted(cause, len(events) + 1))
        for draft in outcome.receipts:
            draft.caused_by = [cause.event_id]
            events.append(persisted(draft, len(events) + 1))
        final = outcome.state
    rows, state = project_purchase_facts(events), final.model_dump(mode="json")
    return events, rows, state


@pytest.mark.parametrize("manner,device", [("phone_continuous", "phone"), ("other_device", "other")])
def test_continuous_facts_keep_current_eight_common_fields_and_actual_segments(manner, device):
    activity = _app(manner=manner)
    events = [_event(1, "action_accepted", {"activity": activity}, activity_id="phone"),
              _progress(2, deepcopy(activity))]
    activities, rows = app_material(events)
    assert {row["fact_kind"] for row in rows} == {"app_use", "content_consumed"}
    assert all(row["action_id"] == "recommend" and row["manner"] == manner and row["device"] == device
               for row in rows)
    assert rows[0]["segments"] == activities[0]["segments"]
    assert "started_at" not in rows[0] and "ended_at" not in rows[0]
    assert all("kind" not in row for row in rows)


@pytest.mark.parametrize("ending,edges", [("completed", ["start", "end"]), ("cancelled", ["start", "end"]),
                                          ("failed", ["start", "end"]), ("truncated", ["start"])])
def test_offline_action_and_navigation_only_record_true_start_and_end_touches(ending, edges):
    action = _app(manner="offline_assisted", content=None)
    end = deepcopy(action)
    end.update(status=ending, ended_at=START + 300, elapsed_seconds=300)
    events = [_event(1, "action_accepted", {"activity": action}, activity_id="phone"),
              _event(2, "environment_event", {"kind": "activity_paused", "activity": {
                  **deepcopy(action), "status": "paused"}}, at=START, activity_id="phone"),
              _event(3, "environment_event", {"kind": "activity_resumed", "activity": action},
                     at=START, activity_id="phone"),
              _event(4, "activity_progress", {"activity": end, "interval_start": START,
                     "interval_end": START + 300}, at=START + 300, activity_id="phone")]
    projected = project_app_usage(events)
    assert len(projected) == len(edges) and all(row["fact_kind"] == "app_touch" for row in projected)
    assert [row["edge"] for row in projected] == edges
    _, rows = app_material(events)
    assert [row["edge"] for row in rows] == edges
    assert all(row["fact_kind"] == "app_touch" and row["device"] == "phone" for row in rows)
    travel = _travel(completed=True)
    travel["data"]["navigation_app_id"] = "amap"
    travel["elapsed_seconds"] = 300
    initial = {**deepcopy(travel), "status": "active", "ended_at": None, "elapsed_seconds": 0}
    _, navigation = app_material([_event(1, "action_accepted", {"activity": initial}, activity_id="commute"),
        _event(2, "activity_progress", {"activity": travel, "interval_start": START,
            "interval_end": START + 300}, at=START + 300, activity_id="commute")])
    assert [row["edge"] for row in navigation] == ["start", "end"]
    assert all(row["action_id"] is None and row["manner"] == "offline_assisted" for row in navigation)


def test_no_app_action_never_generates_digital_facts_and_guard_rejects_invented_use():
    activity = _app(app_id=None, manner=None, content=None, action_id="rest")
    activity["data"]["category"] = "daily_care"
    event = _event(1, "action_accepted", {"activity": activity}, activity_id="phone")
    activities, rows = app_material([event])
    assert rows == []
    forged = {"fact_kind": "app_use", "agent_id": 1, "activity_id": "phone", "app_id": "douyin",
              "action_id": "rest", "manner": "phone_continuous", "device": "phone",
              "source_event_id": event["event_id"], "segments": [], "status": "active"}
    with pytest.raises(IntegrityError, match="no actual app identity"):
        validate_footprint_facts([event], activities, [], [forged], {"deliveries": {}})


@pytest.mark.parametrize("manner,rejected", [("phone_continuous", True), ("other_device", False)])
def test_phone_overlap_guard_rejects_real_reprojected_overlap_but_other_device_can_overlap(manner, rejected):
    first = _app(content="first")
    second = _app(identity="second", app_id="wechat", content="second", manner=manner, action_id="voice")
    events = [_progress(1, first, content="first"), _progress(2, second, content="second")]
    for event in events:
        event["data"]["activity"]["elapsed_seconds"] = 300
    activities = reconstruct_activities([Event.model_validate(row) for row in events])
    rows = project_app_usage(events)
    if rejected:
        with pytest.raises(IntegrityError, match="Phone app use intervals overlap"):
            validate_footprint_facts(events, activities, [], rows, {"deliveries": {}})
    else:
        validate_footprint_facts(events, activities, [], rows, {"deliveries": {}})


@pytest.mark.parametrize("corruption", ["missing_receipt", "duplicate_receipt", "missing_event", "duplicate_event"])
def test_delivered_order_guard_rejects_each_missing_or_duplicate_receipt_or_goods_event(corruption):
    events, rows, state = receipt_material("agent:2")
    if corruption == "missing_receipt":
        rows = [row for row in rows if row["fact_kind"] != "receipt"]
    elif corruption == "duplicate_receipt":
        rows.append(deepcopy(next(row for row in rows if row["fact_kind"] == "receipt")))
    elif corruption == "missing_event":
        events = [event for event in events if event["data"].get("kind") != "goods_received"]
        rows = project_purchase_facts(events)
    else:
        duplicate = deepcopy(events[-1])
        duplicate.update(event_id="duplicate", event_seq=4)
        events.append(duplicate)
        rows = project_purchase_facts(events)
    with pytest.raises(IntegrityError, match="Receipt and goods received counts"):
        validate_footprint_facts(events, [], rows, [], state)


def test_contact_receipt_belongs_to_payer_with_null_effects_and_no_goods_event():
    events, rows, state = receipt_material("contact-colleague")
    receipt = next(row for row in rows if row["fact_kind"] == "receipt")
    assert receipt["agent_id"] == 1 and receipt["provided"] is receipt["applied"] is None
    assert not any(event["data"].get("kind") == "goods_received" for event in events)
    source = events[-1]
    forged = {**deepcopy(source), "event_id": "contact-goods", "event_seq": 3,
              "data": {"kind": "goods_received", "order_id": receipt["order_id"], "provided": 100, "applied": 100}}
    with pytest.raises(IntegrityError, match="Receipt and goods received counts"):
        validate_footprint_facts([*events, forged], [], rows, [], state)
    receipt["provided"] = 0.05
    with pytest.raises(IntegrityError, match="Contact receipts cannot contain body effects"):
        validate_footprint_facts(events, [], rows, [], state)


@pytest.mark.parametrize("kind", ["delivery_arrival", "goods_received"])
def test_receipt_and_arrival_guard_rejects_account_changes_from_actual_source_events(kind):
    events, _, state = receipt_material()
    source = next(event for event in events if event["data"].get("kind") == kind)
    source["data"]["resource_changes"] = {"account:agent:1": -500, "account:merchant:noodle-1": 500}
    rows = project_purchase_facts(events)
    with pytest.raises(IntegrityError, match="cannot change account balances"):
        validate_footprint_facts(events, [], rows, [], state)


def test_receipt_time_guard_rejects_before_purchase_even_when_projection_agrees():
    events, _, state = receipt_material()
    order_id = next(iter(state["deliveries"]))
    state["deliveries"][order_id]["received_at"] = state["deliveries"][order_id]["created_at"] - 1
    for event in events:
        for order in event["data"].get("orders", []):
            order["received_at"] = state["deliveries"][order_id]["received_at"]
        if event["data"].get("kind") == "goods_received":
            event["occurred_at"] = state["deliveries"][order_id]["received_at"]
    with pytest.raises(IntegrityError, match="current receipt contract"):
        validate_footprint_facts(events, [], project_purchase_facts(events), [], state)


@pytest.mark.parametrize("corruption", ["after_backwards", "next_before_backwards", "outside_segment"])
def test_content_guard_rejects_backwards_cursor_and_consumption_in_pause(corruption):
    events = current_app_events()
    events[-1]["data"]["content"].update(completed=False, completed_rounds_after=0)
    activities, rows = app_material(events)
    content = next(row for row in rows if row["fact_kind"] == "content_consumed")
    if corruption == "after_backwards":
        content.update(position_before=300, position_after=250)
    elif corruption == "next_before_backwards":
        rows.append({**deepcopy(content), "position_before": 250, "position_after": 300})
    else:
        activities[0]["segments"] = [{"started_at": START, "ended_at": START + 100},
                                     {"started_at": START + 200, "ended_at": START + 300}]
        rows[0]["segments"] = activities[0]["segments"]
        content.update(interval_start=START + 100, interval_end=START + 200)
    with pytest.raises(IntegrityError, match="Content positions|outside actual activity"):
        validate_footprint_facts(events, activities, [], rows, {"deliveries": {}})


@pytest.mark.parametrize("fact", ["push_opened", "push_released", "content_consumed"])
@pytest.mark.parametrize("corruption", ["missing", "other_person", "later"])
def test_push_reference_guard_rejects_missing_other_person_or_later_delivery(fact, corruption):
    events = current_app_events()
    activities, rows = app_material(events)
    target = next(row for row in rows if row["fact_kind"] == fact)
    if corruption == "missing":
        target["push_id"] = "missing"
    elif corruption == "other_person":
        delivered = next(row for row in rows if row["fact_kind"] == "push_delivered")
        delivered["agent_id"] = 2
        events[0]["agent_id"] = 2
    else:
        events[0]["event_seq"] = 99
    with pytest.raises(IntegrityError, match="Push reference"):
        validate_footprint_facts(events, activities, [], rows, {"deliveries": {}})


@pytest.mark.parametrize("fact", ["app_use", "content_consumed", "push_delivered", "push_opened", "push_released"])
@pytest.mark.parametrize("corruption", ["missing", "other_person"])
def test_every_app_fact_source_must_exist_and_be_owned_by_the_fact_persona(fact, corruption):
    events = current_app_events()
    activities, rows = app_material(events)
    target = next(row for row in rows if row["fact_kind"] == fact)
    if corruption == "missing":
        target["source_event_id"] = "missing"
    else:
        source = next(event for event in events if event["event_id"] == target["source_event_id"])
        source["agent_id"] = 2
    with pytest.raises(IntegrityError, match="source event is missing or owned by another"):
        validate_footprint_facts(events, activities, [], rows, {"deliveries": {}})


@pytest.mark.parametrize("collected", [False, True])
def test_station_ready_and_received_have_one_actual_recipient_source_without_second_notification(tmp_path, collected):
    events, rows, state = station_material(tmp_path, collected)
    assert len([row for row in rows if row["fact_kind"] == "pickup_ready"]) == 1
    assert len([row for row in rows if row["fact_kind"] == "receipt"]) == int(collected)
    validate_footprint_facts(events, [], rows, [], state)
    purchase = next(row for row in rows if row["fact_kind"] == "purchase")
    ready = next(row for row in rows if row["fact_kind"] == "pickup_ready")
    assert purchase["agent_id"] == 1 and ready["agent_id"] == 2
    assert len([row for row in rows if row["fact_kind"] == "pickup_ready"]) == 1
    assert len([row for row in rows if row["fact_kind"] == "receipt"]) == int(collected)
    if collected:
        receipt = next(row for row in rows if row["fact_kind"] == "receipt")
        source = next(event for event in events if event["event_id"] == receipt["source_event_id"])
        assert source["agent_id"] == receipt["agent_id"] == 2 and source["data"]["kind"] == "goods_received"
        assert events[-2]["event_id"] == source["caused_by"][0]
    assert "inventory" not in state["agents"]["2"] and "contact_holdings" not in state


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "owner", "time", "source", "cause"])
def test_station_notification_guard_rejects_every_wrong_count_time_owner_source_or_cause(tmp_path, corruption):
    events, rows, state = station_material(tmp_path)
    validate_footprint_facts(events, [], rows, [], state)
    ready = next(row for row in rows if row["fact_kind"] == "pickup_ready")
    if corruption == "missing":
        rows.remove(ready)
    elif corruption == "duplicate":
        rows.append(deepcopy(ready))
    elif corruption == "owner":
        ready["agent_id"] = 1
    elif corruption == "time":
        ready["arrived_at"] += 1
    elif corruption == "source":
        ready["source_event_id"] = events[-1]["event_id"]
    else:
        arrival = next(event for event in events if event["event_id"] == ready["source_event_id"])
        arrival["caused_by"] = ["scheduled-delivery-id"]
    with pytest.raises(IntegrityError, match="source event|Parcel pickup notification|Parcel arrival"):
        validate_footprint_facts(events, [], rows, [], state)


def test_twenty_content_completions_still_share_one_actual_app_use():
    """@brief 接续只换内容，二十条真实完成区间仍归一项持续应用活动。"""
    activity = _app(content="unit-0")
    events = [_event(1, "action_accepted", {"activity": activity}, activity_id="phone")]
    for index in range(20):
        after = deepcopy(activity)
        after["data"]["content_id"] = f"unit-{index}"
        after.update(elapsed_seconds=(index + 1) * 360, media_position_seconds=0)
        event = _progress(index + 2, after, content=f"unit-{index}", completed=True, rounds=1)
        event["occurred_at"] = START + (index + 1) * 360
        event["data"].update(interval_start=START + index * 360, interval_end=event["occurred_at"])
        event["data"]["content"]["position_after_seconds"] = 360
        events.append(event)
    projected = project_app_usage(events)
    uses = [row for row in projected if row["fact_kind"] == "app_use"]
    assert len(uses) == 1 and uses[0]["segments"] == [{"started_at": START, "ended_at": START + 7200}]
    assert len([row for row in projected if row["fact_kind"] == "content_consumed"]) == 20
    activities, rows = app_material(events)
    uses = [row for row in rows if row["fact_kind"] == "app_use"]
    contents = [row for row in rows if row["fact_kind"] == "content_consumed"]
    assert len(uses) == 1 and uses[0]["segments"] == [{"started_at": START, "ended_at": START + 7200}]
    assert len(contents) == sum(row["completed"] for row in contents) == 20
    assert len({row["content_id"] for row in contents}) == 20
    assert activities[0]["elapsed_seconds"] == 7200


def test_no_app_content_counts_action_quality_without_digital_fact_or_empty_app_key():
    """@brief 无 app 内容仍是动作接触，数字事实和按 app 统计没有空身份。"""
    from worldmock.footprints.app_usage import app_usage_quality

    activity = _app(app_id=None, manner=None, content="work-unit", action_id="work")
    activity["data"]["category"] = "work_study"
    event = _progress(1, activity, content="work-unit")
    assert project_app_usage([event]) == []
    report = app_usage_quality([event], "Asia/Shanghai")["content_contact"]
    assert report["by_app"] == {} and report["by_action"] == {"work": 1}
    assert "" not in report["by_app"] and None not in report["by_app"]


@pytest.mark.parametrize("kind", ["purchase", "receipt", "order_attempt", "app_touch"])
@pytest.mark.parametrize("corruption", ["missing", "other_person"])
def test_purchase_attempt_receipt_and_touch_sources_require_the_actual_owner(kind, corruption):
    """@brief 购买、尝试、收货和辅助触点也受同一实际来源人物不变量约束。"""
    if kind == "app_touch":
        event = _event(1, "action_accepted", {"activity": _app(manner="offline_assisted")}, activity_id="phone")
        activities, app_rows = app_material([event])
        events, purchases, state = [event], [], {"deliveries": {}}
        row = app_rows[0]
    else:
        events, purchases, state = receipt_material()
        activities, app_rows = [], []
        if kind == "order_attempt":
            requested = persisted(EventDraft(event_type="action_requested", occurred_at=events[0]["occurred_at"],
                agent_id=1, action_id="goods-buy", data={"tool_name": "start_activity", "request": {
                    "items": [{"target": "牛肉面", "way": "购买", "app": "美团", "to": None,
                               "pay_app": "微信"}], "answering": None}}), 1)
            events.insert(0, requested)
            for sequence, event in enumerate(events, 1):
                event["event_seq"] = sequence
            purchases = project_purchase_facts(events)
        row = next(row for row in purchases if row["fact_kind"] == kind)
    key = "request_event_id" if kind == "order_attempt" else "source_event_id"
    if corruption == "missing":
        row[key] = "missing-event"
    else:
        source = next(event for event in events if event["event_id"] == row[key])
        source["agent_id"] = 2
    with pytest.raises(IntegrityError, match="source event is missing or owned by another"):
        validate_footprint_facts(events, activities, purchases, app_rows, state)
