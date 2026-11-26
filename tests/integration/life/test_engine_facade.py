"""@brief 引擎门面 LifeEngine 的证据：唤醒与只读查询在出口深拷贝、交给沙盒的对象立即副本化并随屏障落库、
提交回执、屏障协议类型与检查点人物集合核对（docs/design/10-runtime.md）。"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tests.integration.life.test_opslog import _records
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, action_identity, do_at, stage_day_plan, stub_day_session, use_app
from tests.life.environment_runtime import action_result, events
from worldmock.foundation.errors import IntegrityError, ProtocolError
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.simulation_engine.observation.layers.observe import ObservationBuilder
from worldmock.state.decisions.barrier import AgentWakeupInput
from worldmock.state.decisions.round import SchedulingState
from worldmock.state.interaction.requests import RecallTaskSummaryRequest, ReviseDayPlanRequest, SearchMemoryRequest
from worldmock.state.model.exchange import ModelCallEvidence
from worldmock.state.planning.schedule import PlanGoal


def _containers(tree) -> set[int]:
    """树里每个 dict 与 list 的 id()（含根）；别的值不计。"""
    if isinstance(tree, dict):
        children = list(tree.values())
    elif isinstance(tree, list):
        children = tree
    else:
        return set()
    found = {id(tree)}
    for child in children:
        found |= _containers(child)
    return found


def _scramble(tree) -> None:
    """先递归改乱每个子容器，再清空树里的这个 dict 或 list；别的值不动。"""
    if isinstance(tree, (dict, list)):
        for child in list(tree.values() if isinstance(tree, dict) else tree):
            _scramble(child)
        tree.clear()


def _goal(engine: LifeEngine) -> None:
    """预置一条当日目标「歇一歇」，回看任务才有可回看的目标。"""
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="休息", goal="歇一歇",
                                           registered_at=engine.context.state.current_time),))


def _committed_barrier_detail(engine: LifeEngine) -> str:
    """最后一条 BARRIER_COMMITTED 运维记录的说明。"""
    records = [record for record in _records(engine) if record["code"] == "BARRIER_COMMITTED"]
    return records[-1]["detail"]


def test_prepare_barrier_returns_a_deep_copy(engine_factory, monkeypatch):
    """prepare_barrier 返回的观察字典与引擎内部构造结果内容相等却不共享任何 dict 或 list。"""
    engine = engine_factory()
    captured = []
    original = ObservationBuilder._observation_bundle

    def capture(builder, agent, state, loaded_target_keys=()):
        """记下引擎内部构造的观察字典。"""
        data = original(builder, agent, state, loaded_target_keys)
        captured.append(data)
        return data

    monkeypatch.setattr(ObservationBuilder, "_observation_bundle", capture)
    [wakeup] = engine.prepare_barrier((1,))
    returned = wakeup.observation
    [built] = captured
    assert {k: v for k, v in returned.items() if k != "observation_event_id"} == built
    assert not _containers(returned) & _containers(built)


def test_mutating_the_returned_observation_changes_nothing_persisted(engine_factory):
    """人物改乱准备好的观察后：世界状态与沙盒观察照旧完整、回看任务不变、提交通道正常落库。"""
    engine = engine_factory()
    _goal(engine)
    [wakeup] = engine.prepare_barrier((1,))
    pristine = copy.deepcopy(wakeup.observation)
    sandbox = engine.open_sandbox(wakeup)
    world = engine.context.state.model_dump_json()
    _scramble(wakeup.observation)
    assert engine.context.state.model_dump_json() == world
    request = RecallTaskSummaryRequest(task_name=pristine["goal_bindings"][0]["task_name"])
    assert sandbox.recall_task_summary(request) == engine.read_task_summary(1, request, pristine)
    aid = action_identity("facade-mutate")
    assert sandbox.dispatch_world_tool(MAIN_TOOL, do_at("家", "测试休息"), aid).accepted
    receipt = engine.commit_barrier([sandbox.finish_wakeup(None, stub_day_session(wakeup))])
    assert receipt.committed_agent_ids == (1,) and action_result(engine, aid).error_code is None
    stored = engine.context.store.read_event(pristine["observation_event_id"])
    assert stored.data["tool_layer"] == pristine["tool_layer"]


def test_read_task_summary_returns_a_deep_copy(engine_factory, monkeypatch):
    """只读回看的返回值与内部构造结果逐值相等、键序相同，却不共享任何 dict 或 list。"""
    engine = engine_factory()
    _goal(engine)
    [wakeup] = engine.prepare_barrier((1,))
    captured = []
    original = engine.actions.read_task_summary

    def capture(agent_id, request, observation):
        """记下动作处理给出的回看回答。"""
        answer = original(agent_id, request, observation)
        captured.append(answer)
        return answer

    monkeypatch.setattr(engine.actions, "read_task_summary", capture)
    request = RecallTaskSummaryRequest(task_name=wakeup.observation["goal_bindings"][0]["task_name"])
    returned = engine.read_task_summary(1, request, wakeup.observation)
    [built] = captured
    assert returned == built and list(returned) == list(built)
    assert json.dumps(returned, ensure_ascii=False) == json.dumps(built, ensure_ascii=False)
    assert not _containers(returned) & _containers(built)


def test_read_only_memory_queries_repeat_after_the_caller_mutates_them(engine_factory):
    """检索记忆与回看任务各问一次、改乱返回值之后再问：回答与改动之前的副本相同。"""
    engine = engine_factory()
    _goal(engine)
    [wakeup] = engine.prepare_barrier((1,))
    mem_req = SearchMemoryRequest(category="饮食", time="今天", keyword=None)
    ans_mem = engine.search_memory(1, mem_req)
    saved_mem = copy.deepcopy(ans_mem)
    _scramble(ans_mem)
    assert engine.search_memory(1, mem_req) == saved_mem

    rec_req = RecallTaskSummaryRequest(task_name=wakeup.observation["goal_bindings"][0]["task_name"])
    ans_rec = engine.read_task_summary(1, rec_req, wakeup.observation)
    saved_rec = copy.deepcopy(ans_rec)
    _scramble(ans_rec)
    assert engine.read_task_summary(1, rec_req, wakeup.observation) == saved_rec


def test_mutating_submitted_requests_changes_nothing_persisted(engine_factory):
    """人物改动已经交给沙盒的模型请求字典与请求模型之后：落库的模型请求与计划仍是提交时原本的副本。"""
    engine = engine_factory()
    stage_day_plan(engine)
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    request = {"model": "test-model", "messages": [{"role": "user", "content": "看一下现在。"}]}
    saved = copy.deepcopy(request)
    call = ModelCallEvidence(
        wakeup_id=wakeup.wakeup_id, agent_id=1, call_id="facade-call", phase="request",
        attempt=1, request=request, response=None, error=None, transport_attempts=(),
    )
    sandbox.record_model_exchange(call)
    request["messages"].append({"role": "user", "content": "再看一下。"})
    request["messages"][0]["content"] = "改过的内容。"
    request["model"] = "another-model"
    sandbox.record_model_exchange(replace(call, phase="response", request=saved, response={
        "choices": [{"message": {"role": "assistant", "content": "好。"}}]}))
    plan = ReviseDayPlanRequest.model_validate(
        {"goals": [{"intent_kind": "休息", "goal": "下午喝杯咖啡", "remind_time": None, "around_time": None}]})
    aid = action_identity("facade-submit")
    sandbox.dispatch_world_tool("revise_day_plan", plan, aid)
    plan.goals.clear()
    engine.commit_barrier([sandbox.finish_wakeup(None, stub_day_session(wakeup))])
    [requested] = events(engine, "model_request")
    [responded] = events(engine, "model_response")
    assert requested.data["request"] == saved and responded.caused_by == [requested.event_id]
    assert "下午喝杯咖啡" in [goal.goal for goal in engine.context.state.agents[1].day_plan.goals]


def test_commit_returns_receipt_and_persists_both_results_of_start_group(engine_factory):
    """一次唤醒在沙盒内派发两条开始调用并提交：屏障回执、落库事件与 BARRIER_COMMITTED 运维日志完整对齐。"""
    engine = engine_factory()
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    side_id, main_id = action_identity("facade-group", 1), action_identity("facade-group", 2)
    sandbox.dispatch_world_tool(SIDE_TOOL, use_app("哔哩哔哩"), side_id)
    sandbox.dispatch_world_tool(MAIN_TOOL, do_at("家", "测试闲聊"), main_id)
    receipt = engine.commit_barrier([sandbox.finish_wakeup(None, stub_day_session(wakeup))])
    assert receipt.committed_agent_ids == (1,) and receipt.conflict_requeued_agent_ids == ()
    assert [action_result(engine, identity).action_id for identity in (side_id, main_id)] == [side_id, main_id]
    assert _committed_barrier_detail(engine) == "Barrier committed agents=[1] conflicts=[]"


def test_engine_uses_the_worldmock_barrier_types(engine_factory):
    """引擎调度状态是 SchedulingState、唤醒快照是 AgentWakeupInput；过期时刻与不合法版本均被协议故障拒绝。"""
    engine = engine_factory()
    assert type(engine.scheduling_state()) is SchedulingState
    [wakeup] = engine.prepare_barrier((1,))
    assert type(wakeup) is AgentWakeupInput
    with pytest.raises(ProtocolError, match="^Wakeup world state version mismatch$"):
        engine.open_sandbox(wakeup.model_copy(update={"world_state_version": wakeup.world_state_version + 1}))
    with pytest.raises(ProtocolError, match="^Wakeup time mismatch$"):
        engine.open_sandbox(wakeup.model_copy(update={"observed_at": wakeup.observed_at + 1}))
    wrong = datetime.fromtimestamp(engine.context.state.current_time, ZoneInfo(engine.context.config.timezone))
    with pytest.raises(ProtocolError, match="^World time cannot advance while an agent decision is due$"):
        engine.advance(1, wrong + timedelta(seconds=1))
    sandbox = engine.open_sandbox(wakeup)
    with pytest.raises(ProtocolError, match="^Unsupported sandbox world tool: start_activity$"):
        sandbox.dispatch_world_tool("start_activity", SimpleNamespace(), "bad-1")
    assert sandbox.dispatch_world_tool(MAIN_TOOL, do_at("家", "测试休息"), action_identity("facade-boundary")).accepted
    engine.commit_barrier([sandbox.finish_wakeup(None, stub_day_session(wakeup))])
    with pytest.raises(ProtocolError, match="^World advance must match the next scheduling boundary$"):
        engine.advance(1, wrong + timedelta(seconds=1))


def test_checkpoint_rejects_an_incomplete_agent_snapshot_set(engine_factory):
    """直接驱动引擎：人物快照集合与清单人物集合不相等的共同检查点按完整性错误拒绝——空集合不行，多出一个清单外人物的
    集合也不行——都不写任何事件。"""
    engine = engine_factory()
    store = engine.context.store
    assert {f"agent:{agent_id}" for agent_id in store.manifest["agent_ids"]} == {"agent:1"}
    reference = store.assets.put_json({"probe": 1}, {"kind": "probe"})
    before = store.last_event_seq
    for snapshots in ({}, {"agent:1": reference, "agent:2": reference}):
        with pytest.raises(IntegrityError, match="^Checkpoint agent snapshot set is incomplete$"):
            engine.checkpoint(snapshots)
        assert store.last_event_seq == before, sorted(snapshots)
