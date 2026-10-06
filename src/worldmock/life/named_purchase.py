"""@brief 按真实食品名称、店铺及 app 来源分段解析购买，失败整组原子拒绝。"""

from __future__ import annotations
from worldmock.life.tool_layer import Candidate, refusal_for

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from worldmock.errors import PreconditionError
from worldmock.life.error_contract import MAX_CANDIDATES, NAME_REJECTION_CODES, ErrorItem, activity_candidate
from worldmock.life.guidance import (Refusal, default_reason,
                                     items_refusal, refusal_for_code)
from worldmock.life.interaction_menu import NAMED_SUPPLY_KIND, binding_action
from worldmock.life.vocabulary import PURCHASE_WAYS, SYSTEM_PURCHASE_WAY
from worldmock.life.name_resolution import rank
from worldmock.life.object_labels import resolve_name
from worldmock.life.order_routing import undeliverable
from worldmock.life.purchase_offers import (DELIVERY_WAY, PICKUP_WAY, Offer, SourceScope, choose, handed_over,
                                            menu_foods, near_sources, nearest_store, offers, order_key, serves,
                                            source_scope)
from worldmock.life.readable import clock_phrase, money_phrase
from worldmock.life.wayfinding import go_step

if TYPE_CHECKING:
    from worldmock.life.tool_layer import LayerContext

_LOGGER = logging.getLogger(__name__)
#: SUPPLY_NOT_FOUND 至多列出的近似标签数，也是逐项明细中至多给出的候选数。
MAX_NEAR = MAX_CANDIDATES
#: 分段匹配 M3 为空时的拒绝码（引导性拒绝，不是名称类）。
_UNMET = "ACTIVITY_PRECONDITION_UNMET"
#: 方式短语 → 履约方式。
_MODES = {way: mode for mode, way in PURCHASE_WAYS.items()}


@dataclass(frozen=True, slots=True)
class PurchaseAsk:
    """@brief 一项指名购买的请求事实。

    :param written: 人物在 items[].target 中写的名称（完整标签「<门店>出售的<商品>」或商品名）。
    :param way: 到店、远程下单或「购买」。
    :param source: from：null、门店名或渠道称呼（其余写法报 SOURCE_NOT_FOUND）。
    :param destination_id: 收货地（收货人此刻的收货地点：送给自己为人此刻所在的地点，也是到店时东西交到收货人手上的
        地方）；收货人此刻没有收货地点时为 None（送不到，到店也交不到他手上）。
    :param prefer: 请求级偏好“最快拿到”或“到手价最低”，购买不接受 null。
    :param receiver: 收货地的可读说法（地点显示名，或「<收货人>那里」），只用于原因。
    :param recipient_id: 送给别人时收货人的身份（送不到时的原因与下一步按收货人写，order_routing.undeliverable）；送给
        自己时为 None。
    """

    written: str
    way: str
    source: str | None
    destination_id: str | None
    prefer: str
    receiver: str = "the current place"
    recipient_id: str | None = None


def resolve_named_supply(context: LayerContext, ask: PurchaseAsk) -> dict:
    """@brief 把一项指名购买按分段匹配解析为选定的门店供给的绑定行（selected_action 为定下的到店或远程下单，quote 为当前
    报价，另带私有的 channel_id 与 purchase_note）。

    :raises PreconditionError: SUPPLY_NOT_FOUND、SOURCE_NOT_FOUND、SUPPLY_SOURCE_MISMATCH、SUPPLY_NOT_AT_STORE、
        FULFILLMENT_NOT_SUPPORTED 或 ACTIVITY_PRECONDITION_UNMET；逐项明细（参数名、原文与候选）写在私有
        suggested.item，合并时换成可照填的请求（resolve_named_rows）。
    """
    supplies = menu_foods(context)
    labelled = _named(ask.written, supplies, lambda supply: supply_label(context, supply))
    matches = labelled or _named(ask.written, supplies, lambda supply: _product_name(context, supply))
    if not matches:
        _LOGGER.error("Named purchase matches no supply")
        raise _refusal("SUPPLY_NOT_FOUND", _Item("target", ask.written, _near_specs(context, ask)))
    scope = source_scope(context, ask.source)
    if scope is None:
        _LOGGER.error("Named purchase source is neither a store nor a channel")
        raise _source_not_found(context, ask, matches)
    kept = [supply for supply in matches if scope.contains(supply[0])]
    if not kept:
        _LOGGER.error("Named purchase source does not cover the named supply")
        raise _mismatch(context, ask, labelled[0], scope) if labelled else _not_at_store(context, ask, matches, scope)
    return _staged(context, ask, (kept, matches), scope)


