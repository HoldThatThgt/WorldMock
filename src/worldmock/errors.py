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
