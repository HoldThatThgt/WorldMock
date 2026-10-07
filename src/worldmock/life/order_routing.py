"""@brief 购买按实际店铺、收货地、购物应用与支付应用拆成整单。"""

from __future__ import annotations

from worldmock.errors import IntegrityError, PreconditionError
from worldmock.life.guidance import guided
from worldmock.life.basket import plan_recipient_delivery
from worldmock.life.models import ActionResult, PurchaseItem, PurchaseOrderRequest, WorldState
from worldmock.life.recipients import agent_identity, recipient_location, undeliverable
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.semantics import ScenarioSemantics


def order_requests(state: WorldState, semantics: ScenarioSemantics,
                   agent_id: int, bindings: list[dict]) -> list[PurchaseOrderRequest]:
    """@brief 每行独立选择的履约店铺与两个应用组成整单，不混合不同付款选择。"""
    groups: dict[tuple, list[PurchaseItem]] = {}
    for binding in bindings:
        fulfillment = binding["selected_action"]["fulfillment"]
        destination = _destination(state, semantics, agent_id, binding, fulfillment)
        app_id = binding["app_id"]
        payment_id = binding["payment_app_id"]
        key = (binding["store_id"], destination, app_id, payment_id, fulfillment)
        groups.setdefault(key, []).append(_item(binding))
    return [PurchaseOrderRequest(items=tuple(items), fulfillment=fulfillment, destination_id=destination,
                                  app_id=app_id, payment_app_id=payment_id)
            for (_, destination, app_id, payment_id, fulfillment), items in groups.items()]


def merged_result(results: list[ActionResult], answering: str | None) -> ActionResult:
    """@brief 多笔整单只合并订单、货款与账户变化，不声称物品持有。"""
    data = dict(results[0].data)
    if len(results) > 1:
        changes: dict[str, int] = {}
        for result in results:
            for account, amount in result.data["resource_changes"].items():
                changes[account] = changes.get(account, 0) + amount
        data.update({"orders": [order for result in results for order in result.data["orders"]],
                     "total_price_fen": sum(result.data["total_price_fen"] for result in results),
                     "resource_changes": changes})
    if answering is not None:
        data["answering"] = answering
    return results[0].model_copy(update={"data": data})


def _destination(state: WorldState, semantics: ScenarioSemantics, agent_id: int, binding: dict,
                 fulfillment: str) -> str:
    """@brief 收货地来自真实收货人；途中本人和异地到店收货明确拒绝。"""
    recipient = binding["recipient_id"]
    if fulfillment != "pickup":
        plan = plan_recipient_delivery(state, semantics, agent_id, recipient, binding["store_id"])
        if binding["destination_id"] != plan.destination_id or fulfillment != plan.kind:
            raise IntegrityError("Selected order destination or fulfillment disagrees with its delivery quote")
        return plan.destination_id
    place = recipient_location(state, semantics.scenario, agent_id, recipient)
    if recipient == agent_identity(agent_id) and place is None:
        raise guided("ACTIVITY_PRECONDITION_UNMET", "Place the order before departing or after arriving.",
                      "Wait until you arrive, then place the order.", ("delivery_destination",))
    if place is None or fulfillment == "pickup" and place != binding["store_id"]:
        raise undeliverable(state, semantics.scenario, agent_id, recipient)
    return place



def _item(binding: dict) -> PurchaseItem:
    """@brief 实际店铺、商品和收货人形成恒为一件的订单行。"""
    return PurchaseItem(store_id=binding["store_id"], goods_id=binding["goods_id"], quantity=1,
                        recipient_id=binding["recipient_id"], request_message_id=binding.get("request_message_id"))
