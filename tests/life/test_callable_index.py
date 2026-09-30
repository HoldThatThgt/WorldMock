"""工具层只列名称的可调用索引 callable_index（docs/dev/SPEC-dayrun-fidelity.md 5.2，裁决 S5；验收台账「呈现」的
「索引名称都可按名调用」）。

全部经真实 LifeEngine、SQLite 与 LifeAgent：索引里的每个名称（全部生活活动名称、内容与商品各品类的示例名）都能按名调用——
各在一份已提交状态的独立副本上单独提交，宿主本地校验放行，经宿主派发交给环境后被受理并落到同名对象（商品名有几家门店都能
送到时按请求级 prefer 的缺省选预计送达最早的一家，3.5）；索引只含此地此刻可做的
生活活动（地点类不符的不在其中，与生活活动窗口资格段保留的条目逐项相同），内容与商品只给品类计数与至多 3 个示例名；商品取
此刻从这里能得到的（门店开着、有货、能送到这里或人就在店里）；有效菜单不许的动作使对应部分为空；索引不是目标，不计入单次
呈现上限、不写呈现留痕。容量计数含索引见 tests/integration/test_seven_layer_request.py，Schema 见 test_prompt_layers.py。
"""

from __future__ import annotations

import shutil
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from agentsociety2.agent.base import ReactDecision
from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context, drive, stage_day_plan, start_activity, start_arguments
from tests.life.test_presentation_fidelity import _allowed, _hashed, _stage
from tests.life.test_tool_layer import _branch, _host, _without_routes
from worldmock.footprints.models import EventDraft
from worldmock.life.basket import delivery_expected_at
from worldmock.life.callable_index import MAX_EXAMPLES, obtainable_here
from worldmock.life.candidates import tiebreak_digest
from worldmock.life.discovery import DiscoveryContext
from worldmock.life.engine import LifeEngine
from worldmock.life.interaction_menu import NAMED_SUPPLY_KIND, WAYS
from worldmock.life.models import StartActivityRequest
from worldmock.life.name_resolution import name_book, resolve_start
from worldmock.life.readable import BROWSE_LABELS, count_phrase

#: 场景首日；12:30 六家门店都开着，09:30 三家饭店还没开门，00:30 全部关着。
_DAY = "2026-01-05"
#: 只能到店取餐、10:00 才开门的饭店，与门店商品最多的集市。
_RESTAURANT, _MARKET = "poi-3538682404", "market"


def _at(engine: LifeEngine, hour: int, place: str, minute: int = 30) -> None:
    """把没有活动的隔离运行置于场景首日 hour:minute、地点 place（经存储提交，版本加一）。"""
    moment = datetime(2026, 1, 5, hour, minute, tzinfo=ZoneInfo(engine.scenario.timezone))
    _stage(engine, int(moment.timestamp()), place)


def _index(engine: LifeEngine, label: str) -> tuple[dict, dict]:
    """一次真实观察：（观察，工具层的可调用索引）。"""
    observed = engine.observe(context(engine, label))
    return observed, observed["tool_layer"]["callable_index"]


def _products(engine: LifeEngine) -> dict[str, object]:
    """商品身份 → 商品。"""
    return {item.product_id: item for item in engine.scenario.products}


def _number(row: dict) -> int:
    """品类行的计数「N 个」中的 N。"""
    return int(row["count"].removesuffix(" 个"))


def _content_way(observed: dict, name: str) -> tuple[str, str]:
    """内容或游戏名称此刻会列出的（动词，方式）：取本轮私有绑定或目录绑定里去掉类别前缀就是这个名称的那一个对象。"""
    rows = [row for row in (*observed["interaction_bindings"], *observed["catalog_bindings"])
            if row["kind"] in {"content", "game"} and row["object"].split("：", 1)[1] == name]
    [row] = rows
    way = row["way"] if "way" in row else row["actions"][0]["label"]
    return WAYS[way][0], way


def _purchase_way(engine: LifeEngine, name: str) -> str:
    """商品名按名取得的方式：人就在出售它的门店里写「到店」，否则写「远程下单」。"""
    products, here = _products(engine), engine.state.agents[1].location_id
    in_store = any(products[supply.product_id].name == name and supply.location_id == here
                   and "pickup" in supply.fulfillment_modes for supply in engine.scenario.supplies)
    return "到店" if in_store else "远程下单"


