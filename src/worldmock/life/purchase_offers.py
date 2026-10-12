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
    #: 下单 app：意向用得上时是意向 app，否则是当天抽到的；到店为空。
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
            offer = quote_goods(inputs, state, agent_id, identity, None)
        except BusinessRuleError as error:
            logging.error("Platform product quote is unavailable: %s", error)
            row["reason"] = rejection_guidance(error)["reason"]
        else:
            row.update(delivery_fee=money_phrase(offer.delivery_fee_fen),
                       expected_at=iso_time(offer.expected_at, state.timezone), available=True)
        rows.append(row)
    return rows


def quote_goods(inputs, state: WorldState, agent_id: int, goods_id: str, prefer: str | None) -> Offer:
    """@brief 按绑定侧与真实现场定一件商品的路线并报价，不消费未来请求身份。

    判断次序：快递类商品、人停在卖它的地点（到店）、人在路上、外卖。意向只改变外卖从哪些店里选和下单 app
    是抽的还是意向 app；到店与三种拒绝都不看意向。

    :param prefer: 已认出的意向 app 身份；没有意向时为 None（工具层预览与打开 app 的清单都写 None）。
    """
    goods = inputs.catalog.goods.get(goods_id)
    if goods is None:
        raise BusinessRuleError("SUPPLY_NOT_FOUND")
    if goods.app_ids:
        return courier_offer(inputs, state, goods, order_app(state, f"{goods_id}:courier", goods.app_ids, prefer))
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
    preferred = tuple(key for key in eligible if prefer in inputs.catalog.apps_by_store[key])
    if not eligible:
        _require_store(inputs, state, agent_id, stores)
    # 意向用得上时只在登记了意向 app 的店里选，可以因此换到更远的店；用不上时在全部候选店里选。
    # 同价时取离收货地点最近的店，同距取较小的店铺身份；配送时长随距离只增不减，最近的店也最早送达。
    store = min(preferred or eligible, key=lambda key: (goods.price_fen + delivery_fee_fen(),
                inputs.catalog.shortest_m(key, here), key))
    app = order_app(state, store, inputs.catalog.apps_by_store[store], prefer)
    return delivery_offer(inputs, state, goods, (store, app), here)


def _require_store(inputs, state: WorldState, agent_id: int, stores: tuple[str, ...]) -> None:
    """@brief 没有营业远程渠道时说明最近实际有售地点。"""
    origin = state.agents[agent_id].location_id
    nearest = min(stores, key=lambda key: (inputs.catalog.shortest_m(origin, key), key))
    name = inputs.catalog.locations[nearest].display_name
    raise PreconditionError("ACTIVITY_PRECONDITION_UNMET", ["required_location_id"],
                            public_reason=f"Purchase requires presence at {name} while it is open.")


def order_app(state: WorldState, source: str, apps: tuple[str, ...], prefer: str | None) -> str:
    """@brief 定下单 app：意向 app 在给定的 app 之内就用它，否则按抽取来源与本地日期在其中等概率抽一个。

    抽取来源：店铺商品是店铺身份，快递类商品是商品身份加快递标记。同一家店同一本地日没有意向时总抽到同一个下单 app，
    一家店在一次购买里因此只有一个配送组、一份运费：经意向选中的店都登记了意向 app，另选选中的店一定没有登记它。

    :param source: 抽取来源。
    :param apps: 这家店或这件商品登记的 app。
    :param prefer: 已认出的意向 app 身份，可以为 None。
    :return: 下单 app 的身份。
    """
    if prefer in apps:
        return prefer
    local_date = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone)).date().isoformat()
    key = f"order-app:{source}:{local_date}"
    return random_stream(state.random_state, key).choice(sorted(apps))


def delivery_offer(inputs, state: WorldState, goods, route: tuple[str, str], here: str) -> Offer:
    """@brief 一家店经定好的下单 app 送到收货地点的外卖报价；保留完整图路线，不因距离长而改为快递。

    运费、预计送达和路径只取决于店和收货地点，app 原样写进报价。不判断营业，也不判断这家店有没有登记这个 app，
    由定路线的一方保证。

    :param route: 选好的（店铺身份，下单 app 身份）。
    :param here: 收货地点。
    """
    catalog = inputs.catalog
    store, app = route
    meters = catalog.shortest_m(store, here)
    return Offer(goods.goods_id, store, app, here, state.current_time + delivery_seconds(meters),
                 "delivery", delivery_fee_fen(), here, catalog.graph.path(store, here),
                 f"{store}->{here}:delivery", meters, DELIVERY_PREP_SECONDS)


def courier_offer(inputs, state: WorldState, goods, app: str) -> Offer:
    """@brief 经定好的下单 app 下单的快递报价：按本地日期边界到站，到站天数与运费按这个 app 的键取，不随查看次数改变。

    :param app: 定好的下单 app 身份。
    """
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone))
    days_key = f"courier-days:{goods.goods_id}:{app}:{local.date().isoformat()}"
    days = random_stream(state.random_state, days_key).choice((2, 3, 4, 5))
    arrival = datetime.combine(local.date() + timedelta(days=days), time.min, local.tzinfo)
    fee = random_stream(state.random_state, f"courier-fee:{goods.goods_id}:{app}").choice((0, delivery_fee_fen()))
    station = inputs.catalog.nearest_parcel_station(inputs.home_location_id)
    return Offer(goods.goods_id, None, app, station, int(arrival.timestamp()), "station_pickup", fee,
                 inputs.home_location_id, (station,), None, 0, 0)
