"""@brief search_world 活动生命周期：随机耗时、prefer 作用域、槽位约束、不可暂停、取消清除悬挂循环与唤醒恢复。"""

from __future__ import annotations

import copy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.integration.runtime.current_contract_fixtures import ScriptedModel, ToolResponse, person, resolved_binding
from tests.life.conftest import (
    SEARCH_TOOL,
    SIDE_TOOL,
    action_identity,
    advance_next,
    conclude_request,
    do_at,
    pause_request,
    search_world,
    sleep,
    stage_day_plan,
    travel,
    use_app,
)
from tests.life.current_world import stage_moment
from tests.life.environment_runtime import act
from worldmock.agents.life_agent import LifeAgent, person_inputs
from worldmock.agents.protocol.agent_loop import LoopToolCall, validate_single_tool_call
from worldmock.foundation.errors import BusinessRuleError, ProtocolError
from worldmock.foundation.randomness import random_stream
from worldmock.simulation_engine.actions.start_group import ToolInvocation
from worldmock.simulation_engine.activities.lifecycle import activity_control
from worldmock.simulation_engine.activities.search.search_activity import search_seconds
from worldmock.simulation_engine.observation.layers.result_projection import public_result
from worldmock.simulation_engine.observation.layers.search_projection import UNFINISHED, search_details
from worldmock.state.decisions.barrier import SearchSessionOutcome
from worldmock.state.interaction.requests import REQUEST_MODELS, SearchWorldRequest
from worldmock.state.world.kinds import ActivityKind, ActivityStatus

_ZONE = ZoneInfo("Asia/Shanghai")


def _attentive(persona, config):
    """每 5 分钟允许重估且第 1 秒起可播报，用于测试与 slot 1 并发及截断。"""
    del persona
    config["reconsideration_interval_seconds"] = 300
    config["progress_report_interval_seconds"] = 1


def test_search_duration_uses_seeded_stream_between_180_and_420(engine_factory):
    """search_world 耗时使用 search-duration:{action_id} 随机流在 [180, 420] 内确定抽取，不用写死 180 秒。"""
    engine = engine_factory()
    drawn = {search_seconds(engine.context.state.random_state, action_identity("probe", idx)) for idx in range(1, 30)}
    assert min(drawn) >= 180 and max(drawn) <= 420 and len(drawn) > 1
    aid = action_identity("timed")
    expected = random_stream(engine.context.state.random_state, f"search-duration:{aid}").randint(180, 420)
    _, started = act(engine, "timed", search_world("查饮品"))
    assert started.status == "in_progress", started.error_code
    activity = engine.context.state.activities[started.activity_id]
    assert activity.kind == ActivityKind.SEARCH and activity.status == ActivityStatus.ACTIVE
    assert activity.resource_claims == {"second"}
    assert activity.planned_duration_seconds == expected and 180 <= activity.planned_duration_seconds <= 420
    assert activity.planned_end_at == activity.started_at + expected
    assert search_details(activity.model_dump(mode="json"), engine.context.inputs) == {
        "app": None, "state": UNFINISHED,
    }
    second_run = engine_factory()
    _, repeat = act(second_run, "timed", search_world("查饮品"))
    assert second_run.context.state.activities[repeat.activity_id].planned_duration_seconds == expected


@pytest.mark.parametrize("prefer,expected_app,expected_scoped", [
    (None, None, False),
    ("饿了么", "eleme", True),
    ("饿 了 么", "eleme", True),
    ("美团外卖", None, False),
    ("哔哩哔哩", "bilibili", True),
    ("抖音", None, False),
])
def test_prefer_sets_data_app_id_only_when_order_app_matches(engine_factory, prefer, expected_app, expected_scoped):
    """data.app_id 只在 prefer 命中下单应用时填入 app_id，其余情况与未填保持 None。"""
    engine = engine_factory()
    _, started = act(engine, "scope", search_world("想喝柠檬茶", prefer=prefer))
    assert started.status == "in_progress", started.error_code
    activity = engine.context.state.activities[started.activity_id]
    assert activity.data["app_id"] == expected_app and activity.data["scoped"] is expected_scoped
    assert activity.data["prefer"] == prefer


