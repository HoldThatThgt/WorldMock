"""指名购买（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「指名购买」、裁决 #19 与 #26；docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-指名购买from-是约束不是语法 、3.5）。

真实 LifeEngine 与 SQLite 上：被轮转隐藏但存在的商品按标签或唯一商品名可买，按当前报价在同一秒结算，不查可见凭据；
不存在的名称以 SUPPLY_NOT_FOUND 拒绝，逐项明细 details.items 的候选是至多 3 个确定性的近似标签（此刻能送到的在前），写成
可照填的请求（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第四章、3.1），不换成别的商品；多家店都能服务的同名商品按请求级 prefer
选定一家并写进 details.notes，不再以 SUPPLY_NAME_AMBIGUOUS 拒绝（3.5「收敛」第 5 条）；完整标签又写了另一家门店（from）以
SUPPLY_SOURCE_MISMATCH 拒绝、候选就是这个标签，写的正是标签里的门店按冗余受理并写一句说明（3.2（b））；分段匹配按所写方式与
此刻能不能服务依次报码（M2、M3）。宿主本地校验对 obtain（到店、远程下单、「购买」）放行不在本轮对象中的名称（named_kind 为
supply），其余动词写的名称不是任何对象时是协议错误（NAME_NOT_FOUND），在本轮纠正。
"""

from __future__ import annotations

import copy
import logging
import os

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest  # noqa: E402
from agentsociety2.society import DirectToolCall  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from tests.integration.test_life_agent_protocol import _context, _observations  # noqa: E402
from tests.life.conftest import context, last_result, stage_day_plan, start_arguments, start_request  # noqa: E402
from tests.life.routing_fixtures import place, stage  # noqa: E402
from tests.life.test_browse_and_hints import _retarget  # noqa: E402
from tests.life.test_commerce import _browse, _stage_open  # noqa: E402
from worldmock.errors import BusinessRuleError  # noqa: E402
from worldmock.life import projection  # noqa: E402
from worldmock.life.interaction_menu import NAMED_SUPPLY_KIND, resolve_interaction_menu
from tests.life.conftest import select_start, start_arguments  # noqa: E402
from worldmock.life.models import StartActivityRequest  # noqa: E402
from worldmock.life import named_purchase  # noqa: E402
from worldmock.life.basket import delivery_expected_at  # noqa: E402
from worldmock.life.named_purchase import (PurchaseAsk, near_labels, resolve_named_rows,  # noqa: E402
                                           resolve_named_supply, supply_label)
from worldmock.life.purchase_offers import landed_price  # noqa: E402
from worldmock.life.purchase_offers import choose, offers, order_key  # noqa: E402
from worldmock.life.readable import clock_phrase, money_phrase  # noqa: E402
from worldmock.life.storefront_state import current_quote  # noqa: E402
from worldmock.life.tool_layer import LayerContext  # noqa: E402

_MILK = "纯牛奶"


def _layer_context(engine) -> LayerContext:
    """引擎当前世界的只读切片（指名购买的解析只读世界、场景与语义）。"""
    agent = engine.context.state.agents[1]
    return LayerContext(state=engine.context.state, scenario=engine.context.scenario, rules=engine.context.rules, semantics=engine.context.semantics,
                        agent_id=1, names={"sessions": {}, "activities": {}}, bindings=[],
                        menu=resolve_interaction_menu(engine.context.scenario, 1, agent.location_id))


def _obtain(target: str, way: str = "远程下单", prefer: str = "最快拿到") -> StartActivityRequest:
    """对一个名称选择取得（obtain）及其方式的请求（prefer 为请求级偏好，缺省 null）。"""
    return start_request(targets=[target], actions=["obtain"], ways=[way], prefer=prefer)