def _calls(engine: LifeEngine, observed: dict, index: dict, sections: tuple[str, ...],
           per_category: int = MAX_EXAMPLES) -> list[tuple]:
    """索引里名称按名调用时写的（部分，名称，动词，方式）：生活活动全部写「进行」，内容与游戏写它此刻会列出的方式，商品写
    到店或远程下单；内容与商品每个品类取前 per_category 个示例名（缺省为全部示例）。"""
    calls = []
    if "lifestyle" in sections:
        calls.extend(("lifestyle", name, "interact", "进行") for name in index["lifestyle"])
    if "contents" in sections:
        calls.extend(("contents", name, *_content_way(observed, name))
                     for row in index["contents"] for name in row["examples"][:per_category])
    if "goods" in sections:
        calls.extend(("goods", name, "obtain", _purchase_way(engine, name))
                     for row in index["goods"] for name in row["examples"][:per_category])
    return calls


def _submitted(engine: LifeEngine, tmp_path, monkeypatch, label: str, index: dict, call: tuple):
    """在已提交状态的独立副本上开一轮：本轮观察的索引与列出时相同；宿主本地校验放行这一项，经宿主派发交给环境，结算后
    返回（结果，开始的活动的目标身份；取得没有活动时为 None）。"""
    _, name, verb, way = call
    branch = _branch(engine, tmp_path, monkeypatch, label)
    try:
        decision = context(branch, label)
        branch.begin_decision_round([decision])
        seen = branch.observe(decision)
        assert seen["tool_layer"]["callable_index"] == index
        person = _host(branch, decision, seen)
        arguments = start_arguments([name], [verb], [way])
        assert person.validate_react_decisions([ReactDecision("", "start_activity", arguments, "")], False) == "", call
        drive(person.dispatch_react_tool("start_activity", arguments))
        [result] = branch.commit_decision_round(decision.round_id)
        target = branch.state.activities[result.activity_id].target_id if result.activity_id else None
        return result, target
    finally:
        branch.close()
        shutil.rmtree(branch.store.run_dir)


def _assert_every_name_is_called(engine: LifeEngine, tmp_path, monkeypatch, label: str,
                                 sections: tuple[str, ...], per_category: int = MAX_EXAMPLES) -> int:
    """逐个按名调用本次观察索引里（sections 各部分的）名称，断言都被受理并落到同名对象；返回调用的名称数。"""
    observed, index = _index(engine, f"{label}-listing")
    lifestyle = {item.activity_id: item.name for item in engine.lifestyle_items}
    titles = {**engine.rules.content_titles, **{item.game_id: item.title for item in engine.scenario.games}}
    calls = _calls(engine, observed, index, sections, per_category)
    for number, call in enumerate(calls):
        result, target = _submitted(engine, tmp_path, monkeypatch, f"{label}-{number}", index, call)
        section, name = call[0], call[1]
        assert result.status in {"in_progress", "completed"} and result.error_code is None, (call, result.message)
        if section == "lifestyle":
            assert lifestyle[target] == name
        elif section == "contents":
            assert titles[target] == name
        else:
            _assert_goods_choice(engine, result, name, call[3])
    return len(calls)


