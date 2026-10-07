"""系统层事实有引擎证据：活动进行中本人订单送达。

正式单人场景建立的 LifeEngine 上经真实请求、引擎与 SQLite：人物 1 远程下单薯片后开始等待，配送在等待进行中送达。
那一秒的共同身体结算以 delivery_arrival 提供决策，人物在这一秒播报等待时写下的 decision_started 带同一原因，等待照常
进行、计划结束时刻不变。同样的事件落在睡着的人身上时，那一秒的结算静默，没有决策机会，睡眠继续。

两种事件都取场景里真实的预排事件（订单的配送、任务的开放），只把到期时刻改到活动进行中的一个固定秒，避开活动的计划
结束与本人的其他候选时刻；配送不能早于订单的预计送达时刻，所以下单后人物先照常过到预计送达之后，再开始那项活动。
"""

from __future__ import annotations

from tests.life.conftest import report_request, start_activity
from tests.life.routing_fixtures import (
    WAIT_ITEM,
    act,
    buy_food,
    routed,
    run_until,
    single_person_home,
    stage,
)
from tests.life.test_errand_reminder import settlement as body_settlement
from tests.life.test_errand_reminder import started_reasons, until
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.models import ActivityStatus, active_activities
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

#: 等待开始后多少秒事件到点：早于等待的计划结束（场景结算间隔 900 秒）。
_AWAKE_OFFSET = 420
#: 入睡后多少秒事件到点：睡眠仍在进行。
_ASLEEP_OFFSET = 300


def _move(engine, event: ScheduledEvent, due: int) -> None:
    """把一条预排事件改到 due 到期（身份、序号与载荷不变，按到期时刻重排），经存储提交。"""
    def change(state) -> None:
        """改到期时刻并重排。"""
        state.pending_events = sorted(
            [item.model_copy(update={"due_at": due}) if item.scheduled_id == event.scheduled_id else item
             for item in state.pending_events], key=lambda item: (item.due_at, item.sequence, item.scheduled_id))
    stage(engine, change)


def _idle(engine) -> bool:
    """人物 1 此刻可以决策，手上既没有进行中的活动，也没有待收尾的活动。"""
    state = engine.context.state
    return (1 in engine.scheduling_state().ready_agent_ids and not pending_conclusions(state, 1)
            and not active_activities(state, 1))


def _own_delivery(engine) -> tuple[str, ScheduledEvent]:
    """人物 1 在集市远程下单一份纯牛奶（送到这里，本人付款），返回（订单身份，它的配送事件）。配送不能早于订单的预计送达
    时刻：先把配送暂时移到三小时后，人物照常过日子（一段段等待做满），直到预计送达时刻之后、手上没有活动的那一秒。"""
    ordered = buy_food(engine, "order", way="远程下单")
    assert ordered.status == "completed", (ordered.error_code, ordered.message)
    order_id = ordered.data["orders"][0]["order_id"]
    delivery = next(item for item in engine.context.state.pending_events
                    if item.kind == ScheduledKind.DELIVERY_ARRIVAL and order_id in item.data["order_ids"])
    assert delivery.agent_id == 1
    expected = engine.context.state.deliveries[order_id].expected_at
    _move(engine, delivery, expected + 3 * 3600)
    run_until(engine, lambda current: current.context.state.current_time >= expected and _idle(current))
    assert engine.context.state.deliveries[order_id].status == "pending"
    return order_id, delivery


def _offered_while_waiting(engine, event: ScheduledEvent, reason: str) -> int:
    """人物 1 开始等待，把 event 改到等待进行中的一秒到点：那一秒的共同身体结算恰以 reason 提供决策，等待照常进行；人物在
    这一秒播报等待，SQLite 里这一秒唯一的 decision_started 带同一原因，播报之后等待仍在进行、计划结束时刻不变。返回到点时刻。"""
    waited = act(engine, "wait", routed(WAIT_ITEM))
    assert waited.status == "in_progress", (waited.error_code, waited.message)
    wait = engine.context.state.activities[waited.activity_id]
    due = engine.context.state.current_time + _AWAKE_OFFSET
    assert due < wait.planned_end_at
    _move(engine, event, due)
    until(engine, due)
    boundary = body_settlement(engine, due)
    assert (boundary.data["decision_offered"], boundary.data["wake_reasons"]) == (True, [reason])
    assert 1 in engine.scheduling_state().ready_agent_ids
    assert engine.context.state.agents[1].pending_wake_reasons == [reason]
    reported = act(engine, "reported", report_request(wait.name, summary="接着等。"), tool="report_status")
    assert reported.status == "completed", (reported.error_code, reported.message)
    assert started_reasons(engine, due) == [[reason]]
    current = engine.context.state.activities[wait.activity_id]
    assert (current.status, current.ended_at, current.planned_end_at) == (ActivityStatus.ACTIVE, None,
                                                                         wait.planned_end_at)
    assert engine.context.state.current_time == due and 1 not in engine.scheduling_state().ready_agent_ids
    return due


def _silent_while_asleep(engine, event: ScheduledEvent) -> int:
    """人物 1 开始睡觉，把 event 改到睡眠进行中的一秒到点：那一秒照常是结算边界，但他的共同身体结算静默（不提供决策、
    没有唤醒原因），没有人就绪，这一秒没有 decision_started，睡眠照常进行、计划结束时刻不变。返回到点时刻。"""
    slept = act(engine, "sleep", start_activity("睡觉", "自己"))
    assert slept.status == "in_progress", (slept.error_code, slept.message)
    sleep = engine.context.state.activities[slept.activity_id]
    due = engine.context.state.current_time + _ASLEEP_OFFSET
    assert due < sleep.planned_end_at
    _move(engine, event, due)
    until(engine, due)
    boundary = body_settlement(engine, due)
    assert (boundary.data["decision_offered"], boundary.data["wake_reasons"]) == (False, [])
    assert engine.scheduling_state().ready_agent_ids == () and started_reasons(engine, due) == []
    current = engine.context.state.activities[sleep.activity_id]
    assert (current.status, current.ended_at, current.planned_end_at) == (ActivityStatus.ACTIVE, None,
                                                                         sleep.planned_end_at)
    return due


def test_an_own_delivery_during_a_wait_offers_a_decision_and_the_wait_goes_on(engine_factory) -> None:
    """本人远程下单的配送在等待进行中送达：送达那一秒订单已送达、牛奶入库，共同身体结算只以 delivery_arrival 提供决策，
    这一秒的 decision_started 带 delivery_arrival，等待不因此结束。"""
    engine = single_person_home(engine_factory, {})
    order_id, delivery = _own_delivery(engine)
    due = _offered_while_waiting(engine, delivery, "delivery_arrival")
    assert engine.context.state.deliveries[order_id].status == "delivered"
    [arrival] = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                 if event.data.get("kind") == "delivery_arrival"]
    assert arrival.occurred_at == due


def test_a_sleeper_gets_no_decision_when_an_own_delivery_arrives(engine_factory) -> None:
    """同一笔本人订单在人物睡着时送达：订单照常送达，那一秒的结算静默，没有决策机会，睡眠继续。"""
    engine = single_person_home(engine_factory, {})
    order_id, delivery = _own_delivery(engine)
    _silent_while_asleep(engine, delivery)
    assert engine.context.state.deliveries[order_id].status == "delivered"
