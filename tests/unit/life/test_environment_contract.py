"""@brief 单人环境的动作、固定名额、闭集唤醒与六轮停止的可观察合同。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.exchange_engine.conditions.activity_rules import (
    ACTIVITY_ACTIONS,
    MAIN_ACTIVITY_ACTIONS,
    SIDE_ACTIVITY_ACTIONS,
)
from worldmock.state.world.kinds import ActivityKind, ActivityStatus
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.agent import AgentState, BodyState
from worldmock.configuration.inputs.models import ModelMetadata
from worldmock.state.identity import ModelIdentity, StageProfileIdentity
from worldmock.state.interaction.requests import SearchWorldRequest, StartMainActivityRequest, StartSideActivityRequest
from worldmock.state.world.world import WorldState, WORLD_SCHEMA_VERSION
from worldmock.state.planning import schedule
from worldmock.state.planning.schedule import (
    NextDecisionQuery,
    WAKE_TRIGGERS,
    WakeSnapshot,
    WakeTrigger,
    evaluate_wake_reasons,
    next_decision_time,
    wake_categories,
)


def agent():
    return AgentState(
        agent_id=1, name="Person", location_id="home", balance_account_id="agent:1",
        body=BodyState(energy_kcal=1200.0, sleep_pressure=0.2, sleep_deficit_hours=0.0,
                       sleep_need_hours=7.5, chronotype_midsleep=4.0, relaxation_need=0.2,
                       social_need=0.2, social_need_growth_per_hour=0.06),
        body_settled_at=100, next_decision_at=100,
    )


def activity(identity, action, status=ActivityStatus.ACTIVE, app_id="media-app"):
    """手工活动：interact 缺省用内容 app（占槽位 2），app_id 为 None 时是在地点做事（占槽位 1）；搜索带 app_id，
    不论有没有 app 都占槽位 2。"""
    kinds = {"sleep": ActivityKind.SLEEP, "interact": ActivityKind.INTERACT,
             "purchase": ActivityKind.PURCHASE, "travel": ActivityKind.TRAVEL, "search": ActivityKind.SEARCH}
    terminal = status == ActivityStatus.COMPLETED
    data = {"action": action, **({"app_id": app_id} if action in {"interact", "search"} else {})}
    first = action in {"sleep", "travel"} or (action == "interact" and app_id is None)
    return ActivityState(
        activity_id=identity, agent_id=1, location_id="home", kind=kinds[action], status=status,
        started_at=0, ended_at=100 if terminal else None, planned_end_at=100 if terminal else 200,
        last_settled_at=100, elapsed_seconds=100, planned_duration_seconds=100 if terminal else 200,
        resource_claims={"first" if first else "second"}, origin_action_id="a", origin_wakeup_id="d",
        origin_request_event_id="e", data=data,
    )


def world(activities=(), **changes):
    payload = dict(
        schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=100, end_time=10000,
        run_id="run", timezone="UTC", persona_sha256s={1: "a" * 64}, config_sha256="b" * 64,
        agents={1: agent()}, accounts={"agent:1": 100, "external:scenario": -100},
        activities=list(activities), random_seed=3, random_state="frozen",
    )
    return WorldState.model_validate({**payload, **changes})


_MAIN_SLEEP = {"action": "sleep", "target": "450", "detail": None}
_SIDE_PURCHASE = {"action": "purchase", "target": ["Food"], "prefer": None}
_START_MODELS = ((StartMainActivityRequest, _MAIN_SLEEP), (StartSideActivityRequest, _SIDE_PURCHASE))


def test_action_contract_and_request_shapes():
    """五个动作与独立搜索工具；请求只带声明的键，所写名称原样保留。"""
    assert ACTIVITY_ACTIONS == ("travel", "purchase", "sleep", "interact", "search")
    assert MAIN_ACTIVITY_ACTIONS == ("travel", "interact", "sleep")
    assert SIDE_ACTIVITY_ACTIONS == ("purchase", "interact")
    main = StartMainActivityRequest.model_validate({"action": "travel", "target": " Ｈｏｍｅ ", "detail": "步行"})
    assert main.model_dump() == {"action": "travel", "target": " Ｈｏｍｅ ", "detail": "步行"}
    assert StartSideActivityRequest.model_validate(_SIDE_PURCHASE).model_dump() == _SIDE_PURCHASE
    assert SearchWorldRequest(query="想喝热汤", prefer=None).model_dump() == {"query": "想喝热汤", "prefer": None}


@pytest.mark.parametrize("extra", [{"way": "sleep"}, {"app": None}, {"to": None}, {"pay_app": None},
                                   {"items": [{"action": "sleep", "target": "Home"}]}])
def test_retired_request_fields_are_rejected(extra):
    """旧字段与旧的 items 形状在两个开始工具上都是多余字段。"""
    for model, valid in _START_MODELS:
        model.model_validate(valid)
        with pytest.raises(ValidationError) as caught:
            model.model_validate({**valid, **extra})
        assert [(error["type"], error["loc"]) for error in caught.value.errors()] == [
            ("extra_forbidden", (next(iter(extra)),))]


@pytest.mark.parametrize("action", ["wait", "Sleep", " ｓｌｅｅｐ ", "action", "chat", "walk", "take_taxi"])
def test_unknown_or_wrong_case_action_is_refused_by_the_request_model(action):
    """动作不再作名称归一：闭集之外的写法（大小写、全角、旧出行动作）在请求模型处就被拒，到不了名称解析。"""
    for model, valid in _START_MODELS:
        with pytest.raises(ValidationError) as caught:
            model.model_validate({**valid, "action": action})
        assert [(error["type"], error["loc"], error["input"]) for error in caught.value.errors()] == [
            ("literal_error", ("action",), action)]


def test_repeated_purchase_name_is_two_portions_not_a_rejection():
    """同一个名称（含归一后相同的写法）写两次不再被拒：每个元素是一份，所写原样保留。"""
    request = StartSideActivityRequest.model_validate(
        {"action": "purchase", "target": ["Food", "Ｆｏｏｄ", "Food"], "prefer": None})
    assert request.target == ["Food", "Ｆｏｏｄ", "Food"]


def test_two_slots_and_pending_conclusion_occupancy():
    """在地点做事占槽位 1，用内容 app、购买与搜索占槽位 2；同槽位的两项进行中或待收尾活动不能并存；
    活动的 data.action 不在五个动作之内时拒绝。"""
    world([activity("walk", "travel"), activity("media", "interact")])
    world([activity("old", "interact", ActivityStatus.PAUSED), activity("buy", "purchase")])
    world([activity("stay", "interact", app_id=None), activity("media", "interact")])
    world([activity("stay", "interact", app_id=None), activity("buy", "purchase")])
    world([activity("stay", "interact", app_id=None), activity("look", "search", app_id=None)])
    world([activity("stay", "interact", app_id=None), activity("look", "search", app_id="meituan")])
    with pytest.raises(ValidationError, match="world activity resource claims disagree with the registry"):
        world([activity("look", "search").model_copy(update={"resource_claims": {"first"}})])
    with pytest.raises(ValidationError, match="world active activities claim the same resource"):
        world([activity("look", "search"), activity("buy", "purchase")])
    unknown = activity("look", "search")
    with pytest.raises(ValidationError, match="Activity action is missing or unknown"):
        world([unknown.model_copy(update={"data": {**unknown.data, "action": "lookup"}})])
    with pytest.raises(ValidationError, match="same resource"):
        world([activity("media", "interact"), activity("buy", "purchase")])
    with pytest.raises(ValidationError, match="same resource"):
        world([activity("old", "interact", ActivityStatus.COMPLETED), activity("buy", "purchase")])
    with pytest.raises(ValidationError, match="same resource"):
        world([activity("walk", "travel"), activity("stay", "interact", app_id=None)])
    with pytest.raises(ValidationError, match="disagree with the registry"):
        world([activity("stay", "interact", app_id=None).model_copy(update={"resource_claims": {"second"}})])


def test_sleep_requires_empty_second_slot():
    with pytest.raises(ValidationError, match="second activity slot"):
        world([activity("sleep", "sleep"), activity("buy", "purchase")])
    with pytest.raises(ValidationError, match="Sleep requires the second activity slot to be empty"):
        world([activity("sleep", "sleep"), activity("look", "search")])
    world([activity("sleep", "sleep"), activity("media", "interact", ActivityStatus.PAUSED)])


def test_only_external_account_can_be_signed():
    world()
    with pytest.raises(ValidationError, match="cannot be negative"):
        world(accounts={"agent:1": -1, "external:scenario": 1})
    with pytest.raises(ValidationError, match="N\\+1 accounts"):
        world(accounts={"agent:1": 100, "external:scenario": -100, "merchant": 0})


def test_exact_wake_names_and_category_order():
    assert [trigger.name for trigger in WAKE_TRIGGERS] == [
        "planned_end", "felt_band_change", "delivery_arrival", "reconsideration_interval", "rejection_feedback",
        "session_open", "progress_report_due", "no_active_activity", "reminder_due", "duty_boundary", "app_push",
    ]
    snapshot = WakeSnapshot(now=100, planned_end=100, felt_bands_now={"relaxation_need": "high"},
                            delivery_arrived=True, last_decision_time=0, reconsideration_interval_seconds=100,
                            session_open=True, progress_report_due=True, no_active_activity=True,
                            reminder_due=True, duty_boundary=True, app_push_arrived=True)
    verdict = evaluate_wake_reasons(snapshot)
    assert verdict.wake_reasons == [
        "planned_end", "felt_band_change", "delivery_arrival", "reconsideration_interval", "session_open",
        "progress_report_due", "no_active_activity", "reminder_due", "duty_boundary", "app_push",
    ]
    assert wake_categories(verdict.wake_reasons) == (
        "time_boundary", "state_change", "event_arrival", "reminder_due", "duty_boundary")
    with pytest.raises(ValueError):
        wake_categories(["alarm_ring"])


#: 唤醒表的名称、类别与能否叫醒睡着的人，独立写出，不从被测的表生成。
_WAKE_TABLE = [
    ("planned_end", "time_boundary", True), ("felt_band_change", "state_change", False),
    ("delivery_arrival", "event_arrival", False), ("reconsideration_interval", "time_boundary", False),
    ("rejection_feedback", "outcome_feedback", False), ("session_open", "time_boundary", True),
    ("progress_report_due", "time_boundary", False), ("no_active_activity", "time_boundary", False),
    ("reminder_due", "reminder_due", False), ("duty_boundary", "duty_boundary", False),
    ("app_push", "reminder_due", False),
]


def _everything(**changes):
    """除 rejection_feedback 外每个原因都命中的边界。"""
    fields = dict(now=100, planned_end=100, planned_end_is_sleep=True, felt_bands_now={"social_need": "high"},
                  delivery_arrived=True, last_decision_time=0, reconsideration_interval_seconds=1,
                  session_open=True, progress_report_due=True, no_active_activity=True, reminder_due=True,
                  duty_boundary=True, app_push_arrived=True, is_asleep=True)
    return WakeSnapshot(**{**fields, **changes})


def test_wake_table_rows_categories_and_sleep_column_are_the_declared_ones():
    """唤醒表的行集合、类别、行序与 can_wake_sleep 一列逐行相符。"""
    assert [(row.name, row.category, row.can_wake_sleep) for row in WAKE_TRIGGERS] == _WAKE_TABLE


def test_a_sleeper_is_only_offered_the_rows_that_can_wake_sleep():
    """睡着时只放行表里能叫醒睡着的人的行；同一个边界人物醒着时命中的都保留。"""
    assert evaluate_wake_reasons(_everything()).wake_reasons == ["planned_end", "session_open"]
    assert evaluate_wake_reasons(_everything(is_asleep=False)).wake_reasons == [
        "planned_end", "felt_band_change", "delivery_arrival", "reconsideration_interval", "session_open",
        "progress_report_due", "no_active_activity", "reminder_due", "duty_boundary", "app_push"]
    silent = _everything(planned_end=None, planned_end_is_sleep=False, session_open=False)
    assert evaluate_wake_reasons(silent).wake_reasons == []
    assert evaluate_wake_reasons(silent).decision_offered is False


def test_planned_end_of_something_other_than_the_sleep_does_not_wake_a_sleeper():
    """睡着时结束的不是睡眠本身，planned_end 一行不放行；人物醒着时别的活动结束照常放行。"""
    other = _everything(planned_end_is_sleep=False, session_open=False)
    assert evaluate_wake_reasons(other).wake_reasons == []
    assert "planned_end" in evaluate_wake_reasons(other.model_copy(update={"is_asleep": False})).wake_reasons
    assert evaluate_wake_reasons(_everything(planned_end=101)).wake_reasons == ["session_open"]


@pytest.mark.parametrize("name,expected", [
    ("reminder_due", ["planned_end", "session_open", "reminder_due"]),
    ("delivery_arrival", ["planned_end", "delivery_arrival", "session_open"]),
    ("session_open", ["planned_end"]),
    ("planned_end", ["session_open"]),
])
def test_changing_the_sleep_column_of_a_row_changes_who_is_let_through(monkeypatch, name, expected):
    """睡着时放行谁读的是表里的 can_wake_sleep：把某一行的这一列改掉，结果随之改变。"""
    changed = tuple(WakeTrigger(row.name, row.category, not row.can_wake_sleep) if row.name == name else row
                    for row in WAKE_TRIGGERS)
    monkeypatch.setattr(schedule, "WAKE_TRIGGERS", changed)
    assert evaluate_wake_reasons(_everything()).wake_reasons == expected


def test_sleep_ignores_all_external_wake_conditions():
    snapshot = WakeSnapshot(now=100, planned_end=100, planned_end_is_sleep=False,
                            felt_bands_now={"social_need": "high"}, delivery_arrived=True,
                            last_decision_time=0, reconsideration_interval_seconds=1, session_open=False,
                            progress_report_due=True, no_active_activity=True, reminder_due=True,
                            duty_boundary=True, app_push_arrived=True, is_asleep=True)
    assert evaluate_wake_reasons(snapshot).wake_reasons == []
    assert evaluate_wake_reasons(snapshot.model_copy(update={"planned_end_is_sleep": True})).wake_reasons == [
        "planned_end"]
    query = NextDecisionQuery(now=100, planned_end=1000, felt_band_crossing_times=(101,),
                              scheduled_arrival_times=(102,), reminder_times=(103,), duty_edge_times=(104,),
                              next_progress_report_at=105, last_decision_time=100,
                              reconsideration_interval_seconds=6, is_asleep=True)
    assert next_decision_time(query) == 1000


@pytest.mark.parametrize("field", ["run_start", "wake_from_sleep", "alarm_rang", "message_arrived",
                                   "has_commitments", "idle_reconsideration_interval_seconds"])
def test_deleted_wake_inputs_are_rejected(field):
    with pytest.raises(ValidationError):
        WakeSnapshot.model_validate({"now": 100, field: True})


def test_model_identity_accepts_configured_endpoint_and_budgets():
    meta = ModelMetadata(requested_model="dataset-model", metadata_url="https://model.example/models",
                         context_window=262144, max_output_tokens=8192)
    profile = StageProfileIdentity(
        provider="openai_compatible", base_url="https://model.example/v1", requested_model="dataset-model",
        metadata_url="https://model.example/models", context_window=262144, thinking=True,
        max_output_tokens=4096, max_retries=2, transport_max_retries=3, max_react_turns=8,
        reflection_validation_max_retries=2, day_plan_validation_max_retries=2,
        model_metadata=meta, effective_max_tokens=4096,
    )
    identity = ModelIdentity(
        profiles={"default": profile},
        routing={"action": "default", "search": "default", "plan_day": "default",
                 "digest": "default", "agent_overrides": {}},
        prompt_sha256s={"action": "a" * 64, "search": "b" * 64},
        tool_schema_sha256s={"action": "c" * 64, "search": "d" * 64},
    )
    assert identity.profiles["default"].requested_model == "dataset-model"
    with pytest.raises(ValidationError):
        StageProfileIdentity.model_validate({**profile.model_dump(), "max_retries": 11})