def _assert_goods_choice(engine: LifeEngine, result, name: str, way: str) -> None:
    """商品名按名取得落到出售这个商品的门店（details.resolved 写 {written: 名称, as: <门店>出售的<名称>}，与订单的门店
    一致）：到店落在人所在的这家店；远程下单落在能送到这里的供给中预计送达最早的一家（请求级 prefer 为空按最快，
    docs/dev/SPEC-dayrun-fidelity.md 3.5「收敛」第 5 条），几家都能送到时 details.notes 写明按最快选了哪家，只有一家时
    不写；同名多家不以 SUPPLY_NAME_AMBIGUOUS 拒绝。"""
    [purchase] = [row for row in result.data["resolved"] if row["written"] == name]
    [order] = result.data["orders"]
    places = {item.location_id: item.name for item in engine.scenario.locations}
    assert purchase["as"] == f"{places[order['location_id']]}出售的{name}", (purchase, order)
    notes = [note for note in result.data.get("notes", []) if note.startswith(f"「{name}」")]
    here, now = engine.state.agents[1].location_id, engine.state.current_time
    if way == "到店":
        assert (order["location_id"], order["fulfillment"], notes) == (here, "pickup", []), order
        return
    products = _products(engine)
    expected = {supply.supply_id: delivery_expected_at(engine.semantics, supply, here, now)
                for supply in engine.scenario.supplies
                if products[supply.product_id].name == name and "delivery" in supply.fulfillment_modes}
    served = {supply_id: moment for supply_id, moment in expected.items() if moment is not None}
    assert order["supply_id"] in served and order["expected_at"] == min(served.values()), (order, served)
    if len(served) > 1:
        [note] = notes
        assert note.startswith(f"「{name}」有 {len(served)} 处都能远程下单，按最快选了{purchase['as']}（预计 "), note
    else:
        assert notes == [], notes


def test_every_name_in_the_index_is_callable_by_name_and_accepted(engine_factory, tmp_path, monkeypatch) -> None:
    """性质测试（真实场景的一次观察）：12:30 在家，索引里的全部名称——107 个生活活动、内容各品类与商品各品类的示例名——
    逐个写名称单独提交：宿主本地校验都放行（名称按统一名称解析归一唯一，生活活动与内容补回类别前缀，商品按指名购买交给
    环境），环境都受理并落到同名对象（开始的活动的目标就是这个名称的生活活动或内容，取得的门店商品标签以这个商品名结尾）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _at(engine, 12, "home")
    _, index = _index(engine, "count")
    assert len(index["lifestyle"]) == len(_allowed(engine, "home")) == 107 and index["contents"] and index["goods"]
    called = _assert_every_name_is_called(engine, tmp_path, monkeypatch, "home", ("lifestyle", "contents", "goods"))
    examples = sum(len(row["examples"]) for part in ("contents", "goods") for row in index[part])
    assert called == 107 + examples


def test_goods_named_in_a_store_are_bought_in_store(engine_factory, tmp_path, monkeypatch) -> None:
    """人在门店里（12:30 在集市）：索引的商品示例都是这家店出售的（写「到店」），每个商品品类的第一个示例名按名调用，宿主
    放行、环境受理并当场成交，落到这家店出售的同名商品（全部名称的性质测试在家里那一例；集市的观察要逐件试算列出的
    商品目标，这里每类取一个）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _at(engine, 12, _MARKET)
    _, index = _index(engine, "store")
    assert all(_purchase_way(engine, name) == "到店" for row in index["goods"] for name in row["examples"])
    called = _assert_every_name_is_called(engine, tmp_path, monkeypatch, "store", ("goods",), 1)
    assert called == len(index["goods"]) == 13


