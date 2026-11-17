from tests.integration.runtime.current_contract_fixtures import turns_with_history
"""一轮决策以一个世界请求（或两个开始工具各一次的开始组）结束；记忆只经 conclude_activity 写入、来源由系统登记；
发给人物模型的内容不含内部身份。

对应 docs/design/04-simulation-engine.md 第七章「finish 并入 conclude_activity」「发给模型的内容不含内部身份」与验收台账
「会话、活动与记忆」；实施台账「七·记忆新增」。
"""


from worldmock.agents.prompt.life_prompt import user_layer
from worldmock.agents.prompt.tool_groups import expand_tool_layer

import copy
import json
import os
import re
from collections.abc import Callable
from datetime import timedelta
from itertools import islice
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.agents.protocol.react_loop import ReactDecision
from worldmock.state.decisions.round import DirectToolCall
from worldmock.agents.life_agent import LifeAgent, _conclusion_memories, life_action_tool_names, person_inputs
from worldmock.agents.memory.life_memory import MemoryStep, save_step
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore, read_episodes
from worldmock.agents.protocol.life_feedback import FEEDBACK_INSTRUCTION
from tests.integration.runtime.test_life_agent_protocol import (
    _Environment,
    _GroundedCorrectionDispatcher,
    _MEMORY_TOOL_NAMES,
    _ModelResponse,
    _MultipleModelResponse,
    _OBSERVED_AT,
    _SEARCH,
    _StepDispatcher,
    _StepEnvironment,
    _WAIT,
    _WAIT_CHOICE,
    _WORK,
    _conclude_args,
    _context,
    _memory,
    _observations,
    _prepared,
    _step_host,
    _stored_memories,
    _view,
    _wire_agent,
    _with_activities,
)
from tests.engine_fixture import engine_factory as _engine_factory  # noqa: F401
from tests.life.conftest import (
    MAIN_TOOL,
    SIDE_TOOL,
    activity_name,
    advance_until_ready,
    context,
    do_at,
    errand_add,
    stage_day_plan,
    use_app,
    pause_request,
    report_request,
)  # noqa: F401
from worldmock.foundation.errors import IntegrityError
from worldmock.simulation_engine.activities.lifecycle.activity_control import PHASE_ACTIVE, PHASE_PENDING
from worldmock.state.interaction.control_items import control_items
from worldmock.state.planning.errands import ERRAND_EVENT_KINDS
from worldmock.state.interaction.requests import ConcludeActivityRequest, REQUEST_MODELS
from worldmock.state.world.kinds import SameMomentStage


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    from worldmock.agents.prompt import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


def _observed(*activities: tuple[str, str]) -> list[dict]:
    """当前停留里有给定活动的一条结构化观察记录。"""
    observations = _observations()
    _with_activities(observations[0]["data"], *activities)
    return observations


def _action_requests(requests: list[dict]) -> list[dict]:
    """行动阶段的请求：工具中有 start_main_activity（docs/design/10-runtime.md 「身份与判定」）。"""
    return [request for request in requests
            if MAIN_TOOL in {item["function"]["name"] for item in request.get("tools") or []}]


def _tool_names(requests: list[dict]) -> list[set[str]]:
    """逐个行动阶段请求读取实际开放的工具名。"""
    return [{item["function"]["name"] for item in request["tools"]} for request in _action_requests(requests)]


def _response_errors(environment: _Environment) -> list[str | None]:
    """按调用顺序读取每次模型响应的协议校验结果。"""
    return [item.error for item in environment.evidence if item.phase == "response"]


def _decision(tool: str, arguments: dict) -> ReactDecision:
    """构造一次已解析的原生工具选择。"""
    return ReactDecision(tool, arguments, "")


@pytest.mark.parametrize("stage", [item.value for item in SameMomentStage])
def test_life_tool_surface_never_offers_finish(stage: str) -> None:
    """每个同刻阶段的生活决策工具面都是同样的七个请求工具与检索记忆，没有 finish。"""
    observations = _observed((_WORK, PHASE_ACTIVE), (_WAIT, PHASE_PENDING))
    observations[0]["data"]["same_moment_stage"] = stage
    agent = _prepared(observations[0]["data"])

    names = {item["function"]["name"] for item in agent.turn_tools(2, False)}

    assert "finish" not in names
    assert names - _MEMORY_TOOL_NAMES == set(REQUEST_MODELS)
    assert life_action_tool_names() == frozenset(REQUEST_MODELS)


@pytest.mark.asyncio
async def test_registered_world_request_ends_the_decision_and_memory_queries_do_not() -> None:
    """检索记忆是只读查询，可在请求前多次使用、不结束本轮；生活决策里的 finish 零执行；世界请求登记即结束本轮。"""
    conclusion = _conclude_args(_WAIT, memories=[[_memory("等了一会儿，脑子清楚多了。")]])
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-memory-1", "search_memory", _SEARCH),
        _ModelResponse("call-finish", "finish", json.dumps({"memories": []})),
        _ModelResponse("call-memory-2", "search_memory", _SEARCH),
        _ModelResponse("call-conclude", "conclude_activity", json.dumps(conclusion, ensure_ascii=False)),
        _ModelResponse("call-never-requested", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
    ])
    environment = _Environment()
    agent = _wire_agent(environment, dispatcher, 8)

    registered = await agent.run_turns(_context(), _observed((_WAIT, PHASE_ACTIVE))[0]["data"])
    assert [decision.action for decision in registered] == ["conclude_activity"]

    assert [call.request for call in environment.calls] == [ConcludeActivityRequest.model_validate(conclusion)]
    assert len(dispatcher.responses) == 1
    assert len(_action_requests(dispatcher.requests)) == 4
    assert all("finish" not in names for names in _tool_names(dispatcher.requests))
    assert _response_errors(environment) == [None, "TOOL_CALL_REQUIRED: The tool call batch is invalid.", None, None]
    assert [tool for tool, _ in environment.queries] == ["search_memory", "search_memory"]


