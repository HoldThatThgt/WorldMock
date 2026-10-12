"""真实推送、内容选择与订单边界的拒绝和幂等行为。"""

from random import Random
from types import SimpleNamespace

import pytest

from tests.life.prerequisite_inputs import (
    CLOCK, app_content, dingtalk_environment, moment, silence_other_pushes, weekday_hours,
)
from tests.unit.life.test_environment_content import NOTICE, context_state, dingtalk_world, world_at
from tests.unit.life.test_environment_commerce import (
    environment, finish, operate_phases, pending, runtime, world,
)
from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError
from worldmock.life.action_contract import binding_action
from worldmock.life.app_push import issue_app_push, open_app_push, release_app_pushes, validate_app_pushes
from worldmock.life.basket import activity_orders
from worldmock.life.commerce import deliver_order
from worldmock.life.content_select import ContentRequest, eligible_contents, select_from_contents
from worldmock.life.interact import InteractivityRuntime
from worldmock.life.location_activities import location_units, unit_binding
from worldmock.life.models import (
    ActivityStatus, AppPush, ContentProgress, SessionKind, SessionState, SessionStatus,
)
from worldmock.life.parcel import require_ready_parcels
from worldmock.life.purchase_offers import quote_goods
from worldmock.life.purchase_phases import PAYMENT, activity_phases, advance_phases
from worldmock.life.receipt import settle_receipt


def app_row(context, app_id):
    """内容 app 的绑定行（与观察的私有绑定同形），选中它唯一的 interact 行。"""
    action = binding_action("interact")
    return {"object": context.catalog.apps[app_id].name, "kind": "app", "object_id": app_id,
            "actions": [action], "selected_action": action}


def issued(context_state):
    context, state = context_state
    event = SimpleNamespace(agent_id=1, scheduled_id="actual-push", due_at=state.current_time)
    changed, data = issue_app_push(state, event, context.catalog)
    return context, changed, data


@pytest.mark.parametrize("app,content", [("unknown", "c-course"), ("bilibili", "unknown"),
                                       ("bilibili", "parcel-service"), ("bilibili", "c-long")])
def test_restore_rejects_push_without_actual_app_binding_and_eligibility(context_state, app, content):
    context, state = context_state
    if content == "c-long":
        content = next(item.content_id for item in context.catalog.contents.values()
                       if "bilibili" in item.app_ids and not item.pushable)
    state.sessions["stay"].app_pushes[app] = AppPush(push_id="broken", content_id=content,
                                                  issued_at=state.current_time, status="pending")
    with pytest.raises(IntegrityError, match="invalid app or content"):
        validate_app_pushes(state, context.catalog)


def test_push_requires_a_current_session_and_release_without_session_is_identity(context_state):
    context, state = context_state
    absent = state.model_copy(update={"sessions": {}}, deep=True)
    event = SimpleNamespace(agent_id=1, scheduled_id="actual-push", due_at=state.current_time)
    with pytest.raises(IntegrityError, match="active session"):
        issue_app_push(absent, event, context.catalog)
    assert release_app_pushes(absent, 1, "c-course") is absent
    assert open_app_push(absent, 1, "bilibili", "c-course") == (absent, None)


def test_matching_push_opens_once_and_completion_release_prevents_reopening(context_state):
    _, state, data = issued(context_state)
    app, content = data["app_id"], data["content_id"]
    assert open_app_push(state, 1, app, "unknown") == (state, None)
    opened, push = open_app_push(state, 1, app, content)
    assert push.status == "opened" and state.sessions["stay"].app_pushes[app].status == "pending"
    repeated, duplicate = open_app_push(opened, 1, app, content)
    assert repeated is opened and duplicate == push
    released = release_app_pushes(opened, 1, content)
    assert released.sessions["stay"].app_pushes[app].status == "released"
    assert open_app_push(released, 1, app, content) == (released, None)