def resolve_named_rows(context: LayerContext, selection: list[dict], resolve: Callable[[dict], dict]) -> list[dict]:
    """@brief 逐项把指名购买的占位行解析为门店供给的绑定行（resolve 解析一项），其余行原样。

    分段匹配的拒绝（带逐项明细的名称类拒绝与 M3 的 ACTIVITY_PRECONDITION_UNMET）逐项收集，合并成一次拒绝：items[] 逐项写出
    错项的序号（从 0 起）、参数名、原文与至多 3 个可以照填的候选请求，原因与改法逐项写「第 N 项」；码取第一个名称类拒绝的码
    （名称类优先，docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析  第 5 条、3.2（d）），没有名称类时取 ACTIVITY_PRECONDI
    TION_UNMET 并带
    第一项的下一步与未满足条件；其余拒绝报第一条。整组受理仍原子：有一项解析不出就整组拒绝。

    :param selection: start_activity 各项解析好的绑定行（指名购买为占位行），按请求中的先后排列。
    :param resolve: 把一项占位行解析为供给绑定行的函数（按收货人的收货地点解析，见 session_actions）。
    """
    rows: list[dict] = []
    failures: list[tuple[int, dict, PreconditionError]] = []
    other: PreconditionError | None = None
    for index, row in enumerate(selection):
        if row["kind"] != NAMED_SUPPLY_KIND:
            rows.append(row)
            continue
        try:
            rows.append(resolve(row))
        except PreconditionError as error:
            if (error.suggested or {}).get("item") is None:
                other = other or error
                continue
            failures.append((index, row, error))
    if failures:
        _LOGGER.error("Named purchase items failed resolution: %s", [str(error) for _, _, error in failures])
        raise _merged(failures, len(selection), context).error()
    if other is not None:
        raise other
    return rows


def near_labels(context: LayerContext, name: str, fulfillment: str | None, destination_id: str | None) -> list[str]:
    """@brief 至多 3 个与名称最相近的供给标签（name_resolution.rank，跨全部供给）：此刻能按所写方式服务的在前（到店要人在
    那家店、东西在那里交得到收货人手上，远程下单要能送到收货地；fulfillment 为 None 表示「购买」，两者之一即可，
    purchase_offers.serves），其次按名称与标签、商品名两者 Dice 系数的较大者降序、标签升序；相似度为 0 的不列。确定性，
    不用向量检索与近似最近邻，从不自动替换。"""
    way = PURCHASE_WAYS.get(fulfillment, SYSTEM_PURCHASE_WAY)
    chosen = rank(name, menu_foods(context),
                  lambda supply: (supply_label(context, supply), _product_name(context, supply)),
                  lambda supply: serves(context, supply, way, destination_id))
    return [supply_label(context, supply) for supply in chosen]


def supply_label(context: LayerContext, supply) -> str:
    """@brief 供给的可读标签「<门店>出售的<商品>」（与工具层的商品目标名称同一写法）。"""
    return f"{_store_name(context, supply)}出售的{_product_name(context, supply)}"


@dataclass(frozen=True, slots=True)
class _Item:
    """@brief 一项拒绝的逐项明细（私有 suggested.item）：出错的参数名、原文与候选（{target, way, from}，合并时补上动词与
    去向）。"""

    field: str
    written: str
    candidates: tuple[dict, ...] = ()

    def payload(self) -> dict:
        """@brief 写进私有 suggested.item 的形状。"""
        return {"field": self.field, "written": self.written, "candidates": list(self.candidates[:MAX_NEAR])}


def _staged(context: LayerContext, ask: PurchaseAsk, pools: tuple[list, list], scope: SourceScope) -> dict:
    """@brief 按方式和当前可服务条件收敛真实菜单，并保留实际 app 身份。"""
    kept, matches = pools
    if scope.app_id is not None and ask.way == PICKUP_WAY:
        _LOGGER.error("App source conflicts with onsite pickup")
        raise _refusal("SUPPLY_SOURCE_MISMATCH", _Item("from", ask.source))
    if ask.way != SYSTEM_PURCHASE_WAY:
        supported = [food for food in kept if _MODES[ask.way] in _modes(context, food)]
        if not supported:
            _LOGGER.error("Store has no delivery app")
            raise _unsupported(context, ask, kept)
        kept = supported
    way, served = offers(context, kept, ask.way, ask.destination_id)
    if not served:
        _LOGGER.error("Named food cannot be received at the current location")
        raise _unserved(context, ask, kept, matches)
    chosen = choose(served, ask.prefer)
    app_id = (scope.app_id or chosen.app_id) if way == DELIVERY_WAY else None
    return {**_binding(context, (chosen.store_id, chosen.food), way), "app_id": app_id,
            "purchase_note": _note(context, ask, chosen, len(served), app_id)}


