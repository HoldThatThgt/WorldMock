"""给予与收货人（docs/dev/SPEC-persona-prompt.md 第五章「来源与去向」，验收台账「来源与去向」）。

真实 LifeEngine 与 SQLite：给予瞬时、不占名额、不增加任何人的摄入，每件移出 min(1, 持有) 记入对方持有，要求同处；
收件人不存在、手边没有、不同处各有拒绝码；订单只有一个收货人，送达只入收货人的持有（同世界人物的库存或联系人的
contact_holdings），付款人仍是下单的人；同一请求里同一商品可以分给两人，订单按（方式，收货地）拆分；自己收货后再
给予只转移一次。
"""

from __future__ import annotations

import pytest

from tests.life.conftest import start_request, submit_round
from tests.life.routing_fixtures import (COLLEAGUE, COLLEAGUE_TARGET, HOME, MARKET, OFFICE, PERSON_TARGET, act,
                                         food_product, holding_name, observe, open_world, place, routed, run_until,
                                         stage, supply_target)
from worldmock.errors import BusinessRuleError
from worldmock.life import activity_control, same_moment
from worldmock.life.interaction_menu import GIVE_WAY, merged_selection
from worldmock.life.routing import require_recipients

_UNMET = "ACTIVITY_PRECONDITION_UNMET"


def _world(engine) -> tuple:
    """@brief 拒绝要保持不变的世界事实：余额、两人的库存与位置、联系人持有、订单与活动。"""
    agents = {key: (item.inventory, item.location_id) for key, item in engine.state.agents.items()}
    return (dict(engine.state.accounts), agents, engine.state.contact_holdings, dict(engine.state.deliveries),
            len(engine.state.activities))


def _energy(engine) -> dict[int, float]:
    """@brief 两人此刻的能量（kcal）。"""
    return {key: item.body.energy_kcal for key, item in engine.state.agents.items()}


def _give(engine, product: str, recipient: str):
    """@brief 把手边的一件持有物给 recipient 的请求。"""
    return routed((holding_name(engine, product), "consume", GIVE_WAY, "自己", recipient))


def test_giving_to_a_colocated_person_moves_one_portion_and_nobody_eats(engine_factory) -> None:
    """同处的同世界人物：每次移出 min(1, 持有)，记入对方同一地点的库存；瞬时、没有活动、两人的能量都不变；
    结果带给予的商品、份数与收件人，settled 句写「把<商品> <份数>给了<人>」。"""
    engine = open_world(engine_factory)
    food = food_product(engine)
    place(engine, 1, HOME, {food: 1.5})
    energy, activities = _energy(engine), len(engine.state.activities)
    first = act(engine, "give-one", _give(engine, food, PERSON_TARGET))
    assert first.status == "completed" and first.activity_id is None, (first.error_code, first.message)
    assert first.data["given"] == [{"product_id": food, "quantity": 1.0, "recipient_id": "agent:2",
                                    "location_id": HOME}]
    assert engine.state.agents[1].inventory[HOME][food] == 0.5
    assert engine.state.agents[2].inventory[HOME][food] == 1.0
    assert _energy(engine) == energy and len(engine.state.activities) == activities + 1  # 另一人开始等待
    second = act(engine, "give-rest", _give(engine, food, PERSON_TARGET))
    assert second.status == "completed" and second.data["given"][0]["quantity"] == 0.5
    assert engine.state.agents[1].inventory[HOME][food] == 0.0
    assert engine.state.agents[2].inventory[HOME][food] == 1.5
    observed = observe(engine, "after-give")
    call = [row for row in observed["progress_layer"]["events"] if row["type"] == "tool_call"][0]
    name = engine.rules.items[food].name
    assert call["arguments"]["to"] == [PERSON_TARGET] and call["arguments"]["from"] == ["自己"]
    assert call["result"]["details"]["given"] == [{"product": name, "quantity": "1 份", "recipient": "李凡"}]
    assert observed["progress_meta"][0]["lines"] == [f"10:00 把{name} 1 份给了李凡"]


