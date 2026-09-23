"""目录浏览、购买、配送、库存位置与按进度消费测试。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, advance_until_ready, context, stage_day_plan
from worldmock.footprints.models import EventDraft
from worldmock.life.commerce import deliver_order
from worldmock.life.models import (
    ActivityState,
    ActivityStatus,
    ConsumeItem,
    ConsumeRequest,
    FulfillmentKind,
    PurchaseItem,
    PurchaseOrderRequest,
    StartActivityRequest,
)


def _stage_open(engine) -> None:
    """把隔离夹具移到商家营业时段并保留之后的真实后台事件。"""
    zone = ZoneInfo(engine.scenario.timezone)
    epoch = int(datetime(2026, 1, 5, 10, 0, tzinfo=zone).timestamp())
    staged = engine.state.model_copy(deep=True)
    staged.current_time = epoch
    staged.pending_events = [event for event in staged.pending_events if event.due_at > epoch]
    for agent_id, agent in staged.agents.items():
        staged.agents[agent_id] = agent.model_copy(
            update={"next_decision_at": epoch, "body_settled_at": epoch}, deep=True)
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
    if callable(request):
        request = request(observation)
    engine.submit_action(DirectToolCall(ctx, identity, tool_name, request))
    return observation, engine.commit_decision_round(round_id)[0]


def _choice(observation: dict, action: str, object_id: str | None = None,
            mode: str | None = None) -> StartActivityRequest:
    """@brief 只从本轮冻结观察取可读对象与动作短语。"""
    return next(StartActivityRequest(targets=[row["object"]], actions=[offered["label"]])
                for row in observation["interaction_bindings"]
                if object_id is None or row["object_id"] == object_id
                for offered in row["actions"]
                if offered["canonical_action"] == action
                and (mode is None or (offered.get("travel_mode") or offered.get("fulfillment")) == mode))


def _delivery_supply_ids(observation: dict) -> set[str]:
    """从当前商品窗口获取可配送的供给。"""
    return {row["object_id"] for row in observation["interaction_bindings"]
            if row["kind"] == "supply"
            and any(action.get("fulfillment") == "delivery" for action in row["actions"])}


def _browse(engine) -> None:
    """通过可读商品目录浏览活动取得具体门店供给。"""
    _, result = _observed_action(engine, "browse", "start_activity",
                                 lambda observed: _choice(observed, "browse"))
    assert result.status == "in_progress", result.error_code
    advance_next(engine)


def _selected_supply(engine, product_id: str):
    """明确选择测试使用的集市供给；商品本身不携带商家或报价。"""
    return next(item for item in engine.scenario.supplies
                if item.product_id == product_id and item.location_id == "market")


def _delivery_request(engine, product_id: str) -> PurchaseOrderRequest:
    """按所选供给的当前世界报价形成送货到家的整单请求。"""
    supply = _selected_supply(engine, product_id)
    quote = engine.state.supply_quotes[supply.supply_id]
    return PurchaseOrderRequest(
        items=(PurchaseItem(supply_id=supply.supply_id, quantity=1, price_version=quote.price_version),),
        fulfillment=FulfillmentKind.DELIVERY, destination_id="home")


def test_browse_purchase_replay_uses_one_canonical_payment(engine_factory) -> None:
    """可读目录浏览后购买只扣款一次，重放不重复付款。"""
    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    before = engine.observe(context(engine, "before-browse"))
    assert before["supplies"] == []
    _browse(engine)
    product = engine.scenario.products[0]
    supply = _selected_supply(engine, product.product_id)
    request = _choice(engine.observe(context(engine, "purchase-preview")),
                      "purchase", supply.supply_id, "delivery")
    balances = dict(engine.state.accounts)
    price = engine.state.supply_quotes[supply.supply_id].price_fen
    stock = engine.state.supply_stock[supply.supply_id]
    observation, result = _observed_action(engine, "buy", "start_activity", request)
    assert supply.supply_id in {row["supply_id"] for row in observation["supplies"]}
    assert {row["supply_id"] for row in observation["supplies"]} <= engine.state.agents[1].visible_supply_ids
    assert result.status == "completed"
    assert engine.state.accounts["agent:1"] == balances["agent:1"] - price
    assert engine.state.accounts[supply.seller_account_id] == (
        balances[supply.seller_account_id] + price)
    assert engine.state.supply_stock[supply.supply_id] == stock - 1
    assert len(engine.state.deliveries) == 1
    money_events = [event for event in engine.store.read_agent_events("action_result", 1)
                    if event.action_id == "action-buy" and event.data.get("resource_changes")]
    assert len(money_events) == 1
    assert sum(money_events[0].data["resource_changes"].values()) == 0
    assert all("resource_changes" not in event.data
               for event in engine.store.read_agent_events("action_accepted", 1))

    advance_next(engine)
    snapshot = (dict(engine.state.accounts), dict(engine.state.supply_stock), dict(engine.state.deliveries))
    _observed_action(engine, "buy-replay", "start_activity", request, action_id="action-buy")
    assert (engine.state.accounts, engine.state.supply_stock, engine.state.deliveries) == snapshot
    assert engine.state.agents[1].next_decision_at == engine.state.current_time + 1
    all_money = [event for event in engine.store.read_agent_events("action_result", 1)
                 if event.action_id == "action-buy" and event.data.get("resource_changes")]
    assert len(all_money) == 1



def test_food_progress_persists_partial_quantity_across_pause_and_completion(engine_factory) -> None:
    """进食按实际进度扣份量和加热量，暂停重复结算不抹去或重复已吃部分。"""
    engine = engine_factory()
    product = engine.scenario.products[0]
    agent = engine.state.agents[1].model_copy(
        update={"inventory": {"home": {product.product_id: 2.0}}}, deep=True)
    request = ConsumeRequest(items=(ConsumeItem(product_id=product.product_id, quantity=1),), use_mode="eat",
                             duration_seconds=product.use_duration_seconds)
    activity = ActivityState(
        activity_id="food-progress", agent_id=1, kind="consume", status=ActivityStatus.ACTIVE,
        started_at=0, planned_end_at=900, last_settled_at=450, elapsed_seconds=450,
        planned_duration_seconds=900, target_id=product.product_id,
        origin_action_id="eat", origin_decision_id="eat-decision",
        origin_request_event_id="eat-request", data={"request": request.model_dump(mode="json")})
    from worldmock.life.basket import apply_meal_progress

    first = apply_meal_progress(agent, engine.rules.products, activity, engine.scenario.body)
    assert activity.data["consumed"][product.product_id] == pytest.approx(0.5)
    assert first.inventory["home"][product.product_id] == pytest.approx(1.5)
    paused = activity.model_copy(update={"status": ActivityStatus.PAUSED}, deep=True)
    repeated = apply_meal_progress(first, engine.rules.products, paused, engine.scenario.body)
    assert repeated.inventory["home"][product.product_id] == first.inventory["home"][product.product_id]
    assert paused.data["consumed"][product.product_id] == pytest.approx(0.5)
    completed = paused.model_copy(update={"status": ActivityStatus.COMPLETED, "ended_at": 900,
                                          "last_settled_at": 900, "elapsed_seconds": 900}, deep=True)
    final = apply_meal_progress(repeated, engine.rules.products, completed, engine.scenario.body)
    assert completed.data["consumed"][product.product_id] == pytest.approx(1)
    assert final.inventory["home"][product.product_id] == pytest.approx(1)


def test_remote_inventory_is_not_a_current_interactive_object(engine_factory) -> None:
    """人在办公室时，家里的商品不进入本轮可操作对象。"""
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
    stage_day_plan(engine)
    observed = engine.observe(context(engine, "remote-preview"))
    assert not any(row["kind"] == "holding" and row["object_id"] == product.product_id
                   for row in observed["interaction_bindings"])
    request = StartActivityRequest(targets=["家里的食物"], actions=["食用"])
    _, result = _observed_action(engine, "remote-eat", "start_activity", request)
    assert result.status == "rejected" and result.error_code == "ACTION_REQUEST_INVALID"
    assert engine.state.agents[1].inventory["home"][product.product_id] == 1


def test_delivery_replay_does_not_duplicate_inventory(engine_factory) -> None:
    """同一配送到期事件重复处理只返回已交付订单，不再次增加库存。"""
    engine = engine_factory()
    _stage_open(engine)
    product = engine.scenario.products[0]
    from worldmock.life.basket import purchase_order

    outcome = purchase_order(engine.state, engine.semantics, 1,
                             _delivery_request(engine, product.product_id))
    order_id = outcome.orders[0].order_id
    outcome.state.current_time = outcome.orders[0].expected_at
    first, order = deliver_order(outcome.state, order_id)
    second, replayed = deliver_order(first, order_id)
    assert order.status == replayed.status == "delivered"
    assert first.agents[1].inventory == second.agents[1].inventory
    assert second.agents[1].inventory["home"][product.product_id] == 1


def _multi_browse(engine) -> None:
    """让同轮人物经可读商品目录真实发现供给。"""
    for agent_id in engine.scheduling_state().ready_agent_ids:
        stage_day_plan(engine, agent_id)
    ready = engine.scheduling_state().ready_agent_ids
    contexts = {agent_id: context(engine, "multi-browse", agent_id) for agent_id in ready}
    engine.begin_decision_round([contexts[agent_id] for agent_id in ready])
    observations = {agent_id: engine.observe(ctx) for agent_id, ctx in contexts.items()}
    for agent_id in reversed(ready):
        request = _choice(observations[agent_id], "browse")
        engine.submit_action(DirectToolCall(contexts[agent_id], f"browse-{agent_id}",
                                            "start_activity", request))
    results = engine.commit_decision_round("multi-browse")
    assert all(result.status == "in_progress" for result in results)
    advance_next(engine)


def _compete_for_last_item(engine, arrival_order: tuple[int, int]) -> tuple[int, int, dict[int, str]]:
    """通过真实多人轮结算最后一件库存并返回胜者和逐人物结果。"""
    previews = {agent_id: engine.observe(context(engine, "stock-preview", agent_id))
                for agent_id in engine.scheduling_state().ready_agent_ids}
    common = _delivery_supply_ids(previews[1]) & _delivery_supply_ids(previews[2])
    assert common, "both agents must see a shared deliverable supply"
    supply_id = sorted(common)[0]
    staged = engine.state.model_copy(deep=True)
    staged.supply_stock[supply_id] = 1
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                       visibility="system", data={"kind": "test_last_stock"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged
    ready = engine.scheduling_state().ready_agent_ids
    contexts = {agent_id: context(engine, "stock-race", agent_id) for agent_id in ready}
    engine.begin_decision_round([contexts[agent_id] for agent_id in ready])
    observations = {agent_id: engine.observe(ctx) for agent_id, ctx in contexts.items()}
    for agent_id in arrival_order:
        request = _choice(observations[agent_id], "purchase", supply_id, "delivery")
        engine.submit_action(DirectToolCall(contexts[agent_id], f"buy-{agent_id}",
                                            "start_activity", request))
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
    supply_id = sorted(_delivery_supply_ids(observations[1]))[0]
    requests = {1: ("start_activity", _choice(
                    observations[1], "purchase", supply_id, "delivery")),
                2: ("start_activity", StartActivityRequest(targets=["自己"], actions=["等待"]))}
    for agent_id in reversed(ready):
        tool, request = requests[agent_id]
        engine.submit_action(DirectToolCall(contexts[agent_id], f"private-{agent_id}", tool, request))
    engine.commit_decision_round("private-order")
    assert observations[1]["pending_deliveries"] == observations[2]["pending_deliveries"] == []
    owner = engine.observe(context(engine, "owner-after", 1))
    other = engine.observe(context(engine, "other-after", 2))
    assert len(owner["pending_deliveries"]) == 1
    assert other["pending_deliveries"] == []
