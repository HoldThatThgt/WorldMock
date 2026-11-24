"""一次唤醒通过 conclude_activity 写下的记忆与看法：形状核验、工作区落盘、沙盒拒收原子性与无内部身份泄露。"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory as _engine_factory  # noqa: F401
from tests.integration.host_run_fixtures import host_run  # noqa: E402
from tests.integration.runtime.current_contract_fixtures import decide, inputs, resolved_binding, turns_with_history  # noqa: E402
from tests.integration.runtime.test_life_agent_protocol import (  # noqa: E402
    _ModelResponse,
    _MultipleModelResponse,
    _OBSERVED_AT,
    _SEARCH,
    _SandboxStub,
    _WAIT,
    _WAIT_CHOICE,
    _WORK,
    _binding,
    _conclude_args,
    _memory,
    _observations,
    _person_7,
    _stored_memories,
    _view,
    _wakeup,
    _with_activities,
)
from tests.life.conftest import (  # noqa: E402
    MAIN_TOOL,
    SIDE_TOOL,
    activity_name,
    advance_until_ready,
    do_at,
    errand_add,
    pause_request,
    report_request,
    stage_day_plan,
    use_app,
)
from worldmock.agents.life_agent import LifeAgent, _life_turn_tools, life_action_tool_names, life_query_tool_names, person_inputs  # noqa: E402
from worldmock.agents.memory.life_memory import MemoryStep, save_step  # noqa: E402
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore, read_episodes  # noqa: E402
from worldmock.agents.prompt.life_prompt import user_layer  # noqa: E402
from worldmock.agents.prompt.tool_groups import expand_tool_layer  # noqa: E402
from worldmock.agents.protocol.agent_loop import LoopToolCall, TURNS_EXHAUSTED, validate_single_tool_call  # noqa: E402
from worldmock.agents.protocol.life_feedback import FEEDBACK_INSTRUCTION  # noqa: E402
from worldmock.foundation.errors import IntegrityError, ProtocolError  # noqa: E402
from worldmock.simulation_engine.activities.lifecycle.activity_control import PHASE_ACTIVE, PHASE_PENDING  # noqa: E402
from worldmock.state.interaction.control_items import control_items  # noqa: E402
from worldmock.state.interaction.requests import ConcludeActivityRequest, REQUEST_MODELS  # noqa: E402
from worldmock.state.planning.errands import ERRAND_EVENT_KINDS  # noqa: E402


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，只验证请求和执行合同。"""
    from worldmock.agents.prompt import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


def _observed(*activities: tuple[str, str]) -> dict:
    """带一组活动的冻结观察。"""
    return _with_activities(_observations()[0]["data"], *activities)


def test_life_tool_surface_never_offers_finish() -> None:
    """常规生活回合始终开放全部九个原生工具，不含 finish。"""
    schemas, _ = _life_turn_tools(2, False)
    names = {item["function"]["name"] for item in schemas}
    assert "finish" not in names
    assert names == set(life_action_tool_names()) | set(life_query_tool_names())
    assert set(REQUEST_MODELS) == set(life_action_tool_names())


