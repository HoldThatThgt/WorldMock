"""@brief 从完整目录与真实现场发现地点侧商品和行程。"""

from dataclasses import dataclass
from typing import Any

from worldmock.state.interaction.actions import binding_action


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    """@brief 发现直接使用唯一环境输入与世界。"""
    inputs: Any
    state: Any
    agent: Any


def product_rows(context: DiscoveryContext) -> tuple[dict, ...]:
    """@brief 地点侧商品仅保留当前停留或锚定城市在售的店铺商品，app 侧商品只在查询与按名购买时出现。"""
    catalog = context.inputs.catalog
    current_city = catalog.locations[context.agent.location_id].city_id
    local_goods = {goods_id for location_id, location in catalog.locations.items()
                   if location.city_id == current_city
                   for goods_id in catalog.goods_by_store[location_id]}
    return tuple({"goods_id": goods.goods_id, "name": goods.name, "category": goods.category,
                  "price_fen": goods.price_fen}
                 for _, goods in sorted(catalog.goods.items())
                 if goods.store_kinds and goods.goods_id in local_goods)


def product_bindings(context) -> list[dict]:
    """@brief 商品窗口与完整查询共用相同绑定。"""
    discovery = DiscoveryContext(context.inputs, context.state, context.state.agents[context.agent_id])
    return [{"object": row["name"], "kind": "supply", "object_id": row["goods_id"],
             "goods_id": row["goods_id"], "actions": [binding_action("purchase")]}
            for row in product_rows(discovery)]