@pytest.mark.asyncio
async def test_conclusion_memories_flatten_by_task_in_the_stored_shape() -> None:
    """宿主按项的顺序展平，原样交出 control_items 读出的存储形状记忆（每项先 memories、后 views，看法带 subject）；其他世界请求不写记忆。"""
    first = _memory("会议纪要整理完了，松了口气。", kind="resolution", importance=0.6)
    second = _memory("下次要早点动笔。", kind="goal", importance=0.4, keywords=("写作", "计划"))
    taste = _view("会议室", "小会议室开会最安静。", kind="preference", importance=0.2, keywords=("开会",))
    third = _memory("等人的时候心里有点急。", kind="conflict", importance=0.3)
    conclusion = _conclude_args(_WORK, _WAIT, memories=[[first, second], [third]], views=[[taste], []])
    written = []
    for tool, arguments in (("conclude_activity", conclusion), (MAIN_TOOL, _WAIT_CHOICE)):
        dispatcher = _GroundedCorrectionDispatcher(responses=[
            _ModelResponse("call-action", tool, json.dumps(arguments, ensure_ascii=False))])
        agent = _wire_agent(_Environment(), dispatcher, 4)
        observed = _observed((_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))[0]["data"]
        registered = await agent.run_turns(_context(), observed)
        assert [decision.action for decision in registered] == [tool]
        written.append(agent._memories)

    assert written[0] == [memory for item in control_items(
        "conclude_activity", ConcludeActivityRequest.model_validate(conclusion)) for memory in item.memories]
    assert written[1] == []


#: 基线代码对「等了一刻钟，心里平静了。」写出的条目行去掉五个键、编号改为 m_000001 之后的字面（人物 1，观察时刻 _OBSERVED_AT）。
_FIRST_EPISODE_LINE = (
    '{"id": "m_000001", "agent_id": 1, "time": "2026-01-05T00:00:00+00:00", "type": "routine", "importance": 0.3, '
    '"keywords": ["休息"], "text": "等了一刻钟，心里平静了。", "source": "activity_conclusion", "merge_count": 1, '
    '"epistemic_status": "subjective"}')


def test_views_persist_as_memories_byte_identical_to_the_subject_shape(tmp_path) -> None:
    """看法写在 views、对象写在 about：经 control_items 读出的记忆带 subject，与旧形状（memories[][] 里带 subject 的
    relationship、preference）写下的同一组记忆逐字节相同；宿主原样交出这组存储形状记忆，两组记忆各经步尾保存写进临时
    工作区，episodes.jsonl 逐字节相同、不含 subject，第一行等于基线代码对同一条记忆写出的行去掉五个键、编号改为
    m_000001（docs/design/04-simulation-engine.md 第六章验收）。"""
    plain = _memory("等了一刻钟，心里平静了。", importance=0.3)
    mother = _view("陈母", "想起还没回妈妈的消息。", importance=0.4, keywords=("妈妈",))
    taste = _view("拿铁", "下午想喝拿铁。", kind="preference", keywords=("咖啡",))
    current = _conclude_args(_WAIT, memories=[[plain]], views=[[mother, taste]])
    legacy = {"task": [_WAIT], "summary": ["先收个尾。"], "memories": [[
        {**plain, "subject": None},
        *({"type": view["type"], "text": view["text"], "importance": view["importance"],
           "keywords": view["keywords"], "subject": view["about"]} for view in (mother, taste))]]}
    read = control_items("conclude_activity", ConcludeActivityRequest.model_validate(current))
    with pytest.raises(IntegrityError, match="shape is not recognised"):
        control_items("conclude_activity", legacy)
    assert json.dumps(read[0].memories, ensure_ascii=False) == json.dumps(legacy["memories"][0], ensure_ascii=False)
    assert [memory["subject"] for memory in read[0].views] == ["陈母", "拿铁"]
    flattened = _conclusion_memories("conclude_activity", current)
    assert json.dumps(flattened, ensure_ascii=False) == json.dumps(legacy["memories"][0], ensure_ascii=False)
    persisted = []
    for label, memories in (("current", flattened), ("legacy", legacy["memories"][0])):
        store = WorldMockMemoryStore(tmp_path / label, 1, ())
        save_step(store, MemoryStep("人物画像里的身份", (), "observation-1", tuple(memories), _OBSERVED_AT))
        persisted.append(store.episodes_path.read_bytes())
    assert persisted[0] == persisted[1] and "拿铁".encode() in persisted[0] and b'"subject"' not in persisted[0]
    assert persisted[0].decode("utf-8").split("\n")[0] == _FIRST_EPISODE_LINE


