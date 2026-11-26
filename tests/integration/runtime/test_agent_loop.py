"""通用 AgentLoop 与 LifeAgent 唤醒内多回合控制、查询、截断重试、取消与耗尽失败测试。"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory  # noqa: F401
from tests.integration.life_host_fixtures import TruncatedResponse  # noqa: E402
from tests.integration.runtime.current_contract_fixtures import ScriptedModel, decide, resolved_binding  # noqa: E402
from tests.integration.runtime.test_life_agent_protocol import (  # noqa: E402
    _ModelResponse,
    _MultipleModelResponse,
    _SandboxStub,
    _SEARCH,
    _WAIT,
    _WAIT_CHOICE,
    _conclude_args,
    _person_7,
    _step_observation,
    _wakeup,
)
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, main_arguments, side_arguments, stage_day_plan  # noqa: E402
from tests.life.environment_runtime import every_five_minutes, reach  # noqa: E402
from worldmock.agents.life_agent import LifeAgent, person_inputs  # noqa: E402
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.agents.protocol.agent_loop import (  # noqa: E402
    AgentLoop,
    LoopCallbacks,
    LoopConfig,
    LoopStepOutcome,
    LoopToolCall,
    TURNS_EXHAUSTED,
    build_tool_schema,
)
from worldmock.foundation.errors import ProtocolError  # noqa: E402
from worldmock.state.interaction.actions import SEARCH_TOOL  # noqa: E402
from worldmock.state.interaction.requests import RecallTaskSummaryRequest  # noqa: E402
from worldmock.state.interaction.schema import ToolRequestModel  # noqa: E402


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


class _DummyToolReq(ToolRequestModel):
    value: str


def test_generic_agent_loop_decouples_from_life_and_runs_multi_turn() -> None:
    """通用 AgentLoop 不依赖 LifeAgent/Search 业务规则，可直接驱动自定义回调并完成多回合流转。"""
    async def _run():
        evidences: list = []
        calls_seen: list[tuple[str, dict]] = []
        responses = [
            _ModelResponse("t1", "echo_tool", '{"value":"first"}', content="先问一次"),
            _ModelResponse("t2", "done_tool", '{"value":"final"}', content="完成"),
        ]
        model = ScriptedModel(responses)
        binding = resolved_binding(model, max_turns=4)
        config = LoopConfig(
            scope_id="scope-1", wakeup_id="wakeup-1", agent_id=1, call_kind="generic",
            binding=binding, start_turn=1, max_turns=4, allow_thinking=False,
        )

        def turn_tools(_remaining: int, _retry: bool) -> tuple[list[dict], dict[str, type[BaseModel]]]:
            models = {"echo_tool": _DummyToolReq, "done_tool": _DummyToolReq}
            schemas = [build_tool_schema(name, name, mdl) for name, mdl in models.items()]
            return schemas, models

        def execute_calls(calls: list[LoopToolCall], _note: str | None) -> LoopStepOutcome:
            call = calls[0]
            calls_seen.append((call.tool, call.arguments))
            if call.tool == "done_tool":
                return LoopStepOutcome("completed", payload=call.arguments["value"])
            return LoopStepOutcome("continue", ({"role": "tool", "tool_call_id": call.tool_call_id,
                                                "content": json.dumps({"echo": call.arguments["value"]})},))

        callbacks = LoopCallbacks(
            turn_tools=turn_tools,
            validate_call=lambda _c, _m: None,
            validate_batch=lambda _c, _m: None,
            execute_calls=execute_calls,
            record_exchange=evidences.append,
        )
        outcome = await AgentLoop(config, callbacks).run([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ])
        assert outcome.status == "completed" and outcome.turns_used == 2 and outcome.payload == "final"
        assert calls_seen == [("echo_tool", {"value": "first"}), ("done_tool", {"value": "final"})]
        assert [e.call_id for e in evidences] == [
            "scope-1:generic:0001:attempt:1", "scope-1:generic:0001:attempt:1",
            "scope-1:generic:0002:attempt:1", "scope-1:generic:0002:attempt:1",
        ]
        assert model.requests[1]["messages"][-1]["role"] == "tool"
        assert json.loads(model.requests[1]["messages"][-1]["content"]) == {"echo": "first"}

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_multi_turn_zero_second_control_tools_feed_layer_changes_without_advancing_time(engine_factory) -> None:
    """同一唤醒内连续调用 revise_day_plan、pause_activity、report_status、conclude_activity 均在零秒完成并回传层差量。"""
    engine = engine_factory(mutate=every_five_minutes)
    stage_day_plan(engine)
    # 先开始并运行一个在家的“测试休息”活动，再推进到可暂停与播报边界
    await decide(engine, "boot", [_ModelResponse("c0", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息")))])
    act_id = next(iter(engine.context.state.activities))
    act_name = engine.context.state.activities[act_id].name
    now = engine.context.state.current_time
    # 推进 300s 后离结束 600s >= 180s（可暂停、可播报）
    reach(engine, now + 300)
    t_wakeup = engine.context.state.current_time
    responses = [
        _ModelResponse("c-plan", "revise_day_plan", {
            "goals": [{"intent_kind": "休息", "goal": "歇一会儿", "remind_time": None, "around_time": None}],
        }, "先更新计划", "调一下计划。"),
        _ModelResponse("c-pause", "pause_activity", {
            "items": [{"activity_name": act_name, "summary": "先停一下。"}], "errands": [],
        }, "先暂停"),
        _ModelResponse("c-resume", "report_status", {
            "items": [{"activity_name": act_name, "summary": "恢复继续。"}], "errands": [],
        }, "再恢复"),
        _ModelResponse("c-conclude", "conclude_activity", _conclude_args(act_name), "直接收尾"),
        _ModelResponse("c-rest", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息")), "再休息"),
    ]
    raw_resps = [
        _ModelResponse(r._call_id, r._name, r._arguments if isinstance(r._arguments, str)
                       else json.dumps(r._arguments, ensure_ascii=False), r._reasoning_content, r._content)
        for r in responses
    ]
    _, requests, answer, results = await decide(engine, "in-loop-control", raw_resps)
    assert answer == ["revise_day_plan", "pause_activity", "report_status", "conclude_activity", MAIN_TOOL]
    assert [r.status for r in results] == ["completed", "completed", "completed", "completed", "in_progress"]
    assert engine.context.state.current_time == t_wakeup
    # 每次控制工具都通过 tool 消息把 {ok, result, changes} 喂入下一回合
    for idx in range(1, 5):
        payload = json.loads(requests[idx]["messages"][-1]["content"])
        assert {"ok", "result", "changes"} <= set(payload) and payload["ok"] is True
        assert set(payload) <= {"ok", "result", "changes", "events"}


@pytest.mark.asyncio
async def test_read_queries_share_turn_and_cannot_mix_with_world_tools(engine_factory) -> None:
    """只读查询可在同批同时调 search_memory 与 recall_task_summary，但不能与控制/世界工具混批，第 4 次 search_memory 限流。"""
    engine = engine_factory(mutate=every_five_minutes)
    stage_day_plan(engine)
    await decide(engine, "start-rest", [_ModelResponse("c0", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息")))])
    reach(engine, engine.context.state.current_time + 900)
    act_name = next(iter(engine.context.state.activities.values())).name

    bad_mix = _MultipleModelResponse([
        ("c-q", "search_memory", _SEARCH),
        ("c-r", "revise_day_plan", '{"goals":[]}'),
    ])
    good_queries = _MultipleModelResponse([
        ("c-q1", "search_memory", _SEARCH),
        ("c-q2", "search_memory", _SEARCH),
        ("c-q3", "search_memory", _SEARCH),
        ("c-q4", "search_memory", _SEARCH),
        ("c-rec", "recall_task_summary", json.dumps({"task_name": act_name}, ensure_ascii=False)),
    ])
    finish_turn = _ModelResponse("c-done", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息")))
    conclude_turn = _ModelResponse("c-end", "conclude_activity", json.dumps(_conclude_args(act_name), ensure_ascii=False))

    _, requests, answer, _ = await decide(engine, "query-batch", [bad_mix, good_queries, conclude_turn, finish_turn])
    assert answer == ["conclude_activity", MAIN_TOOL]
    # 第 2 次请求携带混批纠错；第 3 次请求携带本轮 5 条 tool 消息（第 4 条为 MEMORY_SEARCH_LIMIT，第 5 条为 recall 回答）
    assert json.loads(requests[1]["messages"][-1]["content"])["error_code"] == "TOOL_CALL_REQUIRED"
    tool_msgs = [json.loads(m["content"]) for m in requests[2]["messages"] if m["role"] == "tool"][-5:]
    assert len(tool_msgs) == 5 and tool_msgs[3]["error_code"] == "MEMORY_SEARCH_LIMIT"
    assert "error_code" not in tool_msgs[4]


@pytest.mark.asyncio
async def test_output_truncation_and_max_turns_exhaustion_preserve_prefix_and_drain_evidence(engine_factory) -> None:
    """截断重试保持恒定思考、恒定 11 工具与未改前缀；回合预算耗尽抛出 ProtocolError 且可回收未提交的纯证据草稿。"""
    engine = engine_factory(mutate=every_five_minutes)
    stage_day_plan(engine)
    [wakeup] = engine.prepare_barrier((1,))
    sb = engine.open_sandbox(wakeup)
    model = ScriptedModel([TruncatedResponse("反复犹豫……"), TruncatedResponse("再次耗尽……"), TruncatedResponse("第三次……")])
    binding = resolved_binding(model, frozen=engine.context.inputs, max_turns=3)
    with pytest.raises(ProtocolError, match=f"^{TURNS_EXHAUSTED}$"):
        await LifeAgent(person_inputs(engine.context.inputs.persona_by_id[1])).decide_wakeup(wakeup, sb, binding)
    retry_req = model.requests[1]
    assert retry_req["tool_choice"] == "auto"
    assert retry_req["extra_body"] == {"thinking": {"type": "enabled"}}
    assert len(retry_req["tools"]) == 11
    assert retry_req["messages"] == model.requests[0]["messages"]
    assert len(retry_req["messages"]) == 2
    drained = sb.drain_evidence()
    assert [d.event_type for d in drained] == [
        "decision_started", "observation",
        "model_request", "model_response", "model_request", "model_response", "model_request", "model_response",
    ]
    assert [d.data["protocol_feedback"]["error_code"] for d in drained if d.event_type == "model_response"] == [
        "OUTPUT_TRUNCATED", "OUTPUT_TRUNCATED", "OUTPUT_TRUNCATED",
    ]


@pytest.mark.asyncio
async def test_agent_loop_propagates_cancellation_and_distinguishes_completed_vs_suspended() -> None:
    """异步取消 asyncio.CancelledError 原样传播；开始动作返回 completed，search_world 返回 suspended。"""
    async def cancelled_call(_req):
        raise asyncio.CancelledError()

    sandbox_cancel = _SandboxStub()
    with pytest.raises(asyncio.CancelledError):
        await LifeAgent(_person_7()).decide_wakeup(
            _wakeup(), sandbox_cancel, resolved_binding(SimpleNamespace(call=cancelled_call), max_turns=4),
        )
    assert not sandbox_cancel.finished

    sandbox_search = _SandboxStub()
    m_search = ScriptedModel([_ModelResponse("c-sw", SEARCH_TOOL, '{"query":"咖啡","prefer":null}', "搜一下")])
    out_sw = await LifeAgent(_person_7()).decide_wakeup(_wakeup(), sandbox_search, resolved_binding(m_search, max_turns=4))
    assert out_sw.status == "suspended" and sandbox_search.suspended is not None
    assert sandbox_search.suspended.pending_tool_call_id == "c-sw"

    sandbox_pair = _SandboxStub(_step_observation())
    m_pair = ScriptedModel([_MultipleModelResponse([
        ("c-main", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
        ("c-side", SIDE_TOOL, json.dumps(side_arguments("interact", "整理会议纪要"), ensure_ascii=False)),
    ])])
    out_pair = await LifeAgent(_person_7()).decide_wakeup(_wakeup(_step_observation()), sandbox_pair,
                                                          resolved_binding(m_pair, max_turns=4))
    assert out_pair.status == "completed" and sandbox_pair.suspended is None
    assert len(sandbox_pair.calls) == 2
