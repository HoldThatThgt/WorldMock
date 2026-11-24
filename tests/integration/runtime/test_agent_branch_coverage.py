"""当前工具协议的观察冻结、工具面稳定、沙盒越界守卫与检索记忆错误路径。"""

from __future__ import annotations

import copy
import json
import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory  # noqa: F401
from tests.integration.runtime.current_contract_fixtures import ScriptedModel, decide
from tests.integration.runtime.test_life_agent_protocol import (
    _ModelResponse,
    _NEWS,
    _SandboxStub,
    _WAIT,
    _WAIT_CHOICE,
    _WORK,
    _WORK_ACTION,
    _WORK_CHOICE,
    _binding,
    _conclude_args,
    _functions,
    _memory,
    _observations,
    _person_7,
    _step_observation,
    _wakeup,
    _with_activities,
)
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, conclude_request, main_arguments, stage_day_plan
from worldmock.agents.life_agent import LifeAgent, _bind_query_error_paths, _life_turn_tools, validate_life_batch
from worldmock.agents.protocol.agent_loop import LoopToolCall, tool_message, validate_single_tool_call
from worldmock.foundation.errors import ProtocolError
from worldmock.simulation_engine.activities.lifecycle.activity_control import PHASE_ACTIVE, PHASE_PAUSED
from worldmock.state.interaction.requests import ConclusionMemory, REQUEST_MODELS


def test_memory_entries_and_finish_keep_content_limits() -> None:
    """收尾记忆条目保持内容上限；记忆不带 refs，来源不由模型填写。"""
    memory = _memory("我等了一会儿。")
    assert ConclusionMemory(**memory).text == "我等了一会儿。"
    with pytest.raises(ValidationError):
        ConclusionMemory(**dict(memory, text="长" * 1001))
    for extra in ({"refs": ["observation-current"]}, {"source": "decision_observation"}):
        with pytest.raises(ValidationError):
            ConclusionMemory(**memory, **extra)
    request = conclude_request(_WAIT, summary="收尾。", memories=[[]])
    assert [(item.memories, item.views) for item in request.items] == [([], [])]
    with pytest.raises(ValidationError):
        conclude_request(_WAIT, summary="收尾。", memories=[[ConclusionMemory(**memory) for _ in range(9)]])


def _misaligned_meta(data: dict) -> None:
    """进度层有一条记录，私有元数据却为空。"""
    data["progress_layer"]["events"].append({"occurred_at": "2026-01-05T00:00:00+00:00", "type": "plan_reminder",
                                             "goal": "工作"})


@pytest.mark.parametrize("change,match", [
    (lambda data: data.pop("observation_event_id"), "observation event identity"),
    (lambda data: data.update(observation_event_id=""), "observation event identity"),
    (lambda data: data.pop("tool_layer"), "observation layer tool_layer is unavailable"),
    (lambda data: data["todo_layer"].update(layer="工具层"), "observation layer todo_layer is unavailable"),
    (lambda data: data.pop("memory_layer"), "observation layer memory_layer is unavailable"),
    (lambda data: data.update(environment_layer=[]), "observation layer environment_layer is unavailable"),
    (lambda data: data.pop("progress_layer"), "observation layer progress_layer is unavailable"),
    (_misaligned_meta, "progress metadata is invalid"),
    (lambda data: data.update(progress_meta=[{"protected": "yes", "lines": []}]), "progress metadata is invalid"),
    (lambda data: data.update(wake_reasons=[""]), "wake reasons are invalid"),
    (lambda data: data.update(current_time=-1), "current time is invalid"),
])
@pytest.mark.asyncio
async def test_corrupt_observation_fails_before_any_model_tool(change, match: str) -> None:
    """缺层、层名不符、进度元数据损坏、时钟或唤醒原因无效时在任何模型调用之前失败。"""
    data = copy.deepcopy(_observations()[0]["data"])
    wakeup = _wakeup(copy.deepcopy(data))
    change(data)
    change(wakeup.observation)
    sandbox = _SandboxStub(data)
    model = ScriptedModel([])
    with pytest.raises(RuntimeError, match=match):
        await LifeAgent(_person_7()).decide_wakeup(wakeup, sandbox, _binding(model, max_turns=4))
    assert model.requests == []


