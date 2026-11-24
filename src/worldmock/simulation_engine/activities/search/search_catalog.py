"""@brief 隔离子搜索会话的只读环境目录与 Fact Guard 校验视图。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.exchange_engine.definitions.types import GOODS_CATEGORY_LABELS
from worldmock.foundation.errors import BusinessRuleError
from worldmock.foundation.time.readable import duration_phrase, interval_phrases, iso_time, money_phrase
from worldmock.resource_engine.catalog.travel_methods import travel_method, travel_methods
from worldmock.simulation_engine.actions import admission
from worldmock.simulation_engine.activities.content.location_activities import location_units, unit_locked
from worldmock.simulation_engine.activities.travel.travel import plan_trip
from worldmock.simulation_engine.commerce.purchase.order_apps import order_app_ids, preferred_app_id
from worldmock.simulation_engine.commerce.purchase.purchase_offers import quote_goods
from worldmock.simulation_engine.observation.targets.object_labels import resolve_name
from worldmock.simulation_engine.scheduling.clock.weather import trip_weather_factor
from worldmock.state.decisions.barrier import SearchTaskSpec
from worldmock.state.interaction.search_requests import (
    InspectGoodsOffersRequest,
    SearchGoodsRequest,
    SearchPlacesRequest,
    SubmitSearchResultsRequest,
)
from worldmock.state.world.kinds import ROUTE_NAMES
from worldmock.state.world.world import WorldState

_LOGGER = logging.getLogger(__name__)


class SearchCatalogView:
    """@brief 实现 SearchCatalogHandle 协议的只读搜索视图：零状态写入、强制作用域与 Fact Guard。"""

    def __init__(self, inputs: EnvironmentInputs, state: WorldState, task: SearchTaskSpec) -> None:
        """@brief 绑定冻结环境目录、只读世界快照与本次搜索任务契约。"""
        self._inputs = inputs
        self._state = state
        self._task = task

    def search_goods(self, request: SearchGoodsRequest | Mapping[str, Any]) -> dict[str, Any]:
        """@brief 按条件分页检索当前作用域内商品与实时可用报价。"""
        req = request if isinstance(request, SearchGoodsRequest) else SearchGoodsRequest.model_validate(request)
        current_city = _current_city(self._inputs.catalog, self._state, self._task.agent_id)
        app_filter = _resolve_app_filter(self._inputs.catalog, req.app)
        store_filter = _resolve_store_filter(self._inputs.catalog, req.store, current_city)
        rows = _matching_goods_rows(self._inputs, self._state, self._task, (req, app_filter, store_filter))
        page = rows[req.offset:req.offset + req.limit]
        next_offset = req.offset + len(page)
        return {"total": len(rows), "offset": req.offset, "limit": req.limit,
                "has_more": next_offset < len(rows), "next_offset": next_offset, "items": page}

    def inspect_goods_offers(self, request: InspectGoodsOffersRequest | Mapping[str, Any]) -> dict[str, Any]:
        """@brief 查询指定商品集合在当前人物位置的精确履约途径、售价、运费与预计到达时刻。"""
        req = (request if isinstance(request, InspectGoodsOffersRequest)
               else InspectGoodsOffersRequest.model_validate(request))
        items = [_inspect_one_goods(self._inputs, self._state, self._task, gid) for gid in req.goods_ids]
        return {"items": items}

    def search_places(self, request: SearchPlacesRequest | Mapping[str, Any]) -> dict[str, Any]:
        """@brief 按距离排序检索同城及直达异地枢纽地点、营业状态、可做内容单元与各出行方式耗时和车费。"""
        req = request if isinstance(request, SearchPlacesRequest) else SearchPlacesRequest.model_validate(request)
        rows = _matching_place_rows(self._inputs, self._state, self._task, req)
        page = rows[req.offset:req.offset + req.limit]
        next_offset = req.offset + len(page)
        return {"total": len(rows), "offset": req.offset, "limit": req.limit,
                "has_more": next_offset < len(rows), "next_offset": next_offset, "items": page}

    def validate_submission(self, request: SubmitSearchResultsRequest | Mapping[str, Any]) -> dict[str, Any]:
        """@brief 按 Fact Guard 白名单校验最终提交的 goods_ids、place_ids 与摘要。"""
        req = (request if isinstance(request, SubmitSearchResultsRequest)
               else SubmitSearchResultsRequest.model_validate(request))
        here = self._state.agents[self._task.agent_id].location_id
        invalid_goods, invalid_places = validate_fact_ids(
            self._inputs.catalog, self._task.scoped_app_id, req.goods_ids, req.place_ids, here)
        if invalid_goods or invalid_places:
            _LOGGER.error("Search submission contains out-of-scope or unknown ids: goods=%s places=%s",
                          invalid_goods, invalid_places)
            return {"valid": False, "error_code": "SEARCH_FACT_INVALID",
                    "invalid_goods_ids": list(invalid_goods), "invalid_place_ids": list(invalid_places),
                    "message": "Submitted goods_ids or place_ids are unknown or outside scoped app."}
        return {"valid": True, "goods_ids": list(req.goods_ids),
                "place_ids": list(req.place_ids), "summary": req.summary}


def validate_fact_ids(catalog, scoped_app_id: str | None,
                      goods_ids: tuple[str, ...] | list[str],
                      place_ids: tuple[str, ...] | list[str],
                      here_location_id: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """@brief 校验商品与地点身份均为冻结环境真实对象、满足意向 app 及同城与直达异地枢纽作用域。"""
    current_city = catalog.graph.city_of(here_location_id)
    bad_goods = tuple(
        gid for gid in goods_ids
        if gid not in catalog.goods or not _goods_in_scope(catalog, gid, current_city, scoped_app_id)
    )
    bad_places = tuple(
        pid for pid in place_ids
        if pid not in catalog.locations or not _place_in_scope(catalog, here_location_id, pid)
    )
    return bad_goods, bad_places


def _current_city(catalog, state: WorldState, agent_id: int) -> str:
    """@brief 返回当前锚定地点所属城市身份。"""
    return catalog.locations[state.agents[agent_id].location_id].city_id


def _local_stores(catalog, goods_id: str, current_city: str) -> tuple[str, ...]:
    """@brief 返回当前城市里售卖该商品的实体门店身份，按身份升序。"""
    return tuple(sid for sid in sorted(catalog.goods_by_store)
                 if goods_id in catalog.goods_by_store[sid] and catalog.locations[sid].city_id == current_city)


def _local_selling_app_ids(catalog, goods_id: str, current_city: str) -> tuple[str, ...]:
    """@brief 线上 app 登记或当前城市售卖门店登记的下单 app，按身份升序。"""
    local = set(_local_stores(catalog, goods_id, current_city))
    return tuple(
        key for key in sorted(catalog.apps)
        if goods_id in catalog.goods_by_app[key]
        or any(goods_id in catalog.goods_by_store[sid] for sid in catalog.stores_by_app[key] if sid in local)
    )


def _goods_in_scope(catalog, goods_id: str, current_city: str, scoped_app_id: str | None) -> bool:
    """@brief 网上有售商品跨城可见；实体门店商品须在当前城市有售且满足作用域 app。"""
    local = _local_stores(catalog, goods_id, current_city)
    if not catalog.goods[goods_id].app_ids and not local:
        return False
    return scoped_app_id is None or scoped_app_id in _local_selling_app_ids(catalog, goods_id, current_city)


def _place_in_scope(catalog, here: str, loc_id: str) -> bool:
    """@brief 地点可见当且仅当位于当前所在城市，或是从当前地点直达的异地枢纽。"""
    graph = catalog.graph
    if graph.city_of(loc_id) == graph.city_of(here):
        return True
    return loc_id in dict(graph.intercity_edges_from(here))


def _resolve_app_filter(catalog, app_text: str | None) -> tuple[bool, str | None]:
    """@brief 把调用方所写的下单 app 文本规整为唯一 app_id，未命中时返回不可匹配标记。"""
    if app_text is None:
        return True, None
    if app_text in order_app_ids(catalog):
        return True, app_text
    found = preferred_app_id(catalog, app_text)
    return (found is not None), found


def _resolve_store_filter(catalog, store_text: str | None, current_city: str) -> tuple[bool, str | None]:
    """@brief 把调用方所写的店铺名称或身份规整为当前城市的唯一地点身份。"""
    if store_text is None:
        return True, None
    if store_text in catalog.locations:
        ok = catalog.locations[store_text].city_id == current_city
        return ok, (store_text if ok else None)
    by_name = {loc.display_name: key for key, loc in catalog.locations.items() if loc.city_id == current_city}
    matched = resolve_name(store_text, list(by_name)).name
    return (matched is not None), (None if matched is None else by_name[matched])


def _matching_goods_rows(inputs: EnvironmentInputs, state: WorldState,
                         task: SearchTaskSpec, filters: tuple) -> list[dict[str, Any]]:
    """@brief 筛选并按可买性、单价和身份排序商品候选，附带可用报价概览。"""
    req, (app_ok, app_filter), (store_ok, store_filter) = filters
    if not app_ok or not store_ok:
        return []
    catalog = inputs.catalog
    scoped = task.scoped_app_id
    current_city = _current_city(catalog, state, task.agent_id)
    rows: list[tuple[tuple, dict[str, Any]]] = []
    for goods_id in sorted(catalog.goods):
        goods = catalog.goods[goods_id]
        if not _goods_in_scope(catalog, goods_id, current_city, scoped):
            continue
        sellers = _local_selling_app_ids(catalog, goods_id, current_city)
        if app_filter is not None and app_filter not in sellers:
            continue
        if store_filter is not None and goods_id not in catalog.goods_by_store.get(store_filter, ()):
            continue
        if req.category is not None and goods.category != req.category:
            continue
        if req.max_price_yuan is not None and goods.price_fen > round(req.max_price_yuan * 100):
            continue
        if not _matches_goods_keywords(catalog, goods, req.keywords, current_city):
            continue
        row = project_goods_row(inputs, state, task.agent_id, goods_id, scoped)
        if req.open_only and not row["available"]:
            continue
        rows.append(((not row["available"], row["price_fen"], goods_id), row))
    return [row for _, row in sorted(rows, key=lambda item: item[0])]


def _matches_goods_keywords(catalog, goods, keywords: tuple[str, ...] | list[str], current_city: str) -> bool:
    """@brief 关键词空时全部通过，否则商品名、类别或当前城市售卖店名至少命中一条不区分大小写子串。"""
    if not keywords:
        return True
    stores = " ".join(
        catalog.locations[sid].display_name for sid in _local_stores(catalog, goods.goods_id, current_city)
    )
    haystack = " ".join((goods.name, GOODS_CATEGORY_LABELS.get(goods.category, goods.category), stores)).casefold()
    return any(kw.casefold() in haystack for kw in keywords)


def _unavailable_reason(inputs: EnvironmentInputs, state: WorldState, agent_id: int,
                        goods_id: str, scoped_app_id: str | None) -> str:
    """@brief 将不可下单的商品状态映射为六句固定中文原因之一。"""
    catalog = inputs.catalog
    current_city = _current_city(catalog, state, agent_id)
    sellers = _local_selling_app_ids(catalog, goods_id, current_city)
    if scoped_app_id is not None and scoped_app_id not in sellers:
        return "这个 app 里没有"
    here = admission.present_location(state, agent_id)
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(inputs.config.timezone))
    stores = _local_stores(catalog, goods_id, current_city)
    if here in stores and not catalog.locations[here].is_open(local):
        return "这家店此刻已打烊"
    if scoped_app_id is not None:
        return "上了这个 app、卖这件商品的店此刻都没有营业"
    if any(not catalog.apps_by_store[sid] and catalog.locations[sid].is_open(local) for sid in stores):
        return "这家店没有上任何 app，要到店里买"
    return "此刻没有营业的店卖这件商品"


def _next_open_label(inputs: EnvironmentInputs, state: WorldState,
                     task: SearchTaskSpec, goods_id: str) -> str | None:
    """@brief 为当前打烊商品找出作用域内同城店铺当日接下来最早开放时刻。"""
    catalog = inputs.catalog
    current_city = _current_city(catalog, state, task.agent_id)
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(inputs.config.timezone))
    minute = local.hour * 60 + local.minute
    candidates: list[str] = []
    for sid in _local_stores(catalog, goods_id, current_city):
        if task.scoped_app_id is not None and task.scoped_app_id not in catalog.apps_by_store[sid]:
            continue
        for start_min, _ in catalog.locations[sid].opening_intervals(local.date()):
            if start_min > minute:
                candidates.append(f"{start_min // 60:02d}:{start_min % 60:02d}")
    return min(candidates) if candidates else None


def project_goods_row(inputs: EnvironmentInputs, state: WorldState, agent_id: int,
                      goods_id: str, scoped_app_id: str | None) -> dict[str, Any]:
    """@brief 与购买报价共用同一判定生成单件商品搜索事实行，禁止自造价格或通道。"""
    catalog = inputs.catalog
    goods = catalog.goods[goods_id]
    current_city = _current_city(catalog, state, agent_id)
    sellers = _local_selling_app_ids(catalog, goods_id, current_city)
    base: dict[str, Any] = {
        "goods_id": goods_id, "name": goods.name, "category": goods.category,
        "category_label": GOODS_CATEGORY_LABELS.get(goods.category, goods.category),
        "unit_hint": "1 份", "price_fen": goods.price_fen, "price_yuan": round(goods.price_fen / 100, 2),
        "app_ids": list(sellers), "selling_app_ids": list(sellers),
        "selling_apps": [catalog.apps[aid].name for aid in sellers],
    }
    try:
        offer = quote_goods(inputs, state, agent_id, goods_id, scoped_app_id)
    except BusinessRuleError:
        reason = _unavailable_reason(inputs, state, agent_id, goods_id, scoped_app_id)
        base.update(available=False, orderable=False, open_now=False, delivery_seconds=None,
                    reason=reason, unavailable_reason=reason, fulfillment=None, route=None,
                    total_price_fen=goods.price_fen, delivery_fee_fen=None, selling_app_id=None, app_id=None,
                    selling_app=None, store_id=None, store=None, store_name=None, rating=None,
                    store_rating=None, expected_at=None)
        return base
    store = None if offer.store_id is None else catalog.locations[offer.store_id]
    store_name = None if store is None else store.display_name
    store_rating = None if store is None else store.rating
    base.update(
        available=True, orderable=True, open_now=True,
        delivery_seconds=max(0, offer.expected_at - state.current_time),
        reason=None, unavailable_reason=None, fulfillment=offer.fulfillment,
        route=ROUTE_NAMES.get(offer.fulfillment), total_price_fen=goods.price_fen + offer.delivery_fee_fen,
        delivery_fee_fen=offer.delivery_fee_fen, selling_app_id=offer.app_id, app_id=offer.app_id,
        selling_app=None if offer.app_id is None else catalog.apps[offer.app_id].name,
        store_id=offer.store_id, store=store_name, store_name=store_name,
        rating=store_rating, store_rating=store_rating,
        expected_at=iso_time(offer.expected_at, inputs.config.timezone),
    )
    return base


def _inspect_one_goods(inputs: EnvironmentInputs, state: WorldState,
                       task: SearchTaskSpec, goods_id: str) -> dict[str, Any]:
    """@brief 检查单个商品在本作用域与当前城市内的存在性与履约报价细节。"""
    here = state.agents[task.agent_id].location_id
    bad, _ = validate_fact_ids(inputs.catalog, task.scoped_app_id, (goods_id,), (), here)
    if bad:
        return {"goods_id": goods_id, "found": False, "error": "Out of scope or unknown goods_id"}
    row = project_goods_row(inputs, state, task.agent_id, goods_id, task.scoped_app_id)
    available_from = None if row["available"] else _next_open_label(inputs, state, task, goods_id)
    return {"found": True, **row, "available_from": available_from}


def _matching_place_rows(inputs: EnvironmentInputs, state: WorldState,
                         task: SearchTaskSpec, req: SearchPlacesRequest) -> list[dict[str, Any]]:
    """@brief 筛选同城与直达异地枢纽地点并按最短路距离排序搜索候选。"""
    catalog = inputs.catalog
    here = admission.present_location(state, task.agent_id) or task.current_location_id
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(inputs.config.timezone))
    rows: list[tuple[tuple, dict[str, Any]]] = []
    for loc_id in sorted(catalog.locations):
        if not _place_in_scope(catalog, here, loc_id):
            continue
        location = catalog.locations[loc_id]
        if req.kind is not None and location.kind != req.kind:
            continue
        open_now = location.is_open(local)
        if req.open_only and not open_now:
            continue
        if not _matches_place_keywords(catalog, location, req.keywords):
            continue
        dist = catalog.graph.distance_meters(here, loc_id, city_wide=False)
        rows.append(((dist, loc_id), project_place_row(inputs, state, (task.agent_id, here), loc_id)))
    return [row for _, row in sorted(rows, key=lambda item: item[0])]


def _matches_place_keywords(catalog, location, keywords: tuple[str, ...] | list[str]) -> bool:
    """@brief 匹配地点显示名、类型或该地点内容单元名。"""
    if not keywords:
        return True
    units = " ".join(u.name for u in location_units(catalog, location.kind))
    haystack = " ".join((location.display_name, location.kind, units)).casefold()
    return any(kw.casefold() in haystack for kw in keywords)


def project_place_row(inputs: EnvironmentInputs, state: WorldState,
                      agent_here: tuple[int, str], loc_id: str) -> dict[str, Any]:
    """@brief 生成单个地点的营业、评星、已解锁内容单元与当前可行的出行方案。"""
    agent_id, here = agent_here
    catalog = inputs.catalog
    location = catalog.locations[loc_id]
    agent = state.agents[agent_id]
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(inputs.config.timezone))
    intervals = location.opening_intervals(local.date())
    opening_hours = ["全天营业"] if location.opens is None else (list(interval_phrases(intervals)) or ["今日休息"])
    unlocked = [unit.name for unit in location_units(catalog, location.kind) if not unit_locked(unit.contents, agent)]
    return {
        "location_id": loc_id, "name": location.display_name, "kind": location.kind,
        "open_now": location.is_open(local), "today_hours": "、".join(opening_hours),
        "opening_hours": opening_hours, "rating": location.rating,
        "sleepable": location.sleepable, "accepts_parcels": location.accepts_parcels,
        "units": unlocked, "has_content": bool(unlocked),
        "content_summary": "、".join(unlocked) if unlocked else None,
        "travel_options": _travel_options(inputs, state, here, loc_id),
    }


def _travel_options(inputs: EnvironmentInputs, state: WorldState,
                    origin: str, destination: str) -> list[dict[str, Any]]:
    """@brief 计算当前天气下前往目标地点的全部合法出行方式报价。"""
    if origin == destination:
        return []
    weather = trip_weather_factor(
        state, inputs.catalog.locations[origin].city_id, inputs.catalog.locations[destination].city_id,
    )
    options: list[dict[str, Any]] = []
    for method in travel_methods(inputs.catalog):
        try:
            trip = plan_trip(inputs.catalog.graph, origin, destination,
                             travel_method(inputs.catalog, method.travel_method_id), weather)
        except BusinessRuleError:
            continue
        options.append({"method": method.name, "fare_fen": trip.fare_fen,
                        "fare": money_phrase(trip.fare_fen),
                        "duration_seconds": trip.duration_seconds,
                        "duration": duration_phrase(trip.duration_seconds)})
    return options
