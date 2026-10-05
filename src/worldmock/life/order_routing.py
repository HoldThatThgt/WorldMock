"""@brief 取得的收货人与拆单（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「来源与去向」）。

订单只有一个收货人，付款人仍是执行人物。同一请求中方式相同的各项按收货地拆成几笔整单（每笔一次付款、一次配送），
整单的去重键为（供给，收货人）。收货地点不作参数：自己为当前地点，同世界人物为其所在地点，联系人为其收货地点。
到店都要本人在店里；送给别人时，到店要收货人也在店里，远程下单要能送到收货人那里，否则以 ACTIVITY_PRECONDITION_UNMET
拒绝（undeliverable）：远程下单按原因分「收货地点不在配送范围」（delivery_destination）与「配送路线缺失」（delivery_route）
两句（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-浏览结果与提示看可达性 ），收货人此刻没有收货地点或到店时不在店里为「暂时送不到<名>那里」；送给自己时
沿用 FULFILLMENT_NOT_SUPPORTED。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.life.admission import present_location
from worldmock.life.basket import delivery_expected_at
from worldmock.life.guidance import guided
from worldmock.life.map_models import SupplyConfig
from worldmock.life.models import ActionResult, PurchaseItem, PurchaseOrderRequest, WorldState
from worldmock.life.recipients import agent_identity, person_name, recipient_location
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.semantics import ScenarioSemantics
from worldmock.life.wayfinding import delivery_route_code

_LOGGER = logging.getLogger(__name__)
#: 远程下单送给别人却送不到时的两种原因（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-浏览结果与提示看可达性 ）：与本人送达地的
#: guidance.DELIVERY_DESTINATION_REASON、DELIVERY_ROUTE_REASON 同一说法，「这里」换成收货人那里。
_RECIPIENT_DESTINATION_REASON = "The receiving location of {name} is outside this store's delivery area."
_RECIPIENT_ROUTE_REASON = "No reachable delivery route connects this store to the receiving location of {name}."


def order_requests(state: WorldState, semantics: ScenarioSemantics, supplies: Mapping[str, SupplyConfig],
                   agent_id: int, bindings: list[dict]) -> list[PurchaseOrderRequest]:
    """@brief 各取得项（已写上去向的绑定行）按收货地拆成的整单请求，保持请求中的先后。

    :raises BusinessRuleError: FULFILLMENT_NOT_SUPPORTED、SUPPLY_QUOTE_UNKNOWN，或送不到收货人那里的
        ACTIVITY_PRECONDITION_UNMET。
    """
    modes = {item["selected_action"].get("fulfillment") for item in bindings}
    if len(modes) != 1 or modes.pop() not in {"pickup", "delivery"}:
        _LOGGER.error("Purchase fulfillment selection is invalid")
        raise BusinessRuleError("FULFILLMENT_NOT_SUPPORTED")
    fulfillment = bindings[0]["selected_action"]["fulfillment"]
    destinations = [_destination(state, semantics, agent_id, (item, supplies[item["object_id"]]), fulfillment)
                    for item in bindings]
    groups: dict[str, list[PurchaseItem]] = {}
    for binding, destination in zip(bindings, destinations):
        groups.setdefault(destination, []).append(_item(binding))
    return [PurchaseOrderRequest(items=tuple(items), fulfillment=fulfillment, destination_id=destination)
            for destination, items in groups.items()]


def merged_result(results: list[ActionResult], answering: str | None) -> ActionResult:
    """@brief 几笔整单的结果合成一个：订单、库存变化与品类命中依次相接，货款与资金变动相加（只有一笔时原样保留）；
    关联消息写 answering。"""
    data = dict(results[0].data)
    if len(results) > 1:
        changes: dict[str, int] = {}
        for result in results:
            for account, amount in result.data["resource_changes"].items():
                changes[account] = changes.get(account, 0) + amount
        data.update({"orders": [order for result in results for order in result.data["orders"]],
                     "total_price_fen": sum(result.data["total_price_fen"] for result in results),
                     "inventory_changes": [row for result in results for row in result.data["inventory_changes"]],
                     "category_matches": [row for result in results for row in result.data["category_matches"]],
                     "resource_changes": changes})
    if answering is not None:
        data["answering"] = answering
    return results[0].model_copy(update={"data": data})


def _destination(state: WorldState, semantics: ScenarioSemantics, agent_id: int, pair: tuple[dict, SupplyConfig],
                 fulfillment: str) -> str:
    """@brief 一项的收货地：本人到店要在店里；收货人是自己时取当前地点，是别人时取其收货地点并核对送得到。"""
    binding, supply = pair
    agent = state.agents[agent_id]
    valid = (supply.location_id == present_location(state, agent_id) and "pickup" in supply.fulfillment_modes
             if fulfillment == "pickup" else "delivery" in supply.fulfillment_modes)
    recipient = binding["recipient_id"]
    if recipient == agent_identity(agent_id):
        valid = valid and (fulfillment == "pickup" or agent.location_id in supply.delivery_destination_ids)
    if not valid:
        _LOGGER.error("Selected supplies cannot use the chosen fulfillment")
        raise BusinessRuleError("FULFILLMENT_NOT_SUPPORTED")
    if recipient == agent_identity(agent_id):
        return agent.location_id
    place = recipient_location(state, semantics.scenario, agent_id, recipient)
    reachable = place == supply.location_id if fulfillment == "pickup" else (
        place is not None and delivery_expected_at(semantics, supply, place, state.current_time) is not None)
    if not reachable:
        _LOGGER.error("Purchase recipient cannot receive goods from the selected supply")
        raise undeliverable(state, semantics.scenario, agent_id, recipient,
                            supply if fulfillment == "delivery" else None)
    return place


def undeliverable(state: WorldState, scenario: RuntimeScenario, agent_id: int, recipient_id: str,
                  supply: SupplyConfig | None = None) -> PreconditionError:
    """@brief 送不到别的收货人那里：ACTIVITY_PRECONDITION_UNMET，下一步先送到自己这里再当面给。

    给出远程下单的供给、收货人此刻有收货地点时按原因分两句（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-浏览结果与提示看可达性 ，与本人送达地的
    admission._delivery_refusal 同一顺序）：收货地点不在该供给的可配送目的地为 delivery_destination「<名>那里不在这家店的
    配送范围内」；在配送范围内而门店到那里的配送路线缺失（没有、不可达或未知，wayfinding.delivery_route_code）为
    delivery_route「这家店到<名>那里没有能走的配送路线」。其余情形（收货人此刻没有收货地点：联系人没有收货地点、同世界
    人物在途中；到店时收货人不在店里，不给供给）为 delivery_destination「暂时送不到<名>那里」。

    :param supply: 远程下单所选的供给；None 时不按配送原因细分。
    """
    name = person_name(state, scenario, agent_id, recipient_id)
    place = recipient_location(state, scenario, agent_id, recipient_id) if supply is not None else None
    if place is not None and place not in supply.delivery_destination_ids:
        unmet, reason = "delivery_destination", _RECIPIENT_DESTINATION_REASON.format(name=name)
    elif place is not None and delivery_route_code(scenario.routes, supply.location_id, place) is not None:
        unmet, reason = "delivery_route", _RECIPIENT_ROUTE_REASON.format(name=name)
    else:
        unmet, reason = "delivery_destination", f"{name} currently has no eligible receiving location."
    return guided("ACTIVITY_PRECONDITION_UNMET", reason,
                  "Receive the item at your current location, then give it in person.", (unmet,))


def _item(binding: dict) -> PurchaseItem:
    """@brief 一项取得的整单条目：冻结报价版本、单件、收货人与所回应的消息。"""
    quote = binding.get("quote")
    if not isinstance(quote, dict) or not quote.get("price_version"):
        _LOGGER.error("Frozen supply quote is unavailable")
        raise BusinessRuleError("SUPPLY_QUOTE_UNKNOWN")
    return PurchaseItem(supply_id=binding["object_id"], quantity=1, price_version=quote["price_version"],
                        recipient_id=binding["recipient_id"], request_message_id=binding.get("request_message_id"))