@pytest.mark.asyncio
async def test_unknown_target_in_sandbox_is_rejected_and_retried_in_same_wakeup(engine_factory) -> None:
    """未知对象名在沙盒派发处被业务规则拒绝并把结果消息喂回同一连续循环，改为修订空计划后再开始动作顺利提交。"""
    engine = engine_factory()
    stage_day_plan(engine)
    wrong = _ModelResponse("call-unseen", MAIN_TOOL,
                           json.dumps(main_arguments("interact", "不存在的家", "歇一会儿"), ensure_ascii=False))
    revise = _ModelResponse("call-revise", "revise_day_plan", '{"goals":[]}')
    rest = _ModelResponse("call-rest", MAIN_TOOL,
                          json.dumps(main_arguments("interact", "家", "测试休息"), ensure_ascii=False))

    _, requests, answer, results = await decide(engine, "sandbox-reject-retry", [wrong, revise, rest])

    assert answer == [MAIN_TOOL, "revise_day_plan", MAIN_TOOL]
    assert [r.status for r in results] == ["rejected", "completed", "in_progress"]
    rejected_payload = json.loads(requests[1]["messages"][-1]["content"])
    assert rejected_payload["result"]["error_code"] == "NAME_NOT_FOUND"
    assert len(requests) == 3
    for tool, actions in ((MAIN_TOOL, ["travel", "interact", "sleep"]), (SIDE_TOOL, ["purchase", "interact"])):
        offered = {item["function"]["name"]: item["function"] for item in requests[0]["tools"]}
        properties = offered[tool]["parameters"]["properties"]
        assert properties["action"]["enum"] == actions
        assert all("enum" not in json.dumps(properties[field]) for field in properties if field != "action")


def test_tool_surface_is_stable_across_observation_activities() -> None:
    """全量九个原生工具整次运行保持稳定，私有身份不进 Schema；批次和请求校验只核对通用批次及参数结构。"""
    _, models = _life_turn_tools(2, False)
    functions = _functions(2, False)
    assert set(REQUEST_MODELS) <= set(functions)
    assert not {"purchase", "consume", "set_activity_state", "discover_places", "finish"} & set(functions)
    for call in (
        LoopToolCall(SIDE_TOOL, _WORK_CHOICE, "c1"),
        LoopToolCall(MAIN_TOOL, _WAIT_CHOICE, "c2"),
        LoopToolCall("pause_activity", {"items": [{"activity_name": _WORK, "summary": "先停。"}], "errands": []}, "c3"),
        LoopToolCall("report_status", {"items": [{"activity_name": _NEWS, "summary": "接着看。"}],
                                        "errands": []}, "c4"),
        LoopToolCall("conclude_activity", _conclude_args(_WORK, _NEWS, memories=[[], []]), "c5"),
        LoopToolCall("recall_task_summary", {"task_name": _NEWS}, "c6"),
    ):
        assert validate_single_tool_call(call, models) is None
        assert validate_life_batch([call], models) is None
    assert "TOOL_CALL_REQUIRED: " in validate_single_tool_call(
        LoopToolCall("send_message", {"recipient_name": "陌生人", "text": "你好"}, "c7"), models).text
    assert "activity-private-1" not in str(functions)
    assert "session-7-0001" not in str(functions)


def test_life_agent_sandbox_guards_fail_closed_on_unsupported_tool(engine_factory) -> None:
    """宿主沙盒调度越界工具时以 ProtocolError 明确拒绝，不分派。"""
    engine = engine_factory()
    stage_day_plan(engine)
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    for bad in ("start_activity", "finish", "memory_step_range", "recall_task_summary"):
        with pytest.raises(ProtocolError, match="Unsupported sandbox world tool"):
            sandbox.dispatch_world_tool(bad, {}, "act-bad")


def test_tool_messages_and_query_error_paths() -> None:
    """tool_message 紧凑序列化只读查询结果，_bind_query_error_paths 将失败路径改写到真实调用位置。"""
    success = {"类别": "全部", "时间": "今天", "关键词": None, "结果": [], "未列出": []}
    msg_ok = tool_message("call-1", success)
    assert json.loads(msg_ok["content"]) == success
    refused = {
        "error_code": "MEMORY_TIME_INVALID", "reason": "bad time", "fix": "fix it", "unmet": [],
        "items": [{"path": ["tool_calls", 0, "function", "arguments", "time"], "written": "2026-02-30",
                   "error_code": "MEMORY_TIME_INVALID", "allowed_values": [], "expected": "e", "why": "w",
                   "candidates": []}],
    }
    rebound = _bind_query_error_paths(refused, 1)
    msg_err = tool_message("call-2", rebound)
    feedback = json.loads(msg_err["content"])
    assert set(feedback) == {"error_code", "reason", "fix", "items", "unmet"}
    assert feedback["error_code"] == "MEMORY_TIME_INVALID"
    assert feedback["items"][0]["path"] == ["tool_calls", 1, "function", "arguments", "time"]
