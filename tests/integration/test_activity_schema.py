"""DeepSeek strict 原生工具 Schema 与本地 Pydantic 合同测试。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from custom.agents.life_agent import (
    FinishRequest,
    LifeAgent,
    MemoryReadRequest,
    MemorySearchRequest,
)
from agentsociety2.agent.base import ReactLoopRequest
from worldmock.life.models import (
    GameActionRequest,
    MeetingMinutes,
    REQUEST_MODELS,
    deepseek_strict_schema,
)
from worldmock.life.work_review import WorkReviewResponse


_UNSUPPORTED_KEYS = {"oneOf", "minLength", "maxLength", "minItems", "maxItems", "default"}


def _prepared_agent() -> LifeAgent:
    """按生产顺序用真实五类目录准备一次人物决策。"""
    data = {
        "observation_event_id": "observation-current",
        "recent_action_results": [],
        "tasks": [{"task_id": "task"}],
        "content_catalog": [{"item_id": "content"}],
        "game_catalog": [{"game_id": "game"}],
        "messages": [{"message_id": "message"}],
        "browse_targets": ["product_catalog"],
    }
    observations = [{"turn": 0, "action": "observe", "ok": True, "data": data}]
    agent = LifeAgent()
    request = ReactLoopRequest(0, datetime(2026, 1, 5, tzinfo=timezone.utc), observations)
    agent.prepare_react_loop(request)
    return agent


def _function(tool_name: str) -> dict[str, Any]:
    """返回模型实际收到的一个 LifeAgent 函数声明。"""
    agent = _prepared_agent()
    if tool_name == "finish":
        agent._life_action_count = 1
    schemas = agent.build_react_tool_schemas(readonly=False)
    return next(item["function"] for item in schemas if item["function"]["name"] == tool_name)


def _assert_closed_objects(value: Any) -> None:
    """递归核对 strict 对象全部必填且禁止额外字段。"""
    if isinstance(value, list):
        for item in value:
            _assert_closed_objects(item)
        return
    if not isinstance(value, dict):
        return
    assert _UNSUPPORTED_KEYS.isdisjoint(value)
    if value.get("type") == "object":
        assert set(value["required"]) == set(value["properties"])
        assert value["additionalProperties"] is False
    for item in value.values():
        _assert_closed_objects(item)


def _assert_action_case(tool_name: str, payload: dict, accepted: bool) -> None:
    """要求服务可表达部分的 JSON Schema 和 Pydantic 接受结果一致。"""
    schema = _function(tool_name)["parameters"]
    model = REQUEST_MODELS[tool_name]
    schema_errors = list(Draft202012Validator(schema).iter_errors(payload))
    if accepted:
        assert schema_errors == []
        model.model_validate_json(json.dumps(payload), strict=True)
        return
    assert schema_errors
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(payload), strict=True)


def test_all_life_tools_use_deepseek_strict_schemas_with_grounded_target_enums() -> None:
    """正式工具保持 strict，目标活动仅增加本轮目录闭集。"""
    agent = _prepared_agent()
    schemas = agent.build_react_tool_schemas(readonly=False)
    agent._life_action_count = 1
    schemas.extend(agent.build_react_tool_schemas(readonly=False))
    functions = {item["function"]["name"]: item["function"] for item in schemas}
    assert len(functions) == 17
    for function in functions.values():
        assert function["strict"] is True
        Draft202012Validator.check_schema(function["parameters"])
        _assert_closed_objects(function["parameters"])
        encoded = json.dumps(function["parameters"])
        assert "$ref" not in encoded and "$defs" not in encoded and "$def" not in encoded
    for name, model in REQUEST_MODELS.items():
        parameters = _function(name)["parameters"]
        if name == "start_targeted_activity":
            properties = parameters["properties"]
            assert properties["activity_type"]["enum"] == [
                "work", "watch", "play_game", "read_mail", "browse",
            ]
            assert properties["target_id"]["enum"] == [
                "content", "game", "message", "product_catalog", "task",
            ]
            continue
        assert parameters == model.model_json_schema(mode="validation")


def test_duration_maximum_is_preserved_in_schema_and_runtime_validation() -> None:
    """计划时长最大值由原始 Schema 与本地模型分别接受和拒绝。"""
    model = REQUEST_MODELS["start_basic_activity"]
    schema = _function("start_basic_activity")["parameters"]
    valid = {"activity_type": "wait", "planned_duration_seconds": 86_400}
    invalid = {"activity_type": "wait", "planned_duration_seconds": 86_401}

    assert schema == model.model_json_schema(mode="validation")
    assert list(Draft202012Validator(schema).iter_errors(valid)) == []
    model.model_validate_json(json.dumps(valid), strict=True)
    assert list(Draft202012Validator(schema).iter_errors(invalid))
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(invalid), strict=True)


def test_old_conditional_tool_names_are_absent() -> None:
    """模型工具闭集删除旧启动、变更与记忆范围名称。"""
    names = {
        item["function"]["name"]
        for item in _prepared_agent().build_react_tool_schemas(readonly=False)
    }
    assert {"start_activity", "change_activity", "memory_range"}.isdisjoint(names)
    assert {"agent_id", "decision_id", "action_id"}.isdisjoint(
        _function("start_basic_activity")["parameters"]["properties"]
    )


def test_nested_life_schemas_are_inlined_from_runtime_models() -> None:
    """工作交付与结束记忆直接内联同一 Pydantic 子模型约束。"""
    deliverable = _function("submit_work")["parameters"]["properties"]["deliverable"]
    finish = _function("finish")["parameters"]["properties"]["memories"]["items"]

    assert deliverable == MeetingMinutes.model_json_schema(mode="validation")
    assert finish["properties"]["text"]["pattern"] == r"\S"
    assert set(finish["required"]) == set(finish["properties"])


@pytest.mark.parametrize(
    ("tool_name", "payload", "accepted"),
    [
        ("start_basic_activity", {"activity_type": "wait", "planned_duration_seconds": 60}, True),
        ("start_basic_activity", {"activity_type": "sleep", "planned_duration_seconds": 60}, True),
        ("start_basic_activity", {"activity_type": "work", "planned_duration_seconds": 60}, False),
        ("start_basic_activity", {"activity_type": "wait"}, False),
        ("start_basic_activity", {
            "activity_type": "wait", "planned_duration_seconds": 60, "target_id": "task",
        }, False),
        ("start_targeted_activity", {
            "activity_type": "work", "planned_duration_seconds": 60,
            "target_id": "task", "playback_speed": 1,
        }, True),
        ("start_targeted_activity", {
            "activity_type": "watch", "planned_duration_seconds": 60,
            "target_id": "content", "playback_speed": 0.5,
        }, True),
        ("start_targeted_activity", {
            "activity_type": "travel", "planned_duration_seconds": 60,
            "target_id": "office", "playback_speed": 1,
        }, False),
        ("start_targeted_activity", {
            "activity_type": "work", "planned_duration_seconds": 60, "playback_speed": 1,
        }, False),
        ("start_targeted_activity", {
            "activity_type": "work", "planned_duration_seconds": 60,
            "target_id": " ", "playback_speed": 1,
        }, False),
        ("start_targeted_activity", {
            "activity_type": "work", "planned_duration_seconds": 60, "target_id": "task",
        }, False),
        ("start_travel", {
            "planned_duration_seconds": 216, "destination_id": "office", "travel_mode": "driving",
        }, True),
        ("start_travel", {"destination_id": "office", "travel_mode": "driving"}, False),
        ("start_travel", {"planned_duration_seconds": 216, "travel_mode": "driving"}, False),
        ("start_travel", {"planned_duration_seconds": 216, "destination_id": "office"}, False),
        ("set_activity_state", {"activity_id": "activity", "operation": "pause"}, True),
        ("set_activity_state", {"activity_id": "activity", "operation": "change_speed"}, False),
        ("change_playback_speed", {"activity_id": "activity", "playback_speed": 1.5}, True),
    ],
)
def test_split_activity_schema_matches_runtime_model(
    tool_name: str,
    payload: dict,
    accepted: bool,
) -> None:
    """拆分后的十八个代表输入由服务 Schema 和运行模型作出同一判断。"""
    _assert_action_case(tool_name, payload, accepted)


@pytest.mark.parametrize(
    ("deliverable", "accepted"),
    [
        ({"meeting_id": "m1", "summary": "Summary", "decisions": [], "action_items": []}, True),
        ({"meeting_id": "m1", "summary": " ", "decisions": [], "action_items": []}, False),
        ({
            "meeting_id": "m1", "summary": "Summary",
            "decisions": [" "], "action_items": [],
        }, False),
        ({
            "meeting_id": "m1", "summary": "Summary",
            "decisions": [], "action_items": [" "],
        }, False),
    ],
)
def test_submit_work_schema_rejects_blank_minutes(deliverable: dict, accepted: bool) -> None:
    """工作成果的嵌套文本约束在服务 Schema 与本地验证中一致。"""
    payload = {"task_id": "task", "submission_id": "submission", "deliverable": deliverable}
    _assert_action_case("submit_work", payload, accepted)


@pytest.mark.parametrize(
    ("model", "tool_name", "payload"),
    [
        (MemoryReadRequest, "memory_read", {"ids": []}),
        (MemoryReadRequest, "memory_read", {"ids": [f"memory-{index}" for index in range(21)]}),
        (MemorySearchRequest, "memory_search", {"query": "q" * 201, "limit": 1}),
        (FinishRequest, "finish", {"memories": []}),
        (GameActionRequest, "game_action", {"game_session_id": "game", "command": "x" * 201}),
    ],
)
def test_provider_array_or_text_limit_gap_remains_a_local_hard_rejection(
    model,
    tool_name: str,
    payload: dict,
) -> None:
    """服务未支持的长度条件不写入 Schema，本地同一请求模型仍硬拒绝。"""
    schema = _function(tool_name)["parameters"]
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(payload), strict=True)


def test_finish_nested_schema_and_runtime_reject_the_same_expressible_error() -> None:
    """内联 finish 条目缺少必填字段时服务 Schema 与 Pydantic 同时拒绝。"""
    agent = LifeAgent()
    agent._life_visible_refs = {"event-visible"}
    agent._life_action_count = 1
    schema = next(
        item["function"]["parameters"]
        for item in agent.build_react_tool_schemas(readonly=False)
        if item["function"]["name"] == "finish"
    )
    memory = {
        "type": "observation",
        "importance": 0.5,
        "keywords": ["work"],
        "text": "I observed a work result.",
        "source": "decision_observation",
        "refs": ["event-visible"],
    }
    payload = {"memories": [{key: value for key, value in memory.items() if key != "importance"}]}

    assert list(Draft202012Validator(schema).iter_errors({"memories": [memory]})) == []
    FinishRequest.model_validate_json(json.dumps({"memories": [memory]}), strict=True)
    assert list(Draft202012Validator(schema).iter_errors(payload))
    with pytest.raises(ValidationError):
        FinishRequest.model_validate_json(json.dumps(payload), strict=True)


def test_work_review_nested_schema_is_inline_and_runtime_aligned() -> None:
    """工作评审的嵌套证据字段无引用方言且与运行解析同判。"""
    schema = deepseek_strict_schema(WorkReviewResponse)
    reference = {
        "reference_id": "summary:0",
        "required_fact_covered": True,
        "source_supported": True,
        "contradiction": False,
        "source_excerpt": "three source words",
        "rationale": "Supported by source.",
    }
    delivery = {
        "delivery_path": "summary",
        "source_supported": True,
        "contradiction": False,
        "source_excerpt": "three source words",
        "rationale": "Supported by source.",
    }
    valid = {"reference_assessments": [reference], "delivery_assessments": [delivery], "accepted": True}
    invalid = json.loads(json.dumps(valid))
    del invalid["reference_assessments"][0]["source_excerpt"]

    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    assert list(Draft202012Validator(schema).iter_errors(valid)) == []
    WorkReviewResponse.model_validate_json(json.dumps(valid), strict=True)
    assert list(Draft202012Validator(schema).iter_errors(invalid))
    with pytest.raises(ValidationError):
        WorkReviewResponse.model_validate_json(json.dumps(invalid), strict=True)


def test_work_review_array_cardinality_remains_a_local_hard_rejection() -> None:
    """服务不支持的评审数组非空条件由同一 Pydantic 模型保留。"""
    payload = {"reference_assessments": [], "delivery_assessments": [], "accepted": False}
    schema = deepseek_strict_schema(WorkReviewResponse)

    assert list(Draft202012Validator(schema).iter_errors(payload)) == []
    with pytest.raises(ValidationError, match="requires reference and delivery"):
        WorkReviewResponse.model_validate_json(json.dumps(payload), strict=True)
