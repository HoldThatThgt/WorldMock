"""目录浏览、购买、配送、库存位置与按进度消费测试。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agentsociety2.society import DirectToolCall

from conftest import advance_next, context
from worldmock.errors import BusinessRuleError, ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.commerce import apply_product_progress, deliver_order, purchase
from worldmock.life.models import (
    ActivityState,
    ActivityStatus,
    ConsumeRequest,
    FulfillmentKind,
    PurchaseRequest,
    StartBasicActivityRequest,
    StartTargetedActivityRequest,
    StartTravelRequest,
)


def _stage_open(engine) -> None:
    """把隔离夹具移到商家营业时段并保留之后的真实后台事件。"""
    zone = ZoneInfo(engine.scenario.timezone)
    epoch = int(datetime(2026, 1, 5, 10, 0, tzinfo=zone).timestamp())
    staged = engine.state.model_copy(deep=True)
    staged.current_time = epoch
    staged.pending_events = [event for event in staged.pending_events if event.due_at > epoch]
    for agent_id, agent in staged.agents.items():
        staged.agents[agent_id] = agent.model_copy(update={"next_decision_at": epoch}, deep=True)
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=epoch, visibility="system",
                       source_kind="scenario", data={"kind": "test_merchant_open"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged


def _observed_action(engine, round_id: str, tool_name: str, request, action_id: str | None = None):
    """按人物真实观察顺序提交并结算一个动作。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    identity = action_id or f"action-{round_id}"
    engine.submit_action(DirectToolCall(ctx, identity, tool_name, request))
    return observation, engine.commit_decision_round(round_id)[0]


def _browse(engine) -> None:
    """实际花费一分钟浏览并取得冻结商品目录。"""
    _observed_action(
        engine, "browse", "start_targeted_activity",
        StartTargetedActivityRequest(activity_type="browse", target_id="product_catalog",
                                     planned_duration_seconds=60, playback_speed=1))
    advance_next(engine)


def _delivery_request(engine, product_id: str) -> PurchaseRequest:
    """按当前冻结报价形成送货到家的请求。"""
    product = next(item for item in engine.scenario.products if item.product_id == product_id)
    return PurchaseRequest(product_id=product_id, quantity=1, fulfillment=FulfillmentKind.DELIVERY,
                           destination_id="home", price_version=product.price_version)


