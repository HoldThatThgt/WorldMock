"""@brief 单人环境的动作、固定名额、闭集唤醒与六轮停止的可观察合同。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.life.action_contract import ACTIVITY_ACTIONS, MAIN_ACTIVITY_ACTIONS, SIDE_ACTIVITY_ACTIONS
from worldmock.life.models import (
    ActivityKind, ActivityState, ActivityStatus, AgentState, BodyState, ModelIdentity,
    StartMainActivityRequest, StartSideActivityRequest, WorldState, WORLD_SCHEMA_VERSION,
)
from worldmock.life.same_moment import RoundOutcome, budget_exhausted, round_wake_reasons
from worldmock.life.schedule import (
    NextDecisionQuery, WAKE_TRIGGERS, WakeSnapshot, evaluate_wake_reasons,
    next_decision_time, wake_categories,
)
from worldmock.profile.mapper import map_world_dynamics
from worldmock.profile.models import BigFiveProfile, WorldDynamics


def agent():
    return AgentState(
        agent_id=1, name="Person", location_id="home", balance_account_id="agent:1",
        body=BodyState(energy_kcal=1200.0, sleep_pressure=0.2, sleep_deficit_hours=0.0,
                       sleep_need_hours=7.5, chronotype_midsleep=4.0, relaxation_need=0.2,
                       social_need=0.2, social_need_growth_per_hour=0.06),
        body_settled_at=100, next_decision_at=100,
    )


def activity(identity, action, status=ActivityStatus.ACTIVE, app_id="media-app"):
    """手工活动：interact 缺省用内容 app（占槽位 2），app_id 为 None 时是在地点做事（占槽位 1）。"""
    kinds = {"sleep": ActivityKind.SLEEP, "interact": ActivityKind.INTERACT,
             "purchase": ActivityKind.PURCHASE, "walk": ActivityKind.TRAVEL}
    terminal = status == ActivityStatus.COMPLETED
    data = {"action": action, **({"app_id": app_id} if action == "interact" else {})}
    first = action in {"sleep", "walk"} or (action == "interact" and app_id is None)
    return ActivityState(
        activity_id=identity, agent_id=1, location_id="home", kind=kinds[action], status=status,
        started_at=0, ended_at=100 if terminal else None, planned_end_at=100 if terminal else 200,
        last_settled_at=100, elapsed_seconds=100, planned_duration_seconds=100 if terminal else 200,
        resource_claims={"first" if first else "second"}, origin_action_id="a", origin_decision_id="d",
        origin_request_event_id="e", data=data,
    )


def world(activities=(), **changes):
    payload = dict(
        schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=100, end_time=10000,
        run_id="run", timezone="UTC", persona_sha256="a" * 64, config_sha256="b" * 64,
        agents={1: agent()}, accounts={"agent:1": 100, "external:scenario": -100},
        activities={item.activity_id: item for item in activities}, random_seed=3, random_state="frozen",
    )
    return WorldState.model_validate({**payload, **changes})


_MAIN_SLEEP = {"action": "sleep", "target": "450", "detail": None}
_SIDE_PURCHASE = {"action": "purchase", "target": ["Food"]}
_START_MODELS = ((StartMainActivityRequest, _MAIN_SLEEP), (StartSideActivityRequest, _SIDE_PURCHASE))


def test_action_contract_and_request_shapes():
    """四个动作分属两个开始工具；请求只带声明的键，所写名称原样保留。"""
    assert ACTIVITY_ACTIONS == ("travel", "purchase", "sleep", "interact")
    assert MAIN_ACTIVITY_ACTIONS == ("travel", "interact", "sleep")
    assert SIDE_ACTIVITY_ACTIONS == ("purchase", "interact")
    main = StartMainActivityRequest.model_validate({"action": "travel", "target": " Ｈｏｍｅ ", "detail": "步行"})
    assert main.model_dump() == {"action": "travel", "target": " Ｈｏｍｅ ", "detail": "步行"}
    assert StartSideActivityRequest.model_validate(_SIDE_PURCHASE).model_dump() == _SIDE_PURCHASE


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
    request = StartSideActivityRequest.model_validate({"action": "purchase", "target": ["Food", "Ｆｏｏｄ", "Food"]})
    assert request.target == ["Food", "Ｆｏｏｄ", "Food"]


def test_two_slots_and_pending_conclusion_occupancy():
    """在地点做事占槽位 1，用内容 app 与购买占槽位 2；同槽位的两项进行中或待收尾活动不能并存。"""
    world([activity("walk", "walk"), activity("media", "interact")])
    world([activity("old", "interact", ActivityStatus.PAUSED), activity("buy", "purchase")])
    world([activity("stay", "interact", app_id=None), activity("media", "interact")])
    world([activity("stay", "interact", app_id=None), activity("buy", "purchase")])
    with pytest.raises(ValidationError, match="same resource"):
        world([activity("media", "interact"), activity("buy", "purchase")])
    with pytest.raises(ValidationError, match="same resource"):
        world([activity("old", "interact", ActivityStatus.COMPLETED), activity("buy", "purchase")])
    with pytest.raises(ValidationError, match="same resource"):
        world([activity("walk", "walk"), activity("stay", "interact", app_id=None)])
    with pytest.raises(ValidationError, match="disagree with the registry"):
        world([activity("stay", "interact", app_id=None).model_copy(update={"resource_claims": {"second"}})])


def test_sleep_requires_empty_second_slot():
    with pytest.raises(ValidationError, match="second activity slot"):
        world([activity("sleep", "sleep"), activity("buy", "purchase")])
    world([activity("sleep", "sleep"), activity("media", "interact", ActivityStatus.PAUSED)])


def test_only_external_account_can_be_signed():
    world()
    with pytest.raises(ValidationError, match="cannot be negative"):
        world(accounts={"agent:1": -1, "external:scenario": 1})
    with pytest.raises(ValidationError, match="two accounts"):
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
    assert round_wake_reasons(False, True) == ["rejection_feedback", "no_active_activity"]
    with pytest.raises(ValueError):
        wake_categories(["alarm_ring"])


def test_sleep_ignores_all_external_wake_conditions():
    snapshot = WakeSnapshot(now=100, planned_end=100, planned_end_is_sleep=False,
                            felt_bands_now={"social_need": "high"}, delivery_arrived=True,
                            last_decision_time=0, reconsideration_interval_seconds=1, session_open=True,
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


@pytest.mark.parametrize("rounds,active,pending,exhausted", [
    (5, False, False, False), (6, False, False, True), (6, True, False, False), (6, True, True, True)])
def test_exact_six_round_budget(rounds, active, pending, exhausted):
    person = agent().model_copy(update={"same_moment_rounds": rounds})
    assert budget_exhausted(RoundOutcome(100, person, active, False, pending)) is exhausted


@pytest.mark.parametrize("score,expected", [(1, 0.02), (4, 0.06), (7, 0.10)])
def test_social_growth_mapping_is_the_only_world_dynamics(score, expected):
    traits = BigFiveProfile(openness=4, conscientiousness=4, extraversion=score, agreeableness=4, neuroticism=4)
    assert map_world_dynamics(traits).model_dump() == {"social_need_growth_per_hour": expected}
    with pytest.raises(ValidationError):
        WorldDynamics.model_validate({"social_need_growth_per_hour": expected, "mood_halflife_minutes": 45.0})


def test_model_identity_accepts_configured_endpoint_and_budgets():
    identity = ModelIdentity(provider="openai_compatible", base_url="https://model.example/v1",
                             requested_model="dataset-model", thinking="enabled", tool_choice="required",
                             max_retries=2, transport_max_retries=3,
                             prompt_sha256="a" * 64, tool_schema_sha256="b" * 64)
    assert identity.requested_model == "dataset-model"
    with pytest.raises(ValidationError):
        ModelIdentity.model_validate({**identity.model_dump(), "max_retries": 11})
