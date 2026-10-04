"""指名购买的分段匹配、from 约束与由系统定方式的「购买」（docs/dev/SPEC-dayrun-fidelity.md 3.2（b）（c）（d）、3.5；验收台账
「指名购买」与 3.5 的验收）。

白领场景的真实 LifeEngine、SQLite 与 LifeAgent 上，同一次冻结观察先经宿主本地校验、再由环境受理：from 是可选约束——
写的门店不卖这件商品报 SUPPLY_NOT_AT_STORE（那家店完全没有供给时原因写「这里没有可买的商品」），范围内同名的供给都不支持
所写方式报 FULFILLMENT_NOT_SUPPORTED 并说明只能用哪种方式，此刻服务不了报 ACTIVITY_PRECONDITION_UNMET 并写明去哪里；方式
「购买」由系统定到店或远程下单（人在出售它的店里就到店，否则能送到就远程下单，都不行就拒绝并在 next_step 写路线最短的那家
能到店买的出售门店），跳过方式一段（M2），对本轮列出与按名写的商品目标都受理，工具层的商品目标从不列出它；多件购买逐项报错、
整组原子。
"""

from __future__ import annotations

import os

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from tests.life.conftest import (advance_until_ready, context, stage_day_plan, start_arguments,  # noqa: E402
                                 wake_by_mail)
from tests.life.purchase_worlds import at_clock, decide  # noqa: E402
from tests.life.routing_fixtures import place  # noqa: E402
from tests.life.test_browse_and_hints import _rescenario, _retarget  # noqa: E402
from tests.life.test_commerce import _browse  # noqa: E402
from worldmock.life.basket import delivery_expected_at  # noqa: E402
from worldmock.life.guidance import refusal_for_code  # noqa: E402
from worldmock.life.readable import clock_phrase  # noqa: E402
from worldmock.life.wayfinding import shortest_paths  # noqa: E402

_MILK, _NOODLES = "纯牛奶", "牛肉炒面"
_MARKET_NAME = "张江日用集市与配送取货点（冻结场景锚点）"
#: 菜饭骨头汤只到店出售鸡汤面（10:00–21:00 营业）；鼎盛川菜不卖纯牛奶；紫薇公园没有任何供给。
_SOUP, _SICHUAN, _PARK = "poi-3538660453", "poi-3538682404", "poi-337936012"
#: 出售纯牛奶的另两家门店：离家最近的农工商超市，与更远一些的好德（集市最远）。
_SUPERMARKET, _HAODE = "poi-3538763552", "poi-3538682402"


def _world(engine_factory, hour: int = 10):
    """白领场景：登记空计划，时间在 1 月 5 日的某个整点（门店营业），人物在家。"""
    engine = engine_factory()
    stage_day_plan(engine)
    at_clock(engine, hour, 0)
    return engine


def _buy(*items, prefer: str = "最快拿到") -> dict:
    """逐项（名称，方式，from）的取得请求，都送给自己。"""
    return start_arguments([item[0] for item in items], ["obtain"] * len(items), [item[1] for item in items],
                           **{"from": [item[2] for item in items], "to": [None] * len(items), "prefer": prefer})


def _world_facts(engine) -> tuple:
    """拒绝时必须不变的世界事实：余额、库存与订单。"""
    return dict(engine.state.accounts), dict(engine.state.supply_stock), dict(engine.state.deliveries)


def _place(engine, location_id: str) -> str:
    """地点的显示名。"""
    return next(item.name for item in engine.scenario.locations if item.location_id == location_id)