def test_giving_to_a_person_who_left_is_refused_as_not_together(engine_factory) -> None:
    """观察时同处、同一轮里对方先出发了：给予以 ACTIVITY_PRECONDITION_UNMET（unmet=recipient_present）拒绝，原因
    「要和<人>在同一个地方才能给他」，对方在路上时下一步是等在一起再给；世界不变。"""
    engine = open_world(engine_factory)
    food = food_product(engine)
    place(engine, 2, HOME, {food: 1.0})
    observed = observe(engine, "leave-preview")
    office = next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == OFFICE)
    before = engine.state.agents[2].inventory
    _, results = submit_round(engine, {1: ("start_activity", start_request(targets=[office], actions=["travel"],
                                                                           ways=["步行"])),
                                       2: ("start_activity", _give(engine, food, "人物：陈曦"))}, "leave-and-give")
    refused = next(item for item in results if item.agent_id == 2)
    assert (refused.status, refused.error_code) == ("rejected", _UNMET)
    assert refused.data["reason"] == "要和陈曦在同一个地方才能给他"
    assert refused.data["next_step"] == "等和陈曦在一起时再给" and refused.data["unmet"] == ["recipient_present"]
    assert engine.state.agents[2].inventory == before


@pytest.mark.parametrize("contact,target,step", [
    (COLLEAGUE, COLLEAGUE_TARGET, "去张江高科技园区办公室（冻结场景锚点）"),
    ("contact-chenxi-oldfriend", "联系人：沈昊", "等和沈昊在一起时再给"),
], ids=["receive-location-elsewhere", "no-receive-location"])
def test_giving_to_a_contact_needs_being_at_their_receive_location(engine_factory, contact, target, step) -> None:
    """联系人只在其收货地点与本人同处：不在那里（或联系人没有收货地点）时给予被拒，原因同样是「要和<人>在同一个地方
    才能给他」，下一步去收货地点或等在一起；世界不变。"""
    engine = open_world(engine_factory)
    food = food_product(engine)
    place(engine, 1, HOME, {food: 1.0})
    before = _world(engine)
    refused = act(engine, "give-contact", _give(engine, food, target))
    salutation = target.split("：", 1)[1]
    assert (refused.status, refused.error_code) == ("rejected", _UNMET)
    assert refused.data["reason"] == f"要和{salutation}在同一个地方才能给他" and refused.data["next_step"] == step
    after = _world(engine)
    assert (after[0], after[1][1], after[2], after[3]) == (before[0], before[1][1], before[2], before[3])
    assert contact not in engine.state.contact_holdings


def test_giving_to_a_contact_at_their_receive_location_goes_into_contact_holdings(engine_factory) -> None:
    """本人在联系人的收货地点时给予成立：物品记入 contact_holdings，本人手边少一份，没有人的摄入增加。"""
    engine = open_world(engine_factory)
    food = food_product(engine)
    place(engine, 1, OFFICE, {food: 2.0})
    energy = _energy(engine)
    given = act(engine, "give-colleague", _give(engine, food, COLLEAGUE_TARGET))
    assert given.status == "completed", (given.error_code, given.message)
    assert engine.state.contact_holdings == {COLLEAGUE: {food: 1.0}}
    assert engine.state.agents[1].inventory[OFFICE][food] == 1.0 and _energy(engine) == energy


def test_giving_needs_the_item_at_hand_and_the_tool_layer_says_why(engine_factory) -> None:
    """他处的持有物不能给：工具层给予动作标不可用并写原因，受理以 PRODUCT_NOT_HELD 拒绝，原因一字不差；
    手边有的给予动作可用、数量写 min(1, 持有)。"""
    engine = open_world(engine_factory)
    food = food_product(engine)
    stage(engine, lambda state: setattr(state.agents[1], "inventory", {HOME: {food: 0.25}, OFFICE: {food: 1.0}}))
    observed = observe(engine, "give-layer")
    targets = {row["name"]: row for row in observed["tool_layer"]["targets"]}
    name = engine.rules.items[food].name
    here = next(item for item in targets[f"持有物：{name}"]["actions"] if item["way"] == GIVE_WAY)
    away = next(item for item in targets[f"持有物：{name}（张江高科技园区办公室（冻结场景锚点））"]["actions"]
                if item["way"] == GIVE_WAY)
    assert (here["action"], here["available"], here["quantity"]) == ("consume", True, "0.25 份")
    assert away["available"] is False and away["reason"] == "这件东西在张江高科技园区办公室（冻结场景锚点），只能在当地用"
    refused = act(engine, "give-away", routed((f"持有物：{name}（张江高科技园区办公室（冻结场景锚点））", "consume",
                                                GIVE_WAY, "自己", PERSON_TARGET)))
    assert refused.error_code == "PRODUCT_NOT_HELD" and refused.data["reason"] == away["reason"]