def test_only_conclude_activity_carries_memories() -> None:
    """结束工具每项带记忆与看法；播报与暂停不带，多给 memories 按协议错误拒绝（第七章，一项一个对象）。"""
    agent = _prepared(_observed((_WORK, PHASE_ACTIVE))[0]["data"])
    functions = {item["function"]["name"]: item["function"] for item in agent.turn_tools(2, False)}

    def item_fields(tool: str) -> set[str]:
        """工具参数里每一项对象的字段。"""
        return set(functions[tool]["parameters"]["properties"]["items"]["items"]["properties"])

    assert item_fields("conclude_activity") == {"activity_name", "summary", "memories", "views"}
    for tool in ("report_status", "pause_activity"):
        assert item_fields(tool) == {"activity_name", "summary"}
        arguments = {"items": [{"activity_name": _WORK, "summary": "还在做。", "memories": [_memory()], "views": []}],
                     "errands": []}
        error = agent._validate([_decision(tool, arguments)])
        assert "not declared" in error.text


def _nine_entries() -> dict:
    """两项合计九条记忆与看法（超过八条上限）：七条普通记忆、两条偏好。"""
    texts = ("早上的阳光很好。", "路上有点堵。", "午饭吃得很满足。", "同事帮了我一把。", "下午有点犯困。",
             "会开得比预想顺利。", "晚上想早点睡。")
    views = [_view("面馆", "楼下面馆的面好吃。", kind="preference"), _view("散步", "晚上散步很舒服。", kind="preference")]
    return _conclude_args(_WORK, _WAIT, memories=[[_memory(text) for text in texts[:4]],
                                                  [_memory(text) for text in texts[4:]]], views=[views, []])


def _single_view(**changes: Any) -> dict:
    """结束一项活动，没有普通记忆，唯一一条看法按 changes 改动字段。"""
    return _conclude_args(_WAIT, memories=[[]], views=[[{**_view("沈昊"), **changes}]])


def _single(**changes: Any) -> dict:
    """结束一项活动，唯一一条记忆按 changes 改动字段。"""
    return _conclude_args(_WAIT, memories=[[{**_memory(), **changes}]])


_LIMIT_CASES = {
    "three_items": ({"items": [{"activity_name": name, "summary": "收尾。", "memories": [], "views": []}
                               for name in (_WORK, _WAIT, "午饭（1月5日 12:00 起）")], "errands": []},
                    "one or two items"),
    "repeated_activity": (_conclude_args(_WAIT, _WAIT), "must be distinct"),
    "blank_summary": ({"items": [{"activity_name": _WAIT, "summary": " ", "memories": [], "views": []}],
                       "errands": []}, "matching the declared format"),
    "summary_over_500_characters": ({"items": [{"activity_name": _WAIT, "summary": "长" * 501, "memories": [],
                                                "views": []}], "errands": []}, "within 500 characters"),
    "views_missing": ({"items": [{"activity_name": _WAIT, "summary": "收尾。", "memories": []}], "errands": []},
                      "required field"),
    "nine_memories_and_views_in_total": (_nine_entries(), "exceed eight"),
    "memory_with_a_subject": (_single(subject=None), "not declared"),
    "memory_of_a_view_type": (_single(type="relationship"), "outside the declared"),
    "view_without_about": (_single_view(about=" "), "matching the declared format"),
    "view_of_a_plain_type": (_single_view(type="routine"), "outside the declared"),
    "view_text_over_1000_characters": (_single_view(text="长" * 1001), "exceeds 1000 characters"),
    "view_with_thirteen_keywords": (_single_view(keywords=[f"词{index}" for index in range(13)]),
                                    "more than twelve keywords"),
    "empty_text": (_single(text=""), "matching the declared format"),
    "blank_text": (_single(text="   "), "matching the declared format"),
    "text_over_1000_characters": (_single(text="长" * 1001), "exceeds 1000 characters"),
    "hexadecimal_identity_in_text": (_single(text="我还记得 3e71a031bb3d 那一刻。"), "hexadecimal identifier"),
    # 八位以上十六进制串不分大小写：全大写与大小写混写（恰好八位）的身份串同样不进记忆文字，看法的文字也一样。
    "upper_case_hexadecimal_identity_in_text": (_single(text="我还记得 3E71A031BB3D 那一刻。"),
                                                "hexadecimal identifier"),
    "mixed_case_hexadecimal_identity_in_text": (_single(text="我还记得 3e71A031 那一刻。"), "hexadecimal identifier"),
    "upper_case_hexadecimal_identity_in_view_text": (_single_view(text="他给我看过 3E71A031BB3D 那张单子。"),
                                                     "hexadecimal identifier"),
    "thirteen_keywords": (_single(keywords=[f"词{index}" for index in range(13)]), "more than twelve keywords"),
    "importance_above_one": (_single(importance=1.5), "at most 1"),
    "importance_below_zero": (_single(importance=-0.1), "at least 0"),
    "fact_like_type": (_single(type="event"), "outside the declared"),
    "model_supplied_source": (_single(source="decision_observation"), "not declared"),
    "model_supplied_refs": (_single(refs=["observation-current"]), "not declared"),
    "model_supplied_status": (_single(epistemic_status="subjective"), "not declared"),
}
#: 一项一个对象的形状下，协议纠错的原因（经宿主的 ARGUMENTS_INVALID 逐字段写出，不带校验库转储）。
_LIMIT_REASONS = {
    "three_items": "conclude_activity takes one or two items",
    "repeated_activity": "conclude_activity activity names must be distinct",
    "blank_summary": "A non-blank string matching the declared format is required.",
    "summary_over_500_characters": "must be within 500 characters",
    "views_missing": "This required field was omitted.",
    "nine_memories_and_views_in_total": "conclude_activity memories and views exceed eight entries in total",
    "memory_with_a_subject": "This field is not declared and must be removed.",
    "view_without_about": "A non-blank string matching the declared format is required.",
    "view_text_over_1000_characters": "memory text exceeds 1000 characters",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_LIMIT_CASES))
