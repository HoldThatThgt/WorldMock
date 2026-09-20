"""@brief 域级审计：closed 域守恒对账与 level 域逐主体流水对账。

资源定义见 docs/design/03-resource-engine.md「可计量资源定义」；
验收规则见 docs/design/05-recording-engine.md「资金守恒验证」。

closed 域（货币）：全部真实账户之和恒等于外部锚点注入总额，含锚点的全域总和
在全历史重放前后不变。level 域（身体）：期末余额 = 初值 + Σsource − Σdrain，
逐主体输出对账表，其中 source/drain 口径把转换分录的输出腿/输入腿一并计入。
对账失败即 fail-closed。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from worldmock.resources.ledger import Balances, ResourceLedger
from worldmock.resources.models import (
    ConvertEntry,
    FlowEntry,
    LedgerEntry,
    ResourceLedgerError,
    SOURCE_DIRECTION,
)


@dataclass(frozen=True, slots=True)
class ClosedDomainReport:
    """@brief closed 资源的守恒审计结论。"""

    resource_id: str
    anchor_owners: frozenset[str]
    initial_total: float
    final_total: float
    real_final_total: float
    injection_total: float


@dataclass(frozen=True, slots=True)
class LevelFlowRow:
    """@brief level 资源单主体的流水对账行。"""

    resource_id: str
    owner: str
    initial: float
    total_source: float
    total_drain: float
    expected_final: float
    replayed_final: float


def audit_closed_domain(ledger: ResourceLedger, resource_id: str, initial: Balances,
                        entries: Sequence[LedgerEntry],
                        expected_final: Mapping[str, float] | None = None) -> ClosedDomainReport:
    """@brief 重放 closed 资源全历史并核对守恒、注入与（可选）导出期末。"""
    final = ledger.validate_history(initial, entries)
    anchors = ledger.anchor_owners
    initial_total = _domain_total(initial, resource_id)
    final_total = _domain_total(final, resource_id)
    if not _same_number(final_total, initial_total):
        raise ResourceLedgerError(
            "CONSERVATION_VIOLATED",
            f"{resource_id} total changed from {initial_total} to {final_total} across the history")
    real_final = sum(value for (resource, owner), value in final.items()
                     if resource == resource_id and owner not in anchors)
    real_initial = sum(value for (resource, owner), value in initial.items()
                       if resource == resource_id and owner not in anchors)
    if expected_final is not None:
        _compare_expected(resource_id, final, expected_final, anchors, "CONSERVATION_VIOLATED")
    return ClosedDomainReport(resource_id, anchors, initial_total, final_total, real_final,
                              real_final - real_initial)


def audit_level_flows(ledger: ResourceLedger, resource_id: str, initial: Balances,
                      entries: Sequence[LedgerEntry],
                      expected_final: Mapping[str, float] | None = None) -> list[LevelFlowRow]:
    """@brief 以闭式公式逐主体对账 level 资源：期末 = 初值 + Σsource − Σdrain。"""
    final = ledger.validate_history(initial, entries)
    sources, drains, owners = _flow_totals(resource_id, entries, initial, final)
    rows = []
    for owner in sorted(owners):
        start = initial.get((resource_id, owner), 0)
        expected = start + sources.get(owner, 0) - drains.get(owner, 0)
        replayed = final[(resource_id, owner)]
        if not _same_number(expected, replayed):
            raise ResourceLedgerError(
                "FLOW_RECONCILIATION_MISMATCH",
                f"{resource_id}/{owner} closed-form final {expected} disagrees with replay {replayed}")
        rows.append(LevelFlowRow(resource_id, owner, start, sources.get(owner, 0),
                                 drains.get(owner, 0), expected, replayed))
    if expected_final is not None:
        _compare_expected(resource_id, final, expected_final, ledger.anchor_owners,
                          "FLOW_RECONCILIATION_MISMATCH")
    return rows


def _flow_totals(resource_id: str, entries: Sequence[LedgerEntry], initial: Balances,
                 final: Balances) -> tuple[dict[str, float], dict[str, float], set[str]]:
    """@brief 汇总各主体的 source/drain 总额（含转换分录的输出/输入腿）。"""
    sources: dict[str, float] = {}
    drains: dict[str, float] = {}
    owners = {owner for resource, owner in list(initial) + list(final) if resource == resource_id}
    for entry in entries:
        if isinstance(entry, FlowEntry) and entry.resource == resource_id:
            bucket = sources if entry.direction == SOURCE_DIRECTION else drains
            bucket[entry.owner] = bucket.get(entry.owner, 0) + entry.amount
        elif isinstance(entry, ConvertEntry):
            for leg in entry.inputs:
                if leg.resource == resource_id:
                    drains[leg.owner] = drains.get(leg.owner, 0) + leg.amount
            for leg in entry.outputs:
                if leg.resource == resource_id:
                    sources[leg.owner] = sources.get(leg.owner, 0) + leg.amount
    return sources, drains, owners


def _domain_total(balances: Balances, resource_id: str) -> float:
    """@brief 汇总某资源全部主体（含锚点）的余额总和。"""
    return sum(value for (resource, _owner), value in balances.items() if resource == resource_id)


def _compare_expected(resource_id: str, final: Balances, expected_final: Mapping[str, float],
                      anchors: frozenset[str], code: str) -> None:
    """@brief 逐主体比对重放期末与导出期末（锚点主体不在导出口径内）。"""
    replayed = {owner: value for (resource, owner), value in final.items()
                if resource == resource_id and owner not in anchors}
    if set(replayed) != set(expected_final):
        raise ResourceLedgerError(
            code,
            f"{resource_id} exported owners {sorted(expected_final)} differ from replay "
            f"{sorted(replayed)}")
    for owner, value in expected_final.items():
        if not _same_number(replayed[owner], value):
            raise ResourceLedgerError(
                code,
                f"{resource_id}/{owner} exported final {value} disagrees with replay {replayed[owner]}")


def _same_number(left: float, right: float) -> bool:
    """@brief 整数精确相等；浮点按 1e-9 绝对容差判等。"""
    if type(left) is int and type(right) is int:
        return left == right
    return abs(left - right) <= 1e-9