def _round(engine, name: str, request):
    """单人物按真实顺序观察、登记并结算一轮 start_activity，返回（本轮观察，结果）。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", "start_activity", request))
    return observed, engine.commit_decision_round(name)[0]


def _world(engine) -> tuple:
    """拒绝时必须不变的世界事实：余额、库存与订单。"""
    return dict(engine.context.state.accounts), dict(engine.context.state.supply_stock), dict(engine.context.state.deliveries)


def _browsed(engine) -> set[str]:
    """营业时段浏览一次商品目录后，本轮观察列出的对象名称（轮转只呈现一部分供给）。"""
    _stage_open(engine)
    stage_day_plan(engine)
    _browse(engine)
    return {row["object"] for row in engine.observe(context(engine, "listed"))["interaction_bindings"]}


def _hidden(engine, listed: set[str], unique_name: bool):
    """一条存在、能送到家、没有被本轮列出也没有可见凭据的供给（unique_name 时其商品名在场景供给中唯一）。"""
    context_ = _layer_context(engine)
    names = [next(item.name for item in engine.context.scenario.products if item.product_id == supply.product_id)
             for supply in engine.context.scenario.supplies]
    visible = set(projection.visible_supplies(engine.context.state, engine.context.state.agents[1], engine.context.scenario))
    return next(supply for supply, name in zip(engine.context.scenario.supplies, names)
                if supply_label(context_, supply) not in listed and supply.supply_id not in visible
                and "delivery" in supply.fulfillment_modes and "home" in supply.delivery_destination_ids
                and (not unique_name or resolve_named_supply(context_, PurchaseAsk(
                    name, "远程下单", None, "home", "最快拿到"))["object_id"] == supply.supply_id))


@pytest.mark.parametrize("by", ["label", "product"])
def test_rotation_hidden_supply_is_bought_by_name_at_the_current_quote(engine_factory, by: str) -> None:
    """浏览后被轮转隐藏、没有可见凭据的供给按标签「<门店>出售的<商品>」或唯一商品名远程下单：按当前报价同一秒结算，
    付款、库存与订单都是这条供给；结果进入下一次观察的公开结果。"""
    engine = engine_factory()
    listed = _browsed(engine)
    supply = _hidden(engine, listed, unique_name=by == "product")
    product = engine.context.rules.items[supply.product_id]
    name = supply_label(_layer_context(engine), supply) if by == "label" else product.name
    assert name not in listed
    quote = current_quote(engine.context.state, supply.supply_id)
    price = engine.context.semantics.purchase_amount(supply.product_id, quote.price_fen, 1)
    balance, stock = engine.context.state.accounts["agent:1"], engine.context.state.supply_stock[supply.supply_id]
    _, result = _round(engine, f"named-{by}", _obtain(name))
    assert (result.status, result.error_code) == ("completed", None)
    [order] = result.data["orders"]
    assert (order["supply_id"], order["product_id"], order["price_version"], order["total_price_fen"]) == (
        supply.supply_id, supply.product_id, quote.price_version, price)
    assert (order["fulfillment"], order["destination_id"], order["created_at"]) == (
        "delivery", "home", engine.context.state.current_time)
    assert engine.context.state.accounts["agent:1"] == balance - price
    assert engine.context.state.supply_stock[supply.supply_id] == stock - 1
    [shown] = last_result(engine.observe(context(engine, f"named-{by}-seen")))["details"]["purchases"]
    assert (shown["product"], shown["paid"]) == (product.name, money_phrase(price)) and "way" not in shown


def test_unknown_name_is_rejected_with_near_candidates_and_nothing_is_substituted(engine_factory) -> None:
    """不存在的名称以 SUPPLY_NOT_FOUND 拒绝：逐项明细的候选是至多 3 个确定性的近似标签（名称相近的真实供给），各写成
    可照填的请求 {target, action, way, from, to}；原因、改法与下一步可读，message 就是原因；不自动换成近似商品，余额、
    库存与订单都不变。"""
    engine = engine_factory()
    _browsed(engine)
    before = _world(engine)
    name = f"{_MILK}加冰"
    _, result = _round(engine, "unknown", _obtain(name))
    assert (result.status, result.error_code) == ("rejected", "SUPPLY_NOT_FOUND")
    labels = {supply_label(_layer_context(engine), supply) for supply in engine.context.scenario.supplies}
    [item] = result.data["items"]
    candidates = [row["arguments"]["items"][0]["target"] for row in item["candidates"]]
    assert (item["path"][5], item["path"][-1], item["written"], item["error_code"]) == (0, "target", name, "SUPPLY_NOT_FOUND")
    assert 1 <= len(candidates) <= 3 and set(candidates) <= labels
    assert candidates == near_labels(_layer_context(engine), name, "delivery", "home")
    assert all(_MILK in label for label in candidates)
    assert all(row["tool"] == "start_activity" and row["arguments"]["prefer"] == "最快拿到"
               and row["arguments"]["items"][0]["from"] is None and row["arguments"]["items"][0]["to"] is None
               and row["arguments"]["items"][0]["way"] in ("到店", "远程下单") for row in item["candidates"])
    assert _world(engine) == before
    details = last_result(engine.observe(context(engine, "unknown-seen")))["details"]
    assert details["reason"] and details["fix"] and details["items"] == [item]
    assert result.message == details["reason"]


def _home_supply(engine):
    """一条能送到家的场景供给（按名称购买的对象，不必出现在本轮）。"""
    return next(supply for supply in engine.context.scenario.supplies
                if "delivery" in supply.fulfillment_modes and "home" in supply.delivery_destination_ids)


def test_named_purchase_at_a_closed_store_is_a_business_rejection_and_nothing_is_substituted(engine_factory) -> None:
    """凌晨门店打烊时按标签指名远程下单：名称照常解析到这条供给，结算照常检查营业，以 MERCHANT_CLOSED 业务拒绝；不换成
    别的商品或别的门店，余额、库存与订单都不变，拒绝原因进入下一次观察的公开结果（第五章「指名购买」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    supply = _home_supply(engine)
    label = supply_label(_layer_context(engine), supply)
    assert label not in {row["object"] for row in engine.observe(context(engine, "closed-listed"))[
        "interaction_bindings"]}
    before = _world(engine)
    _, result = _round(engine, "closed", _obtain(label))
    assert (result.status, result.error_code) == ("rejected", "MERCHANT_CLOSED")
    assert "orders" not in result.data and _world(engine) == before
    details = last_result(engine.observe(context(engine, "closed-seen")))["details"]
    assert details["reason"] and details["items"][0]["path"][-3:] == ["items", 0, "target"]