def _named(name: str, supplies, naming) -> list:
    """@brief 名称按统一名称解析的逐级归一比对（第一级有匹配就停）后匹配到的供给；naming 取一件供给的比对名称。"""
    names = [naming(supply) for supply in supplies]
    match = resolve_name(name, names)
    return [supply for supply, value in zip(supplies, names) if value in match.matches]


def _store_name(context: LayerContext, supply) -> str:
    """@brief 供给所在门店（地点）的显示名。"""
    return _place(context, supply[0])


def _place(context: LayerContext, location_id: str) -> str:
    """@brief 登记地点含分店标记的唯一显示名。"""
    return context.semantics.catalog.locations[location_id].display_name


def _product_name(context: LayerContext, supply) -> str:
    """@brief 菜单中实际目录食品的名称。"""
    return supply[1].name


def _way_for(context: LayerContext, supply, way: str) -> str:
    """@brief 候选只提供实际店铺支持的购买方式。"""
    return way if way == SYSTEM_PURCHASE_WAY or _MODES.get(way) in _modes(context, supply) else PICKUP_WAY


def _spec(context: LayerContext, supply, way: str) -> dict:
    """@brief 以完整标签写成的候选（from 为 null：标签已写明门店）。"""
    return {"target": supply_label(context, supply), "way": way, "from": None}


def _near_specs(context: LayerContext, ask: PurchaseAsk) -> tuple[dict, ...]:
    """@brief SUPPLY_NOT_FOUND 的候选：至多 3 个近似标签，方式按该供给支持的取。"""
    supplies = {supply_label(context, supply): supply for supply in menu_foods(context)}
    fulfillment = _MODES.get(ask.way)
    return tuple(_spec(context, supplies[label], _way_for(context, supplies[label], ask.way))
                 for label in near_labels(context, ask.written, fulfillment, ask.destination_id))


def _source_not_found(context: LayerContext, ask: PurchaseAsk, matches: Sequence) -> PreconditionError:
    """@brief SOURCE_NOT_FOUND：from 不是门店名也不是渠道称呼；候选为最相近的门店名与 App 称呼（范围内此刻能服务的在前），
    写成同一目标与方式、换了 from 的请求。"""
    def feasible(stores: frozenset[str]) -> bool:
        """@brief 这个来源范围里此刻有没有能服务的同名供给。"""
        return any(supply[0] in stores and serves(context, supply, ask.way, ask.destination_id)
                   for supply in matches)

    specs = tuple({"target": ask.written, "way": ask.way, "from": name}
                  for name in near_sources(context, ask.source, feasible))
    return _refusal("SOURCE_NOT_FOUND", _Item("from", ask.source, specs),
                    default_reason("SOURCE_NOT_FOUND"))


def _mismatch(context: LayerContext, ask: PurchaseAsk, supply, scope: SourceScope) -> PreconditionError:
    """@brief SUPPLY_SOURCE_MISMATCH：完整标签写明的门店不在 from 的范围内（另一家门店，或不含该门店的渠道）；候选就是
    这个标签（from 为 null）。原因接在所写的 from 之后（合并时写成「「from」：原因」）。"""
    store = _store_name(context, supply)
    reason = default_reason("SUPPLY_SOURCE_MISMATCH")
    return _refusal("SUPPLY_SOURCE_MISMATCH",
                    _Item("from", ask.source, (_spec(context, supply, _way_for(context, supply, ask.way)),)), reason)


def _not_at_store(context: LayerContext, ask: PurchaseAsk, matches: Sequence, scope: SourceScope
                  ) -> PreconditionError:
    """@brief SUPPLY_NOT_AT_STORE：from 的范围里没有这件商品；那家店完全没有供给时原因写「这里没有可买的商品」；候选为出售
    它的门店的标签（此刻能服务的在前）。原因接在所写的 from 之后。"""
    reason = default_reason("SUPPLY_NOT_AT_STORE")
    chosen = rank(ask.written, matches, lambda supply: (supply_label(context, supply), _product_name(context, supply)),
                  lambda supply: serves(context, supply, ask.way, ask.destination_id))
    specs = tuple(_spec(context, supply, _way_for(context, supply, ask.way)) for supply in chosen)
    return _refusal("SUPPLY_NOT_AT_STORE", _Item("from", ask.source, specs), reason)


