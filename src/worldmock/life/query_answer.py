"""@brief 查询的答案：做完那一秒按世界算出的机器形式，以及投影给人物的 details.query。

规则见 docs/design/04-simulation-engine.md「出行、内容与查询」，机器形式见 docs/design/09-io-formats.md「世界、人物与
活动」，人物读到的形状见 docs/design/06-agent.md「进度、待办与记忆」。途径的三个中文名与原因的六句文字只在这里定义。
外卖与快递的运费、预计送达直接取购买的报价函数，不另算一份。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.life.models import ActivityState, WorldState
from worldmock.life.order_apps import selling_app_ids
from worldmock.life.purchase_offers import courier_offer, delivery_offer, order_app
from worldmock.life.readable import iso_time, money_phrase

#: 一次查询至多列出的商品件数（固定常数，不读配置）。
QUERY_PRODUCT_LIMIT = 20
#: 途径的机器取值（沿用 FulfillmentKind）→ 人物读到的中文名；「到店」与购买结果 channel 里的是同一个词。
ROUTE_NAMES = MappingProxyType({"pickup": "到店", "delivery": "外卖", "station_pickup": "快递"})
#: 不可下单的六个原因码 → 人物读到的固定文字；{place} 填人物所在地点的显示名。
REASON_TEXTS = MappingProxyType({
    "location_closed": "这家店此刻已打烊",
    "present_at_seller": "你所在的{place}就卖这件商品，此刻购买只在所在地点到店完成，不从这家店送",
    "store_without_app": "这家店没有上任何 app，要到店里买",
    "no_open_store": "此刻没有营业的店卖这件商品",
    "no_open_store_on_app": "上了这个 app、卖这件商品的店此刻都没有营业",
    "not_sold_on_app": "这个 app 里没有",
})
#: 答案的两种范围：只列经意向 app 能买的，或列出全部途径。
SCOPED, ALL_ROUTES = "只列经这个 app 能买的", "列出全部途径"
#: 提前结束或被截断、没有答案的查询读到的状态。
UNFINISHED = "没有查完"


@dataclass(frozen=True, slots=True)
class _Moment:
    """@brief 算答案的那一刻：环境输入、做完那一秒的世界、人物所在地点、本地时刻与答案限定的意向 app。"""

    #: 完整环境输入（冻结目录与运行配置）。
    inputs: Any
    #: 查询做完那一秒的世界。
    state: WorldState
    #: 人物所在的地点：查询期间人物不会离开开始它的地点。
    here: str
    #: 这一秒在运行时区的本地时刻，营业按它判定。
    local: datetime
    #: 答案限定的意向 app；答案列出全部途径时为 None。
    app: str | None


def queried_goods(catalog, targets: Sequence[Mapping[str, Any]], app_id: str | None = None) -> tuple[str, ...]:
    """@brief 所查商品：指名的商品加各类别下的全部商品（类别下只在 app 里卖的商品也算），按 goods_id 升序去重。

    :param catalog: 冻结目录。
    :param targets: 查询活动数据里认出的各项（商品或类别）。
    :param app_id: 答案限定的意向 app；给出时类别只展开它卖的商品，指名的商品照留。
    """
    named = {item["goods_id"] for item in targets if item["kind"] == "goods"}
    categories = {item["category"] for item in targets if item["kind"] == "category"}
    expanded = {key for key, goods in catalog.goods.items() if goods.category in categories
                and (app_id is None or app_id in selling_app_ids(catalog, (key,)))}
    return tuple(sorted(named | expanded))


def query_answer(inputs, state: WorldState, activity: ActivityState) -> dict:
    """@brief 按做完那一秒的世界算查询答案的机器形式：已排序、已截取的各行与没列出的商品件数。

    :param inputs: 完整环境输入。
    :param state: 查询做完那一秒的世界（时钟已到这一秒，同一秒的送达与推送还没有处理）。
    :param activity: 刚走满时长的查询活动。
    :return: {"rows": 各行, "omitted": 没列出的商品件数}。
    """
    data, now = activity.data, state.current_time
    local = datetime.fromtimestamp(now, ZoneInfo(inputs.config.timezone))
    moment = _Moment(inputs, state, activity.location_id, local, data["app_id"] if data["scoped"] else None)
    goods_ids = queried_goods(inputs.catalog, data["targets"], moment.app)
    return ordered_answer([_product_rows(moment, key) for key in goods_ids], now)


def ordered_answer(products: Sequence[Sequence[dict]], now: int) -> dict:
    """@brief 排序并截取答案：商品按（有没有可以下单的行，有的在前；最早的可下单送达时刻，没有的取 0；goods_id）排，
    一件商品之内的行按（可以下单的在前；可下单行的送达时刻，到店行按此刻算，不可下单的取 0；store_id，没有店的取空串）
    排；取前 QUERY_PRODUCT_LIMIT 件，其余只计件数。

    :param products: 每件商品的全部行（至少一行），行是机器形式。
    :param now: 查询做完的那一秒。
    :return: {"rows": 已排序、已截取的各行, "omitted": 没列出的商品件数}。
    """
    ordered = sorted((sorted(rows, key=lambda row: _row_key(row, now)) for rows in products),
                     key=lambda rows: _product_key(rows, now))
    shown = ordered[:QUERY_PRODUCT_LIMIT]
    return {"rows": [row for rows in shown for row in rows], "omitted": len(ordered) - len(shown)}


def _product_rows(moment: _Moment, goods_id: str) -> list[dict]:
    """@brief 一件商品的全部行（尚未排序）；一行都没有时留一行说明原因。"""
    catalog = moment.inputs.catalog
    goods = catalog.goods[goods_id]
    if moment.app is not None and moment.app not in selling_app_ids(catalog, (goods_id,)):
        # 指名的商品意向 app 不卖：一行「这个 app 里没有」；人物所在的地点卖它时到店行另外照列。
        local = [_store_row(moment, goods, moment.here, True)] if moment.here in _stores(catalog, goods) else []
        rows = [*local, _row(goods, (None, None), (), "not_sold_on_app")]
    elif goods.app_ids:
        rows = [_courier_row(moment, goods)]
    else:
        rows = _store_rows(moment, goods)
    if not rows:
        rows = [_row(goods, (None, None), (), "no_open_store" if moment.app is None else "no_open_store_on_app")]
    return rows


def _stores(catalog, goods) -> list[str]:
    """@brief 卖这件店铺商品的地点：类型在商品的 store_kinds 里，按地点身份升序。"""
    return [key for key in sorted(catalog.locations) if catalog.locations[key].kind in goods.store_kinds]


def _store_rows(moment: _Moment, goods) -> list[dict]:
    """@brief 店铺商品：卖它的每个地点各给一行或不列（别处已打烊、或答案限定意向 app 而这家没登记它）。"""
    stores = _stores(moment.inputs.catalog, goods)
    present = moment.here in stores
    rows = (_store_row(moment, goods, store, present) for store in stores)
    return [row for row in rows if row is not None]


def _store_row(moment: _Moment, goods, store: str, present: bool) -> dict | None:
    """@brief 一个卖这件商品的地点出的行：所在地点是到店行；别处营业而登记了 app 的是外卖行，没登记的是到店行。

    :param present: 人物所在的地点卖这件商品（此刻购买只会在所在地点到店完成）。
    """
    catalog = moment.inputs.catalog
    location = catalog.locations[store]
    if store == moment.here:
        return _row(goods, ("pickup", store), (), None if location.is_open(moment.local) else "location_closed")
    registered = tuple(sorted(catalog.apps_by_store[store]))
    apps = registered if moment.app is None else tuple(app for app in registered if app == moment.app)
    if not location.is_open(moment.local) or (moment.app is not None and not apps):
        return None
    if not apps:
        return _row(goods, ("pickup", store), (), "store_without_app")
    if present:
        return _row(goods, ("delivery", store), apps, "present_at_seller")
    # 外卖报价的运费与预计送达与下单 app 无关：传这家店登记的 app 里身份最小的一个，限定意向时就是意向 app。
    offer = delivery_offer(moment.inputs, moment.state, goods, (store, apps[0]), moment.here)
    return _row(goods, ("delivery", store), apps, None, offer)


def _courier_row(moment: _Moment, goods) -> dict:
    """@brief 只在 app 里卖的商品恰一行快递：运费与到站时刻取此刻照这次查询的意向下单会得到的那一份报价。"""
    app = order_app(moment.state, f"{goods.goods_id}:courier", goods.app_ids, moment.app)
    apps = goods.app_ids if moment.app is None else (moment.app,)
    offer = courier_offer(moment.inputs, moment.state, goods, app)
    return _row(goods, ("station_pickup", None), apps, None, offer)


def _row(goods, place: tuple[str | None, str | None], apps: Sequence[str], reason: str | None,
         offer=None) -> dict:
    """@brief 答案的一行（九个键）：可以下单的外卖行与快递行带报价里的运费与预计送达，其余为 null。

    :param place: （途径，店铺身份）；快递行没有店，没有途径的行两者都为 None。
    :param reason: 不可下单的原因码；可以下单时为 None。
    """
    route, store = place
    return {"goods_id": goods.goods_id, "route": route, "store_id": store, "app_ids": sorted(apps),
            "price_fen": goods.price_fen, "delivery_fee_fen": None if offer is None else offer.delivery_fee_fen,
            "expected_at": None if offer is None else offer.expected_at, "available": reason is None,
            "reason": reason}


def _arrival(row: dict, now: int) -> int:
    """@brief 可下单行的送达时刻：到店行按此刻算，外卖行和快递行取报价的预计送达；不可下单的行取 0。"""
    if not row["available"]:
        return 0
    return now if row["expected_at"] is None else row["expected_at"]


def _row_key(row: dict, now: int) -> tuple:
    """@brief 一件商品之内行的次序：可以下单的在前，再按送达时刻，再按店铺身份（没有店的行取空串）。"""
    return not row["available"], _arrival(row, now), row["store_id"] or ""


def _product_key(rows: list[dict], now: int) -> tuple:
    """@brief 商品的次序：有可以下单的行的在前，再按最早的可下单送达时刻（没有可下单行的取 0），再按商品身份。"""
    arrivals = [_arrival(row, now) for row in rows if row["available"]]
    return not arrivals, min(arrivals, default=0), rows[0]["goods_id"]


def query_details(snapshot: Mapping[str, Any], inputs) -> dict:
    """@brief 把终态查询快照投影成人物读到的 details.query：有答案的是查完的形状，没有的是「没有查完」。

    :param snapshot: 开始这项查询的那条动作的终态结果里的活动快照。
    :param inputs: 完整环境输入（显示名与运行时区）。
    """
    data, catalog = snapshot["data"], inputs.catalog
    app = None if data["app_id"] is None else catalog.apps[data["app_id"]].name
    answer = data.get("answer")
    if answer is None:
        return {"app": app, "state": UNFINISHED}
    place = catalog.locations[snapshot["location_id"]].display_name
    details = {"app": app, "scope": SCOPED if data["scoped"] else ALL_ROUTES,
               "rows": [_shown(row, inputs, place) for row in answer["rows"]]}
    if answer["omitted"]:
        details["omitted"] = f"还有 {answer['omitted']} 件没列"
    return details


def _shown(row: Mapping[str, Any], inputs, place: str) -> dict:
    """@brief 答案一行的人读形式：显示名、途径与原因的中文、金额短语与带时区的时刻。"""
    catalog, zone = inputs.catalog, inputs.config.timezone
    return {"product": catalog.goods[row["goods_id"]].name,
            "route": None if row["route"] is None else ROUTE_NAMES[row["route"]],
            "store": None if row["store_id"] is None else catalog.locations[row["store_id"]].display_name,
            "apps": [catalog.apps[key].name for key in row["app_ids"]], "price": money_phrase(row["price_fen"]),
            "delivery_fee": None if row["delivery_fee_fen"] is None else money_phrase(row["delivery_fee_fen"]),
            "expected_at": None if row["expected_at"] is None else iso_time(row["expected_at"], zone),
            "now": "可以下单" if row["available"] else "不可下单",
            "reason": None if row["reason"] is None else REASON_TEXTS[row["reason"]].format(place=place)}