def test_goods_from_a_store_that_does_not_sell_them_are_supply_not_at_store(engine_factory) -> None:
    """from 是已知门店但它不卖（3.2（b）第三行、M1）：以 SUPPLY_NOT_AT_STORE 拒绝，逐项明细的参数名是 from，候选为出售它的
    门店的完整标签（from 为 null，可照填并被受理）；那家店完全没有供给时原因写「这里没有可买的商品」。宿主都放行。"""
    engine = _world(engine_factory)
    before = _world_facts(engine)
    sichuan, park = _place(engine, _SICHUAN), _place(engine, _PARK)
    _, error, refused = decide(engine, "not-at-store", _buy((_MILK, "远程下单", sichuan)))
    assert error == "" and (refused.status, refused.error_code) == ("rejected", "SUPPLY_NOT_AT_STORE")
    [item] = refused.data["items"]
    assert (item["path"][-2], item["path"][-1], item["written"]) == (0, "from", sichuan)
    assert refused.data["reason"] == f"This store does not sell {_MILK}."
    assert refused.data["fix"] == "Set from to null or a store that sells this product."
    assert item["candidates"] and all(row["arguments"]["items"][0]["target"].endswith(f"出售的{_MILK}") and row["arguments"]["items"][0]["from"] is None
                                      for row in item["candidates"])
    _, error, nothing = decide(engine, "store-without-supplies", _buy((_MILK, "远程下单", park)))
    assert error == "" and nothing.error_code == "SUPPLY_NOT_AT_STORE"
    assert nothing.data["reason"] == "The source has no purchasable supplies."
    assert _world_facts(engine) == before
    candidate = item["candidates"][0]
    _, _, retried = decide(engine, "not-at-store-retry", _buy((candidate["arguments"]["items"][0]["target"], candidate["arguments"]["items"][0]["way"], None)))
    assert retried.status == "completed"


def test_the_name_stage_is_judged_before_the_source(engine_factory) -> None:
    """分段匹配依次判定（3.2（c）第 1 步先于来源，3.3 解析规则 1）：名称不是任何商品时报 SUPPLY_NOT_FOUND（只表示没有这件
    商品，逐项明细的参数名是 targets），即使 from 也不是门店或 App 的名称；名称对得上而 from 不认识时才报 SOURCE_NOT_FOUND，
    参数名是 from。宿主都放行（App 称呼与不认识的写法交给环境），不下单。"""
    engine = _world(engine_factory)
    before = _world_facts(engine)
    _, error, missing = decide(engine, "no-such-goods", _buy(("不存在的商品", "远程下单", "不存在的App")))
    assert error == "" and (missing.status, missing.error_code) == ("rejected", "SUPPLY_NOT_FOUND")
    assert [(item["path"][-1], item["written"]) for item in missing.data["items"]] == [("target", "不存在的商品")]
    _, error, unknown = decide(engine, "no-such-source", _buy((_MILK, "远程下单", "不存在的App")))
    assert error == "" and (unknown.status, unknown.error_code) == ("rejected", "SOURCE_NOT_FOUND")
    assert [(item["path"][-1], item["written"]) for item in unknown.data["items"]] == [("from", "不存在的App")]
    assert _world_facts(engine) == before


def test_a_way_the_matching_store_does_not_offer_is_fulfillment_not_supported(engine_factory) -> None:
    """范围内同名的供给都不支持所写方式（M2）：菜饭骨头汤的鸡汤面只到店，远程下单以 FULFILLMENT_NOT_SUPPORTED 拒绝，原因写
    「鸡汤面只能到店」，逐项明细的参数名是 ways，候选是同一标签的到店；方式写「购买」时跳过这一段，人在店里就按到店受理。"""
    engine = _world(engine_factory, hour=11)
    soup = _place(engine, _SOUP)
    _, error, refused = decide(engine, "way", _buy((_NOODLES, "远程下单", soup)))
    assert error == "" and (refused.status, refused.error_code) == ("rejected", "FULFILLMENT_NOT_SUPPORTED")
    [item] = refused.data["items"]
    assert (item["path"][-1], item["written"]) == ("way", "远程下单")
    assert refused.data["reason"] == f"The only supported way for {_NOODLES} is 到店."
    assert [row["arguments"] for row in item["candidates"]] == [_buy((f"{soup}出售的{_NOODLES}", "到店", None))]
    assert item["candidates"][0]["note"]
    place(engine, 1, _SOUP)
    _, error, bought = decide(engine, "system-way-in-store", _buy((_NOODLES, "购买", soup)))
    assert error == "" and (bought.status, bought.error_code) == ("completed", None)
    [order] = bought.data["orders"]
    assert (order["fulfillment"], order["location_id"], order["channel_id"]) == ("pickup", _SOUP, None)


