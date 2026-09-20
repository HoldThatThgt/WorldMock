"""真实新观察合同的供给与发现词表测试夹具。"""


def supply_row(**overrides) -> dict:
    """构造具有独立商品引用、卖方、营业资格和履约范围的可见供给。"""
    return {"supply_id": "supply-visible", "product_id": "product-visible", "location_id": "home",
            "seller_account_id": "merchant:home", "price_version": "price-v1", "price_fen": 1000,
            "quote_status": "known", "current_stock": 2, "opening_status": "open",
            "access_allowed": True, "service_allowed": True, "purchase_allowed": True,
            "pickup_allowed": True, "fulfillment_modes": ["pickup", "delivery"],
            "delivery_destination_ids": ["home", "office"], **overrides}


def discovery_catalog(product_id="product-visible") -> dict:
    """构造独立于已浏览供给的公开需求和业态词表。"""
    return {"product_ids": [product_id], "business_kinds": ["convenience"]}
