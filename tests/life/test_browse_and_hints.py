"""浏览结果与下一步提示看可达性（docs/dev/SPEC-dayrun-fidelity.md 5.3，裁决 D17、S9；验收台账「浏览与提示」）。

全部经真实引擎：浏览结果在进度层列完整标签「<门店>出售的<商品>」，只是名称；离开浏览地点后商品目标照常从工具层消失，
人物凭记忆层里的完整标签经指名购买路径下单；到店与远程下单被拒时的下一步只指向从此地可达的地点（直达「去」、经别处
「可经……前往」、到不了「这里去不了那家店」）；「送达地不对」与「配送路线缺失」是两个未满足条件、两句原因；不可达的
路线照旧不列出。路线与配送目的地的改动直接换掉引擎、语义层与规则共用的运行时场景（地图文件本身不动）。
"""

from __future__ import annotations

import dataclasses

from tests.life.conftest import (activity_name, conclude_pending, context, last_result, progress_calls,
                                 stage_day_plan, start_activity, start_request, target_actions, tool_targets)
from tests.life.test_commerce import _choice, _stage_open
from tests.life.test_same_moment_decisions import _act, _finish
from worldmock.footprints.models import EventDraft
from worldmock.life.engine import LifeEngine
from worldmock.life.guidance import DELIVERY_DESTINATION_REASON, DELIVERY_ROUTE_REASON
from worldmock.life.wayfinding import UNREACHABLE_STORE_STEP

_UNMET = "ACTIVITY_PRECONDITION_UNMET"
#: 只能到店取餐的饭店（菜饭骨头汤）与它出售的鸡汤面。
_STORE, _MEAL_SUPPLY = "poi-3538660453", "supply-poi-3538660453-meal-0018"
#: 张江日用集市（配送取货点）出售、能配送的一件食品；紫薇公园是能送达的一处地点。
_PARK = "poi-337936012"


def _places(engine: LifeEngine) -> dict[str, str]:
    """地点身份 → 场景显示名。"""
    return {item.location_id: item.name for item in engine.scenario.locations}


def _label(engine: LifeEngine, supply_id: str) -> str:
    """供给的完整标签「<门店>出售的<商品>」。"""
    supply = engine.rules.supplies[supply_id]
    return f"{_places(engine)[supply.location_id]}出售的{engine.rules.items[supply.product_id].name}"


def _market_delivery_supply(engine: LifeEngine) -> str:
    """集市出售、支持配送的一条供给（按身份取第一条，确定性）。"""
    return min(item.supply_id for item in engine.scenario.supplies
               if item.location_id == "market" and "delivery" in item.fulfillment_modes)


def _rescenario(engine: LifeEngine, routes=None, supplies=None) -> None:
    """换掉引擎、语义层与规则共用的运行时场景中的路线或供给（同一份场景对象，三处读到的一致）。"""
    update = {key: value for key, value in (("routes", routes), ("supplies", supplies)) if value is not None}
    scenario = engine.scenario.model_copy(update=update)
    engine.scenario = scenario
    engine.semantics = dataclasses.replace(engine.semantics, scenario=scenario)
    engine.rules.scenario = scenario
    engine.rules.supplies = {item.supply_id: item for item in scenario.supplies}


def _block(engine: LifeEngine, blocked) -> None:
    """把 blocked(route) 为真的冻结路线改成不可达（两种出行方式各自判定）。"""
    _rescenario(engine, routes=tuple(
        route.model_copy(update={"status": "unreachable", "distance_meters": None, "duration_seconds": None,
                                 "fare_fen": None}) if blocked(route) else route
        for route in engine.scenario.routes))


def _retarget(engine: LifeEngine, supply_id: str, destinations: tuple[str, ...]) -> None:
    """把一条供给的可配送目的地换成 destinations。"""
    _rescenario(engine, supplies=tuple(
        item.model_copy(update={"delivery_destination_ids": destinations}) if item.supply_id == supply_id else item
        for item in engine.scenario.supplies))


def _show(engine: LifeEngine, supply_id: str) -> None:
    """这条供给刚在人物此刻所在的地点呈现过（本轮工具层列为商品目标）。"""
    staged = engine.state.model_copy(deep=True)
    agent = staged.agents[1]
    agent.visible_supply_ids = {supply_id}
    agent.supply_browsed_at = {supply_id: staged.current_time}
    agent.supply_browsed_location = {supply_id: agent.location_id}
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_supply_shown"})])
    engine.state = staged