def test_a_name_several_stores_deliver_is_called_by_name_and_chosen_by_prefer(engine_factory, tmp_path,
                                                                              monkeypatch) -> None:
    """同一商品名有几家门店都能送到（12:30 在家，集市到家的配送路线断了之后，索引的商品只剩另两家门店送得到的纯牛奶）：
    这个示例名按名调用照样被宿主放行、环境受理，不以 SUPPLY_NAME_AMBIGUOUS 拒绝；请求级 prefer 为空按最快，落到预计送达
    最早的那家，details.notes 写明按最快选了它（docs/dev/SPEC-dayrun-fidelity.md 3.5「收敛」第 5 条）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _at(engine, 12, "home")
    _without_routes(engine, _MARKET, "home")
    _, index = _index(engine, "several-stores")
    assert [name for row in index["goods"] for name in row["examples"]] == ["纯牛奶"]
    products, now = _products(engine), engine.state.current_time
    stores = {supply.location_id for supply in engine.scenario.supplies if products[supply.product_id].name == "纯牛奶"
              and delivery_expected_at(engine.semantics, supply, "home", now) is not None}
    assert len(stores) == 2
    assert _assert_every_name_is_called(engine, tmp_path, monkeypatch, "several-stores", ("goods",)) == 1


def test_every_index_name_resolves_by_name_in_the_host_at_several_places(engine_factory) -> None:
    """同一性质在别的地点与时刻的名称解析这一层（宿主本地校验，环境受理用同一次解析）：09:30 在家、12:30 在办公室、在饭店、
    13:30 在集市，各次观察索引里的全部名称写进 start_activity 都被宿主放行，并且唯一解析到索引所指的对象——生活活动与内容
    补回类别前缀就是这个名称，商品名是交给环境按名称解析的指名购买。"""
    engine = engine_factory()
    stage_day_plan(engine)
    checked: Counter = Counter()
    for hour, place in ((9, "home"), (12, "office"), (12, _RESTAURANT), (13, _MARKET)):
        _at(engine, hour, place)
        decision = context(engine, f"host-{place}")
        observed = engine.observe(decision)
        person = _host(engine, decision, observed)
        index = observed["tool_layer"]["callable_index"]
        for section, name, verb, way in _calls(engine, observed, index, ("lifestyle", "contents", "goods")):
            arguments = start_arguments([name], [verb], [way])
            decisions = [ReactDecision("", "start_activity", arguments, "")]
            assert person.validate_react_decisions(decisions, False) == "", (place, name, way)
            [row] = resolve_start(name_book(observed), StartActivityRequest.model_validate(arguments)).rows
            if section == "goods":
                assert (row["kind"], row["object"]) == (NAMED_SUPPLY_KIND, name)
            else:
                assert row["object"].split("：", 1)[1] == name and row["kind"] != NAMED_SUPPLY_KIND
            checked[place, section] += 1
    assert all(checked[place, section] for place in ("home", "office", _RESTAURANT, _MARKET)
               for section in ("lifestyle", "contents", "goods"))


def test_the_index_leaves_out_activities_excluded_by_location_kind(engine_factory) -> None:
    """索引只列此地可做的生活活动：在家、在办公室、在集市，名称恰是地点类允许的全部条目（按决胜键排列），就是生活活动
    窗口资格段保留的条目（presented 与 deferred）；以 ELIGIBILITY_LOCATION_KIND_MISMATCH 排除的条目一个都不在索引里，
    窗口里的条目都在索引里。在家是 107 个名称、415 字（规格 5.2 的估算）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    names = {item.activity_id: item.name for item in engine.lifestyle_items}
    sizes = {}
    for place in ("home", "office", _MARKET):
        _at(engine, 12, place)
        _, index = _index(engine, f"kinds-{place}")
        allowed = _allowed(engine, place)
        assert index["lifestyle"] == [names[item] for item in _hashed(engine, allowed, _DAY)]
        window = engine._menu_windows(engine.state.agents[1])["lifestyle"]
        mismatched = {names[item.object_id] for item in window.excluded
                      if item.reason == "ELIGIBILITY_LOCATION_KIND_MISMATCH"}
        assert mismatched and not mismatched & set(index["lifestyle"])
        assert set(index["lifestyle"]) == {names[item] for item in (*window.presented, *window.deferred)}
        assert {row["name"] for row in window.items} <= set(index["lifestyle"])
        sizes[place] = (len(index["lifestyle"]), sum(len(name) for name in index["lifestyle"]))
    assert sizes == {"home": (107, 415), "office": (20, 85), _MARKET: (92, 373)}


def _deny(engine: LifeEngine, *actions: str) -> None:
    """把家的地点菜单收窄为不含 actions（权威菜单随场景重算）。"""
    menus = engine.scenario.interaction_menus_by_location_kind.copy()
    home = menus["home"]
    menus["home"] = home.model_copy(update={"allowed_actions": tuple(
        action for action in home.allowed_actions if action not in actions)})
    engine.scenario = engine.scenario.model_copy(update={"interaction_menus_by_location_kind": menus})


