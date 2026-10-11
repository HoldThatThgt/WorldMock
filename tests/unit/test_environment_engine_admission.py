"""当前环境开始组的固定槽位、睡眠隔离与出发前的准入。

槽位占用与睡眠约束排在对象条件之前，用手工世界经 group_refusal 直接核对；整组成立（返回 None）与对象条件要读真实
目录，改用夹具引擎的世界、观察与共用名称解析得到的绑定行。
"""

from __future__ import annotations

import pytest

from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, context, purchase, tool_of, travel, use_app
from tests.life.environment_runtime import act
from worldmock.life.action_contract import binding_action
from worldmock.life.admission import Blocked, Call, Context, group_refusal
from worldmock.life.guidance import Refusal
from worldmock.life.models import (
    ActivityKind, ActivityState, ActivityStatus, AgentState, BodyState,
    SessionKind, SessionState, SessionStatus, WorldState, WORLD_SCHEMA_VERSION,
)
from worldmock.life.name_resolution import name_book, resolve_start, sleep_row
from worldmock.life.schedule import WakeSnapshot, evaluate_wake_reasons
from worldmock.life.world_ops import place_names

_SLEEP_REASON = "Sleep requires the second activity slot to be empty."


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
    """一项进行中的手工活动；presence 为真的 interact 是在地点做事（不带内容 app，占槽位 1）。"""
    data = {"action": action}
    if kind == ActivityKind.INTERACT:
        data["app_id"] = None if presence else "content-app"
    return ActivityState(activity_id="active", agent_id=1, location_id="home", kind=kind,
        status=ActivityStatus.ACTIVE, started_at=100, planned_end_at=1000, last_settled_at=100,
        elapsed_seconds=0, planned_duration_seconds=900, resource_claims={slot}, origin_action_id="action",
        origin_decision_id="decision", origin_request_event_id="requested", data=data, name="Activity")


def occupied(activity: ActivityState) -> WorldState:
    """只有这一项活动的手工世界。"""
    state = world()
    state.activities[activity.activity_id] = activity
    return state


def _row(kind: str, object_id: str | None, action: dict) -> dict:
    """一条选中了动作行的私有绑定行（与观察的绑定行同形）。"""
    return {"object": object_id, "kind": kind, "object_id": object_id, "actions": [action], "selected_action": action}


def travel_call(method: str = "walking") -> Call:
    """去 Store 的出行调用。"""
    action = {**binding_action("travel", method), "travel_method_id": method}
    return Call(MAIN_TOOL, "travel", (_row("location", "store", action),))


def place_call() -> Call:
    """在 Home 做一件事的调用。"""
    action = {**binding_action("interact", "Rest"), "content_ids": ["rest"], "episode_titles": []}
    return Call(MAIN_TOOL, "interact", (_row("location", "home", action),))


def sleep_call() -> Call:
    """睡 450 分钟的调用。"""
    return Call(MAIN_TOOL, "sleep", (sleep_row(450),))


def purchase_call(*goods: str) -> Call:
    """一条购买调用，每份商品一行。"""
    return Call(SIDE_TOOL, "purchase", tuple(_row("supply", name, binding_action("purchase")) for name in goods))


def app_call() -> Call:
    """用内容 app 的调用。"""
    return Call(SIDE_TOOL, "interact", (_row("app", "content-app", binding_action("interact")),))


def refusal(state: WorldState, calls: list[Call], names: dict | None = None) -> Blocked | None:
    """手工世界不带环境输入：只有排在对象条件之前的槽位与睡眠核验能给出结论。"""
    return group_refusal(Context(state, 1, {"home": "Home", "store": "Store"}, names or {}), calls)


def conflict(slot: str, step: str, name: str = "Activity") -> Refusal:
    """同槽位占用的拒绝：点名占用者，下一步按占用者能否暂停写。"""
    return Refusal("RESOURCE_CONFLICT", f"The {slot} activity slot is occupied by {name}.", f"{step} {name} first.",
                   ("resource_claims",))


def test_each_call_claims_the_slot_given_by_its_action_and_object():
    assert [call.slot for call in (travel_call(), place_call(), sleep_call())] == ["first", "first", "first"]
    assert [call.slot for call in (purchase_call("Bread"), app_call())] == ["second", "second"]
    assert purchase_call("Bread", "Coffee", "Bread").goods_ids == ("Bread", "Coffee", "Bread")


