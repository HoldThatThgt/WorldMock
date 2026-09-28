"""指名购买（docs/dev/SPEC-persona-prompt.md 第五章「指名购买」、裁决 #19 与 #26）。

真实 LifeEngine 与 SQLite 上：被轮转隐藏但存在的商品按标签或唯一商品名可买，按当前报价在同一秒结算，不查可见凭据；
不存在的名称以 SUPPLY_NOT_FOUND 拒绝，details.candidates 给至多 3 个确定性的近似标签，不换成别的商品；多店同名且未限定
门店以 SUPPLY_NAME_AMBIGUOUS 拒绝并列出全部门店；完整标签又写了门店（from）以 ACTION_REQUEST_INVALID 拒绝。宿主本地校验只对 obtain（到店、远程下单）放行未列出的名称，其余动词的
未列名称仍是协议错误，在本轮纠正。
"""

from __future__ import annotations

import copy
import os

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest  # noqa: E402
from agentsociety2.society import DirectToolCall  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from tests.integration.test_life_agent_protocol import _context, _observations  # noqa: E402
from tests.life.conftest import context, last_result, stage_day_plan, start_arguments, start_request  # noqa: E402
from tests.life.test_commerce import _browse, _stage_open  # noqa: E402
from worldmock.errors import BusinessRuleError  # noqa: E402
from worldmock.life import projection  # noqa: E402
from worldmock.life.interaction_menu import NAMED_SUPPLY_KIND, resolve_interaction_menu, resolve_selection  # noqa: E402
from worldmock.life.models import StartActivityRequest  # noqa: E402
from worldmock.life.named_purchase import near_candidates, resolve_named_supply, supply_label  # noqa: E402
from worldmock.life.readable import money_phrase  # noqa: E402
from worldmock.life.storefront_state import current_quote  # noqa: E402
from worldmock.life.tool_layer import LayerContext  # noqa: E402

_MILK = "纯牛奶"


def _layer_context(engine) -> LayerContext:
    """引擎当前世界的只读切片（指名购买的解析只读世界、场景与语义）。"""
    agent = engine.state.agents[1]
    return LayerContext(state=engine.state, scenario=engine.scenario, rules=engine.rules, semantics=engine.semantics,
                        agent_id=1, names={"sessions": {}, "activities": {}}, bindings=[],
                        menu=resolve_interaction_menu(engine.scenario, 1, agent.location_id))


def _obtain(target: str, way: str = "远程下单") -> StartActivityRequest:
    """对一个名称选择取得（obtain）及其方式的请求。"""
    return start_request(targets=[target], actions=["obtain"], ways=[way])


