"""@brief 从当前门店供给生成餐食状态句，不扩大供给呈现窗口。"""

from __future__ import annotations

from worldmock.life.discovery import DiscoveryContext, supply_row


def local_food_summary(context: DiscoveryContext) -> str:
    """@brief 描述本人所在地点的实际餐食可购状态，不授予购买凭据。"""
    products = {item.product_id: item for item in context.scenario.products}
    local = [item for item in context.scenario.supplies
             if item.location_id == context.agent.location_id
             and "eat" in products[item.product_id].use_modes]
    if not local:
        return "当前地点没有餐食供给。"
    balance = context.state.accounts[context.agent.balance_account_id]
    for supply in local:
        row = supply_row(context, supply)
        if (row["purchase_allowed"] and row["pickup_allowed"]
                and row["purchase_price_fen"] <= balance):
            return "当前地点有可购买餐食；只能购买本轮已呈现的供给。"
    return "当前地点有餐食供给，但现在没有可购买餐食。"