@pytest.mark.asyncio
async def test_registered_start_ends_the_wakeup_and_memory_queries_do_not() -> None:
    """只读查询不结束唤醒，finish 在本地零执行；conclude_activity 后接 start_main_activity 正常结束唤醒。"""
    obs = _observed((_WAIT, PHASE_ACTIVE))
    conclusion = _conclude_args(_WAIT, memories=[[_memory("等了一会儿，脑子清楚多了。")]])
    responses = [
        _ModelResponse("call-memory-1", "search_memory", _SEARCH),
        _ModelResponse("call-finish", "finish", json.dumps({"memories": []})),
        _ModelResponse("call-memory-2", "search_memory", _SEARCH),
        _ModelResponse("call-conclude", "conclude_activity", json.dumps(conclusion, ensure_ascii=False)),
        _ModelResponse("call-start", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
        _ModelResponse("call-never", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
    ]

    class _Client:
        def __init__(self) -> None:
            self.requests: list[dict] = []

        async def call(self, request):
            self.requests.append(copy.deepcopy(request))
            return responses.pop(0)

    client = _Client()
    sandbox = _SandboxStub(obs)
    outcome = await LifeAgent(_person_7()).decide_wakeup(_wakeup(obs), sandbox, _binding(client, max_turns=8))
    assert outcome.status == "completed" and len(responses) == 1
    assert [c.tool_name for c in sandbox.calls] == ["conclude_activity", MAIN_TOOL]
    errors = [item.error for item in sandbox.evidence if item.phase == "response"]
    assert errors[0] is None and errors[2:] == [None, None, None]
    assert errors[1].startswith("TOOL_CALL_REQUIRED:") and errors[1].endswith("The tool call batch is invalid.")
    assert [tool for tool, _ in sandbox.queries] == ["search_memory", "search_memory"]


@pytest.mark.asyncio
async def test_conclusion_memories_flatten_by_task_in_the_stored_shape() -> None:
    """宿主按 items 顺序展平 control_items 记忆与看法；仅开始动作不产生待写记忆。"""
    first = _memory("会议纪要整理完了，松了口气。", kind="resolution", importance=0.6)
    second = _memory("下次要早点动笔。", kind="goal", importance=0.4, keywords=("写作", "计划"))
    taste = _view("会议室", "小会议室开会最安静。", kind="preference", importance=0.2, keywords=("开会",))
    third = _memory("等人的时候心里有点急。", kind="conflict", importance=0.3)
    conclusion = _conclude_args(_WORK, _WAIT, memories=[[first, second], [third]], views=[[taste], []])
    obs = _observed((_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))

    class _Client:
        def __init__(self, resps) -> None:
            self._r = list(resps)

        async def call(self, _req):
            return self._r.pop(0)

    sb1 = _SandboxStub(obs)
    out1 = await LifeAgent(_person_7()).decide_wakeup(
        _wakeup(obs), sb1, _binding(_Client([
            _ModelResponse("c1", "conclude_activity", json.dumps(conclusion, ensure_ascii=False)),
            _ModelResponse("c2", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
        ]), max_turns=4),
    )
    sb2 = _SandboxStub(obs)
    out2 = await LifeAgent(_person_7()).decide_wakeup(
        _wakeup(obs), sb2, _binding(_Client([
            _ModelResponse("c3", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
        ]), max_turns=4),
    )
    assert list(out1.pending_memories) == [
        dict(memory) for item in control_items("conclude_activity", conclusion) for memory in item.memories]
    assert out2.pending_memories == ()


_FIRST_EPISODE_LINE = (
    '{"id": "m_000001", "agent_id": 1, "time": "2026-01-05T00:00:00+00:00", "type": "routine", "importance": 0.3, '
    '"keywords": ["休息"], "text": "等了一刻钟，心里平静了。", "source": "activity_conclusion", "merge_count": 1, '
    '"epistemic_status": "subjective"}')


def test_views_persist_as_memories_byte_identical_to_the_subject_shape(tmp_path) -> None:
    """看法写在 views 以 about 转成 subject 落盘后与直接带 subject 的旧条逐字节一致。"""
    plain = _memory("等了一刻钟，心里平静了。", importance=0.3)
    mother = _view("陈母", "想起还没回妈妈的消息。", importance=0.4, keywords=("妈妈",))
    taste = _view("拿铁", "下午想喝拿铁。", kind="preference", keywords=("咖啡",))
    current = _conclude_args(_WAIT, memories=[[plain]], views=[[mother, taste]])
    legacy = {"task": [_WAIT], "summary": ["先收个尾。"], "memories": [[
        {**plain, "subject": None},
        *({"type": v["type"], "text": v["text"], "importance": v["importance"],
           "keywords": v["keywords"], "subject": v["about"]} for v in (mother, taste))]]}
    read = control_items("conclude_activity", ConcludeActivityRequest.model_validate(current))
    with pytest.raises(IntegrityError, match="shape is not recognised"):
        control_items("conclude_activity", legacy)
    assert json.dumps(read[0].memories, ensure_ascii=False) == json.dumps(legacy["memories"][0], ensure_ascii=False)
    assert [m["subject"] for m in read[0].views] == ["陈母", "拿铁"]
    flattened = _stored_memories(current)
    persisted = []
    for label, memories in (("current", flattened), ("legacy", legacy["memories"][0])):
        store = WorldMockMemoryStore(tmp_path / label, 1, ())
        save_step(store, MemoryStep("人物画像里的身份", (), "observation-1", tuple(memories), _OBSERVED_AT))
        persisted.append(store.episodes_path.read_bytes())
    assert persisted[0] == persisted[1] and "拿铁".encode() in persisted[0] and b'"subject"' not in persisted[0]
    assert persisted[0].decode("utf-8").split("\n")[0] == _FIRST_EPISODE_LINE


def test_only_conclude_activity_carries_memories() -> None:
    """仅 conclude_activity 接受 memories 与 views；report_status 与 pause_activity 拒绝多余字段。"""
    schemas, models = _life_turn_tools(2, False)
    functions = {item["function"]["name"]: item["function"] for item in schemas}
    item_fields = lambda tool: set(functions[tool]["parameters"]["properties"]["items"]["items"]["properties"])
    assert item_fields("conclude_activity") == {"activity_name", "summary", "memories", "views"}
    for tool in ("report_status", "pause_activity"):
        assert item_fields(tool) == {"activity_name", "summary"}
        arguments = {"items": [{"activity_name": _WORK, "summary": "还在做。", "memories": [_memory()], "views": []}],
                     "errands": []}
        assert "not declared" in validate_single_tool_call(LoopToolCall(tool, arguments, "c1"), models).text


def _nine_entries() -> dict:
    texts = ("早上的阳光很好。", "路上有点堵。", "午饭吃得很满足。", "同事帮了我一把。", "下午有点犯困。",
             "会开得比预想顺利。", "晚上想早点睡。")
    views = [_view("面馆", "楼下面馆的面好吃。", kind="preference"), _view("散步", "晚上散步很舒服。", kind="preference")]
    return _conclude_args(_WORK, _WAIT, memories=[[_memory(t) for t in texts[:4]], [_memory(t) for t in texts[4:]]],
                          views=[views, []])


def _single_view(**changes: Any) -> dict:
    return _conclude_args(_WAIT, memories=[[]], views=[[{**_view("沈昊"), **changes}]])


def _single(**changes: Any) -> dict:
    return _conclude_args(_WAIT, memories=[[{**_memory(), **changes}]])


_LIMIT_CASES = {
    "three_items": ({"items": [{"activity_name": n, "summary": "收尾。", "memories": [], "views": []}
                               for n in (_WORK, _WAIT, "午饭（1月5日 12:00 起）")], "errands": []}, "one or two items"),
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
    "view_with_thirteen_keywords": (_single_view(keywords=[f"词{i}" for i in range(13)]), "more than twelve keywords"),
    "empty_text": (_single(text=""), "matching the declared format"),
    "blank_text": (_single(text="   "), "matching the declared format"),
    "text_over_1000_characters": (_single(text="长" * 1001), "exceeds 1000 characters"),
    "hexadecimal_identity_in_text": (_single(text="我还记得 3e71a031bb3d 那一刻。"), "hexadecimal identifier"),
    "upper_case_hexadecimal_identity_in_text": (_single(text="我还记得 3E71A031BB3D 那一刻。"),
                                                "hexadecimal identifier"),
    "mixed_case_hexadecimal_identity_in_text": (_single(text="我还记得 3e71A031 那一刻。"), "hexadecimal identifier"),
    "upper_case_hexadecimal_identity_in_view_text": (_single_view(text="他给我看过 3E71A031BB3D 那张单子。"),
                                                     "hexadecimal identifier"),
    "thirteen_keywords": (_single(keywords=[f"词{i}" for i in range(13)]), "more than twelve keywords"),
    "importance_above_one": (_single(importance=1.5), "at most 1"),
    "importance_below_zero": (_single(importance=-0.1), "at least 0"),
    "fact_like_type": (_single(type="event"), "outside the declared"),
    "model_supplied_source": (_single(source="decision_observation"), "not declared"),
    "model_supplied_refs": (_single(refs=["observation-current"]), "not declared"),
    "model_supplied_status": (_single(epistemic_status="subjective"), "not declared"),
}
_LIMIT_REASONS = {
    "three_items": "conclude_activity takes one or two items",
    "repeated_activity": "conclude_activity activity names must be distinct",
    "blank_summary": "A non-blank string matching the declared format is required.",
    "summary_over_500_characters": "must be within 500 characters",
    "views_missing": "This required field was omitted: items.0.views.",
    "nine_memories_and_views_in_total": "conclude_activity memories and views exceed eight entries in total",
    "memory_with_a_subject": "This field is not declared and must be removed.",
    "view_without_about": "A non-blank string matching the declared format is required.",
    "view_text_over_1000_characters": "memory text exceeds 1000 characters",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_LIMIT_CASES))
async def test_conclusion_memory_limits_are_protocol_errors_retried_in_same_wakeup(case: str) -> None:
    """收尾记忆结构越界在通用循环本地纠错并重试；改正后再开始动作，只留合法记忆。"""
    invalid, message = _LIMIT_CASES[case]
    valid = _conclude_args(_WAIT)
    obs = _observed((_WORK, PHASE_ACTIVE), (_WAIT, PHASE_ACTIVE))
    resps = [
        _ModelResponse("call-invalid", "conclude_activity", json.dumps(invalid, ensure_ascii=False)),
        _ModelResponse("call-valid", "conclude_activity", json.dumps(valid, ensure_ascii=False)),
        _ModelResponse("call-start", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
    ]
    requests: list[dict] = []

    class _Client:
        async def call(self, req):
            requests.append(copy.deepcopy(req))
            return resps.pop(0)

    sandbox = _SandboxStub(obs)
    outcome = await LifeAgent(_person_7()).decide_wakeup(_wakeup(obs), sandbox, _binding(_Client(), max_turns=4))
    assert outcome.status == "completed"
    assert [c.request for c in sandbox.calls][:1] == [ConcludeActivityRequest.model_validate(valid)]
    error = [item.error for item in sandbox.evidence if item.phase == "response"][0]
    assert error.startswith("ARGUMENTS_INVALID:") and message in error
    content = requests[1]["messages"][-1]["content"]
    feedback = json.loads(content)
    assert (feedback["error_code"], feedback["instruction"]) == ("ARGUMENTS_INVALID", FEEDBACK_INSTRUCTION)
    assert feedback["reason"] == _LIMIT_REASONS.get(case, feedback["reason"])
    assert not any(d in content for d in ("input_value", "errors.pydantic", "Input should be",
                                          "Extra inputs are not permitted", "should match pattern"))
    assert list(outcome.pending_memories) == _stored_memories(valid)


def test_conclusion_memory_limits_accept_the_boundaries() -> None:
    """八条记忆与看法合计上限及各字段极值全部接受。"""
    boundary = [
        _memory("长" * 1000),
        _memory("关键词正好十二个。", keywords=tuple(f"词{i}" for i in range(12))),
        _memory("一件不太要紧的小事。", importance=0.0),
        _memory("一件最要紧的大事。", importance=1.0),
        *(_memory(text) for text in ("晚风很舒服。", "楼下新开了面馆。")),
    ]
    views = [_view("面馆", "长" * 1000, kind="preference", importance=1.0),
             _view("晚风", "晚风很舒服。", kind="preference", keywords=tuple(f"词{i}" for i in range(12)))]
    arguments = _conclude_args(_WORK, _WAIT, memories=[boundary, []], views=[views, []])
    _, models = _life_turn_tools(2, False)
    assert validate_single_tool_call(LoopToolCall("conclude_activity", arguments, "c1"), models) is None
    assert sum(len(item.memories) + len(item.views) for item in ConcludeActivityRequest.model_validate(arguments).items) == 8


def test_store_fixes_source_and_status_and_refuses_non_subjective_types(tmp_path) -> None:
    """记忆存储固定 source 与 epistemic_status，拒绝非主观事件类型。"""
    store = WorldMockMemoryStore(tmp_path / "agent_0001", 1, ())
    memory = _stored_memories(_conclude_args(_WAIT))[0]
    written = store.append_episodes([{**memory, "source": "decision_observation", "epistemic_status": "objective"}],
                                    _OBSERVED_AT)
    assert [(i["source"], i["epistemic_status"]) for i in written] == [("activity_conclusion", "subjective")]
    before = store.episodes_path.read_bytes()
    with pytest.raises(ValueError, match="has an invalid type"):
        store.append_episodes([{**memory, "type": "event"}], _OBSERVED_AT + timedelta(minutes=1))
    assert store.episodes_path.read_bytes() == before and read_episodes(store.workspace, 1) == tuple(written)


def _settle_action(engine, tool: str, req_dict: dict):
    """在真实引擎上开启单步唤醒并提交一条世界动作。"""
    [wakeup] = engine.prepare_barrier((1,))
    sb = engine.open_sandbox(wakeup)
    res = sb.dispatch_world_tool(tool, req_dict, f"{wakeup.wakeup_id}:action:0001")
    out = sb.finish_wakeup(None)
    engine.commit_barrier([out])
    return res, wakeup.wakeup_id


def _engine_with_pending_wait(engine_factory) -> tuple[Any, str]:
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    res, _ = _settle_action(engine, MAIN_TOOL, do_at("家", "测试休息").model_dump(mode="json"))
    act_id = res.tool_message_payload["result"]["details"]["planned"][0] if False else next(iter(engine.context.state.activities))
    assert advance_until_ready(engine)
    assert engine.context.state.activities[act_id].status.value == "completed"
    return engine, act_id


def _engine_with_paused_app_and_pending_wait(engine) -> tuple[Any, str, str]:
    stage_day_plan(engine, submitted=True)
    _settle_action(engine, SIDE_TOOL, use_app("抖音").model_dump(mode="json"))
    watch_id = next(iter(engine.context.state.activities))
    assert advance_until_ready(engine)
    _settle_action(engine, "pause_activity", pause_request(activity_name(engine, watch_id), summary="先停。").model_dump(mode="json"))
    _settle_action(engine, MAIN_TOOL, do_at("家", "测试休息").model_dump(mode="json"))
    wait_id = [aid for aid in engine.context.state.activities if aid != watch_id][0]
    for _ in range(16):
        assert advance_until_ready(engine)
        if engine.context.state.activities[wait_id].status.value == "completed":
            break
        _settle_action(engine, "report_status", report_request(activity_name(engine, wait_id), summary="继续。").model_dump(mode="json"))
    assert engine.context.state.activities[wait_id].status.value == "completed"
    return engine, watch_id, wait_id


def _events(engine, event_type: str, wakeup_id: str | None = None) -> list:
    return [e for e in engine.context.store.iter_events(engine.context.store.last_event_seq)
            if e.event_type == event_type and (wakeup_id is None or e.wakeup_id == wakeup_id)]


@pytest.mark.asyncio
async def test_conclusion_sources_and_rejection_atomicity_with_host(long_content_engine, host_factory, tmp_path) -> None:
    """被沙盒拒绝的 conclude_activity（含事务新增与记忆）不写记忆也不建事务；改正结束等待并休息后，只落盘合法记忆。"""
    engine, watch_id, wait_id = _engine_with_paused_app_and_pending_wait(long_content_engine)
    watch_name, wait_name = activity_name(engine, watch_id), activity_name(engine, wait_id)
    bad_mem = _memory("不该存下的记忆。", importance=0.3)
    good_mem = _memory("等完这一刻钟，心里平静了。", importance=0.3)
    noted = errand_add("买一袋薯片", "薯片", memo="下班前下单", remind_time="17:00")
    bad_req = {"items": [{"activity_name": watch_name, "summary": "不看了。", "memories": [bad_mem], "views": []}],
               "errands": [noted]}
    good_req = {"items": [{"activity_name": wait_name, "summary": "歇好了。", "memories": [good_mem], "views": []}],
                "errands": []}

    class _SeqModel:
        def __init__(self):
            self.resps = [
                _ModelResponse("c-bad", "conclude_activity", json.dumps(bad_req, ensure_ascii=False)),
                _ModelResponse("c-good", "conclude_activity", json.dumps(good_req, ensure_ascii=False)),
                _ModelResponse("c-rest", MAIN_TOOL, json.dumps(do_at("家", "测试休息").model_dump(mode="json"),
                                                               ensure_ascii=False)),
            ]

        async def call(self, _req):
            return self.resps.pop(0)

    host = host_factory(engine, _SeqModel())
    await host.step()
    stored = read_episodes(engine.context.store.run_dir / "agents" / "agent_0001", 1)
    assert [item["text"] for item in stored] == [good_mem["text"]]
    assert engine.context.state.agents[1].errands == {}
    assert [e for e in _events(engine, "environment_event") if e.data.get("kind") in ERRAND_EVENT_KINDS] == []


_HEX_RUN = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{8,}(?![0-9A-Za-z])")
_ID_KEY = re.compile(r'"([A-Za-z0-9_]*_ids?)"\s*:')


def _private_identities(value: Any, key: str = "") -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_private_identities(child, name) for name, child in value.items()))
    if isinstance(value, list):
        return set().union(*(_private_identities(child, key) for child in value))
    identity_key = key == "id" or key.endswith("_id") or key.endswith("_ids")
    return {value} if identity_key and isinstance(value, str) and re.search(r"[\d:_-]", value) else set()


def _model_visible(messages: list[dict], tools: list[dict]) -> list[str]:
    texts = [json.dumps(tools, ensure_ascii=False)]
    for message in messages:
        texts.extend((str(message.get("content") or ""), str(message.get("reasoning_content") or "")))
        texts.extend(part for call in message.get("tool_calls") or []
                     for part in (call["function"]["name"], call["function"]["arguments"]))
    return texts


def _identity_leaks(text: str, private: set[str], now: int) -> list[str]:
    leaks = [m.group() for m in _HEX_RUN.finditer(text) if not (m.group().isdigit() and abs(int(m.group()) - now) <= 366 * 86400)]
    leaks.extend(_ID_KEY.findall(text))
    leaks.extend(identity for identity in private if identity in text)
    return leaks


@pytest.mark.asyncio
async def test_nothing_sent_to_the_person_model_carries_an_internal_identity(_engine_factory) -> None:
    """系统层、六层 user、工具 Schema 与查询/纠错返回中不出现任何私有身份。"""
    engine, wait_id = _engine_with_pending_wait(_engine_factory)
    name = activity_name(engine, wait_id)
    resps = [
        _ModelResponse("c-mem", "search_memory", _SEARCH),
        _ModelResponse("c-bad", "conclude_activity", json.dumps({
            "items": [{"activity_name": name, "summary": "等完了。", "memories": []}], "errands": []},
            ensure_ascii=False)),
        _ModelResponse("c-conclude", "conclude_activity", json.dumps(
            _conclude_args(name, memories=[[_memory("等完这一刻钟，决定出门散步。", importance=0.2)]]),
            ensure_ascii=False)),
        _ModelResponse("c-rest", MAIN_TOOL, json.dumps(do_at("家", "测试休息").model_dump(mode="json"),
                                                       ensure_ascii=False)),
    ]

    class _Client:
        def __init__(self) -> None:
            self.requests: list[dict] = []

        async def call(self, req):
            self.requests.append(copy.deepcopy(req))
            return resps.pop(0)

    client = _Client()
    [wakeup] = engine.prepare_barrier((1,))
    sb = engine.open_sandbox(wakeup)
    agent = LifeAgent(replace(person_inputs(engine.context.inputs.persona_by_id[1]), name="陈曦"))
    outcome, history = await turns_with_history(
        agent, wakeup, sb, resolved_binding(client, frozen=engine.context.inputs),
    )
    engine.commit_barrier([outcome])
    [observed] = _events(engine, "observation", wakeup.wakeup_id)
    private = _private_identities(observed.data) | {observed.event_id, engine.context.state.run_id, wakeup.wakeup_id}
    now = observed.data["current_time"]
    visible = [text for req in client.requests for text in _model_visible(req["messages"], req.get("tools", []))]
    visible.extend(_model_visible(history, []))
    assert [leak for text in visible for leak in _identity_leaks(text, private, now)] == []
    assert any(row["name"] == name and row["kind"] == "activity" and row["phase"] == "刚结束"
               for row in expand_tool_layer(user_layer(json.loads(client.requests[0]["messages"][1]["content"]),
                                                       "工具层"))["targets"])


@pytest.mark.asyncio
async def test_budget_exhausted_without_terminal_action_fails_the_run(engine_factory, host_factory) -> None:
    """回合预算耗尽仍未通过开始动作结束唤醒：RunHost 抛出 ProtocolError(TURNS_EXHAUSTED) 并登记 run_failed。"""
    engine = engine_factory(mutate=lambda _p, c: c["models"]["reasoner"].__setitem__("max_react_turns", 4))
    stage_day_plan(engine, submitted=True)

    class _QueryOnly:
        def __init__(self) -> None:
            self.i = 0

        async def call(self, _req):
            self.i += 1
            return _ModelResponse(f"c-{self.i}", "search_memory", _SEARCH)

    host = host_factory(engine, _QueryOnly())
    with pytest.raises(ProtocolError, match=f"^{TURNS_EXHAUSTED}$"):
        await host.run()
    [failed] = _events(engine, "run_failed")
    assert failed.data["error"] == repr(ProtocolError(TURNS_EXHAUSTED))
