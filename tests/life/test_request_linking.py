"""代点请求与订单关联（docs/dev/SPEC-persona-prompt.md 第五章「代点请求与订单关联」，验收台账「代点关联」）。

answering 填可见直接消息的规范名称「<发件人> HH:MM 的消息」（不是今天的加「M月D日」），私有绑定 message_bindings 把它
映射到消息；只关联 to 等于发件人的那几项并写入 request_message_id；同一（消息，收货人，商品）已有未取消的关联订单时
再提交以 REQUEST_ALREADY_HANDLED 拒绝并列出原订单与预计送达；换商品、回应新消息或不关联照常购买；关联在进度层的参数、
结果与 settled 句里可见。
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.life.routing_fixtures import (COLLEAGUE, COLLEAGUE_TARGET, HOME, OFFICE, act, contact_message, later,
                                         observe, open_world, routed, stage, supply_target)
from worldmock.life.readable import iso_time
from worldmock.life.request_linking import message_bindings


def _message_name(engine, message_id: str) -> str:
    """@brief 进度层 message_arrival 记录里人物看到的发件人与时刻组成的规范名称。"""
    observed = observe(engine, f"name-{message_id}")
    [binding] = [row for row in observed["message_bindings"] if row["message_id"] == message_id]
    record = next(row for row in observed["progress_layer"]["events"] if row["type"] == "message_arrival"
                  and row["kind"] == "direct" and row["text"] == engine.state.agents[1].inbox[message_id].text)
    assert binding["name"] == f"{record['sender']} {record['occurred_at'][11:16]} 的消息"
    assert binding["sender_id"] == COLLEAGUE
    return binding["name"]


def _two_supplies(engine) -> tuple[tuple[str, str], tuple[str, str]]:
    """@brief 本轮两件不同的能远程下单的集市供给：（目标名，商品身份）。"""
    observed = observe(engine, "two-supplies")
    rows = [row for row in observed["interaction_bindings"] if row["kind"] == "supply"
            and any(action["label"] == "远程下单" for action in row["actions"])]
    return ((rows[0]["object"], rows[0]["object_id"].removeprefix("supply-market-")),
            (rows[1]["object"], rows[1]["object_id"].removeprefix("supply-market-")))


def test_answering_links_only_items_sent_to_the_sender_and_shows_the_link(engine_factory) -> None:
    """answering 只关联 to 等于发件人的项：送给联系人的订单写 request_message_id，送给自己的不写；进度层参数带
    answering，结果的 purchases 写收货人与所回应的消息，settled 句写「（回应<消息>，收货人<人>）」。"""
    engine = open_world(engine_factory)
    message_id = contact_message(engine, "帮我也点一份吧，谢谢")
    name = _message_name(engine, message_id)
    target, food = supply_target(observe(engine, "supply"))
    result = act(engine, "order-both", routed((target, "obtain", "远程下单", None, None),
                                              (target, "obtain", "远程下单", None, COLLEAGUE_TARGET), answering=name))
    assert result.status == "completed", (result.error_code, result.message)
    assert [(item["recipient_id"], item["request_message_id"], item["destination_id"])
            for item in result.data["orders"]] == [("agent:1", None, HOME), (COLLEAGUE, message_id, OFFICE)]
    assert result.data["answering"] == name
    observed = observe(engine, "linked-progress")
    [call] = [row for row in observed["progress_layer"]["events"] if row["type"] == "tool_call"
              and row["arguments"].get("answering") == name]
    purchases = call["result"]["details"]["purchases"]
    assert [(item["recipient"], item["request"]) for item in purchases] == [("陈曦", None), ("王磊", name)]
    lines = observed["progress_meta"][observed["progress_layer"]["events"].index(call)]["lines"]
    product = engine.rules.items[food].name
    assert lines[0].startswith(f"10:01 远程下单{product} 1 份") and lines[0].endswith("送到张江居所（冻结场景锚点）")
    assert lines[1].endswith(f"送到张江高科技园区办公室（冻结场景锚点）（回应{name}，收货人王磊）")


def test_an_answering_name_written_without_spaces_is_resolved_to_that_message(engine_factory) -> None:
    """answering 按可见消息的名称统一解析（docs/dev/SPEC-dayrun-fidelity.md 3.1）：规范名称「<发件人> HH:MM 的消息」去掉空白
    写成「王磊10:01的消息」时无损归一唯一就是那条消息，环境照常关联订单（request_message_id）、结果的 answering 写规范名称，
    details.resolved 记下 {written, as}。"""
    engine = open_world(engine_factory)
    message_id = contact_message(engine, "帮我也点一份吧，谢谢")
    name = _message_name(engine, message_id)
    written = name.replace(" ", "")
    target, _ = supply_target(observe(engine, "supply"))
    result = act(engine, "order-bare-answering", routed((target, "obtain", "远程下单", None, COLLEAGUE_TARGET),
                                                        answering=written))
    assert (result.status, result.error_code) == ("completed", None)
    assert [item["request_message_id"] for item in result.data["orders"]] == [message_id]
    assert result.data["answering"] == name and {"written": written, "as": name} in result.data["resolved"]


def test_resubmitting_the_same_request_is_rejected_and_points_to_the_order(engine_factory) -> None:
    """已有同一（消息，收货人，商品）的未取消关联订单：再提交以 REQUEST_ALREADY_HANDLED 拒绝，details 列出已关联订单与
    预计送达时刻，不再扣款；换商品、不关联或回应新消息照常购买；关联订单取消后可以重新下单。"""
    engine = open_world(engine_factory)
    message_id = contact_message(engine, "中午帮我带一份")
    name = _message_name(engine, message_id)
    (first, food), (second, _) = _two_supplies(engine)
    ordered = act(engine, "linked", routed((first, "obtain", "远程下单", None, COLLEAGUE_TARGET), answering=name))
    [order] = ordered.data["orders"]
    balance = engine.state.accounts["agent:1"]
    repeat = act(engine, "linked-again", routed((first, "obtain", "远程下单", None, COLLEAGUE_TARGET), answering=name))
    product = engine.rules.items[food].name
    expected = iso_time(order["expected_at"], engine.scenario.timezone)
    assert (repeat.status, repeat.error_code) == ("rejected", "REQUEST_ALREADY_HANDLED")
    assert repeat.data["reason"] == f"An order for {product} has already been placed in response to {name}." and repeat.data["next_step"] == "Wait for the existing order to arrive."
    assert repeat.data["linked_orders"] == [{"order": f"{product} 1 份（10:01 远程下单）", "expected_at": expected}]
    assert engine.state.accounts["agent:1"] == balance and len(engine.state.deliveries) == 1
    observed = observe(engine, "repeat-progress")
    rejected = [row for row in observed["progress_layer"]["events"] if row["type"] == "tool_call"][-1]
    assert rejected["result"]["details"]["linked_orders"] == repeat.data["linked_orders"]
    later(engine)
    other = act(engine, "other-product", routed((second, "obtain", "远程下单", None, COLLEAGUE_TARGET), answering=name))
    unlinked = act(engine, "unlinked", routed((first, "obtain", "远程下单", None, COLLEAGUE_TARGET)))
    assert (other.status, unlinked.status) == ("completed", "completed")
    assert unlinked.data["orders"][0]["request_message_id"] is None
    stage(engine, lambda state: setattr(state.deliveries[order["order_id"]], "status", "cancelled"))
    later(engine)
    after_cancel = act(engine, "after-cancel",
                       routed((first, "obtain", "远程下单", None, COLLEAGUE_TARGET), answering=name))
    assert after_cancel.status == "completed" and after_cancel.data["orders"][0]["request_message_id"] == message_id


def test_a_new_message_is_a_new_request(engine_factory) -> None:
    """回应另一条新消息时，同一商品同一收货人照常购买（不全局禁止复购）。"""
    engine = open_world(engine_factory)
    first_id = contact_message(engine, "帮我带一份")
    first_name = _message_name(engine, first_id)
    target, _ = supply_target(observe(engine, "supply"))
    assert act(engine, "first", routed((target, "obtain", "远程下单", None, COLLEAGUE_TARGET),
                                       answering=first_name)).status == "completed"
    second_id = contact_message(engine, "再帮我带一份，下午吃", offset=120)
    second_name = _message_name(engine, second_id)
    assert second_name != first_name
    again = act(engine, "second", routed((target, "obtain", "远程下单", None, COLLEAGUE_TARGET), answering=second_name))
    assert again.status == "completed" and again.data["orders"][0]["request_message_id"] == second_id


def test_answering_a_cross_day_unreplied_memory_message_links_the_order(engine_factory) -> None:
    """跨日未回复的消息经记忆层关联：1月5日收到王磊的消息没有回复，出门结束那段停留、到次日 10:30，消息以「1月5日」的写法
    留在记忆层 earlier_days，私有绑定给出规范名称「王磊 1月5日 HH:MM 的消息」；按名称远程下单送给王磊并以它作 answering，
    订单写 request_message_id，进度层的 settled 句写「（回应…，收货人王磊）」；同一请求再提交以 REQUEST_ALREADY_HANDLED
    拒绝；关联不是回复，消息仍留在记忆层（第五章「代点请求与订单关联」、第三章「零点、反思与未了结」）。"""
    from tests.life.conftest import stage_day_plan
    from tests.life.routing_fixtures import with_colleague
    from tests.life.test_memory_layer import _act, _arrive, _contact_message, _jump, _next_morning, _walk
    from worldmock.life.named_purchase import supply_label

    engine = engine_factory(mutate=with_colleague)
    stage_day_plan(engine)
    received_at = engine.state.current_time
    message_id = _contact_message(engine, COLLEAGUE, "明天中午帮我点份吃的")
    _arrive(engine, "cross-arrive", _walk(engine, "cross-walk").activity_id)
    _jump(engine, _next_morning(received_at) + 5400)
    stage_day_plan(engine, submitted=True)
    zone = engine.scenario.timezone
    name = f"王磊 1月5日 {iso_time(received_at, zone)[11:16]} 的消息"
    seen = observe(engine, "cross-next-day")
    [day] = seen["memory_layer"]["earlier_days"]
    assert day["date"] == "2026-01-05" and day["settled"][0].endswith("收到王磊的消息：明天中午帮我点份吃的")
    assert seen["message_bindings"] == [{"name": name, "message_id": message_id, "sender_id": COLLEAGUE}]
    supply = next(item for item in engine.scenario.supplies
                  if "delivery" in item.fulfillment_modes and OFFICE in item.delivery_destination_ids)
    label = supply_label(engine._layer_context(engine.state.agents[1], (), []), supply)
    request = routed((label, "obtain", "远程下单", None, COLLEAGUE_TARGET), answering=name)
    _, ordered = _act(engine, "cross-order", "start_activity", request)
    assert ordered.status == "completed", (ordered.error_code, ordered.message)
    [order] = ordered.data["orders"]
    assert (order["recipient_id"], order["request_message_id"], order["destination_id"]) == (
        COLLEAGUE, message_id, OFFICE)
    observed = observe(engine, "cross-linked")
    [call] = [row for row in observed["progress_layer"]["events"] if row["type"] == "tool_call"
              and row["tool"] == "start_activity"]
    lines = observed["progress_meta"][observed["progress_layer"]["events"].index(call)]["lines"]
    assert call["arguments"]["answering"] == name and lines == [lines[0]]
    assert lines[0].endswith(f"送到张江高科技园区办公室（冻结场景锚点）（回应{name}，收货人王磊）")
    _, repeat = _act(engine, "cross-again", "start_activity", request)
    assert (repeat.status, repeat.error_code) == ("rejected", "REQUEST_ALREADY_HANDLED")
    assert observe(engine, "cross-still")["memory_layer"]["earlier_days"] == [day]


def _context(inbox: dict, arrivals: dict, now: int) -> tuple:
    """@brief message_bindings 的最小输入：人物 1 的收件箱、到达事件与此刻（时区 Asia/Shanghai）。"""
    agents = {1: SimpleNamespace(inbox=inbox, name="陈曦"), 2: SimpleNamespace(name="李凡")}
    contact = SimpleNamespace(contact_id=COLLEAGUE, salutation="李凡")
    context = SimpleNamespace(agent_id=1, state=SimpleNamespace(agents=agents, current_time=now),
                              scenario=SimpleNamespace(timezone="Asia/Shanghai",
                                                       agents=(SimpleNamespace(agent_id=1, contacts=(contact,)),)))
    events = [SimpleNamespace(data={"kind": "message_arrival", "message_id": key}, occurred_at=value)
              for key, value in arrivals.items()]
    events.append(SimpleNamespace(data={"kind": "mail_arrival", "message_id": "mail-1"}, occurred_at=now))
    return context, SimpleNamespace(read_agent_events=lambda *_: events)


def test_message_bindings_name_visible_direct_messages_by_sender_and_arrival() -> None:
    """只收全文呈现的直接消息（交谈邀请与未呈现的不收）；名称是发件人加送达的 HH:MM，不是今天的加「M月D日」；
    同一分钟同名的两条（同世界人物李凡与同事联系人李凡），名称指后一条。"""
    today = 1767582000  # 2026-01-05 11:00 +08:00
    inbox = {"a": SimpleNamespace(kind="direct", sender_id=COLLEAGUE),
             "b": SimpleNamespace(kind="direct", sender_id="agent:2"),
             "c": SimpleNamespace(kind="direct", sender_id=COLLEAGUE),
             "invite": SimpleNamespace(kind="chat_invite", sender_id="agent:2"),
             "hidden": SimpleNamespace(kind="direct", sender_id=COLLEAGUE)}
    arrivals = {"a": today - 86_400, "b": today - 600, "c": today - 590, "invite": today - 60, "hidden": today}
    context, store = _context(inbox, arrivals, today)
    assert message_bindings(context, store, ["a", "b", "c", "invite", "unknown"]) == [
        {"name": "李凡 1月4日 11:00 的消息", "message_id": "a", "sender_id": COLLEAGUE},
        {"name": "李凡 10:50 的消息", "message_id": "c", "sender_id": COLLEAGUE}]
