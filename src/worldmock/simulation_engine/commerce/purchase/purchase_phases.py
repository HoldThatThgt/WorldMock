"""@brief 购买的实际时长、下单段与付款段，以及逐边界推进；全部是只读写世界副本的纯函数。

一次购买的商品履约记录按下单的内容 app 分成订单。购买时长把付款包在里面：先是各张带下单 app 的订单依次下单，
再是各张订单依次付款，最后一个付款段结束于购买的计划结束。段的起止只由购买活动的开始时刻、计划时长和
各张订单冻结的付款时长算出，不另存一份，恢复和重放得到同样的段。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.state.world.kinds import ActivityKind, ActivityStatus
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.orders import DeliveryState
from worldmock.state.world.world import WorldState
from worldmock.simulation_engine.activities.travel.navigation_foreground import foreground_event
from worldmock.simulation_engine.commerce.purchase.order_routing import payment_orders

_LOGGER = logging.getLogger(__name__)
#: 段的两种步骤：在下单的内容 app 里下单，以及付款。
ORDERING, PAYMENT = "ordering", "payment"


@dataclass(frozen=True, slots=True)
class OrderPayment:
    """@brief 一张订单受理时冻结的付款抽取。"""

    #: 订单键，即下单的内容 app 的规范 JSON。
    order_key: str
    #: 下单的内容 app；到店订单为空。
    app_id: str | None
    #: 抽到的支付 app；直付为空。
    payment_app_id: str | None
    #: 付款时长秒数；到店直付为 0。
    seconds: int


@dataclass(frozen=True, slots=True)
class PurchasePhase:
    """@brief 购买里一个 app 连续在前台的一段，半开区间。"""

    #: 步骤：ordering 或 payment。
    step: str
    #: 这一段属于哪张订单。
    order_key: str
    #: 前台 app：下单段是订单的下单 app；付款段是支付 app，直付时是订单的下单 app。
    app_id: str
    #: 段的开始时刻。
    started_at: int
    #: 段的结束时刻。
    ended_at: int


def purchase_seconds(drawn: int, payments: tuple[OrderPayment, ...]) -> int:
    """@brief 实际购买时长：抽到的购买时长与各张订单付款时长合计中较大的一个。"""
    return max(drawn, sum(item.seconds for item in payments))


def split_phases(started_at: int, seconds: int, payments: tuple[OrderPayment, ...]) -> tuple[PurchasePhase, ...]:
    """@brief 把一次购买排成下单段和付款段，各段互不重叠，有带下单 app 的订单时首尾相接铺满整次购买。

    :param seconds: 实际购买时长；它减去付款合计是下单时间。
    :param payments: 各张订单的付款抽取，按订单键升序。

    下单时间按订单键升序分给带下单 app 的订单：前面每张各得整除后的秒数，最后一张拿剩下的全部，长度为 0 的段不存在。
    付款段从下单时间之后起首尾相接，付款时长为 0 的到店直付订单没有付款段。
    """
    ordering = seconds - sum(item.seconds for item in payments)
    apps = [item for item in payments if item.app_id is not None]
    phases, cursor = [], started_at
    for index, item in enumerate(apps):
        last = index == len(apps) - 1
        ends = started_at + ordering if last else cursor + ordering // len(apps)
        if ends > cursor:
            phases.append(PurchasePhase(ORDERING, item.order_key, item.app_id, cursor, ends))
        cursor = ends
    cursor = started_at + ordering
    for item in payments:
        if item.seconds:
            app = item.payment_app_id if item.payment_app_id is not None else item.app_id
            phases.append(PurchasePhase(PAYMENT, item.order_key, app, cursor, cursor + item.seconds))
            cursor += item.seconds
    return tuple(phases)


def order_payments(orders: tuple[DeliveryState, ...]) -> tuple[OrderPayment, ...]:
    """@brief 从商品履约记录读回各张订单冻结的付款抽取，按订单键升序；同一张订单的记录取值相同。"""
    return tuple(OrderPayment(key, rows[0].app_id, rows[0].payment_app_id, rows[0].payment_duration_seconds)
                 for key, rows in payment_orders(orders).items())


def activity_phases(state: WorldState, activity: ActivityState) -> tuple[PurchasePhase, ...]:
    """@brief 一项购买活动的全部段：由活动的开始时刻、计划时长和它列出的记录算出。"""
    orders = tuple(state.deliveries[key] for key in activity.data["order_ids"])
    return split_phases(activity.started_at, activity.planned_duration_seconds, order_payments(orders))


def phase_boundaries(state: WorldState, activity: ActivityState) -> tuple[int, ...]:
    """@brief 严格晚于当前时刻的段边界，升序；下一次推进不得越过其中任何一个。"""
    moments = {moment for phase in activity_phases(state, activity) for moment in (phase.started_at, phase.ended_at)}
    return tuple(sorted(moment for moment in moments if moment > state.current_time))


def advance_phases(state: WorldState, activity: ActivityState, since: int) -> WorldState:
    """@brief 把恰好落在当前时刻的付款段边界写到所属订单的全部记录上：开始写 payment_started_at，走完写
    payment_ended_at。付款段结束不扣款。

    :param since: 这项购买上一次结算到的时刻；受理的那一次提交传购买开始时刻。
    :raises IntegrityError: since 与当前时刻之间还有段边界，即推进越过了某个段的开始或结束。
    """
    now, phases = state.current_time, activity_phases(state, activity)
    if any(since < moment < now for phase in phases for moment in (phase.started_at, phase.ended_at)):
        _LOGGER.error("Purchase phase boundary was skipped")
        raise IntegrityError("Purchase phase boundary was skipped")
    reached = {phase.order_key: field for phase in phases if phase.step == PAYMENT
               for field, moment in (("payment_started_at", phase.started_at), ("payment_ended_at", phase.ended_at))
               if moment == now}
    if not reached:
        return state
    orders = tuple(state.deliveries[key] for key in activity.data["order_ids"])
    changed = {order.order_id: order.model_copy(update={reached[key]: now}, deep=True)
               for key, rows in payment_orders(orders).items() if key in reached for order in rows}
    return state.model_copy(update={"deliveries": {**state.deliveries, **changed}}, deep=True)


def foreground_events(before: WorldState, after: WorldState) -> list[EventDraft]:
    """@brief 世界提交之前写购买里各 app 的前台区间：每个带前台 app 的段在它结束的那一秒写一条。

    :param before: 提交前的世界；只看其中进行中的购买。
    :param after: 待提交的世界；当前时刻取它的时钟。

    时钟走到一个段的结束时刻就写这一段。购买在这次提交里停止而没有走完（人物提前结束，或运行截止截断）时，
    正在进行的那一段在当前时刻截断并写出，之前走完的段不回删；长度为 0 的区间不写。
    """
    now, drafts = after.current_time, []
    for activity in before.activities.values():
        if activity.kind != ActivityKind.PURCHASE or activity.status != ActivityStatus.ACTIVE:
            continue
        stopped = after.activities[activity.activity_id].status != ActivityStatus.ACTIVE
        for phase in activity_phases(after, activity):
            ended = phase.ended_at == now and before.current_time < now
            cut = stopped and phase.started_at < now < phase.ended_at
            if ended or cut:
                drafts.append(foreground_event(activity, phase.app_id, phase.step, (phase.started_at, now)))
    return drafts
