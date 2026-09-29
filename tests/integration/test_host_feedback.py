"""宿主协议纠错的错误合同（docs/dev/SPEC-dayrun-fidelity.md 第四章「错误合同」）。

LifeAgent 覆写 AS2 的 _protocol_feedback_content（锁定提交不改）：纠错正文是共用错误结构 {error_code, reason, fix, items,
unmet} 加固定 instruction「本次没有登记到世界；按 fix 修改后重新提交」，不带英文校验转储、input_value 与网址；宿主错误码
闭集的每个码经真实宿主路径各有一例具体原因；多项请求中第二项出错时 items 写 index 1，名称类错误逐项报全部、其余条件报第一条；
写进模型响应证据的协议错误串是普通字符串「<错误码>: <英文说明>」。真实 LifeEngine、LifeEnv 与脚本化模型上，环境拒绝给出的
候选原样提交即被受理。
"""

from __future__ import annotations

import copy
import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from custom.agents.life_feedback import FEEDBACK_INSTRUCTION, HostFeedback, host_feedback  # noqa: E402
from tests.integration.test_life_agent_protocol import (_TASK, _WAIT, _WAIT_CHOICE, _WORK,  # noqa: E402
                                                        _Environment, _GroundedCorrectionDispatcher, _ModelResponse,
                                                        _add_objects, _conclude_args, _context, _memory, _observations,
                                                        _offer, _prepared, _step_observation, _view,
                                                        _visible_object, _wire_agent, _with_activities)
from tests.integration.test_output_truncation import _Truncated  # noqa: E402
from tests.integration.test_same_second_continuation import _environment, _reply, _single  # noqa: E402
from tests.integration.test_storefront_tools import _OFFER, _observed_offer  # noqa: E402
from tests.life.conftest import engine_factory as _engine_factory, last_result, start_arguments  # noqa: E402,F401
from tests.life.test_named_purchase import _browsed, _hidden, _layer_context  # noqa: E402
from worldmock.life import prompt_capacity  # noqa: E402
from worldmock.life.activity_control import PHASE_ACTIVE  # noqa: E402
from worldmock.life.error_contract import ERROR_KEYS, HOST_ERROR_CODES, host_defaults, host_report  # noqa: E402
from worldmock.life.guidance import GENERIC_REASON  # noqa: E402
from worldmock.life.named_purchase import supply_label  # noqa: E402

