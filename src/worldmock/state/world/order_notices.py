"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations



#: 三种订单通知的标题。
ORDER_PLACED = "订单已下单"

ORDER_SHIPPED = "订单已发货"

ORDER_DELIVERED = "订单已送达"

ORDER_TITLES = frozenset({ORDER_PLACED, ORDER_SHIPPED, ORDER_DELIVERED})

#: 支付通知的标题。
PAYMENT_SUCCEEDED = "支付成功"