async def test_conclusion_memory_limits_are_protocol_errors_retried_in_the_same_decision(case: str) -> None:
    """一至两项且活动名互不相同、总结非空、memories 与 views 都必填且合计不超过八条、普通记忆没有对象且不是看法类型、看法
    写对象、text 非空不超 1000 字符且无十六进制身份、关键词不超 12 个、重要度 0–1：越界由本地请求模型拒绝，按协议错误在
    本轮重试，只有改正后的请求到达环境（不计同刻轮数）。"""
    invalid, message = _LIMIT_CASES[case]
    valid = _conclude_args(_WAIT)
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-invalid", "conclude_activity", json.dumps(invalid, ensure_ascii=False)),
        _ModelResponse("call-valid", "conclude_activity", json.dumps(valid, ensure_ascii=False)),
    ])
    environment = _Environment()
    agent = _wire_agent(environment, dispatcher, 4)

    result = await agent.run_turns(_context(), _observed((_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))[0]["data"])

    assert [decision.action for decision in result] == ["conclude_activity"]
    assert [call.request for call in environment.calls] == [ConcludeActivityRequest.model_validate(valid)]
    error = _response_errors(environment)[0]
    assert error.startswith("ARGUMENTS_INVALID:") and message in error
    correction = _action_requests(dispatcher.requests)[1]
    content = correction["messages"][-1]["content"]
    feedback = json.loads(content)
    assert (feedback["error_code"], feedback["instruction"]) == ("ARGUMENTS_INVALID", FEEDBACK_INSTRUCTION)
    assert feedback["reason"] and "error" not in feedback
    assert feedback["reason"] == _LIMIT_REASONS.get(case, feedback["reason"])
    assert not any(dump in content for dump in ("input_value", "errors.pydantic", "Input should be",
                                                "Extra inputs are not permitted", "should match pattern"))
    assert agent._memories == _stored_memories(valid)


def test_conclusion_memory_limits_accept_the_boundaries() -> None:
    """边界值都受理：记忆与看法合计恰好八条、text 恰好 1000 字符、12 个关键词、重要度 0 与 1、某项零条记忆与零条看法。"""
    boundary = [
        _memory("长" * 1000),
        _memory("关键词正好十二个。", keywords=tuple(f"词{index}" for index in range(12))),
        _memory("一件不太要紧的小事。", importance=0.0),
        _memory("一件最要紧的大事。", importance=1.0),
        *(_memory(text) for text in ("晚风很舒服。", "楼下新开了面馆。")),
    ]
    views = [_view("面馆", "长" * 1000, kind="preference", importance=1.0),
             _view("晚风", "晚风很舒服。", kind="preference", keywords=tuple(f"词{index}" for index in range(12)))]
    arguments = _conclude_args(_WORK, _WAIT, memories=[boundary, []], views=[views, []])
    agent = _prepared(_observed((_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))[0]["data"])
    agent.turn_tools(2, False)

    assert agent._validate([_decision("conclude_activity", arguments)]) is None
    items = ConcludeActivityRequest.model_validate(arguments).items
    assert sum(len(item.memories) + len(item.views) for item in items) == 8


def test_store_fixes_source_and_status_and_refuses_non_subjective_types(tmp_path) -> None:
    """记忆存储按写入点固定来源与认知状态：输入里带的 source、epistemic_status 不被读取；事实型类型 event 以 has an invalid
    type 拒绝，条目文件与存储里的条目都保持调用之前的样子。"""
    store = WorldMockMemoryStore(tmp_path / "agent_0001", 1, ())
    memory = _stored_memories(_conclude_args(_WAIT))[0]

    written = store.append_episodes([{**memory, "source": "decision_observation", "epistemic_status": "objective"}],
                                    _OBSERVED_AT)

    assert [(item["source"], item["epistemic_status"]) for item in written] == [("activity_conclusion", "subjective")]
    before = store.episodes_path.read_bytes()
    with pytest.raises(ValueError, match="has an invalid type"):
        store.append_episodes([{**memory, "type": "event"}], _OBSERVED_AT + timedelta(minutes=1))
    assert store.episodes_path.read_bytes() == before
    assert store.episode_ids() == {"m_000001"}
    assert read_episodes(store.workspace, 1) == tuple(written)


