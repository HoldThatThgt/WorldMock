"""@brief 区分生活业务拒绝与必须终止运行的故障。"""

import logging


class WorldMockError(RuntimeError):
    """@brief 记录并传播运行故障，不生成替代结果。"""

    def __init__(self, message: str):
        """@brief 使用英文错误信息记录故障。"""
        logging.error("WorldMock failure: %s", message)
        super().__init__(message)


class IntegrityError(WorldMockError):
    """@brief 数据身份、来源、哈希或状态不一致。"""


class ProtocolError(WorldMockError):
    """@brief 宿主或模型调用违反已冻结的协议。"""


class BusinessRuleError(WorldMockError):
    """@brief 可被生活环境明确登记为拒绝的业务条件。"""


class PreconditionError(BusinessRuleError):
    """@brief 前置条件未满足的业务拒绝；携带未满足项列表与可选建议动作。"""

    def __init__(self, message: str, unmet: list[str],
                 suggested: "dict | None" = None):
        """@brief 绑定未满足条件列表，建议由调用侧按需填充。

        :param message: 英文错误码字符串（与 BusinessRuleError 保持同一口径）
        :param unmet: 未满足条件名称列表（如 ["required_location_id"]）
        :param suggested: 可选的后续动作建议字典，引擎侧按需填充
        """
        super().__init__(message)
        self.unmet = unmet
        self.suggested = suggested
