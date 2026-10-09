"""@brief 逐件履约结果按规范四字段键合单并只收一次运费。"""

import json

from worldmock.life.purchase_offers import Offer


def group_key(offer: Offer) -> str:
    """@brief 合单键不含支付选择，使用规范 JSON 字节语义。"""
    return json.dumps([offer.store_id, offer.destination_id, offer.app_id, offer.fulfillment],
                      ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def group_offers(offers: tuple[Offer, ...]) -> dict[str, tuple[Offer, ...]]:
    """@brief 组和商品次序固定，费用合并不依赖请求项顺序。"""
    grouped = {}
    for offer in sorted(offers, key=lambda row: row.goods_id):
        grouped.setdefault(group_key(offer), []).append(offer)
    return {key: tuple(grouped[key]) for key in sorted(grouped)}


def group_shipping_fen(offers: tuple[Offer, ...]) -> int:
    """@brief 到店零运费；其他组只在任意商品收费时收一次 500 分。"""
    return 500 if any(row.delivery_fee_fen for row in offers) else 0