def test_system_purchase_and_a_null_from_each_skip_only_their_own_stage(engine_factory) -> None:
    """3.5「收敛」第 4 条：方式为「购买」只跳过方式一段（M2），名称（M0）与门店（M1）两段照常报码——名称找不到报
    SUPPLY_NOT_FOUND（参数名 targets），from 写的门店不卖报 SUPPLY_NOT_AT_STORE（参数名 from）；from 为 null 只跳过门店一段
    （M1），方式一段照常报码——菜饭骨头汤只到店出售的鸡汤面按完整标签、from 为 null 写远程下单报 FULFILLMENT_NOT_SUPPORTED
    （参数名 ways）。宿主都放行，拒绝时余额、库存与订单都不变。"""
    engine = _world(engine_factory, hour=11)
    sichuan, label = _place(engine, _SICHUAN), f"{_place(engine, _SOUP)}出售的{_NOODLES}"
    before = _world_facts(engine)
    stages = []
    for round_id, request in (("system-unknown", ("并不存在的神秘商品", "购买", None)),
                              ("system-not-at-store", (_MILK, "购买", sichuan)),
                              ("null-from-way", (label, "远程下单", None))):
        _, error, refused = decide(engine, round_id, _buy(request))
        [item] = refused.data["items"]
        stages.append((error, refused.status, refused.error_code, item["path"][-1], item["written"]))
    assert stages == [("", "rejected", "SUPPLY_NOT_FOUND", "target", "并不存在的神秘商品"),
                      ("", "rejected", "SUPPLY_NOT_AT_STORE", "from", sichuan),
                      ("", "rejected", "FULFILLMENT_NOT_SUPPORTED", "way", "远程下单")]
    assert _world_facts(engine) == before


def test_system_purchase_goes_in_store_when_here_and_remote_when_deliverable(engine_factory) -> None:
    """「购买」由系统定方式（3.5「收敛」第 2 条）：人在出售它的集市里就到店；在家时能送到家就远程下单，多家都能送时按最快
    选定；受理说明写明定下的方式与所选门店。"""
    engine = _world(engine_factory)
    place(engine, 1, "market")
    _, error, here = decide(engine, "system-in-store", _buy((_MILK, "购买", None)))
    assert error == "" and here.status == "completed"
    [order] = here.data["orders"]
    assert (order["fulfillment"], order["location_id"]) == ("pickup", "market")
    assert here.data["notes"] == [f"「{_MILK}」「购买」定为到店：{_MARKET_NAME}出售的{_MILK}"]
    place(engine, 1, "home")
    _, error, remote = decide(engine, "system-remote", _buy((_MILK, "购买", None)))
    assert error == "" and remote.status == "completed"
    [delivered] = remote.data["orders"]
    assert (delivered["fulfillment"], delivered["destination_id"]) == ("delivery", "home")
    assert remote.data["notes"][0].startswith(f"「{_MILK}」「购买」定为远程下单，有 3 处都能远程下单，按最快拿到选了")


def test_system_purchase_on_the_road_is_not_buying_in_the_store_just_left(engine_factory) -> None:
    """「购买」只在人此刻就在出售它的门店里时到店（3.5「收敛」第 2 条；途中不在任何店里）：从集市步行回家的路上写「购买」，
    即使出发地集市就卖纯牛奶也不定为到店，而是能送到收货地就远程下单（远程下单的送达地途中取出发地，与列出的远程下单同一
    口径），受理说明写明定为远程下单。"""
    engine = _world(engine_factory)
    place(engine, 1, "market")
    _, error, walking = decide(engine, "walk-home", start_arguments([f"地点：{_place(engine, 'home')}"], ["travel"],
                                                                    ["步行"]))
    assert error == "" and walking.status == "in_progress"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    assert engine.state.activities[walking.activity_id].status == "active"
    _, error, bought = decide(engine, "buy-on-the-road", _buy((_MILK, "购买", None)))
    assert error == "" and (bought.status, bought.error_code) == ("completed", None)
    assert [order["fulfillment"] for order in bought.data["orders"]] == ["delivery"]
    assert bought.data["notes"][0].startswith(f"「{_MILK}」「购买」定为远程下单")


