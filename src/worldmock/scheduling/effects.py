"""@brief 到期事件原子投影到世界账户、订单和门店供给，并返回实际变更证据。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.app_push import issue_app_push
from worldmock.life.commerce import deliver_order
from worldmock.life.models import ActivityKind, WorldState, active_activities
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.settlement import settle_money_transfer
from worldmock.life.social import Message
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind


@dataclass(frozen=True)
class ScheduledOutcome:
    """@brief 单条预排事件的投影结果：持久化世界状态与实际事件证据。"""

    state: WorldState
    data: dict[str, Any]
    #: 到期事件的唯一唤醒来源；没有实际推送时为空。
    wake: str | None = None
    #: 人物可见推送来自环境，其余既有预排事件保持场景来源。
    source_kind: str = "scenario"
    #: 本次真实收货人的证据，与付款人送达事件同一次提交。
    receipts: tuple[EventDraft, ...] = ()


def apply_scheduled_effect(state: WorldState, event: ScheduledEvent,
                           semantics: ScenarioSemantics) -> ScheduledOutcome:
    """@brief 将后台事件投影到世界；供给变更不得另建运行语义中的状态。"""
    if event.kind == ScheduledKind.DELIVERY_ARRIVAL:
        return _deliver_orders(state, event, semantics)
    if event.kind == ScheduledKind.APP_PUSH:
        changed, data = issue_app_push(state, event, semantics.catalog)
        sleeping = any(item.kind == ActivityKind.SLEEP for item in active_activities(state, event.agent_id))
        wake = "app_push" if "push_id" in data and not sleeping else None
        return ScheduledOutcome(changed, data, wake, "environment")
    if event.kind == ScheduledKind.CALENDAR_FLAG:
        return ScheduledOutcome(state, dict(event.data), "message_arrival")
    if event.kind == ScheduledKind.BILL_DUE:
        return _bill_due(state, event)
    if event.kind == ScheduledKind.PAYROLL:
        return _payroll(state, event)
    if event.kind == ScheduledKind.MESSAGE_ARRIVAL:
        return _deliver_message(state, event)
    return _reveal_event(state, event, semantics)


def process_scheduled_events(state: WorldState, target: int, semantics: ScenarioSemantics
                             ) -> tuple[WorldState, list[EventDraft], dict[int, set[str]]]:
    """@brief 效果与提交前先弹出到期事件，再依稳定顺序生成实际环境记录和人物唤醒。"""
    due = [item for item in state.pending_events if item.due_at == target]
    state = state.model_copy(update={"pending_events": [item for item in state.pending_events
                                                      if item.due_at != target]})
    drafts = []
    arrivals: dict[int, set[str]] = {}
    for item in sorted(due, key=lambda value: (value.sequence, value.scheduled_id)):
        outcome = apply_scheduled_effect(state, item, semantics)
        state = outcome.state
        if item.kind == ScheduledKind.DELIVERY_ARRIVAL and not outcome.data:
            continue
        if item.agent_id is not None and outcome.wake is not None:
            arrivals.setdefault(item.agent_id, set()).add(outcome.wake)
        visibility = "system" if item.kind == ScheduledKind.APP_PUSH and "push_id" not in outcome.data else "agent"
        source = EventDraft(event_type="environment_event", occurred_at=target, agent_id=item.agent_id,
                            source_kind=outcome.source_kind, visibility=visibility,
                            data={"kind": item.kind.value, "scheduled_id": item.scheduled_id, **outcome.data})
        drafts.append(source)
        for receipt in outcome.receipts:
            receipt.caused_by = [source.event_id]
            drafts.append(receipt)
            if not receipt.data["recipient_asleep"]:
                arrivals.setdefault(receipt.agent_id, set()).add("delivery_arrival")
    return state, drafts, arrivals


def _reveal_event(state: WorldState, event: ScheduledEvent, semantics: ScenarioSemantics) -> ScheduledOutcome:
    """@brief 普通邮件到达只增加对应人物的可见邮件，不创建工作资格或任务状态。"""
    if event.kind != ScheduledKind.MAIL_ARRIVAL:
        logging.error("Unsupported scheduled event kind: %s", event.kind)
        raise IntegrityError(f"Unsupported scheduled event kind: {event.kind}")
    agent = state.agents[event.agent_id]
    visible = set(agent.visible_message_ids) | {event.data["message_id"]}
    agent = agent.model_copy(update={"visible_message_ids": visible}, deep=True)
    agents = {**state.agents, agent.agent_id: agent}
    changed = state.model_copy(update={"agents": agents}, deep=True)
    return ScheduledOutcome(changed, dict(event.data), "message_arrival")


def _deliver_message(state: WorldState, event: ScheduledEvent) -> ScheduledOutcome:
    """@brief 预排送达的联系人回复到期写入收件人收件箱，返回 message_arrival 送达记录（docs/CONTRACTS.md#contracts-worldmock-接口合同「联系人回复」）。"""
    message = Message.model_validate(event.data["message"])
    agent = state.agents[event.agent_id]
    agent = agent.model_copy(update={"inbox": {**agent.inbox, message.message_id: message}}, deep=True)
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    changed = state.model_copy(update={"agents": agents}, deep=True)
    return ScheduledOutcome(changed, {"message_id": message.message_id, "recipient_id": message.recipient_id,
                                      "occurred_at": event.due_at}, "message_arrival")