class _ScriptedModel:
    """脚本化人物模型：行动请求按序返回脚本工具调用，留存发出的每个请求。

    可调用的脚本项按本次实际请求构造响应（例如从工具层取活动名）。
    """

    model_name = "test-model"

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    async def call(self, request):
        """保存实际请求的独立副本并返回下一条响应。"""
        self.requests.append(copy.deepcopy(request))
        response = self.responses.pop(0)
        return response(request) if callable(response) else response


def _offered_activity(request: dict) -> str:
    """一次模型请求的工具层里唯一的活动目标名：宿主实际列给模型的活动名。"""
    tool_layer = expand_tool_layer(user_layer(json.loads(request["messages"][1]["content"]), "工具层"))
    [name] = [target["name"] for target in tool_layer["targets"] if target["kind"] == "activity"]
    return name


def _naming_offered(call_id: str, arguments: Callable[[str], dict]) -> Callable[[dict], _ModelResponse]:
    """脚本项：取本次请求工具层里唯一的活动目标名，按它构造收尾调用。

    名称不在脚本里写死：宿主列出的名称即使混进内部身份，模型照样点名、收尾照样完成，扫描一直走到身份断言。
    """
    def respond(request: dict) -> _ModelResponse:
        offered = _offered_activity(request)
        return _ModelResponse(call_id, "conclude_activity", json.dumps(arguments(offered), ensure_ascii=False))

    return respond


def _settle(engine, round_id: str, tool: str, request):
    """单个就绪人物按真实顺序观察、登记并结算一个世界请求，返回动作结果。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"{decision.decision_id}:action:0001", tool, request))
    [result] = engine.commit_decision_round(decision.round_id)
    return result


def _engine_with_pending_wait(engine_factory) -> tuple[Any, str]:
    """真实引擎：人物在家点名歇一刻钟（夹具单元「测试休息」，900 秒）并推进到这段等待到点，等待成为刚结束、待收尾，
    人物在同一秒获得决策。"""
    engine = engine_factory()
    stage_day_plan(engine)
    started = _settle(engine, "wait", MAIN_TOOL, do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    assert engine.context.state.activities[started.activity_id].status.value == "completed"
    return engine, started.activity_id


def _engine_with_paused_app_and_pending_wait(engine) -> tuple[Any, str, str]:
    """真实引擎：人物用抖音看了五分钟内容后暂停（槽位 2 让出），接着在家点名歇一会儿（槽位 1）直到到点；此刻有暂停的观看
    与刚结束、待收尾的等待（只有后者占槽位）。"""
    stage_day_plan(engine)
    watch = _settle(engine, "open-app", SIDE_TOOL, use_app("抖音"))
    assert watch.status.value == "in_progress"
    assert advance_until_ready(engine)
    watch_name = activity_name(engine, watch.activity_id)
    paused = _settle(engine, "pause", "pause_activity", pause_request(watch_name, summary="先停一下。"))
    assert paused.status.value == "completed"
    wait = _settle(engine, "wait", MAIN_TOOL, do_at("家", "测试休息"))
    for index in range(16):
        assert advance_until_ready(engine)
        if engine.context.state.activities[wait.activity_id].status.value == "completed":
            break
        resumed = _settle(engine, f"wait-report-{index}", "report_status", report_request(
            activity_name(engine, wait.activity_id), summary="继续等待。"))
        assert resumed.status.value == "completed"
    assert engine.context.state.activities[wait.activity_id].status.value == "completed"
    return engine, watch.activity_id, wait.activity_id


def _person(engine, workspace: Path, model: _ScriptedModel) -> LifeAgent:
    """真实 LifeAgent：冻结输入取自引擎的人设表与运行清单的模型身份，名字「陈曦」；记忆存储建在全新的工作区，引擎就是
    人物拿到的句柄。"""
    inputs = person_inputs(engine.context.inputs.persona, engine.context.store.manifest["stage_model_identity"])
    return LifeAgent(replace(inputs, name="陈曦"), engine, model, WorldMockMemoryStore(workspace, 1, ()))


async def _decide(engine, person: LifeAgent, round_id: str):
    """真实人物决策：开轮、观察、登记唯一世界请求与写记忆，然后结算；返回本轮决策身份与动作结果。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    await person.decide(decision)
    [result] = engine.commit_decision_round(decision.round_id)
    return decision, result


def _events(engine, event_type: str, decision_id: str) -> list:
    """本轮决策写下的某类事件。"""
    return [event for event in list(engine.context.store.iter_events(engine.context.store.last_event_seq))
            if event.event_type == event_type and event.decision_id == decision_id]


