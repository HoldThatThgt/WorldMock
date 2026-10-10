"""真实推送、内容选择与订单边界的拒绝和幂等行为。"""

from random import Random
from types import SimpleNamespace

import pytest

from tests.unit.life.test_environment_content import context_state
from tests.unit.life.test_environment_commerce import (
    environment, finish, operate_payments, pending, runtime, world,
)
from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError
from worldmock.life.app_push import issue_app_push, open_app_push, release_app_pushes, validate_app_pushes
from worldmock.life.basket import activity_orders
from worldmock.life.commerce import deliver_order
from worldmock.life.content_select import ContentRequest, select_from_contents
from worldmock.life.interact import InteractivityRuntime
from worldmock.life.models import AppPush, ContentProgress
from worldmock.life.parcel import require_ready_parcels
from worldmock.life.purchase_offers import quote_goods
from worldmock.life.receipt import settle_receipt


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
    changed, activity, drafts = InteractivityRuntime(context).start(state, pending(), "app", data["app_id"])
    assert activity.data["content_id"] == data["content_id"]
    assert activity.data["pinned_by_push_id"] == data["push_id"]
    assert changed.sessions["stay"].app_pushes[data["app_id"]].status == "opened" and drafts == []
    assert state.sessions["stay"].app_pushes[data["app_id"]].status == "pending"


def test_browse_resume_keeps_the_same_activity_and_unknown_content_resume_is_rejected(context_state):
    context, state = context_state
    interactivity = InteractivityRuntime(context)
    changed, activity, _ = interactivity.start(state, pending(), "app", "meituan")
    assert activity.data["content_id"] is None and interactivity.resume(changed, activity) is changed
    content_state, content_activity, _ = interactivity.start(state, pending(), "app", "bilibili")
    broken = content_activity.model_copy(update={"data": {**content_activity.data, "content_id": "unknown"}})
    before = content_state.model_dump(mode="json")
    with pytest.raises(IntegrityError, match="Persisted activity target is invalid"):
        interactivity.resume(content_state, broken)
    assert content_state.model_dump(mode="json") == before


def test_started_series_precedes_unseen_independent_content_without_random_redraw(context_state):
    context, state = context_state
    state.agents[1].content_progress["c-drama-1"] = ContentProgress(content_id="c-drama-1",
        position_seconds=0, completed_rounds=1, last_consumed_at=state.current_time)
    contents = tuple(context.catalog.contents[key] for key in ("c-course", "c-drama-1", "c-drama-2", "c-drama-3"))
    stream = Random(7)
    before = stream.getstate()
    selected, pin = select_from_contents(contents, state, ContentRequest(1, "app", "bilibili"), stream)
    assert selected.content_id == "c-drama-2" and pin is None
    assert stream.getstate() == before


def test_empty_content_pool_is_explicit_exhaustion(context_state):
    _, state = context_state
    with pytest.raises(BusinessRuleError, match="CONTENT_EXHAUSTED"):
        select_from_contents((), state, ContentRequest(1, "app", "bilibili"), Random(7))


def test_unknown_goods_and_orders_never_change_resources(environment):
    state, commerce = world(environment), runtime(environment)
    before = state.model_dump(mode="json")
    with pytest.raises(BusinessRuleError, match="SUPPLY_NOT_FOUND"):
        quote_goods(environment, state, 1, "unknown")
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_FOUND"):
        deliver_order(state, commerce.context, "unknown")
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_FOUND"):
        settle_receipt(state, commerce.context, "unknown")
    assert state.model_dump(mode="json") == before


def test_paid_courier_rejects_early_arrival_and_repeated_station_arrival_is_identity(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment), pending(), ("online-notebook",))
    state, _ = operate_payments(commerce, state, activity)
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
    state, activity = commerce.prepare_purchase(world(environment), pending(), ("lemon-tea",))
    state, _ = operate_payments(commerce, state, activity)
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


def test_partially_paid_purchase_is_rejected_before_other_group_is_charged(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store"), pending(),
                                                ("lemon-tea", "online-notebook"))
    first = activity_orders(state, activity)[0]
    state.deliveries[first.order_id] = first.model_copy(update={"paid_at": state.current_time})
    before = state.model_dump(mode="json")
    with pytest.raises(IntegrityError, match="partially paid groups"):
        commerce.complete_purchase(state, activity)
    assert state.model_dump(mode="json") == before


def test_skipped_payment_end_rejects_without_backdating_notification(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment, "store", random_state="payment-0"),
                                                pending(), ("lemon-tea", "online-notebook"))
    start, end = commerce.payment_boundaries(state, activity)[:2]
    started = state.model_copy(update={"current_time": start}, deep=True)
    started, touches = commerce.advance_payment(started, activity)
    assert len(touches) == 1
    skipped = started.model_copy(update={"current_time": end + 1}, deep=True)
    before = skipped.model_dump(mode="json")
    with pytest.raises(IntegrityError, match="end boundary was skipped"):
        commerce.advance_payment(skipped, activity)
    assert skipped.model_dump(mode="json") == before


def test_parcel_collection_requires_real_station_then_receives_all_once(environment):
    commerce = runtime(environment)
    state, activity = commerce.prepare_purchase(world(environment), pending(), ("online-notebook",))
    state, _ = operate_payments(commerce, state, activity)
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
