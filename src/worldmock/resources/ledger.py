"""@brief 多资源分录账本：纯函数式结算与不变量守卫（docs/design/02-exchange-engine.md「校验资源分录」）。

账本以 (resource, owner) 二元组为主键。转账分录只在 closed 账户域内移动数量（零和），
流水分录只在 open level 域内有源有漏（界内），转换分录一笔事务内原子完成跨资源交换。
apply 不修改传入的 balances，返回新字典，天然支持事务回滚与测试断言。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

from worldmock.resources.models import (
    CLOSED_CONSERVATION,
    OPEN_CONSERVATION,
    ConvertEntry,
    FlowEntry,
    LedgerEntry,
    ResourceLedgerError,
    ResourceMeta,
    TransferEntry,
)

_TOLERANCE = 1e-9
_AMOUNT_TYPES = (int, float)
BalanceKey = tuple[str, str]
Balances = dict[BalanceKey, float]


class ResourceLedger:
    """@brief 按注册元信息校验并应用分录序列的资源账本。"""

    def __init__(self, metas: Iterable[ResourceMeta] | Mapping[str, ResourceMeta],
                 anchor_owners: Iterable[str] = ()):
        """@brief 注册资源元信息与外部锚点主体（锚点吸收注入/销毁，可为负）。"""
        items = metas.items() if isinstance(metas, Mapping) else metas
        self._metas: dict[str, ResourceMeta] = {}
        for meta in items:
            if not isinstance(meta, ResourceMeta):
                raise ValueError(f"meta must be a ResourceMeta, got {type(meta).__name__}")
            if meta.resource_id in self._metas:
                raise ValueError(f"duplicate resource meta for {meta.resource_id}")
            self._metas[meta.resource_id] = meta
        self._anchor_owners = frozenset(anchor_owners)

    @property
    def anchor_owners(self) -> frozenset[str]:
        """@brief 免除非负与下界约束的外部锚点主体集合。"""
        return self._anchor_owners

    @property
    def resource_ids(self) -> frozenset[str]:
        """@brief 已注册的资源标识集合。"""
        return frozenset(self._metas)

    def apply(self, entries: Sequence[LedgerEntry], balances: Balances) -> Balances:
        """@brief 纯函数式应用一批分录：校验初值，逐笔记账并守卫不变量。"""
        working = dict(balances)
        self._validate_balances(working)
        for entry in entries:
            self._apply_entry(working, entry)
        return working

    def validate_history(self, initial: Balances, entries: Sequence[LedgerEntry]) -> Balances:
        """@brief 全历史重放重建期末余额（对审计导出用）。"""
        return self.apply(entries, initial)

    def _apply_entry(self, working: Balances, entry: LedgerEntry) -> None:
        """@brief 按分录形态分派到对应的应用器。"""
        if isinstance(entry, TransferEntry):
            self._apply_transfer(working, entry)
        elif isinstance(entry, FlowEntry):
            self._apply_flow(working, entry)
        elif isinstance(entry, ConvertEntry):
            self._apply_convert(working, entry)
        else:
            raise ValueError(f"unknown ledger entry type: {type(entry).__name__}")

    def _apply_transfer(self, working: Balances, entry: TransferEntry) -> None:
        """@brief closed 域双边移动：一方扣减、一方增加，随后两端守卫。"""
        meta = self._require_meta(entry.resource)
        if meta.conservation != CLOSED_CONSERVATION:
            raise ResourceLedgerError(
                "TRANSFER_NOT_CLOSED_DOMAIN",
                f"transfer targets {entry.resource} whose conservation is {meta.conservation}")
        source = (entry.resource, entry.from_owner)
        target = (entry.resource, entry.to_owner)
        working[source] = working.get(source, 0) - entry.amount
        working[target] = working.get(target, 0) + entry.amount
        self._check_invariants(working, (source, target))

    def _apply_flow(self, working: Balances, entry: FlowEntry) -> None:
        """@brief open 域单边流水：source 流入、drain 流出，随后守卫界。"""
        meta = self._require_meta(entry.resource)
        if meta.conservation != OPEN_CONSERVATION:
            raise ResourceLedgerError(
                "FLOW_ON_CLOSED_DOMAIN",
                f"{entry.direction} targets {entry.resource} whose conservation is {meta.conservation}")
        key = (entry.resource, entry.owner)
        delta = entry.amount if entry.direction == "source" else -entry.amount
        working[key] = working.get(key, 0) + delta
        self._check_invariants(working, (key,))

    def _apply_convert(self, working: Balances, entry: ConvertEntry) -> None:
        """@brief 原子转换：closed 资源笔内等量，全部腿一次落地后统一守卫。"""
        closed_in: dict[str, float] = {}
        closed_out: dict[str, float] = {}
        for legs, totals in ((entry.inputs, closed_in), (entry.outputs, closed_out)):
            for leg in legs:
                meta = self._require_meta(leg.resource)
                if meta.conservation == CLOSED_CONSERVATION:
                    totals[leg.resource] = totals.get(leg.resource, 0) + leg.amount
        for resource in sorted(set(closed_in) | set(closed_out)):
            inbound, outbound = closed_in.get(resource, 0), closed_out.get(resource, 0)
            if not math.isclose(inbound, outbound, rel_tol=0, abs_tol=_TOLERANCE):
                raise ResourceLedgerError(
                    "CONVERT_IMBALANCED",
                    f"convert moves closed resource {resource} with inputs {inbound} vs outputs {outbound}")
        touched: set[BalanceKey] = set()
        for legs, sign in ((entry.inputs, -1), (entry.outputs, 1)):
            for leg in legs:
                key = (leg.resource, leg.owner)
                working[key] = working.get(key, 0) + sign * leg.amount
                touched.add(key)
        self._check_invariants(working, touched)

    def _validate_balances(self, balances: Balances) -> None:
        """@brief 初值自身必须取值合法（数值类型、资源已注册、界内非负）。"""
        for key, value in balances.items():
            if not isinstance(key, tuple) or len(key) != 2:
                raise ValueError(f"balance key must be (resource, owner), got {key!r}")
            self._require_meta(key[0])
            if type(value) not in _AMOUNT_TYPES or (
                    isinstance(value, float) and not math.isfinite(value)):
                raise ValueError(f"balance for {key} must be a finite int or float, got {value!r}")
        self._check_invariants(balances, list(balances))

    def _require_meta(self, resource_id: str) -> ResourceMeta:
        """@brief 取资源元信息，未注册即 fail-closed。"""
        meta = self._metas.get(resource_id)
        if meta is None:
            raise ResourceLedgerError("UNKNOWN_RESOURCE", f"resource {resource_id} is not registered")
        return meta

    def _check_invariants(self, working: Balances, keys: Iterable[BalanceKey]) -> None:
        """@brief 守卫非负与界：先查负，再查上下界；浮点边界容差内钳到界上。"""
        for key in keys:
            resource, owner = key
            meta = self._metas[resource]
            anchored = owner in self._anchor_owners
            value = working[key]
            if not anchored and value < 0:
                if math.isclose(value, 0, rel_tol=0, abs_tol=_TOLERANCE):
                    working[key] = 0
                else:
                    raise ResourceLedgerError("NEGATIVE_BALANCE", f"{key} balance {value} is negative")
                value = working[key]
            if not anchored and meta.bounds_min > 0 and value < meta.bounds_min:
                if math.isclose(value, meta.bounds_min, rel_tol=0, abs_tol=_TOLERANCE):
                    working[key] = meta.bounds_min
                else:
                    raise ResourceLedgerError(
                        "OUT_OF_BOUNDS", f"{key} balance {value} is below bounds_min {meta.bounds_min}")
            if meta.bounds_max is not None and value > meta.bounds_max:
                if math.isclose(value, meta.bounds_max, rel_tol=0, abs_tol=_TOLERANCE):
                    working[key] = meta.bounds_max
                else:
                    raise ResourceLedgerError(
                        "OUT_OF_BOUNDS", f"{key} balance {value} is above bounds_max {meta.bounds_max}")