@pytest.mark.asyncio
async def test_conclusion_sources_are_registered_by_the_system(_engine_factory, tmp_path) -> None:
    """带记忆收尾：环境只校验形状并随 action_requested 留证，来源（活动、会话、已核验经历）与写入点由系统登记。"""
    engine, wait_id = _engine_with_pending_wait(_engine_factory)
    name = activity_name(engine, wait_id)
    memories = [_memory("等了一刻钟，心里平静了。", importance=0.3)]
    views = [_view("抖音", "想起还没看完的内容。", kind="preference", importance=0.4, keywords=("妈妈",))]
    conclusion = {"items": [{"activity_name": name, "summary": "等完了。", "memories": memories, "views": views}],
                  "errands": []}
    model = _ScriptedModel([_ModelResponse("call-conclude", "conclude_activity",
                                           json.dumps(conclusion, ensure_ascii=False))])
    person = _person(engine, tmp_path / "agent_0001", model)

    decision, result = await _decide(engine, person, "conclude")

    activity = engine.context.state.activities[wait_id]
    assert result.status.value == "completed"
    assert activity.status.value == "completed" and activity.concluded_at == engine.context.state.current_time
    [observed] = _events(engine, "observation", decision.decision_id)
    waited = [row["source_event_id"] for row in observed.data["verified_experiences"] if row["activity_id"] == wait_id]
    assert waited
    assert result.data["memory_sources"] == [{"task": name, "activity_id": wait_id,
                                              "session_id": activity.session_ids[-1], "verified_event_ids": waited}]
    [requested] = _events(engine, "action_requested", decision.decision_id)
    assert requested.data["request"] == conclusion
    [update] = _events(engine, "memory_updated", decision.decision_id)
    assert update.data["trigger"] == ["activity_conclusion"]
    assert update.data["decision_id"] == decision.decision_id
    stored = read_episodes(person.memory.workspace, person.memory.agent_id)
    assert set(update.data["created_episode_ids"]) == {item["id"] for item in stored}
    assert [(item["type"], item["text"], item["source"], item["epistemic_status"])
            for item in stored] == [(memory["type"], memory["text"], "activity_conclusion", "subjective")
                                    for memory in (*memories, *views)]
    assert all("subject" not in item and "about" not in item for item in stored)
    after = context(engine, "after-conclusion")
    engine.begin_decision_round([after])
    assert engine.observe(after)["persona_views"]["preferences"] == [
        {"subject": "抖音", "preference": views[0]["text"], "noted_on": "2026-01-05"}]


@pytest.mark.asyncio
async def test_conclusion_rejected_at_settlement_keeps_its_memories(long_content_engine, tmp_path) -> None:
    """登记后结算被拒的 conclude_activity（有刚结束的等待却只结束暂停的观看），其记忆仍作为人物自述保留，写入点记为该动作。"""
    engine, watch_id, wait_id = _engine_with_paused_app_and_pending_wait(long_content_engine)
    memories = [_memory("那段短剧还没看完，有点惦记。", importance=0.3)]
    conclusion = {"items": [
        {"activity_name": activity_name(engine, watch_id), "summary": "不看了。", "memories": memories, "views": []}],
        "errands": []}
    model = _ScriptedModel([_ModelResponse("call-conclude", "conclude_activity",
                                           json.dumps(conclusion, ensure_ascii=False))])
    person = _person(engine, tmp_path / "agent_0001", model)

    decision, result = await _decide(engine, person, "rejected")

    assert (result.status.value, result.error_code) == ("rejected", "ACTIVITY_CONCLUSION_REQUIRED")
    assert activity_name(engine, wait_id) in result.message
    assert engine.context.state.activities[watch_id].status.value == "paused"
    [requested] = _events(engine, "action_requested", decision.decision_id)
    assert requested.data["request"] == conclusion
    [update] = _events(engine, "memory_updated", decision.decision_id)
    assert update.data["trigger"] == ["activity_conclusion"]
    assert update.data["decision_id"] == decision.decision_id
    stored = read_episodes(person.memory.workspace, person.memory.agent_id)
    assert [(item["text"], item["source"], item["epistemic_status"]) for item in stored] == [
        (memories[0]["text"], "activity_conclusion", "subjective")]
    assert update.data["created_episode_ids"] == [stored[0]["id"]]


@pytest.mark.asyncio
async def test_rejected_conclusion_keeps_its_memories_but_registers_no_errand(long_content_engine, tmp_path) -> None:
    """被业务拒绝的收尾带着一条合法的个人事务新增（docs/design/04-simulation-engine.md 「原子性」）：真实 LifeAgent 经
    引擎登记收尾（有刚结束的等待却只结束暂停的观看），宿主本地校验通过，登记时照常写入收尾记忆——memory_updated 的
    触发点只有 activity_conclusion，新建条目只有这一条收尾记忆；事务变更不是记忆写入点，记忆里没有事务的文字。结算以
    ACTIVITY_CONCLUSION_REQUIRED 拒绝，事务变更随整请求一并不生效：人物没有未完成事务，没有任何事务事件，结果没有回执键，
    请求事件原样留着这条变更。"""
    engine, watch_id, _ = _engine_with_paused_app_and_pending_wait(long_content_engine)
    memories = [_memory("那段短剧还没看完，有点惦记。", importance=0.3)]
    noted = errand_add("买一袋薯片", "薯片", memo="下班前下单送到家", remind_time="17:00")
    conclusion = {"items": [
        {"activity_name": activity_name(engine, watch_id), "summary": "不看了。", "memories": memories, "views": []}],
        "errands": [noted]}
    model = _ScriptedModel([_ModelResponse("call-conclude", "conclude_activity",
                                           json.dumps(conclusion, ensure_ascii=False))])
    person = _person(engine, tmp_path / "agent_0001", model)

    decision, result = await _decide(engine, person, "rejected")

    assert (result.status.value, result.error_code) == ("rejected", "ACTIVITY_CONCLUSION_REQUIRED")
    assert "errands" not in result.data and engine.context.state.activities[watch_id].status.value == "paused"
    [requested] = _events(engine, "action_requested", decision.decision_id)
    assert requested.data["request"] == conclusion
    [update] = _events(engine, "memory_updated", decision.decision_id)
    assert update.data["trigger"] == ["activity_conclusion"]
    stored = read_episodes(person.memory.workspace, person.memory.agent_id)
    assert [(item["text"], item["source"], item["epistemic_status"]) for item in stored] == [
        (memories[0]["text"], "activity_conclusion", "subjective")]
    assert update.data["created_episode_ids"] == [stored[0]["id"]]
    assert not any(text in json.dumps(stored, ensure_ascii=False) for text in ("买一袋薯片", "下班前下单送到家"))
    assert engine.context.state.agents[1].errands == {}
    assert [event for event in engine.context.store.read_agent_events("environment_event", 1)
            if event.data.get("kind") in ERRAND_EVENT_KINDS] == []