def test_actual_interaction_starts_the_pinned_content_and_opens_its_current_push(context_state):
    context, state, data = issued(context_state)
    changed, activity, drafts = InteractivityRuntime(context).start(state, pending(), app_row(context, data["app_id"]),
                                                                    "home")
    assert activity.data["content_id"] == data["content_id"] and activity.resource_claims == {"second"}
    assert activity.data["pinned_by_push_id"] == data["push_id"]
    assert changed.sessions["stay"].app_pushes[data["app_id"]].status == "opened" and drafts == []
    assert state.sessions["stay"].app_pushes[data["app_id"]].status == "pending"


def test_browse_resume_keeps_the_same_activity_and_unknown_content_resume_is_rejected(context_state):
    context, state = context_state
    interactivity = InteractivityRuntime(context)
    changed, activity, _ = interactivity.start(state, pending(), app_row(context, "meituan"), "home")
    assert activity.data["content_id"] is None and interactivity.resume(changed, activity) is changed
    content_state, content_activity, _ = interactivity.start(state, pending(), app_row(context, "bilibili"), "home")
    broken = content_activity.model_copy(update={"data": {**content_activity.data, "content_id": "unknown"}})
    before = content_state.model_dump(mode="json")
    with pytest.raises(IntegrityError, match="Persisted activity target is invalid"):
        interactivity.resume(content_state, broken)
    assert content_state.model_dump(mode="json") == before


@pytest.mark.parametrize("name,content_id,seconds", [("测试休息", "c-rest", 900), ("测试闲聊", "c-chat", 600)])
def test_named_location_unit_starts_exactly_that_content_in_the_first_slot(context_state, name, content_id, seconds):
    """在地点做事由人物点名单元：开始的就是这个单元的内容，引擎不替人物在地点的内容里挑选。"""
    context, state = context_state
    home = context.catalog.locations[state.agents[1].location_id]
    unit = next(unit for unit in location_units(context.catalog, home.kind) if unit.name == name)
    action = unit_binding(unit)
    row = {"object": home.display_name, "kind": "location", "object_id": home.location_id,
           "actions": [action], "selected_action": action}
    changed, activity, drafts = InteractivityRuntime(context).start(state, pending(), row, home.location_id)
    assert (activity.data["content_id"], activity.data["app_id"]) == (content_id, None)
    assert activity.data["pinned_by_push_id"] is None and activity.data["draw_index"] == 0
    assert activity.planned_duration_seconds == seconds and activity.resource_claims == {"first"}
    assert activity.name.startswith(f"进行{name}（") and activity.activity_id == "activity-buy"
    assert drafts == [] and changed.random_state == state.random_state and not state.activities


def test_started_series_precedes_unseen_independent_content_without_random_redraw(context_state):
    context, state = context_state
    state.agents[1].content_progress["c-drama-1"] = ContentProgress(content_id="c-drama-1",
        position_seconds=0, completed_rounds=1, last_consumed_at=state.current_time)
    contents = tuple(context.catalog.contents[key] for key in ("c-course", "c-drama-1", "c-drama-2", "c-drama-3"))
    stream = Random(7)
    before = stream.getstate()
    selected, pin = select_from_contents(contents, state, ContentRequest(1, "bilibili", "home"), stream)
    assert selected.content_id == "c-drama-2" and pin is None
    assert stream.getstate() == before


def test_unknown_goods_and_orders_never_change_resources(environment):
    state, commerce = world(environment), runtime(environment)
    before = state.model_dump(mode="json")
    with pytest.raises(BusinessRuleError, match="SUPPLY_NOT_FOUND"):
        quote_goods(environment, state, 1, "unknown", None)
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_FOUND"):
        deliver_order(state, commerce.context, "unknown")
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_FOUND"):
        settle_receipt(state, commerce.context, "unknown")
    assert state.model_dump(mode="json") == before