#: 纠错正文里不得出现的校验库原文、输入回显与网址片段。
_DUMP_MARKERS = ("input_value", "input_type", "http://", "https://", "errors.pydantic", "validation error",
                 "Value error", "For further information")


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """测试使用固定供应商窗口，只验证纠错合同。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


class _NoToolResponse:
    """没有任何工具调用、只有回复正文的模型响应（AS2 报 native tool_calls are required）。"""

    def __init__(self) -> None:
        message = SimpleNamespace(tool_calls=[], content="我想先想一想。", reasoning_content="思考")
        self.choices = [SimpleNamespace(message=message)]

    def model_dump(self, *, mode: str) -> dict:
        """最小 JSON 响应。"""
        assert mode == "json"
        return {"choices": [{"message": {"content": "我想先想一想。", "tool_calls": [], "reasoning_content": "思考"}}]}


def _decision(tool: str, arguments: dict) -> ReactDecision:
    """一次原生工具决定。"""
    return ReactDecision("", tool, arguments, "")


def _feedback(error: str) -> dict:
    """一条协议错误回给模型的纠错正文（经 LifeAgent 覆写的 AS2 入口）。"""
    return json.loads(LifeAgent._protocol_feedback_content(error))


async def _corrected_loop(bad_responses: list, observations: list[dict] | None = None) -> tuple[list, list, list]:
    """真实 ReAct 循环：先给出几次不合法的响应，最后提交等待；返回（纠错正文列表，响应证据的错误串，发出的请求）。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        *bad_responses, _ModelResponse("call-action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, len(bad_responses) + 2, memory=False)
    result = await agent.run_react_loop(ReactLoopRequest(0, _context().observed_at, observations or [
        {"turn": 0, "action": "observe", "ok": True, "data": _step_observation()}]))
    assert result == "start_activity registered" and len(environment.calls) == 1
    feedback = [json.loads(request["messages"][-1]["content"]) for request in dispatcher.requests[1:]]
    errors = [item.error for item in environment.evidence if item.phase == "response" and item.error is not None]
    return feedback, errors, dispatcher.requests


@pytest.mark.asyncio
async def test_protocol_feedback_is_the_shared_structure_with_the_fixed_instruction() -> None:
    """真实循环中的三类协议错误（参数不是 JSON、缺少参数、没有工具调用）：每次纠错正文恰为共用结构五个键加 instruction，
    不再有 ok、executed、error_type 与英文 error；instruction 是「本次没有登记到世界；按 fix 修改后重新提交」；纠错正文
    不含校验转储、input_value 与网址；写进响应证据的协议错误串是普通字符串，宿主本地校验的以错误码开头。"""
    missing = dict(_WAIT_CHOICE)
    missing.pop("ways")
    broken = '{"targets": ["自己" "等待"]}'
    with pytest.raises(json.JSONDecodeError) as parsed:
        json.loads(broken)
    feedback, errors, requests = await _corrected_loop([
        _ModelResponse("call-json", "start_activity", broken),
        _ModelResponse("call-shape", "start_activity", json.dumps(missing, ensure_ascii=False)),
        _NoToolResponse()])
    assert [item["error_code"] for item in feedback] == ["ARGUMENTS_NOT_JSON", "ARGUMENTS_INVALID",
                                                         "TOOL_CALL_REQUIRED"]
    for item in feedback:
        assert list(item) == [*ERROR_KEYS, "instruction"] and item["instruction"] == FEEDBACK_INSTRUCTION
        assert item["reason"] and item["fix"] and item["unmet"] == []
    assert feedback[0]["reason"] == (f"「start_activity」的参数不是合法的 JSON（第 {parsed.value.lineno} 行第 "
                                     f"{parsed.value.colno} 列附近）")
    assert feedback[1]["reason"] == "ways 没有写"
    assert feedback[1]["items"] == [{"index": 0, "field": "ways", "written": "", "error_code": "ARGUMENTS_INVALID",
                                     "candidates": []}]
    assert feedback[2]["reason"] == "这次回复没有可以执行的工具调用"
    for request in requests[1:]:
        content = request["messages"][-1]["content"]
        assert not any(marker in content for marker in _DUMP_MARKERS), content
    assert [type(error) for error in errors] == [str, str, str]
    assert errors[0].startswith("invalid tool arguments for start_activity")
    assert errors[1].startswith("ARGUMENTS_INVALID: invalid start_activity arguments") and "ways" in errors[1]
    assert errors[2] == "native tool_calls are required"


def _twin_news() -> LifeAgent:
    """本轮列出同名的内容「内容：晚间新闻」与只能观看的游戏「游戏：晚间新闻」：只写「晚间新闻」归一后对应两个对象。"""
    observed = _observations()
    _add_objects(observed[0]["data"],
                 _visible_object("内容：晚间新闻", "content", "content-news", _offer("interact", "接着看", "watch")),
                 _visible_object("游戏：晚间新闻", "game", "game-news", _offer("interact", "接着看", "watch")))
    return _prepared(observed)


def _host_cases() -> dict[str, tuple[str, str]]:
    """宿主错误码闭集的每个码经真实宿主路径得到的（错误码，原因）：截断经 LifeAgent 覆写的 _parse_react_responses 判定
    （docs/dev/SPEC-dayrun-fidelity.md 7.2），名称歧义经统一名称解析（同名的内容与游戏，3.1）。"""
    agent = _prepared()
    offer = LifeAgent()
    offer.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, _observed_offer()))
    concluding = _prepared([{"turn": 0, "action": "observe", "ok": True, "data": _step_observation()}])
    unknown = _conclude_args(_WAIT, memories=[[]], views=[[_view("陌生人")]])
    produced = {
        "ARGUMENTS_NOT_JSON": _feedback("invalid tool arguments for start_activity: Expecting value: line 1 column 1"
                                        " (char 0)"),
        "ARGUMENTS_INVALID": agent.validate_react_decisions([_decision("start_activity", {
            **_WAIT_CHOICE, "targets": "自己"})], False),
        "TOOL_CALL_REQUIRED": agent.validate_react_decisions([_decision("start_activity", _WAIT_CHOICE)] * 2, False),
        "TOO_MANY_TARGETS": agent.validate_react_decisions([_decision("start_activity", start_arguments(
            ["自己"] * 3, ["interact"] * 3, ["等待"] * 3))], False),
        "NAME_NOT_FOUND": agent.validate_react_decisions([_decision("start_activity", start_arguments(
            ["不存在的对象"], ["interact"], ["等待"]))], False),
        "NAME_AMBIGUOUS": _twin_news().validate_react_decisions([_decision("start_activity", start_arguments(
            ["晚间新闻"], ["interact"], ["接着看"]))], False),
        "WAY_NOT_OFFERED": agent.validate_react_decisions([_decision("start_activity", start_arguments(
            ["自己"], ["interact"], ["观看"]))], False),
        "ROUTE_FIELDS_INVALID": agent.validate_react_decisions([_decision("start_activity", start_arguments(
            ["自己"], ["interact"], ["等待"], **{"from": ["自己"]}))], False),
        "SUPPLY_SOURCE_MISMATCH": offer.validate_react_decisions([_decision("start_activity", start_arguments(
            [_OFFER], ["obtain"], ["远程下单"], **{"from": ["街角超市"]}))], False),
        "SUBJECT_NOT_KNOWN": concluding.validate_react_decisions([_decision("conclude_activity", unknown)], False),
        "OUTPUT_TRUNCATED": agent._parse_react_responses(_Truncated("思考写满了输出上限。"))[1],
    }
    return {code: _as_report(value) for code, value in produced.items()}


