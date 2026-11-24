"""@brief 把搜索活动快照投影为人物可见的 details.search。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.foundation.time.readable import money_phrase
from worldmock.state.world.kinds import ROUTE_NAMES

SCOPED = "只列经这个 app 能买的"
ALL_ROUTES = "列出全部途径"
UNFINISHED = "没有查完"
SEARCH_GOODS_LIMIT = 20
_DEFAULT_UNAVAILABLE = "此刻没有营业的店卖这件商品"


def search_details(snapshot: Mapping[str, Any], inputs: EnvironmentInputs) -> dict[str, Any]:
    """@brief 把搜索快照投影成符合 PROGRESS_LAYER_SCHEMA 的完成态或未查完对象。"""
    data = snapshot["data"]
    if snapshot["status"] != "completed" or "answer" not in data:
        app_id = data.get("app_id")
        app_name = None if app_id is None else inputs.catalog.apps[app_id].name
        return {"app": app_name, "state": UNFINISHED}
    answer = data["answer"]
    app_id = answer["app_use"] if "app_use" in answer else data.get("app_id")
    app_name = None if app_id is None else inputs.catalog.apps[app_id].name
    scope = SCOPED if (answer.get("scoped") and app_name is not None) else ALL_ROUTES
    raw_goods = answer.get("goods_rows", ())
    shown = [_public_goods(row) for row in raw_goods[:SEARCH_GOODS_LIMIT]]
    places = [_public_place(row) for row in answer.get("place_rows", ())]
    result: dict[str, Any] = {
        "app": app_name,
        "scope": scope,
        "goods_rows": shown,
        "place_rows": places,
        "summary": answer.get("summary", ""),
    }
    omitted = int(answer.get("omitted") or 0) + max(0, len(raw_goods) - len(shown))
    if omitted > 0:
        result["omitted"] = f"还有 {omitted} 件没列"
    return result


def _public_goods(row: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 投影单件搜索命中商品的路线、商铺评分、费用、期望时刻与可买原因，不含内部身份。"""
    store = row.get("store")
    available = bool(row.get("available"))
    fee_fen = row.get("delivery_fee_fen")
    item: dict[str, Any] = {
        "product": row["name"],
        "route": ROUTE_NAMES.get(row.get("fulfillment")),
        "store": store,
    }
    if store is not None:
        item["rating"] = row["rating"]
    item.update({
        "apps": list(dict.fromkeys(row.get("selling_apps", ()))),
        "price": money_phrase(row["price_fen"]),
        "delivery_fee": None if fee_fen is None else money_phrase(fee_fen),
        "expected_at": row.get("expected_at"),
        "now": "可以下单" if available else "不可下单",
        "reason": None if available else (row.get("unavailable_reason") or row.get("reason") or _DEFAULT_UNAVAILABLE),
    })
    return item


def _public_place(row: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 投影单个搜索命中地点的名称、时段数组、评分、可做项目与出行选项。"""
    raw_hours = row.get("opening_hours") if row.get("opening_hours") is not None else row.get("today_hours", ())
    if isinstance(raw_hours, str):
        hours = [part.strip() for part in raw_hours.split("、") if part.strip()] or ["今日休息"]
    else:
        hours = [str(part) for part in raw_hours if str(part).strip()] or ["今日休息"]
    return {
        "name": row["name"],
        "kind": row["kind"],
        "open_now": bool(row["open_now"]),
        "today_hours": hours,
        "rating": row.get("rating"),
        "units": list(row.get("units", ())),
        "travel_options": [
            {"method": opt["method"], "fare": opt["fare"], "duration": opt["duration"]}
            for opt in row.get("travel_options", ())
        ],
    }