def test_the_interaction_menu_decides_what_can_be_done_here(engine_factory) -> None:
    """此地不许的事不在索引里（与工具层「菜单外的能力不列出」同一有效菜单）：家的菜单去掉做生活活动与买东西，生活活动与商品
    两部分为空、内容照旧；再去掉观看，内容只剩可玩游戏（游戏会话只能观看）；再去掉游玩，内容也为空。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _at(engine, 12, "home")
    _, full = _index(engine, "menu-full")
    _deny(engine, "do", "purchase")
    _, narrowed = _index(engine, "menu-narrowed")
    assert (narrowed["lifestyle"], narrowed["goods"], narrowed["contents"]) == ([], [], full["contents"])
    _deny(engine, "watch")
    _, playable = _index(engine, "menu-playable")
    games = {item.title for item in engine.scenario.games}
    [row] = playable["contents"]
    assert (row["category"], row["count"]) == (BROWSE_LABELS["game_session"], count_phrase(len(games)))
    assert set(row["examples"]) <= games and len(row["examples"]) == MAX_EXAMPLES
    _deny(engine, "play")
    assert _index(engine, "menu-none")[1] == {"lifestyle": [], "contents": [], "goods": []}


def _set_stock(engine: LifeEngine, supply_ids: set[str], stock: int) -> None:
    """把几件供给的库存改为 stock，经存储提交（版本加一）。"""
    staged = engine.state.model_copy(deep=True)
    for supply_id in supply_ids:
        staged.supply_stock[supply_id] = stock
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_stock_staged"})])
    engine.state = staged


def _category_of(index: dict, name: str) -> dict:
    """商品名所在的商品品类行。"""
    return next(row for row in index["goods"] if name in row["examples"])


def test_goods_are_only_what_can_be_obtained_from_here_now(engine_factory) -> None:
    """商品只取此刻从这里能得到的：00:30 门店都关着，商品部分为空（生活活动与内容照旧）；12:30 在家都能送到，某件示例商品的
    全部供给卖完后它离开索引、所在品类计数减一；集市到家的配送路线断了之后，只剩另两家门店送得到的纯牛奶。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _at(engine, 0, "home")
    _, night = _index(engine, "night")
    assert night["goods"] == [] and night["lifestyle"] and night["contents"]
    _at(engine, 12, "home")
    _, noon = _index(engine, "noon")
    name = next(name for row in noon["goods"] for name in row["examples"] if name != "纯牛奶")
    before = _category_of(noon, name)
    products = _products(engine)
    _set_stock(engine, {item.supply_id for item in engine.scenario.supplies
                        if products[item.product_id].name == name}, 0)
    _, sold_out = _index(engine, "sold-out")
    after = next(row for row in sold_out["goods"] if row["category"] == before["category"])
    assert _number(after) == _number(before) - 1 and name not in after["examples"]
    assert [row for row in sold_out["goods"] if row["category"] != before["category"]] == [
        row for row in noon["goods"] if row["category"] != before["category"]]
    _without_routes(engine, _MARKET, "home")
    _, cut = _index(engine, "no-market-route")
    [dairy] = cut["goods"]
    assert (dairy["count"], dairy["examples"]) == (count_phrase(1), ["纯牛奶"])
    assert {item.location_id for item in engine.scenario.supplies if products[item.product_id].name == "纯牛奶"} == {
        _MARKET, "poi-3538682402", "poi-3538763552"}


def test_a_pickup_only_supply_is_obtainable_only_in_its_open_store(engine_factory) -> None:
    """只能到店取的饭店供给：人在这家饭店且它开着（12:30）时此刻能得到；09:30 还没开门、或人在家（到不了店也送不过来）
    时都不能。计数按名称：同名的餐由集市送到饭店，所以饭店开门前后「Prepared meal」都是全部 30 个餐名。"""
    engine = engine_factory()
    stage_day_plan(engine)
    products = _products(engine)
    supply = next(item for item in engine.scenario.supplies if item.location_id == _RESTAURANT)
    meals = {products[item.product_id].name for item in engine.scenario.supplies
             if products[item.product_id].category_id == "prepared-meal"}

    def here() -> bool:
        """@brief 此刻从人物所在地点能否得到这件供给。"""
        found = DiscoveryContext(engine.state, engine.scenario, engine.semantics, engine.state.agents[1])
        return obtainable_here(found, supply)

    _at(engine, 9, _RESTAURANT)
    closed = here()
    _, early = _index(engine, "restaurant-early")
    _at(engine, 12, _RESTAURANT)
    opened = here()
    _, noon = _index(engine, "restaurant-noon")
    _at(engine, 13, "home")
    assert (closed, opened, here()) == (False, True, False)
    for index in (early, noon):
        row = next(row for row in index["goods"] if row["category"] == "Prepared meal")
        assert row["count"] == count_phrase(len(meals)) and len(meals) == 30
    assert products[supply.product_id].name in {products[item.product_id].name for item in engine.scenario.supplies
                                                if item.location_id == _MARKET}