def _as_report(value) -> tuple[str, str]:
    """一次纠错的（错误码，原因）：宿主本地校验是 HostFeedback，AS2 的协议错误串已写成纠错正文。"""
    payload = _feedback(value) if isinstance(value, HostFeedback) else value
    return payload["error_code"], payload["reason"]


def test_each_host_code_has_a_concrete_reason_through_the_host() -> None:
    """宿主错误码闭集的 11 个码各有一例具体原因（docs/dev/SPEC-dayrun-fidelity.md 第四章验收）：有宿主产生方的都经真实
    本地校验路径得到，原因写出所错之处；每个原因都不是兜底句。"""
    cases = _host_cases()
    assert set(cases) == set(HOST_ERROR_CODES)
    for code, (produced, reason) in cases.items():
        assert produced == code and reason and reason != GENERIC_REASON, (code, reason)
    assert cases["ARGUMENTS_INVALID"][1] == "targets 要写成数组"
    assert cases["TOOL_CALL_REQUIRED"][1] == "一次回复只能提交一个世界请求，这次有 2 个"
    assert cases["TOO_MANY_TARGETS"][1] == "一次最多选两个目标，这次写了 3 个"
    assert cases["NAME_NOT_FOUND"][1] == "「不存在的对象」不是可以操作的对象"
    assert cases["WAY_NOT_OFFERED"][1] == "「自己」没有列出「interact：观看」这种做法"
    assert cases["ROUTE_FIELDS_INVALID"][1] == "interact 的 from 与 to 都要写 null"
    assert cases["SUPPLY_SOURCE_MISMATCH"][1] == f"「{_OFFER}」已经写明了门店，from 要写 null"
    assert cases["SUBJECT_NOT_KNOWN"][1] == "关系看法写的对象「陌生人」不是认识的人"
    assert cases["NAME_AMBIGUOUS"][1] == "「晚间新闻」对应不止一个对象：「内容：晚间新闻」、「游戏：晚间新闻」"
    assert host_defaults("NAME_AMBIGUOUS")[0] not in {reason for _, reason in cases.values()}
    assert cases["OUTPUT_TRUNCATED"][1] == "输出在思考阶段用尽，尚未调用工具"