def _unsupported(context: LayerContext, ask: PurchaseAsk, kept: Sequence) -> PreconditionError:
    """@brief 无配送 app 的店铺拒绝显式远程下单，给实际到店候选。"""
    specs = tuple(_spec(context, food, PICKUP_WAY) for food in kept)
    return _refusal("FULFILLMENT_NOT_SUPPORTED", _Item("way", ask.way, specs),
                    default_reason("FULFILLMENT_NOT_SUPPORTED"),
                    f"Go to {_store_name(context, kept[0])}.")


def _unserved(context: LayerContext, ask: PurchaseAsk, kept: Sequence, matches: Sequence) -> PreconditionError:
    """@brief 实际收货条件不足时给前往门店或等待行程结束的真实引导。"""
    if ask.way == PICKUP_WAY or (ask.way == SYSTEM_PURCHASE_WAY and
                               not any("delivery" in _modes(context, food) for food in kept)):
        reason, step, unmet = _unreached(context, kept)
    elif ask.recipient_id is None and ask.destination_id is None:
        reason = "Place the order before departing or after arriving."
        step, unmet = "Wait until you arrive, then place the order.", ("delivery_destination",)
    else:
        reason, step, unmet = _undelivered(context, ask)
    return _refusal(_UNMET, _Item("target", ask.written, _served_specs(context, ask, matches)), reason, step, unmet)


def _unreached(context: LayerContext, pickable: Sequence) -> tuple[str, str, tuple[str, ...]]:
    """@brief 人不在能到店买它的出售门店里时的（原因，下一步，未满足条件）：下一步去从当前地点出发路线最短的那家能到店买的
    门店（登记图查询后直接提示前往门店）；到店写
    要人在哪家店。

    :param pickable: 范围内支持到店的同名供给（到店时即 kept）。
    """
    places = {item.location_id: item.display_name for item in context.scenario.locations}
    origin = context.state.agents[context.agent_id].location_id
    nearest = nearest_store(context, (supply[0] for supply in pickable))
    step = go_step(context.semantics.catalog.graph, places, origin, nearest)
    several = len({supply[0] for supply in pickable}) > 1
    reason = (f"Pickup requires the caller at a selling store; the nearest is {places[nearest]}." if several
              else f"Pickup requires the caller at {places[nearest]}.")
    return reason, step, ("required_location_id",)


def _undelivered(context: LayerContext, ask: PurchaseAsk) -> tuple[str, str, tuple[str, ...]]:
    """@brief 收货人没有收货地点或到店不在场时，沿用实际收货人的拒绝与当面转交提示。"""
    error = undeliverable(context.state, context.scenario, context.agent_id, ask.recipient_id)
    return error.public_reason, error.suggested["next_step"], tuple(error.unmet)


def _served_specs(context: LayerContext, ask: PurchaseAsk, matches: Sequence) -> tuple[dict, ...]:
    """@brief 此刻能服务的同名供给写成的候选（不论 from 与所写方式）：人在店里、且东西在店里交得到收货人手上的写到店
    （purchase_offers.handed_over，交不到的到店候选照填也会被结算拒绝），其余能送到收货地的写远程下单；按最快排序，至多
    3 个。"""
    pickups = handed_over(offers(context, matches, PICKUP_WAY, ask.destination_id)[1], ask.destination_id)
    taken = {(offer.store_id, offer.food.food_id) for offer in pickups}
    deliveries = [offer for offer in offers(context, matches, DELIVERY_WAY, ask.destination_id)[1]
                  if (offer.store_id, offer.food.food_id) not in taken]
    ordered = sorted(pickups, key=lambda offer: order_key(offer, "最快拿到")) + sorted(
        deliveries, key=lambda offer: order_key(offer, "最快拿到"))
    return tuple(_spec(context, (offer.store_id, offer.food), offer.way) for offer in ordered[:MAX_NEAR])


