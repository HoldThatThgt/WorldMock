"""@brief 取得的收货人与拆单（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「来源与去向」）。

订单只有一个收货人，付款人仍是执行人物。同一请求中方式相同的各项按收货地拆成几笔整单（每笔一次付款、一次配送），
整单的去重键为（供给，收货人）。收货地点不作参数：自己为当前地点，同世界人物为其所在地点，联系人为其收货地点。
到店都要本人在店里；送给别人时，到店要收货人也在店里，远程下单要能送到收货人那里，否则以 ACTIVITY_PRECONDITION_UNMET
拒绝（undeliverable）：收货人此刻没有收货地点或到店时不在店里为「暂时送不到<名>那里」；送给自己时
沿用 FULFILLMENT_NOT_SUPPORTED。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.life.admission import present_location
from worldmock.life.guidance import guided
from worldmock.life.models import ActionResult, PurchaseItem, PurchaseOrderRequest, WorldState
from worldmock.life.recipients import agent_identity, person_name, recipient_location
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.semantics import ScenarioSemantics

_LOGGER = logging.getLogger(__name__)


def order_requests(state: WorldState, semantics: ScenarioSemantics,
                   agent_id: int, bindings: list[dict]) -> list[PurchaseOrderRequest]:
    """@brief 将实际食品绑定按收货地点和 app 来源组成请求，每单保持原食品行。"""
    modes = {item["selected_action"].get("fulfillment") for item in bindings}
    if len(modes) != 1 or modes.pop() not in {"pickup", "delivery"}:
        _LOGGER.error("Purchase fulfillment selection is invalid")
        raise BusinessRuleError("FULFILLMENT_NOT_SUPPORTED")
    fulfillment = bindings[0]["selected_action"]["fulfillment"]
    groups: dict[tuple[str, str | None], list[PurchaseItem]] = {}
    for binding in bindings:
        destination = _destination(state, semantics, agent_id, binding, fulfillment)
        app_id = binding.get("app_id") if fulfillment == "delivery" else None
        groups.setdefault((destination, app_id), []).append(_item(binding))
    return [PurchaseOrderRequest(items=tuple(items), fulfillment=fulfillment, destination_id=destination,
                                  app_id=app_id) for (destination, app_id), items in groups.items()]


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
                     "resource_changes": changes})
    if answering is not None:
        data["answering"] = answering
    return results[0].model_copy(update={"data": data})


def _destination(state: WorldState, semantics: ScenarioSemantics, agent_id: int, binding: dict,
                 fulfillment: str) -> str:
    """@brief 取实际收货人的地点，途中自送和不同处的到店收货明确拒绝。"""
    recipient = binding["recipient_id"]
    place = recipient_location(state, semantics.scenario, agent_id, recipient)
    if recipient == agent_identity(agent_id) and place is None:
        _LOGGER.error("Self delivery has no receiving location during travel")
        raise guided("ACTIVITY_PRECONDITION_UNMET", "Place the order before departing or after arriving.",
                      "Wait until you arrive, then place the order.", ("delivery_destination",))
    if place is None or fulfillment == "pickup" and place != binding["store_id"]:
        _LOGGER.error("Purchase recipient cannot receive the selected food")
        raise undeliverable(state, semantics.scenario, agent_id, recipient)
    return place


def undeliverable(state: WorldState, scenario: RuntimeScenario, agent_id: int,
                  recipient_id: str) -> PreconditionError:
    """@brief 送不到别的收货人那里：ACTIVITY_PRECONDITION_UNMET，下一步先送到自己这里再当面给。

    收货人没有收货地点或到店时不在店里则说明当前不能收货。
    """
    name = person_name(state, scenario, agent_id, recipient_id)
    return guided("ACTIVITY_PRECONDITION_UNMET", f"{name} currently has no eligible receiving location.",
                  "Receive the item at your current location, then give it in person.")


def _item(binding: dict) -> PurchaseItem:
    """@brief 用实际门店、食品和消息关联形成单件订单行。"""
    return PurchaseItem(store_id=binding["store_id"], food_id=binding["food_id"], quantity=1,
                        recipient_id=binding["recipient_id"], request_message_id=binding.get("request_message_id"))
