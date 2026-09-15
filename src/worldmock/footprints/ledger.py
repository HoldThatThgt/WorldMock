"""@brief 核对权威资金事件与实际账户余额，避免遗漏或重复记账。"""

from __future__ import annotations

from collections import defaultdict

from worldmock.errors import IntegrityError
from worldmock.footprints.models import Event, EventDraft


def validate_account_transition(before: dict, after: dict, events: list[EventDraft], initial: bool) -> None:
    """@brief 每次事务的账户差额必须逐项等于同一事务中唯一资金事实。"""
    if "accounts" not in before and "accounts" not in after:
        return
    previous, current = before["accounts"], after["accounts"]
    _validate_accounts(previous)
    _validate_accounts(current)
    if set(previous) != set(current):
        raise IntegrityError("Account identity cannot change within a running world")
    changes = defaultdict(int)
    for event in events:
        money = _money_changes(event, set(current))
        if event.data.get("kind") == "initial_funds_injected":
            if not initial or event != events[0] or previous != current:
                raise IntegrityError("Initial funding must be the first event without changing initialized balances")
            expected = {f"account:{key}": value for key, value in current.items()}
            expected["account:external:scenario"] = -sum(current.values())
            if money != expected:
                raise IntegrityError("Initial funding does not describe the complete initialized accounts")
            continue
        for key, amount in money.items():
            changes[key.removeprefix("account:")] += amount
    if any(current[key] - previous[key] != changes[key] for key in current):
        raise IntegrityError("Account balances disagree with committed financial events")


def validate_account_history(events: list[Event], final_state: dict) -> None:
    """@brief 完整导出的全部资金事实必须从初始注资重建最终共同检查点账户。"""
    if "accounts" not in final_state:
        return
    expected = final_state["accounts"]
    _validate_accounts(expected)
    balances = {key: 0 for key in expected}
    initial = [event for event in events if event.data.get("kind") == "initial_funds_injected"]
    if len(initial) != 1 or initial[0] != events[0]:
        raise IntegrityError("Complete account history requires one initial funding event")
    for event in events:
        for key, amount in _money_changes(event, set(expected)).items():
            identity = key.removeprefix("account:")
            if identity in balances:
                balances[identity] += amount
                if balances[identity] < 0:
                    raise IntegrityError("Account history contains a negative balance")
    if balances != expected:
        raise IntegrityError("Export financial events do not reconstruct the final account balances")


def _validate_accounts(accounts: dict) -> None:
    """@brief 所有真实账户余额必须是非负的最小货币单位整数。"""
    if (not isinstance(accounts, dict) or not accounts
            or any(not isinstance(key, str) or not key for key in accounts)
            or any(type(value) is not int or value < 0 for value in accounts.values())):
        raise IntegrityError("Account balances must be nonnegative integer minor units")


def _money_changes(event: EventDraft, accounts: set[str]) -> dict[str, int]:
    """@brief 唯一账本字段位于事件 data 顶层，外部注资对手方也必须显式守恒。"""
    values = event.data.get("resource_changes", {})
    if not isinstance(values, dict):
        raise IntegrityError("Event resource changes must be an object")
    money = {key: value for key, value in values.items() if key.startswith("account:")}
    if any(type(value) is not int for value in money.values()):
        raise IntegrityError("Financial event amounts must use integer minor units")
    known = accounts | {"external:scenario"}
    if any(key.removeprefix("account:") not in known for key in money):
        raise IntegrityError("Financial event references an undeclared account")
    if money and (len(money) < 2 or sum(money.values()) != 0):
        raise IntegrityError("Financial event does not balance across its counterparties")
    return money
