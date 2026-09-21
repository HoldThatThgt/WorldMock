"""DeepSeek strict 原生工具 Schema 与本地 Pydantic 合同测试。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import pytest

from tests.integration.storefront_fixtures import supply_row, discovery_catalog
from jsonschema import Draft202012Validator
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://openrouter.ai/api/v1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from custom.agents.life_agent import (
    FinishRequest,
    LifeAgent,
    MemoryReadRequest,
    MemorySearchRequest,
)
from agentsociety2.agent.base import ReactLoopRequest
from worldmock.catalog.affordances import default_affordances
from worldmock.life.engine import affordance_contract
from worldmock.life.models import (
    GameActionRequest,
    MeetingMinutes,
    REQUEST_MODELS,
    deepseek_strict_schema,
)
from worldmock.life.work_review import WorkReviewResponse


_UNSUPPORTED_KEYS = {"oneOf", "minLength", "maxLength", "minItems", "maxItems", "default"}

#: 真实注册表构造的观察合同冻结块（与引擎 observe 输出同一来源）。
_AFFORDANCE_CONTRACT = affordance_contract(default_affordances())

_OBSERVED_AT = datetime(2026, 1, 5, tzinfo=timezone.utc)
_NOW_EPOCH = int(_OBSERVED_AT.timestamp())

#: 两层观察投影的 facts 段与 scene 段（新提示合同的最小观察输入）。
_FACTS = {
    "current_time": "2026-01-05T00:00:00+00:00",
    "location_id": "home",
    "people_here": [],
    "inbox": [],
    "tasks": [
        {"task_id": "task", "title": "整理会议纪要", "due_at": _NOW_EPOCH + 86400,
         "remaining_contract_seconds": 3600, "new_material_ranges": []},
    ],
    "routes": [{"destination": "office", "mode": "driving", "minutes": 4,
                "fare": 800, "feasible": True}],
    "products": [],
    "inventory": {},
    "day_plan": None,
    "felt": {"appetite": "不饿", "sleepiness": "有点困"},
    "drive_summary": "你有点困。",
    "recent_action_results": [],
}

_SCENE = {
    "current_time": "2026-01-05T00:00:00+00:00",
    "location": "自己家",
    "people_here": [],
    "felt": {"appetite": "不饿", "sleepiness": "有点困"},
    "current_activity": {},
    "paused_activities": [],
    "day_plan": None,
    "inbox_head": [],
    "todos_summary": "",
    "reachable_places": [],
    "inventory": [],
    "recent_action_results": [],
}


def _prepared_agent(tool_name: str | None = None) -> LifeAgent:
    """按生产顺序用真实五类目录准备一次人物决策。"""
    data = {
        "observation_event_id": "observation-current",
        "current_time": _NOW_EPOCH,
        "wake_reasons": [],
        "scene": dict(_SCENE),
        "facts": dict(_FACTS),
        "recent_action_results": [],
        "active_activity": None,
        "paused_activities": [],
        "energy_feasibility": {"maximum_duration_seconds_by_kind": {
            "work": 86_400, "watch": 86_400, "play_game": 86_400,
            "read_mail": 86_400, "browse": 86_400,
        }},
        "affordance_contract": _AFFORDANCE_CONTRACT,
        "interaction_menu": {
            "allowed_tools": sorted(REQUEST_MODELS),
            "allowed_activity_kinds": sorted(_AFFORDANCE_CONTRACT),
        },
        "tasks": [{"task_id": "task", "remaining_contract_seconds": 3600,
                   "material": {"end_char": 10, "total_chars": 10}}],
        "activity_summary": {"feature_film": 1, "sport": 44, "foods": 1},
        "content_progress": {"content": {
            "item_id": "content", "position_seconds": 0.0,
            "remaining_media_seconds": 630.0, "exposure_count": 0,
        }},
        "game_catalog": [{"game_id": "game"}],
        "messages": [{"message_id": "message"}],
        "browse_targets": ["product_catalog"],
        "products": [{"product_id": "product", "use_modes": ["eat"],
                      "use_duration_seconds": 300, "required_location_id": None}],
        "supplies": [supply_row(supply_id="supply", product_id="product")],
        "discovery_catalog": discovery_catalog("product"),
        "locations": [{"location_id": "home"}, {"location_id": "office"}],
        "location_id": "home",
        "available_inventory": {"product": 1.0},
        "inventory_items": [{"product_id": "product", "name": "现有食品", "use_modes": ["eat"],
                             "use_duration_seconds": 300, "required_location_id": None}],
        "routes": [{"destination_id": "office", "status": "reachable", "mode": "driving", "duration_seconds": 216,
                    "minimum_energy_cost_kcal": 6.0, "energy_feasible": True}],
        "games": {},
    }
    _set_activity_boundary(data, tool_name)
    observations = [{"turn": 0, "action": "observe", "ok": True, "data": data}]
    agent = LifeAgent()
    request = ReactLoopRequest(0, _OBSERVED_AT, observations)
    agent.prepare_react_loop(request)
    return agent


def _function(tool_name: str) -> dict[str, Any]:
    """返回模型实际收到的一个 LifeAgent 函数声明。"""
    agent = _prepared_agent(tool_name)
    if tool_name == "finish":
        agent._life_action_count = 1
    schemas = agent.build_react_tool_schemas(readonly=False)
    return next(item["function"] for item in schemas if item["function"]["name"] == tool_name)


def _set_activity_boundary(data: dict, tool_name: str | None) -> None:
    """为活动控制工具提供与生产观察一致的当前边界。"""
    kinds = {"set_activity_state": "wait", "continue_activity": "wait",
             "change_playback_speed": "watch", "game_action": "play_game"}
    if tool_name not in kinds:
        _set_new_tool_visibility(data, tool_name)
        return
    activity = {"activity_id": "activity", "status": "active", "kind": kinds[tool_name], "data": {}}
    if tool_name == "game_action":
        activity["data"] = {"game_session_id": "game"}
        data["games"] = {"game": {
            "session_id": "game", "admissible_commands": ["look", "x" * 201],
            "won": False, "lost": False,
        }}
    data["active_activity"] = activity


def _set_new_tool_visibility(data: dict, tool_name: str | None) -> None:
    """为修订合同四件新工具提供与生产观察一致的可见身份。"""
    if tool_name == "plan_day":
        data["wake_reasons"] = ["run_start"]
    elif tool_name == "revise_day_plan":
        data["facts"] = {**data["facts"], "day_plan": {"day_end": _NOW_EPOCH + 72000, "goals": []}}
    elif tool_name in ("send_message", "start_social_activity"):
        data["facts"] = {**data["facts"], "inbox": [
            {"id": "m-1", "kind": "direct", "sender_id": "agent:9",
             "head": "在吗", "read": False, "created_at": _NOW_EPOCH - 60}]}


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
    schemas = [
        {"function": _function(name)}
        for name in REQUEST_MODELS
    ]
    agent._life_action_count = 1
    schemas.extend(agent.build_react_tool_schemas(readonly=False))
    functions = {item["function"]["name"]: item["function"] for item in schemas}
    assert len(functions) == 23  # 16 生活动作（含 discover_places）+ 6 记忆 + finish
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
        base = model.model_json_schema(mode="validation")
        assert set(parameters["properties"]) == set(base["properties"])
        assert parameters["required"] == base["required"]
        if name == "start_basic_activity":
            assert parameters["properties"]["activity_type"]["enum"] == ["wait", "sleep"]
            assert parameters["properties"]["activity_type"]["pattern"] == (
                base["properties"]["activity_type"]["pattern"])


def test_duration_bounds_are_preserved_in_schema_and_runtime_validation() -> None:
    """计划时长上下界由原始 Schema 与本地模型分别接受和拒绝。"""
    model = REQUEST_MODELS["start_basic_activity"]
    schema = _function("start_basic_activity")["parameters"]
    valid = {"activity_type": "wait", "planned_duration_seconds": 86_400}
    too_long = {"activity_type": "wait", "planned_duration_seconds": 86_401}
    too_short = {"activity_type": "wait", "planned_duration_seconds": 59}

    base = model.model_json_schema(mode="validation")
    assert schema["properties"]["activity_type"]["enum"] == ["wait", "sleep"]
    assert schema["properties"]["planned_duration_seconds"] == (
        base["properties"]["planned_duration_seconds"])
    assert list(Draft202012Validator(schema).iter_errors(valid)) == []
    model.model_validate_json(json.dumps(valid), strict=True)
    for invalid in (too_short, too_long):
        assert list(Draft202012Validator(schema).iter_errors(invalid))
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(invalid), strict=True)

    consume = REQUEST_MODELS["consume"]
    invalid_consume = {
        "items": [{"product_id": "product", "quantity": 1}],
        "use_mode": "eat",
        "duration_seconds": 59,
    }
    assert list(Draft202012Validator(consume.model_json_schema()).iter_errors(invalid_consume))
    with pytest.raises(ValidationError):
        consume.model_validate_json(json.dumps(invalid_consume), strict=True)


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


def test_tuple_request_models_inline_subschemas_without_adapter() -> None:
    """四件含元组字段的请求模型在模型层内联子 Schema（strict 合同，docs/CONTRACTS.md「人物工具与管理接口」）。

    purchase.items/consume.items/plan_day.goals/revise_day_plan.goals
    的子模型约束以 WithJsonSchema 注解进 life.models 字段定义，生成的
    strict Schema 不含嵌套引用键，agent 侧适配层已删除。
    """
    import custom.agents.life_agent as life_agent_module
    from worldmock.life.models import ConsumeItem, PlanGoalInput, PurchaseItem

    subs = {"purchase": PurchaseItem, "consume": ConsumeItem,
            "plan_day": PlanGoalInput, "revise_day_plan": PlanGoalInput}
    fields = {"purchase": "items", "consume": "items",
              "plan_day": "goals", "revise_day_plan": "goals"}
    for name, field in fields.items():
        schema = deepseek_strict_schema(REQUEST_MODELS[name])
        encoded = json.dumps(schema)
        assert "$ref" not in encoded and "$defs" not in encoded, name
        node = schema["properties"][field]
        assert node["type"] == "array", name
        assert node["items"] == subs[name].model_json_schema(mode="validation"), name
        assert set(node["items"]["required"]) == set(node["items"]["properties"]), name
    # 反例：agent 侧的 _request_tool_model 适配层不再存在（单一事实源）。
    assert not hasattr(life_agent_module, "_request_tool_model")


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
        ({"summary": "Summary", "decisions": [], "action_items": []}, True),
        ({"summary": " ", "decisions": [], "action_items": []}, False),
        ({
            "summary": "Summary",
            "decisions": [" "], "action_items": [],
        }, False),
        ({
            "summary": "Summary",
            "decisions": [], "action_items": [" "],
        }, False),
    ],
)
def test_submit_work_schema_rejects_blank_minutes(deliverable: dict, accepted: bool) -> None:
    """工作成果的嵌套文本约束在服务 Schema 与本地验证中一致。"""
    payload = {"task_id": "task", "deliverable": deliverable}
    _assert_action_case("submit_work", payload, accepted)


@pytest.mark.parametrize(
    ("model", "tool_name", "payload"),
    [
        (MemoryReadRequest, "memory_read", {"ids": []}),
        (MemoryReadRequest, "memory_read", {"ids": [f"memory-{index}" for index in range(21)]}),
        (MemorySearchRequest, "memory_search", {"query": "q" * 201, "limit": 1}),
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


def test_finish_accepts_empty_memories_list_in_schema_and_runtime() -> None:
    """记忆合同：finish 空记忆列表合法（无新事件时允许零条）。"""
    from agentsociety2.agent.base import ReactDecision

    schema = _function("finish")["parameters"]
    assert list(Draft202012Validator(schema).iter_errors({"memories": []})) == []
    FinishRequest.model_validate_json('{"memories": []}', strict=True)

    agent = _prepared_agent()
    agent._life_action_count = 1
    agent.build_react_tool_schemas(readonly=False)
    assert agent.validate_react_decisions(
        [ReactDecision("", "finish", {"memories": []}, "")], False) == ""


def test_memory_text_rejects_hexadecimal_identities() -> None:
    """记忆 text 使用人物语言：事件标识/请求哈希/十六进制身份本地拒绝。"""
    episode = {
        "type": "event",
        "importance": 0.5,
        "keywords": ["meeting"],
        "text": "We met at the usual place and talked about the weekend plans.",
        "source": "decision_observation",
        "refs": ["observation-current"],
    }
    FinishRequest.model_validate_json(
        json.dumps({"memories": [episode]}), strict=True)

    for poisoned in (
        "事件 d41d8cd98f00b204e9800998ecf8427e 已经发生",
        "request 88f3ab17 hash",
    ):
        invalid = {**episode, "text": poisoned}
        assert list(Draft202012Validator(
            _function("finish")["parameters"]).iter_errors({"memories": [invalid]})) == []
        with pytest.raises(ValidationError):
            FinishRequest.model_validate_json(
                json.dumps({"memories": [invalid]}), strict=True)


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