def test_paid_courier_rejects_early_arrival_and_repeated_station_arrival_is_identity(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment), pending(), ("online-notebook",), None)
    state = operate_phases(state, activity)
    state, activity = finish(state, activity)
    state, _ = commerce.complete_purchase(state, activity)
    order = activity_orders(state, activity)[0]
    with pytest.raises(BusinessRuleError, match="DELIVERY_NOT_DUE"):
        deliver_order(state, commerce.context, order.order_id)
    due = state.model_copy(update={"current_time": order.expected_at}, deep=True)
    arrival = deliver_order(due, commerce.context, order.order_id)
    duplicate = deliver_order(arrival.state, commerce.context, order.order_id)
    assert duplicate.state is arrival.state and duplicate.order == arrival.order and duplicate.receipts == ()
    assert arrival.state.accounts == state.accounts and arrival.state.agents[1].body == state.agents[1].body
    assert arrival.order.status == "ready_for_pickup" and arrival.order.received_at is None


def test_paid_takeout_is_received_at_actual_arrival_with_exactly_one_goods_effect(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment), pending(), ("lemon-tea",), None)
    state = operate_phases(state, activity)
    state, activity = finish(state, activity)
    state, _ = commerce.complete_purchase(state, activity)
    order = activity_orders(state, activity)[0]
    assert order.fulfillment == "delivery"
    due = state.model_copy(update={"current_time": order.expected_at}, deep=True)
    received = deliver_order(due, commerce.context, order.order_id)
    assert received.order.status == "delivered" and received.order.received_at == due.current_time
    assert len(received.receipts) == 1 and received.receipts[0].data["goods_id"] == "lemon-tea"
    assert received.state.agents[1].body.relaxation_need == pytest.approx(state.agents[1].body.relaxation_need - 0.1)
    assert received.state.accounts == state.accounts
    duplicate = deliver_order(received.state, commerce.context, order.order_id)
    assert duplicate.state is received.state and duplicate.receipts == ()


def test_partially_paid_purchase_is_rejected_before_any_order_is_charged(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store"), pending(),
                                                ("lemon-tea", "online-notebook"), None)
    first = activity_orders(state, activity)[0]
    state.deliveries[first.order_id] = first.model_copy(update={"paid_at": state.current_time})
    before = state.model_dump(mode="json")
    with pytest.raises(IntegrityError, match="^Purchase contains partially paid orders$"):
        commerce.complete_purchase(state, activity)
    assert state.model_dump(mode="json") == before


def test_skipped_payment_end_is_a_run_fault_that_leaves_the_orders_untouched(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store", random_state="payment-0"),
                                                pending(), ("lemon-tea", "online-notebook"), None)
    phase = next(item for item in activity_phases(state, activity) if item.step == PAYMENT)
    assert phase.started_at > state.current_time
    started = advance_phases(state.model_copy(update={"current_time": phase.started_at}, deep=True), activity,
                             state.current_time)
    paying = [order for order in started.deliveries.values() if order.payment_started_at is not None]
    assert paying and all((order.payment_started_at, order.payment_ended_at) == (phase.started_at, None)
                          for order in paying)
    skipped = started.model_copy(update={"current_time": phase.ended_at + 1}, deep=True)
    before = skipped.model_dump(mode="json")
    with pytest.raises(IntegrityError, match="^Purchase phase boundary was skipped$"):
        advance_phases(skipped, activity, phase.started_at)
    assert skipped.model_dump(mode="json") == before


def test_parcel_collection_requires_real_station_then_receives_all_once(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment), pending(), ("online-notebook",), None)
    state = operate_phases(state, activity)
    state, activity = finish(state, activity)
    state, _ = commerce.complete_purchase(state, activity)
    order = activity_orders(state, activity)[0]
    state = deliver_order(state.model_copy(update={"current_time": order.expected_at}, deep=True),
                          commerce.context, order.order_id).state
    with pytest.raises(PreconditionError):
        require_ready_parcels(state, commerce.context, 1, "parcel-station")
    state.agents[1].location_id = "parcel-station"
    changed, receipts = commerce.collect_parcels(state, 1, "parcel-station")
    assert len(receipts) == 1 and changed.deliveries[order.order_id].status == "delivered"
    assert changed.accounts == state.accounts


OFFICE_MORNING = {"location_kinds": ["office"], "opens": weekday_hours(("08:00", "10:00"))}
MORNING = app_content("dingtalk-morning", "晨会纪要", OFFICE_MORNING, pushable=True)
REMINDER = app_content("dingtalk-reminder", "日报提醒", {"opens": weekday_hours(("17:00", "20:00"))}, pushable=True)
EPISODES = tuple(app_content(f"dingtalk-week-{number}", f"周会第{number}段", OFFICE_MORNING,
                             series={"title": "周会", "episode": number}) for number in (1, 2, 3))