def _deliver_orders(state: WorldState, event: ScheduledEvent,
                    semantics: ScenarioSemantics) -> ScheduledOutcome:
    """@brief 配送整单在到期当秒收货，重复到期不产生状态、分录或事件。"""
    identities = event.data["order_ids"]
    if not identities or len(set(identities)) != len(identities):
        logging.error("Delivery event requires unique nonempty order identities")
        raise IntegrityError("Delivery event requires unique nonempty order identities")
    orders = []
    receipts = []
    changed = state
    for identity in identities:
        outcome = deliver_order(changed, semantics, identity)
        if changed.deliveries[identity].status == "pending":
            orders.append(outcome.order.model_dump(mode="json"))
            receipts.extend(outcome.receipts)
        changed = outcome.state
    data = {"orders": orders} if orders else {}
    return ScheduledOutcome(changed, data, "delivery_arrival" if orders else None,
                            receipts=tuple(receipts))


def _bill_due(state: WorldState, event: ScheduledEvent) -> ScheduledOutcome:
    """@brief 到期账单通过守恒账本结算；余额不足记录拒付且继续运行。"""
    payer = event.data["account_id"]
    payee = event.data["payee_account_id"]
    amount = event.data["amount_fen"]
    data = {**event.data, "bill_paid": False, "resource_changes": {}}
    if state.accounts.get(payer, 0) < amount:
        logging.error("Bill payment rejected: insufficient account funds")
        return ScheduledOutcome(state, data, "message_arrival")
    settled = settle_money_transfer(state, payer, payee, amount,
                                    f"bill:settle:{event.data['bill_id']}")
    data.update(bill_paid=True,
                resource_changes={f"account:{payer}": -amount, f"account:{payee}": amount})
    return ScheduledOutcome(settled, data, "message_arrival")


def _payroll(state: WorldState, event: ScheduledEvent) -> ScheduledOutcome:
    """@brief 到点工资通过守恒账本结算；雇主余额不足记录拒付且继续运行。"""
    employer = event.data["employer_account_id"]
    recipient = f"agent:{event.data['agent_id']}"
    amount = event.data["amount_fen"]
    data = {**event.data, "payroll_paid": False, "resource_changes": {}}
    if state.accounts.get(employer, 0) < amount:
        logging.error("Payroll payment rejected: insufficient employer funds")
        return ScheduledOutcome(state, data, "message_arrival")
    settled = settle_money_transfer(state, employer, recipient, amount,
                                    f"payroll:settle:{event.data['payroll_id']}")
    data.update(payroll_paid=True,
                resource_changes={f"account:{employer}": -amount, f"account:{recipient}": amount})
    return ScheduledOutcome(settled, data, "message_arrival")