def test_a_second_item_failing_is_reported_at_index_one_with_fill_in_candidates() -> None:
    """两项 start_activity 中第一项合法、第二项的对象没有列出所写的做法：WAY_NOT_OFFERED 只写第二项（index 1），原因与
    改法写「第 2 项」，候选是该对象列出的动词与方式写成的可照填请求；证据串以错误码开头。"""
    agent = _prepared()
    error = agent.validate_react_decisions([_decision("start_activity", start_arguments(
        ["自己", "自己"], ["interact", "sleep"], ["等待", "观看"], to=[None, "自己"]))], False)
    payload = _feedback(error)
    assert error.startswith("WAY_NOT_OFFERED: start_activity verb and way are not offered by the chosen target")
    assert payload["error_code"] == "WAY_NOT_OFFERED"
    [item] = payload["items"]
    assert (item["index"], item["field"], item["written"]) == (1, "ways", "观看")
    assert item["candidates"] == [
        {"target": "自己", "action": "interact", "way": "等待", "from": None, "to": None},
        {"target": "自己", "action": "sleep", "way": "睡觉", "from": None, "to": None}]
    assert payload["reason"] == "第 2 项「自己」没有列出「sleep：观看」这种做法"
    assert payload["fix"] == "第 2 项改用该对象列出的动词与方式（见候选），或者去掉这一项"


def test_a_view_about_an_unknown_person_in_the_second_item_is_reported_at_index_one() -> None:
    """结束两项活动，第一项只写偏好、第二项的关系看法写了不认识的人：SUBJECT_NOT_KNOWN 的明细指向第二项（index 1），参数名
    是 about、原文是所写的名字（docs/dev/SPEC-dayrun-fidelity.md 第四章 items、第六章 views）。"""
    data = _with_activities(_observations()[0]["data"], (_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))
    agent = _prepared([{"turn": 0, "action": "observe", "ok": True, "data": data}])
    arguments = _conclude_args(_WORK, _WAIT, memories=[[], []],
                               views=[[_view("拿铁", "下午想喝拿铁。", kind="preference")], [_view("陌生人")]])
    payload = _feedback(agent.validate_react_decisions([_decision("conclude_activity", arguments)], False))
    assert payload["error_code"] == "SUBJECT_NOT_KNOWN"
    assert [(item["index"], item["field"], item["written"], item["error_code"]) for item in payload["items"]] == [
        (1, "about", "陌生人", "SUBJECT_NOT_KNOWN")]


#: relationship 看法的对象不是已知人物时写进证据的英文说明（证据串为「SUBJECT_NOT_KNOWN: <说明>: <名字>」）。
_SUBJECT_DETAIL = "conclude_activity relationship subject is not a known person"


def _two_activities(*people: str) -> LifeAgent:
    """当前停留里工作与等待两项都在进行；people 是同世界人物（私有绑定 person_bindings，都是认识的人）。"""
    data = _with_activities(_observations()[0]["data"], (_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))
    data["person_bindings"] = [{"name": f"人物：{name}", "person_id": f"agent:{index + 8}"}
                               for index, name in enumerate(people)]
    return _prepared([{"turn": 0, "action": "observe", "ok": True, "data": data}])


def _concluded(agent: LifeAgent, arguments: dict) -> tuple[str, dict]:
    """一次 conclude_activity 经宿主本地校验：（写进证据的协议错误串，回给模型的纠错正文）。"""
    error = agent.validate_react_decisions([_decision("conclude_activity", arguments)], False)
    return error, _feedback(error)