@pytest.mark.parametrize("call", [purchase_call("Bread"), purchase_call("Bread", "Coffee"), app_call()])
def test_running_purchase_blocks_every_second_slot_start(call):
    state = occupied(running("purchase", ActivityKind.PURCHASE, "second"))
    assert refusal(state, [call]) == Blocked(conflict("second", "Conclude"), (SIDE_TOOL,))


@pytest.mark.parametrize("call", [purchase_call("Bread"), app_call()])
def test_running_app_interaction_blocks_every_second_slot_start_and_can_be_paused(call):
    state = occupied(running("interact", ActivityKind.INTERACT, "second"))
    assert refusal(state, [call]) == Blocked(conflict("second", "Conclude or pause"), (SIDE_TOOL,))


@pytest.mark.parametrize("call", [travel_call("walking"), travel_call("driving"), travel_call("taxi"),
                                  travel_call("bus"), place_call(), sleep_call()])
def test_running_location_interaction_blocks_every_first_slot_start(call):
    """在地点做事占槽位 1：出行、睡眠和另一件在地点做的事都按同槽位占用拒绝，不再有「出发前先结束」的说法。"""
    state = occupied(running("interact", ActivityKind.INTERACT, "first", presence=True))
    assert refusal(state, [call]) == Blocked(conflict("first", "Conclude or pause"), (MAIN_TOOL,))
    named = refusal(state, [call], {"active": "进行测试休息（1月5日 08:00 起）"})
    assert named == Blocked(conflict("first", "Conclude or pause", "进行测试休息（1月5日 08:00 起）"), (MAIN_TOOL,))


def test_running_travel_blocks_a_first_slot_start_and_cannot_be_paused():
    state = occupied(running("travel", ActivityKind.TRAVEL, "first"))
    assert refusal(state, [place_call()]) == Blocked(conflict("first", "Conclude"), (MAIN_TOOL,))


@pytest.mark.parametrize("reverse", [False, True])
def test_occupied_slot_names_only_the_responsible_call_of_a_group(reverse):
    """一组两条调用里只有占了槽位的那条负责；两个槽位都被占时先报主调用。"""
    calls = [travel_call(), app_call()]
    calls = list(reversed(calls)) if reverse else calls
    second = occupied(running("interact", ActivityKind.INTERACT, "second"))
    assert refusal(second, calls) == Blocked(conflict("second", "Conclude or pause"), (SIDE_TOOL,))
    first = occupied(running("interact", ActivityKind.INTERACT, "first", presence=True))
    assert refusal(first, calls) == Blocked(conflict("first", "Conclude or pause"), (MAIN_TOOL,))
    both = occupied(running("interact", ActivityKind.INTERACT, "first", presence=True))
    both.activities["other"] = running("purchase", ActivityKind.PURCHASE, "second").model_copy(
        update={"activity_id": "other", "name": "Other"})
    assert refusal(both, calls) == Blocked(conflict("first", "Conclude or pause"), (MAIN_TOOL,))


@pytest.mark.parametrize("side", [app_call(), purchase_call("Bread")])
@pytest.mark.parametrize("reverse", [False, True])
def test_sleep_and_second_slot_are_rejected_in_any_call_order(side, reverse):
    calls = [sleep_call(), side]
    rejected = refusal(world(), list(reversed(calls)) if reverse else calls)
    assert rejected == Blocked(Refusal("RESOURCE_CONFLICT", _SLEEP_REASON,
                                       fix="Submit start_main_activity alone to sleep."), (MAIN_TOOL, SIDE_TOOL))


@pytest.mark.parametrize("action,kind,step", [("interact", ActivityKind.INTERACT, "Conclude or pause"),
                                              ("purchase", ActivityKind.PURCHASE, "Conclude")])
def test_sleep_requires_an_empty_second_slot(action, kind, step):
    """槽位 2 被占时去睡觉：下一步按占用者能否暂停写，用内容 app 写结束或暂停，购买只写结束。"""
    state = occupied(running(action, kind, "second"))
    assert refusal(state, [sleep_call()]) == Blocked(
        Refusal("RESOURCE_CONFLICT", _SLEEP_REASON, f"{step} the second activity first."), (MAIN_TOOL,))


