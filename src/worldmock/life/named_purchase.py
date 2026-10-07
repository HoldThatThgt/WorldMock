"""@brief 商品名称与明确应用选择解析为实际购买，拒绝时整组保持原子。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from worldmock.errors import PreconditionError
from worldmock.exchange.types import AppPurpose, GoodsCategory, LocationKind
from worldmock.life.admission import present_location
from worldmock.life.error_contract import ErrorItem, activity_candidate
from worldmock.life.guidance import Refusal, default_reason, guided, refusal_for_code
from worldmock.life.interaction_menu import NAMED_SUPPLY_KIND, binding_action
from worldmock.life.object_labels import resolve_name
from worldmock.life.purchase_offers import choose, menu_goods, nearest_store, offers
from worldmock.life.recipients import person_name, recipient_location, undeliverable

if TYPE_CHECKING:
    from worldmock.life.tool_layer import LayerContext

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PurchaseAsk:
    """@brief 一项购买只携带商品名、两个应用名与真实收货人。"""

    written: str
    app: str | None
    payment_app: str | None
    recipient_id: str


def _app_id(context: LayerContext, name: str | None) -> str | None:
    """@brief 仅唯一匹配已登记应用名称，不选择默认应用。"""
    if name is None:
        return None
    catalog = context.semantics.catalog
    match = resolve_name(name, [app.name for app in catalog.apps.values()])
    return next((identity for identity, app in catalog.apps.items()
                 if len(match.matches) == 1 and app.name in match.matches), None)


def _choice_error(context: LayerContext, ask: PurchaseAsk, field: str, code: str,
                  allowed: tuple[str, ...]) -> PreconditionError:
    """@brief 应用拒绝保留全部可用名称，候选为可直接提交的完整请求。"""
    written = ask.app if field == "app" else ask.payment_app
    receiver = None if ask.recipient_id == f"agent:{context.agent_id}" else _receiver_name(context, ask.recipient_id)
    candidates = tuple(activity_candidate(ask.written, "购买", (
        value if field == "app" else ask.app, receiver, value if field == "pay_app" else ask.payment_app))
        for value in allowed[:3])
    item = ErrorItem(("items", 0, field), written, code, allowed,
                     "Use a declared app offered for this purchase.", default_reason(code), candidates)
    return Refusal(code, default_reason(code), items=(item,), fix=refusal_for_code(code).fix).error()


def _receiver_name(context: LayerContext, identity: str) -> str:
    """@brief 同世界人物与本人通讯录联系人使用统一可读实名。"""
    return person_name(context.state, context.scenario, context.agent_id, identity)


def _payment(context: LayerContext, ask: PurchaseAsk, app_id: str | None) -> str | None:
    """@brief 到店允许空支付，在线必须选择购物应用明确绑定的支付应用。"""
    catalog = context.semantics.catalog
    allowed_ids = (catalog.apps[app_id].payment_app_ids if app_id else
                   tuple(identity for identity, app in catalog.apps.items() if AppPurpose.PAYMENT in app.purposes))
    allowed = tuple(catalog.apps[identity].name for identity in sorted(allowed_ids))
    if app_id and ask.payment_app is None:
        raise _choice_error(context, ask, "pay_app", "PAYMENT_APP_REQUIRED", allowed)
    identity = _app_id(context, ask.payment_app)
    if ask.payment_app is not None and identity not in allowed_ids:
        raise _choice_error(context, ask, "pay_app", "PAYMENT_APP_NOT_BOUND", allowed)
    return identity


def resolve_named_supply(context: LayerContext, ask: PurchaseAsk) -> dict:
    """@brief 只按商品名匹配并选择真实履约店铺，保留明确应用和支付身份。"""
    catalog = context.semantics.catalog
    match = resolve_name(ask.written, [goods.name for goods in catalog.goods.values()])
    if len(match.matches) != 1:
        _LOGGER.error("Named purchase does not uniquely identify registered goods")
        raise refusal_for_code("SUPPLY_NOT_FOUND").error()
    goods = next(goods for goods in catalog.goods.values() if goods.name in match.matches)
    if goods.category == GoodsCategory.FUEL:
        config = next(agent for agent in context.scenario.agents if agent.agent_id == context.agent_id)
        here = context.state.agents[context.agent_id].location_id
        valid = (config.owns_car and ask.app is None and ask.recipient_id == f"agent:{context.agent_id}"
                 and context.semantics.catalog.locations[here].kind == LocationKind.GAS_STATION
                 and present_location(context.state, context.agent_id) == here)
        if not valid:
            raise refusal_for_code("FUEL_PURCHASE_INVALID").error()
    app_id = _app_id(context, ask.app)
    shopping = tuple(identity for identity, app in catalog.apps.items() if AppPurpose.SHOPPING in app.purposes)
    if ask.app is not None and app_id not in shopping:
        allowed = tuple(catalog.apps[identity].name for identity in sorted(shopping))
        raise _choice_error(context, ask, "app", "APP_NOT_OFFERED", allowed)
    payment = _payment(context, ask, app_id)
    stores = {store for store, item in menu_goods(context) if item.goods_id == goods.goods_id}
    if app_id is not None and not any(app_id in catalog.apps_by_store[store] for store in stores):
        allowed = tuple(catalog.apps[identity].name for identity in sorted(shopping)
                        if any(identity in catalog.apps_by_store[store] for store in stores))
        raise _choice_error(context, ask, "app", "SUPPLY_NOT_AT_STORE", allowed)
    served = offers(context, goods.goods_id, app_id, ask.recipient_id)
    if not served:
        raise _unserved(context, ask, stores)
    chosen = choose(served)
    action = binding_action("购买", "purchase", fulfillment=chosen.fulfillment, app_id=app_id,
                           payment_app_id=payment)
    return {"object": goods.name, "kind": "supply", "object_id": goods.goods_id, "goods_id": goods.goods_id,
            "store_id": chosen.store_id, "app_id": app_id, "payment_app_id": payment,
            "destination_id": chosen.destination_id, "expected_at": chosen.expected_at,
            "actions": [action], "selected_action": action}


def _unserved(context: LayerContext, ask: PurchaseAsk, stores: set[str]) -> PreconditionError:
    """@brief 收货人不可收货与本人不在售货店采用各自权威原因。"""
    here = context.state.agents[context.agent_id].location_id
    if ask.app is None and here not in stores:
        location = nearest_store(context, stores)
        name = context.semantics.catalog.locations[location].display_name
        return guided("ACTIVITY_PRECONDITION_UNMET", f"Purchase requires presence at {name}.",
                      f"Go to {name}.", ("required_location_id",))
    return undeliverable(context.state, context.scenario, context.agent_id, ask.recipient_id)


def resolve_named_rows(context: LayerContext, selection: list[dict], resolve: Callable[[dict], dict]) -> list[dict]:
    """@brief 逐项解析购买并合并错误，任何失败都不受理整组。"""
    rows, errors = [], []
    for index, row in enumerate(selection):
        if row["kind"] != NAMED_SUPPLY_KIND:
            rows.append(row)
            continue
        try:
            rows.append(resolve(row))
        except PreconditionError as error:
            _LOGGER.error("Named purchase item failed resolution: %s", str(error))
            errors.append((index, error))
    if errors:
        first = errors[0][1]
        suggested = dict(first.suggested or {})
        items = []
        for index, error in errors:
            for item in (error.suggested or {}).get("items", []):
                path = list(item["path"])
                position = path.index("items") + 1
                path[position] = index
                items.append({**item, "path": path})
        if items:
            suggested["items"] = items
        raise PreconditionError(str(first), public_reason=first.public_reason,
                                unmet=first.unmet, suggested=suggested or None)
    return rows