def test_system_purchase_that_can_neither_pick_up_nor_deliver_names_the_nearest_store(engine_factory) -> None:
    """「购买」两者都不行（人不在出售它的店里，范围内的供给也送不到收货地）：以 ACTIVITY_PRECONDITION_UNMET 拒绝，next_step
    写从当前地点出发路线最短的出售门店，未满足条件是要在那里与送不到这里；不下单。"""
    engine = _world(engine_factory, hour=11)
    soup = _place(engine, _SOUP)
    before = _world_facts(engine)
    _, error, refused = decide(engine, "system-none", _buy((_NOODLES, "购买", soup)))
    assert error == "" and (refused.status, refused.error_code) == ("rejected", "ACTIVITY_PRECONDITION_UNMET")
    home = _place(engine, "home")
    assert refused.data["reason"] == f"The caller is not at a selling store and delivery cannot reach {home}."
    assert refused.data["next_step"] == f"Go to {soup}."
    [item] = refused.data["items"]
    assert (item["path"][-2], item["path"][-1], item["error_code"]) == (0, "target", "ACTIVITY_PRECONDITION_UNMET")
    assert _world_facts(engine) == before
    [stored] = [event for event in engine.store.read_agent_events("action_rejected", 1)
                if event.action_id == "action-system-none"]
    assert stored.data["unmet"] == ["required_location_id", "delivery_destination"]


def test_system_purchase_next_step_names_the_nearest_store_that_sells_it_in_store(engine_factory) -> None:
    """「购买」两者都不行时 next_step 写路线最短的那家能到店买的出售门店（3.5「收敛」第 2 条）：三家出售纯牛奶的门店都送不到
    家，离家最近的农工商超市只远程下单（去了也买不到），好德与集市到店、远程下单都行；拒绝的下一步是好德，不是农工商超市，
    未满足条件是要在那里与送不到这里，不下单；照着去了好德再写「购买」就按到店成单。"""
    engine = _world(engine_factory)
    milk = next(item.product_id for item in engine.scenario.products if item.name == _MILK)
    _rescenario(engine, supplies=tuple(
        item.model_copy(update={"delivery_destination_ids": ("office",), "fulfillment_modes": (
            ("delivery",) if item.location_id == _SUPERMARKET else item.fulfillment_modes)})
        if item.product_id == milk else item for item in engine.scenario.supplies))
    before = _world_facts(engine)
    home, haode, supermarket = _place(engine, "home"), _place(engine, _HAODE), _place(engine, _SUPERMARKET)
    _, error, refused = decide(engine, "system-pickup-store", _buy((_MILK, "购买", None)))
    assert error == "" and (refused.status, refused.error_code) == ("rejected", "ACTIVITY_PRECONDITION_UNMET")
    assert refused.data["reason"] == f"The caller is not at a selling store and delivery cannot reach {home}."
    assert refused.data["next_step"] == f"Go to {haode}." and supermarket not in refused.data["next_step"]
    [stored] = [event for event in engine.store.read_agent_events("action_rejected", 1)
                if event.action_id == "action-system-pickup-store"]
    assert stored.data["unmet"] == ["required_location_id", "delivery_destination"]
    assert _world_facts(engine) == before
    place(engine, 1, _HAODE)
    _, error, bought = decide(engine, "system-at-haode", _buy((_MILK, "购买", None)))
    assert error == "" and (bought.status, bought.error_code) == ("completed", None)
    [order] = bought.data["orders"]
    assert (order["fulfillment"], order["location_id"]) == ("pickup", _HAODE)