#: 八位以上的十六进制串（事件身份、摘要、条目编号中的长数字段）。
_HEX_RUN = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{8,}(?![0-9A-Za-z])")
#: 场景与事实清单按时间合同披露的 epoch 秒是时刻，不是身份：只放行本轮时刻前后一年内的纯数字串。
_TIME_WINDOW_SECONDS = 366 * 86400
#: 以 _id 或 _ids 结尾的 JSON 键。
_ID_KEY = re.compile(r'"([A-Za-z0-9_]*_ids?)"\s*:')


def _private_identities(value: Any, key: str = "") -> set[str]:
    """递归收集以 _id、_ids 结尾或名为 id 的键所带的身份值（只取含数字或分隔符、不是普通单词的串）。"""
    if isinstance(value, dict):
        return set().union(*(_private_identities(child, name) for name, child in value.items()))
    if isinstance(value, list):
        return set().union(*(_private_identities(child, key) for child in value))
    identity_key = key == "id" or key.endswith("_id") or key.endswith("_ids")
    return {value} if identity_key and isinstance(value, str) and re.search(r"[\d:_-]", value) else set()


def _time_value(run: str, now: int) -> bool:
    """命中的纯数字串是否为本轮前后一年内的 epoch 秒（例如场景的「Unix epoch：当前 …」与任务截止时刻）。"""
    return run.isdigit() and abs(int(run) - now) <= _TIME_WINDOW_SECONDS


def _model_visible(messages: list[dict], tools: list[dict]) -> list[str]:
    """模型能读到的全部文本：消息正文、思考字段、回传的工具调用名与参数、工具 Schema。

    tool_call_id 与工具调用的 id 是协议句柄，不属于内部身份（第七章「发给模型的内容不含内部身份」）。
    """
    texts = [json.dumps(tools, ensure_ascii=False)]
    for message in messages:
        texts.extend((str(message.get("content") or ""), str(message.get("reasoning_content") or "")))
        texts.extend(part for call in message.get("tool_calls") or []
                     for part in (call["function"]["name"], call["function"]["arguments"]))
    return texts


def _identity_leaks(text: str, private: set[str], now: int) -> list[str]:
    """一段模型可见文本中的内部身份：十六进制串（时刻秒数除外）、*_id 键与本轮私有身份值。"""
    leaks = [match.group() for match in _HEX_RUN.finditer(text) if not _time_value(match.group(), now)]
    leaks.extend(_ID_KEY.findall(text))
    leaks.extend(identity for identity in private if identity in text)
    return leaks


@pytest.mark.asyncio
async def test_nothing_sent_to_the_person_model_carries_an_internal_identity(_engine_factory, tmp_path) -> None:
    """系统层、六层 user、工具 Schema 与工具返回（检索记忆、协议纠错）中都没有内部身份；登记后没有工具返回。

    模型按工具层实际列出的活动名点名收尾，名称里混进的内部身份由身份断言检出，而不是让脚本对话提前中断。"""
    engine, wait_id = _engine_with_pending_wait(_engine_factory)
    name = activity_name(engine, wait_id)
    model = _ScriptedModel([
        _ModelResponse("call-recent", "search_memory", _SEARCH),
        _ModelResponse("call-search", "search_memory",
                       json.dumps({"category": "生活", "time": "今天", "keyword": "散步"}, ensure_ascii=False)),
        _naming_offered("call-mismatch", lambda offered: {"items": [
            {"activity_name": offered, "summary": "等完了。", "memories": []}], "errands": []}),
        _naming_offered("call-conclude", lambda offered: _conclude_args(
            offered, memories=[[_memory("等完这一刻钟，决定出门散步。", importance=0.2)]])),
    ])
    person = _person(engine, tmp_path / "agent_0001", model)
    store = person.memory
    earlier = _conclude_args("散步（1月4日 19:00 起）",
                             memories=[[_memory("答应周末陪朋友去散步。", kind="commitment", importance=0.2)]],
                             views=[[_view("散步", "晚饭后在小区里散步很舒服。", kind="preference", importance=0.2)]])
    store.append_episodes(_stored_memories(earlier), _OBSERVED_AT - timedelta(hours=10))

    decision = context(engine, "scan")
    engine.begin_decision_round([decision])
    _, history = await turns_with_history(person, decision, engine.observe(decision))
    person._record_memory(decision)
    [result] = engine.commit_decision_round(decision.round_id)

    assert result.status.value == "completed"
    [observed] = _events(engine, "observation", decision.decision_id)
    private = _private_identities(observed.data) | {
        item["id"] for item in read_episodes(store.workspace, store.agent_id)} | {
        observed.event_id, decision.run_id, decision.round_id, decision.decision_id,
        f"{decision.decision_id}:action:0001"}
    now = observed.data["current_time"]
    raw = _identity_leaks(json.dumps(observed.data, ensure_ascii=False), private, now)
    assert {wait_id, "activity_id", "source_event_id"} <= set(raw) and name not in private
    assert len(model.requests) == 4
    visible = [text for request in model.requests
               for text in _model_visible(request["messages"], request.get("tools", []))]
    visible.extend(_model_visible(history, []))
    assert [leak for text in visible for leak in _identity_leaks(text, private, now)] == []
    assert any(observed.data["environment_layer"]["observed_at"] in text for text in visible)
    returned = [json.loads(message["content"]) for message in history if message.get("role") == "tool"]
    searched = [payload for payload in returned if "未列出" in payload]
    assert [payload["关键词"] for payload in searched] == [None, "散步"]
    assert any(payload.get("instruction") == FEEDBACK_INSTRUCTION for payload in returned)
    assert len(returned) == 3 and history[-1]["role"] == "assistant"
    assert any(row["name"] == name and row["kind"] == "activity" and row["phase"] == "刚结束"
               for row in expand_tool_layer(user_layer(json.loads(model.requests[0]["messages"][1]["content"]),
                                                       "工具层"))["targets"])
    assert [_offered_activity(request) for request in model.requests[2:]] == [name, name]