def test_named_purchase_beyond_the_balance_is_a_business_rejection_and_nothing_is_substituted(engine_factory) -> None:
    """营业时段按标签指名远程下单、余额不够这一份：以 INSUFFICIENT_FUNDS 业务拒绝，不换成更便宜的商品，余额、库存与订单
    都不变（第五章「指名购买」：价格、余额、营业与库存照常检查，不自动换成别的商品）。"""
    engine = engine_factory(mutate=lambda document: document["agents"][0].update(initial_balance_fen=100))
    _stage_open(engine)
    stage_day_plan(engine)
    supply = _home_supply(engine)
    price = engine.context.semantics.purchase_amount(supply.product_id,
                                             current_quote(engine.context.state, supply.supply_id).price_fen, 1)
    assert price > engine.context.state.accounts["agent:1"] == 100
    before = _world(engine)
    _, result = _round(engine, "broke", _obtain(supply_label(_layer_context(engine), supply)))
    assert (result.status, result.error_code) == ("rejected", "INSUFFICIENT_FUNDS")
    assert "orders" not in result.data and _world(engine) == before
    details = last_result(engine.observe(context(engine, "broke-seen")))["details"]
    assert details["reason"] and details["items"][0]["path"][-3:] == ["items", 0, "target"]


def _milk(engine) -> list:
    """能送到家的同名商品纯牛奶在各店的供给（集市、好德、农工商超市）。"""
    return [supply for supply in engine.context.scenario.supplies
            if engine.context.rules.items[supply.product_id].name == _MILK and "home" in supply.delivery_destination_ids]


def _store(layer, supply) -> str:
    """供给所在门店的显示名（完整标签「<门店>出售的<商品>」的门店部分）。"""
    return supply_label(layer, supply).removesuffix(f"出售的{_MILK}")


def test_same_name_in_several_stores_is_chosen_by_prefer_unless_a_store_is_named(engine_factory) -> None:
    """多家店都能送到家的同名商品（纯牛奶）：from 为 null、prefer 为 null 时按最快选定一家（预计送达最早，并列时比到手价与
    门店标识），不再以 SUPPLY_NAME_AMBIGUOUS 拒绝；details.notes 写明有几处能买、按什么选了哪家（完整标签）与预计送达
    （docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-更简单的购买请求裁决-s10 「收敛」第 5 条）。写成标签或以 from 限定门店时解析为那家店的供给。"""
    engine = engine_factory()
    _browsed(engine)
    layer = _layer_context(engine)
    milk, now = _milk(engine), engine.context.state.current_time
    assert len({supply.location_id for supply in milk}) >= 2
    arrival = {supply.supply_id: delivery_expected_at(engine.context.semantics, supply, "home", now) for supply in milk}
    fastest = min(milk, key=lambda supply: (arrival[supply.supply_id], landed_price(layer, supply),
                                            supply.location_id))
    _, result = _round(engine, "several", _obtain(_MILK))
    assert (result.status, result.error_code) == ("completed", None)
    [order] = result.data["orders"]
    assert (order["supply_id"], order["expected_at"], order["channel_id"]) == (
        fastest.supply_id, arrival[fastest.supply_id], None)
    moment = clock_phrase(arrival[fastest.supply_id], now, engine.context.scenario.timezone)
    assert result.data["notes"] == [f"「{_MILK}」有 {len(milk)} 处都能远程下单，按最快拿到选了{supply_label(layer, fastest)}"
                                    f"（预计 {moment} 送到）"]
    assert result.data["resolved"] == [{"written": _MILK, "as": supply_label(layer, fastest)}]
    layer = _layer_context(engine)
    for supply in milk:
        assert resolve_named_supply(layer, PurchaseAsk(_MILK, "远程下单", _store(layer, supply), "home", prefer="最快拿到"))[
            "object_id"] == supply.supply_id
        assert resolve_named_supply(layer, PurchaseAsk(supply_label(layer, supply), "远程下单", None, "home", prefer="最快拿到"))[
            "object_id"] == supply.supply_id


