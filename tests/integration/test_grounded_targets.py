"""冻结观察中的可读对象、逐项的动词与方式和稳定的生活工具面（docs/dev/SPEC-persona-prompt.md 第一章「工具层」）。"""

from __future__ import annotations

import os
from copy import deepcopy

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from tests.integration.test_life_agent_protocol import (
    _Environment, _TASK, _WORK, _add_objects, _context, _observations, _offer, _visible_object, _with_activities,
)
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_PAUSED, PHASE_PENDING
from worldmock.life.models import REQUEST_MODELS, StartActivityRequest
from tests.life.conftest import start_arguments

_FILM = "内容：纪录片"
_PARK = "地点：公园"


def _data() -> dict:
    """以真实生活协议观察为基础，提供五类语义对象：自己、工作任务、内容、地点与同场人物。"""
    data = deepcopy(_observations()[0]["data"])
    return _add_objects(
        data,
        _visible_object(_FILM, "content", "content-hidden", _offer("interact", "接着看", "watch")),
        _visible_object(_PARK, "location", "park-hidden", _offer("travel", "步行", "travel_to", travel_mode="walking")),
        _visible_object("人物：小李", "person", "agent-hidden", _offer("interact", "交谈", "chat")))


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
    """构造一次原生工具选择（start_activity 未写的来源、去向与 answering 取缺省）。"""
    if name == "start_activity" and "to" not in arguments:
        arguments = start_arguments(**arguments)
    return ReactDecision("", name, arguments, "")