@pytest.mark.asyncio
async def test_the_reply_body_and_the_conclusion_stay_out_of_the_next_progress_layer(_engine_factory,
                                                                                    tmp_path) -> None:
    """真实宿主先查记忆、再收尾：下一次观察的进度层没有收尾请求的记录，不含 self_note，也不含收尾写下的记忆与看法正文
    （回复正文与收尾都在今天条目里）；search_memory 不在进度层、只在模型请求证据里，记忆条目正文不出现在进度层。"""
    engine, wait_id = _engine_with_pending_wait(_engine_factory)
    name = activity_name(engine, wait_id)
    memories = [_memory("等完这一刻钟，心里踏实了。", importance=0.2)]
    views = [_view("抖音", "想看看下一条内容。", kind="preference", importance=0.3)]
    conclusion = {"items": [{"activity_name": name, "summary": "等完了。", "memories": memories, "views": views}],
                  "errands": []}
    model = _ScriptedModel([
        _ModelResponse("call-recent", "search_memory", _SEARCH),
        _ModelResponse("call-conclude", "conclude_activity", json.dumps(conclusion, ensure_ascii=False),
                       content="  等够了，收个尾。 "),
    ])
    person = _person(engine, tmp_path / "agent_0001", model)
    person.memory.append_episodes(
        _stored_memories(_conclude_args("散步（1月4日 19:00 起）", memories=[[_memory("散步很舒服。")]])),
        _OBSERVED_AT - timedelta(hours=10))
    decision, result = await _decide(engine, person, "queried")
    assert result.status.value == "completed"
    [requested] = _events(engine, "action_requested", decision.decision_id)
    assert requested.data["self_note"] == "等够了，收个尾。"

    after = context(engine, "after-query")
    engine.begin_decision_round([after])
    progress = engine.observe(after)["progress_layer"]
    records = [event for event in progress["events"] if event["type"] == "tool_call"]
    assert not any(record["tool"] == "search_memory" for record in records)
    assert "search_memory" not in json.dumps(progress, ensure_ascii=False)
    evidence = _events(engine, "model_request", decision.decision_id)
    assert any('"name": "search_memory"' in json.dumps(event.data["request"]["messages"]) for event in evidence)
    text = json.dumps(progress, ensure_ascii=False)
    assert "散步很舒服" not in text
    assert not any(record["tool"] == "conclude_activity" for record in records)
    assert "self_note" not in text and "等够了，收个尾。" not in text
    assert memories[0]["text"] not in text and views[0]["text"] not in text


@pytest.mark.asyncio
async def test_budget_exhausted_without_a_registered_world_request_fails_the_run(monkeypatch, tmp_path) -> None:
    """回合预算耗尽仍无登记的世界请求即运行故障：运行宿主以原异常的 repr 中止本轮、登记运行失败，不结算、不写记忆、
    不建检查点。"""
    environment = _StepEnvironment()
    dispatcher = _StepDispatcher()
    dispatcher.responses = [_ModelResponse(f"call-memory-{index}", "search_memory", _SEARCH)
                            for index in range(4)]
    host = _step_host(monkeypatch, tmp_path, environment, dispatcher)

    with pytest.raises(RuntimeError, match="^ReAct decision budget exhausted without a world request$"):
        await host.run()

    reason = "RuntimeError('ReAct decision budget exhausted without a world request')"
    assert environment.calls == []
    assert environment.memory_updates == []
    assert environment.commits == []
    assert environment.checkpoints == []
    assert _response_errors(environment) == [None, None, None, "TOOL_CALL_REQUIRED: The tool call batch is invalid."]
    assert len(environment.aborts) == 1
    assert environment.aborts[0][1] == reason
    assert len(environment.failures) == 1
    assert environment.failures[0] == reason