def quiet_world(tmp_path, contents, when, location_id, origin_id="home"):
    """钉钉带给定内容、其他 app 的内容都不可推送的最小真实世界；location_id 为 None 时人在从 origin_id 出发的路上。"""
    root = silence_other_pushes(dingtalk_environment(tmp_path / "input", *contents))
    return world_at(root, moment(*when), location_id, origin_id)


def push(context, state):
    """在当前这一秒处理一条整点推送事件。"""
    event = SimpleNamespace(agent_id=1, scheduled_id="actual-push", due_at=state.current_time)
    return issue_app_push(state, event, context.catalog)


@pytest.mark.parametrize("when,location_id,pushed", [
    ((12, 9, 0), "office", "dingtalk-morning"),
    ((12, 9, 0), "home", None),
    ((12, 10, 0), "office", None),
    ((17, 9, 0), "office", None),
], ids=["office-in-hours", "home", "hours-over", "saturday"])
def test_push_candidates_are_the_pushable_contents_eligible_at_that_second(tmp_path, when, location_id, pushed):
    """推送候选只含此刻合格且标了 pushable 的内容；没写前提但不可推送的公告从不是候选。"""
    context, state = quiet_world(tmp_path, (MORNING, NOTICE), when, location_id)
    changed, data = push(context, state)
    if pushed is None:
        assert data == {"kind": "app_push", "pushed": False, "session_id": "stay"}
        assert changed is state and not state.sessions["stay"].app_pushes
        return
    assert (data["app_id"], data["content_id"], data["title"]) == ("dingtalk", pushed, "晨会纪要")
    assert changed.sessions["stay"].app_pushes["dingtalk"].status == "pending"
    assert changed.random_state == state.random_state
    validate_app_pushes(changed, context.catalog)


@pytest.mark.parametrize("origin_id,destination_id", [("home", "office"), ("office", "home")],
                         ids=["towards-the-office", "after-leaving-the-office"])
def test_content_that_needs_a_place_is_no_push_candidate_on_the_road(tmp_path, origin_id, destination_id):
    """人在路上既不算在目的地也不算在出发地：周一 09:00 去办公室的路上，或刚离开办公室（人物记录的地点仍是办公室），
    只在办公室能开始的晨会纪要都不是候选。"""
    context, state = quiet_world(tmp_path, (MORNING, NOTICE), (12, 9, 0), None, origin_id)
    road = state.sessions["stay"]
    assert (road.kind, road.origin_id, road.destination_id) == (SessionKind.TRANSIT, origin_id, destination_id)
    assert state.agents[1].location_id == origin_id
    changed, data = push(context, state)
    assert data == {"kind": "app_push", "pushed": False, "session_id": "stay"}
    assert changed is state and not road.app_pushes


@pytest.mark.parametrize("hour,pushed", [(18, True), (16, False)])
def test_time_only_pushable_content_is_judged_by_the_clock_on_the_road(tmp_path, hour, pushed):
    """人在路上时带地点前提的内容不是候选，只带时段前提的内容照常按时刻判定。"""
    context, state = quiet_world(tmp_path, (MORNING, REMINDER), (12, hour, 0), None)
    _, data = push(context, state)
    assert data.get("content_id") == ("dingtalk-reminder" if pushed else None)
    assert data.get("pushed", True) is pushed


def finish_episode(interactivity, state, activity):
    """把活动当前这一集推进到计划结束，再交给内容结算（同系列在这里接下一集）。"""
    content = interactivity.context.catalog.contents[activity.data["content_id"]]
    end = activity.planned_end_at
    done = activity.model_copy(update={
        "media_position_seconds": float(content.duration_seconds), "last_settled_at": end, "ended_at": end,
        "elapsed_seconds": activity.elapsed_seconds + content.duration_seconds, "status": ActivityStatus.COMPLETED})
    staged = state.model_copy(update={"current_time": end, "activities": {**state.activities, done.activity_id: done}})
    changed, evidence = interactivity.advance(staged, done, end)
    return changed, changed.activities[done.activity_id], evidence


