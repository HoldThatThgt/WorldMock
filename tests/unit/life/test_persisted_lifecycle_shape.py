"""真实状态序列化后的生命周期与身份矛盾必须被拒绝。"""

import json

import pytest
from pydantic import ValidationError

from tests.integration.runtime_fixtures import current_run_identity
from tests.life.conftest import do_at, purchase
from tests.life.environment_runtime import act
from worldmock.foundation.errors import IntegrityError
from worldmock.state.world.activity import ActivityState
from worldmock.state.interaction.requests import ConclusionMemory, ErrandChange, PlanGoalInput
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.state.world.orders import DeliveryState
from worldmock.state.identity import RunIdentity
from worldmock.state.world.agent import SessionState
from worldmock.state.world.world import WorldState, validated_payload


def _decode(model, payload):
    return model.model_validate_json(json.dumps(payload, ensure_ascii=False))


@pytest.mark.parametrize("change", [{"git_commit": "a" * 40, "git_dirty": None},
                                    {"git_commit": None, "git_dirty": False}])
def test_runtime_identity_requires_both_git_observations_or_neither(engine_factory, change):
    engine = engine_factory()
    identity = current_run_identity(engine.context.inputs, "identity")
    with pytest.raises(ValidationError, match="both be recorded"):
        _decode(RunIdentity, {**identity.model_dump(mode="json"), **change})


@pytest.mark.parametrize("sources,hypothesis", [("repeat", "实际假设"), ("one", "   ")])
def test_counterfactual_requires_unique_sources_and_actual_hypothesis(sources, hypothesis):
    with pytest.raises(ValidationError, match="unique|blank"):
        CounterfactualIntervention(kind="set_balance_fen", agent_id=1, balance_fen=1, hypothesis=hypothesis,
            source_event_ids=("event", "event") if sources == "repeat" else ("event",))


@pytest.mark.parametrize("change,message", [
    ({"origin_id": "office"}, "place fields"),
    ({"kind": "transit"}, "place fields"),
    ({"ended_at": 1}, "status"),
    ({"status": "ended", "ended_at": 1}, "before started"),
])
def test_session_restore_rejects_impossible_place_or_end_time(engine_factory, change, message):
    engine = engine_factory()
    session = next(iter(engine.context.state.sessions.values())).model_dump(mode="json")
    with pytest.raises(ValidationError, match=message):
        _decode(SessionState, {**session, **change})


@pytest.mark.parametrize("case,message", [
    ("end-with-active", "lifecycle status"), ("terminal-without-end", "lifecycle status"),
    ("future-progress", "progress interval"), ("excess-progress", "progress interval"),
    ("conclude-active", "lifecycle status"), ("conclude-before-end", "lifecycle status"),
])
def test_activity_restore_rejects_contradictory_progress_and_lifecycle(engine_factory, case, message):
    engine = engine_factory()
    _, result = act(engine, "activity", do_at("家", "测试休息"))
    payload = engine.context.state.activities[result.activity_id].model_dump(mode="json")
    if case == "end-with-active":
        payload["ended_at"] = payload["started_at"]
    elif case == "terminal-without-end":
        payload["status"] = "completed"
    elif case == "future-progress":
        payload["last_settled_at"] = payload["planned_end_at"] + 1
    elif case == "excess-progress":
        payload["elapsed_seconds"] = payload["planned_duration_seconds"] + 1
    elif case == "conclude-active":
        payload["concluded_at"] = payload["started_at"]
    else:
        payload.update(status="cancelled", ended_at=payload["started_at"] + 1,
                       concluded_at=payload["started_at"])
    with pytest.raises(ValidationError, match=message):
        _decode(ActivityState, payload)