def test_start_schema_binds_readable_objects_and_actions_without_internal_id() -> None:
    """模型只填对象、动词和方式；可选的名称只在工具层，报价与内部身份留在私有绑定，Schema 不带逐轮枚举。"""
    data = _data()
    agent = _prepare(data)
    start = _functions(agent)["start_activity"]
    properties = start["parameters"]["properties"]
    assert start["strict"] is True
    assert start["parameters"]["additionalProperties"] is False
    assert set(properties) == {"targets", "actions", "ways", "from", "to", "answering"}
    assert all("enum" not in properties[name]["items"] for name in ("targets", "actions", "ways"))
    user = agent.build_react_messages(ReactLoopRequest(0, _context().observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": data}]))[1]["content"]
    assert all(f'"name":"{row["object"]}"' in user for row in data["interaction_bindings"])
    for private in ("task-visible", "park-hidden", "content-hidden", "travel_to", "walking"):
        assert private not in str(start) and private not in user


@pytest.mark.parametrize("targets,actions,ways,error", [
    ([_FILM], ["travel"], ["步行"], "not offered"),
    ([_FILM], ["interact"], ["观看"], "not offered"),
    ([_FILM], ["travel"], ["接着看"], "not offered"),
    (["content-hidden"], ["interact"], ["接着看"], "not a visible"),
    ([_FILM, _PARK], ["interact"], ["接着看"], "equal lengths"),
    ([_FILM, _PARK], ["interact", "travel"], ["接着看"], "equal lengths"),
    ([_FILM, _FILM], ["interact", "interact"], ["接着看", "接着看"], "repeat only with different to"),
    (["自己", _FILM, _PARK], ["interact", "interact", "travel"], ["等待", "接着看", "步行"], "one or two"),
])
def test_start_rejects_invalid_pairs_before_dispatch(targets, actions, ways, error) -> None:
    """每项动词与方式必须属于对应对象，三个数组等长，且整个请求共享一个原子校验边界。"""
    agent = _prepare()
    actual = agent.validate_react_decisions([
        _decision("start_activity", targets=targets, actions=actions, ways=ways)], False)
    assert error in actual


def test_parallel_start_accepts_two_independent_human_readable_pairs() -> None:
    """步行和接着看分别配对，不再以一个 action 覆盖双活动。"""
    agent = _prepare()
    choice = _decision("start_activity", targets=[_PARK, _FILM], actions=["travel", "interact"],
                       ways=["步行", "接着看"])
    assert agent.validate_react_decisions([choice], False) == ""
    assert agent.validate_react_decisions([choice, choice], False) == (
        "TOOL_CALL_REQUIRED: one model response cannot contain multiple life actions")


def test_frozen_observation_does_not_change_until_next_round() -> None:
    """提示冻结后修改原字典不能补充对象；下一轮明确观察才能刷新。"""
    data = _data()
    agent = _prepare(data)
    _add_objects(data, _visible_object("内容：新电影", "content", "content-new",
                                       _offer("interact", "接着看", "watch")))
    invalid = _decision("start_activity", targets=["内容：新电影"], actions=["interact"], ways=["接着看"])
    assert "not a visible" in agent.validate_react_decisions([invalid], False)
    refreshed = _prepare(data)
    assert refreshed.validate_react_decisions([invalid], False) == ""


def test_duplicate_readable_object_names_fail_closed() -> None:
    """同名对象必须在观察投影阶段消歧，人物工具不会猜内部身份。"""
    data = _data()
    data["interaction_bindings"].append(deepcopy(data["interaction_bindings"][-1]))
    with pytest.raises(RuntimeError, match="interaction binding identity"):
        _prepare(data)


def test_menu_closes_actions_without_removing_stable_tools() -> None:
    """地点菜单按映射后的内部动作判定所选各项；工具面整次运行不变，菜单外的动作在本地按协议错误纠正。"""
    data = _data()
    data["interaction_menu"] = {"allowed_actions": ["sleep", "wait"]}
    agent = _prepare(data)
    assert _functions(agent) == _functions(_prepare())
    assert agent.validate_react_decisions([
        _decision("start_activity", targets=["自己"], actions=["sleep"], ways=["睡觉"])], False) == ""
    for target, verb, way in ((_TASK, "interact", "工作"), (_PARK, "travel", "步行")):
        assert "interaction menu" in agent.validate_react_decisions([
            _decision("start_activity", targets=[target], actions=[verb], ways=[way])], False)


def test_visible_task_and_person_names_bind_independent_tools() -> None:
    """工作成果和消息正文经独立工具提交，只接收私有绑定里的可读任务或人物名。"""
    agent = _prepare()
    functions = _functions(agent)
    assert "enum" not in functions["submit_work"]["parameters"]["properties"]["task_name"]
    assert "enum" not in functions["send_message"]["parameters"]["properties"]["recipient_name"]
    assert "task-visible" not in str(functions["submit_work"])
    assert "agent-hidden" not in str(functions["send_message"])
    assert agent.validate_react_decisions([_decision(
        "send_message", recipient_name="人物：小李", text="你好")], False) == ""
    assert "not visible" in agent.validate_react_decisions([_decision(
        "send_message", recipient_name="agent-hidden", text="你好")], False)
    assert "not visible" in agent.validate_react_decisions([_decision(
        "send_message", recipient_name=_FILM, text="你好")], False)


@pytest.mark.parametrize("phase", [PHASE_ACTIVE, PHASE_PAUSED, PHASE_PENDING])
def test_activity_controls_bind_current_readable_activity(phase) -> None:
    """三个控制工具整次运行都在，逐项点名可读活动并写人物自述；宿主只核对名称，阶段与可用性由环境判定。"""
    agent = _prepare(_with_activities(_data(), (_WORK, phase)))
    functions = _functions(agent)
    assert {"report_status", "pause_activity", "conclude_activity"} <= set(functions)
    for name in ("report_status", "pause_activity", "conclude_activity"):
        assert "enum" not in functions[name]["parameters"]["properties"]["items"]["items"]["properties"][
            "activity_name"]
        extra = {"memories": [], "views": []} if name == "conclude_activity" else {}
        assert agent.validate_react_decisions([_decision(
            name, items=[{"activity_name": _WORK, "summary": "继续推进。", **extra}])], False) == ""
    assert "activity-private-1" not in str(functions)
    assert "session-7-0001" not in str(functions)


@pytest.mark.asyncio
async def test_valid_start_dispatches_the_same_typed_request() -> None:
    """本地核对后的对象与动作原样进入宿主有类型请求。"""
    agent = _prepare()
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    payload = start_arguments([_PARK, _FILM], ["travel", "interact"], ["步行", "接着看"])
    assert agent.validate_react_decisions([_decision("start_activity", **payload)], False) == ""
    result = await agent.dispatch_react_tool("start_activity", payload)
    assert result.ok is True
    assert len(environment.calls) == 1
    assert isinstance(environment.calls[0].request, StartActivityRequest)
    assert environment.calls[0].request.model_dump() == payload


def test_revision_follows_current_day_plan_without_plan_tool() -> None:
    """当日计划由零点后台任务生成，人物工具面没有 plan_day；修订整次运行都在，目标只收意图、正文与必填可空的
    remind_time、around_time；待办层已推进的目标不能重交（时间与能量规格第四章；docs/dev/SPEC-dayrun-fidelity.md 8.2）。"""
    data = _data()
    unplanned = _functions(_prepare(data))
    assert "plan_day" not in unplanned and "revise_day_plan" in unplanned
    data["todo_layer"]["day_plan"] = {"date": "2026-01-05", "goals": [
        {"intent_kind": "工作", "goal": "写周报", "progressed": True, "remind_time": None, "around_time": "约 09:30"}]}
    agent = _prepare(data)
    revising = _functions(agent)
    assert revising == unplanned
    parameters = revising["revise_day_plan"]["parameters"]
    assert set(parameters["properties"]) == {"goals"}
    assert set(parameters["properties"]["goals"]["items"]["properties"]) == {
        "intent_kind", "goal", "remind_time", "around_time"}
    resubmitted = {"goals": [{"intent_kind": "工作", "goal": "再写一次", "remind_time": None, "around_time": None}]}
    assert agent.validate_react_decisions([_decision("revise_day_plan", **resubmitted)], False) == (
        "ARGUMENTS_INVALID: revise_day_plan cannot resubmit progressed goals")
    fresh = {"goals": [{"intent_kind": "休息", "goal": "歇一会儿", "remind_time": "21:30", "around_time": "21:30"}]}
    assert agent.validate_react_decisions([_decision("revise_day_plan", **fresh)], False) == ""


def test_day_start_offers_the_normal_tools_and_readonly_keeps_its_gate() -> None:
    """运行开始与醒来的决策不先登记计划：工具面与其他决策相同，有无当日计划都一样（第四章）；只读循环仅开放记忆与结束工具。"""
    ordinary = set(_functions(_prepare()))
    assert set(REQUEST_MODELS) <= ordinary
    assert "plan_day" not in ordinary
    for reasons in (["run_start"], ["wake_from_sleep"], ["run_start", "wake_from_sleep"]):
        data = _data()
        data["wake_reasons"] = reasons
        assert set(_functions(_prepare(data))) == ordinary
        data["todo_layer"]["day_plan"] = {"date": "2026-01-05", "goals": []}
        assert set(_functions(_prepare(data))) == ordinary
    readonly = LifeAgent()
    readonly.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, [], "What happened?", True))
    assert set(_functions(readonly, readonly=True)).isdisjoint(REQUEST_MODELS)