def _refused(engine: LifeEngine, name: str, request):
    """提交一次 start_activity，断言整组以 ACTIVITY_PRECONDITION_UNMET 拒绝，下一次观察的进度层带同一原因与下一步。"""
    result = _act(engine, name, "start_activity", request)
    assert (result.status, result.error_code) == ("rejected", _UNMET), (result.error_code, result.message)
    shown = last_result(engine.observe(context(engine, f"{name}-shown")))
    assert (shown["details"]["reason"], shown["details"]["next_step"]) == (result.data["reason"],
                                                                           result.data["next_step"])
    return result


def _pickup(engine: LifeEngine) -> object:
    """到店指名购买饭店的鸡汤面（写完整标签）。"""
    return start_request(targets=[_label(engine, _MEAL_SUPPLY)], actions=["obtain"], ways=["到店"])


def test_browse_results_list_full_store_labels_and_nothing_else(engine_factory) -> None:
    """浏览结果列完整标签「<门店>出售的<商品>」，仍只是名称（字符串，不标可得性）；与工具层商品目标的名称同一写法。"""
    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    started = _act(engine, "labels-browse", "start_activity", _choice(engine.observe(context(engine, "labels")),
                                                                      "browse"))
    _finish(engine, started.activity_id, "labels-browse")
    rows = engine.state.activities[started.activity_id].data["revealed"]["items"]
    observed = engine.observe(context(engine, "labels-seen"))
    (record,) = [row for row in progress_calls(observed, "start_activity")
                 if "browse_items" in row["result"]["details"]]
    items = record["result"]["details"]["browse_items"]
    assert items == [_label(engine, row["supply_id"]) for row in rows] and len(items) == len(rows) > 0
    assert all(isinstance(label, str) and "出售的" in label for label in items)
    targets = {row["object_id"]: row["object"] for row in observed["interaction_bindings"] if row["kind"] == "supply"}
    assert targets and all(targets[supply] in items for supply in targets if supply in {row["supply_id"]
                                                                                          for row in rows})


def test_goods_seen_in_a_browse_can_be_ordered_by_name_after_leaving(engine_factory) -> None:
    """可见性不放宽（裁决 D17）：在家浏览、走到办公室后，浏览得到的商品目标已从工具层消失，浏览结果随 session 结束进了
    记忆层（带店名的完整标签）；照记忆层的标签远程下单，经指名购买路径受理，订单送到办公室。"""
    engine = engine_factory()
    _stage_open(engine)
    stage_day_plan(engine)
    started = _act(engine, "leave-browse", "start_activity", _choice(engine.observe(context(engine, "leave")),
                                                                     "browse"))
    _finish(engine, started.activity_id, "leave-browse")
    assert conclude_pending(engine, "leave-browse-end").status == "completed"
    rows = engine.state.activities[started.activity_id].data["revealed"]["items"]
    wanted = next(row for row in rows if "delivery" in row["fulfillment_modes"]
                  and "office" in row["delivery_destination_ids"])
    label = _label(engine, wanted["supply_id"])
    office = next(row["object"] for row in engine.observe(context(engine, "leave-preview"))["interaction_bindings"]
                  if row["object_id"] == "office")
    walk = _act(engine, "leave-walk", "start_activity", start_activity("步行", office))
    _finish(engine, walk.activity_id, "leave-walk")
    assert conclude_pending(engine, "leave-arrive").status == "completed"
    assert engine.state.agents[1].location_id == "office"
    observed = engine.observe(context(engine, "leave-seen"))
    assert label not in tool_targets(observed)
    assert not any(row["kind"] == "supply" and row["object_id"] == wanted["supply_id"]
                   for row in observed["interaction_bindings"])
    settled = [line for entry in observed["memory_layer"]["earlier_today"] for line in entry["settled"]]
    (sentence,) = [line for line in settled if "，浏览到" in line]
    assert sentence.startswith(activity_name(engine, started.activity_id).split("（")[0])
    assert sentence.endswith(f"，浏览到{'、'.join(_label(engine, row['supply_id']) for row in rows)}")
    assert label in sentence
    ordered = _act(engine, "leave-order", "start_activity",
                   start_request(targets=[label], actions=["obtain"], ways=["远程下单"]))
    assert ordered.status == "completed", (ordered.error_code, ordered.message)
    (order,) = ordered.data["orders"]
    assert (order["supply_id"], order["destination_id"], order["fulfillment"]) == (wanted["supply_id"], "office",
                                                                                    "delivery")


