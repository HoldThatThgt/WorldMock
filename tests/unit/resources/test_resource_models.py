"""@brief 分录与资源元信息构造层的 fail-closed 校验测试。"""

from dataclasses import FrozenInstanceError

import pytest

from worldmock.resources import (
    ConvertEntry,
    FlowEntry,
    ResourceLedgerError,
    ResourceMeta,
    ResourceLeg,
    TransferEntry,
)


def transfer(**overrides):
    """@brief 提供一条合法转账分录的默认参数。"""
    fields = dict(resource="money_fen", from_owner="a", to_owner="b", amount=10,
                  cause="test", occurred_at=100)
    fields.update(overrides)
    return TransferEntry(**fields)


def flow(**overrides):
    """@brief 提供一条合法流水分录的默认参数。"""
    fields = dict(resource="energy_kcal", owner="a", direction="source", amount=10.5,
                  cause="test", occurred_at=100)
    fields.update(overrides)
    return FlowEntry(**fields)


def convert(**overrides):
    """@brief 提供一条合法转换分录的默认参数。"""
    fields = dict(inputs=[("energy_kcal", "a", 10.5)],
                  outputs=[("money_fen", "a", 20)], cause="test", occurred_at=100)
    fields.update(overrides)
    return ConvertEntry(**fields)


def test_resource_meta_accepts_documented_shapes():
    account = ResourceMeta("money_fen", "account", "closed", 0, None)
    level = ResourceMeta("sleep_debt_hours", "level", "open", 0)
    assert (account.bounds_max is None, level.bounds_max is None) == (True, True)


@pytest.mark.parametrize("kwargs", [
    {"resource_id": ""},
    {"resource_id": 7},
    {"kind": "commodity"},
    {"conservation": "leaky"},
    {"bounds_min": float("nan")},
    {"bounds_min": -1},
    {"bounds_min": "0"},
    {"bounds_min": 5, "bounds_max": 5 - 1},
])
def test_resource_meta_rejects_malformed_definitions(kwargs):
    fields = dict(resource_id="energy_kcal", kind="level", conservation="open")
    fields.update(kwargs)
    with pytest.raises(ValueError):
        ResourceMeta(**fields)


def test_valid_entries_construct_and_freeze():
    entries = [transfer(), flow(), convert()]
    assert all(entry.occurred_at == 100 for entry in entries)
    with pytest.raises(FrozenInstanceError):
        entries[0].amount = 20


@pytest.mark.parametrize("amount", [0, -1, float("nan"), float("inf"), True, "10", None])
def test_amount_must_be_finite_positive_number(amount):
    with pytest.raises(ResourceLedgerError) as exc:
        transfer(amount=amount)
    assert exc.value.code == "AMOUNT_NOT_POSITIVE"
    with pytest.raises(ResourceLedgerError) as flow_exc:
        flow(amount=amount)
    assert flow_exc.value.code == "AMOUNT_NOT_POSITIVE"


@pytest.mark.parametrize("amount", [0, -0.5, float("nan"), False])
def test_convert_leg_amounts_must_be_positive(amount):
    with pytest.raises(ResourceLedgerError) as exc:
        convert(inputs=[("energy_kcal", "a", amount)])
    assert exc.value.code == "AMOUNT_NOT_POSITIVE"


@pytest.mark.parametrize("cause", ["", None, 3])
def test_entry_cause_is_mandatory(cause):
    with pytest.raises(ResourceLedgerError) as exc:
        transfer(cause=cause)
    assert exc.value.code == "CAUSE_REQUIRED"


@pytest.mark.parametrize("occurred_at", [100.5, "100", None])
def test_entry_timestamp_must_be_integer_seconds(occurred_at):
    with pytest.raises(ResourceLedgerError) as exc:
        transfer(occurred_at=occurred_at)
    assert exc.value.code == "TIMESTAMP_NOT_INTEGER"


def test_transfer_requires_two_distinct_counterparties():
    with pytest.raises(ResourceLedgerError) as exc:
        transfer(from_owner="a", to_owner="a")
    assert exc.value.code == "TRANSFER_UNBALANCED"


@pytest.mark.parametrize("direction", ["inflow", "", None])
def test_flow_direction_vocabulary_is_limited(direction):
    with pytest.raises(ValueError):
        flow(direction=direction)


@pytest.mark.parametrize("kwargs", [
    {"inputs": []},
    {"outputs": []},
    {"inputs": [("money_fen", "a", 10), ("money_fen", "a", 5)]},
    {"outputs": [("energy_kcal", "a", 1), ("energy_kcal", "a", 2)]},
    {"inputs": [("money_fen", "a", 10), ("energy_kcal", "a", 1)],
     "outputs": [("money_fen", "a", 10)]},
])
def test_convert_rejects_degenerate_or_ambiguous_legs(kwargs):
    with pytest.raises(ResourceLedgerError) as exc:
        convert(**kwargs)
    assert exc.value.code == "CONVERT_IMBALANCED"


def test_convert_leg_coercion_accepts_tuples_and_legs():
    entry = convert(inputs=[ResourceLeg("energy_kcal", "a", 10.5), ("money_fen", "b", 3)])
    assert entry.inputs == (ResourceLeg("energy_kcal", "a", 10.5), ResourceLeg("money_fen", "b", 3))


def test_convert_leg_coercion_rejects_malformed_values():
    with pytest.raises(ValueError):
        convert(inputs=[("energy_kcal", "a")])
    with pytest.raises(ValueError):
        convert(inputs=["energy_kcal"])