@pytest.mark.parametrize("case,message", [
    ("no-payment", "timestamp disagree"), ("early-payment", "payment precedes creation"),
    ("early-arrival", "arrival precedes payment"), ("early-receipt", "receipt precedes arrival"),
    ("pickup-at-home", "Only station pickup"),
])
def test_order_restore_rejects_unpaid_and_time_reversed_receipts(engine_factory, case, message):
    engine = engine_factory()
    _, started = act(engine, "purchase", purchase("牛肉面"))
    assert started.status == "in_progress"
    payload = next(iter(engine.context.state.deliveries.values())).model_dump(mode="json")
    now = payload["created_at"]
    payload.update(status="delivered", paid_at=now + 2, arrived_at=now + 3, received_at=now + 4)
    if case == "no-payment":
        payload["paid_at"] = None
    elif case == "early-payment":
        payload["paid_at"] = now - 1
    elif case == "early-arrival":
        payload["arrived_at"] = now + 1
    elif case == "early-receipt":
        payload["received_at"] = now + 2
    else:
        payload.update(status="ready_for_pickup", received_at=None, fulfillment="delivery")
    with pytest.raises(ValidationError, match=message):
        _decode(DeliveryState, payload)


@pytest.mark.parametrize("change,message", [
    ({"payment_duration_seconds": 0}, "payment duration disagrees with its payment method"),
    ({"payment_duration_seconds": 9}, "payment duration disagrees with its payment method"),
    ({"payment_app_id": None, "payment_duration_seconds": 0}, "payment duration disagrees with its payment method"),
    ({"app_id": None, "payment_app_id": None}, "payment duration disagrees with its payment method"),
    ({"app_id": None, "payment_app_id": "alipay", "payment_duration_seconds": 0},
     "payment duration disagrees with its payment method"),
    ({"app_id": None, "payment_app_id": None, "payment_duration_seconds": 0, "payment_started_at": 5},
     "without a payment phase carries payment times"),
    ({"app_id": None, "payment_app_id": None, "payment_duration_seconds": 0, "payment_ended_at": 5},
     "without a payment phase carries payment times"),
])
def test_order_restore_rejects_payment_fields_that_no_order_can_carry(engine_factory, change, message):
    """付款时长为 0 只属于到店直付，其余订单是 10 至 20 秒；没有付款段的记录不带付款时刻。"""
    engine = engine_factory()
    _, started = act(engine, "purchase", purchase("牛肉面"))
    assert started.status == "in_progress"
    payload = next(iter(engine.context.state.deliveries.values())).model_dump(mode="json")
    assert payload["app_id"] == "meituan" and 10 <= payload["payment_duration_seconds"] <= 20
    assert _decode(DeliveryState, payload).model_dump(mode="json") == payload
    with pytest.raises(ValidationError, match=message):
        _decode(DeliveryState, {**payload, **change})
    direct = {**payload, "app_id": None, "payment_app_id": None, "payment_duration_seconds": 0}
    assert _decode(DeliveryState, direct).payment_duration_seconds == 0


@pytest.mark.parametrize("field", ["payment_app_id", "payment_duration_seconds", "payment_started_at",
                                   "payment_ended_at"])
def test_world_restore_rejects_records_of_one_order_that_disagree_on_its_payment(engine_factory, field):
    """同一张订单的两条记录各自都合法，付款字段却不一致：世界状态在恢复时和写入前都被拒绝。"""
    engine = engine_factory()
    _, started = act(engine, "purchase", purchase("牛肉面", "柠檬茶"))
    assert started.status == "in_progress"
    state = engine.context.state
    first, second = (state.deliveries[key] for key in state.activities[started.activity_id].data["order_ids"])
    assert first.app_id == second.app_id == "meituan" and first.payment_started_at is None
    assert all(getattr(first, name) == getattr(second, name) for name in (
        "payment_app_id", "payment_duration_seconds", "payment_started_at", "payment_ended_at"))
    payload = state.model_dump(mode="json")
    assert _decode(WorldState, payload).model_dump(mode="json") == payload
    other = {"payment_app_id": "wechat" if second.payment_app_id == "alipay" else "alipay",
             "payment_duration_seconds": 11 if second.payment_duration_seconds == 10 else 10,
             "payment_started_at": second.created_at, "payment_ended_at": second.created_at}[field]
    payload["deliveries"][second.order_id][field] = other
    assert _decode(DeliveryState, payload["deliveries"][second.order_id]).order_id == second.order_id
    with pytest.raises(ValidationError, match="Records of one order disagree on its payment fields"):
        _decode(WorldState, payload)
    corrupted = state.model_copy(deep=True)
    corrupted.deliveries[second.order_id] = second.model_copy(update={field: other})
    with pytest.raises(IntegrityError, match="Records of one order disagree on its payment fields"):
        validated_payload(corrupted)