def test_browse_purchase_replay_and_remote_delivery_use_one_canonical_payment(engine_factory) -> None:
    """购买只扣款一次，重放不重复付款，配送到家后办公室不能直接取得商品。"""
    engine = engine_factory()
    _stage_open(engine)
    before = engine.observe(context(engine, "before-browse"))
    assert before["products"] == []
    _browse(engine)
    product = engine.scenario.products[0]
    request = _delivery_request(engine, product.product_id)
    balances = dict(engine.state.accounts)
    stock = engine.state.merchant_stock[product.product_id]
    observation, result = _observed_action(engine, "buy", "purchase", request)
    assert len(observation["products"]) == len(engine.scenario.products)
    assert result.status == "completed"
    assert engine.state.accounts["agent:1"] == balances["agent:1"] - product.price_fen
    assert engine.state.accounts[product.merchant_account_id] == (
        balances[product.merchant_account_id] + product.price_fen)
    assert engine.state.merchant_stock[product.product_id] == stock - 1
    assert len(engine.state.deliveries) == 1
    money_events = [event for event in engine.store.read_agent_events("action_result", 1)
                    if event.action_id == "action-buy" and event.data.get("resource_changes")]
    assert len(money_events) == 1
    assert sum(money_events[0].data["resource_changes"].values()) == 0
    assert all("resource_changes" not in event.data
               for event in engine.store.read_agent_events("action_accepted", 1))

    advance_next(engine)
    snapshot = (dict(engine.state.accounts), dict(engine.state.merchant_stock), dict(engine.state.deliveries))
    _observed_action(engine, "buy-replay", "purchase", request, action_id="action-buy")
    assert (engine.state.accounts, engine.state.merchant_stock, engine.state.deliveries) == snapshot
    assert engine.state.agents[1].next_decision_at == engine.state.current_time + 1
    all_money = [event for event in engine.store.read_agent_events("action_result", 1)
                 if event.action_id == "action-buy" and event.data.get("resource_changes")]
    assert len(all_money) == 1

    advance_next(engine)
    route = next(item for item in engine.scenario.routes
                 if item.origin_id == "home" and item.destination_id == "office" and item.mode == "driving")
    travel = StartTravelRequest(destination_id="office", travel_mode="driving",
                                planned_duration_seconds=route.duration_seconds)
    travel_observation, _ = _observed_action(engine, "to-office", "start_travel", travel)
    assert len(travel_observation["pending_deliveries"]) == 1
    advance_next(engine)
    assert engine.state.agents[1].location_id == "office"
    after_fare = dict(engine.state.accounts)
    _observed_action(engine, "travel-replay", "start_travel", travel, action_id="action-to-office")
    assert engine.state.accounts == after_fare
    travel_money = [event for event in engine.store.read_agent_events("action_result", 1)
                    if event.action_id == "action-to-office" and event.data.get("resource_changes")]
    assert len(travel_money) == 1
    assert travel_money[0].data["resource_changes"] == {
        "account:agent:1": -route.fare_fen,
        f"account:{engine.scenario.transport_account_id}": route.fare_fen,
    }
    advance_next(engine)
    _observed_action(
        engine, "sleep-for-delivery", "start_basic_activity",
        StartBasicActivityRequest(activity_type="sleep", planned_duration_seconds=3600))
    advance_next(engine)
    assert engine.state.agents[1].current_activity_id is not None
    advance_next(engine)
    delivered = engine.observe(context(engine, "after-delivery"))
    assert delivered["pending_deliveries"] == []
    assert delivered["inventory"]["home"][product.product_id] == 1
    assert product.product_id not in delivered["available_inventory"]
    conflict = context(engine, "travel-conflict")
    engine.begin_decision_round([conflict])
    engine.observe(conflict)
    changed = travel.model_copy(update={"planned_duration_seconds": route.duration_seconds + 60})
    with pytest.raises(ProtocolError, match="reused with different parameters"):
        engine.submit_action(DirectToolCall(conflict, "action-to-office", "start_travel", changed))


@pytest.mark.parametrize(
    ("mutation", "code"),
    (
        ("price", "PRICE_VERSION_MISMATCH"),
        ("funds", "INSUFFICIENT_FUNDS"),
        ("stock", "OUT_OF_STOCK"),
        ("closed", "MERCHANT_CLOSED"),
        ("destination", "DESTINATION_NOT_FOUND"),
        ("pickup", "PICKUP_LOCATION_REQUIRED"),
    ),
)
def test_purchase_rejects_each_resource_boundary_without_mutating_input(engine_factory, mutation, code) -> None:
    """报价、资金、库存、营业、目的地与取货位置逐项失败且输入状态不变。"""
    engine = engine_factory()
    _stage_open(engine)
    product = engine.scenario.products[0]
    state = engine.state.model_copy(deep=True)
    request = _delivery_request(engine, product.product_id)
    if mutation == "price":
        request = request.model_copy(update={"price_version": "wrong"})
    elif mutation == "funds":
        accounts = dict(state.accounts)
        accounts[state.agents[1].balance_account_id] = 0
        state = state.model_copy(update={"accounts": accounts}, deep=True)
    elif mutation == "stock":
        stock = dict(state.merchant_stock)
        stock[product.product_id] = 0
        state = state.model_copy(update={"merchant_stock": stock}, deep=True)
    elif mutation == "closed":
        state = state.model_copy(update={"current_time": int(engine.scenario.start_at.timestamp())}, deep=True)
    elif mutation == "destination":
        request = request.model_copy(update={"destination_id": "missing"})
    elif mutation == "pickup":
        request = request.model_copy(update={"fulfillment": FulfillmentKind.PICKUP,
                                             "destination_id": product.merchant_location_id})
    frozen = state.model_dump(mode="json")
    with pytest.raises(BusinessRuleError, match=code):
        purchase(state, engine.scenario, 1, request)
    assert state.model_dump(mode="json") == frozen


