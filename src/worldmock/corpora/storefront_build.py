"""@brief 从完整逐店声明构建独立库存和一次性冻结报价。"""

from __future__ import annotations

import logging
from datetime import datetime

from worldmock.corpora.storefront import assortment_objects, frozen_price
from worldmock.errors import IntegrityError
from worldmock.life.models import DataSource
from worldmock.life.map_models import QuoteConfig, SupplyConfig


def build_storefront_supplies(catalog, assignment, locations, assumptions: dict, random_seed: int) -> tuple:
    """@brief 每个声明对象生成一条稳定身份供给，营业、履约和卖方必须明确。"""
    stores = {item["location_id"]: item for item in assumptions["locations"]}
    places = {item.location_id: item for item in locations}
    if set(stores) != {item.location_id for item in assignment.locations}:
        _fail("Store supply assumptions must cover exactly all selling locations")
    result = []
    for declaration in sorted(assignment.locations, key=lambda item: item.location_id):
        place = places[declaration.location_id]
        if not place.opening_calendars:
            _fail("Selling locations require explicit opening calendars")
        store = stores[place.location_id]
        for identity in sorted(assortment_objects(declaration, catalog)):
            obj = catalog.objects[identity]
            result.append(_supply(obj, declaration, store, assumptions, random_seed))
    return tuple(result)


def _supply(obj, declaration, store: dict, assumptions: dict, random_seed: int) -> SupplyConfig:
    """@brief 为对象、地点和卖方绑定独立供给，并给报价保留本次生成身份。"""
    source = DataSource.model_validate(store["source"])
    identity = f"supply-{declaration.location_id}-{obj.object_id}"
    price = frozen_price(random_seed, declaration.location_id, obj, declaration.price_position_percent)
    quote = QuoteConfig(
        price_version=f"{identity}:seed-{random_seed}:v1", price_fen=price,
        effective_from=datetime.fromisoformat(assumptions["effective_from"]), effective_to=None,
        reviewed_on=assumptions["reviewed_on"], still_valid_on=assumptions["still_valid_on"], source=source)
    return SupplyConfig(
        supply_id=identity, location_id=declaration.location_id, product_id=obj.object_id,
        seller_account_id=store["seller_account_id"], quotes=(quote,), initial_stock=store["initial_stock"],
        fulfillment_modes=store["fulfillment_modes"], delivery_destination_ids=store["delivery_destination_ids"],
        delivery_delay_seconds=store["delivery_delay_seconds"],
        required_qualifications=store["required_qualifications"], source=source)


def _fail(message: str) -> None:
    """@brief 记录英文错误并停止资料不完整的构建。"""
    logging.error(message)
    raise IntegrityError(message)
