"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
from worldmock.state.world.world import WorldState




def activity_orders(state: WorldState, activities) -> list[dict]:
    """@brief 动作结果只携带这些活动实际创建的订单快照。"""
    identities = {identity for activity in activities for identity in activity.data.get("order_ids", [])}
    return [state.deliveries[identity].model_dump(mode="json") for identity in sorted(identities)]



def start_orders(before: WorldState, state: WorldState, activities) -> list[dict]:
    """@brief 开始结果携带这条动作创建的订单，以及它在驿站领取的订单在领取后的快照。

    :param before: 这条动作派发前的世界；到站待取而派发后已收货的订单就是这次领取的。
    """
    identities = {identity for activity in activities for identity in activity.data.get("order_ids", [])}
    identities |= {identity for identity, order in before.deliveries.items()
                   if order.status == "ready_for_pickup" and state.deliveries[identity].status == "delivered"}
    return [state.deliveries[identity].model_dump(mode="json") for identity in sorted(identities)]



def placed_orders(payload: dict) -> list[dict]:
    """@brief 动作结果里这条动作自己下的订单；它在驿站领取的快递只带领取后的快照，不算下单。"""
    return [order for order in payload.get("orders", [])
            if (order.get("fulfillment"), order.get("status")) != ("station_pickup", "delivered")]