def test_several_servable_matches_follow_prefer_and_never_report_ambiguity(engine_factory) -> None:
    """同一件同名商品（纯牛奶，from 为 null）：prefer=cheapest 选到手价最低的一家，prefer=fastest 选预计送达最早的一家，两者
    在这个世界里不是同一家店；受理说明写明按什么选的，cheapest 另写到手价；两次都不以 SUPPLY_NAME_AMBIGUOUS 拒绝
    （docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-更简单的购买请求裁决-s10  验收「prefer 两个取值各一例」）。"""
    engine = engine_factory()
    _browsed(engine)
    layer = _layer_context(engine)
    milk, now = _milk(engine), engine.context.state.current_time
    cheapest = min(milk, key=lambda supply: (landed_price(layer, supply), supply.location_id))
    fastest = min(milk, key=lambda supply: (delivery_expected_at(engine.context.semantics, supply, "home", now),
                                            landed_price(layer, supply), supply.location_id))
    assert cheapest.location_id != fastest.location_id
    _, cheap = _round(engine, "cheapest", _obtain(_MILK, prefer="到手价最低"))
    _, quick = _round(engine, "fastest", _obtain(_MILK, prefer="最快拿到"))
    assert [order["supply_id"] for order in cheap.data["orders"]] == [cheapest.supply_id]
    assert [order["supply_id"] for order in quick.data["orders"]] == [fastest.supply_id]
    price = money_phrase(landed_price(layer, cheapest))
    [note] = cheap.data["notes"]
    assert note.startswith(f"「{_MILK}」有 {len(milk)} 处都能远程下单，按到手价最低选了{supply_label(layer, cheapest)}")
    assert note.endswith(f"送到，到手价 {price}）")
    assert "按最快拿到选了" in quick.data["notes"][0] and "到手价" not in quick.data["notes"][0]
    assert {cheap.error_code, quick.error_code} == {None}


def test_a_store_whose_price_is_unknown_is_never_the_cheapest(engine_factory) -> None:
    """按最便宜选定时到手价未知的排在最后（3.5「收敛」第 5 条，purchase_offers.order_key）：几家都能送到家的纯牛奶里，最便宜
    那家的报价此刻失效（到手价未知），prefer=cheapest 选报价已知的里最便宜的一家并照常成交，不选报价未知的那家。"""
    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    layer = _layer_context(engine)
    ranked = sorted(_milk(engine), key=lambda supply: (landed_price(layer, supply), supply.location_id))

    def expire(state) -> None:
        """@brief 最便宜那家的报价在此刻失效。"""
        state.supply_quotes[ranked[0].supply_id].effective_to = state.current_time
    stage(engine, expire)
    assert landed_price(_layer_context(engine), ranked[0]) is None
    _, result = _round(engine, "cheapest-known", _obtain(_MILK, prefer="到手价最低"))
    assert (result.status, result.error_code) == ("completed", None)
    assert [order["supply_id"] for order in result.data["orders"]] == [ranked[1].supply_id]


