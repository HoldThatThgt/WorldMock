"""@brief 查看与受理共用的确定性商品履约报价。"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.exchange.constants import DELIVERY_PREP_SECONDS
from worldmock.exchange.operators import delivery_fee_fen, delivery_seconds
from worldmock.life.models import ActivityKind, WorldState, active_activities
from worldmock.life.randomness import random_stream
from worldmock.life.guidance import rejection_guidance
from worldmock.life.readable import iso_time, money_phrase


@dataclass(frozen=True, slots=True)
class Offer:
    """@brief 每件商品在当前真实环境中的冻结履约事实。"""

    #: 商品身份。
    goods_id: str
    #: 实体店铺；快递为空。
    store_id: str | None
    #: 引擎代选下单 app；到店为空。
    app_id: str | None
    #: 真实末端目的地。
    destination_id: str
    #: 受理时冻结的预计到达时刻。
    expected_at: int
    #: 到店、外卖或快递。
    fulfillment: str
    #: 该商品组合的运费策略。
    delivery_fee_fen: int
    #: 外卖收货地址或快递住处。
    delivery_address_id: str
    #: 外卖图路径；快递只含驿站。
    path: tuple[str, ...]
    #: 外卖行程身份；其他为空。
    trip_id: str | None
    #: 外卖路径长度。
    distance_meters: int
    #: 外卖备餐秒数。
    prep_seconds: int


def present_location(state: WorldState, agent_id: int) -> str | None:
    """@brief 正在出行没有停留收货地点。"""
    travelling = any(item.kind == ActivityKind.TRAVEL for item in active_activities(state, agent_id))
    return None if travelling else state.agents[agent_id].location_id


def app_goods(inputs, state: WorldState, agent_id: int, app_id: str) -> list[dict]:
    """@brief 平台开始明细冻结直接供应与绑定店铺商品的同源真实履约报价。"""
    catalog = inputs.catalog
    identities = set(catalog.goods_by_app[app_id])
    identities.update(identity for store in catalog.stores_by_app[app_id]
                      for identity in catalog.goods_by_store[store])
    rows = []
    for identity in sorted(identities):
        goods = catalog.goods[identity]
        row = {"name": goods.name, "price": money_phrase(goods.price_fen), "delivery_fee": None,
               "expected_at": None, "available": False, "reason": None}
        try:
            offer = quote_goods(inputs, state, agent_id, identity)
        except BusinessRuleError as error:
            logging.error("Platform product quote is unavailable: %s", error)
            row["reason"] = rejection_guidance(error)["reason"]
        else:
            row.update(delivery_fee=money_phrase(offer.delivery_fee_fen),
                       expected_at=iso_time(offer.expected_at, state.timezone), available=True)
        rows.append(row)
    return rows


def quote_goods(inputs, state: WorldState, agent_id: int, goods_id: str) -> Offer:
    """@brief 按绑定侧与真实现场派生唯一报价，不消费未来请求身份。"""
    goods = inputs.catalog.goods.get(goods_id)
    if goods is None:
        raise BusinessRuleError("SUPPLY_NOT_FOUND")
    if goods.app_ids:
        return _courier_offer(inputs, state, goods)
    here = present_location(state, agent_id)
    stores = tuple(key for key in sorted(inputs.catalog.goods_by_store)
                   if goods_id in inputs.catalog.goods_by_store[key])
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(inputs.config.timezone))
    if here in stores:
        if not inputs.catalog.locations[here].is_open(local):
            raise BusinessRuleError("LOCATION_CLOSED")
        return Offer(goods_id, here, None, here, state.current_time, "pickup", 0, here,
                     (here,), None, 0, 0)
    if here is None:
        raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", ["delivery_destination"],
                                public_reason="Place the order after arriving at a location.")
    eligible = tuple(key for key in stores if inputs.catalog.apps_by_store[key]
                     and inputs.catalog.locations[key].is_open(local))
    if not eligible:
        _require_store(inputs, state, agent_id, stores)
    store = min(eligible, key=lambda key: (goods.price_fen + delivery_fee_fen(),
                state.current_time + delivery_seconds(inputs.catalog.shortest_m(key, here)), key))
    return _delivery_offer(inputs, state, goods, store, here)


def _require_store(inputs, state: WorldState, agent_id: int, stores: tuple[str, ...]) -> None:
    """@brief 没有营业远程渠道时说明最近实际有售地点。"""
    origin = state.agents[agent_id].location_id
    nearest = min(stores, key=lambda key: (inputs.catalog.shortest_m(origin, key), key))
    name = inputs.catalog.locations[nearest].display_name
    raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", ["required_location_id"],
                            public_reason=f"Purchase requires presence at {name} while it is open.")


def _order_app(state: WorldState, source: str, apps: tuple[str, ...]) -> str:
    """@brief 下单渠道只依赖抽取来源与本地日期：店铺商品的来源是店铺，快递类商品的来源是商品加快递标记。

    同一家店同一本地日的商品因此总抽到同一个下单 app，一家店在一次购买里只有一个配送组、一份运费。
    """
    local_date = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone)).date().isoformat()
    key = f"order-app:{source}:{local_date}"
    return random_stream(state.random_state, key).choice(sorted(apps))


def _delivery_offer(inputs, state: WorldState, goods, store: str, here: str) -> Offer:
    """@brief 外卖保留完整图路线，不因距离长而改为快递。"""
    catalog = inputs.catalog
    meters = catalog.shortest_m(store, here)
    app = _order_app(state, store, catalog.apps_by_store[store])
    return Offer(goods.goods_id, store, app, here, state.current_time + delivery_seconds(meters),
                 "delivery", delivery_fee_fen(), here, catalog.graph.path(store, here),
                 f"{store}->{here}:driving", meters, DELIVERY_PREP_SECONDS)


def _courier_offer(inputs, state: WorldState, goods) -> Offer:
    """@brief 快递按本地日期边界到站；费用不随日期或查看次数改变。"""
    app = _order_app(state, f"{goods.goods_id}:courier", goods.app_ids)
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone))
    days_key = f"courier-days:{goods.goods_id}:{app}:{local.date().isoformat()}"
    days = random_stream(state.random_state, days_key).choice((2, 3, 4, 5))
    arrival = datetime.combine(local.date() + timedelta(days=days), time.min, local.tzinfo)
    fee = random_stream(state.random_state, f"courier-fee:{goods.goods_id}:{app}").choice((0, delivery_fee_fen()))
    station = inputs.catalog.nearest_parcel_station(inputs.home_location_id)
    return Offer(goods.goods_id, None, app, station, int(arrival.timestamp()), "station_pickup", fee,
                 inputs.home_location_id, (station,), None, 0, 0)