@pytest.mark.parametrize("lapse,started", [("hours", (12, 9, 59, 30)), ("road", (12, 9, 0, 0))])
def test_series_with_a_prerequisite_keeps_continuing_after_the_prerequisite_lapses(tmp_path, lapse, started):
    """前提只卡开始：在办公室、在时段内开始的系列，时段过了（09:59:30 开始）或人已在路上（09:00 开始后出发），
    一集做完仍自动接下一集直到播完。"""
    context, state = dingtalk_world(tmp_path, EPISODES, when=started)
    interactivity = InteractivityRuntime(context)
    state, activity, _ = interactivity.start(state, pending(), app_row(context, "dingtalk"), "office")
    assert activity.data["content_id"] == "dingtalk-week-1" and activity.planned_end_at == moment(*started) + 60
    activity = activity.model_copy(update={"data": {**activity.data, "accepted_event_id": "accepted"}})
    if lapse == "road":
        road = SessionState(session_id="stay", agent_id=1, kind=SessionKind.TRANSIT, origin_id="office",
                            destination_id="home", status=SessionStatus.ACTIVE, started_at=state.current_time,
                            name="去家的路上")
        state = state.model_copy(update={"sessions": {"stay": road}})
    request = ContentRequest(1, "dingtalk", None if lapse == "road" else "office")
    for following in ("dingtalk-week-2", "dingtalk-week-3"):
        state, activity, evidence = finish_episode(interactivity, state, activity)
        # 这一集做完的那一秒，这个 app 已经没有合格内容，新的一次打开会被拒绝；接续不判定前提
        assert eligible_contents(context.catalog, state, request) == ()
        assert evidence["completed"] is True and activity.status == ActivityStatus.ACTIVE
        assert activity.data["content_id"] == following and activity.planned_end_at == state.current_time + 60
    state, activity, evidence = finish_episode(interactivity, state, activity)
    assert evidence["completed"] is True and activity.status == ActivityStatus.COMPLETED
    assert state.current_time == moment(*started) + 180
    assert {key: item.completed_rounds for key, item in state.agents[1].content_progress.items()} == {
        "dingtalk-week-1": 1, "dingtalk-week-2": 1, "dingtalk-week-3": 1}


def test_restore_accepts_a_lapsed_push_and_a_running_content_whose_prerequisite_no_longer_holds(tmp_path):
    """带着不合格推送和进行中的带前提内容的世界是合法状态，恢复核对不加前提这一项。"""
    from worldmock.life.activity_validation import validate_activity_targets

    context, state = quiet_world(tmp_path, (CLOCK, MORNING), (12, 9, 0), "office")
    state, data = push(context, state)
    assert data["content_id"] == "dingtalk-morning"
    interactivity = InteractivityRuntime(context)
    state = state.model_copy(update={"current_time": moment(12, 9, 59)}, deep=True)
    state, activity, _ = interactivity.start(state, pending(), app_row(context, "dingtalk"), "office")
    assert activity.data["pinned_by_push_id"] == data["push_id"]
    activity = activity.model_copy(update={"data": {**activity.data, "accepted_event_id": "accepted"}})
    # 推送打开后时段结束、人回了家：进行中的晨会内容与另一条仍未打开的推送都不再满足前提
    pending_push = AppPush(push_id="later", content_id="dingtalk-morning", issued_at=moment(12, 9), status="pending")
    home = state.sessions["stay"].model_copy(update={"location_id": "home", "app_pushes": {"dingtalk": pending_push}})
    lapsed = state.model_copy(update={"current_time": moment(12, 12, 30), "sessions": {"stay": home},
                                      "activities": {activity.activity_id: activity}})
    assert eligible_contents(context.catalog, lapsed, ContentRequest(1, "dingtalk", "home")) == ()
    validate_activity_targets(lapsed, context.catalog, context.persona)
    validate_app_pushes(lapsed, context.catalog)