def _round(engine, name: str, request):
    """单人物按真实顺序观察、登记并结算一轮 start_activity，返回（本轮观察，结果）。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", "start_activity", request))
    return observed, engine.commit_decision_round(name)[0]


def _world(engine) -> tuple:
    """拒绝时必须不变的世界事实：余额、库存与订单。"""
    return dict(engine.state.accounts), dict(engine.state.supply_stock), dict(engine.state.deliveries)


def _browsed(engine) -> set[str]:
    """营业时段浏览一次商品目录后，本轮观察列出的对象名称（轮转只呈现一部分供给）。"""
    _stage_open(engine)
    stage_day_plan(engine)
    _browse(engine)
    return {row["object"] for row in engine.observe(context(engine, "listed"))["interaction_bindings"]}


def _hidden(engine, listed: set[str], unique_name: bool):
    """一条存在、能送到家、没有被本轮列出也没有可见凭据的供给（unique_name 时其商品名在场景供给中唯一）。"""
    context_ = _layer_context(engine)
    names = [next(item.name for item in engine.scenario.products if item.product_id == supply.product_id)
             for supply in engine.scenario.supplies]
    visible = set(projection.visible_supplies(engine.state, engine.state.agents[1], engine.scenario))
    return next(supply for supply, name in zip(engine.scenario.supplies, names)
                if supply_label(context_, supply) not in listed and supply.supply_id not in visible
                and "delivery" in supply.fulfillment_modes and "home" in supply.delivery_destination_ids
                and (not unique_name or names.count(name) == 1))


@pytest.mark.parametrize("by", ["label", "product"])
def test_rotation_hidden_supply_is_bought_by_name_at_the_current_quote(engine_factory, by: str) -> None:
    """浏览后被轮转隐藏、没有可见凭据的供给按标签「<门店>出售的<商品>」或唯一商品名远程下单：按当前报价同一秒结算，
    付款、库存与订单都是这条供给；结果进入下一次观察的公开结果。"""
    engine = engine_factory()
    listed = _browsed(engine)
    supply = _hidden(engine, listed, unique_name=by == "product")
    product = engine.rules.items[supply.product_id]
    name = supply_label(_layer_context(engine), supply) if by == "label" else product.name
    assert name not in listed
    quote = current_quote(engine.state, supply.supply_id)
    price = engine.semantics.purchase_amount(supply.product_id, quote.price_fen, 1)
    balance, stock = engine.state.accounts["agent:1"], engine.state.supply_stock[supply.supply_id]
    _, result = _round(engine, f"named-{by}", _obtain(name))
    assert (result.status, result.error_code) == ("completed", None)
    [order] = result.data["orders"]
    assert (order["supply_id"], order["product_id"], order["price_version"], order["total_price_fen"]) == (
        supply.supply_id, supply.product_id, quote.price_version, price)
    assert (order["fulfillment"], order["destination_id"], order["created_at"]) == (
        "delivery", "home", engine.state.current_time)
    assert engine.state.accounts["agent:1"] == balance - price
    assert engine.state.supply_stock[supply.supply_id] == stock - 1
    [shown] = last_result(engine.observe(context(engine, f"named-{by}-seen")))["details"]["purchases"]
    assert (shown["product"], shown["paid"], shown["way"]) == (product.name, money_phrase(price), "远程下单")


def test_unknown_name_is_rejected_with_near_candidates_and_nothing_is_substituted(engine_factory) -> None:
    """不存在的名称以 SUPPLY_NOT_FOUND 拒绝：details.candidates 给至多 3 个确定性的近似标签（名称相近的真实供给），
    原因与下一步可读；不自动换成近似商品，余额、库存与订单都不变。"""
    engine = engine_factory()
    _browsed(engine)
    before = _world(engine)
    name = f"{_MILK}加冰"
    _, result = _round(engine, "unknown", _obtain(name))
    assert (result.status, result.error_code) == ("rejected", "SUPPLY_NOT_FOUND")
    labels = {supply_label(_layer_context(engine), supply) for supply in engine.scenario.supplies}
    candidates = result.data["candidates"]
    assert 1 <= len(candidates) <= 3 and set(candidates) <= labels
    assert candidates == near_candidates(_layer_context(engine), name)
    assert all(_MILK in label for label in candidates)
    assert _world(engine) == before
    details = last_result(engine.observe(context(engine, "unknown-seen")))["details"]
    assert details == {"reason": "没有找到这个名字的商品", "next_step": "从候选或本轮列出的商品里选",
                       "candidates": candidates}


def test_same_name_in_several_stores_is_ambiguous_until_a_store_is_named(engine_factory) -> None:
    """多家店都能送到家的同名商品（纯牛奶）：未限定门店以 SUPPLY_NAME_AMBIGUOUS 拒绝并列出全部门店（按名称排序）；
    写成标签或限定门店时解析为那家店的供给。"""
    engine = engine_factory()
    _browsed(engine)
    layer = _layer_context(engine)
    milk = [supply for supply in engine.scenario.supplies
            if engine.rules.items[supply.product_id].name == _MILK and "home" in supply.delivery_destination_ids]
    stores = sorted(supply_label(layer, supply).removesuffix(f"出售的{_MILK}") for supply in milk)
    assert len(stores) >= 2
    before = _world(engine)
    _, result = _round(engine, "ambiguous", _obtain(_MILK))
    assert (result.status, result.error_code, result.data["candidates"]) == (
        "rejected", "SUPPLY_NAME_AMBIGUOUS", stores)
    assert result.data["reason"] == "好几家店都卖这个名字的商品"
    assert _world(engine) == before
    for supply in milk:
        store = supply_label(layer, supply).removesuffix(f"出售的{_MILK}")
        assert resolve_named_supply(layer, _MILK, "远程下单", store, "home")["object_id"] == supply.supply_id
        assert resolve_named_supply(layer, supply_label(layer, supply), "远程下单", None, "home")["object_id"] == (
            supply.supply_id)


def test_full_label_with_a_store_in_from_is_an_invalid_request(engine_factory) -> None:
    """目标已是完整标签「<门店>出售的<商品>」时 from 须为 null：本轮没有列出的标签又写了门店，与列出的标签一样以
    ACTION_REQUEST_INVALID 拒绝，不下单、不扣款（docs/dev/SPEC-persona-prompt.md 第五章「来源与去向」）。"""
    engine = engine_factory()
    listed = _browsed(engine)
    supply = _hidden(engine, listed, unique_name=False)
    layer = _layer_context(engine)
    label = supply_label(layer, supply)
    store = label.removesuffix(f"出售的{engine.rules.items[supply.product_id].name}")
    before = _world(engine)
    _, result = _round(engine, "label-with-from", start_request(
        targets=[label], actions=["obtain"], ways=["远程下单"], from_=[store], to=["自己"]))
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert _world(engine) == before
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        resolve_named_supply(layer, label, "远程下单", store, "home")


def test_named_resolution_follows_the_chosen_way_and_never_matches_internal_ids(engine_factory) -> None:
    """到店只找人物所在地点的门店（家里没有门店，按商品名找不到并给候选）；按标签找到的供给不支持所选方式时以
    FULFILLMENT_NOT_SUPPORTED 拒绝；私有供给编号既不是标签也不是商品名，找不到。解析出的行带当前报价与 named 标记。"""
    engine = engine_factory()
    _stage_open(engine)
    layer = _layer_context(engine)
    with pytest.raises(BusinessRuleError, match="SUPPLY_NOT_FOUND") as missing:
        resolve_named_supply(layer, _MILK, "到店", None, "home")
    assert missing.value.suggested["candidates"] == near_candidates(layer, _MILK)
    pickup_only = next(supply for supply in engine.scenario.supplies if set(supply.fulfillment_modes) == {"pickup"})
    with pytest.raises(BusinessRuleError, match="FULFILLMENT_NOT_SUPPORTED"):
        resolve_named_supply(layer, supply_label(layer, pickup_only), "远程下单", None, "home")
    supply = engine.scenario.supplies[0]
    with pytest.raises(BusinessRuleError, match="SUPPLY_NOT_FOUND"):
        resolve_named_supply(layer, supply.supply_id, "远程下单", None, "home")
    row = resolve_named_supply(layer, supply_label(layer, supply), "到店", None, "home")
    quote = current_quote(engine.state, supply.supply_id)
    assert (row["kind"], row["object_id"], row["named"], row["selected_action"]["fulfillment"]) == (
        "supply", supply.supply_id, True, "pickup")
    assert row["quote"]["price_version"] == quote.price_version


def test_near_candidates_are_deterministic_term_similarity() -> None:
    """近似候选只按词项（单字与相邻二字）的 Dice 系数排序，相同得分按标签排序，至多 3 个；毫不相干的名称没有候选。"""
    stores = {"s1": "好德", "s2": "集市", "s3": "农工商超市", "s4": "菜饭骨头汤"}
    products = {"p1": _MILK, "p2": "有机纯牛奶", "p3": "鸡汤面", "p4": "花都牧场有机纯牛奶"}
    scenario = _Scenario([_Supply(s, p) for s, p in (("s1", "p1"), ("s2", "p2"), ("s3", "p4"), ("s4", "p3"))],
                         stores, products)
    layer = _Layer(scenario)
    assert near_candidates(layer, "牛奶") == [f"好德出售的{_MILK}", "集市出售的有机纯牛奶", "农工商超市出售的花都牧场有机纯牛奶"]
    assert near_candidates(layer, "Zero Cola") == []
    assert near_candidates(layer, "汤面") == ["菜饭骨头汤出售的鸡汤面"]


def test_ambiguous_name_lists_every_store_not_just_three() -> None:
    """多店同名且 from 为空：SUPPLY_NAME_AMBIGUOUS 列出全部门店（按名称排序），不像近似候选那样截到 3 个。"""
    stores = {"s1": "好德", "s2": "集市", "s3": "农工商超市", "s4": "盒马"}
    supplies = [_Supply(store, "p1", fulfillment_modes=("delivery",), delivery_destination_ids=("home",))
                for store in stores]
    layer = _Layer(_Scenario(supplies, stores, {"p1": _MILK}))
    with pytest.raises(BusinessRuleError, match="SUPPLY_NAME_AMBIGUOUS") as ambiguous:
        resolve_named_supply(layer, _MILK, "远程下单", None, "home")
    assert ambiguous.value.suggested["candidates"] == sorted(stores.values())


class _Supply:
    """近似候选与指名解析只读的供给字段。"""

    def __init__(self, location_id: str, product_id: str, fulfillment_modes: tuple = (),
                 delivery_destination_ids: tuple = ()):
        self.location_id, self.product_id = location_id, product_id
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


class _Layer:
    """近似候选只读场景的最小上下文。"""

    def __init__(self, scenario):
        self.scenario = scenario


def test_unlisted_obtain_selection_is_a_named_placeholder_other_verbs_are_not() -> None:
    """选择解析：obtain 以到店或远程下单写未列出的名称得到指名购买占位行（所选动作已是购买）；其余动词、制作或列出目标
    不提供的方式仍解析不出。"""
    bindings = [{"object": "自己", "kind": "self", "object_id": "self",
                 "actions": [{"verb": "interact", "label": "等待", "canonical_action": "wait"}]}]
    [row] = resolve_selection(bindings, ["纯牛奶"], ["obtain"], ["到店"])
    assert (row["kind"], row["object"], row["object_id"]) == (NAMED_SUPPLY_KIND, "纯牛奶", None)
    assert (row["selected_action"]["canonical_action"], row["selected_action"]["fulfillment"]) == (
        "purchase", "pickup")
    pair = resolve_selection(bindings, ["自己", "纯牛奶"], ["interact", "obtain"], ["等待", "远程下单"])
    assert [item["selected_action"]["label"] for item in pair] == ["等待", "远程下单"]
    for verb, way in (("obtain", "制作"), ("consume", "食用"), ("interact", "到店")):
        assert resolve_selection(bindings, ["纯牛奶"], [verb], [way]) is None
    assert resolve_selection(bindings, ["自己"], ["obtain"], ["到店"]) is None


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
    """宿主本地校验：obtain 以到店或远程下单写未列出的名称放行（与列出的等待同一请求也可）；其余动词、制作写未列出的名称
    按协议错误在本轮纠正；指名购买按购买受菜单判定，菜单不开放购买时同样纠正。"""
    person = _host()
    for way in ("到店", "远程下单"):
        assert person.validate_react_decisions([_decision(["纯牛奶"], ["obtain"], [way])], False) == ""
    assert person.validate_react_decisions([_decision(["自己", "纯牛奶"], ["interact", "obtain"],
                                                      ["等待", "远程下单"])], False) == ""
    for verb, way in (("consume", "食用"), ("interact", "观看"), ("travel", "步行"), ("obtain", "制作")):
        assert person.validate_react_decisions([_decision(["纯牛奶"], [verb], [way])], False) == (
            "start_activity target is not a visible interactive object")
    closed = _host(("sleep", "wait"))
    assert closed.validate_react_decisions([_decision(["纯牛奶"], ["obtain"], ["到店"])], False) == (
        "interaction menu denies start_activity")