def test_food_progress_persists_partial_quantity_across_pause_and_completion(engine_factory) -> None:
    """进食按实际进度扣份量和加热量，暂停重复结算不抹去或重复已吃部分。"""
    engine = engine_factory()
    product = engine.scenario.products[0]
    agent = engine.state.agents[1].model_copy(
        update={"inventory": {"home": {product.product_id: 2.0}}}, deep=True)
    request = ConsumeRequest(product_id=product.product_id, quantity=1, use_mode="eat",
                             duration_seconds=product.use_duration_seconds)
    activity = ActivityState(
        activity_id="food-progress", agent_id=1, kind="consume", status=ActivityStatus.ACTIVE,
        started_at=0, planned_end_at=900, last_settled_at=450, elapsed_seconds=450,
        planned_duration_seconds=900, target_id=product.product_id,
        origin_action_id="eat", origin_decision_id="eat-decision",
        origin_request_event_id="eat-request", data={"request": request.model_dump(mode="json")})
    first = apply_product_progress(agent, product, activity, engine.scenario.body)
    assert first.consumed_quantity == pytest.approx(0.5)
    assert first.agent.inventory["home"][product.product_id] == pytest.approx(1.5)
    assert first.settlement is not None
    paused = activity.model_copy(update={"status": ActivityStatus.PAUSED,
                                         "data": {"request": request.model_dump(mode="json"),
                                                  "consumed_quantity": first.consumed_quantity}}, deep=True)
    repeated = apply_product_progress(first.agent, product, paused, engine.scenario.body)
    assert repeated.agent == first.agent and repeated.consumed_quantity == pytest.approx(0.5)
    completed = paused.model_copy(update={"status": ActivityStatus.COMPLETED, "ended_at": 900,
                                          "last_settled_at": 900, "elapsed_seconds": 900}, deep=True)
    final = apply_product_progress(repeated.agent, product, completed, engine.scenario.body)
    assert final.consumed_quantity == pytest.approx(1)
    assert final.agent.inventory["home"][product.product_id] == pytest.approx(1)


