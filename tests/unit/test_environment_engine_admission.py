"""当前环境的固定名额、睡眠隔离与实际在场准入。"""

from __future__ import annotations

import pytest

from worldmock.life.admission import Context, Item, group_refusal
from worldmock.life.models import (
    ActivityKind, ActivityState, ActivityStatus, AgentState, BodyState,
    SessionKind, SessionState, SessionStatus, WorldState, WORLD_SCHEMA_VERSION,
)
from worldmock.life.schedule import WakeSnapshot, evaluate_wake_reasons


def world() -> WorldState:
    agent = AgentState(agent_id=1, name="Test", location_id="home", balance_account_id="agent:1",
        body=BodyState(energy_kcal=1200.0, sleep_pressure=0.2, sleep_deficit_hours=0.0,
            sleep_need_hours=8.0, chronotype_midsleep=4.0, relaxation_need=0.2, social_need=0.2,
            social_need_growth_per_hour=0.06), body_settled_at=100, next_decision_at=100)
    session = SessionState(session_id="session", agent_id=1, kind=SessionKind.STAY, location_id="home",
        status=SessionStatus.ACTIVE, started_at=100, name="Home")
    return WorldState(schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=100, end_time=10000,
        run_id="run", timezone="UTC", persona_sha256="a"*64, config_sha256="b"*64,
        agents={1:agent}, accounts={"agent:1":10000,"external:scenario":-10000}, sessions={"session":session},
        random_seed=1, random_state="frozen")


def running(action: str, kind: ActivityKind, slot: str, *, presence: bool = False) -> ActivityState:
    data = {"action": action}
    if kind == ActivityKind.INTERACT:
        data["app_id"] = None if presence else "content-app"
    return ActivityState(activity_id="active", agent_id=1, location_id="home", kind=kind,
        status=ActivityStatus.ACTIVE, started_at=100, planned_end_at=1000, last_settled_at=100,
        elapsed_seconds=0, planned_duration_seconds=900, resource_claims={slot}, origin_action_id="action",
        origin_decision_id="decision", origin_request_event_id="requested", data=data, name="Activity")


def refusal(state: WorldState, items: list[Item]):
    return group_refusal(Context(state,1,{"home":"Home","store":"Store"}), items)


@pytest.mark.parametrize("action", ["walk","drive","take_taxi","take_bus"])
def test_remote_interaction_and_each_travel_can_share_distinct_slots(action):
    state=world()
    state.activities["active"]=running("interact",ActivityKind.INTERACT,"second")
    assert refusal(state,[Item(action,"Store",frozenset({"first"}))]) is None


def test_multiple_purchase_items_share_one_process_slot():
    assert refusal(world(),[Item("purchase","Bread",frozenset({"second"})),
        Item("purchase","Coffee",frozenset({"second"}))]) is None


@pytest.mark.parametrize("other", ["interact"])
def test_purchase_and_any_other_second_slot_activity_are_atomic_conflicts(other):
    rejected=refusal(world(),[Item("purchase","Bread",frozenset({"second"})),
        Item(other,"App",frozenset({"second"}))])
    assert rejected.code == "RESOURCE_CONFLICT"


def test_running_purchase_blocks_another_purchase_request():
    state=world()
    state.activities["active"]=running("purchase",ActivityKind.PURCHASE,"second")
    assert refusal(state,[Item("purchase","Bread",frozenset({"second"}))]).code == "RESOURCE_CONFLICT"


@pytest.mark.parametrize("reverse", [False,True])
def test_sleep_and_second_slot_are_rejected_in_any_item_order(reverse):
    items=[Item("sleep","Home",frozenset({"first"}),"home"),
           Item("interact","App",frozenset({"second"}))]
    rejected=refusal(world(),list(reversed(items)) if reverse else items)
    assert rejected.code == "RESOURCE_CONFLICT"


def test_sleep_blocks_second_slot_start_until_its_own_end():
    state=world()
    state.activities["active"]=running("sleep",ActivityKind.SLEEP,"first")
    assert refusal(state,[Item("interact","App",frozenset({"second"}))]).code == "RESOURCE_CONFLICT"


def test_only_presence_dependent_running_activity_blocks_departure():
    state=world()
    state.activities["active"]=running("interact",ActivityKind.INTERACT,"second",presence=True)
    rejected=refusal(state,[Item("walk","Store",frozenset({"first"}))])
    assert rejected.code == "ACTIVITY_PRECONDITION_UNMET"


def test_location_interaction_cannot_start_in_same_request_as_departure():
    rejected=refusal(world(),[Item("walk","Store",frozenset({"first"})),
        Item("interact","Home",frozenset({"second"}),"home")])
    assert rejected.code == "ACTIVITY_PRECONDITION_UNMET"
    assert "cannot start with travel" in rejected.reason


@pytest.mark.parametrize("flags",[
    {"delivery_arrived":True},{"reminder_due":True},{"duty_boundary":True},{"app_push_arrived":True},
    {"last_decision_time":0,"reconsideration_interval_seconds":60},{"progress_report_due":True},
])
def test_external_events_never_wake_sleep(flags):
    result=evaluate_wake_reasons(WakeSnapshot(now=100,is_asleep=True,planned_end=500,
        planned_end_is_sleep=True,**flags))
    assert result.decision_offered is False
    assert result.wake_reasons == []


def test_sleep_ends_only_at_its_real_planned_end():
    result=evaluate_wake_reasons(WakeSnapshot(now=500,is_asleep=True,planned_end=500,
        planned_end_is_sleep=True,delivery_arrived=True,reminder_due=True,duty_boundary=True))
    assert result.wake_reasons == ["planned_end"]