def _note(context: LayerContext, ask: PurchaseAsk, chosen: Offer, count: int,
          app_id: str | None) -> str | None:
    """@brief 受理说明记录真实来源 app、确定性选店及当前请求偏好。"""
    clauses = [f"「购买」定为{chosen.way}"] if ask.way == SYSTEM_PURCHASE_WAY else []
    if count > 1:
        clauses.append(f"按{ask.prefer}选择{supply_label(context, (chosen.store_id, chosen.food))}")
    if app_id is not None:
        clauses.append(f"通过{context.semantics.catalog.apps[app_id].name}下单")
    if chosen.expected_at is not None:
        when = clock_phrase(chosen.expected_at, context.state.current_time, context.scenario.timezone)
        clauses.append(f"预计 {when} 送到")
    if ask.prefer == "到手价最低":
        clauses.append(f"到手价 {money_phrase(chosen.price)}")
    return "，".join(clauses) or None


def _binding(context: LayerContext, supply, way: str) -> dict:
    """@brief 从实际门店和 Food 形成真实购买绑定，没有报价或可见凭据。"""
    store_id, food = supply
    action = binding_action(way, "purchase", fulfillment=_MODES[way])
    return {"object": supply_label(context, supply), "kind": "supply", "object_id": f"{store_id}:{food.food_id}",
            "store_id": store_id, "food_id": food.food_id, "actions": [action], "named": True,
            "selected_action": action}


def _refusal(code: str, item: _Item, reason: str | None = None, next_step: str | None = None,
             unmet: tuple[str, ...] = ()) -> PreconditionError:
    """@brief 一项分段匹配的拒绝：原因缺省取该码的默认说法，下一步缺省取默认做法，改法取默认改法；逐项明细写在私有
    suggested.item，合并时换成可照填的请求（resolve_named_rows）。"""
    default = refusal_for_code(code)
    step = next_step if next_step is not None else default.next_step
    return Refusal(code, reason or default.reason, step, unmet,
                   suggested={"next_step": step, "item": item.payload()}, fix=default.fix).error()


def _merged(failures: list[tuple[int, dict, PreconditionError]], total: int, context: LayerContext) -> Refusal:
    """@brief 逐项拒绝合并成一次：items[] 按请求先后逐项写出；码取第一个名称类拒绝的码（名称类优先），没有名称类时取
    ACTIVITY_PRECONDITION_UNMET，带该项的下一步与未满足条件。"""
    entries = [(_error_item(index, row, error, context), error.public_reason or default_reason(str(error)))
               for index, row, error in failures]
    lead = next((error for _, _, error in failures if str(error) in NAME_REJECTION_CODES), failures[0][2])
    merged = items_refusal(entries, total)
    if str(lead) in NAME_REJECTION_CODES:
        return replace(merged, code=str(lead), next_step=refusal_for_code(str(lead)).next_step)
    return replace(merged, code=str(lead), next_step=(lead.suggested or {}).get("next_step") or "",
                   unmet=tuple(lead.unmet or ()))


def _error_item(index: int, row: dict, error: PreconditionError, context: LayerContext) -> ErrorItem:
    """@brief 一项拒绝的明细：参数名与原文取私有 suggested.item，候选补上动词 obtain 与该项所写的去向，写成可以照填的
    请求（至多 3 个）。"""
    item = error.suggested["item"]
    candidates = tuple(_request_candidate(context, row, spec) for spec in item["candidates"])[:MAX_NEAR]
    allowed = tuple(dict.fromkeys(spec["way"] for spec in item["candidates"])) if item["field"] == "way" else ()
    return ErrorItem(("items", index, item["field"]), item["written"], str(error), allowed,
                     "A known product, valid source and serviceable fulfillment are required.",
                     error.public_reason or default_reason(str(error)), candidates)


def _request_candidate(context: LayerContext, row: dict, spec: dict):
    """@brief 候选完整可提交，冻结业务条件不满足时明确说明。"""

    supplies = [supply for supply in menu_foods(context) if supply_label(context, supply) == spec["target"]]
    note = None
    if len(supplies) == 1 and spec["way"] != SYSTEM_PURCHASE_WAY:
        binding = {**_binding(context, supplies[0], spec["way"]),
                   **{key: row[key] for key in ("recipient_id", "to_self", "to") if key in row}}
        refusal = refusal_for(context, Candidate("start_activity", binding, binding["selected_action"]))
        note = refusal.reason if refusal else None
    return activity_candidate(spec["target"], spec["way"], (spec["from"], row["to"]), note)


def _modes(context: LayerContext, food) -> tuple[str, ...]:
    """@brief 所有门店均可到店，只有绑定 app 的门店可配送。"""
    return ("pickup", "delivery") if context.semantics.catalog.apps_by_store[food[0]] else ("pickup",)
