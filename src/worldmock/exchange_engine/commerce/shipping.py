"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations




def shipping_delay_seconds(interval_seconds: int) -> int:
    """@brief 付款到发货的秒数：付款到到达这段时间的十分之一，向上取整到秒。"""
    return -(-interval_seconds // 10)