def test_every_unknown_relationship_view_is_reported_at_its_own_item() -> None:
    """结束两项活动，两项的关系看法各写了一个不认识的人（第一项另有一条写认识的人、一条偏好）：SUBJECT_NOT_KNOWN 逐项报全部，
    明细是 index 0 与 1、参数名 about、原文为所写的名字，原因逐项写「第 N 项」，改法一句写「第 1、2 项」；同一项里写了两个
    不认识的人时两个都报、改法只写一次这一项；证据串仍是「<错误码>: <英文说明>」，逐条说明以「; 」相连
    （docs/dev/SPEC-dayrun-fidelity.md 3.1 第 5 条、第四章：全部名称类失败项都报）。"""
    agent = _two_activities("沈昊")
    error, payload = _concluded(agent, _conclude_args(_WORK, _WAIT, memories=[[], []], views=[
        [_view("王五"), _view("沈昊"), _view("拿铁", "下午想喝拿铁。", kind="preference")], [_view("陌生人")]]))
    assert payload["error_code"] == "SUBJECT_NOT_KNOWN"
    assert [(item["index"], item["field"], item["written"], item["error_code"]) for item in payload["items"]] == [
        (0, "about", "王五", "SUBJECT_NOT_KNOWN"), (1, "about", "陌生人", "SUBJECT_NOT_KNOWN")]
    assert payload["reason"] == "第 1 项关系看法写的对象「王五」不是认识的人；第 2 项关系看法写的对象「陌生人」不是认识的人"
    assert payload["fix"] == f"第 1、2 项{host_defaults('SUBJECT_NOT_KNOWN')[1]}"
    assert error == f"SUBJECT_NOT_KNOWN: {_SUBJECT_DETAIL}: 王五; {_SUBJECT_DETAIL}: 陌生人"
    error, payload = _concluded(agent, _conclude_args(_WORK, _WAIT, memories=[[], []], views=[
        [_view("王五"), _view("陌生人"), _view("王五", "又想起他。")], []]))
    assert [(item["index"], item["field"], item["written"]) for item in payload["items"]] == [
        (0, "about", "王五"), (0, "about", "陌生人")]
    assert payload["reason"] == "第 1 项关系看法写的对象「王五」不是认识的人；第 1 项关系看法写的对象「陌生人」不是认识的人"
    assert payload["fix"] == f"第 1 项{host_defaults('SUBJECT_NOT_KNOWN')[1]}"
    assert error == f"SUBJECT_NOT_KNOWN: {_SUBJECT_DETAIL}: 王五; {_SUBJECT_DETAIL}: 陌生人"
    _, single = _concluded(agent, _conclude_args(_WAIT, memories=[[]], views=[[_view("王五"), _view("陌生人")]]))
    assert [(item["index"], item["written"]) for item in single["items"]] == [(0, "王五"), (0, "陌生人")]
    assert single["reason"] == "关系看法写的对象「王五」不是认识的人；关系看法写的对象「陌生人」不是认识的人"
    assert single["fix"] == host_defaults("SUBJECT_NOT_KNOWN")[1]


def test_an_unknown_activity_name_does_not_hide_an_unknown_view_in_another_item() -> None:
    """第一项的活动名不是本轮列出的活动、第二项的关系看法写了不认识的人：两项合并成一次纠错都报出（NAME_NOT_FOUND 在 index 0
    的 activity_name，SUBJECT_NOT_KNOWN 在 index 1 的 about），码取第一项的码，原因逐项写、改法每码一句；证据串以第一项的码
    开头，两条英文说明以「; 」相连（docs/dev/SPEC-dayrun-fidelity.md 3.1 第 5 条：名称类错误逐项报全部）。"""
    agent = _two_activities()
    error, payload = _concluded(agent, _conclude_args("散步", _WAIT, memories=[[], []], views=[[], [_view("陌生人")]]))
    assert payload["error_code"] == "NAME_NOT_FOUND"
    assert [(item["index"], item["field"], item["written"], item["error_code"]) for item in payload["items"]] == [
        (0, "activity_name", "散步", "NAME_NOT_FOUND"), (1, "about", "陌生人", "SUBJECT_NOT_KNOWN")]
    assert payload["reason"].startswith("第 1 项「散步」不是本轮列出的活动")
    assert payload["reason"].endswith("；第 2 项关系看法写的对象「陌生人」不是认识的人")
    assert payload["fix"] == ("第 1 项照工具层、待办层或记忆层中的写法填写名称，或者去掉这一项；"
                              f"第 2 项{host_defaults('SUBJECT_NOT_KNOWN')[1]}")
    assert error == ("NAME_NOT_FOUND: conclude_activity activity_name names an activity that is not visible; "
                     f"{_SUBJECT_DETAIL}: 陌生人")
    _, both = _concluded(agent, _conclude_args("散步", _WAIT, memories=[[], []], views=[[_view("王五")], []]))
    assert [(item["index"], item["field"], item["error_code"]) for item in both["items"]] == [
        (0, "activity_name", "NAME_NOT_FOUND"), (0, "about", "SUBJECT_NOT_KNOWN")]


