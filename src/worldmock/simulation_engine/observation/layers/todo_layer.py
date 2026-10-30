"""@brief 待办层保存暂停的活动、未办完的个人事务、在途订单及当前完整目标清单。"""

from __future__ import annotations

from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.activities.lifecycle import activity_control
from worldmock.simulation_engine.observation.layers.progress_layer import OPEN_ORDER_STATUSES, order_label
from worldmock.simulation_engine.scheduling.plans import errands
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.kinds import ActivityStatus
from worldmock.state.world.orders import DeliveryState
from worldmock.state.world.world import WorldState
from worldmock.foundation.time.readable import iso_time, local_date
from worldmock.state.planning.schedule import PlanGoal, epoch_clock
from worldmock.simulation_engine.observation.targets.tool_layer import Candidate, LayerContext, availability

#: 待办层在途订单至多列出的行数；多出的合成一行「另有 N 笔在途」（docs/design/06-agent.md「进度、待办与记忆」）。
ORDER_ROW_LIMIT = 6


def todo_layer(context: LayerContext, store: FootprintStore) -> dict:
    """@brief 待办层 JSON：暂停的活动、还没办完的个人事务、在途订单与今日计划。"""
    paused = [item for item in context.state.activities.values()
              if item.agent_id == context.agent_id and item.status == ActivityStatus.PAUSED]
    order = _pause_order(store, context.agent_id, [item.paused_at for item in paused])
    paused.sort(key=lambda item: (item.paused_at, order.get(item.activity_id, 0)))
    agent, names = context.state.agents[context.agent_id], _product_names(context)
    return {"layer": "待办层", "paused_activities": [_paused_item(context, item) for item in paused],
            "errands": errands.todo_rows(agent, names, context.config.timezone),
            "orders": _order_rows(context),
            "day_plan": _day_plan(context)}


def open_orders(state: WorldState, agent_id: int) -> tuple[DeliveryState, ...]:
    """@brief 本人的在途订单：已付款、还没收到（pending 或 ready_for_pickup）的商品履约记录，按预计到达与订单身份升序；
    待办层与零点计划共用。"""
    return tuple(sorted((order for order in state.deliveries.values()
                         if order.agent_id == agent_id and order.paid_at is not None
                         and order.status in OPEN_ORDER_STATUSES),
                        key=lambda order: (order.expected_at, order.order_id)))


def _order_rows(context: LayerContext) -> list[dict]:
    """@brief 待办层 orders：同商品、同购买时刻、同状态、同目的地、同预计到达的记录合成一行（份数为条数），至多
    ORDER_ROW_LIMIT 行，多出的写一行「另有 N 笔在途」。"""
    groups: dict[tuple, list[DeliveryState]] = {}
    for order in open_orders(context.state, context.agent_id):
        key = (order.goods_id, order.created_at, order.status, order.destination_id, order.expected_at)
        groups.setdefault(key, []).append(order)
    rows = [_order_row(context, group) for group in groups.values()]
    if len(rows) <= ORDER_ROW_LIMIT:
        return rows
    hidden = len(rows) - ORDER_ROW_LIMIT
    return [*rows[:ORDER_ROW_LIMIT], {"order": f"另有 {hidden} 笔在途"}]


def _order_row(context: LayerContext, group: list[DeliveryState]) -> dict:
    """@brief 合成的一行在途订单：订单标签（份数是这一组的条数）、状态、送到的地点显示名与预计到达。"""
    first, zone = group[0], context.config.timezone
    label = order_label({"goods_id": first.goods_id, "quantity": len(group), "created_at": first.created_at},
                        _product_names(context), (zone, context.state.current_time))
    return {"order": label, "status": first.status,
            "destination": context.catalog.locations[first.destination_id].display_name,
            "expected_at": iso_time(first.expected_at, zone)}


def _product_names(context: LayerContext) -> dict[str, str]:
    """@brief 冻结目录里商品的商品身份 → 商品名（事务行的「要办成的事」按它写商品名）。"""
    return {product_id: item.name for product_id, item in context.catalog.goods.items()}


def _pause_order(store: FootprintStore, agent_id: int, moments: list[int]) -> dict[str, int]:
    """@brief 同刻暂停的活动的事件顺序：活动 → 其最近一次 activity_paused 事件的序号；没有同刻暂停时不读事件。"""
    tied = [moment for moment in moments if moments.count(moment) > 1]
    if not tied:
        return {}
    after = store.last_event_seq_before(min(tied))
    return {event.data["activity_id"]: event.event_seq
            for event in store.read_agent_events("environment_event", agent_id, after=after)
            if event.data.get("kind") == "activity_paused"}


def _paused_item(context: LayerContext, activity: ActivityState) -> dict:
    """@brief 一项暂停的活动：名称、暂停时刻、恢复地点、客观进度与恢复、结束两个工具的可用性。"""
    place = context.catalog.locations[activity.location_id].display_name
    tools = [{"tool": tool, **availability(context, Candidate(tool, activity_id=activity.activity_id))}
             for tool in ("report_status", "conclude_activity")]
    return {"name": context.names["activities"][activity.activity_id],
            "paused_at": iso_time(activity.paused_at, context.config.timezone), "place": place,
            "progress": activity_control.objective_progress(activity), "tools": tools}


def visible_goals(state: WorldState, agent_id: int) -> tuple[PlanGoal, ...] | None:
    """@brief 待办层列出的今日目标（按计划顺序）；零点计划生成之前或当日结束之后没有今日计划，为 None。"""
    plan = state.agents[agent_id].day_plan
    if plan is None or plan.day_end <= state.current_time:
        return None
    return plan.goals


def _day_plan(context: LayerContext) -> dict | None:
    """@brief 今日计划：计划日期与各目标的意图、文字、提醒时刻（HH:MM 或 null）与大致时刻（HH:MM 或
    null，docs/design/04-simulation-engine.md）；没有或已过期时为 null。"""
    goals = visible_goals(context.state, context.agent_id)
    if goals is None:
        return None
    plan, zone = context.state.agents[context.agent_id].day_plan, context.config.timezone
    rows = [{"intent_kind": goal.intent_kind, "goal": goal.goal,
             "remind_time": epoch_clock(goal.remind_at, zone) if goal.remind_at is not None else None,
             "around_time": epoch_clock(goal.around_at, zone) if goal.around_at is not None else None}
            for goal in goals]
    return {"date": local_date(plan.day_end - 1, zone), "goals": rows}