def test_recipient_that_is_not_visible_is_rejected_by_the_environment(engine_factory) -> None:
    """绕过宿主提交的去向写了本轮没有的人物：环境在整组准入之后以 RECIPIENT_NOT_FOUND 业务拒绝，世界不变。"""
    engine = open_world(engine_factory)
    food = food_product(engine)
    place(engine, 1, HOME, {food: 1.0})
    before = _world(engine)
    refused = act(engine, "give-nobody", _give(engine, food, "人物：不存在的人"))
    assert (refused.status, refused.error_code) == ("rejected", "RECIPIENT_NOT_FOUND")
    assert _world(engine)[:4] == before[:4]
    with pytest.raises(BusinessRuleError, match="RECIPIENT_NOT_FOUND"):
        require_recipients([{"to": "人物：不存在的人", "recipient_id": None}])
    require_recipients([{"to": None, "recipient_id": None}, {"to": "自己", "recipient_id": "agent:1"}])


def test_giving_is_instant_takes_no_slot_and_is_closed_in_restricted_rounds(engine_factory) -> None:
    """给予与持续活动同一请求：等待开始、给予当场完成，只占一个名额；给予不合成一餐、受限轮不开放（与下单一样
    归入 other）。"""
    engine = open_world(engine_factory)
    food = food_product(engine)
    place(engine, 1, HOME, {food: 1.0})
    both = routed(("自己", "interact", "等待", None, None),
                  (holding_name(engine, food), "consume", GIVE_WAY, "自己", PERSON_TARGET))
    result = act(engine, "wait-and-give", both)
    assert result.status == "in_progress" and result.message == "Activities started"
    assert [row.get("given") for row in result.data["activity_results"]][1][0]["recipient_id"] == "agent:2"
    assert result.data["given"][0]["quantity"] == 1.0 and len(result.data["activities"]) == 1
    assert activity_control.free_slots(engine.state, 1) == 1
    assert activity_control.new_activity_count(["consume", "consume"], [GIVE_WAY, GIVE_WAY]) == 0
    assert activity_control.new_activity_count(["consume", "consume"], ["食用", GIVE_WAY]) == 1
    assert not merged_selection(["consume", "consume"], [GIVE_WAY, GIVE_WAY])
    assert same_moment.request_kind("start_activity", ["consume"], [], frozenset(), [GIVE_WAY]) == "other"
    assert same_moment.request_kind("start_activity", ["consume"], [], frozenset(), ["食用"]) == "start"


def test_two_gives_in_one_request_hand_over_real_holdings_only(engine_factory) -> None:
    """同一请求把同一件持有物给两个人（去向不同才允许重复目标）：各自移出 min(1, 持有)，1.5 份分成 1 与 0.5，
    两人都没有摄入；消息写明是给予。"""
    engine = open_world(engine_factory)
    food = food_product(engine)
    place(engine, 1, OFFICE, {food: 1.5})
    place(engine, 2, OFFICE)
    name = holding_name(engine, food)
    energy = _energy(engine)
    result = act(engine, "give-two", routed((name, "consume", GIVE_WAY, "自己", PERSON_TARGET),
                                            (name, "consume", GIVE_WAY, "自己", COLLEAGUE_TARGET)))
    assert result.status == "completed" and result.message == "Items handed over", result.error_code
    assert engine.state.agents[2].inventory[OFFICE][food] == 1.0
    assert engine.state.contact_holdings[COLLEAGUE][food] == 0.5
    assert engine.state.agents[1].inventory[OFFICE][food] == 0.0 and _energy(engine) == energy