def test_system_purchase_is_accepted_for_listed_and_named_goods_and_never_listed(engine_factory) -> None:
    """「购买」对任何商品目标都受理（3.5「收敛」第 2 条）：本轮列出的商品目标写「购买」宿主放行、环境按系统定下的方式成单；
    按名写的商品同样受理；工具层的商品目标只列到店与远程下单，从不列出「购买」。"""
    engine = _world(engine_factory)
    _browse(engine)
    observed = engine.observe(context(engine, "listed"))
    goods = [target for target in observed["tool_layer"]["targets"] if target["kind"] == "product"]
    assert goods and all(action["way"] in {"到店", "远程下单"} for target in goods for action in target["actions"])
    listed = next(row["object"] for row in observed["interaction_bindings"] if row["kind"] == "supply"
                  and any(action["label"] == "远程下单" for action in row["actions"]))
    _, error, bought = decide(engine, "system-listed", _buy((listed, "购买", None)))
    assert error == "" and (bought.status, bought.error_code) == ("completed", None)
    [order] = bought.data["orders"]
    moment = clock_phrase(order["expected_at"], engine.state.current_time, engine.scenario.timezone)
    assert order["fulfillment"] == "delivery"
    assert bought.data["notes"] == [f"「{listed}」「购买」定为远程下单：{listed}（预计 {moment} 送到）"]
    _, error, named = decide(engine, "system-named", _buy((_MILK, "购买", None)))
    assert error == "" and named.status == "completed"
    assert named.data["resolved"][0]["written"] == _MILK


def test_multi_item_purchases_report_every_item_and_stay_atomic(engine_factory) -> None:
    """多件购买逐项解析、逐项报错（3.2（d）、第四章 items[]）：两项各有一段不成立时一次拒绝写出两项（各带自己的码与参数名，
    码取第一个名称类拒绝）；一项能买、另一项此刻服务不了时整组拒绝（ACTIVITY_PRECONDITION_UNMET，只写出错的第 2 项，下一步
    是它的出售门店），能买的那一项也不下单。"""
    engine = _world(engine_factory, hour=11)
    park, soup = _place(engine, _PARK), _place(engine, _SOUP)
    before = _world_facts(engine)
    _, error, both = decide(engine, "two-failures", _buy((_MILK, "远程下单", park), (_NOODLES, "远程下单", soup)))
    assert error == "" and (both.status, both.error_code) == ("rejected", "SUPPLY_NOT_AT_STORE")
    assert [(item["path"][-2], item["path"][-1], item["error_code"]) for item in both.data["items"]] == [
        (0, "from", "SUPPLY_NOT_AT_STORE"), (1, "way", "FULFILLMENT_NOT_SUPPORTED")]
    assert both.data["reason"] == (f"The source has no purchasable supplies.; "
                                   f"The only supported way for {_NOODLES} is 到店.")
    assert both.data["fix"] == ("Set from to null or a store that sells this product.; "
                                "Use an offered purchase way or 购买.")
    _, error, mixed = decide(engine, "one-unserved", _buy((_MILK, "远程下单", None), (_NOODLES, "购买", soup)))
    assert error == "" and (mixed.status, mixed.error_code) == ("rejected", "ACTIVITY_PRECONDITION_UNMET")
    assert [(item["path"][-2], item["error_code"]) for item in mixed.data["items"]] == [
        (1, "ACTIVITY_PRECONDITION_UNMET")]
    assert mixed.data["next_step"] == f"Go to {soup}." and "orders" not in mixed.data
    assert _world_facts(engine) == before


def test_a_name_class_item_leads_the_merged_rejection_even_when_it_comes_second(engine_factory) -> None:
    """多件购买合并成一次拒绝时码取第一个名称类拒绝的码（名称类优先，3.1 第 5 条、3.2（d）；其余条件才报第一条）：第 1 项
    此刻服务不了（ACTIVITY_PRECONDITION_UNMET）、第 2 项没有这件商品（SUPPLY_NOT_FOUND）时，一次拒绝的码是 SUPPLY_NOT_FOUND、
    下一步取它的默认做法，items 按项序各写各的码；它是名称类拒绝，本秒前两次不计同刻轮数（3.4）。"""
    engine = _world(engine_factory, hour=11)
    rounds = engine.state.agents[1].same_moment_rounds
    soup = _place(engine, _SOUP)
    _, error, result = decide(engine, "unmet-then-missing",
                              _buy((_NOODLES, "购买", soup), ("不存在的商品", "远程下单", None)))
    assert error == "" and (result.status, result.error_code) == ("rejected", "SUPPLY_NOT_FOUND")
    assert [(item["path"][-2], item["error_code"]) for item in result.data["items"]] == [
        (0, "ACTIVITY_PRECONDITION_UNMET"), (1, "SUPPLY_NOT_FOUND")]
    assert result.data["next_step"] == refusal_for_code("SUPPLY_NOT_FOUND").next_step
    assert engine.state.agents[1].same_moment_rounds == rounds