@pytest.mark.parametrize("prefer", ["最快", "fastest", None])
def test_a_preference_outside_the_declared_pair_is_refused_and_no_store_is_chosen(engine_factory, caplog,
                                                                                 prefer) -> None:
    """请求级偏好只有「最快拿到」「到手价最低」两个取值（3.5「收敛」第 5 条，请求模型的闭集）：几家都能送到家的纯牛奶要按
    偏好选定一家时，闭集之外的偏好（旧写法、英文或 null）不按缺省的最快处理——指名购买的解析以 ValueError 明确失败并写英文
    日志，不选定任何一家；排序键与选定函数直接调用同样拒绝，闭集内的两个取值照常给出各自的键。"""
    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    layer = _layer_context(engine)
    way, served = offers(layer, _milk(engine), "远程下单", "home")
    assert way == "远程下单" and len(served) >= 2
    message = "purchase preference must be 最快拿到 or 到手价最低"
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError, match=message):
            resolve_named_supply(layer, PurchaseAsk(_MILK, "远程下单", None, "home", prefer=prefer))
    assert "Purchase preference is outside the declared set" in caplog.text
    for refused in (lambda: order_key(served[0], prefer), lambda: choose(served, prefer)):
        with pytest.raises(ValueError, match=message):
            refused()
    first = served[0]
    tail = (first.price, first.supply.location_id, first.supply.supply_id)
    assert order_key(first, "到手价最低") == tail and order_key(first, "最快拿到") == (first.speed, *tail)
    chosen = resolve_named_supply(layer, PurchaseAsk(_MILK, "远程下单", None, "home", prefer="最快拿到"))
    assert chosen["object_id"] == choose(served, "最快拿到").supply.supply_id


def test_full_label_with_another_store_in_from_is_a_source_mismatch(engine_factory) -> None:
    """目标已是完整标签「<门店>出售的<商品>」时 from 只能是 null、标签里的那家店或包含它的渠道：本轮没有列出的标签又写了
    另一家门店，环境以 SUPPLY_SOURCE_MISMATCH 拒绝，逐项明细的参数名是 from、候选就是这个标签（from 为 null，可照填），
    不下单、不扣款（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-指名购买from-是约束不是语法 （b）第二行）；写的正是标签里的门店是冗余的 from，照常受理，
    details.notes 说明可以写 null（3.2（b）第一行）。"""
    engine = engine_factory()
    listed = _browsed(engine)
    supply = _hidden(engine, listed, unique_name=False)
    layer = _layer_context(engine)
    label = supply_label(layer, supply)
    store = label.removesuffix(f"出售的{engine.context.rules.items[supply.product_id].name}")
    other = next(item.name for item in engine.context.scenario.locations if item.name != store)
    before = _world(engine)
    _, result = _round(engine, "label-with-from", start_request(
        targets=[label], actions=["obtain"], ways=["远程下单"], from_=[other], to=[None]))
    assert (result.status, result.error_code) == ("rejected", "SUPPLY_SOURCE_MISMATCH")
    [item] = result.data["items"]
    assert (item.get("field", item.get("path", [None])[-1]), item["written"], item["error_code"]) == ("from", other, "SUPPLY_SOURCE_MISMATCH")
    assert [candidate["arguments"]["items"][0] for candidate in item["candidates"]] == [
        {"target": label, "way": "远程下单", "from": None, "to": None}]
    assert result.data["reason"] == f"The target names {store}, but from names a different place."
    assert _world(engine) == before
    _, same = _round(engine, "label-with-own-store", start_request(
        targets=[label], actions=["obtain"], ways=["远程下单"], from_=[store], to=[None]))
    assert (same.status, same.error_code) == ("completed", None)
    assert [order["supply_id"] for order in same.data["orders"]] == [supply.supply_id]
    assert "resolved" not in same.data and [row["supply_id"] for row in same.data["orders"]] == [supply.supply_id]
    shown = last_result(engine.observe(context(engine, "label-with-own-store-seen")))["details"]
    assert shown["notes"] == same.data["notes"]


