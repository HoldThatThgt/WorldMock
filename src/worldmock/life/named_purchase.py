"""@brief 指名购买：obtain 的目标按名称解析为门店供给（docs/dev/SPEC-persona-prompt.md 第五章「指名购买」，
docs/dev/SPEC-dayrun-fidelity.md 3.2、3.3、3.5）。

环境中存在的商品不因轮转未展示而禁止购买（裁决 #19）。名称按统一名称解析的逐级归一比对（worldmock.life.name_resolution：
精确，再按全半角、括号、空白与「（模拟）」归一，第一级有匹配就停）。from 是可选约束，不是语法：null（全部门店）、门店名
（该门店）或渠道称呼（该渠道的成员门店，记下 channel_id；purchase_offers.source_scope）。分段匹配是错误骨架，依次判定、
各报各的码：

- M0 按名称匹配：先匹配完整标签「<门店>出售的<商品>」，再按商品名；为空报 SUPPLY_NOT_FOUND（只表示没有这件商品），候选为
  至多 3 个近似标签（此刻能按所写方式服务的在前，name_resolution.rank：单字加二字的 Dice 系数，不用向量检索）；
- 来源：from 不是门店名也不是渠道称呼时报 SOURCE_NOT_FOUND，候选为最相近的门店名与 App 称呼；
- M1 = M0 ∩ from 的范围（from 为 null 时跳过）：完整标签的门店不在范围内（from 是另一家门店、或不含该门店的渠道）报
  SUPPLY_SOURCE_MISMATCH，候选就是这个标签；商品名为空报 SUPPLY_NOT_AT_STORE（那家店完全没有供给时原因写「这里没有可买的
  商品」），候选为出售它的门店的标签；
- M2 = M1 ∩ 所写方式（方式为「购买」时跳过）：为空报 FULFILLMENT_NOT_SUPPORTED 并说明原因（如「只能远程下单」）；
- M3 = M2 ∩ 此刻能服务（到店：人在店里；远程下单：能送到收货地；「购买」：人在出售它的店里、且东西在那里交得到收货人手上——
  收货人是自己，或此刻也在那家店里——就到店，否则能送到收货地就远程下单，purchase_offers.offers）：为空以
  ACTIVITY_PRECONDITION_UNMET 说明要去哪里（到店与送给自己的「购买」：从当前地点出发路线最短的那家能到店买的出售门店）或哪家
  送不到哪里（远程下单、范围内都只能远程下单的「购买」与送给别人的「购买」；只有一家出售门店时与非指名的远程下单同一说法，
  5.3：送给自己为「这里…」，送给别人为 order_routing.undeliverable 的「<名>那里…」，下一步先送到自己这里再当面给），并附此刻
  能服务的同名候选；多于一个时按请求级 prefer 选定一家（purchase_offers.choose），所选写进 details.notes，商品购买不再以
  SUPPLY_NAME_AMBIGUOUS 拒绝（3.5「收敛」第 5 条）。

恰好选定一条时返回该供给按当前报价的绑定行（named 标记：购买时不查可见凭据，storefront_runtime），另带私有的 channel_id
（订单载荷与 purchases.jsonl 各记一列）与 purchase_note（受理结果的 details.notes）；价格、余额、营业与库存由整单结算照常
检查，从不自动换成别的商品。一个请求中的几项逐项解析（resolve_named_rows）：各段拒绝逐项收集、合并成一次拒绝，逐项明细
items[] 的候选写成可以照填的请求 {target, action, way, from, to}（第四章「错误合同」），整组受理仍原子。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from worldmock.errors import PreconditionError
from worldmock.life.discovery import DiscoveryContext, supply_row
from worldmock.life.error_contract import MAX_CANDIDATES, NAME_REJECTION_CODES, ErrorItem, activity_candidate
from worldmock.life.guidance import (DELIVERY_DESTINATION_REASON, DELIVERY_ROUTE_REASON, Refusal, default_reason,
                                     items_refusal, refusal_for_code)
from worldmock.life.interaction_menu import NAMED_SUPPLY_KIND, PURCHASE_WAYS, SYSTEM_PURCHASE_WAY, binding_action
from worldmock.life.name_resolution import rank, resolve_name
from worldmock.life.order_routing import undeliverable
from worldmock.life.purchase_offers import (DELIVERY_WAY, PICKUP_WAY, Offer, SourceScope, choose, handed_over,
                                            near_sources, nearest_store, offers, order_key, serves, source_scope)
from worldmock.life.readable import clock_phrase, money_phrase
from worldmock.life.wayfinding import UNREACHABLE_STORE_STEP, delivery_route_code, delivery_step, go_step

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
    supplies = context.scenario.supplies
    labelled = _named(ask.written, supplies, lambda supply: supply_label(context, supply))
    matches = labelled or _named(ask.written, supplies, lambda supply: _product_name(context, supply))
    if not matches:
        _LOGGER.error("Named purchase matches no supply")
        raise _refusal("SUPPLY_NOT_FOUND", _Item("target", ask.written, _near_specs(context, ask)))
    scope = source_scope(context, ask.source)
    if scope is None:
        _LOGGER.error("Named purchase source is neither a store nor a channel")
        raise _source_not_found(context, ask, matches)
    kept = [supply for supply in matches if scope.contains(supply.location_id)]
    if not kept:
        _LOGGER.error("Named purchase source does not cover the named supply")
        raise _mismatch(context, ask, labelled[0], scope) if labelled else _not_at_store(context, ask, matches, scope)
    return _staged(context, ask, (kept, matches), scope)


def resolve_named_rows(context: LayerContext, selection: list[dict], resolve: Callable[[dict], dict]) -> list[dict]:
    """@brief 逐项把指名购买的占位行解析为门店供给的绑定行（resolve 解析一项），其余行原样。

    分段匹配的拒绝（带逐项明细的名称类拒绝与 M3 的 ACTIVITY_PRECONDITION_UNMET）逐项收集，合并成一次拒绝：items[] 逐项写出
    错项的序号（从 0 起）、参数名、原文与至多 3 个可以照填的候选请求，原因与改法逐项写「第 N 项」；码取第一个名称类拒绝的码
    （名称类优先，docs/dev/SPEC-dayrun-fidelity.md 3.1 第 5 条、3.2（d）），没有名称类时取 ACTIVITY_PRECONDITION_UNMET 并带
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
    chosen = rank(name, context.scenario.supplies,
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
    """@brief M2 与 M3：所写方式（「购买」跳过）与此刻能服务；恰好一条受理，多于一条按 prefer 选定。

    :param pools: （M1，M0）：来源范围内按名称匹配到的供给，与全部按名称匹配到的供给（M3 为空时的同名候选）。
    """
    kept, matches = pools
    if ask.way != SYSTEM_PURCHASE_WAY:
        supported = [supply for supply in kept if _MODES[ask.way] in supply.fulfillment_modes]
        if not supported:
            _LOGGER.error("Named supply does not support the chosen way")
            raise _unsupported(context, ask, kept)
        kept = supported
    way, served = offers(context, kept, ask.way, ask.destination_id)
    if not served:
        _LOGGER.error("Named supply cannot be served here and now")
        raise _unserved(context, ask, kept, matches)
    chosen = choose(served, ask.prefer)
    return {**_binding(context, chosen.supply, way), "channel_id": scope.channel_id,
            "purchase_note": _note(context, ask, chosen, len(served))}


def _named(name: str, supplies, naming) -> list:
    """@brief 名称按统一名称解析的逐级归一比对（第一级有匹配就停）后匹配到的供给；naming 取一件供给的比对名称。"""
    names = [naming(supply) for supply in supplies]
    match = resolve_name(name, names)
    return [supply for supply, value in zip(supplies, names) if value in match.matches]


def _store_name(context: LayerContext, supply) -> str:
    """@brief 供给所在门店（地点）的显示名。"""
    return _place(context, supply.location_id)


def _place(context: LayerContext, location_id: str) -> str:
    """@brief 地点身份的显示名。"""
    return next(item.name for item in context.scenario.locations if item.location_id == location_id)


def _product_name(context: LayerContext, supply) -> str:
    """@brief 供给商品的目录名称。"""
    return next(item.name for item in context.scenario.products if item.product_id == supply.product_id)


def _way_for(supply, way: str) -> str:
    """@brief 候选请求的方式：写的是「购买」或该供给支持所写方式时照用，否则取它支持的第一种方式。"""
    if way == SYSTEM_PURCHASE_WAY or _MODES.get(way) in supply.fulfillment_modes:
        return way
    return next((PURCHASE_WAYS[mode] for mode in PURCHASE_WAYS if mode in supply.fulfillment_modes), way)


def _spec(context: LayerContext, supply, way: str) -> dict:
    """@brief 以完整标签写成的候选（from 为 null：标签已写明门店）。"""
    return {"target": supply_label(context, supply), "way": way, "from": None}


def _near_specs(context: LayerContext, ask: PurchaseAsk) -> tuple[dict, ...]:
    """@brief SUPPLY_NOT_FOUND 的候选：至多 3 个近似标签，方式按该供给支持的取。"""
    supplies = {supply_label(context, supply): supply for supply in context.scenario.supplies}
    fulfillment = _MODES.get(ask.way)
    return tuple(_spec(context, supplies[label], _way_for(supplies[label], ask.way))
                 for label in near_labels(context, ask.written, fulfillment, ask.destination_id))


def _source_not_found(context: LayerContext, ask: PurchaseAsk, matches: Sequence) -> PreconditionError:
    """@brief SOURCE_NOT_FOUND：from 不是门店名也不是渠道称呼；候选为最相近的门店名与 App 称呼（范围内此刻能服务的在前），
    写成同一目标与方式、换了 from 的请求。"""
    def feasible(stores: frozenset[str]) -> bool:
        """@brief 这个来源范围里此刻有没有能服务的同名供给。"""
        return any(supply.location_id in stores and serves(context, supply, ask.way, ask.destination_id)
                   for supply in matches)

    specs = tuple({"target": ask.written, "way": ask.way, "from": name}
                  for name in near_sources(context, ask.source, feasible))
    return _refusal("SOURCE_NOT_FOUND", _Item("from", ask.source, specs),
                    "The source is not a known store or channel name.")


def _mismatch(context: LayerContext, ask: PurchaseAsk, supply, scope: SourceScope) -> PreconditionError:
    """@brief SUPPLY_SOURCE_MISMATCH：完整标签写明的门店不在 from 的范围内（另一家门店，或不含该门店的渠道）；候选就是
    这个标签（from 为 null）。原因接在所写的 from 之后（合并时写成「「from」：原因」）。"""
    store = _store_name(context, supply)
    reason = (f"The target store {store} is not a member of the requested channel." if scope.channel_id is not None
              else f"The target names {store}, but from names a different place.")
    return _refusal("SUPPLY_SOURCE_MISMATCH",
                    _Item("from", ask.source, (_spec(context, supply, _way_for(supply, ask.way)),)), reason)


def _not_at_store(context: LayerContext, ask: PurchaseAsk, matches: Sequence, scope: SourceScope
                  ) -> PreconditionError:
    """@brief SUPPLY_NOT_AT_STORE：from 的范围里没有这件商品；那家店完全没有供给时原因写「这里没有可买的商品」；候选为出售
    它的门店的标签（此刻能服务的在前）。原因接在所写的 from 之后。"""
    stocked = any(scope.contains(supply.location_id) for supply in context.scenario.supplies)
    if not stocked:
        reason = "The source has no purchasable supplies."
    else:
        reason = (f"No store in this channel sells {ask.written}." if scope.channel_id
                  else f"This store does not sell {ask.written}.")
    chosen = rank(ask.written, matches, lambda supply: (supply_label(context, supply), _product_name(context, supply)),
                  lambda supply: serves(context, supply, ask.way, ask.destination_id))
    specs = tuple(_spec(context, supply, _way_for(supply, ask.way)) for supply in chosen)
    return _refusal("SUPPLY_NOT_AT_STORE", _Item("from", ask.source, specs), reason)


def _unsupported(context: LayerContext, ask: PurchaseAsk, kept: Sequence) -> PreconditionError:
    """@brief FULFILLMENT_NOT_SUPPORTED：范围内同名的供给都不支持所写方式；原因写它们支持的方式（如「番茄只能远程下单」，
    接在所写的方式之后），候选为同一标签换成它支持的方式。"""
    modes = {mode for supply in kept for mode in supply.fulfillment_modes}
    reason = (f"The only supported way for {ask.written} is {PURCHASE_WAYS[next(iter(modes))]}." if len(modes) == 1
              else default_reason("FULFILLMENT_NOT_SUPPORTED"))
    specs = tuple(_spec(context, supply, _way_for(supply, ask.way)) for supply in kept if supply.fulfillment_modes)
    return _refusal("FULFILLMENT_NOT_SUPPORTED", _Item("way", ask.way, specs), reason)


def _unserved(context: LayerContext, ask: PurchaseAsk, kept: Sequence, matches: Sequence) -> PreconditionError:
    """@brief M3 为空：ACTIVITY_PRECONDITION_UNMET，说明要去哪里或哪家送不到哪里；候选为此刻能服务的同名供给（到店的在前，
    其次预计送达早的）。

    到店，以及送给自己的「购买」而范围内有能到店买的出售门店时，说要去哪里（_unreached，下一步只指向能到店买的门店：只能
    远程下单的门店去了也买不到）；其余——远程下单、范围内都只能远程下单的「购买」、送给别人的「购买」（东西只有收货人也在
    店里时才能在店里交给他，下一步同远程下单送给别人：先送到自己这里再当面给）——说哪家送不到哪里（_undelivered）。"""
    pickable = [supply for supply in kept if "pickup" in supply.fulfillment_modes]
    if ask.way == PICKUP_WAY or (ask.way == SYSTEM_PURCHASE_WAY and ask.recipient_id is None and pickable):
        reason, step, unmet = _unreached(context, ask, kept, pickable)
    else:
        deliverable = [supply for supply in kept if "delivery" in supply.fulfillment_modes]
        reason, step, unmet = _undelivered(context, ask, deliverable)
    return _refusal(_UNMET, _Item("target", ask.written, _served_specs(context, ask, matches)), reason,
                    step, unmet)


def _unreached(context: LayerContext, ask: PurchaseAsk, kept: Sequence,
               pickable: Sequence) -> tuple[str, str, tuple[str, ...]]:
    """@brief 人不在能到店买它的出售门店里时的（原因，下一步，未满足条件）：下一步去从当前地点出发路线最短的那家能到店买的
    门店（wayfinding.go_step：此地直达「去<门店>」，经别处「可经<第一站>前往<门店>」，到不了「这里去不了那家店」）；到店写
    要人在哪家店，「购买」写「不在出售它的门店里，也送不到<收货地>」并另记送不到的原因类别。

    :param pickable: 范围内支持到店的同名供给（到店时即 kept）。
    """
    places = {item.location_id: item.name for item in context.scenario.locations}
    origin = context.state.agents[context.agent_id].location_id
    nearest = nearest_store(context, (supply.location_id for supply in pickable))
    step = go_step(context.scenario.routes, places, origin, nearest, UNREACHABLE_STORE_STEP)
    if ask.way == SYSTEM_PURCHASE_WAY:
        gap = _delivery_gap(context, ask, kept)
        return (f"The caller is not at a selling store and delivery cannot reach {ask.receiver}.",
                step, ("required_location_id", gap))
    several = len({supply.location_id for supply in pickable}) > 1
    reason = (f"Pickup requires the caller at a selling store; the nearest is {places[nearest]}." if several
              else f"Pickup requires the caller at {places[nearest]}.")
    return reason, step, ("required_location_id",)


def _undelivered(context: LayerContext, ask: PurchaseAsk, deliverable: Sequence) -> tuple[str, str, tuple[str, ...]]:
    """@brief 远程下单送不到收货地时的（原因，下一步，未满足条件）：只有一家出售门店时与非指名的远程下单同一说法
    （_undeliverable）；几家都送不到时原因写「出售它的门店都送不到<收货地>」，下一步送给自己时只指向路线最短的那家能送达、且
    此地可达的地点，送给别人时先送到自己这里再当面给。送给别人的「购买」范围内的供给都只能到店（收货人又不在店里）时，与到店
    买给不在店里的人同一说法（order_routing.undeliverable：「暂时送不到<名>那里」）。

    :param deliverable: 范围内支持配送的同名供给；为空只会是送给别人的「购买」。
    """
    if not deliverable:
        error = undeliverable(context.state, context.scenario, context.agent_id, ask.recipient_id)
        return error.public_reason, error.suggested["next_step"], tuple(error.unmet)
    stores = {supply.location_id for supply in deliverable}
    if len(stores) == 1:
        return _undeliverable(context, ask, deliverable[0])
    nearest = nearest_store(context, stores)
    return (f"No selling store can deliver to {ask.receiver}.", _delivery_next_step(context, ask, deliverable, nearest),
            (_delivery_gap(context, ask, deliverable),))


def _undeliverable(context: LayerContext, ask: PurchaseAsk, supply) -> tuple[str, str, tuple[str, ...]]:
    """@brief 只有一家出售门店、远程下单却送不到收货地时的（原因，下一步，未满足条件），与非指名的远程下单同一说法
    （docs/dev/SPEC-dayrun-fidelity.md 5.3）：送给自己时为「这里不在这家店的配送范围内」（delivery_destination）或「这家店
    到这里没有能走的配送路线」（delivery_route），下一步只指向那家店能送达、且从此地可达的地点；送给别人时把解析出的供给
    交给 order_routing.undeliverable，「这里」换成收货人那里，下一步先送到自己这里再当面给。"""
    if ask.recipient_id is not None:
        error = undeliverable(context.state, context.scenario, context.agent_id, ask.recipient_id, supply)
        return error.public_reason, error.suggested["next_step"], tuple(error.unmet)
    gap = _delivery_gap(context, ask, (supply,))
    reason = DELIVERY_ROUTE_REASON if gap == "delivery_route" else DELIVERY_DESTINATION_REASON
    return reason, _delivery_next_step(context, ask, (supply,), supply.location_id), (gap,)


def _delivery_gap(context: LayerContext, ask: PurchaseAsk, kept: Sequence) -> str:
    """@brief 送不到的原因类别：收货地在某条供给的配送范围内而配送路线走不通时为 delivery_route，否则为
    delivery_destination（docs/dev/SPEC-dayrun-fidelity.md 5.3 的两个未满足条件）。"""
    destination = ask.destination_id
    covered = [supply for supply in kept if destination is not None and destination in supply.delivery_destination_ids]
    if any(delivery_route_code(context.scenario.routes, supply.location_id, destination) is not None
           for supply in covered):
        return "delivery_route"
    return "delivery_destination"


def _delivery_next_step(context: LayerContext, ask: PurchaseAsk, kept: Sequence, nearest: str) -> str:
    """@brief 远程下单送不到时的下一步：送给自己时只指向那家店能送达、且从此地可达的地点（wayfinding.delivery_step）；送给
    别人时先送到自己这里再当面给。"""
    if ask.recipient_id is not None:
        return "Deliver to the caller first, then give the item in person."
    supply = next(item for item in kept if item.location_id == nearest)
    places = {item.location_id: item.name for item in context.scenario.locations}
    here = context.state.agents[context.agent_id].location_id
    return delivery_step(context.scenario.routes, places, here, nearest, supply.delivery_destination_ids)


def _served_specs(context: LayerContext, ask: PurchaseAsk, matches: Sequence) -> tuple[dict, ...]:
    """@brief 此刻能服务的同名供给写成的候选（不论 from 与所写方式）：人在店里、且东西在店里交得到收货人手上的写到店
    （purchase_offers.handed_over，交不到的到店候选照填也会被结算拒绝），其余能送到收货地的写远程下单；按最快排序，至多
    3 个。"""
    pickups = handed_over(offers(context, matches, PICKUP_WAY, ask.destination_id)[1], ask.destination_id)
    taken = {offer.supply.supply_id for offer in pickups}
    deliveries = [offer for offer in offers(context, matches, DELIVERY_WAY, ask.destination_id)[1]
                  if offer.supply.supply_id not in taken]
    ordered = sorted(pickups, key=lambda offer: order_key(offer, "最快拿到")) + sorted(
        deliveries, key=lambda offer: order_key(offer, "最快拿到"))
    return tuple(_spec(context, offer.supply, offer.way) for offer in ordered[:MAX_NEAR])


def _note(context: LayerContext, ask: PurchaseAsk, chosen: Offer, count: int) -> str | None:
    """@brief 受理说明（details.notes）：方式为「购买」时写定下的方式，多于一处可服务时写按偏好选了哪家（完整标签），并附
    预计送达（远程下单）与到手价（按最便宜选时）；两者都没有时为 None。"""
    label = supply_label(context, chosen.supply)
    clauses = [f"「购买」定为{chosen.way}"] if ask.way == SYSTEM_PURCHASE_WAY else []
    if count > 1:
        clauses.append(f"有 {count} 处都能{chosen.way}，按{ask.prefer}选了{label}")
    elif clauses:
        clauses[-1] += f"：{label}"
    if not clauses:
        return None
    details = []
    if chosen.expected_at is not None:
        details.append(f"预计 {clock_phrase(chosen.expected_at, context.state.current_time, context.scenario.timezone)}"
                       " 送到")
    if chosen.price is not None and ask.prefer == "到手价最低":
        details.append(f"到手价 {money_phrase(chosen.price)}")
    return f"「{ask.written}」{'，'.join(clauses)}" + (f"（{'，'.join(details)}）" if details else "")


def _binding(context: LayerContext, supply, way: str) -> dict:
    """@brief 选定的供给的绑定行：与观察中的供给绑定同形（报价取当前世界报价，方式为定下的到店或远程下单），另带 named
    标记。"""
    agent = context.state.agents[context.agent_id]
    row = supply_row(DiscoveryContext(context.state, context.scenario, context.semantics, agent), supply)
    action = binding_action(way, "purchase", fulfillment=_MODES[way])
    return {"object": supply_label(context, supply), "kind": "supply", "object_id": supply.supply_id,
            "actions": [action], "named": True, "selected_action": action,
            "quote": {"price_version": row["price_version"], "price_fen": row["price_fen"],
                      "purchase_price_fen": row["purchase_price_fen"]}}


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
    from worldmock.life.tool_layer import Candidate, refusal_for

    supplies = [supply for supply in context.scenario.supplies if supply_label(context, supply) == spec["target"]]
    note = None
    if len(supplies) == 1 and spec["way"] != SYSTEM_PURCHASE_WAY:
        binding = {**_binding(context, supplies[0], spec["way"]),
                   **{key: row[key] for key in ("recipient_id", "to_self", "to") if key in row}}
        refusal = refusal_for(context, Candidate("start_activity", binding, binding["selected_action"]))
        note = refusal.reason if refusal else None
    return activity_candidate(spec["target"], spec["way"], (spec["from"], row["to"]), note)
