"""@brief 联系人往来与实际送达商品，经真实消息和订单收货链验证。"""

from __future__ import annotations

from tests.life.conftest import (advance_next, conclude_request, context, pending_names, report_request,
                                 stage_day_plan, start_activity, start_arguments, submit_round, wake_by_mail, advance_until_ready, pause_request)
from tests.integration.life.test_acs_amended_runtime import perform, pump
from tests.integration.life.test_acs_runtime import request
from worldmock.footprints.models import EventDraft
from worldmock.life.contact_exchange import reply_request
from worldmock.life.models import REQUEST_MODELS, SendMessageActionRequest, active_activities
from worldmock.life.readable import clock_phrase, iso_time
from worldmock.life.social import HISTORY_LIMIT, Message

_FRIEND, _COLLEAGUE = "reply-friend", "reply-colleague"


def _world(factory, scenario="single-person.json"):
    """@brief 人工场景明确声明两位联系人的收货地址边界。"""
    def mutate(document):
        config = document["agents"][0]
        config["contacts"] = [
            {"contact_id": _FRIEND, "salutation": "沈昊", "relation": "friend", "closeness": 20,
             "channels": ["message"], "reply_policy": "model", "receive_location_id": None},
            {"contact_id": _COLLEAGUE, "salutation": "王磊", "relation": "colleague", "closeness": 15,
             "channels": ["message"], "reply_policy": "model", "receive_location_id": "office"}]
    engine = factory(scenario, mutate=mutate)
    for identity in engine.context.state.agents:
        stage_day_plan(engine, identity)
    return engine


def _act(engine, label, tool, value):
    """@brief 经全部真实就绪人物的原子提交取得本人结果。"""
    return perform(engine, label, value, tool)


def _send(engine, label: str, name: str, text: str):
    """@brief 人物实际发送消息，经联系人或同世界收件人路由结算。"""
    return _act(engine, label, "send_message", SendMessageActionRequest(recipient_name=name, text=text))


def _pending_request(engine):
    """@brief 读取实际发送链写入的最新联系人模型请求。"""
    events = engine.context.store.read_agent_events("environment_event", 1)
    return [item.data["request"] for item in events if item.data.get("kind") == "model_npc_reply_pending"][-1]


def _save(engine, staged):
    """@brief 显式预置消息历史，世界与足迹通过真实存储提交保持同一版本。"""
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_contact_history_staged"})])
    engine.context.state = staged


def test_messages_to_contacts_go_to_the_outbox_and_the_reply_request_carries_both_sides(engine_factory):
    """@brief 联系人收到真实消息，回复只携带此前双方往来，远程联系人没有虚构收货地。"""
    engine = _world(engine_factory, "multi-person.json")
    assert _send(engine, "friend-first", "联系人：沈昊", "周末有空吗？").status == "completed"
    previous = dict(engine.context.state.agents[1].outbox)
    assert [(item.recipient_id, item.text) for item in previous.values()] == [(_FRIEND, "周末有空吗？")]
    assert _pending_request(engine)["history"] == [] and _pending_request(engine)["received"] == []
    other = next(agent.name for agent in engine.context.scenario.agents if agent.agent_id == 2)
    assert _send(engine, "same-world", f"人物：{other}", "我先下楼了").status == "completed"
    assert len(engine.context.state.agents[1].outbox) == 1
    wake_by_mail(engine, offset=60)
    assert _act(engine, "friend-await", "start_activity", start_activity("等待", "自己")).status == "in_progress"
    assert advance_until_ready(engine)
    now = engine.context.state.current_time
    staged = engine.context.state.model_copy(deep=True)
    received = Message(message_id="friend-answer", kind="direct", sender_id=_FRIEND,
                       recipient_id="agent:1", text="周六可以，去哪？", created_at=now)
    staged.agents[1].inbox[received.message_id] = received
    _save(engine, staged)
    assert _send(engine, "friend-second", "联系人：沈昊", "去公园走走吧").status == "completed"
    request = _pending_request(engine)
    zone = engine.context.scenario.timezone
    name = engine.context.state.agents[1].name
    assert (request["sender_name"], request["now"], request["presence"], request["receive_location"]) == (
        name, iso_time(now, zone), "remote", None)
    assert request["history"] == [
        {"speaker": name, "time": clock_phrase(next(iter(previous.values())).created_at, now, zone),
         "text": "周末有空吗？"},
        {"speaker": "沈昊", "time": clock_phrase(now, now, zone), "text": "周六可以，去哪？"}]
    assert len(engine.context.state.agents[1].outbox) == 2


