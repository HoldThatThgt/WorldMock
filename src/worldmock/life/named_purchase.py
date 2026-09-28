"""@brief 指名购买：obtain 的目标不在本轮列出时按名称解析门店供给（docs/dev/SPEC-persona-prompt.md 第五章「指名购买」）。

环境中存在的商品不因轮转未展示而禁止购买（裁决 #19）。解析先精确匹配标签「<门店>出售的<商品>」，再按商品名找满足所选
方式的供给：到店是人物所在地点的门店，远程下单是配送到收货地的门店；source 可限定门店。零个匹配以 SUPPLY_NOT_FOUND
拒绝，details.candidates 给至多 3 个确定性的近似标签（词项相似度，不用向量检索与近似最近邻）；多店同名且未限定门店以
SUPPLY_NAME_AMBIGUOUS 拒绝并列出门店；恰好一个时返回该供给按当前报价的绑定行，价格、余额、营业与库存由整单结算照常
检查。从不自动换成别的商品。解析出的绑定行带 named 标记，购买时不查可见凭据（storefront_runtime）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from worldmock.errors import PreconditionError
from worldmock.life.discovery import DiscoveryContext, supply_row
from worldmock.life.guidance import Refusal, refusal_for_code
from worldmock.life.interaction_menu import PURCHASE_WAYS, binding_action

if TYPE_CHECKING:
    from worldmock.life.tool_layer import LayerContext

_LOGGER = logging.getLogger(__name__)
#: 拒绝结果里至多列出的近似标签或门店数。
MAX_CANDIDATES = 3


def resolve_named_supply(context: LayerContext, name: str, way: str, source: str | None,
                         destination_id: str) -> dict:
    """@brief 把指名购买的名称解析为唯一门店供给的绑定行（selected_action 为所选方式的购买，quote 为当前报价）。

    :param name: 人物在 targets 中写的名称（标签「<门店>出售的<商品>」或商品名）。
    :param way: 取得方式「到店」或「远程下单」。
    :param source: 限定的门店名，没有时为 None。
    :param destination_id: 收货地（远程下单按它筛选能配送到的门店）。
    :raises PreconditionError: SUPPLY_NOT_FOUND（带近似候选）、SUPPLY_NAME_AMBIGUOUS（带门店）、
        FULFILLMENT_NOT_SUPPORTED（按标签找到的供给不支持所选方式）。
    """
    fulfillment = next(mode for mode, label in PURCHASE_WAYS.items() if label == way)
    supplies = context.scenario.supplies
    matches = [supply for supply in supplies if supply_label(context, supply) == name]
    if not matches:
        matches = [supply for supply in supplies if _product_name(context, supply) == name
                   and _serves(context, supply, fulfillment, destination_id)
                   and (source is None or _store_name(context, supply) == source)]
    if not matches:
        _LOGGER.error("Named purchase matches no supply")
        raise _refusal("SUPPLY_NOT_FOUND", near_candidates(context, name))
    if len(matches) > 1:
        _LOGGER.error("Named purchase matches supplies of several stores")
        stores = sorted(dict.fromkeys(_store_name(context, supply) for supply in matches))
        raise _refusal("SUPPLY_NAME_AMBIGUOUS", stores[:MAX_CANDIDATES])
    supply = matches[0]
    if fulfillment not in supply.fulfillment_modes:
        _LOGGER.error("Named supply does not support the chosen fulfillment")
        raise refusal_for_code("FULFILLMENT_NOT_SUPPORTED").error()
    return _binding(context, supply, way, fulfillment)


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


def _refusal(code: str, candidates: list[str]) -> PreconditionError:
    """@brief 带候选的业务拒绝：原因与下一步取该码的默认说法，候选经 guidance 进入结果 details.candidates。"""
    default = refusal_for_code(code)
    return Refusal(code, default.reason, default.next_step,
                   suggested={"next_step": default.next_step, "candidates": candidates}).error()


def _terms(text: str) -> frozenset[str]:
    """@brief 名称的词项：去掉空白、统一大小写后的单字与相邻二字。"""
    compact = "".join(text.split()).casefold()
    return frozenset(compact) | frozenset(compact[index:index + 2] for index in range(len(compact) - 1))


def _dice(left: frozenset[str], right: frozenset[str]) -> float:
    """@brief 两组词项的 Dice 系数：2|A∩B| / (|A|+|B|)，任一为空时为 0。"""
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))
