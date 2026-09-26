"""冻结观察中的可读对象、逐项动作和生活工具闭集。"""

from __future__ import annotations

import os
from copy import deepcopy

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from tests.integration.test_life_agent_protocol import (
    _Environment, _WORK, _context, _observations, _with_activities,
)
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_PAUSED, PHASE_PENDING
from worldmock.life.models import REQUEST_MODELS, StartActivityRequest


def _data() -> dict:
    """以真实生活协议观察为基础，提供五类语义对象。"""
    data = deepcopy(_observations()[0]["data"])
    public = data["facts"]["interactive_objects"]
    public.extend([
        {"object": "内容：纪录片", "actions": ["观看"], "preconditions_met": True},
        {"object": "地点：公园", "actions": ["步行前往"], "preconditions_met": True},
        {"object": "人物：小李", "actions": ["交谈"], "preconditions_met": True},
    ])
    data["interaction_bindings"].extend([
        {"object": "内容：纪录片", "kind": "content", "object_id": "content-hidden",
         "actions": [{"label": "观看", "canonical_action": "watch"}]},
        {"object": "地点：公园", "kind": "location", "object_id": "park-hidden",
         "actions": [{"label": "步行前往", "canonical_action": "travel_to", "travel_mode": "walking"}]},
        {"object": "人物：小李", "kind": "person", "object_id": "agent-hidden",
         "actions": [{"label": "交谈", "canonical_action": "chat"}]},
    ])
    return data


def _prepare(data: dict | None = None) -> LifeAgent:
    """将本轮唯一观察冻结到实际 LifeAgent。"""
    agent = LifeAgent()
    observed = {"turn": 0, "action": "observe", "ok": True, "data": data or _data()}
    agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, [observed]))
    return agent


def _functions(agent: LifeAgent, readonly: bool = False) -> dict[str, dict]:
    """按名称读取人物本轮真实原生工具声明。"""
    return {item["function"]["name"]: item["function"]
            for item in agent.build_react_tool_schemas(readonly=readonly)}


def _decision(name: str, **arguments) -> ReactDecision:
    """构造一次原生工具选择。"""
    return ReactDecision("", name, arguments, "")


def test_start_schema_binds_readable_objects_and_actions_without_internal_id() -> None:
    """模型只选本轮对象和动作，报价与内部身份留在私有绑定。"""
    data = _data()
    functions = _functions(_prepare(data))
    start = functions["start_activity"]
    properties = start["parameters"]["properties"]
    assert start["strict"] is True
    assert start["parameters"]["additionalProperties"] is False
    assert set(properties) == {"targets", "actions"}
    assert properties["targets"]["items"]["enum"] == sorted(
        row["object"] for row in data["facts"]["interactive_objects"])
    assert set(properties["actions"]["items"]["enum"]) == {
        "等待", "睡觉", "工作", "观看", "步行前往", "交谈"}
    assert "task-visible" not in str(start)
    assert "park-hidden" not in str(start)


@pytest.mark.parametrize("targets,actions,error", [
    (["内容：纪录片"], ["步行前往"], "not offered"),
    (["content-hidden"], ["观看"], "not a visible"),
    (["内容：纪录片", "地点：公园"], ["观看"], "equal lengths"),
    (["内容：纪录片", "内容：纪录片"], ["观看", "观看"], "unique"),
    (["自己", "内容：纪录片", "地点：公园"], ["等待", "观看", "步行前往"], "one or two"),
])
def test_start_rejects_invalid_pairs_before_dispatch(targets, actions, error) -> None:
    """每项动作必须属于对应对象，且整个请求共享一个原子校验边界。"""
    agent = _prepare()
    actual = agent.validate_react_decisions([
        _decision("start_activity", targets=targets, actions=actions)], False)
    assert error in actual


def test_parallel_start_accepts_two_independent_human_readable_pairs() -> None:
    """步行和观看分别配对，不再以一个 action 覆盖双活动。"""
    agent = _prepare()
    choice = _decision("start_activity", targets=["地点：公园", "内容：纪录片"],
                       actions=["步行前往", "观看"])
    assert agent.validate_react_decisions([choice], False) == ""
    assert agent.validate_react_decisions([choice, choice], False) == (
        "one model response cannot contain multiple life actions")


def test_frozen_observation_does_not_change_until_next_round() -> None:
    """提示冻结后修改原字典不能补充对象；下一轮明确观察才能刷新。"""
    data = _data()
    agent = _prepare(data)
    data["facts"]["interactive_objects"].append(
        {"object": "内容：新电影", "actions": ["观看"], "preconditions_met": True})
    invalid = _decision("start_activity", targets=["内容：新电影"], actions=["观看"])
    assert "not a visible" in agent.validate_react_decisions([invalid], False)
    assert "内容：新电影" not in _functions(agent)["start_activity"]["parameters"]["properties"][
        "targets"]["items"]["enum"]
    refreshed = _prepare(data)
    assert refreshed.validate_react_decisions([invalid], False) == ""


def test_duplicate_readable_object_names_fail_closed() -> None:
    """同名对象必须在观察投影阶段消歧，人物工具不会猜内部身份。"""
    data = _data()
    data["facts"]["interactive_objects"].append(
        {"object": "内容：纪录片", "actions": ["观看"], "preconditions_met": True})
    with pytest.raises(RuntimeError, match="interactive object identity"):
        _prepare(data)


