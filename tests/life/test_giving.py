"""给予与收货人（docs/dev/SPEC-persona-prompt.md 第五章「来源与去向」，验收台账「来源与去向」）。

真实 LifeEngine 与 SQLite：给予瞬时、不占名额、不增加任何人的摄入，每件移出 min(1, 持有) 记入对方持有，要求同处；
收件人不存在、手边没有、不同处各有拒绝码，身边没有任何能给的人时工具层的给予动作标不可用、原因与拒绝一字不差；同世界
人物不在身边也是去向（给予被拒、配送送到其所在地点）；订单只有一个收货人，送达只入收货人的持有（同世界人物的库存或
联系人的 contact_holdings），付款人仍是下单的人；同一请求里同一商品可以分给两人，订单按（方式，收货地）拆分；自己收货
后再给予只转移一次；与同世界人物同名的场景联系人没有收货地点；远程下单送给别人却送不到时，收货地点不在配送范围与
配送路线缺失各一句原因（docs/dev/SPEC-dayrun-fidelity.md 5.3）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.life.conftest import start_request, submit_round
from tests.life.routing_fixtures import (COLLEAGUE, COLLEAGUE_TARGET, DUPLICATE_CONTACT, DUPLICATE_TARGET, HOME, MARKET,
                                         OFFICE, PERSON_TARGET, act, food_product, holding_name, observe, open_world,
                                         place, routed, run_until, stage, supply_target)
from tests.life.test_browse_and_hints import _block, _rescenario, _retarget
from worldmock.errors import BusinessRuleError
from worldmock.life import activity_control, same_moment
from worldmock.life.basket import delivery_expected_at
from worldmock.life.interaction_menu import GIVE_WAY, merged_selection
from worldmock.life.readable import clock_phrase
from worldmock.life.routing import require_recipients
from worldmock.life.scenario import load_scenario

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
    """观察时同处、同一轮里对方先出发了：身边再没有能给的人，给予以 ACTIVITY_PRECONDITION_UNMET
    （unmet=recipient_present）拒绝，原因「要和对方在同一个地方才能给」，下一步是等和要给的人在一起再给；世界不变。"""
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
    assert refused.data["reason"] == "要和对方在同一个地方才能给"
    assert refused.data["next_step"] == "等和要给的人在一起时再给" and refused.data["unmet"] == ["recipient_present"]
    assert engine.state.agents[2].inventory == before


def test_giving_to_a_same_world_person_elsewhere_is_refused_as_not_together(engine_factory) -> None:
    """同世界人物不在身边（不在工具层，只在私有绑定 person_bindings）也是去向：本人在王磊的收货地点、身边有能给的人时，
    给不在身边的李凡以 ACTIVITY_PRECONDITION_UNMET（unmet=recipient_present）拒绝，原因指名「要和李凡在同一个地方才能
    给他」，下一步去李凡所在的地点；世界不变（docs/dev/SPEC-persona-prompt.md 第五章「来源与去向」）。"""
    engine = open_world(engine_factory)
    food = food_product(engine)
    place(engine, 1, OFFICE, {food: 1.0})
    place(engine, 2, HOME)
    observed = observe(engine, "elsewhere")
    assert PERSON_TARGET not in {row["name"] for row in observed["tool_layer"]["targets"]}
    assert {"name": PERSON_TARGET, "person_id": "agent:2"} in observed["person_bindings"]
    before = _world(engine)
    refused = act(engine, "give-elsewhere", _give(engine, food, PERSON_TARGET))
    assert (refused.status, refused.error_code, refused.data["unmet"]) == ("rejected", _UNMET, ["recipient_present"])
    assert refused.data["reason"] == "要和李凡在同一个地方才能给他"
    assert refused.data["next_step"] == "去张江居所（冻结场景锚点）"
    assert _world(engine)[:4] == before[:4]


def _give_action(engine, name: str, product: str) -> dict:
    """@brief 本轮工具层里手边持有物的给予动作。"""
    targets = {row["name"]: row for row in observe(engine, name)["tool_layer"]["targets"]}
    return next(item for item in targets[holding_name(engine, product)]["actions"] if item["way"] == GIVE_WAY)


def test_give_is_listed_available_only_with_someone_to_hand_to(engine_factory) -> None:
    """给予动作的可用性与受理同一判定：手边有东西而身边没有任何能给的人（李凡在办公室，本人不在任何联系人的收货地点）
    时标不可用，原因与受理的拒绝一字不差；本人到了有李凡、也是王磊收货地点的办公室后标可用，给李凡即成立。"""
    engine = open_world(engine_factory)
    food = food_product(engine)
    place(engine, 1, HOME, {food: 2.0})
    place(engine, 2, OFFICE)
    alone = _give_action(engine, "alone", food)
    assert (alone["available"], alone["reason"]) == (False, "要和对方在同一个地方才能给")
    refused = act(engine, "give-alone", _give(engine, food, PERSON_TARGET))
    assert (refused.error_code, refused.data["reason"], refused.data["unmet"]) == (
        _UNMET, alone["reason"], ["recipient_present"])
    assert refused.data["next_step"] == "等和要给的人在一起时再给"
    place(engine, 1, OFFICE, {food: 2.0})
    together = _give_action(engine, "together", food)
    assert (together["available"], together["reason"]) == (True, None)
    given = act(engine, "give-together", _give(engine, food, PERSON_TARGET))
    assert given.status == "completed", (given.error_code, given.message)
    assert engine.state.agents[2].inventory[OFFICE][food] == 1.0


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
    """绕过宿主提交的去向写了本轮没有的人物：环境（在整组准入之前）以 RECIPIENT_NOT_FOUND 业务拒绝，世界不变。"""
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
    assert records[-1]["message"] == "已送到张江高科技园区办公室（冻结场景锚点），收货人王磊"


def test_a_recipient_written_without_the_prefix_is_resolved_to_that_person(engine_factory) -> None:
    """to 写收货人时按人物名称统一解析（docs/dev/SPEC-dayrun-fidelity.md 3.1）：只写「王磊」无损归一唯一就是「联系人：王磊」，
    宿主不纠错、环境受理并送到他的收货地点，受理结果 details.resolved 记 {written: 王磊, as: 联系人：王磊}；与同名的人物、
    联系人都对得上的「李凡」不唯一，不替人物挑一个，环境以 RECIPIENT_NOT_FOUND 拒绝。"""
    engine = open_world(engine_factory)
    target, _ = supply_target(observe(engine, "supply-bare-recipient"))
    result = act(engine, "order-for-bare-name", routed((target, "obtain", "远程下单", None, "王磊")))
    assert (result.status, result.error_code) == ("completed", None)
    assert [(order["recipient_id"], order["destination_id"]) for order in result.data["orders"]] == [(COLLEAGUE, OFFICE)]
    assert {"written": "王磊", "as": COLLEAGUE_TARGET} in result.data["resolved"]
    shared = act(engine, "order-for-shared-name", routed((target, "obtain", "远程下单", None, "李凡")))
    assert (shared.status, shared.error_code) == ("rejected", "RECIPIENT_NOT_FOUND")


def test_direct_delivery_to_a_same_world_person_elsewhere_goes_to_where_they_are(engine_factory) -> None:
    """远程下单送给不在身边（不在工具层）的同世界人物：收货地点是其所在地点，送达只入对方在那里的库存；付款人是下单的
    人，两人都没有摄入（没有吃的活动、能量没有增加）。"""
    engine = open_world(engine_factory)
    place(engine, 2, OFFICE)
    supplies = {item.supply_id: item for item in engine.scenario.supplies}
    row = next(row for row in observe(engine, "supply-elsewhere")["interaction_bindings"] if row["kind"] == "supply"
               and any(action["label"] == "远程下单" for action in row["actions"])
               and OFFICE in supplies[row["object_id"]].delivery_destination_ids)
    food = supplies[row["object_id"]].product_id
    balance, energy = engine.state.accounts["agent:1"], _energy(engine)
    result = _order_and_wait(engine, "order-for-li-elsewhere",
                             (row["object"], "obtain", "远程下单", None, PERSON_TARGET))
    [order] = result.data["orders"]
    assert (order["agent_id"], order["recipient_id"], order["destination_id"]) == (1, "agent:2", OFFICE)
    assert engine.state.accounts["agent:1"] == balance - order["total_price_fen"]
    run_until(engine, lambda current: current.state.deliveries[order["order_id"]].status == "delivered")
    assert engine.state.agents[2].inventory[OFFICE][food] == 1.0
    assert all(food not in holdings for holdings in engine.state.agents[1].inventory.values())
    assert not [item for item in engine.state.activities.values() if item.kind.value == "consume"]
    assert all(_energy(engine)[key] <= energy[key] for key in energy)


def test_scenario_contacts_that_are_same_world_persons_do_not_receive(engine_factory) -> None:
    """两份多人场景里与同世界人物同名的场景联系人没有收货地点（同世界人物按人物身份收货）：直接配送给通讯录里的
    「李凡」以「暂时送不到李凡那里」拒绝，世界不变，没有记入联系人持有。"""
    for name in ("multi-person.json", "multi-person-half-day.json"):
        scenario, _ = load_scenario(Path("scenarios") / name)
        people = {item.name for item in scenario.agents}
        duplicates = [contact for item in scenario.agents for contact in item.contacts if contact.salutation in people]
        assert len(duplicates) == 2 and all(contact.receive_location_id is None for contact in duplicates)
    engine = open_world(engine_factory)
    target, _ = supply_target(observe(engine, "supply"))
    before = _world(engine)
    refused = act(engine, "order-for-duplicate", routed((target, "obtain", "远程下单", None, DUPLICATE_TARGET)))
    assert (refused.error_code, refused.data["reason"]) == (_UNMET, "暂时送不到李凡那里")
    assert _world(engine)[:4] == before[:4] and DUPLICATE_CONTACT not in engine.state.contact_holdings


def test_direct_delivery_to_a_contact_who_cannot_receive_is_refused(engine_factory) -> None:
    """联系人没有收货地点：直接配送以 ACTIVITY_PRECONDITION_UNMET「暂时送不到<人>那里」拒绝，世界不变。"""
    engine = open_world(engine_factory)
    target, _ = supply_target(observe(engine, "supply"))
    before = _world(engine)
    refused = act(engine, "order-for-friend", routed((target, "obtain", "远程下单", None, "联系人：沈昊")))
    assert (refused.error_code, refused.data["reason"]) == (_UNMET, "暂时送不到沈昊那里")
    assert refused.data["next_step"] == "先送到自己这里，再当面给"
    assert _world(engine)[:4] == before[:4]


def _remote_supply(engine) -> dict:
    """@brief 本轮一个能远程下单的集市供给的私有绑定行（门店在集市）。"""
    return next(row for row in observe(engine, "remote-supply")["interaction_bindings"] if row["kind"] == "supply"
                and any(action["label"] == "远程下单" for action in row["actions"]))


def _order_for_colleague(engine, name: str, target: str):
    """@brief 远程下单一件商品送给同事联系人王磊（收货地点是办公室），返回结果。"""
    return act(engine, name, routed((target, "obtain", "远程下单", None, COLLEAGUE_TARGET)))


def test_remote_order_for_someone_outside_the_delivery_area_is_a_destination_refusal(engine_factory) -> None:
    """王磊的收货地点（办公室）不在该供给的可配送目的地：以 ACTIVITY_PRECONDITION_UNMET（unmet=delivery_destination）拒绝，
    原因「王磊那里不在这家店的配送范围内」（本人送达地的原因同一说法，「这里」换成收货人那里），不再是笼统的「暂时送不到
    王磊那里」；下一步先送到自己这里再当面给，世界不变。"""
    engine = open_world(engine_factory)
    row = _remote_supply(engine)
    _retarget(engine, row["object_id"], (HOME,))
    before = _world(engine)
    refused = _order_for_colleague(engine, "order-outside-area", row["object"])
    assert (refused.error_code, refused.data["reason"], refused.data["unmet"]) == (
        _UNMET, "王磊那里不在这家店的配送范围内", ["delivery_destination"])
    assert refused.data["next_step"] == "先送到自己这里，再当面给"
    assert _world(engine)[:4] == before[:4]


def test_remote_order_for_someone_without_a_delivery_route_is_a_route_refusal(engine_factory) -> None:
    """王磊的收货地点在配送范围内，门店到办公室的驾车路线却不可达：unmet=delivery_route，原因「这家店到王磊那里没有能走的
    配送路线」，与送达地不对的原因不同；下一步先送到自己这里再当面给，世界不变；路线恢复后同一请求照常成单。"""
    engine = open_world(engine_factory)
    row = _remote_supply(engine)
    _retarget(engine, row["object_id"], (HOME, OFFICE))
    routes = engine.scenario.routes
    _block(engine, lambda route: (route.origin_id, route.destination_id, route.mode.value) == (MARKET, OFFICE,
                                                                                            "driving"))
    before = _world(engine)
    refused = _order_for_colleague(engine, "order-without-route", row["object"])
    assert (refused.error_code, refused.data["reason"], refused.data["unmet"]) == (
        _UNMET, "这家店到王磊那里没有能走的配送路线", ["delivery_route"])
    assert refused.data["next_step"] == "先送到自己这里，再当面给"
    assert _world(engine)[:4] == before[:4]
    _rescenario(engine, routes=routes)
    accepted = _order_for_colleague(engine, "order-with-route", row["object"])
    assert accepted.status == "completed", (accepted.error_code, accepted.message)
    assert [(order["recipient_id"], order["destination_id"]) for order in accepted.data["orders"]] == [
        (COLLEAGUE, OFFICE)]


@pytest.mark.parametrize(("destinations", "blocked", "unmet", "reason"), [
    ((HOME,), False, "delivery_destination", "王磊那里不在这家店的配送范围内"),
    ((HOME, OFFICE), True, "delivery_route", "这家店到王磊那里没有能走的配送路线"),
    ((HOME,), True, "delivery_destination", "王磊那里不在这家店的配送范围内")],
    ids=["destination", "route", "destination-before-route"])
def test_named_remote_order_for_someone_it_cannot_reach_says_why_like_a_listed_one(
        engine_factory, destinations, blocked, unmet, reason) -> None:
    """指名购买（商品名配门店名，本轮没有列出）远程下单送给王磊，这家店却送不到他的收货地点（办公室）：分段匹配 M3 以
    ACTIVITY_PRECONDITION_UNMET 拒绝，原因与列出的供给送给别人同一说法（解析出的供给交给 order_routing.undeliverable，
    docs/dev/SPEC-dayrun-fidelity.md 3.2、5.3）：不在配送范围为 delivery_destination，路线缺失为 delivery_route，两者都不成立时
    与本人送达地（admission._delivery_refusal）同一顺序先报配送范围；下一步先送到自己这里再当面给，逐项明细的候选不含这家店，
    世界不变。"""
    engine = open_world(engine_factory)
    row = _remote_supply(engine)
    store, product = row["object"].split("出售的", 1)
    _retarget(engine, row["object_id"], destinations)
    if blocked:
        _block(engine, lambda route: (route.origin_id, route.destination_id, route.mode.value) == (
            MARKET, OFFICE, "driving"))
    before = _world(engine)
    refused = act(engine, f"named-{unmet}", routed((product, "obtain", "远程下单", store, COLLEAGUE_TARGET)))
    assert (refused.error_code, refused.data["reason"], refused.data["unmet"]) == (
        _UNMET, f"「{product}」：{reason}", [unmet])
    assert refused.data["next_step"] == "先送到自己这里，再当面给"
    [item] = refused.data["items"]
    assert (item["field"], item["written"]) == ("targets", product)
    assert row["object"] not in [candidate["target"] for candidate in item["candidates"]]
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
    """到店买给同在店里的人即时入对方在店里的库存；买给不在店里的联系人以「暂时送不到<人>那里」拒绝——到店与配送范围无关，
    这家店送不到他的收货地点时也是这一句，不按远程下单的两种配送原因细分（order_routing.undeliverable 只对远程下单细分）。"""
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
    assert (refused.error_code, refused.data["reason"]) == (_UNMET, "暂时送不到王磊那里")
    _retarget(engine, row["object_id"], (HOME,))
    outside = act(engine, "pickup-outside-area", routed((row["object"], "obtain", "到店", None, COLLEAGUE_TARGET)))
    assert (outside.error_code, outside.data["reason"], outside.data["unmet"]) == (
        _UNMET, "暂时送不到王磊那里", ["delivery_destination"])


def test_system_purchase_for_someone_goes_in_store_only_when_they_are_there(engine_factory) -> None:
    """「购买」由系统定到店或远程下单，定下的方式要交得到收货人手上（docs/dev/SPEC-dayrun-fidelity.md 3.5「收敛」第 2 条）：
    人站在出售它的集市里，同在集市的李凡按到店即时入他在集市的库存；收货地点是办公室的王磊不在店里，改按远程下单送到办公室
    （不再定为到店、再在结算时以「暂时送不到王磊那里」拒绝），受理说明写明定下的方式；显式写到店照旧由结算拒绝。"""
    engine = open_world(engine_factory)
    place(engine, 1, MARKET)
    place(engine, 2, MARKET)
    row = _remote_supply(engine)
    label, food = row["object"], row["object_id"].removeprefix("supply-market-")
    for_li = act(engine, "system-for-li", routed((label, "obtain", "购买", None, PERSON_TARGET)))
    assert for_li.status == "completed", (for_li.error_code, for_li.message)
    [handed] = for_li.data["orders"]
    assert (handed["fulfillment"], handed["recipient_id"], handed["location_id"]) == ("pickup", "agent:2", MARKET)
    assert engine.state.agents[2].inventory[MARKET][food] == 1.0
    assert for_li.data["notes"] == [f"「{label}」「购买」定为到店：{label}"]
    for_colleague = act(engine, "system-for-colleague", routed((label, "obtain", "购买", None, COLLEAGUE_TARGET)))
    assert for_colleague.status == "completed", (for_colleague.error_code, for_colleague.message)
    [sent] = for_colleague.data["orders"]
    assert (sent["fulfillment"], sent["recipient_id"], sent["destination_id"]) == ("delivery", COLLEAGUE, OFFICE)
    moment = clock_phrase(sent["expected_at"], sent["created_at"], engine.scenario.timezone)
    assert for_colleague.data["notes"] == [f"「{label}」「购买」定为远程下单：{label}（预计 {moment} 送到）"]
    chosen = act(engine, "pickup-chosen-for-colleague", routed((label, "obtain", "到店", None, COLLEAGUE_TARGET)))
    assert (chosen.error_code, chosen.data["reason"]) == (_UNMET, "暂时送不到王磊那里")


@pytest.mark.parametrize("where", [MARKET, HOME])
def test_system_purchase_for_someone_it_cannot_reach_says_to_buy_for_oneself_first(engine_factory, where) -> None:
    """「购买」送给王磊，集市这家店却送不到他的收货地点（办公室），他也不在店里（东西只有他在店里时才能在店里交给他）：人在
    集市还是在家，都以 ACTIVITY_PRECONDITION_UNMET 拒绝，原因与远程下单送给他同一说法「王磊那里不在这家店的配送范围内」，
    下一步「先送到自己这里，再当面给」——不说人不在出售它的门店里，也不指向人已经在、或去了也交不到他手上的门店；候选里没有
    交不到他手上的到店，世界不变（docs/dev/SPEC-dayrun-fidelity.md 3.5「收敛」第 2 条）。"""
    engine = open_world(engine_factory)
    row = _remote_supply(engine)
    _retarget(engine, row["object_id"], (HOME,))
    place(engine, 1, where)
    before = _world(engine)
    refused = act(engine, f"system-unreachable-{where}", routed((row["object"], "obtain", "购买", None,
                                                                   COLLEAGUE_TARGET)))
    assert (refused.error_code, refused.data["reason"], refused.data["unmet"]) == (
        _UNMET, f"「{row['object']}」：王磊那里不在这家店的配送范围内", ["delivery_destination"])
    assert refused.data["next_step"] == "先送到自己这里，再当面给"
    [item] = refused.data["items"]
    assert (item["field"], item["written"], item["candidates"]) == ("targets", row["object"], [])
    assert _world(engine)[:4] == before[:4]


def test_system_purchase_for_someone_of_goods_sold_only_in_store_cannot_reach_them(engine_factory) -> None:
    """「购买」送给不在店里的王磊，而范围内的供给都只能到店（菜饭骨头汤的鸡汤面）：东西只有他也在店里时才能在店里交给他，又
    送不出去——与到店买给不在店里的人同一说法「暂时送不到王磊那里」，下一步先送到自己这里再当面给；不按配送范围说「王磊那里
    不在这家店的配送范围内」（这家店本来就不配送）。世界不变（docs/dev/SPEC-dayrun-fidelity.md 3.5「收敛」第 2 条）。"""
    engine = open_world(engine_factory)
    store = next(item.name for item in engine.scenario.locations if item.location_id == "poi-3538660453")
    before = _world(engine)
    refused = act(engine, "system-pickup-only-for-colleague", routed(("鸡汤面", "obtain", "购买", store,
                                                                      COLLEAGUE_TARGET)))
    assert (refused.error_code, refused.data["reason"], refused.data["unmet"]) == (
        _UNMET, "「鸡汤面」：暂时送不到王磊那里", ["delivery_destination"])
    assert refused.data["next_step"] == "先送到自己这里，再当面给"
    assert _world(engine)[:4] == before[:4]


def test_a_pickup_candidate_for_someone_not_at_the_store_is_not_ranked_as_feasible(engine_factory) -> None:
    """候选此刻可行的在前（3.1 第 3 条），送给别人的到店按东西交不交得到收货人手上判定（purchase_offers.serves，与整单结算同一
    判定）：人在集市、王磊不在，把纯牛奶写错成「纯牛奶加冰」到店送给王磊，以 SUPPLY_NOT_FOUND 拒绝；三家的纯牛奶此刻都交不到
    他手上，候选按名称相似度与标签排序，集市这家不因人站在店里就排到最前。"""
    engine = open_world(engine_factory)
    place(engine, 1, MARKET)
    products = {item.product_id: item.name for item in engine.scenario.products}
    places = {item.location_id: item.name for item in engine.scenario.locations}
    milk = sorted(f"{places[item.location_id]}出售的纯牛奶" for item in engine.scenario.supplies
                  if products[item.product_id] == "纯牛奶")
    assert len(milk) == 3 and milk[0] != f"{places[MARKET]}出售的纯牛奶"
    refused = act(engine, "pickup-typo-for-colleague", routed(("纯牛奶加冰", "obtain", "到店", None, COLLEAGUE_TARGET)))
    assert (refused.status, refused.error_code) == ("rejected", "SUPPLY_NOT_FOUND")
    [item] = refused.data["items"]
    assert [row["target"] for row in item["candidates"]] == milk


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


def test_named_purchase_takes_from_as_the_store_and_the_recipient_place_as_destination(engine_factory) -> None:
    """指名购买（本轮没有列出的名称）按收货人的收货地点找能送到的门店、from 限定门店：送给联系人时多店同名按最快选定一家
    （到其收货地点的预计送达最早，docs/dev/SPEC-dayrun-fidelity.md 3.5「收敛」第 5 条），不再报歧义；写上门店名后解析为
    那家店、订单收货人是联系人、送到其收货地点；收货人没有收货地点时「暂时送不到<人>那里」。"""
    engine = open_world(engine_factory)
    milk = [item for item in engine.scenario.supplies if engine.rules.items[item.product_id].name == "纯牛奶"
            and OFFICE in item.delivery_destination_ids]
    stores = {item.supply_id: next(place.name for place in engine.scenario.locations
                                   if place.location_id == item.location_id) for item in milk}
    assert len(set(stores.values())) >= 2
    now = engine.state.current_time
    fastest = min(milk, key=lambda item: (delivery_expected_at(engine.semantics, item, OFFICE, now),
                                          item.location_id))
    chosen = act(engine, "milk-chosen", routed(("纯牛奶", "obtain", "远程下单", None, COLLEAGUE_TARGET)))
    assert chosen.status == "completed", (chosen.error_code, chosen.message)
    assert [(order["supply_id"], order["destination_id"]) for order in chosen.data["orders"]] == [
        (fastest.supply_id, OFFICE)]
    assert "按最快选了" in chosen.data["notes"][0]
    supply_id, store = next(iter(stores.items()))
    named = act(engine, "milk-from-store", routed(("纯牛奶", "obtain", "远程下单", store, COLLEAGUE_TARGET)))
    assert named.status == "completed", (named.error_code, named.message)
    [order] = named.data["orders"]
    assert (order["supply_id"], order["recipient_id"], order["destination_id"]) == (supply_id, COLLEAGUE, OFFICE)
    refused = act(engine, "milk-to-friend", routed(("纯牛奶", "obtain", "远程下单", store, "联系人：沈昊")))
    assert (refused.error_code, refused.data["reason"]) == (_UNMET, "暂时送不到沈昊那里")
