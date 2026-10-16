"""@brief 单次购买的受理冻结与完成时逐张订单原子付款的接线；时长与分段的规则在 purchase_phases。"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.basket import activity_orders, build_orders, delivery_events, quoted_basket
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, FulfillmentKind, WorldState
from worldmock.life.order_notices import goods_names, payment_notice, placed_notice
from worldmock.life.order_routing import payment_orders
from worldmock.life.parcel import collect_parcels
from worldmock.life.purchase_phases import advance_phases, order_payments, purchase_seconds
from worldmock.life.randomness import random_stream
from worldmock.life.receipt import settle_receipt
from worldmock.life.settlement import settle_money_transfer


class StorefrontRuntime:
    """@brief 支付状态属于订单，购买活动只关联订单身份，不保存第二份付款状态。"""

    def __init__(self, context):
        """@brief 绑定唯一环境与足迹存储。"""
        self.context = context

    def prepare_purchase(self, state: WorldState, pending, goods_ids: tuple[str, ...], prefer: str | None) -> tuple:
        """@brief 受理只创建一个购买活动及未付款的商品履约记录，不产生账户变化。

        购买时长只抽一次，实际时长把各张订单的付款包在里面；到店记录预计在购买结束时到手。
        第一个付款段恰好从购买开始那一秒起时，它的开始时刻在这同一次提交里写到订单上。
        意向只在这里用一次：路线冻结在商品履约记录上，之后付款、通知、送达与恢复都只读记录。

        :param prefer: 这条购买调用已认出的意向 app 身份；没有意向时为 None。
        """
        groups, _ = quoted_basket(self.context.inputs, state, pending.agent_id, tuple(goods_ids), prefer)
        orders = build_orders(self.context, state, pending, groups)
        drawn = random_stream(state.random_state, f"purchase-duration:{pending.action_id}").randint(60, 300)
        duration = purchase_seconds(drawn, order_payments(orders))
        orders = tuple(order.model_copy(update={"expected_at": state.current_time + duration}, deep=True)
                       if order.fulfillment == FulfillmentKind.PICKUP else order for order in orders)
        activity = _purchase_activity(state, pending, orders, duration, self.context.catalog)
        changed = state.model_copy(update={"deliveries": {**state.deliveries,
            **{order.order_id: order for order in orders}},
            "activities": {**state.activities, activity.activity_id: activity}}, deep=True)
        return advance_phases(changed, activity, state.current_time), activity

    def complete_purchase(self, state: WorldState, activity: ActivityState) -> tuple:
        """@brief 先核对整次总价，再在同次提交中按订单键升序逐张结算，并把各张订单付款事件的身份交给发货与到达排程。"""
        orders = activity_orders(state, activity)
        if all(order.paid_at is not None for order in orders) or all(order.status == "cancelled" for order in orders):
            return state, []
        if any(order.paid_at is not None for order in orders):
            logging.error("Purchase contains partially paid orders")
            raise IntegrityError("Purchase contains partially paid orders")
        if any(order.payment_duration_seconds and order.payment_ended_at is None for order in orders):
            logging.error("Purchase completion requires actual payment operation evidence")
            raise IntegrityError("Purchase completion requires actual payment operation evidence")
        total = sum(order.total_price_fen for order in orders)
        owner = state.agents[activity.agent_id].balance_account_id
        if state.accounts[owner] < total:
            logging.error("Purchase completion failed: insufficient funds")
            changed = self.cancel_purchase(state, activity)
            failed = activity.model_copy(update={"status": ActivityStatus.FAILED,
                "ended_at": state.current_time, "boundary_type": "payment_failed",
                "interruption_reason": "INSUFFICIENT_FUNDS"}, deep=True)
            return changed.model_copy(update={"activities": {**changed.activities,
                                       activity.activity_id: failed}}, deep=True), []
        cause = self._accepted_cause(activity)
        changed, events = state, []
        for rows in payment_orders(orders).values():
            changed, order_events = self._pay_order(changed, activity, rows, cause)
            events.extend(order_events)
        payments = {identity: event.event_id for event in events if event.data.get("kind") == "purchase_payment"
                    for identity in event.data["order_ids"]}
        scheduled = delivery_events(changed, activity_orders(changed, activity), payments)
        pending = sorted([*changed.pending_events, *scheduled],
                         key=lambda row: (row.due_at, row.sequence, row.scheduled_id))
        return changed.model_copy(update={"pending_events": pending}, deep=True), events

    def cancel_purchase(self, state: WorldState, activity: ActivityState) -> WorldState:
        """@brief 尚未付款的全部记录一起取消，不收费、不收货；已经写下的付款段开始时刻保留。"""
        orders = tuple(order for order in activity_orders(state, activity) if order.paid_at is None)
        return _update_orders(state, orders, {"status": "cancelled"})

    def collect_parcels(self, state: WorldState, agent_id: int, station_id: str) -> tuple:
        """@brief 本人在站交互一次领取全部包裹，因果由同次真实交互结果绑定。"""
        outcome = collect_parcels(state, self.context, agent_id, station_id)
        return outcome.state, list(outcome.receipts)

    def _accepted_cause(self, activity: ActivityState) -> str:
        """@brief 支付证据只关联已提交的真实购买受理结果；受理与活动开始同一秒，只读活动开始那一秒及以后的结果。"""
        store = self.context.store
        matches = [event for event in store.read_agent_events(
                       "action_result", activity.agent_id, store.last_event_seq_before(activity.started_at))
                   if event.action_id == activity.origin_action_id
                   and event.data.get("status") in {"accepted", "in_progress", "completed"}]
        if len(matches) != 1:
            logging.error("Purchase requires its unique committed acceptance result")
            raise IntegrityError("Purchase requires its unique committed acceptance result")
        return matches[0].event_id

    def _pay_order(self, state: WorldState, activity: ActivityState, orders: tuple, cause: str) -> tuple:
        """@brief 一张订单的付款事实承载唯一账户变化：金额是这张订单的货款加它名下各配送组的运费。

        带下单 app 的订单随后写「订单已下单」，支付 app 与下单 app 不全为空的订单再写「支付成功」；到店订单的记录
        逐条当秒收货。通知和收货都引用这条付款事实。
        """
        amount = sum(order.total_price_fen for order in orders)
        owner = state.agents[activity.agent_id].balance_account_id
        order_ids = sorted(order.order_id for order in orders)
        changed = settle_money_transfer(state, owner, "external:scenario", amount,
                                       f"purchase:{','.join(order_ids)}")
        changed = _update_orders(changed, orders, {"paid_at": state.current_time, "status": "pending"})
        paid = EventDraft(event_type="environment_event", occurred_at=state.current_time,
            agent_id=activity.agent_id, activity_id=activity.activity_id, caused_by=[cause], visibility="system",
            data={"kind": "purchase_payment", "activity_id": activity.activity_id,
                  "order_ids": order_ids, "amount_fen": amount},
            resource_changes={f"account:{owner}": -amount, "account:external:scenario": amount})
        events = [paid]
        if orders[0].app_id is not None:
            events.append(placed_notice(orders, paid, self.context.catalog))
        if orders[0].app_id is not None or orders[0].payment_app_id is not None:
            events.append(payment_notice(orders, paid, self.context.catalog))
        if orders[0].fulfillment == FulfillmentKind.PICKUP:
            for order in orders:
                outcome = settle_receipt(changed, self.context, order.order_id)
                changed = outcome.state
                for receipt in outcome.receipts:
                    if receipt.data.get("kind") == "goods_received":
                        receipt.caused_by = [paid.event_id]
                events.extend(outcome.receipts)
        return changed, events


def _purchase_activity(state: WorldState, pending, orders: tuple, duration: int, catalog) -> ActivityState:
    """@brief 同请求全部商品组成一个占槽位 2 的活动，计划时长是含付款的实际购买时长。"""
    goods = goods_names(orders, catalog)
    moment = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone))
    data = {"action": "purchase", "order_ids": [order.order_id for order in orders], "exertion": "idle",
            "relaxation_relief": "none", "social_relief": "none",
            "relaxation_relief_per_hour": 0.0, "social_relief_per_hour": 0.0}
    label = f"购买{'、'.join(goods)}（{moment.month}月{moment.day}日 {moment:%H:%M} 起）"
    return ActivityState(activity_id=f"activity-{pending.action_id}-purchase", agent_id=pending.agent_id,
        location_id=state.agents[pending.agent_id].location_id, kind=ActivityKind.PURCHASE,
        status=ActivityStatus.ACTIVE, started_at=state.current_time, planned_end_at=state.current_time + duration,
        last_settled_at=state.current_time, elapsed_seconds=0, planned_duration_seconds=duration,
        resource_claims={"second"}, origin_action_id=pending.action_id, origin_decision_id=pending.decision_id,
        origin_request_event_id=pending.requested_event_id, report_anchor_at=state.current_time, name=label, data=data)


def _update_orders(state: WorldState, orders: tuple, values: dict) -> WorldState:
    """@brief 同一纯状态替换保存全部相关商品记录，不修改输入对象。"""
    changed = {order.order_id: order.model_copy(update=values, deep=True) for order in orders}
    return state.model_copy(update={"deliveries": {**state.deliveries, **changed}}, deep=True)