def test_search_world_request_validation_and_schema_exposure(engine_factory):
    """search_world 对开放人物可见，且严格拒绝空白 query 或超长字符串。"""
    tools = [row["function"]["name"] for row in person().turn_tools(16, False)]
    assert SEARCH_TOOL in tools
    with pytest.raises(ValueError):
        search_world("   ")
    feedback = validate_single_tool_call(
        LoopToolCall(SEARCH_TOOL, {"query": "   ", "prefer": None}, "call-bad"), REQUEST_MODELS,
    )
    assert feedback is not None and feedback.report.error_code == "ARGUMENTS_INVALID"
    engine = engine_factory()
    bad = SearchWorldRequest.model_construct(query="", prefer=None)
    _, refused = act(engine, "bad-q", bad, SEARCH_TOOL)
    assert (refused.status, refused.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert not engine.context.state.activities


def test_search_slot_second_preconditions_and_concurrency_with_slot_first(engine_factory):
    """search_world 占第二槽位：可与第一槽位并行；在路上、睡着或第二槽位已占时被拒。"""
    # 与第一槽位并行
    first_busy = engine_factory(mutate=_attentive)
    _, rest = act(first_busy, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress"
    advance_next(first_busy)
    assert first_busy.scheduling_state().ready_agent_ids == (1,)
    _, search_ok = act(first_busy, "while-rest", search_world("找奶茶"))
    assert search_ok.status == "in_progress", search_ok.error_code
    assert first_busy.context.state.activities[search_ok.activity_id].resource_claims == {"second"}
    # 出行途中被拒
    road = engine_factory(mutate=_attentive)
    _, trip = act(road, "go", travel("公司", "步行"))
    assert trip.status == "in_progress"
    advance_next(road)
    _, road_refused = act(road, "search-road", search_world("查咖啡"))
    assert (road_refused.status, road_refused.error_code) == ("rejected", "ACTIVITY_PRECONDITION_UNMET")
    assert road_refused.message == "A search cannot start while travelling."
    # 睡着时被拒
    asleep = engine_factory(mutate=_attentive)
    act(asleep, "nap", sleep(60))
    obs = asleep.observations.build_wakeup_observation(asleep.context.state.agents[1])[0]
    with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
        inv = ToolInvocation(1, "w-sleep", action_identity("s"), SEARCH_TOOL,
                             search_world("梦中找").model_dump(mode="json"), "sha", "req-s", obs)
        from worldmock.simulation_engine.activities.search.search_activity import start_search
        start_search(asleep.context, asleep.context.state, inv)
    # 第二槽位被内容使用占用时被拒
    app_ready = engine_factory(mutate=_attentive)
    _, watching = act(app_ready, "watch", use_app("哔哩哔哩"))
    assert watching.status == "in_progress"
    advance_next(app_ready)
    _, blocked = act(app_ready, "blocked", search_world("买点啥"))
    assert (blocked.status, blocked.error_code) == ("rejected", "RESOURCE_CONFLICT")
    # 有活动待收尾时启动 search_world 被拒（ACTIVITY_CONCLUSION_REQUIRED）
    pending_end = engine_factory()
    _, fin = act(pending_end, "fin-rest", do_at("家", "测试休息"))
    while pending_end.context.state.activities[fin.activity_id].status == ActivityStatus.ACTIVE:
        advance_next(pending_end)
    assert pending_end.context.state.activities[fin.activity_id].status == ActivityStatus.COMPLETED
    _, concl_refused = act(pending_end, "search-while-pending", search_world("找零食"))
    assert (concl_refused.status, concl_refused.error_code) == ("rejected", "ACTIVITY_CONCLUSION_REQUIRED")


def test_active_search_cannot_be_paused_and_conclude_clears_suspended_loop(engine_factory):
    """进行中的搜索不可暂停（ACTIVITY_PAUSE_UNAVAILABLE）；被 conclude_activity 取消后清空 suspended_loop 并不启动子会话。"""
    engine = engine_factory()
    _, started = act(engine, "cancel-s", search_world("搜索零食"))
    act_id = started.activity_id
    state = engine.context.state
    search_act = state.activities[act_id]
    assert state.agents[1].suspended_loop is not None
    assert activity_control.pausable(state, search_act) is False
    observed = engine.observations.build_wakeup_observation(state.agents[1])[0]
    pause_inv = ToolInvocation(
        1, "w-pause", action_identity("pause"), "pause_activity",
        pause_request(search_act.name).model_dump(mode="json"), "sha-p", "req-p", observed,
    )
    with pytest.raises(BusinessRuleError, match="ACTIVITY_PAUSE_UNAVAILABLE"):
        engine.actions.dispatch(state, [pause_inv])
    conclude_inv = ToolInvocation(
        1, "w-conclude", action_identity("conclude"), "conclude_activity",
        conclude_request(search_act.name, summary="不等了").model_dump(mode="json"), "sha-c", "req-c", observed,
    )
    changed, results = engine.actions.dispatch(state, [conclude_inv])
    assert results[0].status == "completed"
    cancelled = changed.activities[act_id]
    assert cancelled.status == ActivityStatus.CANCELLED and cancelled.concluded_at == state.current_time
    assert changed.agents[1].suspended_loop is None
    assert activity_control.free_slots(activity_control.open_activities(changed, 1)) == 2
    engine.context.state = changed
    assert engine.due_searches() == ()


def test_run_end_truncates_active_search_and_clears_suspended_loop(engine_factory):
    """搜索跨过实验截止时间时在终点被截断、清除 suspended_loop，不触发任何子会话。"""
    from datetime import timedelta

    engine = engine_factory(mutate=lambda _p, c: c.__setitem__("stop_time", c["start_time"] + timedelta(hours=1)))
    stage_moment(engine, int(engine.context.state.end_time) - 60)
    _, started = act(engine, "short", search_world("跨结尾搜索"))
    assert engine.context.state.agents[1].suspended_loop is not None
    assert engine.scheduling_state().ready_agent_ids == ()
    advance_next(engine)
    assert engine.context.state.current_time == engine.context.state.end_time
    assert engine.due_searches() == ()
    truncated = engine.context.state.activities[started.activity_id]
    assert truncated.status == ActivityStatus.TRUNCATED and truncated.boundary_type == "experiment_end"
    assert engine.context.state.agents[1].suspended_loop is None


@pytest.mark.asyncio
async def test_search_suspends_and_resumes_same_agent_loop_with_tool_result(engine_factory):
    """完整 LifeAgent 循环遇 search_world 挂起并保存 pending_tool_call_id；到期交卷释放槽位，同唤醒恢复后继购买。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    frozen = engine.context.inputs
    agent = LifeAgent(person_inputs(frozen.persona_by_id[1]))

    # 第一段唤醒：先调 search_world 并挂起
    [wakeup] = engine.prepare_barrier((1,))
    obs_before = copy.deepcopy(wakeup.observation)
    sandbox1 = engine.open_sandbox(wakeup)
    model1 = ScriptedModel([
        ToolResponse(SEARCH_TOOL, {"query": "买杯柠檬茶", "prefer": "饿了么"}, call_id="call-search-1",
                     reasoning="先查一下哪里买。"),
    ])
    binding1 = resolved_binding(model1, frozen=frozen, stage="action", agent_id=1)
    outcome1 = await agent.decide_wakeup(wakeup, sandbox1, binding1)
    assert outcome1.status == "suspended"
    engine.commit_barrier([outcome1])

    loop = engine.context.state.agents[1].suspended_loop
    assert loop is not None and loop.wakeup_id == wakeup.wakeup_id
    assert loop.pending_tool_call_id == "call-search-1" and loop.turn_used == 1
    assert engine.due_searches() == ()
    assert engine.scheduling_state().ready_agent_ids == ()

    # 未交卷期间屏障与继续推进都被拒绝
    advance_next(engine)
    due = engine.due_searches()
    assert len(due) == 1
    task, _ = due[0]
    with pytest.raises(ProtocolError, match="Due searches must complete"):
        engine.prepare_barrier((1,))
    with pytest.raises(ProtocolError, match="due search is pending"):
        engine.advance(1, datetime.fromtimestamp(engine.context.state.current_time + 1, _ZONE))

    # 交卷并释放第二槽位
    receipt = engine.complete_searches([SearchSessionOutcome(
        action_id=task.action_id, agent_id=1, activity_id=task.activity_id,
        goods_ids=("lemon-tea",), place_ids=(), summary="饿了么可下单三杯咖啡柠檬茶",
    )])
    assert receipt.completed_count == 1
    done_act = engine.context.state.activities[task.activity_id]
    assert done_act.status == ActivityStatus.COMPLETED and done_act.ended_at == done_act.concluded_at
    assert engine.scheduling_state().ready_agent_ids == (1,)

    # 第二段：以同一 wakeup_id + resume_search 恢复原循环，注入工具结果并在次一回合下单购买
    [resumed] = engine.prepare_barrier((1,))
    assert resumed.mode == "resume_search" and resumed.wakeup_id == wakeup.wakeup_id
    assert resumed.search_result_payload is not None
    assert resumed.search_result_payload["search"]["goods_rows"][0]["product"] == "柠檬茶"
    assert resumed.observation["observation_event_id"] != obs_before["observation_event_id"]

    sandbox2 = engine.open_sandbox(resumed)
    model2 = ScriptedModel([
        ToolResponse(SIDE_TOOL, {"action": "purchase", "target": ["柠檬茶"], "prefer": "饿了么"},
                     call_id="call-buy-2", reasoning="现在直接下单。"),
    ])
    binding2 = resolved_binding(model2, frozen=frozen, stage="action", agent_id=1)
    outcome2 = await agent.decide_wakeup(resumed, sandbox2, binding2)
    assert outcome2.status == "completed"
    engine.commit_barrier([outcome2])

    # 验证恢复给模型发出的历史最后一条恰好是以 pending_tool_call_id 配对的搜索工具结果
    injected = model2.requests[0]["messages"][-1]
    assert len(model2.requests[0]["messages"]) == 4
    assert injected["role"] == "tool" and injected["tool_call_id"] == "call-search-1"
    assert "柠檬茶" in injected["content"]
    assert engine.context.state.agents[1].suspended_loop is None
    assert any(item.kind == ActivityKind.PURCHASE for item in engine.context.state.activities.values())
    assert engine.observations.build_wakeup_observation(engine.context.state.agents[1])[0]["todo_layer"] is not None
    assert engine.scheduling_state().next_event_at is not None
    assert public_result(
        {"occurred_at": done_act.ended_at, "status": "completed", "message": "ok",
         "data": {"activity": done_act.model_dump(mode="json")}},
        frozen, {},
    )["details"]["search"] == resumed.search_result_payload["search"]
