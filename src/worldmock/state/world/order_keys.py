"""@brief 按职责保存现有定义与纯计算。"""

import json




def payment_order_key(app_id: str | None) -> str:
    """@brief 订单的键是下单的内容 app 的规范 JSON，写法与配送组的键相同；到店订单是 `[null]`，按字符串升序排在最后。"""
    return json.dumps([app_id], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
