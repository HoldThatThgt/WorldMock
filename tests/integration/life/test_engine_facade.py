"""@brief 引擎门面 LifeEngine 的证据：观察与只读查询在出口深拷贝、交给引擎的对象在调用之内转成副本或落库、提交返回本轮全部结果并如实计数、决策轮类型与检查点人物集合核对（docs/design/10-runtime.md）。"""

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.integration.life.test_opslog import _records
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, action_identity, context, do_at, stage_day_plan, use_app
from tests.life.environment_runtime import action_result, events
from worldmock.foundation.errors import IntegrityError, ProtocolError
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.simulation_engine.observation.layers.observe import ObservationBuilder
from worldmock.state.decisions.round import DecisionContext, DirectToolCall, SchedulingState
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


def _open(engine: LifeEngine, label: str) -> tuple[DecisionContext, dict]:
    """按标签开轮并做一次真实观察，返回决策上下文与观察。"""
    decision = context(engine, label)
    engine.begin_decision_round([decision])
    return decision, engine.observe(decision)


def _committed_detail(engine: LifeEngine, round_id: str) -> str:
    """这一轮唯一那条 DECISION_ROUND_COMMITTED 运维记录的说明。"""
    [record] = [record for record in _records(engine)
                if record["code"] == "DECISION_ROUND_COMMITTED" and record.get("round_id") == round_id]
    return record["detail"]


def test_observe_returns_a_deep_copy(engine_factory, monkeypatch):
    """观察的返回值与引擎内部构造的观察字典逐值相等、键序相同，却不共享任何 dict 或 list。"""
    engine = engine_factory()
    captured = []
    original = ObservationBuilder._observation_data

    def capture(builder, agent):
        """记下引擎内部构造的观察字典。"""
        data = original(builder, agent)
        captured.append(data)
        return data

    monkeypatch.setattr(ObservationBuilder, "_observation_data", capture)
    _, returned = _open(engine, "facade-observe-copy")
    [built] = captured
    assert returned == built and list(returned) == list(built)
    assert not _containers(returned) & _containers(built)


def test_mutating_the_returned_observation_changes_nothing_persisted(engine_factory):
    """人物改乱观察的返回值之后：世界状态与已落库的观察事件逐字节不变，回看任务的回答与改动之前相同，这次决策照常登记
    与结算，观察事件仍不变。"""
    engine = engine_factory()
    _goal(engine)
    decision, observed = _open(engine, "facade-mutate")
    store = engine.context.store
    pristine = copy.deepcopy(observed)
    stored = store.read_event(observed["observation_event_id"]).model_dump_json()
    world = engine.context.state.model_dump_json()
    _scramble(observed)
    assert store.read_event(pristine["observation_event_id"]).model_dump_json() == stored
    assert engine.context.state.model_dump_json() == world
    request = RecallTaskSummaryRequest(task_name=pristine["goal_bindings"][0]["task_name"])
    assert engine.read_query(decision, "recall_task_summary", request) == engine.read_task_summary(1, request, pristine)
    engine.submit_action(DirectToolCall(decision, action_identity("facade-mutate"), MAIN_TOOL, do_at("家", "测试休息")))
    [result] = engine.commit_decision_round("facade-mutate")
    assert result.error_code is None
    assert store.read_event(pristine["observation_event_id"]).model_dump_json() == stored


def test_read_query_returns_a_deep_copy(engine_factory, monkeypatch):
    """只读查询的返回值与动作处理给出的回答逐值相等、键序相同，却不共享任何 dict 或 list。"""
    engine = engine_factory()
    _goal(engine)
    decision, observed = _open(engine, "facade-query-copy")
    captured = []
    original = engine.actions.read_task_summary

    def capture(agent_id, request, observation):
        """记下动作处理给出的回看回答。"""
        answer = original(agent_id, request, observation)
        captured.append(answer)
        return answer

    monkeypatch.setattr(engine.actions, "read_task_summary", capture)
    request = RecallTaskSummaryRequest(task_name=observed["goal_bindings"][0]["task_name"])
    returned = engine.read_query(decision, "recall_task_summary", request)
    [built] = captured
    assert returned == built and list(returned) == list(built)
    assert json.dumps(returned, ensure_ascii=False) == json.dumps(built, ensure_ascii=False)
    assert not _containers(returned) & _containers(built)


def test_read_query_answers_repeat_after_the_caller_mutates_them(engine_factory):
    """检索记忆与回看任务各问一次、改乱返回值之后再问：回答与改动之前的副本相同。"""
    engine = engine_factory()
    _goal(engine)
    decision, observed = _open(engine, "facade-query-repeat")
    queries = (("search_memory", SearchMemoryRequest(category="饮食", time="今天", keyword=None)),
               ("recall_task_summary", RecallTaskSummaryRequest(task_name=observed["goal_bindings"][0]["task_name"])))
    for tool, request in queries:
        answer = engine.read_query(decision, tool, request)
        saved = copy.deepcopy(answer)
        _scramble(answer)
        assert engine.read_query(decision, tool, request) == saved, tool


