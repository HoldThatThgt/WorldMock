"""@brief 唯一后台排程处理器，先弹出到期项再记录事实与回调。"""

from __future__ import annotations

from dataclasses import dataclass

from worldmock.exchange.constants import WEATHER_FACTORS
from worldmock.footprints.models import EventDraft
from worldmock.life.callbacks import CallbackCause, evaluate_callbacks, issue_callback_reminders
from worldmock.life.models import ActivityKind, WorldState, active_activities
from worldmock.life.weather import generated_weather
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind


@dataclass(frozen=True)
class ScheduledOutcome:
    """@brief 已实际发生的后台变更与可选清醒唤醒来源。"""

    #: 唯一新世界状态。
    state: WorldState
    #: 该事件的实际事实字段，没有实际配送时为空。
    data: dict
    #: 只有实际推送或到货可提供现行触发名。
    wake: str | None = None
    #: 本批实际收货与对应重置证据。
    receipts: tuple[EventDraft, ...] = ()


def time_trigger_draft(state: WorldState, key: str, scheduled_id: str | None = None) -> EventDraft:
    """@brief 时间事实自身是无上游业务原因的回调触发源。"""
    identity = scheduled_id if scheduled_id is not None else f"time-{key}-{state.current_time}"
    return EventDraft(event_type="environment_event", occurred_at=state.current_time,
                      agent_id=next(iter(state.agents)), source_kind="environment", visibility="system",
                      data={"kind": "time_trigger", "key": key, "scheduled_id": identity})


def apply_scheduled_effect(state: WorldState, event: ScheduledEvent, context) -> ScheduledOutcome:
    """@brief 后台只分派五个现行排程种类，删除工资、邮件、联系人和闹钟分支。"""
    if event.kind == ScheduledKind.DELIVERY_ARRIVAL:
        return _deliver_orders(state, event, context)
    if event.kind == ScheduledKind.APP_PUSH:
        from worldmock.life.app_push import issue_app_push

        changed, data = issue_app_push(state, event, context.catalog)
        return ScheduledOutcome(changed, data, "app_push" if "push_id" in data and not _asleep(state) else None)
    if event.kind == ScheduledKind.WEATHER:
        date = event.data["local_date"]
        condition = generated_weather(state, date)
        changed = state.model_copy(update={"weather_by_date": {**state.weather_by_date, date: condition}})
        data = {"local_date": date, "condition": condition, "speed_factor": WEATHER_FACTORS[condition]}
        return ScheduledOutcome(changed, data)
    return ScheduledOutcome(state, dict(event.data))


def process_scheduled_events(state: WorldState, target: int, context) -> tuple[WorldState, list, dict]:
    """@brief 按稳定顺序提交真实事实，空配送和未投递推送不执行事件订阅。"""
    due = sorted((item for item in state.pending_events if item.due_at == target),
                 key=lambda item: (item.sequence, item.scheduled_id))
    remaining = [item for item in state.pending_events if item.due_at != target]
    state = state.model_copy(update={"pending_events": remaining})
    drafts, arrivals = [], {}
    for item in due:
        outcome = apply_scheduled_effect(state, item, context)
        state = outcome.state
        if item.kind == ScheduledKind.DELIVERY_ARRIVAL and not outcome.data:
            continue
        source = _scheduled_draft(state, item, outcome)
        drafts.append(source)
        for receipt in outcome.receipts:
            if receipt.data.get("kind") == "goods_received":
                receipt.caused_by = [source.event_id]
            drafts.append(receipt)
        if outcome.wake is not None:
            arrivals.setdefault(item.agent_id, set()).add(outcome.wake)
        state, effects = _scheduled_callbacks(state, item, source, context)
        drafts.extend(effects)
        if item.kind == ScheduledKind.APP_PUSH:
            reminder = issue_callback_reminders(state, context.catalog)
            state = reminder.state
            drafts.extend(reminder.drafts)
    return state, drafts, arrivals


def _scheduled_draft(state: WorldState, item: ScheduledEvent, outcome: ScheduledOutcome) -> EventDraft:
    """@brief 时间与天气使用封闭系统事实，内容推送与到货才可进入人物证据。"""
    if item.kind == ScheduledKind.TIME_TRIGGER:
        return time_trigger_draft(state, item.data["key"], item.scheduled_id)
    data = {"kind": item.kind.value, "scheduled_id": item.scheduled_id, **outcome.data}
    visible = (item.kind == ScheduledKind.DELIVERY_ARRIVAL
               or item.kind == ScheduledKind.APP_PUSH and "push_id" in outcome.data)
    return EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=item.agent_id,
                      source_kind="environment", visibility="agent" if visible else "system",
                      caused_by=list(item.caused_by), data=data)


def _scheduled_callbacks(state: WorldState, item: ScheduledEvent, source: EventDraft, context) -> tuple:
    """@brief 只对已写下的合格事实订阅，阈值提醒和支付通知不会进入普通推送订阅。"""
    if item.kind == ScheduledKind.TIME_TRIGGER:
        trigger_class, key = "time", item.data["key"]
    elif item.kind == ScheduledKind.APP_PUSH and "push_id" not in source.data:
        return state, []
    else:
        trigger_class, key = "event", item.kind.value
    cause = CallbackCause(trigger_class, key, item.agent_id, source.event_id, item.scheduled_id)
    outcome = evaluate_callbacks(state, context.catalog, cause)
    context.callback_causes.update(outcome.causes)
    return outcome.state, outcome.drafts


def _deliver_orders(state: WorldState, event: ScheduledEvent, context) -> ScheduledOutcome:
    """@brief 只有实际待配送订单形成到货事实，重复到期不再次收货或重置。"""
    from worldmock.life.commerce import deliver_order

    orders, receipts = [], []
    for identity in event.data["order_ids"]:
        if state.deliveries[identity].status != "pending":
            continue
        outcome = deliver_order(state, context, identity)
        state = outcome.state
        orders.append(outcome.order.model_dump(mode="json"))
        receipts.extend(outcome.receipts)
    wake = "delivery_arrival" if orders and not _asleep(state) else None
    return ScheduledOutcome(state, {"orders": orders} if orders else {}, wake, tuple(receipts))


def _asleep(state: WorldState) -> bool:
    """@brief 后台事件不得提前结束唯一人物的睡眠。"""
    identity = next(iter(state.agents))
    return any(activity.kind == ActivityKind.SLEEP for activity in active_activities(state, identity))
