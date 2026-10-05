"""@brief 按现有品类树展开逐店商品集合并生成确定性整数分报价。"""

from __future__ import annotations
from worldmock.categories import Action, PairingState

import hashlib
import json

from worldmock.errors import IntegrityError


def assortment_objects(assortment, catalog) -> frozenset[str]:
    """@brief 商品集合严格等于完整品类后代展开与显式商品并集。"""
    for category in assortment.category_ids:
        catalog.category_contract.require_category(category)
    if set(assortment.object_ids) - set(catalog.objects):
        raise IntegrityError("Store assortment references unknown catalog objects")
    expanded = {obj.object_id for obj in catalog.objects.values()
                if any(catalog.category_contract.is_descendant(obj.category_id, category)
                       for category in assortment.category_ids)}
    return frozenset(expanded | set(assortment.object_ids))


def frozen_price(random_seed: int, location_id: str, obj, position: int) -> int:
    """@brief 使用规范 JSON 的 SHA-256 扰动一次，并以纯整数四舍五入到分。"""
    identity = json.dumps([random_seed, location_id, obj.object_id], ensure_ascii=False, separators=(",", ":"))
    digest_integer = int.from_bytes(hashlib.sha256(identity.encode("utf-8")).digest(), "big")
    percent = position + digest_integer % 11 - 5
    lower, upper = price_range(obj)
    return lower + ((upper - lower) * percent + 50) // 100


def price_range(obj) -> tuple[int, int]:
    """@brief 读取已经通过品类继承和对象覆盖校验的真实构建边界。"""
    lower = obj.attributes.get("purchase_price_fen_min")
    upper = obj.attributes.get("purchase_price_fen_max")
    if type(lower) is not int or type(upper) is not int or not 0 < lower <= upper:
        raise IntegrityError(f"Catalog product {obj.object_id} requires positive integer purchase price bounds")
    return lower, upper


def validate_assortments(dataset, catalog) -> None:
    """@brief 准入、完整集合、丰富度、菜单差异与所有冻结报价必须同时成立。"""

    contract = catalog.category_contract
    _validate_admission(dataset.category_assignment, contract)
    places = {item.location_id: item for item in dataset.locations}
    menus = {}
    for declaration in dataset.category_assignment.locations:
        place = places[declaration.location_id]
        if place.kind in {"convenience_store", "supermarket"} and {
                "food", "prepared-meal"} & set(declaration.category_ids):
            raise IntegrityError("Convenience stores and supermarkets must select specific food subcategories")
        objects = assortment_objects(declaration, catalog)
        admission = dataset.category_assignment.kinds[place.kind]
        for identity in objects:
            obj = catalog.objects[identity]
            if not any(contract.is_descendant(obj.category_id, parent) for parent in admission):
                raise IntegrityError(f"Store {place.location_id} contains a product outside its kind admission")
            if catalog.pairing("person", obj.category_id, Action.PURCHASE).state != PairingState.DEFINED:
                raise IntegrityError(f"Store product {identity} requires a defined purchase pairing")
        supplied = {item.product_id for item in dataset.supplies if item.location_id == place.location_id}
        if supplied != objects:
            raise IntegrityError(f"Store {place.location_id} supply set differs from its complete assortment")
        _validate_richness(place, objects, catalog)
        _validate_quotes(dataset, declaration, objects, catalog)
        if place.kind == "restaurant":
            menus[place.location_id] = objects
    _validate_menus(menus)
    _validate_positions(dataset, places)


def _validate_richness(place, objects: frozenset[str], catalog) -> None:
    """@brief 丰富度仅统计不同对象及其最终品类，门槛为当前正式场景假设。"""
    thresholds = {"restaurant": (8, 1), "convenience_store": (40, 6), "supermarket": (80, 8),
                  "merchant": (40, 6), "wet_market": (8, 2)}
    minimum, categories = thresholds[place.kind]
    leaves = {catalog.objects[identity].category_id for identity in objects}
    if len(objects) < minimum or len(leaves) < categories:
        raise IntegrityError(f"Store {place.location_id} assortment is below the declared richness requirement")
    contract = catalog.category_contract
    if place.kind == "merchant" and not any(contract.is_descendant(leaf, "durable") for leaf in leaves):
        raise IntegrityError("Merchant assortments require household goods")
    if place.kind == "convenience_store" and not any(
            contract.is_descendant(leaf, parent) for leaf in leaves
            for parent in ("food-soda", "food-dairy", "food-tea-juice")):
        raise IntegrityError("Convenience store assortments require drinks")


def _validate_admission(assignment, contract) -> None:
    """@brief 地点类别限定准入根，便利店与超市不得选入整个食品或正餐根集合。"""
    expected = {"restaurant": ("prepared-meal",), "convenience_store": ("food",),
                "supermarket": ("food", "durable", "ingredient"), "wet_market": ("ingredient",),
                "merchant": ("food", "durable", "ingredient")}
    for kind, categories in assignment.kinds.items():
        permitted = expected[kind] if kind in expected else ()
        if set(categories) != set(permitted):
            raise IntegrityError("Location kind admission disagrees with the declared category roots")
        for category in categories:
            contract.require_category(category)


def _validate_quotes(dataset, declaration, objects, catalog) -> None:
    """@brief 发布报价须等于定位公式且处于商品边界，不允许补货为零的丰富空目录。"""
    for supply in dataset.supplies:
        if supply.location_id != declaration.location_id:
            continue
        obj = catalog.objects[supply.product_id]
        lower, upper = price_range(obj)
        expected = frozen_price(dataset.random_seed, declaration.location_id, obj,
                                declaration.price_position_percent)
        if supply.initial_stock <= 0:
            raise IntegrityError("Published selling assortments require positive stock")
        if not supply.quotes or any(not lower <= quote.price_fen <= upper for quote in supply.quotes):
            raise IntegrityError("Frozen supply quote is outside its product price bounds")
        if supply.quotes[0].price_fen != expected:
            raise IntegrityError("Initial frozen supply quote disagrees with the declared price position")


def _validate_menus(menus: dict[str, frozenset[str]]) -> None:
    """@brief 每对餐馆双方都须有至少三个对方没有的正餐。"""
    ordered = sorted(menus)
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            if len(menus[first] - menus[second]) < 3 or len(menus[second] - menus[first]) < 3:
                raise IntegrityError("Every restaurant menu pair requires at least three distinct dishes on each side")


def _validate_positions(dataset, places: dict) -> None:
    """@brief 当前地图所有便利店定位比普通超市至少高十个百分点。"""
    positions = {item.location_id: item.price_position_percent for item in dataset.category_assignment.locations}
    convenience = [positions[key] for key, place in places.items() if place.kind == "convenience_store"]
    supermarkets = [positions[key] for key, place in places.items() if place.kind == "supermarket"]
    if any(high < low + 10 for high in convenience for low in supermarkets):
        raise IntegrityError(
            "Convenience store price positions must exceed supermarket positions by at least ten points"
        )