def test_mutating_submitted_requests_changes_nothing_persisted(engine_factory):
    """人物改动已经交给引擎的模型请求字典与请求模型之后：已落库的模型请求与行动请求逐字节不变；带着调用前副本的响应
    证据照常登记、原因指向那条请求；开放轮里登记的请求与提交后的计划都是交给引擎时的内容。"""
    engine = engine_factory()
    stage_day_plan(engine)
    decision, _ = _open(engine, "facade-submit")
    request = {"model": "test-model", "messages": [{"role": "user", "content": "看一下现在。"}]}
    saved = copy.deepcopy(request)
    call = ModelCallEvidence(context=decision, call_id="facade-call", phase="request", attempt=1,
                             request=request, response=None, error=None, transport_attempts=())
    engine.record_model_exchange(call)
    [requested] = events(engine, "model_request")
    stored = requested.model_dump_json()
    request["messages"].append({"role": "user", "content": "再看一下。"})
    request["messages"][0]["content"] = "改过的内容。"
    request["model"] = "another-model"
    assert [event.model_dump_json() for event in events(engine, "model_request")] == [stored]
    engine.record_model_exchange(replace(call, phase="response", request=saved, response={
        "choices": [{"message": {"role": "assistant", "content": "好。"}}]}))
    [responded] = events(engine, "model_response")
    assert responded.caused_by == [requested.event_id]
    plan = ReviseDayPlanRequest.model_validate(
        {"goals": [{"intent_kind": "休息", "goal": "下午喝杯咖啡", "remind_time": None, "around_time": None}]})
    dumped = plan.model_dump(mode="json")
    engine.submit_action(DirectToolCall(decision, action_identity("facade-submit"), "revise_day_plan", plan))
    [registered] = events(engine, "action_requested")
    recorded = registered.model_dump_json()
    plan.goals.clear()
    assert [event.model_dump_json() for event in events(engine, "action_requested")] == [recorded]
    assert engine.decisions.round.pending_actions[-1].request == dumped
    engine.commit_decision_round("facade-submit")
    assert "下午喝杯咖啡" in [goal.goal for goal in engine.context.state.agents[1].day_plan.goals]


def test_commit_returns_both_results_of_a_start_group_and_counts_two_actions(engine_factory):
    """一个决策登记开始组的两条动作：提交按登记次序返回两条结果，各自等于落库结果事件重建的结果，运维日志计 2 条动作；
    只登记一条动作的一轮计 1 条。"""
    engine = engine_factory()
    decision, _ = _open(engine, "facade-group")
    side_id, main_id = action_identity("facade-group", 1), action_identity("facade-group", 2)
    engine.submit_action(DirectToolCall(decision, side_id, SIDE_TOOL, use_app("哔哩哔哩")))
    engine.submit_action(DirectToolCall(decision, main_id, MAIN_TOOL, do_at("家", "测试闲聊")))
    returned = engine.commit_decision_round("facade-group")
    assert [result.action_id for result in returned] == [side_id, main_id]
    assert [result.model_dump(mode="json") for result in returned] == [
        action_result(engine, identity).model_dump(mode="json") for identity in (side_id, main_id)]
    assert _committed_detail(engine, "facade-group") == "Decision round committed 2 actions"
    single = engine_factory()
    decision, _ = _open(single, "facade-single")
    single.submit_action(DirectToolCall(decision, action_identity("facade-single"), MAIN_TOOL, do_at("家", "测试休息")))
    single.commit_decision_round("facade-single")
    assert _committed_detail(single, "facade-single") == "Decision round committed 1 actions"


def test_engine_uses_the_worldmock_round_types(engine_factory):
    """引擎的调度状态是 WorldMock 自己的 SchedulingState；不是 DecisionContext 的上下文与不是 DirectToolCall 的调用
    都按协议错误拒绝，原文不再点名已删除的框架。"""
    engine = engine_factory()
    assert type(engine.scheduling_state()) is SchedulingState
    with pytest.raises(ProtocolError, match="^Context must use the DecisionContext type$"):
        engine.observe(SimpleNamespace(round_id="r"))
    with pytest.raises(ProtocolError, match="^Direct tool call requires an open decision round$"):
        engine.submit_action(SimpleNamespace(tool_name=MAIN_TOOL))


def test_checkpoint_rejects_an_incomplete_agent_snapshot_set(engine_factory):
    """直接驱动引擎：人物快照集合不全的共同检查点按完整性错误拒绝，不写任何事件。"""
    engine = engine_factory()
    before = engine.context.store.last_event_seq
    with pytest.raises(IntegrityError, match="^Checkpoint agent snapshot set is incomplete$"):
        engine.checkpoint({})
    assert engine.context.store.last_event_seq == before