def _order(engine: LifeEngine):
    """决胜键 sha256(场景|人物|日期|对象)。"""
    return lambda identity: tiebreak_digest(engine.scenario.scenario_id, 1, _DAY, identity)


def test_counts_and_up_to_three_examples_per_category(engine_factory) -> None:
    """品类计数与示例：内容品类写中文名（游戏会话与可玩游戏同为「游戏」），计数是可写名称的个数（电影 90、游戏 30 + 80）；
    商品品类写目录品类名，计数是此刻能得到的不同商品名；每类至多 3 个不重复的示例，先取本轮没有列为目标的，同组按决胜键；
    生活活动名称按同一决胜键排列。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _at(engine, 12, "home")
    observed, index = _index(engine, "shape")
    titles = engine.rules.content_titles
    kinds = Counter(BROWSE_LABELS[item.kind] for item in engine.scenario.contents if item.item_id in titles)
    kinds[BROWSE_LABELS["game_session"]] += len(engine.scenario.games)
    assert [row["category"] for row in index["contents"]] == sorted(kinds)
    assert {row["category"]: _number(row) for row in index["contents"]} == dict(kinds)
    assert kinds["电影"] == 90 and kinds["游戏"] == 110
    listed = {row["object"].split("：", 1)[1] for row in observed["interaction_bindings"]
              if row["kind"] in {"content", "game"}}
    order = _order(engine)
    identities = {titles[item.item_id]: item.item_id for item in engine.scenario.contents}
    identities.update({item.title: item.game_id for item in engine.scenario.games})
    categories = {titles[item.item_id]: BROWSE_LABELS[item.kind] for item in engine.scenario.contents}
    categories.update({item.title: BROWSE_LABELS["game_session"] for item in engine.scenario.games})
    for row in index["contents"]:
        members = sorted((title for title, label in categories.items() if label == row["category"]),
                         key=lambda title: (title in listed, order(identities[title])))
        assert row["examples"] == members[:MAX_EXAMPLES]
        assert len(set(row["examples"])) == len(row["examples"]) <= MAX_EXAMPLES
    products = _products(engine)
    found = DiscoveryContext(engine.state, engine.scenario, engine.semantics, engine.state.agents[1])
    goods: dict[str, set[str]] = {}
    for supply in engine.scenario.supplies:
        if obtainable_here(found, supply):
            product = products[supply.product_id]
            name = engine.semantics.catalog.category_contract.require_category(product.category_id).name
            goods.setdefault(name, set()).add(product.name)
    assert {row["category"]: _number(row) for row in index["goods"]} == {
        key: len(value) for key, value in goods.items()}
    assert [row["category"] for row in index["goods"]] == sorted(goods)
    assert all(set(row["examples"]) <= goods[row["category"]] and 1 <= len(row["examples"]) <= MAX_EXAMPLES
               for row in index["goods"])
    lifestyle = {item.name: item.activity_id for item in engine.lifestyle_items}
    assert index["lifestyle"] == sorted(index["lifestyle"], key=lambda name: order(lifestyle[name]))
    assert index["lifestyle"] != sorted(index["lifestyle"], key=lambda name: lifestyle[name])


def test_examples_prefer_names_not_already_listed_as_targets(engine_factory) -> None:
    """示例名先取本轮没有列为目标的：在集市里，本店商品作为商品目标列出（至多 20 个），没列出的名称够 3 个的商品品类，示例
    都不是列出的商品；电影与游戏的示例也都不是本轮列出的内容或游戏目标。只有一个名称的品类（字幕体验）照样给出它。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _at(engine, 12, _MARKET)
    observed, index = _index(engine, "unlisted")
    listed = [row for row in observed["interaction_bindings"] if row["kind"] == "supply"]
    products = _products(engine)
    listed_names = {products[engine.rules.supplies[row["object_id"]].product_id].name for row in listed}
    assert listed_names
    wide = [row for row in index["goods"] if _number(row) - len(listed_names) >= MAX_EXAMPLES]
    assert wide and all(not set(row["examples"]) & listed_names for row in wide)
    shown = {row["object"].split("：", 1)[1] for row in observed["interaction_bindings"]
             if row["kind"] in {"content", "game"}}
    large = [row for row in index["contents"] if row["category"] in {"电影", "游戏"}]
    assert shown and len(large) == 2 and all(not set(row["examples"]) & shown for row in large)
    single = next(row for row in index["contents"] if row["category"] == BROWSE_LABELS["subtitle_experience"])
    assert (single["count"], single["examples"]) == (count_phrase(1), ["Sintel"])