def test_reply_history_keeps_the_latest_twenty_messages_in_time_order(engine_factory):
    """@brief 实际回复请求按时间与消息身份确定性取双方最近二十条，不含本次来信。"""
    engine = _world(engine_factory)
    now = engine.context.state.current_time
    staged = engine.context.state.model_copy(deep=True)
    for index in range(25):
        sent = index % 2 == 0
        message = Message(message_id=f"m-{index:02d}", kind="direct", text=f"第 {index} 条",
                          sender_id="agent:1" if sent else _FRIEND, recipient_id=_FRIEND if sent else "agent:1",
                          created_at=now - 3000 + index * 60)
        target = staged.agents[1].outbox if sent else staged.agents[1].inbox
        target[message.message_id] = message
    _save(engine, staged)
    assert _send(engine, "friend-history", "联系人：沈昊", "最新一条").status == "completed"
    history = _pending_request(engine)["history"]
    assert len(history) == HISTORY_LIMIT == 20
    assert [item["text"] for item in history] == [f"第 {index} 条" for index in range(5, 25)]
    assert history[0]["speaker"] == "沈昊" and history[1]["speaker"] == engine.context.state.agents[1].name


def test_received_lists_only_actual_delivered_orders_to_the_contact(engine_factory):
    """@brief 联系人只收到真实订单，付款人身体没有联系人收货效果，也不建持有物。"""
    engine = _world(engine_factory)
    goods = engine.context.semantics.catalog.goods["beef-noodles"]
    before_body = engine.context.state.agents[1].body.model_dump()
    ordered = _act(engine, "contact-order", "start_activity",
                   request(goods.name, "美团", "购买", to="联系人：王磊", payment="微信"))
    assert ordered.error_code is None, ordered.data
    [order] = ordered.data["orders"]
    contact = next(item for item in engine.context.scenario.agents[0].contacts if item.contact_id == _COLLEAGUE)
    message = Message(message_id="received-probe", kind="direct", sender_id="agent:1", recipient_id=_COLLEAGUE,
                      text="到了吗", created_at=engine.context.state.current_time)
    assert reply_request(engine.context.state, engine.context.semantics, message, contact).received == ()
    assert engine.context.state.agents[1].body.model_dump() == before_body
    pump(engine, lambda: engine.context.state.deliveries[order["order_id"]].status == "delivered")
    now, zone = engine.context.state.current_time, engine.context.scenario.timezone
    actual = reply_request(engine.context.state, engine.context.semantics, message, contact)
    assert actual.receive_location == engine.context.semantics.catalog.locations["office"].display_name
    assert [item.model_dump() for item in actual.received] == [
        {"item": goods.name, "quantity": "1 份", "time": clock_phrase(order["expected_at"], now, zone)}]
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    assert not [event for event in events if event.data.get("kind") == "goods_received"]
    assert "contact_holdings" not in engine.context.state.model_dump()


def test_contact_without_a_receive_address_rejects_before_payment(engine_factory):
    """@brief 远程联系人没有声明收货地时拒绝，不猜家庭或当前位置。"""
    engine = _world(engine_factory)
    before = engine.context.state.accounts.copy()
    rejected = _act(engine, "no-contact-address", "start_activity",
                    request("牛肉面", "美团", "购买", to="联系人：沈昊", payment="微信"))
    assert rejected.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert engine.context.state.accounts == before and engine.context.state.deliveries == {}