def test_named_resolution_follows_the_chosen_way_and_never_matches_internal_ids(engine_factory) -> None:
    """到店只在人就在出售它的门店里时可服务（分段匹配 M3）：在家按商品名到店以 ACTIVITY_PRECONDITION_UNMET 拒绝，下一步是
    从家出发路线最短的出售门店，候选是此刻能送到家的同名供给（远程下单）；按标签找到的供给不支持所写方式时以
    FULFILLMENT_NOT_SUPPORTED 拒绝并写明「只能到店」（M2）；私有供给编号既不是标签也不是商品名，找不到（M0）；人在集市时
    按标签到店解析出带当前报价与 named 标记的到店绑定行。"""
    engine = engine_factory()
    _stage_open(engine)
    layer = _layer_context(engine)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_PRECONDITION_UNMET") as far:
        resolve_named_supply(layer, PurchaseAsk(_MILK, "到店", None, "home", prefer="最快拿到"))
    item = far.value.suggested["item"]
    assert (item.get("field", item.get("path", [None])[-1]), item["written"], far.value.unmet) == ("target", _MILK, ["required_location_id"])
    assert "selling store" in far.value.public_reason
    assert far.value.suggested["next_step"].startswith("Go to ")
    assert item["candidates"] and all(row["way"] == "远程下单" and _MILK in row["target"]
                                     for row in item["candidates"])
    pickup_only = next(supply for supply in engine.context.scenario.supplies if set(supply.fulfillment_modes) == {"pickup"})
    with pytest.raises(BusinessRuleError, match="FULFILLMENT_NOT_SUPPORTED") as unsupported:
        resolve_named_supply(layer, PurchaseAsk(supply_label(layer, pickup_only), "远程下单", None, "home", prefer="最快拿到"))
    assert unsupported.value.public_reason == f"The only supported way for {supply_label(layer, pickup_only)} is 到店."
    supply = next(item for item in engine.context.scenario.supplies if item.location_id == "market"
                  and "pickup" in item.fulfillment_modes)
    with pytest.raises(BusinessRuleError, match="SUPPLY_NOT_FOUND"):
        resolve_named_supply(layer, PurchaseAsk(supply.supply_id, "远程下单", None, "home", prefer="最快拿到"))
    place(engine, 1, "market")
    layer = _layer_context(engine)
    row = resolve_named_supply(layer, PurchaseAsk(supply_label(layer, supply), "到店", None, "market", prefer="最快拿到"))
    quote = current_quote(engine.context.state, supply.supply_id)
    assert (row["kind"], row["object_id"], row["named"], row["selected_action"]["fulfillment"]) == (
        "supply", supply.supply_id, True, "pickup")
    assert row["quote"]["price_version"] == quote.price_version
    assert (row["channel_id"], row["purchase_note"]) == (None, None)


def test_remote_orders_no_selling_store_can_deliver_say_so_like_listed_ones(engine_factory) -> None:
    """远程下单此刻服务不了（M3）时的说法：人在不卖纯牛奶的紫薇公园，把它从各家纯牛奶的配送范围里去掉；只有一家出售门店
    （完整标签）时与非指名的远程下单同一句「这里不在这家店的配送范围内」（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-浏览结果与提示看可达性 ），几家出售
    门店（纯牛奶）都送不到时写「出售它的门店都送不到<收货地>」；unmet 都是 delivery_destination，下一步都只指向从此地可达、
    能送达的地点（同一门店、同一此地时两者相同），没有此刻能服务的同名候选。"""
    engine = engine_factory()
    _stage_open(engine)
    here = "poi-337936012"
    milk = [supply for supply in engine.context.scenario.supplies
            if next(item.name for item in engine.context.scenario.products if item.product_id == supply.product_id) == _MILK]
    assert len({supply.location_id for supply in milk}) > 1 and here not in {supply.location_id for supply in milk}
    for supply in milk:
        _retarget(engine, supply.supply_id, tuple(spot for spot in supply.delivery_destination_ids if spot != here))
    place(engine, 1, here)
    layer = _layer_context(engine)
    name = next(item.name for item in engine.context.scenario.locations if item.location_id == here)
    refusals = []
    for written in (_MILK, *(supply_label(layer, supply) for supply in milk)):
        with pytest.raises(BusinessRuleError, match="ACTIVITY_PRECONDITION_UNMET") as refused:
            resolve_named_supply(layer, PurchaseAsk(written, "远程下单", None, here, receiver=name, prefer="最快拿到"))
        assert (refused.value.unmet, refused.value.suggested["item"]["candidates"]) == (["delivery_destination"], [])
        refusals.append((refused.value.public_reason, refused.value.suggested["next_step"]))
    several, *single = refusals
    assert several[0] == f"No selling store can deliver to {name}."
    assert {reason for reason, _ in single} == {"The current place is outside this store's delivery area."}
    assert several[1] in {step for _, step in single} and several[1].startswith("Go to ")


