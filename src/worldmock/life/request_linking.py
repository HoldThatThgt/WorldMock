"""@brief 代点请求与订单关联（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「代点请求与订单关联」）。

answering 填一条可见直接消息的规范名称「<发件人> HH:MM 的消息」（不是今天的在 HH:MM 前加「M月D日」），时刻取消息送达
（进度层 message_arrival 的 occurred_at）；私有绑定 message_bindings 把名称映射到消息与发件人，只收本次观察可见的
直接消息：进度层 message_arrival 的与记忆层中未回复的（已回复的旧消息不可关联）。同一分钟同一发件人的两条消息名称相同时，
名称指后一条。
已有同一（消息，收货人，商品）的未取消关联订单时再提交，以 REQUEST_ALREADY_HANDLED 业务拒绝，details.linked_orders
列出已关联订单与预计送达时刻（带时区 ISO）；换了商品、回应新消息或不关联时照常购买。
"""

from __future__ import annotations

import logging
from typing import Any

from worldmock.footprints.store import FootprintStore
from worldmock.life.guidance import Refusal
from worldmock.life.vocabulary import PURCHASE_WAYS
from worldmock.life.readable import clock_phrase, iso_time, portion_phrase
from worldmock.life.recipients import person_name
from worldmock.life.tool_layer import LayerContext

#: 仍算已处理的订单状态（取消的不算）。
_HANDLED_STATUSES = frozenset({"pending", "ready", "delivered"})


def message_bindings(context: LayerContext, store: FootprintStore, presented: list[str]) -> list[dict]:
    """@brief 本次观察可见直接消息的私有绑定 [{"name", "message_id", "sender_id"}]，按给定顺序（收到先后），名称唯一。"""
    agent = context.state.agents[context.agent_id]
    arrivals = {event.data["message_id"]: event.occurred_at
                for event in store.read_agent_events("environment_event", context.agent_id)
                if event.data.get("kind") == "message_arrival"}
    rows: dict[str, dict] = {}
    for message_id in presented:
        message = agent.inbox.get(message_id)
        if message is None or message.kind != "direct":
            continue
        sender = person_name(context.state, context.scenario, context.agent_id, message.sender_id)
        clock = clock_phrase(arrivals[message_id], context.state.current_time, context.scenario.timezone)
        name = f"{sender} {clock} 的消息"
        rows.pop(name, None)
        rows[name] = {"name": name, "message_id": message_id, "sender_id": message.sender_id}
    return list(rows.values())


def order_label(order: dict[str, Any], names: dict[str, str], clock: tuple[str, int]) -> str:
    """@brief 订单的可读名称「<商品> <份数>（HH:MM <到店|远程下单>）」（HH:MM 为下单时刻）。

    :param names: 商品身份 → 商品名。
    :param clock: （时区，观察时刻）。
    """
    timezone_name, now = clock
    return (f"{names[order['food_id']]} {portion_phrase(order['quantity'])}（"
            f"{clock_phrase(order['created_at'], now, timezone_name)} {PURCHASE_WAYS[order['fulfillment']]}）")


def require_unhandled(state: Any, scenario: Any, bindings: list[dict]) -> None:
    """@brief 被 answering 关联的取得项已有同一（消息，收货人，商品）的未取消订单时以 REQUEST_ALREADY_HANDLED 拒绝。

    :param supplies: 供给身份 → 供给（取商品身份）。
    :param bindings: 已写上去向与关联的取得绑定行（routing.routed_selection）。
    """
    for binding in bindings:
        message_id = binding.get("request_message_id")
        if message_id is None:
            continue
        product = binding["food_id"]
        linked = sorted((order for order in state.deliveries.values() if order.request_message_id == message_id
                         and order.recipient_id == binding["recipient_id"] and order.food_id == product
                         and order.status in _HANDLED_STATUSES), key=lambda item: (item.created_at, item.order_id))
        if linked:
            logging.error("Linked request was already handled by an uncancelled order")
            names = {item.food_id: item.name for item in scenario.foods}
            clock = (scenario.timezone, state.current_time)
            rows = [{"order": order_label(order.model_dump(mode="json"), names, clock),
                     "expected_at": iso_time(order.expected_at, scenario.timezone)}
                    for order in linked]
            reason = f"An order for {names[product]} has already been placed in response to {binding['answering']}."
            raise Refusal("REQUEST_ALREADY_HANDLED", reason,
                          suggested={"next_step": "Wait for the existing order to arrive.",
                                     "linked_orders": rows}).error()
