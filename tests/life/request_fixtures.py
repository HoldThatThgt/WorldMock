"""@brief 当前动作条目的共享请求工厂。"""

from worldmock.life.models import StartActivityRequest


def request(*items):
    """@brief 每项明确实际动作与完整对象名称。"""
    return StartActivityRequest.model_validate({"items": [
        {"action": action, "target": target} for action, target in items]})