def _listed_remote_rows(engine, round_id: str) -> list[dict]:
    """本轮列出的、能远程下单的商品绑定行（先浏览过，工具层列出集市与各家的供给）。"""
    observed = engine.observe(context(engine, round_id))
    return [row for row in observed["interaction_bindings"] if row["kind"] == "supply"
            and any(action["label"] == "远程下单" for action in row["actions"])]


def test_home_written_as_from_is_the_persons_home_where_nothing_is_sold(engine_factory) -> None:
    """from 写「家」「住处」时指本人的住处（3.1 第 2 条的「家」映射，来源约束同一口径）：按名写的纯牛奶写 from「家」或
    「住处」远程下单，以 SUPPLY_NOT_AT_STORE 拒绝，原因写「这里没有可买的商品」，候选是出售它的门店的完整标签（from 为
    null）；列出的标签写明了门店、from 却写「家」时，宿主与环境都以 SUPPLY_SOURCE_MISMATCH 拒绝，说明 from 是另一个地点。
    都不下单。"""
    engine = _world(engine_factory)
    before = _world_facts(engine)
    for word in ("家", "住处"):
        _, error, refused = decide(engine, f"from-{word}", _buy((_MILK, "远程下单", word)))
        assert error == "" and (refused.status, refused.error_code) == ("rejected", "SUPPLY_NOT_AT_STORE")
        [item] = refused.data["items"]
        assert (item["path"][-1], item["written"]) == ("from", word)
        assert refused.data["reason"] == "The source has no purchasable supplies."
        assert item["candidates"] and all(row["arguments"]["items"][0]["target"].endswith(f"出售的{_MILK}") and row["arguments"]["items"][0]["from"] is None
                                          for row in item["candidates"])
    assert _world_facts(engine) == before
    _browse(engine)
    listed = _listed_remote_rows(engine, "look-before-home")[0]["object"]
    before = _world_facts(engine)
    _, error, mismatch = decide(engine, "listed-from-home", _buy((listed, "远程下单", "家")))
    assert error.report.error_code == "SUPPLY_SOURCE_MISMATCH"
    assert [(item.path[-1], item.written) for item in error.report.items] == [("from", "家")]
    assert (mismatch.status, mismatch.error_code) == ("rejected", "SUPPLY_SOURCE_MISMATCH")
    store = listed.split("出售的", 1)[0]
    assert mismatch.data["reason"] == f"The target names {store}, but from names a different place."
    assert _world_facts(engine) == before


def test_a_listed_item_and_a_named_item_resolve_item_by_item_and_are_bought_together(engine_factory) -> None:
    """一次请求里一项照本轮列出的标签写、一项按名写（3.2（d）逐项解析）：列出的那一项照它的绑定行受理，只有按名写的那一项
    按名称与来源约束（from 写集市）解析；两项是集市里同一个卖家的供给，同一秒一起成单、都送到家，受理结果只为按名写的
    那一项记下归一。"""
    engine = _world(engine_factory)
    _browse(engine)
    sellers = {supply.supply_id: supply.seller_account_id for supply in engine.scenario.supplies}
    market: dict[str, list[str]] = {}
    for row in _listed_remote_rows(engine, "look-before-buying"):
        if row["object"].startswith(f"{_MARKET_NAME}出售的"):
            market.setdefault(sellers[row["object_id"]], []).append(row["object"])
    listed, other = next(labels for labels in market.values() if len(labels) > 1)[:2]
    written = other.split("出售的", 1)[1]
    _, error, bought = decide(engine, "listed-and-named", _buy((listed, "远程下单", None),
                                                                (written, "远程下单", _MARKET_NAME)))
    assert error == "" and (bought.status, bought.error_code) == ("completed", None)
    assert [(order["location_id"], order["fulfillment"]) for order in bought.data["orders"]] == [
        ("market", "delivery"), ("market", "delivery")]
    products = [engine.rules.items[order["product_id"]].name for order in bought.data["orders"]]
    assert products == [listed.split("出售的", 1)[1], written]
    assert [row["written"] for row in bought.data["resolved"]] == [written]


