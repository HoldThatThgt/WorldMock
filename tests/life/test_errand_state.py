"""个人事务状态模型 ErrandState 的合同测试（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-范围与状态  字段表与「存放」）。

ErrandState 是一条未完成事务：封闭（拒绝未知字段）、不可变；数量 1 至 9；已累计量是九位小数、恒小于数量；只有设了提醒
时刻的事务才有交付时刻，且交付不早于提醒时刻。AgentState.errands 以事务身份为键保存它们，随世界状态往返。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.life import errand_state
from worldmock.life.errand_state import ErrandState


def _errand(**overrides) -> dict:
    """一条合法的未完成事务（取得一份素炒什锦食材包，尚无进展、带备注与未交付的提醒）。"""
    fields = {"errand_id": "action-1:errand:0", "name": "补一份素炒什锦食材包", "outcome": "obtain",
              "product_id": "ingredient-vegetable-kit", "quantity": 1, "progress": 0.0, "memo": "下班前下单送到家",
              "registered_at": 1_000, "remind_at": 2_000, "reminded_at": None,
              "origin_action_id": "action-1", "origin_event_id": "event-1"}
    return {**fields, **overrides}


def test_a_valid_open_errand_keeps_every_field_as_written() -> None:
    """合法的未完成事务照写保存：名称与备注原样，结果种类是代码 obtain 或 eat；部分进展与已交付的提醒都合法。"""
    errand = ErrandState(**_errand())
    assert errand.model_dump() == _errand()
    eaten = ErrandState(**_errand(outcome="eat", product_id="meal-0030", quantity=2, progress=1.5, memo=None,
                                  remind_at=2_000, reminded_at=2_000))
    assert (eaten.outcome, eaten.progress, eaten.reminded_at) == ("eat", 1.5, 2_000)
    assert list(ErrandState.model_fields) == [
        "errand_id", "name", "outcome", "product_id", "quantity", "progress", "memo", "registered_at", "remind_at",
        "reminded_at", "origin_action_id", "origin_event_id"]


def test_progress_is_kept_to_nine_decimals_and_must_stay_below_the_quantity() -> None:
    """已累计量按九位小数保存（浮点相加误差不进入状态）；达到或超过数量的事务不能是未完成状态（达到即完成并移出）。"""
    assert ErrandState(**_errand(quantity=2, progress=0.1 + 0.2)).progress == 0.3
    assert ErrandState(**_errand(quantity=2, progress=1.0000000001)).progress == 1.0
    for progress in (1.0, 1.5):
        with pytest.raises(ValidationError, match="errand progress must stay below its quantity"):
            ErrandState(**_errand(progress=progress))
    with pytest.raises(ValidationError, match="errand progress must stay below its quantity"):
        ErrandState(**_errand(quantity=1, progress=0.9999999999))
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ErrandState(**_errand(progress=-0.5))


@pytest.mark.parametrize("quantity", [0, 10, -1])
def test_quantity_outside_one_to_nine_is_rejected(quantity: int) -> None:
    """数量只取 1 至 9（errand_state 的两个上下限）。"""
    assert (errand_state.ERRAND_QUANTITY_MIN, errand_state.ERRAND_QUANTITY_MAX) == (1, 9)
    with pytest.raises(ValidationError, match="quantity"):
        ErrandState(**_errand(quantity=quantity))


@pytest.mark.parametrize(("remind_at", "reminded_at"), [(None, 2_000), (2_000, 1_999)])
def test_a_reminder_delivery_requires_an_earlier_reminder(remind_at, reminded_at) -> None:
    """交付时刻要求有提醒时刻：没有提醒时刻却有交付、交付早于提醒时刻都被拒绝。"""
    with pytest.raises(ValidationError, match="errand reminded_at requires remind_at and must not precede it"):
        ErrandState(**_errand(remind_at=remind_at, reminded_at=reminded_at))


def test_the_model_is_closed_frozen_and_every_field_is_required() -> None:
    """未知字段被拒绝；构造后不可改；十二个字段都必填（可空的也要写 null），名称不能只是空白，结果种类只有两种代码。"""
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        ErrandState(**_errand(status="未完成"))
    errand = ErrandState(**_errand())
    with pytest.raises(ValidationError, match="frozen"):
        errand.progress = 0.5
    for field in ErrandState.model_fields:
        fields = _errand()
        del fields[field]
        with pytest.raises(ValidationError, match=field):
            ErrandState(**fields)
    with pytest.raises(ValidationError, match="name"):
        ErrandState(**_errand(name="   "))
    with pytest.raises(ValidationError, match="outcome"):
        ErrandState(**_errand(outcome="取得"))


def test_limits_words_and_event_kinds_are_the_spec_values() -> None:
    """上限、变更与结果种类的写法、六种事务事件与两种人物可见的种类都是规格的取值（2.1、2.2、2.7）。"""
    assert (errand_state.MAX_OPEN_ERRANDS, errand_state.MAX_ERRAND_CHANGES) == (8, 4)
    assert (errand_state.ERRAND_NAME_MAX_CHARS, errand_state.ERRAND_TEXT_MAX_CHARS) == (30, 120)
    assert errand_state.ERRAND_TOLERANCE == 1e-9
    assert errand_state.ERRAND_CHANGES == ("新增", "更新", "取消")
    assert errand_state.OUTCOME_CODES == {"取得": "obtain", "吃掉": "eat"}
    assert errand_state.OUTCOME_WORDS == {code: word for word, code in errand_state.OUTCOME_CODES.items()}
    assert errand_state.ERRAND_EVENT_KINDS == ("errand_registered", "errand_updated", "errand_cancelled",
                                               "errand_progressed", "errand_completed", "errand_reminded")
    assert errand_state.AGENT_VISIBLE_KINDS == {"errand_completed", "errand_reminded"}
