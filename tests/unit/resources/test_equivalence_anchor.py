"""@brief 行为等价锚定：现有引擎真实一天的资金事件可在新账本逐分重建（docs/design/05-recording-engine.md「资金守恒验证」行为等价性）。

数据源 fixtures/anchor-money.json：从真实运行 deepseek-multi-47931e06 只读抽取的钱事件与期末账户，
切片记录原数据库 SHA-256，只含本验收需要的字段，不需要新机器保有完整运行目录。该运行
一天的 6 笔资金事件（initial_funds_injected + 2 工资 + 3 车费）改写为
TransferEntry 序列后，重放期末六账户余额必须与库中最终 checkpoint 的实际期末
完全一致。
"""

import json
from pathlib import Path

import pytest

from worldmock.resources import (
    ResourceLedger,
    ResourceLedgerError,
    ResourceMeta,
    TransferEntry,
    audit_closed_domain,
)

FIXTURE = Path(__file__).resolve().parents[3] / 'tests/resources' / "fixtures" / "anchor-money.json"
MONEY = "money_fen"
ANCHOR_OWNER = "external:scenario"

def read_money_events() -> list[dict]:
    """@brief 读取冻结真实切片中的全部资金事件，按原事件序排列。"""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["events"]


def read_final_accounts() -> dict[str, int]:
    """@brief 读取冻结切片中原最高 state_version 检查点的期末六账户。"""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["accounts"]


def build_transfer_entries(events: list[dict]) -> list[TransferEntry]:
    """@brief 把每笔资金事件改写为双边转账分录（单一付款方、金额逐对相等）。"""
    entries: list[TransferEntry] = []
    for event in events:
        changes = {key.removeprefix("account:"): value
                   for key, value in event["data"]["resource_changes"].items()}
        payers = [owner for owner, value in changes.items() if value < 0]
        if len(payers) != 1 or sum(changes.values()) != 0:
            raise ValueError(f"event {event['event_seq']} is not a paired transfer: {changes}")
        payer = payers[0]
        cause = f"footprints:{event['event_seq']}:{event['data'].get('kind') or event['event_type']}"
        for owner, value in changes.items():
            if owner != payer:
                entries.append(TransferEntry(MONEY, payer, owner, value, cause,
                                             event["occurred_at"]))
    return entries


@pytest.fixture(scope="module")
def anchor() -> tuple[ResourceLedger, list[TransferEntry], dict[str, int]]:
    """@brief 组装（账本， 事件序列， 库中期末账户）。"""
    ledger = ResourceLedger([ResourceMeta(MONEY, "account", "closed", 0, None)],
                            anchor_owners={ANCHOR_OWNER})
    events = read_money_events()
    assert len(events) == 6, f"expected 6 money events in the anchor day, got {len(events)}"
    return ledger, build_transfer_entries(events), read_final_accounts()


def test_anchor_day_has_one_injection_two_payrolls_three_fares(anchor):
    _ledger, entries, _accounts = anchor
    causes = [entry.cause for entry in entries]
    assert sum("initial_funds_injected" in cause for cause in causes) == 6
    payrolls = [entry for entry in entries if entry.from_owner == "employer:zhangjiang"]
    fares = [entry for entry in entries if entry.to_owner == "transport:service"
             and entry.from_owner != ANCHOR_OWNER]
    assert (len(payrolls), len(fares)) == (2, 3)


def test_replayed_final_balances_match_database_checkpoint(anchor):
    ledger, entries, accounts = anchor
    replayed = ledger.validate_history({}, entries)
    actual = {(MONEY, owner): value for owner, value in accounts.items()}
    assert {key: value for key, value in replayed.items() if key in actual} == actual
    assert replayed[(MONEY, ANCHOR_OWNER)] == -sum(accounts.values())


def test_closed_domain_conserves_injection_total(anchor):
    ledger, entries, accounts = anchor
    report = audit_closed_domain(ledger, MONEY, {}, entries, expected_final=accounts)
    assert report.real_final_total == report.injection_total == 35_600_000
    assert report.final_total == report.initial_total == 0


def test_tampered_checkpoint_cannot_pass_the_anchor(anchor):
    ledger, entries, accounts = anchor
    tampered = dict(accounts)
    tampered["agent:1"] += 1
    with pytest.raises(ResourceLedgerError) as exc:
        audit_closed_domain(ledger, MONEY, {}, entries, expected_final=tampered)
    assert exc.value.code == "CONSERVATION_VIOLATED"
