"""@brief 预排事件到点效果的纯处理器（设计文档 §7.2）。

每个处理器接收世界状态并返回状态变更与环境事件证据；引擎在事件点调用并负责
提交足迹与换持语义。资金类事件（BILL_DUE/PAYROLL）经 settlement.settle_money_transfer
结算，账本禁止负余额：余额不足时拒付（resource_changes 为空、bill_paid/payroll_paid
为 false），事件照发、运行不中断。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worldmock.life.commerce import deliver_order
from worldmock.life.models import WorldState
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.settlement import settle_money_transfer
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind


@dataclass(frozen=True)
class ScheduledOutcome:
    """@brief 单条预排事件的投影结果：世界状态、事件证据与可选新语义。"""

    state: WorldState
    data: dict[str, Any]
    semantics: ScenarioSemantics | None = None


def apply_scheduled_effect(state: WorldState, event: ScheduledEvent,
                           semantics: ScenarioSemantics) -> ScheduledOutcome:
    """@brief 将一个后台事件投影到订单、账户、库存或运行语义。"""
    if event.kind == ScheduledKind.DELIVERY_ARRIVAL:
        state, order = deliver_order(state, event.data["order_id"])
        change = {"location_id": order.destination_id, "product_id": order.product_id,
                  "quantity": order.quantity}
        return ScheduledOutcome(state, {"order": order.model_dump(mode="json"),
                                        "inventory_change": change})
    if event.kind == ScheduledKind.TASK_EXPIRED:
        return ScheduledOutcome(state, dict(event.data))
    if event.kind == ScheduledKind.CALENDAR_FLAG:
        return ScheduledOutcome(state, dict(event.data))
    if event.kind == ScheduledKind.BILL_DUE:
        return _bill_due(state, event)
    if event.kind == ScheduledKind.PAYROLL:
        return _payroll(state, event)
    if event.kind == ScheduledKind.PRICE_CHANGE:
        return _price_change(state, event, semantics)
    if event.kind == ScheduledKind.PRODUCT_RELEASE:
        return _product_release(state, event)
    agent = state.agents[event.agent_id]
    if event.kind == ScheduledKind.MAIL_ARRIVAL:
        visible = set(agent.visible_message_ids)
        visible.add(event.data["message_id"])
        agent = agent.model_copy(update={"visible_message_ids": visible}, deep=True)
    elif event.kind == ScheduledKind.WORK_AVAILABLE:
        visible = set(agent.visible_task_ids)
        visible.add(event.data["task_id"])
        agent = agent.model_copy(update={"visible_task_ids": visible}, deep=True)
    state.agents[agent.agent_id] = agent
    return ScheduledOutcome(state, dict(event.data))


def _bill_due(state: WorldState, event: ScheduledEvent) -> ScheduledOutcome:
    """@brief 到期账单从付款账户向收款账户扣款；余额不足拒付不崩运行。"""
    payer = event.data["account_id"]
    payee = event.data["payee_account_id"]
    amount = event.data["amount_fen"]
    data = {**event.data, "bill_paid": False, "resource_changes": {}}
    if state.accounts.get(payer, 0) < amount:
        return ScheduledOutcome(state, data)
    settled = settle_money_transfer(state, payer, payee, amount,
                                    f"bill:settle:{event.data['bill_id']}")
    data.update(bill_paid=True,
                resource_changes={f"account:{payer}": -amount, f"account:{payee}": amount})
    return ScheduledOutcome(settled, data)


def _payroll(state: WorldState, event: ScheduledEvent) -> ScheduledOutcome:
    """@brief 到点工资从雇主账户向人物账户入账；雇主余额不足拒付不崩运行。"""
    employer = event.data["employer_account_id"]
    recipient = f"agent:{event.data['agent_id']}"
    amount = event.data["amount_fen"]
    data = {**event.data, "payroll_paid": False, "resource_changes": {}}
    if state.accounts.get(employer, 0) < amount:
        return ScheduledOutcome(state, data)
    settled = settle_money_transfer(state, employer, recipient, amount,
                                    f"payroll:settle:{event.data['payroll_id']}")
    data.update(payroll_paid=True,
                resource_changes={f"account:{employer}": -amount,
                                  f"account:{recipient}": amount})
    return ScheduledOutcome(settled, data)


def _price_change(state: WorldState, event: ScheduledEvent,
                  semantics: ScenarioSemantics) -> ScheduledOutcome:
    """@brief 调价不触世界状态，返回换持了新价格语义的结果供引擎采纳。"""
    object_id = event.data["object_id"]
    amount = event.data["amount_fen"]
    version = f"catalog:{object_id}:v{event.data['object_version']}@{event.scheduled_id}"
    updated = semantics.with_price(object_id, amount, version)
    return ScheduledOutcome(state, {**event.data, "price_version": version}, semantics=updated)


def _product_release(state: WorldState, event: ScheduledEvent) -> ScheduledOutcome:
    """@brief 上架把商家该对象库存置为给定值并记录前后证据。"""
    object_id = event.data["object_id"]
    stock = dict(state.merchant_stock)
    before = stock.get(object_id, 0)
    stock[object_id] = event.data["stock"]
    changed = state.model_copy(update={"merchant_stock": stock}, deep=True)
    return ScheduledOutcome(changed, {**event.data, "stock_before": before})