def test_pickup_hint_goes_direct_via_a_reachable_place_or_says_the_store_cannot_be_reached(engine_factory) -> None:
    """到店的下一步只指向从此地可达的地点：门店此地直达写「去<门店>」；此地只通办公室、办公室通门店时写「可经<办公室>
    前往<门店>」；哪里都到不了时写「这里去不了那家店」。去不了的地点照旧不作为地点目标列出（裁决 S9）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    places = _places(engine)
    store = places[_STORE]
    direct = _refused(engine, "hint-direct", _pickup(engine))
    assert (direct.data["next_step"], direct.data["unmet"]) == (f"去{store}", ["required_location_id"])
    _block(engine, lambda route: route.origin_id == "home" and route.destination_id != "office")
    listed = {row["object_id"] for row in engine.observe(context(engine, "hint-listed"))["interaction_bindings"]
              if row["kind"] == "location"}
    assert listed == {"office"}
    via = _refused(engine, "hint-via", _pickup(engine))
    assert via.data["next_step"] == f"可经{places['office']}前往{store}"
    _block(engine, lambda route: route.destination_id == _STORE)
    nowhere = _refused(engine, "hint-nowhere", _pickup(engine))
    assert nowhere.data["next_step"] == UNREACHABLE_STORE_STEP == "这里去不了那家店"
    assert nowhere.data["reason"] == direct.data["reason"]


def test_remote_order_outside_the_delivery_area_names_reachable_places_to_order_from(engine_factory) -> None:
    """送达地不对（delivery_destination）：这里不在该供给的配送范围内时，工具层的远程下单标不可用并写这一句原因，
    选了以同一原因拒绝，下一步只列能送达且从此地可达的地点；能送达的地点此地去不了、经办公室可到时写「可经……前往……
    再下单」。"""
    engine = engine_factory()
    stage_day_plan(engine)
    places = _places(engine)
    supply = _market_delivery_supply(engine)
    _retarget(engine, supply, ("office", _PARK))
    _show(engine, supply)
    observed = engine.observe(context(engine, "area-preview"))
    remote = target_actions(observed, _label(engine, supply))["远程下单"]
    assert (remote["available"], remote["reason"]) == (False, DELIVERY_DESTINATION_REASON)
    request = start_request(targets=[_label(engine, supply)], actions=["obtain"], ways=["远程下单"])
    refused = _refused(engine, "area-order", request)
    assert (refused.data["reason"], refused.data["unmet"]) == (DELIVERY_DESTINATION_REASON, ["delivery_destination"])
    assert refused.data["next_step"] == f"去{places['office']}或{places[_PARK]}再下单"
    _retarget(engine, supply, (_PARK,))
    _block(engine, lambda route: route.origin_id == "home" and route.destination_id != "office")
    via = _refused(engine, "area-via", request)
    assert via.data["next_step"] == f"可经{places['office']}前往{places[_PARK]}再下单"


def test_remote_order_without_a_delivery_route_says_so_in_its_own_sentence(engine_factory) -> None:
    """配送路线缺失（delivery_route）：这里在配送范围内、门店到这里的驾车路线未知时，原因写配送路线缺失（不是送达地
    不对），拒绝的未满足条件是 delivery_route；下一步只列其余能送达且可达的地点。"""
    engine = engine_factory()
    stage_day_plan(engine)
    places = _places(engine)
    supply = _market_delivery_supply(engine)
    _retarget(engine, supply, ("home", "office", _PARK))
    _block(engine, lambda route: (route.origin_id, route.destination_id, route.mode.value) == ("market", "home",
                                                                                            "driving"))
    _show(engine, supply)
    observed = engine.observe(context(engine, "route-preview"))
    remote = target_actions(observed, _label(engine, supply))["远程下单"]
    assert (remote["available"], remote["reason"], remote["note"]) == (False, DELIVERY_ROUTE_REASON, None)
    refused = _refused(engine, "route-order", start_request(targets=[_label(engine, supply)], actions=["obtain"],
                                                            ways=["远程下单"]))
    assert (refused.data["reason"], refused.data["unmet"]) == (DELIVERY_ROUTE_REASON, ["delivery_route"])
    assert refused.data["next_step"] == f"去{places['office']}或{places[_PARK]}再下单"
    assert DELIVERY_ROUTE_REASON != DELIVERY_DESTINATION_REASON


def test_remote_order_hint_skips_reachable_places_the_store_cannot_deliver_to(engine_factory) -> None:
    """下一步只列能送达的地点：办公室在配送范围内、从家直达，但门店到办公室的驾车路线不可达（配送路线走不通，与整单
    结算同一判定），就不列为去处；同样在范围内、配送路线走得通的公园照列。"""
    engine = engine_factory()
    stage_day_plan(engine)
    places = _places(engine)
    supply = _market_delivery_supply(engine)
    _retarget(engine, supply, ("home", "office", _PARK))
    _block(engine, lambda route: (route.origin_id, route.mode.value) == ("market", "driving")
           and route.destination_id in {"home", "office"})
    _show(engine, supply)
    assert "office" in {row["object_id"] for row in engine.observe(context(engine, "skip-preview"))[
        "interaction_bindings"] if row["kind"] == "location"}
    refused = _refused(engine, "skip-order", start_request(targets=[_label(engine, supply)], actions=["obtain"],
                                                           ways=["远程下单"]))
    assert (refused.data["reason"], refused.data["unmet"]) == (DELIVERY_ROUTE_REASON, ["delivery_route"])
    assert refused.data["next_step"] == f"去{places[_PARK]}再下单"


def test_held_elsewhere_hint_also_points_only_to_reachable_places(engine_factory) -> None:
    """同一套下一步说法也用于他处持有物：在办公室要吃家里的东西，办公室到家的路线都走不通时，下一步写「可经<某处>
    前往<家>」，第一站是办公室此刻可达、列为地点目标的地点；原因不变。"""
    engine = engine_factory()
    stage_day_plan(engine)
    food = next(item for item in engine.scenario.products if "eat" in item.use_modes)
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={"location_id": "office",
                                                           "inventory": {"home": {food.product_id: 1.0}}}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_holding_elsewhere"})])
    engine.state = staged
    _block(engine, lambda route: route.origin_id == "office" and route.destination_id == "home")
    home = _places(engine)["home"]
    observed = engine.observe(context(engine, "held-preview"))
    listed = {_places(engine)[row["object_id"]] for row in observed["interaction_bindings"]
              if row["kind"] == "location"}
    target = f"持有物：{food.name}（{home}）"
    result = _act(engine, "held-eat", "start_activity",
                  start_request(targets=[target], actions=["consume"], ways=["食用"]))
    assert (result.status, result.error_code, result.data["reason"]) == ("rejected", "PRODUCT_NOT_HELD",
                                                                         f"这件东西在{home}，只能在当地用")
    step = result.data["next_step"]
    assert step.startswith("可经") and step.endswith(f"前往{home}") and home not in listed
    assert step.removeprefix("可经").split("前往")[0] in listed


def test_unreachable_places_stay_unlisted_while_the_hint_explains_them(engine_factory) -> None:
    """不可达的路线不列出（裁决 S9，冻结条款不变）：门店此地不可达时不是地点目标、没有前往它的出行动作；同一门店的
    到店被拒时由下一步说明经由哪里可以到。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = {row["object_id"] for row in engine.observe(context(engine, "unlisted-before"))["interaction_bindings"]
              if row["kind"] == "location"}
    assert _STORE in before
    _block(engine, lambda route: route.origin_id == "home" and route.destination_id == _STORE)
    after = engine.observe(context(engine, "unlisted-after"))
    locations = {row["object_id"] for row in after["interaction_bindings"] if row["kind"] == "location"}
    assert locations == before - {_STORE}
    assert f"地点：{_places(engine)[_STORE]}" not in tool_targets(after)
    refused = _refused(engine, "unlisted-pickup", _pickup(engine))
    assert refused.data["next_step"].startswith("可经") and refused.data["next_step"].endswith(_places(engine)[_STORE])
    hop = refused.data["next_step"].removeprefix("可经").split("前往")[0]
    assert hop in {_places(engine)[place] for place in locations}
