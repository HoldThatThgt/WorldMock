"""@brief 购买受理只冻结订单，所有付款组在正常完成时原子结算。"""

from __future__ import annotations

from worldmock.errors import BusinessRuleError
from worldmock.life.models import DeliveryState, FulfillmentKind, ScheduledEvent, WorldState
from worldmock.life.order_routing import group_offers, group_shipping_fen
from worldmock.life.purchase_offers import quote_goods
from worldmock.life.randomness import random_stream
from worldmock.scheduling.events import ScheduledKind


def quoted_basket(inputs, state: WorldState, agent_id: int, goods_ids: tuple[str, ...]) -> tuple[dict, int]:
    """@brief 整次请求核对重复商品和合并后总价，不改变世界或资金。"""
    if not 1 <= len(goods_ids) <= 2:
        raise BusinessRuleError("ORDER_ITEM_COUNT_OUT_OF_RANGE")
    if len(set(goods_ids)) != len(goods_ids):
        raise BusinessRuleError("ORDER_DUPLICATE_ITEM")
    offers = tuple(quote_goods(inputs, state, agent_id, key) for key in goods_ids)
    groups = group_offers(offers)
    total = sum(inputs.catalog.goods[row.goods_id].price_fen for row in offers)
    total += sum(group_shipping_fen(rows) for rows in groups.values())
    if state.accounts[state.agents[agent_id].balance_account_id] < total:
        raise BusinessRuleError("INSUFFICIENT_FUNDS")
    return groups, total


def build_orders(context, state: WorldState, pending, groups: dict) -> tuple[DeliveryState, ...]:
    """@brief 每组一次选择支付 app 与操作时长，商品行保存真实冻结履约。"""
    orders = []
    candidates = [*sorted(context.catalog.apps_by_action.get("purchase", ())), None]
    for key, offers in groups.items():
        app = random_stream(state.random_state, f"function-app:{pending.action_id}:{key}").choice(candidates)
        seconds = (0 if app is None else
                   random_stream(state.random_state, f"payment-duration:{pending.action_id}:{key}").randint(10, 20))
        fee = group_shipping_fen(offers)
        for index, offer in enumerate(offers):
            order_id = f"order-{pending.action_id}-{len(orders) + 1}"
            orders.append(_order(context, offer, (pending.agent_id, order_id, state.current_time),
                                 (key, app, seconds, fee if index == 0 else 0)))
    return tuple(orders)


def _order(context, offer, identity: tuple, payment: tuple) -> DeliveryState:
    """@brief 一件商品保存付款组、实际渠道与未付款状态。"""
    agent_id, order_id, created_at = identity
    key, app, seconds, fee = payment
    goods = context.catalog.goods[offer.goods_id]
    return DeliveryState(order_id=order_id, agent_id=agent_id, goods_id=goods.goods_id,
        category=goods.category, store_id=offer.store_id, app_id=offer.app_id, payment_app_id=app,
        group_key=key, payment_duration_seconds=seconds, quantity=1, unit_price_fen=goods.price_fen,
        delivery_fee_fen=fee, total_price_fen=goods.price_fen + fee, destination_id=offer.destination_id,
        delivery_address_id=offer.delivery_address_id, fulfillment=offer.fulfillment, created_at=created_at,
        expected_at=offer.expected_at, status="pending", path=offer.path, trip_id=offer.trip_id,
        distance_meters=offer.distance_meters, prep_seconds=offer.prep_seconds)


def activity_orders(state: WorldState, activity) -> tuple[DeliveryState, ...]:
    """@brief 订单身份只保存在购买活动引用中，支付状态只读取订单。"""
    return tuple(state.deliveries[key] for key in activity.data["order_ids"])


def order_groups(orders: tuple[DeliveryState, ...]) -> dict[str, tuple[DeliveryState, ...]]:
    """@brief 按受理冻结的规范合单键稳定返回全部商品履约记录。"""
    groups = {}
    for order in sorted(orders, key=lambda item: item.order_id):
        groups.setdefault(order.group_key, []).append(order)
    return {key: tuple(groups[key]) for key in sorted(groups)}


def delivery_events(state: WorldState, orders: tuple[DeliveryState, ...],
                    payments: dict[str, str]) -> tuple[ScheduledEvent, ...]:
    """@brief 已付款的外卖和快递按一趟配送各排一条到达，起因是付清这一趟的付款事件。

    一趟配送是同一个配送组里预计到达时刻相同的记录；各趟按预计到达时刻、配送组键升序取排程序号，
    到期时刻不早于付款时刻。payments 给出每条已付款记录所属付款事件的身份。
    """
    trips = {}
    for order in orders:
        if order.paid_at is not None and order.fulfillment != FulfillmentKind.PICKUP:
            trips.setdefault((order.expected_at, order.group_key), []).append(order)
    first = max((item.sequence for item in state.pending_events), default=0) + 1
    result = []
    for index, key in enumerate(sorted(trips)):
        selected = sorted(trips[key], key=lambda order: order.order_id)
        head = selected[0]
        result.append(ScheduledEvent(scheduled_id=f"delivery-{head.order_id}",
            kind=ScheduledKind.DELIVERY_ARRIVAL, due_at=max(head.expected_at, head.paid_at),
            sequence=first + index, agent_id=head.agent_id,
            data={"order_ids": [order.order_id for order in selected]}, caused_by=[payments[head.order_id]]))
    return tuple(result)
