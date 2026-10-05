"""宿主协议纠错的错误合同（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第四章「错误合同」）。

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
from tests.engine_fixture import (engine_factory as _engine_factory)  # noqa: F401
from tests.life.conftest import (last_result, start_arguments)  # noqa: F401
from tests.life.test_named_purchase import _browsed, _hidden, _layer_context  # noqa: E402
from worldmock.life import prompt_capacity  # noqa: E402
from worldmock.life.activity_control import PHASE_ACTIVE  # noqa: E402
from worldmock.life.error_contract import ERROR_KEYS, HOST_ERROR_CODES, host_defaults, host_report  # noqa: E402
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
    """损坏 JSON、缺字段与无调用保留五键反馈、完整路径和纯英文证据。"""
    missing = copy.deepcopy(_WAIT_CHOICE)
    missing["items"][0].pop("way")
    broken = '{"items": [{"target": "自己" "way":"等待"}]}'
    feedback, errors, requests = await _corrected_loop([
        _ModelResponse("call-json", "start_activity", broken),
        _ModelResponse("call-shape", "start_activity", json.dumps(missing, ensure_ascii=False)), _NoToolResponse()])
    assert [item["error_code"] for item in feedback] == ["ARGUMENTS_NOT_JSON", "ARGUMENTS_INVALID", "TOOL_CALL_REQUIRED"]
    for item in feedback:
        assert list(item) == [*ERROR_KEYS, "instruction"] and item["instruction"] == FEEDBACK_INSTRUCTION
        assert item["reason"] and item["fix"] and item["unmet"] == []
        assert not any(marker in json.dumps(item) for marker in _DUMP_MARKERS)
    assert feedback[0]["items"][0]["path"] == ["tool_calls", 0, "function", "arguments"]
    assert "line 1, column" in feedback[0]["items"][0]["expected"]
    row = feedback[1]["items"][0]
    assert row["path"] == ["tool_calls", 0, "function", "arguments", "items", 0, "way"]
    assert row["written"] is None and "required" in row["expected"]
    assert feedback[2]["items"][0]["path"] == ["tool_calls"]
    assert [type(error) for error in errors] == [str, str, str]
    assert [error.split(":", 1)[0] for error in errors] == [item["error_code"] for item in feedback]
    assert len(requests) == 4


def _twin_news() -> LifeAgent:
    """本轮列出同名的内容「内容：晚间新闻」与只能观看的游戏「游戏：晚间新闻」：只写「晚间新闻」归一后对应两个对象。"""
    observed = _observations()
    _add_objects(observed[0]["data"],
                 _visible_object("内容：晚间新闻", "content", "content-news", _offer("interact", "接着看", "watch")),
                 _visible_object("游戏：晚间新闻", "game", "game-news", _offer("interact", "接着看", "watch")))
    return _prepared(observed)


def _offer_beside_another_store() -> LifeAgent:
    """本轮列出一件标签写明门店的供给，冻结地点表里另有一家「街角超市」（from 写它是另一个已知地点）。"""
    observed = _observed_offer()
    data = observed[0]["data"]
    data["locations"] = [*data["locations"], {"location_id": "corner-shop", "name": "街角超市"}]
    offer = LifeAgent()
    offer.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observed))
    return offer


def _host_cases() -> dict[str, tuple[str, str]]:
    """宿主错误码闭集的每个码经真实宿主路径得到的（错误码，原因）：截断经 LifeAgent 覆写的 _parse_react_responses 判定
    （docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-输出截断与思考螺旋 ），名称歧义经统一名称解析（同名的内容与游戏，3.1），来源不一致是列出的标签配
    冻结地点表里的另一个地点（3.2（b））。"""
    agent = _prepared()
    offer = _offer_beside_another_store()
    concluding = _prepared([{"turn": 0, "action": "observe", "ok": True, "data": _step_observation()}])
    unknown = _conclude_args(_WAIT, memories=[[]], views=[[_view("陌生人")]])
    produced = {
        "ARGUMENTS_NOT_JSON": agent._parse_react_responses(_ModelResponse("bad", "start_activity", "{"))[1],
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
    """每类旧有效错误行为保留，并使用新的英文完整错误合同。"""
    cases = _host_cases()
    assert set(cases) == set(HOST_ERROR_CODES) - {
        "MODEL_SERVICE_FAILED", "EVIDENCE_WRITE_FAILED", "MEMORY_QUERY_FAILED", "TOOL_SERVICE_FAILED"}
    for code, (produced, reason) in cases.items():
        expected = "ARGUMENTS_INVALID" if code == "TOO_MANY_TARGETS" else code
        assert produced == expected and reason, (code, produced, reason)
    assert "not declared" in cases["ARGUMENTS_INVALID"][1]
    assert "world request" in cases["TOOL_CALL_REQUIRED"][1]
    assert "one or two" in cases["TOO_MANY_TARGETS"][1]
    assert "不存在的对象" in cases["NAME_NOT_FOUND"][1]
    assert "does not support" in cases["WAY_NOT_OFFERED"][1]
    assert "from" in cases["ROUTE_FIELDS_INVALID"][1]
    assert "街角超市" in cases["SUPPLY_SOURCE_MISMATCH"][1]
    assert "陌生人" in cases["SUBJECT_NOT_KNOWN"][1]
    assert "several" in cases["NAME_AMBIGUOUS"][1]
    assert "truncated" in cases["OUTPUT_TRUNCATED"][1]


def test_a_second_item_failing_is_reported_at_index_one_with_fill_in_candidates() -> None:
    """第二项方式错误仍精确定位，候选均为完整可直接提交的新请求。"""
    agent = _prepared()
    error = agent.validate_react_decisions([_decision("start_activity", start_arguments(
        [_TASK, "自己"], ["interact", "interact"], ["工作", "观看"]))], False)
    payload = _feedback(error)
    assert payload["error_code"] == "WAY_NOT_OFFERED"
    [item] = payload["items"]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", "items", 1, "way"]
    assert item["written"] == "观看" and item["allowed_values"] == ["等待", "睡觉"]
    assert [row["arguments"]["items"][0] for row in item["candidates"]] == [
        {"target": "自己", "way": "等待", "from": None, "to": None},
        {"target": "自己", "way": "睡觉", "from": None, "to": None}]
    assert all(row["tool"] == "start_activity" and row["arguments"]["prefer"] is None
               and row["arguments"]["answering"] is None for row in item["candidates"])
    assert "自己" in payload["reason"] and payload["fix"]


def test_a_view_about_an_unknown_person_in_the_second_item_is_reported_at_index_one() -> None:
    """结束两项活动，第一项只写偏好、第二项的关系看法写了不认识的人：SUBJECT_NOT_KNOWN 的明细指向第二项（index 1），参数名
    是 about、原文是所写的名字（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第四章 items、第六章 views）。"""
    data = _with_activities(_observations()[0]["data"], (_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))
    agent = _prepared([{"turn": 0, "action": "observe", "ok": True, "data": data}])
    arguments = _conclude_args(_WORK, _WAIT, memories=[[], []],
                               views=[[_view("拿铁", "下午想喝拿铁。", kind="preference")], [_view("陌生人")]])
    payload = _feedback(agent.validate_react_decisions([_decision("conclude_activity", arguments)], False))
    assert payload["error_code"] == "SUBJECT_NOT_KNOWN"
    assert [(item["path"][-5:], item["written"], item["error_code"]) for item in payload["items"]] == [
        (["items", 1, "views", 0, "about"], "陌生人", "SUBJECT_NOT_KNOWN")]


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
    """每条未知关系对象均带真实 views 下标，同名重复也保留独立位置。"""
    agent = _two_activities("沈昊")
    _, payload = _concluded(agent, _conclude_args(_WORK, _WAIT, memories=[[], []], views=[
        [_view("王五"), _view("沈昊"), _view("拿铁", "下午想喝拿铁。", kind="preference")], [_view("陌生人")]]))
    assert payload["error_code"] == "SUBJECT_NOT_KNOWN"
    assert [(item["path"][-5:], item["written"]) for item in payload["items"]] == [
        (["items", 0, "views", 0, "about"], "王五"), (["items", 1, "views", 0, "about"], "陌生人")]
    _, payload = _concluded(agent, _conclude_args(_WORK, _WAIT, memories=[[], []], views=[
        [_view("王五"), _view("陌生人"), _view("王五", "又想起他。")], []]))
    assert [(item["path"][-2], item["written"]) for item in payload["items"]] == [(0, "王五"), (1, "陌生人"), (2, "王五")]
    _, single = _concluded(agent, _conclude_args(_WAIT, memories=[[]], views=[[_view("王五"), _view("陌生人")]]))
    assert [item["written"] for item in single["items"]] == ["王五", "陌生人"]
    assert all(item["error_code"] == "SUBJECT_NOT_KNOWN" for item in single["items"])
    assert single["fix"] and payload["fix"]


def test_an_unknown_activity_name_does_not_hide_an_unknown_view_in_another_item() -> None:
    """活动名和关系对象错误独立汇总，包含同一项中的不同字段。"""
    agent = _two_activities()
    _, payload = _concluded(agent, _conclude_args("散步", _WAIT, memories=[[], []], views=[[], [_view("陌生人")]]))
    assert payload["error_code"] == "NAME_NOT_FOUND"
    assert [(item["path"][4:], item["written"], item["error_code"]) for item in payload["items"]] == [
        (["items", 0, "activity_name"], "散步", "NAME_NOT_FOUND"),
        (["items", 1, "views", 0, "about"], "陌生人", "SUBJECT_NOT_KNOWN")]
    _, both = _concluded(agent, _conclude_args("散步", _WAIT, memories=[[], []], views=[[_view("王五")], []]))
    assert [item["path"][4:] for item in both["items"]] == [
        ["items", 0, "activity_name"], ["items", 0, "views", 0, "about"]]
    assert "散步" in payload["reason"] and "陌生人" in payload["reason"] and payload["fix"]


def test_all_name_errors_are_reported_and_other_conditions_report_the_first() -> None:
    """名称与独立路线错误一次全报，依赖不存在对象的条件不推断。"""
    agent = _prepared()
    both = _feedback(agent.validate_react_decisions([_decision("start_activity", start_arguments(
        ["不存在的对象", "自己"], ["interact", "interact"], ["等待", "观看"]))], False))
    assert both["error_code"] == "NAME_NOT_FOUND"
    assert [(item["path"][-2], item["error_code"]) for item in both["items"]] == [(0, "NAME_NOT_FOUND"), (1, "WAY_NOT_OFFERED")]
    mixed = _feedback(agent.validate_react_decisions([_decision("start_activity", start_arguments(
        ["自己", "不存在的对象"], ["interact", "interact"], ["等待", "等待"], **{"from": ["自己", None]}))], False))
    assert [(item["path"][-2], item["error_code"]) for item in mixed["items"]] == [(1, "NAME_NOT_FOUND"), (0, "ROUTE_FIELDS_INVALID")]
    route = _feedback(agent.validate_react_decisions([_decision("start_activity", start_arguments(
        ["自己", _TASK], ["interact", "interact"], ["等待", "工作"], **{"from": ["自己", "自己"]}))], False))
    assert [item["path"][-3:] for item in route["items"]] == [["items", 0, "from"], ["items", 1, "from"]]
    assert all(item["allowed_values"] == [None] for item in route["items"])


def test_argument_errors_are_written_per_field_without_any_validation_dump() -> None:
    """类型、范围、文字身份和多余字段全部定位，JSON 原值不字符串化。"""
    concluding = _prepared([{"turn": 0, "action": "observe", "ok": True, "data": _step_observation()}])
    memory = _memory("我记得 3e71a031bb3d4103 那会儿很累。", importance=1.5)
    arguments = {**_conclude_args(_WAIT, memories=[[memory]]), "extra": 1}
    error = concluding.validate_react_decisions([_decision("conclude_activity", arguments)], False)
    payload = _feedback(error)
    assert payload["error_code"] == "ARGUMENTS_INVALID"
    assert [item["path"][4:] for item in payload["items"]] == [
        ["items", 0, "memories", 0, "text"], ["items", 0, "memories", 0, "importance"], ["extra"]]
    assert [item["written"] for item in payload["items"]] == [memory["text"], 1.5, 1]
    assert not any(marker in LifeAgent._protocol_feedback_content(error) for marker in _DUMP_MARKERS)
    limited = _conclude_args(_WAIT, memories=[[_memory("长" * 1001)]])
    row = _feedback(concluding.validate_react_decisions([_decision("conclude_activity", limited)], False))["items"][0]
    assert row["path"][-5:] == ["items", 0, "memories", 0, "text"] and "1000" in row["why"]


def _obtain_arguments(target: str, candidate: dict | None = None) -> dict:
    """一次远程下单送给自己的 start_activity 参数；给出候选时逐字段照填候选。"""
    if candidate is None:
        return start_arguments([target], ["obtain"], ["远程下单"])
    return candidate["arguments"]


@pytest.mark.asyncio
async def test_a_candidate_submitted_verbatim_is_accepted_through_the_host_and_the_engine(_engine_factory) -> None:
    """真实 LifeEngine、LifeEnv 与 LifeAgent（脚本化模型）：人物把一件存在但没有列出的商品名写错，宿主按指名购买放行，
    环境以 SUPPLY_NOT_FOUND 拒绝，items[0] 的候选是可照填的请求；同一秒下一次决策把其中这件商品的候选逐字段原样提交，
    宿主不纠错、环境受理并下单（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第四章验收：候选原样提交可受理）。"""
    engine = _engine_factory()
    listed = _browsed(engine)
    supply = _hidden(engine, listed, unique_name=True)
    label = supply_label(_layer_context(engine), supply)
    written = f"{engine.context.rules.items[supply.product_id].name}加冰"
    environment = _environment(engine)
    await _single(environment, "misspelled", [_reply("call-misspelled", "start_activity",
                                                     _obtain_arguments(written), "买一份。")])
    rejected = engine.context.store.read_agent_events("action_result", 1)[-1].data
    assert (rejected["status"], rejected["error_code"]) == ("rejected", "SUPPLY_NOT_FOUND")
    seen: dict = {}

    def resubmit(observation: dict) -> list:
        """从本次观察进度层的拒绝结果里取这件商品的候选，逐字段原样提交。"""
        details = last_result(observation)["details"]
        [item] = details["items"]
        seen.update(details=details, candidate=next(row for row in item["candidates"] if row["arguments"]["items"][0]["target"] == label))
        return [_reply("call-candidate", "start_activity", _obtain_arguments(label, seen["candidate"]), "照候选买。")]

    _, requests, _ = await _single(environment, "candidate", resubmit)
    assert seen["candidate"]["arguments"]["items"] == [{"target": label, "way": "远程下单", "from": None, "to": None}]
    assert seen["details"]["reason"] and seen["details"]["items"][0]["written"] == written
    assert len(requests) == 1
    accepted = engine.context.store.read_agent_events("action_result", 1)[-1].data
    assert (accepted["status"], accepted["error_code"]) == ("completed", None)
    assert [order["supply_id"] for order in accepted["data"]["orders"]] == [supply.supply_id]


def test_feedback_rejects_bare_strings_instead_of_reconstructing_positions() -> None:
    """不从旧日志猜测错误类别或参数位置。"""
    for text in ("OUTPUT_TRUNCATED: model output ended", "NAME_NOT_FOUND: missing object", "memory unavailable"):
        with pytest.raises(TypeError, match="structured error report"):
            _feedback(text)


def test_feedback_structure_survives_copying_and_evidence_stays_a_plain_string() -> None:
    """HostFeedback 复制后仍带结构（AS2 或证据链路可能复制协议错误串）；落进证据时是普通字符串。"""
    error = _prepared().validate_react_decisions([_decision("start_activity", start_arguments(
        ["不存在的对象"], ["interact"], ["等待"]))], False)
    copied = copy.deepcopy(error)
    assert isinstance(copied, HostFeedback) and copied.report == error.report and copied == error
    assert type(str(error)) is str
