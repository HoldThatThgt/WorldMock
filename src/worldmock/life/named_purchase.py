"""@brief 指名购买：obtain 的目标不在本轮列出时按名称解析门店供给（docs/dev/SPEC-persona-prompt.md 第五章「指名购买」）。

环境中存在的商品不因轮转未展示而禁止购买（裁决 #19）。解析先精确匹配标签「<门店>出售的<商品>」（目标已写明门店，source
须为 null，否则与列出的标签一样以 ACTION_REQUEST_INVALID 拒绝），再按商品名找满足所选方式的供给：到店是人物所在地点的
门店，远程下单是配送到收货地的门店；source 可限定门店。零个匹配以 SUPPLY_NOT_FOUND 拒绝，候选为至多 3 个确定性的近似
标签（词项相似度，不用向量检索与近似最近邻）；多店同名且未限定门店以 SUPPLY_NAME_AMBIGUOUS 拒绝，原因列出全部门店，候选为
各店的完整标签；恰好一个时返回该供给按当前报价的绑定行，价格、余额、营业与库存由整单结算照常检查。从不自动换成别的商品。
解析出的绑定行带 named 标记，购买时不查可见凭据（storefront_runtime）。

一个请求中的几项指名购买逐项解析（resolve_named_rows）：名称类拒绝逐项收集，合并成一次拒绝，逐项明细 items[] 的候选写成
可以照填的请求 {target, action, way, from, to}（docs/dev/SPEC-dayrun-fidelity.md 第四章「错误合同」）；其余拒绝报第一条。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.life.discovery import DiscoveryContext, supply_row
from worldmock.life.error_contract import Candidate, ErrorItem
from worldmock.life.guidance import Refusal, default_reason, items_refusal, refusal_for_code
from worldmock.life.interaction_menu import NAMED_SUPPLY_KIND, PURCHASE_WAYS, binding_action

if TYPE_CHECKING:
    from worldmock.life.tool_layer import LayerContext

_LOGGER = logging.getLogger(__name__)
#: SUPPLY_NOT_FOUND 至多列出的近似标签数，也是逐项明细中至多给出的候选数。
MAX_CANDIDATES = 3
#: 指名解析中逐项收集、合并报告的名称类拒绝码（docs/dev/SPEC-dayrun-fidelity.md 3.4 的环境一侧）。
NAMED_ITEM_CODES = frozenset({"SUPPLY_NOT_FOUND", "SUPPLY_NAME_AMBIGUOUS", "FULFILLMENT_NOT_SUPPORTED"})


def resolve_named_supply(context: LayerContext, name: str, way: str, source: str | None,
                         destination_id: str) -> dict:
    """@brief 把指名购买的名称解析为唯一门店供给的绑定行（selected_action 为所选方式的购买，quote 为当前报价）。

    :param name: 人物在 targets 中写的名称（标签「<门店>出售的<商品>」或商品名）。
    :param way: 取得方式「到店」或「远程下单」。
    :param source: 限定的门店名，没有时为 None。
    :param destination_id: 收货地（远程下单按它筛选能配送到的门店）。
    :raises PreconditionError: SUPPLY_NOT_FOUND（带近似标签）、SUPPLY_NAME_AMBIGUOUS（原因列出全部门店，带各店标签）、
        FULFILLMENT_NOT_SUPPORTED（按标签找到的供给不支持所选方式，带该标签）；标签写在私有 suggested.candidates。
    :raises BusinessRuleError: ACTION_REQUEST_INVALID（名称是完整标签却又写了 source）。
    """
    fulfillment = next(mode for mode, label in PURCHASE_WAYS.items() if label == way)
    supplies = context.scenario.supplies
    matches = [supply for supply in supplies if supply_label(context, supply) == name]
    if matches and source is not None:
        _LOGGER.error("Named purchase gives a store for a label that already names its store")
        raise BusinessRuleError("ACTION_REQUEST_INVALID")
    if not matches:
        matches = [supply for supply in supplies if _product_name(context, supply) == name
                   and _serves(context, supply, fulfillment, destination_id)
                   and (source is None or _store_name(context, supply) == source)]
    if not matches:
        _LOGGER.error("Named purchase matches no supply")
        raise _refusal("SUPPLY_NOT_FOUND", near_candidates(context, name))
    if len(matches) > 1:
        _LOGGER.error("Named purchase matches supplies of several stores")
        ordered = sorted(matches, key=lambda supply: (_store_name(context, supply), supply_label(context, supply)))
        stores = "、".join(dict.fromkeys(_store_name(context, supply) for supply in ordered))
        raise _refusal("SUPPLY_NAME_AMBIGUOUS", [supply_label(context, supply) for supply in ordered],
                       f"{default_reason('SUPPLY_NAME_AMBIGUOUS')}（{stores}）")
    supply = matches[0]
    if fulfillment not in supply.fulfillment_modes:
        _LOGGER.error("Named supply does not support the chosen fulfillment")
        raise _refusal("FULFILLMENT_NOT_SUPPORTED", [supply_label(context, supply)])
    return _binding(context, supply, way, fulfillment)


def resolve_named_rows(context: LayerContext, selection: list[dict], resolve: Callable[[dict], dict]) -> list[dict]:
    """@brief 逐项把指名购买的占位行解析为门店供给的绑定行（resolve 解析一项），其余行原样。

    名称类拒绝（NAMED_ITEM_CODES）逐项收集，合并成一次拒绝：码取第一项，items[] 逐项写出错项的序号（从 0 起）、原文与
    至多 3 个可以照填的候选请求；其余拒绝报第一条，名称类拒绝优先（docs/dev/SPEC-dayrun-fidelity.md 3.1 第 5 条、
    第四章）。

    :param selection: start_activity 各项解析好的绑定行（指名购买为占位行），按请求中的先后排列。
    :param resolve: 把一项占位行解析为供给绑定行的函数（按收货人的收货地点解析，见 session_actions）。
    """
    rows: list[dict] = []
    failures: list[tuple[ErrorItem, str]] = []
    other: PreconditionError | None = None
    for index, row in enumerate(selection):
        if row["kind"] != NAMED_SUPPLY_KIND:
            rows.append(row)
            continue
        try:
            rows.append(resolve(row))
        except PreconditionError as error:
            if str(error) not in NAMED_ITEM_CODES:
                other = other or error
                continue
            failures.append((_named_item(context, index, row, error),
                             error.public_reason or default_reason(str(error))))
    if failures:
        _LOGGER.error("Named purchase items failed name resolution: %s", [item.error_code for item, _ in failures])
        raise items_refusal(failures, len(selection)).error()
    if other is not None:
        raise other
    return rows


def near_candidates(context: LayerContext, name: str) -> list[str]:
    """@brief 至多 3 个与名称最相近的供给标签：按词项相似度降序、标签升序，相似度为 0 的不列。

    词项是去掉空白并统一大小写后的单字与相邻二字；相似度取名称与标签、名称与商品名两者 Dice 系数的较大者，确定性，
    不用向量检索与近似最近邻。
    """
    query = _terms(name)
    scored = []
    for supply in context.scenario.supplies:
        label = supply_label(context, supply)
        score = max(_dice(query, _terms(label)), _dice(query, _terms(_product_name(context, supply))))
        if score > 0:
            scored.append((-score, label))
    return [label for _, label in sorted(set(scored))[:MAX_CANDIDATES]]


def supply_label(context: LayerContext, supply) -> str:
    """@brief 供给的可读标签「<门店>出售的<商品>」（与工具层的商品目标名称同一写法）。"""
    return f"{_store_name(context, supply)}出售的{_product_name(context, supply)}"


def _store_name(context: LayerContext, supply) -> str:
    """@brief 供给所在门店（地点）的名称。"""
    return next(item.name for item in context.scenario.locations if item.location_id == supply.location_id)


def _product_name(context: LayerContext, supply) -> str:
    """@brief 供给商品的目录名称。"""
    return next(item.name for item in context.scenario.products if item.product_id == supply.product_id)


def _serves(context: LayerContext, supply, fulfillment: str, destination_id: str) -> bool:
    """@brief 供给是否满足所选方式：到店要门店在人物所在地点，远程下单要能配送到收货地。"""
    if fulfillment not in supply.fulfillment_modes:
        return False
    if fulfillment == "pickup":
        return supply.location_id == context.state.agents[context.agent_id].location_id
    return destination_id in supply.delivery_destination_ids


def _binding(context: LayerContext, supply, way: str, fulfillment: str) -> dict:
    """@brief 解析出的供给的绑定行：与观察中的供给绑定同形（报价取当前世界报价），另带 named 标记。"""
    agent = context.state.agents[context.agent_id]
    row = supply_row(DiscoveryContext(context.state, context.scenario, context.semantics, agent), supply)
    action = binding_action(way, "purchase", fulfillment=fulfillment)
    return {"object": supply_label(context, supply), "kind": "supply", "object_id": supply.supply_id,
            "actions": [action], "named": True, "selected_action": action,
            "quote": {"price_version": row["price_version"], "price_fen": row["price_fen"],
                      "purchase_price_fen": row["purchase_price_fen"]}}


def _refusal(code: str, labels: list[str], reason: str | None = None) -> PreconditionError:
    """@brief 带候选标签的一项业务拒绝：原因缺省取该码的默认说法，下一步与改法取默认说法；标签写在私有
    suggested.candidates，合并时换成可照填的请求（resolve_named_rows）。"""
    default = refusal_for_code(code)
    return Refusal(code, reason or default.reason, default.next_step,
                   suggested={"next_step": default.next_step, "candidates": labels}, fix=default.fix).error()


def _named_item(context: LayerContext, index: int, row: dict, error: PreconditionError) -> ErrorItem:
    """@brief 一项名称类拒绝的明细：field 为 targets，written 为人物写的名称，候选为该项拒绝带出的标签（至多 3 个），
    各写成可以照填的请求。"""
    labels = (error.suggested or {}).get("candidates", [])
    by_label = {supply_label(context, supply): supply for supply in context.scenario.supplies}
    way = row["selected_action"]["label"]
    candidates = tuple(_candidate(by_label[label], label, way, row["to"])
                       for label in labels if label in by_label)[:MAX_CANDIDATES]
    return ErrorItem(index, "targets", row["object"], str(error), candidates)


def _candidate(supply, label: str, way: str, recipient: str | None) -> Candidate:
    """@brief 可以照填的指名购买：目标为完整标签、动词 obtain、方式取所写的方式（该供给不支持时取它支持的方式）、
    from 为 null（标签已写明门店）、to 为所写的收货人。"""
    offered = [PURCHASE_WAYS[mode] for mode in PURCHASE_WAYS if mode in supply.fulfillment_modes]
    chosen = way if way in offered or not offered else offered[0]
    return Candidate(label, "obtain", chosen, None, recipient)


def _terms(text: str) -> frozenset[str]:
    """@brief 名称的词项：去掉空白、统一大小写后的单字与相邻二字。"""
    compact = "".join(text.split()).casefold()
    return frozenset(compact) | frozenset(compact[index:index + 2] for index in range(len(compact) - 1))


def _dice(left: frozenset[str], right: frozenset[str]) -> float:
    """@brief 两组词项的 Dice 系数：2|A∩B| / (|A|+|B|)，任一为空时为 0。"""
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))
