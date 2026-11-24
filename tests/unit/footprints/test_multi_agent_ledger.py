"""多人物账户与外部对手方守恒账本校验测试。"""

import pytest

from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.recording_engine.validation.ledger import validate_account_history, validate_account_transition


@pytest.fixture
def multi_agent_store(tmp_path):
    """创建含两名人物与外部场景账户的初始存储。"""
    accounts = {"agent:1": 500, "agent:2": 300, "external:scenario": -800}
    store = FootprintStore.create(
        tmp_path / "run",
        {"run_id": "multi-run", "agent_ids": [1, 2]},
        {"version": 0, "current_time": 100, "accounts": accounts},
    )
    injection = EventDraft(
        event_type="environment_event",
        occurred_at=100,
        source_kind="scenario",
        data={"kind": "initial_funds_injected"},
        resource_changes={"account:agent:1": 500, "account:agent:2": 300, "account:external:scenario": -800},
    )
    store.commit(store.state, {**store.state, "version": 1}, [injection])
    yield store
    store.close()


def _transfer(changes, at=101):
    """构造带资金变动的行为结果草稿。"""
    return EventDraft(event_type="action_result", occurred_at=at, resource_changes=changes)


def test_multi_agent_independent_and_peer_transfers_reconstruct_history(multi_agent_store):
    """多人物各自对外部场景或互相转账后，完整事件历史能精确重建期末余额。"""
    s1 = {"version": 2, "current_time": 101,
          "accounts": {"agent:1": 350, "agent:2": 450, "external:scenario": -800}}
    multi_agent_store.commit(
        multi_agent_store.state, s1,
        [_transfer({"account:agent:1": -150, "account:agent:2": 150})],
    )
    s2 = {"version": 3, "current_time": 102,
          "accounts": {"agent:1": 300, "agent:2": 650, "external:scenario": -950}}
    multi_agent_store.commit(
        multi_agent_store.state, s2,
        [_transfer({"account:agent:1": -50, "account:external:scenario": 50}, at=102),
         _transfer({"account:agent:2": 200, "account:external:scenario": -200}, at=102)],
    )
    events = list(multi_agent_store.iter_events(multi_agent_store.last_event_seq))
    validate_account_history(events, multi_agent_store.state)
    assert multi_agent_store.state["accounts"] == s2["accounts"]


@pytest.mark.parametrize("bad_accounts", [
    {"external:scenario": 0},
    {"agent:1": 100},
    {"agent:0": 100, "external:scenario": -100},
    {"agent:01": 100, "external:scenario": -100},
    {"merchant:1": 100, "external:scenario": -100},
    {"agent:1": -1, "external:scenario": 1},
    {"agent:1": True, "external:scenario": -1},
])
def test_invalid_multi_agent_account_table_is_rejected(bad_accounts):
    """账户表缺少 external:scenario、无正整数 agent:<id>、带非规范键、负余额或布尔金额时一律拒绝。"""
    with pytest.raises(IntegrityError):
        validate_account_transition({"accounts": bad_accounts}, {"accounts": bad_accounts}, [], initial=False)


def test_undeclared_or_unbalanced_peer_transfer_is_rejected(multi_agent_store):
    """未声明的第三方账户与不守恒的同伴转账都会被拒绝且不推进状态。"""
    target = {"version": 2, "current_time": 101,
              "accounts": {"agent:1": 400, "agent:2": 350, "external:scenario": -750}}
    with pytest.raises(IntegrityError):
        multi_agent_store.commit(
            multi_agent_store.state, target,
            [_transfer({"account:agent:1": -100, "account:agent:2": 50})],
        )
    with pytest.raises(IntegrityError):
        multi_agent_store.commit(
            multi_agent_store.state, target,
            [_transfer({"account:agent:1": -100, "account:agent:3": 100})],
        )
    assert multi_agent_store.last_event_seq == 1


def test_multi_agent_overdraft_is_rejected(multi_agent_store):
    """次人物余额不允许透支为负数。"""
    bad = {"version": 2, "current_time": 101,
           "accounts": {"agent:1": 900, "agent:2": -100, "external:scenario": -800}}
    with pytest.raises(IntegrityError):
        multi_agent_store.commit(
            multi_agent_store.state, bad,
            [_transfer({"account:agent:2": -400, "account:agent:1": 400})],
        )
    assert multi_agent_store.last_event_seq == 1
