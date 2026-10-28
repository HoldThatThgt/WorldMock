"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
from collections.abc import Sequence
from worldmock.exchange_engine.conditions.rejection_codes import NAME_REJECTION_CODES


#: 每人每模拟秒至多几次名称类拒绝不计同刻轮数、不施加情绪脉冲（docs/design/04-simulation-engine.md；实验假设，登记在
#: docs/design/04-simulation-engine.md「原子性与恢复」）；超出的照常计轮、照常施加脉冲。
FREE_NAME_REJECTIONS_PER_SECOND = 2



def free_rejection_flags(codes: Sequence[str | None]) -> list[bool]:
    """@brief 同一人物同一模拟秒内按结算先后的业务拒绝码逐个是否免计（docs/design/04-simulation-engine.md）：名称
    类拒绝码
    的前 FREE_NAME_REJECTIONS_PER_SECOND 次免计（不计同刻轮数、不施加情绪脉冲），此后的名称类拒绝与其余拒绝一样照常。"""
    flags, seen = [], 0
    for code in codes:
        named = code in NAME_REJECTION_CODES
        flags.append(named and seen < FREE_NAME_REJECTIONS_PER_SECOND)
        seen += named
    return flags
