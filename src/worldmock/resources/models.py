"""@brief 多资源分录的入口类型与资源元信息（设计文档 §3.2/§9 的代码形态）。

分录是判别联合：转账（transfer，限 closed 账户域）、单边流水（flow，限 open level 域）、
原子转换（convert，跨资源组合）。每条分录必须携带 cause（触发它的动作/事件引用）与
occurred_at 时间戳，保证全历史可重建、可对账。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass


ACCOUNT_KIND = "account"
LEVEL_KIND = "level"
CLOSED_CONSERVATION = "closed"
OPEN_CONSERVATION = "open"
SOURCE_DIRECTION = "source"
DRAIN_DIRECTION = "drain"

_AMOUNT_TYPES = (int, float)


class ResourceLedgerError(Exception):
    """@brief 携带英文错误码的分录不变量违规（fail-closed，不生成替代结果）。"""

    def __init__(self, code: str, message: str):
        """@brief 以“错误码: 详情”合成故障信息。"""
        self.code = code
        super().__init__(f"{code}: {message}")


def _require_amount(code: str, label: str, amount) -> None:
    """@brief 金额必须是有限正数（拒绝布尔、NaN、Inf 与零/负值）。"""
    if type(amount) not in _AMOUNT_TYPES:
        raise ResourceLedgerError(code, f"{label} must be an int or float, got {type(amount).__name__}")
    if isinstance(amount, float) and not math.isfinite(amount):
        raise ResourceLedgerError(code, f"{label} must be finite, got {amount}")
    if amount <= 0:
        raise ResourceLedgerError(code, f"{label} must be positive, got {amount}")


def _require_cause(cause) -> None:
    """@brief 分录必须引用触发它的动作/事件（§9：原因必填）。"""
    if not isinstance(cause, str) or not cause:
        raise ResourceLedgerError("CAUSE_REQUIRED", "entry cause must be a non-empty string")


def _require_timestamp(value) -> None:
    """@brief 分录时间戳必须是整秒（与足迹事件 occurred_at 同口径）。"""
    if type(value) is not int:
        raise ResourceLedgerError("TIMESTAMP_NOT_INTEGER", "entry occurred_at must be an integer second")


@dataclass(frozen=True, slots=True)
class ResourceMeta:
    """@brief 一种资源的元信息：形态、守恒域与数值边界（由调用方轻量传入）。"""

    resource_id: str
    kind: str
    conservation: str
    bounds_min: float = 0
    bounds_max: float | None = None

    def __post_init__(self) -> None:
        """@brief 元信息自身必须取值合法（形态/守恒/边界词表与大小关系）。"""
        if not isinstance(self.resource_id, str) or not self.resource_id:
            raise ValueError("resource_id must be a non-empty string")
        if self.kind not in (ACCOUNT_KIND, LEVEL_KIND):
            raise ValueError(f"unknown resource kind: {self.kind}")
        if self.conservation not in (CLOSED_CONSERVATION, OPEN_CONSERVATION):
            raise ValueError(f"unknown conservation domain: {self.conservation}")
        if type(self.bounds_min) not in _AMOUNT_TYPES or not math.isfinite(self.bounds_min):
            raise ValueError("bounds_min must be a finite number")
        if self.bounds_min < 0:
            raise ValueError("bounds_min cannot be negative: ledger invariant NEGATIVE_BALANCE is universal")
        if self.bounds_max is not None and (
                type(self.bounds_max) not in _AMOUNT_TYPES
                or not math.isfinite(self.bounds_max)
                or self.bounds_max < self.bounds_min):
            raise ValueError("bounds_max must be a finite number not below bounds_min")


@dataclass(frozen=True, slots=True)
class ResourceLeg:
    """@brief 转换分录的一端：某主体在某资源上的数量。"""

    resource: str
    owner: str
    amount: float

    @classmethod
    def coerce(cls, leg) -> "ResourceLeg":
        """@brief 允许调用方以 (resource, owner, amount) 三元组简写构造。"""
        if isinstance(leg, cls):
            return leg
        if isinstance(leg, Iterable) and not isinstance(leg, str):
            values = tuple(leg)
            if len(values) == 3:
                return cls(values[0], values[1], values[2])
        raise ValueError(f"leg must be ResourceLeg or (resource, owner, amount), got {leg!r}")


@dataclass(frozen=True, slots=True)
class TransferEntry:
    """@brief 双边转账分录：closed 账户域内 from 向 to 移动 amount（零和）。"""

    resource: str
    from_owner: str
    to_owner: str
    amount: float
    cause: str
    occurred_at: int

    def __post_init__(self) -> None:
        """@brief 转账双方必须成对（两个不同主体）且金额为正、原因必填。"""
        if not isinstance(self.from_owner, str) or not self.from_owner:
            raise ValueError("transfer from_owner must be a non-empty string")
        if not isinstance(self.to_owner, str) or not self.to_owner:
            raise ValueError("transfer to_owner must be a non-empty string")
        if self.from_owner == self.to_owner:
            raise ResourceLedgerError(
                "TRANSFER_UNBALANCED",
                f"transfer on {self.resource} requires two distinct counterparties, "
                f"got {self.from_owner} on both sides")
        _require_amount("AMOUNT_NOT_POSITIVE", f"transfer on {self.resource}", self.amount)
        _require_cause(self.cause)
        _require_timestamp(self.occurred_at)


@dataclass(frozen=True, slots=True)
class FlowEntry:
    """@brief 单边流水分录：open level 域的 source（流入）或 drain（流出）。"""

    resource: str
    owner: str
    direction: str
    amount: float
    cause: str
    occurred_at: int

    def __post_init__(self) -> None:
        """@brief 流水方向必须取 source/drain，金额为正、原因必填。"""
        if self.direction not in (SOURCE_DIRECTION, DRAIN_DIRECTION):
            raise ValueError(f"flow direction must be source or drain, got {self.direction}")
        if not isinstance(self.owner, str) or not self.owner:
            raise ValueError("flow owner must be a non-empty string")
        _require_amount("AMOUNT_NOT_POSITIVE", f"{self.direction} on {self.resource}", self.amount)
        _require_cause(self.cause)
        _require_timestamp(self.occurred_at)


@dataclass(frozen=True, slots=True)
class ConvertEntry:
    """@brief 原子转换分录：一笔事务内同时扣输入、进输出（跨资源组合）。

    convert 自身不必整体零和（能量换货币是跨资源转换），但 closed 资源在笔内必须
    输入输出等量（不得无中生钱），level 资源各端仍受界与非负约束。
    """

    inputs: tuple[ResourceLeg, ...]
    outputs: tuple[ResourceLeg, ...]
    cause: str
    occurred_at: int

    def __post_init__(self) -> None:
        """@brief 归一化两端为 ResourceLeg 元组，并拒绝空端/同端重复/两端交叠。"""
        object.__setattr__(self, "inputs", tuple(ResourceLeg.coerce(leg) for leg in self.inputs))
        object.__setattr__(self, "outputs", tuple(ResourceLeg.coerce(leg) for leg in self.outputs))
        _require_cause(self.cause)
        _require_timestamp(self.occurred_at)
        for label, legs in (("inputs", self.inputs), ("outputs", self.outputs)):
            if not legs:
                raise ResourceLedgerError(
                    "CONVERT_IMBALANCED", f"convert {label} must not be empty; use a flow entry instead")
            seen = set()
            for leg in legs:
                _require_amount("AMOUNT_NOT_POSITIVE", f"convert {label} on {leg.resource}", leg.amount)
                if not isinstance(leg.resource, str) or not leg.resource:
                    raise ValueError(f"convert {label} resource must be a non-empty string")
                if not isinstance(leg.owner, str) or not leg.owner:
                    raise ValueError(f"convert {label} owner must be a non-empty string")
                key = (leg.resource, leg.owner)
                if key in seen:
                    raise ResourceLedgerError(
                        "CONVERT_IMBALANCED", f"convert {label} duplicate leg for {key}")
                seen.add(key)
        overlap = {(leg.resource, leg.owner) for leg in self.inputs} & {
            (leg.resource, leg.owner) for leg in self.outputs}
        if overlap:
            raise ResourceLedgerError(
                "CONVERT_IMBALANCED",
                f"convert inputs and outputs overlap on {sorted(overlap)}; split into separate entries")


#: 判别联合：账本接受的全部分录形态。
LedgerEntry = TransferEntry | FlowEntry | ConvertEntry
