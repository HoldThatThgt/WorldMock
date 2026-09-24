"""人物稳定工具面的 DeepSeek strict Schema 与运行模型合同。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from custom.agents.life_agent import FinishRequest, LifeAgent, MemoryReadRequest, MemorySearchRequest
from tests.integration.test_life_agent_protocol import _OBSERVED_AT, _observations
from worldmock.life.models import MeetingMinutes, REQUEST_MODELS, deepseek_strict_schema
from worldmock.life.work_review import WorkReviewResponse


_UNSUPPORTED_KEYS = {"oneOf", "minLength", "maxLength", "minItems", "maxItems", "default"}
_ACTION_TOOLS = {
    "start_activity", "submit_work", "send_message", "recall_task_summary",
    "report_status", "pause_activity", "conclude_activity", "plan_day", "revise_day_plan",
}


def _prepared_agent(revise: bool = False) -> LifeAgent:
    """用本轮公开事实及私有身份绑定准备实际人物工具。"""
    observations = deepcopy(_observations())
    data = observations[0]["data"]
    data["facts"]["interactive_objects"].append(
        {"object": "同事", "actions": ["交谈"], "preconditions_met": True})
    data["interaction_bindings"].append({
        "object": "同事", "kind": "person", "object_id": "agent:9",
        "actions": [{"label": "交谈", "canonical_action": "chat"}],
    })
    data["facts"]["progress"]["sessions"] = [{
        "task_name": "等待", "session_name": "等待（上午十点，家中）", "phase": "进行中",
        "objective_progress": "已等待一分钟", "subjective_progress": "", "subjective_status": "",
    }]
    data["session_bindings"] = [{
        "task_name": "等待", "session_name": "等待（上午十点，家中）",
        "session_id": "session-visible", "status": "active",
    }]
    if revise:
        data["facts"]["day_plan"] = {"goals": []}
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, _OBSERVED_AT, observations))
    return agent


def _function(name: str) -> dict[str, Any]:
    """取得模型本轮真正可见的函数声明。"""
    agent = _prepared_agent(revise=name == "revise_day_plan")
    if name == "finish":
        agent._life_action_count = 1
    return next(item["function"] for item in agent.build_react_tool_schemas(readonly=False)
                if item["function"]["name"] == name)


def _assert_closed(node: Any) -> None:
    """递归确认 strict 对象字段全必填且没有未知字段。"""
    if isinstance(node, list):
        for item in node:
            _assert_closed(item)
        return
    if not isinstance(node, dict):
        return
    assert _UNSUPPORTED_KEYS.isdisjoint(node)
    if node.get("type") == "object":
        assert set(node["required"]) == set(node["properties"])
        assert node["additionalProperties"] is False
    for item in node.values():
        _assert_closed(item)


def test_all_action_tools_use_closed_strict_schemas_from_one_model() -> None:
    """全部九件动作工具采用同一请求模型，身份与执行参数不进工具面。"""
    assert set(REQUEST_MODELS) == _ACTION_TOOLS
    for name, model in REQUEST_MODELS.items():
        function = _function(name)
        schema = function["parameters"]
        assert function["strict"] is True
        Draft202012Validator.check_schema(schema)
        _assert_closed(schema)
        assert "$ref" not in json.dumps(schema) and "$defs" not in json.dumps(schema)
        base = model.model_json_schema(mode="validation")
        assert set(schema["properties"]) == set(base["properties"])
        assert schema["required"] == base["required"]


def test_start_activity_uses_readable_frozen_objects_and_paired_actions() -> None:
    """模型只选择公开对象和动作；并列动作逐项配对。"""
    schema = _function("start_activity")["parameters"]
    assert set(schema["properties"]) == {"targets", "actions"}
    assert schema["properties"]["targets"]["items"]["enum"] == ["同事", "整理会议纪要", "自己"]
    assert schema["properties"]["actions"]["items"]["enum"] == ["交谈", "工作", "睡觉", "等待"]
    assert {"agent_id", "decision_id", "action_id", "target_id", "planned_duration_seconds",
            "price_fen", "price_version", "quantity"}.isdisjoint(schema["properties"])
    valid = {"targets": ["自己", "整理会议纪要"], "actions": ["等待", "工作"]}
    assert list(Draft202012Validator(schema).iter_errors(valid)) == []
    REQUEST_MODELS["start_activity"].model_validate(valid)
    assert list(Draft202012Validator(schema).iter_errors(
        {"targets": ["internal:task:1"], "actions": ["工作"]}))


@pytest.mark.parametrize("name", ["report_status", "pause_activity", "conclude_activity"])
def test_session_control_schemas_use_paired_readable_task_and_summary(name: str) -> None:
    """三条会话控制工具均要求逐项任务与人物自述。"""
    schema = _function(name)["parameters"]
    assert set(schema["properties"]) == {"task", "summary"}
    assert schema["properties"]["task"]["items"]["enum"] == ["等待"]
    assert schema["properties"]["summary"]["items"]["type"] == "string"
    valid = {"task": ["等待"], "summary": ["我还想等一会儿。"]}
    assert list(Draft202012Validator(schema).iter_errors(valid)) == []
    REQUEST_MODELS[name].model_validate(valid)
    for invalid in ({"task": ["等待"], "summary": []},
                    {"task": ["等待", "等待"], "summary": ["a", "b"]},
                    {"task": ["等待"], "summary": [" "]}):
        with pytest.raises(ValidationError):
            REQUEST_MODELS[name].model_validate(invalid)


def test_named_content_tools_bind_visible_names_and_inline_deliverable() -> None:
    """工作与通讯保留正文生成，但身份字段采用可读名称。"""
    submit = _function("submit_work")["parameters"]
    message = _function("send_message")["parameters"]
    recall = _function("recall_task_summary")["parameters"]
    assert set(submit["properties"]) == {"task_name", "deliverable"}
    assert submit["properties"]["task_name"]["enum"] == ["整理会议纪要"]
    assert submit["properties"]["deliverable"] == MeetingMinutes.model_json_schema(mode="validation")
    assert set(message["properties"]) == {"recipient_name", "text"}
    assert message["properties"]["recipient_name"]["enum"] == ["同事"]
    assert set(recall["properties"]) == {"task_name"}
    assert "整理会议纪要" in recall["properties"]["task_name"]["enum"]


@pytest.mark.parametrize("name", ["plan_day", "revise_day_plan"])
def test_day_plan_schema_inlines_goal_without_timing_fields(name: str) -> None:
    """目标仅接收意图和文字，不向模型索要时长。"""
    schema = _function(name)["parameters"]
    assert set(schema["properties"]) == {"goals"}
    item = schema["properties"]["goals"]["items"]
    assert set(item["properties"]) == {"intent_kind", "goal"}
    assert item["properties"]["goal"]["pattern"] == r"\S"


def test_provider_array_cardinality_gap_is_locally_rejected() -> None:
    """供应商不支持数组长度约束时，本地模型仍拒绝越界请求。"""
    schema = _function("report_status")["parameters"]
    invalid = {"task": [], "summary": []}
    assert list(Draft202012Validator(schema).iter_errors(invalid)) == []
    with pytest.raises(ValidationError):
        REQUEST_MODELS["report_status"].model_validate(invalid)
    memory = {"ids": []}
    assert list(Draft202012Validator(_function("memory_read")["parameters"]).iter_errors(memory)) == []
    with pytest.raises(ValidationError):
        MemoryReadRequest.model_validate(memory)
    search = {"query": "q" * 201, "limit": 1}
    assert list(Draft202012Validator(_function("memory_search")["parameters"]).iter_errors(search)) == []
    with pytest.raises(ValidationError):
        MemorySearchRequest.model_validate(search)


def test_finish_and_work_review_nested_schemas_remain_strict() -> None:
    """结束记忆和工作评审的嵌套数据仍由原生严格 Schema 约束。"""
    finish = _function("finish")["parameters"]
    assert list(Draft202012Validator(finish).iter_errors({"memories": []})) == []
    FinishRequest.model_validate({"memories": []})
    agent = _prepared_agent()
    agent._life_action_count = 1
    agent.build_react_tool_schemas(readonly=False)
    assert agent.validate_react_decisions(
        [ReactDecision("", "finish", {"memories": []}, "")], False) == ""
    review = deepseek_strict_schema(WorkReviewResponse)
    _assert_closed(review)
    assert "$ref" not in json.dumps(review)
    with pytest.raises(ValidationError):
        WorkReviewResponse.model_validate({
            "reference_assessments": [], "delivery_assessments": [], "accepted": False})


@pytest.mark.parametrize("retired_type", ["event", "observation"])
def test_finish_rejects_fact_like_memory_types(retired_type: str) -> None:
    """人物记忆不接受事实型事件记录。"""
    memory = {"type": retired_type, "epistemic_status": "subjective", "importance": 0.5,
              "keywords": ["生活"], "text": "我以为今天已经吃过饭", "source": "decision_observation",
              "refs": ["observation-current"]}
    payload = {"memories": [memory]}
    assert list(Draft202012Validator(_function("finish")["parameters"]).iter_errors(payload))
    with pytest.raises(ValidationError):
        FinishRequest.model_validate(payload)