def test_all_name_errors_are_reported_and_other_conditions_report_the_first() -> None:
    """名称类错误逐项报全部（两项都不成立时两项都在 items 里，码各自写，改法每码一句）；来源与去向这类其余条件只报第一条，
    且名称类错误在前（docs/dev/SPEC-dayrun-fidelity.md 3.1 第 5 条）。"""
    agent = _prepared()
    both = _feedback(agent.validate_react_decisions([_decision("start_activity", start_arguments(
        ["不存在的对象", "自己"], ["interact", "interact"], ["等待", "观看"]))], False))
    assert both["error_code"] == "NAME_NOT_FOUND"
    assert [(item["index"], item["error_code"]) for item in both["items"]] == [(0, "NAME_NOT_FOUND"),
                                                                               (1, "WAY_NOT_OFFERED")]
    assert both["reason"] == "第 1 项「不存在的对象」不是可以操作的对象；第 2 项「自己」没有列出「interact：观看」这种做法"
    assert both["fix"] == ("第 1 项改用候选里的请求，或者照工具层、待办层中的写法重写这一项，或者去掉这一项；"
                           "第 2 项改用该对象列出的动词与方式（见候选），或者去掉这一项")
    mixed = _feedback(agent.validate_react_decisions([_decision("start_activity", start_arguments(
        ["自己", "不存在的对象"], ["interact", "interact"], ["等待", "等待"], **{"from": ["自己", None]}))], False))
    assert (mixed["error_code"], [item["index"] for item in mixed["items"]]) == ("NAME_NOT_FOUND", [1])
    route = _feedback(agent.validate_react_decisions([_decision("start_activity", start_arguments(
        ["自己", _TASK], ["interact", "interact"], ["等待", "工作"], **{"from": ["自己", "自己"]}))], False))
    assert route["error_code"] == "ROUTE_FIELDS_INVALID"
    assert [(item["index"], item["field"], item["written"]) for item in route["items"]] == [(0, "from", "自己")]
    assert route["reason"] == "第 1 项：interact 的 from 与 to 都要写 null"


def test_argument_errors_are_written_per_field_without_any_validation_dump() -> None:
    """参数形状错误按字段写原因（缺字段、多字段、类型、界限与请求模型自己的校验说明），明细写参数名与所写的原文；纠错正文
    不含校验库转储、input_value 与网址，证据串保留英文完整说明。"""
    concluding = _prepared([{"turn": 0, "action": "observe", "ok": True, "data": _step_observation()}])
    memory = _memory("我记得 3e71a031bb3d4103 那会儿很累。", importance=1.5)
    arguments = {**_conclude_args(_WAIT, memories=[[memory]]), "extra": 1}
    error = concluding.validate_react_decisions([_decision("conclude_activity", arguments)], False)
    payload = _feedback(error)
    assert payload["error_code"] == "ARGUMENTS_INVALID"
    assert payload["reason"] == ("items[0].memories[0].importance 要不大于 1；extra 不是这个工具的参数，要去掉")
    assert [(item["index"], item["field"], item["written"]) for item in payload["items"]] == [
        (0, "items", "1.5"), (0, "extra", "1")]
    content = LifeAgent._protocol_feedback_content(error)
    assert not any(marker in content for marker in _DUMP_MARKERS), content
    assert "input_value" in error and error.startswith("ARGUMENTS_INVALID: invalid conclude_activity arguments")
    limited = _conclude_args(_WAIT, memories=[[_memory("长" * 1001)]])
    reason = _feedback(concluding.validate_react_decisions([_decision("conclude_activity", limited)], False))["reason"]
    assert reason == "items[0].memories[0]：memory text exceeds 1000 characters"


def _obtain_arguments(target: str, candidate: dict | None = None) -> dict:
    """一次远程下单送给自己的 start_activity 参数；给出候选时逐字段照填候选。"""
    if candidate is None:
        return start_arguments([target], ["obtain"], ["远程下单"])
    return {"targets": [candidate["target"]], "actions": [candidate["action"]], "ways": [candidate["way"]],
            "from": [candidate["from"]], "to": [candidate["to"]], "answering": None}