def test_near_candidates_are_deterministic_term_similarity(monkeypatch) -> None:
    """近似候选按词项（单字与相邻二字）的 Dice 系数排序（名称与标签、商品名两者取大），相同得分按标签排序，至多 3 个；
    毫不相干的名称没有候选；此刻能按所写方式服务的排在前面（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析  第 3 条；可行性判定
    purchase_offers.serves 在这里换成按收货地是否在配送范围内），从不替换人物写的名称。"""
    monkeypatch.setattr(named_purchase, "serves", lambda context, supply, way, destination: (
        "delivery" in supply.fulfillment_modes and destination in supply.delivery_destination_ids))
    stores = {"s1": "好德", "s2": "集市", "s3": "农工商超市", "s4": "菜饭骨头汤"}
    products = {"p1": _MILK, "p2": "有机纯牛奶", "p3": "鸡汤面", "p4": "花都牧场有机纯牛奶"}
    scenario = _Scenario([_Supply(s, p) for s, p in (("s1", "p1"), ("s2", "p2"), ("s3", "p4"), ("s4", "p3"))],
                         stores, products)
    layer = _Layer(scenario)
    assert near_labels(layer, "牛奶", "delivery", "home") == [
        f"好德出售的{_MILK}", "集市出售的有机纯牛奶", "农工商超市出售的花都牧场有机纯牛奶"]
    assert near_labels(layer, "Zero Cola", "delivery", "home") == []
    assert near_labels(layer, "汤面", "delivery", "home") == ["菜饭骨头汤出售的鸡汤面"]
    deliverable = _Supply("s3", "p4", fulfillment_modes=("delivery",), delivery_destination_ids=("home",))
    feasible = _Layer(_Scenario([*scenario.supplies[:2], deliverable, scenario.supplies[3]], stores, products))
    assert near_labels(feasible, "牛奶", "delivery", "home") == [
        "农工商超市出售的花都牧场有机纯牛奶", f"好德出售的{_MILK}", "集市出售的有机纯牛奶"]
    assert near_labels(feasible, "牛奶", "delivery", "office")[0] == f"好德出售的{_MILK}"


def test_label_scope_decides_before_the_way_and_names_the_mismatch(monkeypatch) -> None:
    """分段匹配的段序（3.2（c））：标签写明的门店不在 from 的范围内时先报 SUPPLY_SOURCE_MISMATCH，不管所写方式是否支持；
    同一标签 from 为 null 时才轮到方式（M2，FULFILLMENT_NOT_SUPPORTED）；逐项合并后 items[] 各写各的码与参数名。"""
    from worldmock.life.error_contract import activity_candidate
    monkeypatch.setattr(named_purchase, "_request_candidate", lambda context, row, spec:
                        activity_candidate(spec["target"], spec["way"], (spec["from"], row["to"])))
    layer = _Layer(_Scenario([_Supply("s1", "p1", fulfillment_modes=("pickup",))], {"s1": "好德", "s2": "集市"},
                             {"p1": _MILK}))
    label = f"好德出售的{_MILK}"
    rows = [{"kind": NAMED_SUPPLY_KIND, "object": label, "selected_action": {"label": "远程下单"}, "to": None,
             "from": source} for source in ("集市", None)]
    with pytest.raises(BusinessRuleError, match="SUPPLY_SOURCE_MISMATCH") as refused:
        resolve_named_rows(layer, rows, lambda row: resolve_named_supply(
            layer, PurchaseAsk(row["object"], "远程下单", row["from"], "home", prefer="最快拿到")))
    items = refused.value.suggested["items"]
    assert [(item["path"][5], item["path"][-1], item["error_code"]) for item in items] == [
        (0, "from", "SUPPLY_SOURCE_MISMATCH"), (1, "way", "FULFILLMENT_NOT_SUPPORTED")]
    assert refused.value.public_reason == ("The target names 好德, but from names a different place.; "
                                           f"The only supported way for {label} is 到店.")


class _Supply:
    """近似候选与指名解析只读的供给字段。"""

    def __init__(self, location_id: str, product_id: str, fulfillment_modes: tuple = (),
                 delivery_destination_ids: tuple = ()):
        self.location_id, self.product_id = location_id, product_id
        self.supply_id = f"supply-{location_id}-{product_id}"
        self.fulfillment_modes, self.delivery_destination_ids = fulfillment_modes, delivery_destination_ids


class _Named:
    """带身份与名称的场景条目。"""

    def __init__(self, identity: str, name: str):
        self.location_id = self.product_id = identity
        self.name = name


class _Scenario:
    """近似候选只读的场景：供给、门店与商品名称。"""

    def __init__(self, supplies, stores: dict[str, str], products: dict[str, str]):
        self.supplies = supplies
        self.locations = [_Named(key, value) for key, value in stores.items()]
        self.products = [_Named(key, value) for key, value in products.items()]
        self.agents = ()


