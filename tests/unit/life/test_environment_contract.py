"""@brief 单人环境的动作、固定名额、闭集唤醒与六轮停止的可观察合同。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.life.action_contract import ACTIVITY_ACTIONS
from worldmock.life.models import (
    ActivityKind, ActivityState, ActivityStatus, AgentState, BodyState, ModelIdentity,
    StartActivityRequest, WorldState, WORLD_SCHEMA_VERSION,
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


def activity(identity, action, status=ActivityStatus.ACTIVE):
    kinds = {"sleep": ActivityKind.SLEEP, "interact": ActivityKind.INTERACT,
             "purchase": ActivityKind.PURCHASE, "walk": ActivityKind.TRAVEL}
    terminal = status == ActivityStatus.COMPLETED
    first = action in {"sleep", "walk"}
    return ActivityState(
        activity_id=identity, agent_id=1, location_id="home", kind=kinds[action], status=status,
        started_at=0, ended_at=100 if terminal else None, planned_end_at=100 if terminal else 200,
        last_settled_at=100, elapsed_seconds=100, planned_duration_seconds=100 if terminal else 200,
        resource_claims={"first" if first else "second"}, origin_action_id="a", origin_decision_id="d",
        origin_request_event_id="e", data={"action": action},
    )


def world(activities=(), **changes):
    payload = dict(
        schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=100, end_time=10000,
        run_id="run", timezone="UTC", persona_sha256="a" * 64, config_sha256="b" * 64,
        agents={1: agent()}, accounts={"agent:1": 100, "external:scenario": -100},
        activities={item.activity_id: item for item in activities}, random_seed=3, random_state="frozen",
    )
    return WorldState.model_validate({**payload, **changes})


def test_action_contract_and_normalization():
    assert ACTIVITY_ACTIONS == ("walk", "drive", "take_taxi", "take_bus", "purchase", "sleep", "interact")
    request = StartActivityRequest.model_validate({"items": [{"action": " ｓｌｅｅｐ ", "target": "Home"}]})
    assert request.items[0].action == " ｓｌｅｅｐ "
    assert set(request.model_dump()["items"][0]) == {"action", "target"}


@pytest.mark.parametrize("extra", [{"way": "sleep"}, {"app": None}, {"to": None}, {"pay_app": None}])
def test_retired_request_fields_are_rejected(extra):
    with pytest.raises(ValidationError):
        StartActivityRequest.model_validate({"items": [{"action": "sleep", "target": "Home", **extra}]})


@pytest.mark.parametrize("action", ["wait", "Sleep", "action", "chat"])
def test_unknown_or_wrong_case_action_reaches_shared_name_resolution(action):
    from worldmock.life.name_resolution import NameBook, resolve_start

    request = StartActivityRequest.model_validate({"items": [{"action": action, "target": "Home"}]})
    outcome = resolve_start(NameBook(()), request)
    assert outcome.failures[0].item.error_code == "ACTION_NOT_OFFERED"


def test_duplicate_normalized_target_is_atomic_rejection():
    with pytest.raises(ValidationError):
        StartActivityRequest.model_validate({"items": [
            {"action": "purchase", "target": "Food"}, {"action": "purchase", "target": "Ｆｏｏｄ"}]})


def test_two_slots_and_pending_conclusion_occupancy():
    world([activity("walk", "walk"), activity("media", "interact")])
    world([activity("old", "interact", ActivityStatus.PAUSED), activity("buy", "purchase")])
    with pytest.raises(ValidationError, match="same resource"):
        world([activity("media", "interact"), activity("buy", "purchase")])
    with pytest.raises(ValidationError, match="same resource"):
        world([activity("old", "interact", ActivityStatus.COMPLETED), activity("buy", "purchase")])


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
