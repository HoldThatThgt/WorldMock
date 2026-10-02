"""联系人往来与收货（docs/dev/SPEC-persona-prompt.md 第五章「联系人往来」，裁决 #24）。

发给场景联系人的消息记入 AgentState.outbox；model 策略联系人的回复请求带双方往来 history（按时间排序，至多 20 条，
不含本次来信）与联系人实际收到的物品 received（收货人是它的已送达订单与当面给予）；回复策略与延迟送达不变。
"""

from __future__ import annotations

from tests.life.routing_fixtures import (COLLEAGUE, COLLEAGUE_TARGET, OFFICE, act, contact_message, holding_name,
                                         observe, open_world, place, routed, run_until, stage, supply_target)
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.contact_exchange import reply_request
from worldmock.life.interaction_menu import GIVE_WAY
from worldmock.life.models import SendMessageActionRequest, active_activities
from worldmock.life.readable import clock_phrase, iso_time
from worldmock.life.social import HISTORY_LIMIT, Message

_FRIEND = "contact-chenxi-oldfriend"


def _idle(engine) -> bool:
    """@brief 人物 1 此刻可以决策，且没有进行中或待收尾的活动。"""
    return (1 in engine.scheduling_state().ready_agent_ids and not active_activities(engine.state, 1)
            and not pending_conclusions(engine.state, 1))


def _send(engine, name: str, recipient: str, text: str):
    """@brief 人物 1 按收件人名称发一条消息，返回结果。"""
    return act(engine, name, SendMessageActionRequest(recipient_name=recipient, text=text), tool="send_message")


def _pending_request(engine) -> dict:
    """@brief 最近一条待执行的 model 联系人回复请求。"""
    events = [event for event in engine.store.read_agent_events("environment_event", 1)
              if event.data.get("kind") == "model_npc_reply_pending"]
    return events[-1].data["request"]


def test_messages_to_contacts_go_to_the_outbox_and_the_reply_request_carries_both_sides(engine_factory) -> None:
    """发给联系人的消息进 outbox，发给同世界人物的不进；model 联系人的回复请求带此前双方的消息（本人与联系人各自的
    说话人、可读时刻与全文，按时间排序，不含本次来信）和联系人实际收到的物品（此时为空）。"""
    engine = open_world(engine_factory)
    first = _send(engine, "to-friend", "联系人：沈昊", "周末有空吗？")
    assert first.status == "completed", (first.error_code, first.message)
    outbox = engine.state.agents[1].outbox
    assert [(item.recipient_id, item.text) for item in outbox.values()] == [(_FRIEND, "周末有空吗？")]
    assert _pending_request(engine)["history"] == [] and _pending_request(engine)["received"] == []
    reply_id = contact_message(engine, "周六可以，去哪？", contact_id=_FRIEND)
    replied_at = engine.state.agents[1].inbox[reply_id].created_at
    person = _send(engine, "to-person", "人物：李凡", "我先下楼了")
    assert person.status == "completed" and len(engine.state.agents[1].outbox) == 1
    second = _send(engine, "to-friend-again", "联系人：沈昊", "去公园走走吧")
    assert second.status == "completed"
    zone, now = engine.scenario.timezone, engine.state.current_time
    request = _pending_request(engine)
    assert request["incoming_text"] == "去公园走走吧"
    # 来信事实（docs/dev/SPEC-dayrun-fidelity.md 7.3）：写信人物的姓名、此刻、对方在远程，沈昊没有收货地点。
    assert (request["sender_name"], request["now"], request["presence"], request["receive_location"]) == (
        engine.state.agents[1].name, iso_time(now, zone), "remote", None)
    assert request["history"] == [
        {"speaker": "陈曦", "time": clock_phrase(next(iter(outbox.values())).created_at, now, zone),
         "text": "周末有空吗？"},
        {"speaker": "沈昊", "time": clock_phrase(replied_at, now, zone), "text": "周六可以，去哪？"}]
    assert len(engine.state.agents[1].outbox) == 2


def test_reply_history_keeps_the_latest_twenty_messages_in_time_order(engine_factory) -> None:
    """往来超过 20 条时只留最近的 20 条，按时刻（同一时刻按消息身份）排序。"""
    engine = open_world(engine_factory)
    now = engine.state.current_time

    def change(state):
        """@brief 预置 13 条发出与 12 条收到的消息，时刻交错。"""
        agent = state.agents[1]
        for index in range(25):
            sent = index % 2 == 0
            message = Message(message_id=f"m-{index:02d}", kind="direct", text=f"第 {index} 条",
                              sender_id="agent:1" if sent else _FRIEND, recipient_id=_FRIEND if sent else "agent:1",
                              created_at=now - 3000 + index * 60)
            (agent.outbox if sent else agent.inbox)[message.message_id] = message
    stage(engine, change)
    assert _send(engine, "many", "联系人：沈昊", "最新一条").status == "completed"
    history = _pending_request(engine)["history"]
    assert len(history) == HISTORY_LIMIT == 20
    assert [item["text"] for item in history] == [f"第 {index} 条" for index in range(5, 25)]
    assert history[0]["speaker"] == "沈昊" and history[1]["speaker"] == "陈曦"


def test_received_lists_delivered_orders_and_gifts_to_the_contact(engine_factory) -> None:
    """联系人实际收到的物品：收货人是它的已送达订单（送达时刻）与当面给予（给予时刻），在途的订单不算。"""
    engine = open_world(engine_factory)
    target, food = supply_target(observe(engine, "supply"))
    ordered = act(engine, "order-for-colleague", routed((target, "obtain", "远程下单", None, COLLEAGUE_TARGET),
                                                         ("自己", "interact", "等待", None, None)))
    [order] = ordered.data["orders"]
    colleague = next(item for item in engine.scenario.agents[0].contacts if item.contact_id == COLLEAGUE)
    message = Message(message_id="probe", kind="direct", sender_id="agent:1", recipient_id=COLLEAGUE, text="到了吗",
                      created_at=engine.state.current_time)
    assert reply_request(engine.state, engine.store, engine.scenario, message, colleague).received == ()
    run_until(engine, lambda current: current.state.deliveries[order["order_id"]].status == "delivered")
    snack = next(item.product_id for item in engine.scenario.supplies if item.product_id != food
                 and "eat" in engine.rules.items[item.product_id].use_modes)
    run_until(engine, _idle)
    place(engine, 1, OFFICE, {snack: 0.5})
    given = act(engine, "give-colleague", routed((holding_name(engine, snack), "consume", GIVE_WAY, None,
                                                  COLLEAGUE_TARGET)))
    assert given.status == "completed", (given.error_code, given.message)
    zone, now = engine.scenario.timezone, engine.state.current_time
    request = reply_request(engine.state, engine.store, engine.scenario, message, colleague)
    assert request.receive_location == next(item.name for item in engine.scenario.locations
                                            if item.location_id == OFFICE)
    assert [item.model_dump() for item in request.received] == [
        {"item": engine.rules.items[food].name, "quantity": "1 份",
         "time": clock_phrase(order["expected_at"], now, zone)},
        {"item": engine.rules.items[snack].name, "quantity": "0.5 份", "time": clock_phrase(now, now, zone)}]
    assert engine.state.contact_holdings[COLLEAGUE] == {food: 1.0, snack: 0.5}