class _Layer:
    """近似候选只读场景的最小上下文（没有购买渠道）。"""

    def __init__(self, scenario):
        self.scenario = scenario
        self.agent_id = 1
        self.semantics = type("Semantics", (), {"purchase_channels": ()})()


def test_unlisted_obtain_selection_is_a_named_placeholder_other_verbs_are_not() -> None:
    """选择解析：obtain 以到店、远程下单或「购买」（含同义写法「买」「下单购买」）写未列出的名称得到指名购买占位行（所选
    动作已是购买；「购买」的履约方式由系统在受理时定，占位行上为 None）；其余动词、制作或列出目标不提供的方式仍解析不出。"""
    bindings = [{"object": "自己", "kind": "self", "object_id": "self",
                 "actions": [{"verb": "interact", "label": "等待", "canonical_action": "wait"}]}]
    [row] = select_start(bindings, start_arguments(["纯牛奶"], ["obtain"], ["到店"]))
    assert (row["kind"], row["object"], row["object_id"]) == (NAMED_SUPPLY_KIND, "纯牛奶", None)
    assert (row["selected_action"]["canonical_action"], row["selected_action"]["fulfillment"]) == (
        "purchase", "pickup")
    for way in ("购买", "买", "下单购买"):
        [system] = select_start(bindings, start_arguments(["纯牛奶"], ["obtain"], [way]))
        assert (system["kind"], system["selected_action"]["label"], system["selected_action"]["fulfillment"]) == (
            NAMED_SUPPLY_KIND, "购买", None)
    pair = select_start(bindings, start_arguments(["自己", "纯牛奶"], ["interact", "obtain"], ["等待", "远程下单"]))
    assert [item["selected_action"]["label"] for item in pair] == ["等待", "远程下单"]
    for verb, way in (("obtain", "制作"), ("consume", "食用")):
        assert select_start(bindings, start_arguments(["纯牛奶"], [verb], [way])) is None
    assert select_start(bindings, start_arguments(["自己"], ["obtain"], ["到店"])) is None
    assert select_start(bindings, start_arguments(["自己"], ["obtain"], ["购买"])) is None

def _host(menu: tuple[str, ...] | None = None) -> LifeAgent:
    """从一次结构化观察准备 LifeAgent（menu 给出时替换本轮有效菜单）。"""
    observed = copy.deepcopy(_observations())
    if menu is not None:
        observed[0]["data"]["interaction_menu"] = {"allowed_actions": sorted(menu)}
    person = LifeAgent()
    person.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observed))
    person.build_react_tool_schemas(readonly=False)
    return person


def _decision(targets: list[str], actions: list[str], ways: list[str]) -> ReactDecision:
    """一次 start_activity 决定。"""
    return ReactDecision("", "start_activity", start_arguments(targets, actions, ways), "")


def test_host_lets_unlisted_obtain_names_through_and_corrects_other_verbs() -> None:
    """宿主本地校验：obtain 以到店、远程下单或「购买」（含同义写法「外卖」「堂食」「买」）写未列出的名称放行（与列出的等待
    同一请求也可）；其余动词、制作写的名称不是任何对象时按协议错误在本轮纠正；指名购买按购买受菜单判定，菜单不开放购买时
    同样纠正。"""
    person = _host()
    for way in ("到店", "远程下单", "外卖", "堂食", "购买", "买"):
        assert person.validate_react_decisions([_decision(["纯牛奶"], ["obtain"], [way])], False) == ""
    assert person.validate_react_decisions([_decision(["自己", "纯牛奶"], ["interact", "obtain"],
                                                      ["等待", "远程下单"])], False) == ""
    for verb, way in (("consume", "食用"), ("interact", "观看"), ("travel", "步行"), ("obtain", "制作")):
        error = person.validate_react_decisions([_decision(["纯牛奶"], [verb], [way])], False)
        assert error.report.error_code == "NAME_NOT_FOUND"
        assert [(item.path[-1], item.written) for item in error.report.items] == [("target", "纯牛奶")]
    closed = _host(("sleep", "wait"))
    denied = closed.validate_react_decisions([_decision(["纯牛奶"], ["obtain"], ["到店"])], False)
    assert denied.report.error_code == "WAY_NOT_OFFERED"
    assert "not admitted" in denied.report.reason and denied.report.items[0].written == "到店"
