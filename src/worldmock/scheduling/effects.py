"""@brief 到期事件原子投影到世界账户、订单和门店供给，并返回实际变更证据。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from worldmock.errors import IntegrityError
from worldmock.life.basket import inventory_change
from worldmock.life.commerce import deliver_order
from worldmock.life.models import WorldState
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.settlement import settle_money_transfer
from worldmock.life.social import Message
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind


@dataclass(frozen=True)
class ScheduledOutcome:
    """@brief 单条预排事件的投影结果：持久化世界状态与实际事件证据。"""

    state: WorldState
    data: dict[str, Any]


def apply_scheduled_effect(state: WorldState, event: ScheduledEvent,
                           semantics: ScenarioSemantics) -> ScheduledOutcome:
    """@brief 将后台事件投影到世界；供给变更不得另建运行语义中的状态。"""
    if event.kind == ScheduledKind.DELIVERY_ARRIVAL:
        return _deliver_orders(state, event)
    if event.kind in {ScheduledKind.TASK_EXPIRED, ScheduledKind.CALENDAR_FLAG}:
        return ScheduledOutcome(state, dict(event.data))
    if event.kind == ScheduledKind.BILL_DUE:
        return _bill_due(state, event)
    if event.kind == ScheduledKind.PAYROLL:
        return _payroll(state, event)
    if event.kind == ScheduledKind.MESSAGE_ARRIVAL:
        return _deliver_message(state, event)
    return _reveal_event(state, event, semantics)


def _reveal_event(state: WorldState, event: ScheduledEvent, semantics: ScenarioSemantics) -> ScheduledOutcome:
    """@brief 到达邮件和可用任务只增加对应人物的实际可见身份。"""
    if event.kind not in {ScheduledKind.MAIL_ARRIVAL, ScheduledKind.WORK_AVAILABLE}:
        raise IntegrityError(f"Unsupported scheduled event kind: {event.kind}")
    agent = state.agents[event.agent_id]
    if event.kind == ScheduledKind.MAIL_ARRIVAL:
        visible = set(agent.visible_message_ids) | {event.data["message_id"]}
        message = next(item for item in semantics.scenario.messages if item.message_id == event.data["message_id"])
        tasks = set(agent.visible_task_ids)
        if message.related_task_id is not None:
            tasks.add(message.related_task_id)
        agent = agent.model_copy(update={"visible_message_ids": visible, "visible_task_ids": tasks}, deep=True)
    else:
        visible = set(agent.visible_task_ids) | {event.data["task_id"]}
        agent = agent.model_copy(update={"visible_task_ids": visible}, deep=True)
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    changed = state.model_copy(update={"agents": agents}, deep=True)
    return ScheduledOutcome(changed, dict(event.data))


def _deliver_message(state: WorldState, event: ScheduledEvent) -> ScheduledOutcome:
    """@brief 预排送达的联系人回复到期写入收件人收件箱，返回 message_arrival 送达记录（docs/CONTRACTS.md#contracts-worldmock-接口合同「联系人回复」）。"""
    message = Message.model_validate(event.data["message"])
    agent = state.agents[event.agent_id]
    agent = agent.model_copy(update={"inbox": {**agent.inbox, message.message_id: message}}, deep=True)
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    changed = state.model_copy(update={"agents": agents}, deep=True)
    return ScheduledOutcome(changed, {"message_id": message.message_id, "recipient_id": message.recipient_id,
                                      "occurred_at": event.due_at})


def _deliver_orders(state: WorldState, event: ScheduledEvent) -> ScheduledOutcome:
    """@brief 一次配送交付整单全部项目，重复到期不增加库存或虚报库存变更。"""
    identities = event.data["order_ids"]
    if not identities or len(set(identities)) != len(identities):
        raise IntegrityError("Delivery event requires unique nonempty order identities")
    orders = []
    changes = []
    changed = state
    for identity in identities:
        before = changed.deliveries.get(identity)
        changed, order = deliver_order(changed, identity)
        orders.append(order.model_dump(mode="json"))
        if before.status == "pending" and order.status == "delivered":
            changes.append(inventory_change(order))
    return ScheduledOutcome(changed, {"orders": orders, "inventory_changes": changes})


def _bill_due(state: WorldState, event: ScheduledEvent) -> ScheduledOutcome:
    """@brief 到期账单通过守恒账本结算；余额不足记录拒付且继续运行。"""
    payer = event.data["account_id"]
    payee = event.data["payee_account_id"]
    amount = event.data["amount_fen"]
    data = {**event.data, "bill_paid": False, "resource_changes": {}}
    if state.accounts.get(payer, 0) < amount:
        logging.error("Bill payment rejected: insufficient account funds")
        return ScheduledOutcome(state, data)
    settled = settle_money_transfer(state, payer, payee, amount,
                                    f"bill:settle:{event.data['bill_id']}")
    data.update(bill_paid=True,
                resource_changes={f"account:{payer}": -amount, f"account:{payee}": amount})
    return ScheduledOutcome(settled, data)


def _payroll(state: WorldState, event: ScheduledEvent) -> ScheduledOutcome:
    """@brief 到点工资通过守恒账本结算；雇主余额不足记录拒付且继续运行。"""
    employer = event.data["employer_account_id"]
    recipient = f"agent:{event.data['agent_id']}"
    amount = event.data["amount_fen"]
    data = {**event.data, "payroll_paid": False, "resource_changes": {}}
    if state.accounts.get(employer, 0) < amount:
        logging.error("Payroll payment rejected: insufficient employer funds")
        return ScheduledOutcome(state, data)
    settled = settle_money_transfer(state, employer, recipient, amount,
                                    f"payroll:settle:{event.data['payroll_id']}")
    data.update(payroll_paid=True,
                resource_changes={f"account:{employer}": -amount, f"account:{recipient}": amount})
    return ScheduledOutcome(settled, data)