def _milk_supplies(engine) -> dict[str, object]:
    """各家门店出售的纯牛奶（门店身份 → 供给）。"""
    return {supply.location_id: supply for supply in engine.scenario.supplies
            if engine.rules.items[supply.product_id].name == _MILK}


def test_an_unservable_source_offers_the_same_goods_that_can_be_served_elsewhere(engine_factory) -> None:
    """M3 为空时附此刻能服务的同名候选，不限于 from 的范围（3.2（c）第 4 步）：好德的纯牛奶送不到家时写 from「好德」远程下单，
    以 ACTIVITY_PRECONDITION_UNMET 拒绝（「这里不在这家店的配送范围内」）；候选是别家能送到家的纯牛奶（完整标签、from 为
    null、远程下单，按预计送达排序），照填即受理。"""
    engine = _world(engine_factory)
    milk = _milk_supplies(engine)
    haode = milk[_HAODE]
    _retarget(engine, haode.supply_id, tuple(spot for spot in haode.delivery_destination_ids if spot != "home"))
    _, error, refused = decide(engine, "unservable-source", _buy((_MILK, "远程下单", _place(engine, _HAODE))))
    assert error == "" and (refused.status, refused.error_code) == ("rejected", "ACTIVITY_PRECONDITION_UNMET")
    assert refused.data["reason"] == "The current place is outside this store's delivery area."
    arrival = {store: delivery_expected_at(engine.semantics, supply, "home", engine.state.current_time)
               for store, supply in milk.items() if store != _HAODE}
    assert len(set(arrival.values())) == len(arrival) == 2
    [item] = refused.data["items"]
    assert [row["arguments"] for row in item["candidates"]] == [
        _buy((f"{_place(engine, store)}出售的{_MILK}", "远程下单", None))
        for store in sorted(arrival, key=arrival.get)]
    _, _, retried = decide(engine, "unservable-source-retry", _buy((item["candidates"][0]["arguments"]["items"][0]["target"], "远程下单", None)))
    assert retried.status == "completed"


def test_goods_sold_by_several_stores_point_to_the_store_with_the_shortest_route(engine_factory) -> None:
    """「购买」两者都不行而几家门店都卖（3.5「收敛」第 2 条）：人在紫薇公园，三家的纯牛奶都送不到这里，以
    ACTIVITY_PRECONDITION_UNMET 拒绝，next_step 写从这里出发路线最短的出售门店（不是身份在前、也不是路线最长的那家）；到店
    时原因同样说最近的是这一家。"""
    engine = _world(engine_factory)
    milk = _milk_supplies(engine)
    for supply in milk.values():
        _retarget(engine, supply.supply_id, tuple(spot for spot in supply.delivery_destination_ids if spot != _PARK))
    place(engine, 1, _PARK)
    paths = shortest_paths(engine.scenario.routes, _PARK)
    nearest = min(milk, key=lambda store: paths[store][0])
    assert nearest not in {min(milk), max(milk, key=lambda store: paths[store][0])}
    _, error, system = decide(engine, "system-several", _buy((_MILK, "购买", None)))
    assert error == "" and (system.status, system.error_code) == ("rejected", "ACTIVITY_PRECONDITION_UNMET")
    assert system.data["reason"] == f"The caller is not at a selling store and delivery cannot reach {_place(engine, _PARK)}."
    assert system.data["next_step"] == f"Go to {_place(engine, nearest)}."
    _, _, pickup = decide(engine, "pickup-several", _buy((_MILK, "到店", None)))
    assert pickup.data["reason"] == f"Pickup requires the caller at a selling store; the nearest is {_place(engine, nearest)}."
    assert pickup.data["next_step"] == system.data["next_step"]