def _order_and_wait(engine, name: str, *items: tuple):
    """@brief 下单并开始等待（让时间可以推进），返回结果。"""
    return act(engine, name, routed(*items, ("自己", "interact", "等待", None, None)))


def test_direct_delivery_to_a_person_goes_into_their_holdings_and_nobody_eats(engine_factory) -> None:
    """远程下单直接送给同处的同世界人物：付款人是下单的人，订单收货人是对方，送达只入对方在其所在地点的库存；
    下单人没有这件东西，两人都没有摄入（没有吃的活动、能量没有增加）。"""
    engine = open_world(engine_factory)
    target, food = supply_target(observe(engine, "supply"))
    balance, energy = engine.state.accounts["agent:1"], _energy(engine)
    result = _order_and_wait(engine, "order-for-li", (target, "obtain", "远程下单", None, PERSON_TARGET))
    [order] = result.data["orders"]
    assert (order["agent_id"], order["recipient_id"], order["destination_id"]) == (1, "agent:2", HOME)
    assert engine.state.accounts["agent:1"] == balance - order["total_price_fen"]
    run_until(engine, lambda current: current.state.deliveries[order["order_id"]].status == "delivered")
    assert engine.state.agents[2].inventory[HOME][food] == 1.0
    assert all(food not in holdings for holdings in engine.state.agents[1].inventory.values())
    assert not [item for item in engine.state.activities.values() if item.kind.value == "consume"]
    assert all(_energy(engine)[key] <= energy[key] for key in energy)


def test_direct_delivery_to_a_contact_goes_to_their_receive_location(engine_factory) -> None:
    """远程下单送给联系人：送达地是联系人的收货地点，送达只入 contact_holdings；下单人的进度层送达记录写明收货人。"""
    engine = open_world(engine_factory)
    target, food = supply_target(observe(engine, "supply"))
    result = _order_and_wait(engine, "order-for-colleague", (target, "obtain", "远程下单", None, COLLEAGUE_TARGET))
    [order] = result.data["orders"]
    assert (order["recipient_id"], order["destination_id"], order["request_message_id"]) == (COLLEAGUE, OFFICE, None)
    run_until(engine, lambda current: current.state.deliveries[order["order_id"]].status == "delivered")
    assert engine.state.contact_holdings == {COLLEAGUE: {food: 1.0}}
    assert all(food not in holdings for holdings in engine.state.agents[1].inventory.values())
    records = [row for row in observe(engine, "delivered")["progress_layer"]["events"] if row["type"] == "delivery"]
    assert records[-1]["message"] == "已送到张江高科技园区办公室（冻结场景锚点），收货人李凡"


def test_direct_delivery_to_a_contact_who_cannot_receive_is_refused(engine_factory) -> None:
    """联系人没有收货地点：直接配送以 ACTIVITY_PRECONDITION_UNMET「暂时送不到<人>那里」拒绝，世界不变。"""
    engine = open_world(engine_factory)
    target, _ = supply_target(observe(engine, "supply"))
    before = _world(engine)
    refused = act(engine, "order-for-friend", routed((target, "obtain", "远程下单", None, "联系人：沈昊")))
    assert (refused.error_code, refused.data["reason"]) == (_UNMET, "暂时送不到沈昊那里")
    assert refused.data["next_step"] == "先送到自己这里，再当面给"
    assert _world(engine)[:4] == before[:4]