def test_names_shared_by_two_contents_are_not_given_as_examples(engine_factory, monkeypatch) -> None:
    """内容或游戏的名称与另一件同名时，只写名称按名调用会有歧义（宿主报 NAME_AMBIGUOUS），这样的名称不作示例：把一部电影
    改成与游戏示例同名后，两边的示例都不再有这个名称，计数照旧。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _at(engine, 12, "home")
    _, index = _index(engine, "shared-before")
    game = next(row for row in index["contents"] if row["category"] == BROWSE_LABELS["game_session"])
    film_row = next(row for row in index["contents"] if row["category"] == "电影")
    shared = game["examples"][0]
    film = next(item.item_id for item in engine.scenario.contents
                if engine.rules.content_titles[item.item_id] == film_row["examples"][0])
    monkeypatch.setattr(engine.rules, "content_titles", {**engine.rules.content_titles, film: shared})
    ctx = context(engine, "shared-after")
    engine.begin_decision_round([ctx])
    seen = engine.observe(ctx)
    after = seen["tool_layer"]["callable_index"]
    assert all(shared not in row["examples"] for row in after["contents"])
    assert [row["count"] for row in after["contents"]] == [row["count"] for row in index["contents"]]
    assert all(len(row["examples"]) == min(_number(row), MAX_EXAMPLES) for row in after["contents"])
    person = _host(engine, ctx, seen)
    verdict = person.validate_react_decisions([ReactDecision("", "start_activity", start_arguments(
        [shared], ["interact"], ["观看"]), "")], False)
    assert verdict.report.error_code == "NAME_AMBIGUOUS"


def test_the_index_is_not_a_target_and_the_presentation_cap_ignores_it(engine_factory) -> None:
    """索引不是候选条目：在家 107 个生活活动名称远超单次呈现上限 20，工具层每类目标仍不超过上限，生活活动目标恰是窗口里的
    条目；呈现留痕只记进了窗口的对象，索引里没进窗口的名称不留痕，时钟推进后 last_presented_at 也只有窗口里的对象。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _at(engine, 12, "home")
    limit = engine.scenario.candidate_presentation_limit
    decision = context(engine, "cap")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    index = observed["tool_layer"]["callable_index"]
    assert len(index["lifestyle"]) > limit
    kinds = Counter(row["kind"] for row in observed["interaction_bindings"])
    assert all(kinds[kind] <= limit for kind in ("lifestyle", "content", "game", "supply"))
    windows = engine._menu_windows(engine.state.agents[1])
    presented = {item for window in windows.values() for item in window.presented}
    supplies = {row["object_id"] for row in observed["interaction_bindings"] if row["kind"] == "supply"}
    stamps = engine._pending_presentation[1].stamps
    assert set(stamps) == presented | supplies
    names = {item.name: item.activity_id for item in engine.lifestyle_items}
    unshown = {names[name] for name in index["lifestyle"]} - set(windows["lifestyle"].presented)
    assert len(unshown) == len(index["lifestyle"]) - len(windows["lifestyle"].presented) > 0
    assert not unshown & set(stamps)
    shown = [row["object_id"] for row in observed["interaction_bindings"] if row["kind"] == "lifestyle"]
    assert shown == list(windows["lifestyle"].presented) and len(shown) <= limit
    engine.submit_action(DirectToolCall(decision, "action-cap", "start_activity", start_activity("等待", "自己")))
    assert engine.commit_decision_round("cap")[0].status == "in_progress"
    advance_next(engine)
    recorded = set(engine.state.presentation_state[1])
    assert presented <= recorded and not unshown & recorded
