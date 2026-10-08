"""@brief 测试使用当前动作请求模型构造具名条目。"""

from worldmock.life.models import StartActivityRequest


def request(*items, **fields):
    """@brief 逐项明确目标、方式、应用、去向和支付应用。"""
    keys = ("target", "way", "app", "to", "pay_app")
    return StartActivityRequest.model_validate({"items": [dict(zip(keys, item, strict=True)) for item in items],
                                               "answering": None, **fields})