def test_same_product_for_two_recipients_splits_orders_by_destination(engine_factory) -> None:
    """同一请求里同一商品分给自己与联系人（收货地不同）拆成两笔整单；分给自己与同处的人（收货地相同）是一笔整单里
    的两个订单（去重键为供给与收货人）；各自只入自己的收货人。"""
    engine = open_world(engine_factory)
    target, food = supply_target(observe(engine, "supply"))
    split = act(engine, "two-places", routed((target, "obtain", "远程下单", None, "自己"),
                                             (target, "obtain", "远程下单", None, COLLEAGUE_TARGET)))
    assert split.status == "completed", (split.error_code, split.message)
    assert [(item["recipient_id"], item["destination_id"]) for item in split.data["orders"]] == [
        ("agent:1", HOME), (COLLEAGUE, OFFICE)]
    assert split.data["total_price_fen"] == sum(item["total_price_fen"] for item in split.data["orders"])
    deliveries = [item for item in engine.state.pending_events if item.kind.value == "delivery_arrival"]
    assert len(deliveries) == 2
    shared = act(engine, "one-place", routed((target, "obtain", "远程下单", None, "自己"),
                                             (target, "obtain", "远程下单", None, PERSON_TARGET),
                                             ), agent_id=1)
    assert [item["recipient_id"] for item in shared.data["orders"]] == ["agent:1", "agent:2"]
    event = next(item for item in engine.state.pending_events if item.kind.value == "delivery_arrival"
                 and shared.data["orders"][0]["order_id"] in item.data["order_ids"])
    assert event.data["order_ids"] == [item["order_id"] for item in shared.data["orders"]]
    orders = [*split.data["orders"], *shared.data["orders"]]
    run_until(engine, lambda current: all(current.state.deliveries[item["order_id"]].status == "delivered"
                                          for item in orders))
    assert engine.state.agents[1].inventory[HOME][food] == 2.0
    assert engine.state.agents[2].inventory[HOME][food] == 1.0
    assert engine.state.contact_holdings == {COLLEAGUE: {food: 1.0}}


def test_pickup_for_someone_else_needs_them_at_the_store(engine_factory) -> None:
    """到店买给同在店里的人即时入对方在店里的库存；买给不在店里的联系人以「暂时送不到<人>那里」拒绝。"""
    engine = open_world(engine_factory)
    place(engine, 1, MARKET)
    place(engine, 2, MARKET)
    observed = observe(engine, "at-market")
    row = next(row for row in observed["interaction_bindings"] if row["kind"] == "supply"
               and any(action["label"] == "到店" for action in row["actions"]))
    food = row["object_id"].removeprefix("supply-market-")
    handed = act(engine, "pickup-for-li", routed((row["object"], "obtain", "到店", None, PERSON_TARGET)))
    assert handed.status == "completed", (handed.error_code, handed.message)
    assert engine.state.agents[2].inventory[MARKET][food] == 1.0
    assert food not in engine.state.agents[1].inventory.get(MARKET, {})
    refused = act(engine, "pickup-for-colleague", routed((row["object"], "obtain", "到店", None, COLLEAGUE_TARGET)))
    assert (refused.error_code, refused.data["reason"]) == (_UNMET, "暂时送不到李凡那里")


def test_self_receipt_then_giving_transfers_exactly_once(engine_factory) -> None:
    """自己收货后再给予：订单只入自己的持有，给予只转出真实持有的量，东西只转移一次（总量守恒为 1）。"""
    engine = open_world(engine_factory)
    target, food = supply_target(observe(engine, "supply"))
    result = _order_and_wait(engine, "order-self", (target, "obtain", "远程下单", None, "自己"))
    [order] = result.data["orders"]
    assert order["recipient_id"] == "agent:1"
    run_until(engine, lambda current: current.state.deliveries[order["order_id"]].status == "delivered")
    assert engine.state.agents[1].inventory[HOME][food] == 1.0
    assert food not in engine.state.agents[2].inventory.get(HOME, {})
    run_until(engine, lambda current: 1 in current.scheduling_state().ready_agent_ids
              and not activity_control.pending_conclusions(current.state, 1))
    given = act(engine, "give-after-receipt", _give(engine, food, PERSON_TARGET))
    assert given.status == "completed", (given.error_code, given.message)
    assert engine.state.agents[1].inventory[HOME][food] == 0.0
    assert engine.state.agents[2].inventory[HOME][food] == 1.0
    run_until(engine, lambda current: 1 in current.scheduling_state().ready_agent_ids
              and not activity_control.pending_conclusions(current.state, 1))
    again = act(engine, "give-again", _give(engine, food, PERSON_TARGET))
    assert again.error_code == "ACTION_REQUEST_INVALID"  # 手边已经没有这件东西，不再是本轮的目标
    assert engine.state.agents[2].inventory[HOME][food] == 1.0
