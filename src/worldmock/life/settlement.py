"""@brief 货币域分录结算通道：引擎全部资金变动的唯一入口（设计文档 §9）。

ResourceLedger 以 money_fen/closed 元信息与 external:scenario 锚点结算四类资金
变动（初始注资、工资支付、商品购买、出行费）：每笔变动构造 TransferEntry 经
apply 纯函数落地——closed 域双边零和、非锚点端非负。整数分余额在分录算术下
保持 int 类型，结果与旧直接赋值逐分一致。对外足迹证据（resource_changes 字段
名与格式）保持原样；本模块是引擎内部结算通道，不是对外证据格式。
"""

from __future__ import annotations

from worldmock.life.models import WorldState
from worldmock.life.semantics import EXTERNAL_ROLE_PREFIX, MONEY_RESOURCE
from worldmock.resources import ResourceLedger, ResourceMeta, TransferEntry
from worldmock.resources.models import ACCOUNT_KIND, CLOSED_CONSERVATION

#: 吸收初始注入与销毁的外部锚点主体（免除非负约束，期末为 -Σ初值）。
SCENARIO_ANCHOR_OWNER = f"{EXTERNAL_ROLE_PREFIX}scenario"

_MONEY_META = ResourceMeta(MONEY_RESOURCE, ACCOUNT_KIND, CLOSED_CONSERVATION)


def money_ledger() -> ResourceLedger:
    """@brief 注册 money_fen 元信息与场景锚点的货币域账本。"""
    return ResourceLedger([_MONEY_META], anchor_owners=(SCENARIO_ANCHOR_OWNER,))


def inject_initial_funds(accounts: dict[str, int], start_at: int) -> dict[str, int]:
    """@brief 从场景锚点向各账户注入初始资金，返回期末账户余额。

    每个初值为正的账户一笔 external:scenario → account 转账分录；从全零余额
    起算，期末与直接采用初值字典逐分一致，锚点吸收 -Σ初值。
    """
    entries = [TransferEntry(MONEY_RESOURCE, SCENARIO_ANCHOR_OWNER, owner, value,
                             "scenario:initial_funds", start_at)
               for owner, value in sorted(accounts.items()) if value > 0]
    balances = {(MONEY_RESOURCE, owner): 0 for owner in accounts}
    balances[(MONEY_RESOURCE, SCENARIO_ANCHOR_OWNER)] = 0
    settled = money_ledger().apply(entries, balances)
    return {owner: settled[(MONEY_RESOURCE, owner)] for owner in accounts}


def settle_money_transfer(state: WorldState, from_owner: str, to_owner: str,
                          amount: int, cause: str) -> WorldState:
    """@brief 经双边转账分录移动资金并返回新世界状态（零和，逐分等价旧算术）。

    金额为零时为无变动的原状态往返（如步行零车费），与旧算术的 -0/+0 一致；
    金额为正时 from_owner 扣减、to_owner 增加，余额不足由调用方业务前置校验拒绝。
    """
    if amount == 0:
        return state.model_copy(deep=True)
    entry = TransferEntry(MONEY_RESOURCE, from_owner, to_owner, amount, cause,
                          state.current_time)
    balances = {(MONEY_RESOURCE, owner): value for owner, value in state.accounts.items()}
    settled = money_ledger().apply([entry], balances)
    accounts = {owner: settled[(MONEY_RESOURCE, owner)] for owner in state.accounts}
    return state.model_copy(update={"accounts": accounts}, deep=True)