@pytest.mark.asyncio
async def test_a_candidate_submitted_verbatim_is_accepted_through_the_host_and_the_engine(_engine_factory) -> None:
    """真实 LifeEngine、LifeEnv 与 LifeAgent（脚本化模型）：人物把一件存在但没有列出的商品名写错，宿主按指名购买放行，
    环境以 SUPPLY_NOT_FOUND 拒绝，items[0] 的候选是可照填的请求；同一秒下一次决策把其中这件商品的候选逐字段原样提交，
    宿主不纠错、环境受理并下单（docs/dev/SPEC-dayrun-fidelity.md 第四章验收：候选原样提交可受理）。"""
    engine = _engine_factory()
    listed = _browsed(engine)
    supply = _hidden(engine, listed, unique_name=True)
    label = supply_label(_layer_context(engine), supply)
    written = f"{engine.rules.items[supply.product_id].name}加冰"
    environment = _environment(engine)
    await _single(environment, "misspelled", [_reply("call-misspelled", "start_activity",
                                                     _obtain_arguments(written), "买一份。")])
    rejected = engine.store.read_agent_events("action_result", 1)[-1].data
    assert (rejected["status"], rejected["error_code"]) == ("rejected", "SUPPLY_NOT_FOUND")
    seen: dict = {}

    def resubmit(observation: dict) -> list:
        """从本次观察进度层的拒绝结果里取这件商品的候选，逐字段原样提交。"""
        details = last_result(observation)["details"]
        [item] = details["items"]
        seen.update(details=details, candidate=next(row for row in item["candidates"] if row["target"] == label))
        return [_reply("call-candidate", "start_activity", _obtain_arguments(label, seen["candidate"]), "照候选买。")]

    _, requests, _ = await _single(environment, "candidate", resubmit)
    assert seen["candidate"] == {"target": label, "action": "obtain", "way": "远程下单", "from": None, "to": "自己"}
    assert seen["details"]["reason"] == f"「{written}」：没有找到这个名字的商品"
    assert len(requests) == 1
    accepted = engine.store.read_agent_events("action_result", 1)[-1].data
    assert (accepted["status"], accepted["error_code"]) == ("completed", None)
    assert [order["supply_id"] for order in accepted["data"]["orders"]] == [supply.supply_id]


def test_a_protocol_error_string_that_starts_with_a_host_code_maps_back_to_that_code() -> None:
    """只剩文字的协议错误串（如落库的证据、截断处理只交出的文字）以宿主错误码开头时取该码的默认说法；其余按 AS2 原文
    判定，判定不出的按没有可执行的工具调用处理。"""
    truncated = _feedback("OUTPUT_TRUNCATED: model output ended in reasoning before any tool call")
    assert (truncated["error_code"], truncated["reason"], truncated["fix"]) == (
        "OUTPUT_TRUNCATED", "输出在思考阶段用尽，尚未调用工具", "只调用一个工具")
    evidence = "NAME_NOT_FOUND: start_activity target is not a visible interactive object (targets[0])"
    assert _feedback(evidence)["error_code"] == "NAME_NOT_FOUND"
    assert _feedback("tool arguments for start_activity must be an object")["reason"] == (
        "「start_activity」的参数不是一个 JSON 对象")
    assert _feedback("tool call ids must be non-empty and unique")["reason"] == "工具调用缺少名称或编号"
    assert _feedback("finish requires a non-empty answer")["error_code"] == "ARGUMENTS_INVALID"
    assert _feedback("finish memories must be a list")["reason"] == "这次决策没有 finish 工具"
    assert _feedback("missing response choices")["error_code"] == "TOOL_CALL_REQUIRED"


def test_feedback_structure_survives_copying_and_evidence_stays_a_plain_string() -> None:
    """HostFeedback 复制后仍带结构（AS2 或证据链路可能复制协议错误串）；落进证据时是普通字符串。"""
    error = _prepared().validate_react_decisions([_decision("start_activity", start_arguments(
        ["不存在的对象"], ["interact"], ["等待"]))], False)
    copied = copy.deepcopy(error)
    assert isinstance(copied, HostFeedback) and copied.report == error.report and copied == error
    assert type(str(error)) is str
