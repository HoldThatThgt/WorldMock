"""@brief 单次购买的冻结、真实支付操作与全部组原子付款接线。"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.basket import activity_orders, build_orders, delivery_events, order_groups, quoted_basket
from worldmock.life.models import ActivityKind, ActivityState, ActivityStatus, FulfillmentKind, WorldState
from worldmock.life.parcel import collect_parcels
from worldmock.life.randomness import random_stream
from worldmock.life.receipt import settle_receipt
from worldmock.life.settlement import settle_money_transfer


class StorefrontRuntime:
    """@brief 支付状态属于订单，购买活动只关联订单身份，不保存第二份付款状态。"""

    def __init__(self, context):
        """@brief 绑定唯一环境与足迹存储。"""
        self.context = context

    def prepare_purchase(self, state: WorldState, pending, goods_ids: tuple[str, ...]) -> tuple:
        """@brief 受理只创建一个购买活动及未付款订单，不产生账户变化。"""
        groups, _ = quoted_basket(self.context.inputs, state, pending.agent_id, tuple(goods_ids))
        duration = random_stream(state.random_state, f"purchase-duration:{pending.action_id}").randint(60, 300)
        orders = build_orders(self.context, state, pending, groups)
        orders = tuple(order.model_copy(update={"expected_at": state.current_time + duration}, deep=True)
                       if order.fulfillment == FulfillmentKind.PICKUP else order for order in orders)
        activity = _purchase_activity(state, pending, orders, duration, self.context.catalog)
        changed = state.model_copy(update={"deliveries": {**state.deliveries,
            **{order.order_id: order for order in orders}},
            "activities": {**state.activities, activity.activity_id: activity}}, deep=True)
        return changed, activity

    def payment_boundaries(self, state: WorldState, activity: ActivityState) -> tuple[int, ...]:
        """@brief 下一次推进必须经过实际支付操作开始和结束边界。"""
        segments = _payment_segments(state, activity)
        return tuple(sorted({moment for starts, ends, _ in segments for moment in (starts, ends)
                             if moment > state.current_time}))

    def advance_payment(self, state: WorldState, activity: ActivityState) -> tuple:
        """@brief 操作开始写一次真实 app 跳转，操作结束只更新订单时刻而不付款。"""
        changed, events = state, []
        for starts, ends, orders in _payment_segments(state, activity):
            first = orders[0]
            if state.current_time >= starts and first.payment_started_at is None:
                if state.current_time != starts:
                    raise IntegrityError("Purchase payment start boundary was skipped")
                changed = _update_orders(changed, orders, {"payment_started_at": starts})
                events.append(EventDraft(event_type="environment_event", occurred_at=starts,
                    agent_id=activity.agent_id, activity_id=activity.activity_id,
                    caused_by=[self._accepted_cause(activity)], visibility="system",
                    data={"kind": "app_touch", "app_id": first.payment_app_id,
                          "action": "purchase", "step": "payment", "rule_id": None}))
            if state.current_time >= ends and first.payment_ended_at is None:
                if state.current_time != ends:
                    raise IntegrityError("Purchase payment end boundary was skipped")
                current = tuple(changed.deliveries[row.order_id] for row in orders)
                changed = _update_orders(changed, current, {"payment_ended_at": ends})
        return changed, events

    def complete_purchase(self, state: WorldState, activity: ActivityState) -> tuple:
        """@brief 先核对整次总价，再在同次提交中依序结算全部组。"""
        orders = activity_orders(state, activity)
        if all(order.paid_at is not None for order in orders) or all(order.status == "cancelled" for order in orders):
            return state, []
        if any(order.paid_at is not None for order in orders):
            raise IntegrityError("Purchase contains partially paid groups")
        if any(order.payment_app_id is not None and order.payment_ended_at is None for order in orders):
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
        for grouped in order_groups(orders).values():
            changed, group_events = self._pay_group(changed, activity, grouped, cause)
            events.extend(group_events)
        paid_orders = activity_orders(changed, activity)
        scheduled = delivery_events(changed, paid_orders, cause)
        pending = sorted([*changed.pending_events, *scheduled],
                         key=lambda row: (row.due_at, row.sequence, row.scheduled_id))
        return changed.model_copy(update={"pending_events": pending}, deep=True), events

    def cancel_purchase(self, state: WorldState, activity: ActivityState) -> WorldState:
        """@brief 尚未付款的全部订单一起取消，不收费、不收货、不撤销真实跳转。"""
        orders = tuple(order for order in activity_orders(state, activity) if order.paid_at is None)
        return _update_orders(state, orders, {"status": "cancelled"})

    def collect_parcels(self, state: WorldState, agent_id: int, station_id: str) -> tuple:
        """@brief 本人在站交互一次领取全部包裹，因果由同次真实交互结果绑定。"""
        outcome = collect_parcels(state, self.context, agent_id, station_id)
        return outcome.state, list(outcome.receipts)

    def _accepted_cause(self, activity: ActivityState) -> str:
        """@brief 支付证据只关联已提交的真实购买受理结果。"""
        matches = [event for event in self.context.store.read_agent_events("action_result", activity.agent_id)
                   if event.action_id == activity.origin_action_id
                   and event.data.get("status") in {"accepted", "in_progress", "completed"}]
        if len(matches) != 1:
            raise IntegrityError("Purchase requires its unique committed acceptance result")
        return matches[0].event_id

    def _pay_group(self, state: WorldState, activity: ActivityState, orders: tuple, cause: str) -> tuple:
        """@brief 每组付款事实承载唯一账户变化，通知和到店收货引用该事实。"""
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
        if orders[0].payment_app_id is not None:
            events.append(_payment_notification(self.context, changed, orders, paid))
        if orders[0].fulfillment == FulfillmentKind.PICKUP:
            for order in orders:
                outcome = settle_receipt(changed, self.context, order.order_id)
                changed = outcome.state
                for receipt in outcome.receipts:
                    if receipt.data.get("kind") == "goods_received":
                        receipt.caused_by = [paid.event_id]
                events.extend(outcome.receipts)
        return changed, events


def _goods_names(orders: tuple, catalog) -> list[str]:
    """@brief 活动名里的商品名列表：按商品身份升序，同一商品的几份合写成「名称×N」。"""
    counts = Counter(order.goods_id for order in orders)
    return [catalog.goods[key].name if count == 1 else f"{catalog.goods[key].name}×{count}"
            for key, count in sorted(counts.items())]


def _purchase_activity(state: WorldState, pending, orders: tuple, duration: int, catalog) -> ActivityState:
    """@brief 同请求全部商品组成一个占槽位 2 的活动，时长只抽一次。"""
    goods = _goods_names(orders, catalog)
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


def _payment_segments(state: WorldState, activity: ActivityState) -> tuple:
    """@brief 非空支付组按键升序放在已冻结购买时长的尾段。"""
    groups = tuple(rows for rows in order_groups(activity_orders(state, activity)).values()
                   if rows[0].payment_app_id is not None)
    cursor = activity.planned_end_at - sum(rows[0].payment_duration_seconds for rows in groups)
    segments = []
    for rows in groups:
        ends = cursor + rows[0].payment_duration_seconds
        segments.append((cursor, ends, rows))
        cursor = ends
    return tuple(segments)


def _payment_notification(context, state: WorldState, orders: tuple, paid: EventDraft) -> EventDraft:
    """@brief 消费文字只读取真实金额和商品名称，通知自身不再次转账。"""
    names = [context.catalog.goods[order.goods_id].name for order in sorted(orders, key=lambda row: row.goods_id)]
    amount = paid.data["amount_fen"]
    return EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=orders[0].agent_id,
        caused_by=[paid.event_id], visibility="system", data={"kind": "payment_notification",
            "app_id": orders[0].payment_app_id, "amount_fen": amount,
            "order_ids": paid.data["order_ids"], "title": "支付成功",
            "summary": f"已消费 {amount / 100:.2f} 元，购买{'、'.join(names)}"})