def test_consume_rejects_inventory_held_at_another_location(engine_factory) -> None:
    """商品虽已送到家中，人物位于办公室时仍不能瞬间取用。"""
    engine = engine_factory()
    product = engine.scenario.products[0]
    staged = engine.state.model_copy(deep=True)
    agent = staged.agents[1].model_copy(
        update={"location_id": "office", "inventory": {"home": {product.product_id: 1.0}}}, deep=True)
    staged.agents[1] = agent
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                       visibility="system", data={"kind": "test_remote_inventory"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged
    request = ConsumeRequest(product_id=product.product_id, quantity=1, use_mode="eat",
                             duration_seconds=product.use_duration_seconds)
    _, result = _observed_action(engine, "remote-eat", "consume", request)
    assert result.status == "rejected" and result.error_code == "PRODUCT_NOT_HELD"
    assert engine.state.agents[1].inventory["home"][product.product_id] == 1


def test_delivery_replay_does_not_duplicate_inventory(engine_factory) -> None:
    """同一配送到期事件重复处理只返回已交付订单，不再次增加库存。"""
    engine = engine_factory()
    _stage_open(engine)
    product = engine.scenario.products[0]
    outcome = purchase(engine.state, engine.scenario, 1, _delivery_request(engine, product.product_id))
    first, order = deliver_order(outcome.state, outcome.delivery.order_id)
    second, replayed = deliver_order(first, outcome.delivery.order_id)
    assert order.status == replayed.status == "delivered"
    assert first.agents[1].inventory == second.agents[1].inventory
    assert second.agents[1].inventory["home"][product.product_id] == 1


def _multi_browse(engine) -> None:
    """让两个就绪人物在同轮真实取得商品目录。"""
    ready = engine.scheduling_state().ready_agent_ids
    contexts = {agent_id: context(engine, "multi-browse", agent_id) for agent_id in ready}
    engine.begin_decision_round([contexts[agent_id] for agent_id in ready])
    for ctx in contexts.values():
        engine.observe(ctx)
    request = StartTargetedActivityRequest(activity_type="browse", target_id="product_catalog",
                                           planned_duration_seconds=60, playback_speed=1)
    for agent_id in reversed(ready):
        engine.submit_action(DirectToolCall(contexts[agent_id], f"browse-{agent_id}",
                                            "start_targeted_activity", request))
    engine.commit_decision_round("multi-browse")
    advance_next(engine)


def _compete_for_last_item(engine, arrival_order: tuple[int, int]) -> tuple[int, int, dict[int, str]]:
    """通过真实多人轮结算最后一件库存并返回胜者和逐人物结果。"""
    product = engine.scenario.products[0]
    staged = engine.state.model_copy(deep=True)
    staged.merchant_stock[product.product_id] = 1
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                       visibility="system", data={"kind": "test_last_stock"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged
    ready = engine.scheduling_state().ready_agent_ids
    contexts = {agent_id: context(engine, "stock-race", agent_id) for agent_id in ready}
    engine.begin_decision_round([contexts[agent_id] for agent_id in ready])
    for ctx in contexts.values():
        engine.observe(ctx)
    request = _delivery_request(engine, product.product_id)
    for agent_id in arrival_order:
        engine.submit_action(DirectToolCall(contexts[agent_id], f"buy-{agent_id}", "purchase", request))
    results = engine.commit_decision_round("stock-race")
    statuses = {item.agent_id: item.status.value for item in results}
    winner = next(agent_id for agent_id, status in statuses.items() if status == "completed")
    return ready[0], winner, statuses


def test_multi_person_stock_competition_uses_seeded_order_not_arrival(engine_factory) -> None:
    """最后一件共享库存归预先公布顺序首人，颠倒请求到达不改变结果。"""
    first = engine_factory("multi-person.json")
    second = engine_factory("multi-person.json")
    for engine in (first, second):
        _stage_open(engine)
        _multi_browse(engine)
    expected_one, winner_one, statuses_one = _compete_for_last_item(first, (1, 2))
    expected_two, winner_two, statuses_two = _compete_for_last_item(second, (2, 1))
    assert expected_one == expected_two
    assert winner_one == winner_two == expected_one
    assert statuses_one == statuses_two
    assert sorted(statuses_one.values()) == ["completed", "rejected"]


def test_pending_delivery_is_private_to_its_owner(engine_factory) -> None:
    """共享世界中的待配送订单只出现在下单人物自己的观察。"""
    engine = engine_factory("multi-person.json")
    _stage_open(engine)
    _multi_browse(engine)
    ready = engine.scheduling_state().ready_agent_ids
    contexts = {agent_id: context(engine, "private-order", agent_id) for agent_id in ready}
    engine.begin_decision_round([contexts[agent_id] for agent_id in ready])
    observations = {agent_id: engine.observe(contexts[agent_id]) for agent_id in ready}
    product = engine.scenario.products[0]
    requests = {1: ("purchase", _delivery_request(engine, product.product_id)),
                2: ("start_basic_activity", StartBasicActivityRequest(
                    activity_type="wait", planned_duration_seconds=60))}
    for agent_id in reversed(ready):
        tool, request = requests[agent_id]
        engine.submit_action(DirectToolCall(contexts[agent_id], f"private-{agent_id}", tool, request))
    engine.commit_decision_round("private-order")
    assert observations[1]["pending_deliveries"] == observations[2]["pending_deliveries"] == []
    owner = engine.observe(context(engine, "owner-after", 1))
    other = engine.observe(context(engine, "other-after", 2))
    assert len(owner["pending_deliveries"]) == 1
    assert other["pending_deliveries"] == []
