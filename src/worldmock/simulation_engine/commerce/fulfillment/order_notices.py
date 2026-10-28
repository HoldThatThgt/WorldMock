"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
from worldmock.recording_engine.projections.presentation.display_wording import goods_items
from worldmock.recording_engine.events.models import EventDraft
from worldmock.state.world.orders import DeliveryState
from worldmock.state.world.world import WorldState
from worldmock.state.planning.events import ScheduledEvent, ScheduledKind

from worldmock.state.world.order_notices import ORDER_DELIVERED, ORDER_PLACED, ORDER_SHIPPED, PAYMENT_SUCCEEDED
from worldmock.exchange_engine.commerce.shipping import shipping_delay_seconds


def goods_names(orders, catalog) -> list[str]:
    """@brief 商品名列表的各项：规则在显示写法的 goods_items，名称取引擎内存里的目录。"""
    return goods_items((order.goods_id for order in orders), lambda key: catalog.goods[key].name)



def goods_text(orders, catalog) -> str:
    """@brief 通知正文里的商品名列表：各项用「、」连接。"""
    return "、".join(goods_names(orders, catalog))



def placed_notice(orders: tuple[DeliveryState, ...], paid: EventDraft, catalog) -> EventDraft:
    """@brief 一张带下单 app 的订单付款那一秒的「订单已下单」，由下单的内容 app 发，起因是这张订单的付款事件。"""
    summary = f"已下单：{goods_text(orders, catalog)}"
    return _order_notice(orders, (ORDER_PLACED, summary), paid.occurred_at, paid.event_id)



def payment_notice(orders: tuple[DeliveryState, ...], paid: EventDraft, catalog) -> EventDraft:
    """@brief 一张订单付款那一秒的「支付成功」：由抽到的支付 app 发，直付时由下单的内容 app 发；通知自身不再次转账。"""
    head, amount = orders[0], paid.data["amount_fen"]
    sender = head.payment_app_id if head.payment_app_id is not None else head.app_id
    return EventDraft(event_type="environment_event", occurred_at=paid.occurred_at, agent_id=head.agent_id,
                      caused_by=[paid.event_id], visibility="system", source_kind="environment",
                      data={"kind": "payment_notification", "app_id": sender, "amount_fen": amount,
                            "order_ids": paid.data["order_ids"], "title": PAYMENT_SUCCEEDED,
                            "summary": f"已消费 {amount / 100:.2f} 元，购买{goods_text(orders, catalog)}"})



def shipping_schedule(trip: list[DeliveryState], sequence: int, payment_event_id: str) -> ScheduledEvent:
    """@brief 一趟配送的发货通知排程：到期时刻在付款到到达之间的十分之一处，身份与到达排程取同一条记录。

    :param trip: 这一趟已付款的记录，按记录身份升序。
    :param payment_event_id: 所属订单的付款事件身份。
    """
    head = trip[0]
    due_at = max(head.expected_at, head.paid_at)
    return ScheduledEvent(scheduled_id=f"shipped-{head.order_id}", kind=ScheduledKind.ORDER_SHIPPED,
                          due_at=head.paid_at + shipping_delay_seconds(due_at - head.paid_at), sequence=sequence,
                          agent_id=head.agent_id, data={"order_ids": [order.order_id for order in trip]},
                          caused_by=[payment_event_id])



def shipped_notice(state: WorldState, event: ScheduledEvent, catalog) -> EventDraft:
    """@brief 发货通知排程到期时只写的那一条「订单已发货」，由这趟配送的下单 app 发，起因是所属订单的付款事件。"""
    orders = tuple(state.deliveries[key] for key in event.data["order_ids"])
    summary = f"{goods_text(orders, catalog)}已发货"
    return _order_notice(orders, (ORDER_SHIPPED, summary), state.current_time, event.caused_by[0])



def delivered_notices(state: WorldState, arrival: EventDraft, catalog) -> list[EventDraft]:
    """@brief 一趟外卖真实送达后的「订单已送达」，起因是这一趟的到达事件；快递到站只是待取，没有这条通知。

    :param state: 到达已经结算的世界。
    :param arrival: 这一趟人物可见的到达事件，它的 orders 是这一趟的记录。
    """
    orders = tuple(state.deliveries[row["order_id"]] for row in arrival.data["orders"])
    delivered = tuple(order for order in orders if order.status == "delivered")
    if not delivered:
        return []
    summary = f"{goods_text(delivered, catalog)}已送达"
    return [_order_notice(delivered, (ORDER_DELIVERED, summary), arrival.occurred_at, arrival.event_id)]



def _order_notice(orders: tuple[DeliveryState, ...], text: tuple[str, str], occurred_at: int,
                  cause: str) -> EventDraft:
    """@brief 一条订单通知：发通知的是这些记录的下单 app，记录身份按字符串升序。"""
    title, summary = text
    return EventDraft(event_type="environment_event", occurred_at=occurred_at, agent_id=orders[0].agent_id,
                      caused_by=[cause], visibility="system", source_kind="environment",
                      data={"kind": "order_notification", "app_id": orders[0].app_id,
                            "order_ids": sorted(order.order_id for order in orders),
                            "title": title, "summary": summary})