@pytest.mark.parametrize("call", [app_call(), purchase_call("Bread")])
def test_sleep_blocks_second_slot_start_until_its_own_end(call):
    state = occupied(running("sleep", ActivityKind.SLEEP, "first"))
    assert refusal(state, [call]) == Blocked(Refusal("RESOURCE_CONFLICT", _SLEEP_REASON), (SIDE_TOOL,))


def test_slot_occupancy_is_checked_before_the_sleep_constraint():
    """睡着时主调用撞上槽位 1：先按同槽位占用点名睡眠活动，不走睡眠约束那一句。"""
    state = occupied(running("sleep", ActivityKind.SLEEP, "first"))
    assert refusal(state, [travel_call(), app_call()]) == Blocked(conflict("first", "Conclude"), (MAIN_TOOL,))


def at_store(persona, config):
    """人物从四季便利开始：这里的购买是到店购买。"""
    persona["initial_location"] = "四季便利"


def observed_call(observed: dict, request) -> Call:
    """经宿主与环境共用的名称解析，把一条请求换成冻结观察里的绑定行。"""
    tool = tool_of(request)
    outcome = resolve_start(name_book(observed), tool, request)
    assert not outcome.failures and all(row["object_id"] is not None for row in outcome.rows)
    return Call(tool, request.action, outcome.rows)


def engine_refusal(engine, observed: dict, calls: list[Call]) -> Blocked | None:
    """按引擎受理时的同一份上下文（真实世界、地点名、本轮活动名与环境输入）核验一组调用。"""
    names = {row["activity_id"]: row["name"] for row in observed["activity_bindings"]}
    current = Context(engine.context.state, 1, place_names(engine.context.catalog), names, engine.context.inputs)
    return group_refusal(current, calls)


@pytest.mark.parametrize("method", ["步行", "驾车", "打车", "公共交通"])
def test_app_interaction_and_each_travel_can_share_distinct_slots(engine_factory, method):
    engine = engine_factory()
    _, watching = act(engine, "watch", use_app("哔哩哔哩"))
    assert watching.status == "in_progress"
    assert engine.context.state.activities[watching.activity_id].resource_claims == {"second"}
    observed = engine.observe(context(engine, "leave"))
    call = observed_call(observed, travel("公司", method))
    assert call.slot == "first"
    assert engine_refusal(engine, observed, [call]) is None


def test_several_goods_in_one_purchase_call_share_the_second_slot(engine_factory):
    engine = engine_factory(mutate=at_store)
    observed = engine.observe(context(engine, "basket"))
    call = observed_call(observed, purchase("柠檬茶", "纸巾", "柠檬茶"))
    assert call.slot == "second" and call.goods_ids == ("lemon-tea", "tissue", "lemon-tea")
    assert engine_refusal(engine, observed, [call]) is None


def test_only_an_in_store_purchase_blocks_departure(engine_factory):
    """出发前要先结束的只剩进行中的到店购买：在地点做事按槽位冲突，用内容 app 可以带着走。"""
    engine = engine_factory(mutate=at_store)
    _, buying = act(engine, "buy", purchase("柠檬茶"))
    assert buying.status == "in_progress"
    name = engine.context.state.activities[buying.activity_id].name
    observed = engine.observe(context(engine, "leave"))
    assert engine_refusal(engine, observed, [observed_call(observed, travel("公司"))]) == Blocked(
        Refusal("ACTIVITY_PRECONDITION_UNMET", f"Conclude {name} before departure.", f"Conclude {name}.",
                ("activities_before_departure",)), (MAIN_TOOL,))


@pytest.mark.parametrize("reverse", [False, True])
def test_in_store_purchase_cannot_start_in_the_same_group_as_departure(engine_factory, reverse):
    engine = engine_factory(mutate=at_store)
    observed = engine.observe(context(engine, "group"))
    calls = [observed_call(observed, travel("公司")), observed_call(observed, purchase("柠檬茶"))]
    assert engine_refusal(engine, observed, list(reversed(calls)) if reverse else calls) == Blocked(
        Refusal("ACTIVITY_PRECONDITION_UNMET", "A local purchase cannot start with departure.",
                "Complete or conclude the local purchase before leaving."), (SIDE_TOOL,))


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
