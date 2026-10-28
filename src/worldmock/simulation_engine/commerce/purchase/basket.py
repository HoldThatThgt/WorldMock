"""@brief 购买受理只冻结商品履约记录与各张订单的付款抽取，所有订单在正常完成时原子结算。"""

from __future__ import annotations

from worldmock.state.world.orders import DeliveryState
from worldmock.state.world.kinds import FulfillmentKind
from worldmock.state.world.world import ScheduledEvent, WorldState
from worldmock.simulation_engine.commerce.fulfillment.order_notices import shipping_schedule
from worldmock.simulation_engine.commerce.purchase.order_routing import group_offers, group_shipping_fen, payment_orders
from worldmock.state.world.order_keys import payment_order_key
from worldmock.simulation_engine.commerce.purchase.purchase_offers import quote_goods
from worldmock.foundation.randomness import random_stream
from worldmock.state.planning.events import ScheduledKind


def quoted_basket(inputs, state: WorldState, agent_id: int, goods_ids: tuple[str, ...],
                  prefer: str | None) -> tuple[dict, int]:
    """@brief 逐份报价并按配送组合并运费，返回配送组与整次总价；同一商品写几次就是几份，件数不设上限。

    每一份带着同一个意向各自定路线：能走意向 app 的走它，不能的另选。不改变世界或资金；余额由整组核验与
    购买结束时的付款各核对一次。

    :param prefer: 这条购买调用已认出的意向 app 身份；没有意向时为 None。
    """
    offers = tuple(quote_goods(inputs, state, agent_id, key, prefer) for key in goods_ids)
    groups = group_offers(offers)
    total = sum(inputs.catalog.goods[row.goods_id].price_fen for row in offers)
    total += sum(group_shipping_fen(rows) for rows in groups.values())
    return groups, total


def order_payment(context, state: WorldState, action_id: str, app_id: str | None) -> tuple[str | None, int]:
    """@brief 一张订单只抽一次付款方式和付款时长，订单里有几件商品都一样。

    付款方式的候选是绑定了 purchase 的功能 app 按身份升序、末尾加一个 null：抽到 null 是直付。
    付款占 10 至 20 秒，在下单的内容 app 里直付也占这段时间；只有到店订单抽到直付时为 0，不抽时长。

    :param app_id: 这张订单下单的内容 app；到店订单为空。
    :return: （支付 app 或空, 付款时长秒数）。
    """
    key = payment_order_key(app_id)
    candidates = [*sorted(context.catalog.apps_by_action.get("purchase", ())), None]
    app = random_stream(state.random_state, f"function-app:{action_id}:{key}").choice(candidates)
    if app is None and app_id is None:
        return None, 0
    return app, random_stream(state.random_state, f"payment-duration:{action_id}:{key}").randint(10, 20)


def build_orders(context, state: WorldState, pending, groups: dict) -> tuple[DeliveryState, ...]:
    """@brief 按订单键升序每张订单抽一次付款，再按配送组键升序、组内按商品身份升序生成商品履约记录。

    每个配送组的第一条记录承担整组运费；同一张订单的记录带着相同的支付 app 与付款时长。
    """
    offers = tuple(offer for rows in groups.values() for offer in rows)
    payments = {key: order_payment(context, state, pending.action_id, rows[0].app_id)
                for key, rows in payment_orders(offers).items()}
    orders = []
    for key, rows in groups.items():
        app, seconds = payments[payment_order_key(rows[0].app_id)]
        fee = group_shipping_fen(rows)
        for index, offer in enumerate(rows):
            order_id = f"order-{pending.action_id}-{len(orders) + 1:04d}"
            orders.append(_order(context, offer, (pending.agent_id, order_id, state.current_time),
                                 (key, app, seconds, fee if index == 0 else 0)))
    return tuple(orders)


def _order(context, offer, identity: tuple, payment: tuple) -> DeliveryState:
    """@brief 一件商品保存配送组、所属订单的付款抽取、实际渠道与未付款状态。"""
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


def delivery_events(state: WorldState, orders: tuple[DeliveryState, ...],
                    payments: dict[str, str]) -> tuple[ScheduledEvent, ...]:
    """@brief 已付款的外卖和快递按一趟配送各排一条发货通知和一条到达，起因都是这一趟所属订单的付款事件。

    一趟配送是同一个配送组里预计到达时刻相同的记录；各趟按预计到达时刻、配送组键升序排列，
    到达的到期时刻取预计到达与付款时刻中较晚的一个。排程序号从现有最大序号加一开始，先按趟的顺序分给各趟的
    发货通知排程，再按同样的顺序分给各趟的到达排程，所以同一秒到期时发货通知先于到达处理。
    payments 给出每条已付款记录所属订单的付款事件身份。
    """
    grouped = {}
    for order in orders:
        if order.paid_at is not None and order.fulfillment != FulfillmentKind.PICKUP:
            grouped.setdefault((order.expected_at, order.group_key), []).append(order)
    trips = [sorted(grouped[key], key=lambda order: order.order_id) for key in sorted(grouped)]
    first = max((item.sequence for item in state.pending_events), default=0) + 1
    shipped = [shipping_schedule(trip, first + index, payments[trip[0].order_id])
               for index, trip in enumerate(trips)]
    arrivals = [ScheduledEvent(scheduled_id=f"delivery-{trip[0].order_id}",
        kind=ScheduledKind.DELIVERY_ARRIVAL, due_at=max(trip[0].expected_at, trip[0].paid_at),
        sequence=first + len(trips) + index, agent_id=trip[0].agent_id,
        data={"order_ids": [order.order_id for order in trip]}, caused_by=[payments[trip[0].order_id]])
        for index, trip in enumerate(trips)]
    return (*shipped, *arrivals)