def test_menu_closes_actions_without_removing_stable_tools() -> None:
    """地点菜单只缩小动作短语，工作提交等工具仍依各自合同开放。"""
    data = _data()
    data["interaction_menu"] = {"allowed_actions": ["sleep", "wait"]}
    agent = _prepare(data)
    functions = _functions(agent)
    assert {"start_activity", "submit_work", "recall_task_summary"} <= set(functions)
    assert functions["start_activity"]["parameters"]["properties"]["actions"]["items"]["enum"] == [
        "睡觉", "等待"]
    assert "interaction menu" in agent.validate_react_decisions([
        _decision("start_activity", targets=["整理会议纪要"], actions=["工作"])], False)


def test_visible_task_and_person_names_bind_independent_tools() -> None:
    """工作成果和消息正文经独立工具提交，只接收可读任务或人物名。"""
    agent = _prepare()
    functions = _functions(agent)
    assert functions["submit_work"]["parameters"]["properties"]["task_name"]["enum"] == [
        "整理会议纪要"]
    assert functions["send_message"]["parameters"]["properties"]["recipient_name"]["enum"] == [
        "人物：小李"]
    assert "task-visible" not in str(functions["submit_work"])
    assert "agent-hidden" not in str(functions["send_message"])
    assert agent.validate_react_decisions([_decision(
        "send_message", recipient_name="人物：小李", text="你好")], False) == ""
    assert "not visible" in agent.validate_react_decisions([_decision(
        "send_message", recipient_name="agent-hidden", text="你好")], False)


@pytest.mark.parametrize("phase,expected", [
    (PHASE_ACTIVE, {"report_status", "pause_activity", "conclude_activity"}),
    (PHASE_PAUSED, {"report_status", "conclude_activity"}),
    (PHASE_PENDING, {"conclude_activity"}),
])
def test_activity_controls_bind_current_readable_activity(phase, expected) -> None:
    """活动控制逐项点名可读活动并写人物自述，活动阶段决定工具可见性：刚结束的活动只能收尾（第七章）。"""
    agent = _prepare(_with_activities(_data(), (_WORK, phase)))
    functions = _functions(agent)
    actual = set(functions) & {"report_status", "pause_activity", "conclude_activity"}
    assert actual == expected
    for name in actual:
        assert functions[name]["parameters"]["properties"]["task"]["items"]["enum"] == [_WORK]
        memories = {"memories": [[]]} if name == "conclude_activity" else {}
        assert agent.validate_react_decisions([_decision(
            name, task=[_WORK], summary=["继续推进。"], **memories)], False) == ""
    assert "activity-private-1" not in str(functions)
    assert "session-7-0001" not in str(functions)


@pytest.mark.asyncio
async def test_valid_start_dispatches_the_same_typed_request() -> None:
    """本地核对后的对象与动作原样进入宿主有类型请求。"""
    agent = _prepare()
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    payload = {"targets": ["地点：公园", "内容：纪录片"], "actions": ["步行前往", "观看"]}
    assert agent.validate_react_decisions([_decision("start_activity", **payload)], False) == ""
    result = await agent.dispatch_react_tool("start_activity", payload)
    assert result.ok is True
    assert len(environment.calls) == 1
    assert isinstance(environment.calls[0].request, StartActivityRequest)
    assert environment.calls[0].request.model_dump() == payload


def test_revision_follows_current_day_plan_without_plan_tool() -> None:
    """当日计划由零点后台任务生成，人物工具面没有 plan_day；当日已有计划（含空清单）时才开放修订，
    目标只收意图、正文与必填可空的 remind_time，不从模型索取内部计时参数（时间与能量规格第四章）。"""
    data = _data()
    unplanned = _functions(_prepare(data))
    assert "plan_day" not in unplanned and "revise_day_plan" not in unplanned
    data["facts"]["day_plan"] = {"goals": []}
    revising = _functions(_prepare(data))
    assert "revise_day_plan" in revising and "plan_day" not in revising
    parameters = revising["revise_day_plan"]["parameters"]
    assert set(parameters["properties"]) == {"goals"}
    assert set(parameters["properties"]["goals"]["items"]["properties"]) == {"intent_kind", "goal", "remind_time"}


def test_day_start_offers_the_normal_tools_and_readonly_keeps_its_gate() -> None:
    """运行开始与醒来的决策不再先登记计划：工具面与其他决策相同（第四章）；只读循环仅开放记忆与结束工具。"""
    ordinary = set(_functions(_prepare()))
    assert {"start_activity", "recall_task_summary", "submit_work", "send_message"} <= ordinary
    assert "plan_day" not in ordinary
    for reasons in (["run_start"], ["wake_from_sleep"], ["run_start", "wake_from_sleep"]):
        data = _data()
        data["wake_reasons"] = reasons
        assert set(_functions(_prepare(data))) == ordinary
        data["facts"]["day_plan"] = {"goals": []}
        assert set(_functions(_prepare(data))) == ordinary | {"revise_day_plan"}
    readonly = LifeAgent()
    readonly.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, [], "What happened?", True))
    assert set(_functions(readonly, readonly=True)).isdisjoint(REQUEST_MODELS)
