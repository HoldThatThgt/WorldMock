"""@brief 按职责保存现有定义与纯计算。"""

import json
from worldmock.exchange_engine.commerce.operators import delivery_fee_fen
from worldmock.simulation_engine.commerce.purchase.purchase_offers import Offer

from worldmock.state.world.order_keys import payment_order_key


def group_key(offer: Offer) -> str:
    """@brief 配送组的键不含支付选择，使用规范 JSON 字节语义。"""
    return json.dumps([offer.store_id, offer.destination_id, offer.app_id, offer.fulfillment],
                      ensure_ascii=False, separators=(",", ":"), sort_keys=True)



def group_offers(offers: tuple[Offer, ...]) -> dict[str, tuple[Offer, ...]]:
    """@brief 组和商品次序固定，费用合并不依赖请求项顺序。"""
    grouped = {}
    for offer in sorted(offers, key=lambda row: row.goods_id):
        grouped.setdefault(group_key(offer), []).append(offer)
    return {key: tuple(grouped[key]) for key in sorted(grouped)}



def payment_orders(rows: tuple) -> dict[str, tuple]:
    """@brief 把一次购买的报价或商品履约记录按订单键归组，订单按键升序，组内保持给定次序。

    :param rows: 带 app_id 的报价或商品履约记录；同一次购买里 app_id 相同的是一张订单。
    """
    grouped = {}
    for row in rows:
        grouped.setdefault(payment_order_key(row.app_id), []).append(row)
    return {key: tuple(grouped[key]) for key in sorted(grouped)}



def group_shipping_fen(offers: tuple[Offer, ...]) -> int:
    """@brief 到店零运费；其他组只在任意商品收费时收一次固定配送费。"""
    return delivery_fee_fen() if any(row.delivery_fee_fen for row in offers) else 0
