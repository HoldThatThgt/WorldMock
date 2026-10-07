"""@brief 联系人往来和实际收到的登记食品，经真实消息、订单与给予链验证。"""

from __future__ import annotations

from tests.life.conftest import (advance_next, conclude_request, context, pending_names, report_request,
                                 stage_day_plan, start_activity, start_arguments, submit_round, wake_by_mail, advance_until_ready, pause_request)
from tests.life.test_work_protocol import trip_to
from worldmock.footprints.models import EventDraft
from worldmock.life.contact_exchange import reply_request
from worldmock.life.models import REQUEST_MODELS, SendMessageActionRequest, active_activities
from worldmock.life.readable import clock_phrase, iso_time
from worldmock.life.social import HISTORY_LIMIT, Message

_FRIEND, _COLLEAGUE = "reply-friend", "reply-colleague"


def _world(factory, scenario="single-person.json"):
    """@brief 人工场景明确声明两位联系人和办公室持有食品，真实加载器验证它们。"""
    def mutate(document):
        config = document["agents"][0]
        config["contacts"] = [
            {"contact_id": _FRIEND, "salutation": "沈昊", "relation": "friend", "closeness": 20,
             "channels": ["message"], "reply_policy": "model", "receive_location_id": None},
            {"contact_id": _COLLEAGUE, "salutation": "王磊", "relation": "colleague", "closeness": 15,
             "channels": ["message"], "reply_policy": "model", "receive_location_id": "office"}]
        config["initial_inventory"] = {"office": {"chips": 0.5}}
    engine = factory(scenario, mutate=mutate)
    for identity in engine.context.state.agents:
        stage_day_plan(engine, identity)
    return engine


def _act(engine, label: str, tool: str, request):
    """@brief 真正结算全部就绪人物的请求，其他人物只开始或播报自己的等待。"""
    asks = {1: (tool, request)}
    for identity in engine.scheduling_state().ready_agent_ids:
        if identity == 1:
            continue
        running = active_activities(engine.context.state, identity)
        asks[identity] = ("report_status", report_request(*[item.name for item in running])) if running else (
            "start_activity", start_activity("等待", "自己"))
    _, results = submit_round(engine, asks, label)
    return next(item for item in results if item.agent_id == 1)


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


def _until_delivered(engine, order_id: str):
    """@brief 通过真实决策与时间推进等到配送完成，在途食品始终不计为收到。"""
    for index in range(32):
        if engine.context.state.deliveries[order_id].status == "delivered":
            return
        if engine.scheduling_state().ready_agent_ids:
            view = engine.observe(context(engine, f"delivery-view-{index}"))
            names = pending_names(view)
            if names:
                _act(engine, f"delivery-end-{index}", "conclude_activity", conclude_request(*names))
            else:
                running = active_activities(engine.context.state, 1)
                if running:
                    _act(engine, f"delivery-report-{index}", "report_status", report_request(*[a.name for a in running]))
                else:
                    _act(engine, f"delivery-wait-{index}", "start_activity", start_activity("等待", "自己"))
        else:
            advance_next(engine)
    raise AssertionError("the actual food delivery did not complete")


def test_received_lists_delivered_orders_and_gifts_to_the_contact(engine_factory):
    """@brief 实际回复只列已送达食品与当面交出的食品，食品名直接取登记目录。"""
    engine = _world(engine_factory)
    food = engine.context.semantics.catalog.foods["beef-noodles"]
    arguments = start_arguments([food.name, "自己"], ["obtain", "interact"], ["远程下单", "等待"],
                                **{"to": ["联系人：王磊", None]})
    ordered = _act(engine, "contact-order", "start_activity", REQUEST_MODELS["start_activity"](**arguments))
    assert ordered.status == "in_progress", (ordered.error_code, ordered.message)
    [order] = ordered.data["orders"]
    contact = next(item for item in engine.context.scenario.agents[0].contacts if item.contact_id == _COLLEAGUE)
    message = Message(message_id="received-probe", kind="direct", sender_id="agent:1", recipient_id=_COLLEAGUE,
                      text="到了吗", created_at=engine.context.state.current_time)
    assert reply_request(engine.context.state, engine.context.store, engine.context.semantics, message, contact).received == ()
    _until_delivered(engine, order["order_id"])
    ended = pending_names(engine.observe(context(engine, "delivery-ended")))
    if ended:
        assert _act(engine, "contact-wait-end", "conclude_activity", conclude_request(*ended)).status == "completed"
    running = active_activities(engine.context.state, 1)
    if running:
        assert _act(engine, "contact-pause", "pause_activity", pause_request(*[a.name for a in running])).status == "completed"
    trip_to(engine, "office", "contact-arrive")
    gift = engine.context.semantics.catalog.foods["chips"]
    given = _act(engine, "contact-gift", "start_activity", start_activity("给予", f"持有物：{gift.name}",
                                                                               **{"to": ["联系人：王磊"]}))
    assert given.status == "completed", (given.error_code, given.message)
    now, zone = engine.context.state.current_time, engine.context.scenario.timezone
    request = reply_request(engine.context.state, engine.context.store, engine.context.semantics, message, contact)
    assert request.receive_location == engine.context.semantics.catalog.locations["office"].display_name
    assert [item.model_dump() for item in request.received] == [
        {"item": food.name, "quantity": "1 份", "time": clock_phrase(order["expected_at"], now, zone)},
        {"item": gift.name, "quantity": "0.5 份", "time": clock_phrase(now, now, zone)}]
    assert engine.context.state.contact_holdings[_COLLEAGUE] == {food.food_id: 1, gift.food_id: 0.5}