def test_world_restore_rejects_a_purchase_activity_that_lists_an_unknown_record(engine_factory):
    """订单由购买活动列出的记录得出：列出的记录不在世界里时，世界状态在恢复时和写入前都被拒绝。"""
    engine = engine_factory()
    _, started = act(engine, "purchase", purchase("牛肉面", "柠檬茶"))
    assert started.status == "in_progress"
    state = engine.context.state
    kept, dropped = state.activities[started.activity_id].data["order_ids"]
    payload = state.model_dump(mode="json")
    del payload["deliveries"][dropped]
    with pytest.raises(ValidationError, match="Purchase activity refers to an unknown order record"):
        _decode(WorldState, payload)
    corrupted = state.model_copy(deep=True)
    del corrupted.deliveries[dropped]
    assert list(corrupted.deliveries) == [kept]
    with pytest.raises(IntegrityError, match="Purchase activity refers to an unknown order record"):
        validated_payload(corrupted)


@pytest.mark.parametrize("case,message", [("clock", "exceeds experiment end"),
    ("identity", "mapping identity"), ("body", "ahead of world time")])
def test_world_restore_rejects_identity_and_time_contradictions(engine_factory, case, message):
    engine = engine_factory()
    payload = engine.context.state.model_dump(mode="json")
    if case == "clock":
        payload["current_time"] = payload["end_time"] + 1
    elif case == "identity":
        payload["agents"]["1"]["agent_id"] = 2
    else:
        payload["agents"]["1"]["body_settled_at"] = payload["current_time"] + 1
    with pytest.raises(ValidationError, match=message):
        _decode(WorldState, payload)
    corrupted = engine.context.state.model_copy(deep=True)
    corrupted.current_time = corrupted.end_time + 1
    with pytest.raises(IntegrityError, match="World state invariant violated before persisting"):
        validated_payload(corrupted)


def test_day_goal_rejects_excess_text_at_its_actual_field():
    with pytest.raises(ValidationError) as caught:
        PlanGoalInput(intent_kind="休息", goal="字" * 301, remind_time=None, around_time=None)
    assert caught.value.errors()[0]["loc"] == ("goal",)


@pytest.mark.parametrize("text", ["字" * 1001, "The reference is abcdef0123456789abcdef0123456789."])
def test_memory_rejects_excess_text_and_hidden_hexadecimal_identities(text):
    with pytest.raises(ValidationError) as caught:
        ConclusionMemory(type="reflection", text=text, importance=0.5, keywords=[])
    assert caught.value.errors()[0]["loc"] == ("text",)


@pytest.mark.parametrize("change", [
    {"change": "新增", "item": None, "quantity": None},
    {"change": "更新", "item": "纸巾", "quantity": 1},
    {"change": "取消", "reason": None},
])
def test_errand_change_reports_required_and_forbidden_fields_together(change):
    payload = {"change": "新增", "name": "买纸巾", "item": "纸巾", "quantity": 1, "memo": None,
               "remind_time": None, "reason": None, **change}
    with pytest.raises(ValidationError) as caught:
        ErrandChange(**payload)
    expected = {"item", "quantity"} if change["change"] != "取消" else {"item", "quantity", "reason"}
    assert {issue["loc"][0] for issue in caught.value.errors()} == expected