def test_home_words_in_from_keep_meaning_home_under_lossless_spellings(engine_factory) -> None:
    """@brief from 写「家」「住处」的无损写法仍指本人住处（接口合同：地点显示名按精确、无损归一或去掉「地点：」前缀比对，
    「家」「住处」指本人住处；个人事务与工作安排规格 5.2：来源的合法写法以现行实现为准）：带「地点：」前缀或带空白的
    写法按名买纯牛奶，与照写「家」一样以 SUPPLY_NOT_AT_STORE 拒绝、原因写「这里没有可买的商品」，而不是不认识来源的
    SOURCE_NOT_FOUND；列出的标签写明了门店、from 写「地点：家」时，宿主与环境都以 SUPPLY_SOURCE_MISMATCH 拒绝。都不下单。"""
    engine = _world(engine_factory)
    before = _world_facts(engine)
    for index, word in enumerate(("地点：家", " 住 处")):
        _, error, refused = decide(engine, f"home-spelling-{index}", _buy((_MILK, "远程下单", word)))
        assert error == "" and (refused.status, refused.error_code) == ("rejected", "SUPPLY_NOT_AT_STORE"), word
        [item] = refused.data["items"]
        assert (item["path"][-1], item["written"]) == ("from", word)
        assert refused.data["reason"] == "The source has no purchasable supplies."
        assert item["candidates"] and all(row["arguments"]["items"][0]["from"] is None for row in item["candidates"])
    assert _world_facts(engine) == before
    _browse(engine)
    listed = _listed_remote_rows(engine, "look-before-home-spelling")[0]["object"]
    before = _world_facts(engine)
    _, error, mismatch = decide(engine, "listed-from-home-spelling", _buy((listed, "远程下单", "地点：家")))
    assert error != "" and error.report.error_code == "SUPPLY_SOURCE_MISMATCH"
    assert [(item.path[-1], item.written) for item in error.report.items] == [("from", "地点：家")]
    assert (mismatch.status, mismatch.error_code) == ("rejected", "SUPPLY_SOURCE_MISMATCH")
    assert _world_facts(engine) == before


def test_an_unknown_source_near_a_place_without_supplies_never_offers_that_place(engine_factory) -> None:
    """@brief SOURCE_NOT_FOUND 的候选是最相近的门店名与 App 称呼，一个也不相近时是此刻能买到这件商品的来源（接口合同
    「活动受理拒绝码」；一日运行保真规格 3.3 解析规则 1）——候选来源只取有供给的门店，与查询一侧同一口径：from 写成与
    一处没有任何供给的地点（住处、办公室、健身房、公园）相近的写法「<地点>旁」，宿主放行、环境以 SOURCE_NOT_FOUND
    拒绝，一至三条候选只换 from，来源都是有供给的门店的显示名，那处地点不在其中；不下单。每处地点各用一个引擎。"""
    probe = _world(engine_factory)
    stores = {supply.location_id for supply in probe.scenario.supplies}
    selling = {item.name for item in probe.scenario.locations if item.location_id in stores}
    bare = [item for item in probe.scenario.locations if item.location_id not in stores]
    assert {"home", "office", _PARK} <= {item.location_id for item in bare}
    for index, location in enumerate(bare):
        engine = probe if index == 0 else _world(engine_factory)
        before, written = _world_facts(engine), f"{location.name}旁"
        _, error, result = decide(engine, f"near-storeless-{index}", _buy((_MILK, "远程下单", written)))
        assert error == "" and (result.status, result.error_code) == ("rejected", "SOURCE_NOT_FOUND"), written
        [item] = result.data["items"]
        assert (item["path"][-1], item["written"]) == ("from", written)
        rows = [candidate["arguments"]["items"][0] for candidate in item["candidates"]]
        sources = [row["from"] for row in rows]
        assert 1 <= len(sources) <= 3 and location.name not in sources and set(sources) <= selling, (written, sources)
        assert all((row["target"], row["way"], row["to"]) == (_MILK, "远程下单", None) for row in rows), written
        assert _world_facts(engine) == before
