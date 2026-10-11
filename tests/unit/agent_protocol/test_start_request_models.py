"""两个开始工具的严格请求模型：字段全必填、交叉约束逐条定位、DeepSeek strict Schema 与用途句逐字。"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from worldmock.agents.life_prompt import TOOL_DESCRIPTIONS
from worldmock.life.action_contract import (
    ACTIVITY_ACTIONS, MAIN_ACTIVITY_ACTIONS, SIDE_ACTIVITY_ACTIONS, START_TOOLS, activity_slot,
)
from worldmock.life.models import (
    REQUEST_MODELS, StartMainActivityRequest, StartSideActivityRequest, deepseek_strict_schema,
)
from worldmock.life.request_errors import argument_report
from worldmock.life.sleep_plan import SLEEP_MINUTES_REASON

_MAIN_VALID = (
    {"action": "travel", "target": "公司", "detail": "驾车"},
    {"action": "interact", "target": "家", "detail": "测试休息"},
    {"action": "sleep", "target": "470", "detail": None},
)
_SIDE_VALID = (
    {"action": "purchase", "target": ["牛肉面", "柠檬茶", "柠檬茶"]},
    {"action": "interact", "target": ["哔哩哔哩"]},
)


def _report(model, tool: str, arguments) -> list[tuple]:
    """严格校验失败时宿主与环境共用的明细：（参数内路径，所写原值，允许值，原因）。"""
    with pytest.raises(ValidationError) as caught:
        model.model_validate(arguments)
    report = argument_report(tool, caught.value, model, arguments)
    assert report.error_code == "ARGUMENTS_INVALID" and report.fix == "Correct every reported parameter."
    assert report.reason == "; ".join(item.why for item in report.items)
    return [(item.path, item.written, item.allowed_values, item.why) for item in report.items]


def test_action_closed_sets_and_start_tools_are_declared_once():
    """动作闭集、两个工具的枚举与工具次序只在动作合同声明，两个开始工具按此顺序排在请求模型最前。"""
    assert ACTIVITY_ACTIONS == ("travel", "purchase", "sleep", "interact")
    assert MAIN_ACTIVITY_ACTIONS == ("travel", "interact", "sleep")
    assert SIDE_ACTIVITY_ACTIONS == ("purchase", "interact")
    assert START_TOOLS == ("start_main_activity", "start_side_activity")
    assert list(REQUEST_MODELS) == [*START_TOOLS, "recall_task_summary", "report_status", "pause_activity",
                                    "conclude_activity", "revise_day_plan"]
    assert REQUEST_MODELS["start_main_activity"] is StartMainActivityRequest
    assert REQUEST_MODELS["start_side_activity"] is StartSideActivityRequest
    assert "start_activity" not in REQUEST_MODELS and "start_activity" not in TOOL_DESCRIPTIONS


@pytest.mark.parametrize("action,app_id,slot", [
    ("travel", None, "first"), ("sleep", None, "first"), ("interact", None, "first"),
    ("purchase", None, "second"), ("interact", "bilibili", "second"),
    ("walk", None, "first"), ("drive", None, "first"), ("take_taxi", None, "first"), ("take_bus", None, "first"),
])
def test_slot_is_given_by_the_action_and_the_content_app(action, app_id, slot):
    """出行、睡眠和在地点做事占槽位 1，购买和用内容 app 占槽位 2；四个固定出行动作仍记在出行活动上。"""
    assert activity_slot(action, app_id) == slot


@pytest.mark.parametrize("action", ["wait", "do", "travel_to", "Sleep", "interact ", ""])
def test_unknown_action_cannot_acquire_a_slot(action):
    with pytest.raises(ValueError, match="Unknown activity action"):
        activity_slot(action, None)


@pytest.mark.parametrize("arguments", _MAIN_VALID)
def test_main_request_accepts_the_three_declared_shapes(arguments):
    request = StartMainActivityRequest.model_validate(arguments)
    assert request.model_dump(mode="json") == arguments


@pytest.mark.parametrize("arguments", _SIDE_VALID)
def test_side_request_accepts_arrays_and_repeated_names(arguments):
    """购买不设上限并允许同名重复：同一个名称写两次就是两份。"""
    request = StartSideActivityRequest.model_validate(arguments)
    assert request.model_dump(mode="json") == arguments
    many = {"action": "purchase", "target": ["薯片"] * 12}
    assert StartSideActivityRequest.model_validate(many).target == ["薯片"] * 12


def test_both_models_generate_closed_strict_schemas_with_verbatim_descriptions():
    """两个模型都通过 DeepSeek strict 校验；每个参数都必填，用途句与字段说明逐字。"""
    main = deepseek_strict_schema(StartMainActivityRequest)
    side = deepseek_strict_schema(StartSideActivityRequest)
    for schema in (main, side):
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False and schema["required"] == list(schema["properties"])
        assert "$ref" not in json.dumps(schema) and "description" not in schema
    assert list(main["properties"]) == ["action", "target", "detail"]
    assert main["properties"]["action"]["enum"] == ["travel", "interact", "sleep"]
    assert main["properties"]["target"]["type"] == "string" and main["properties"]["target"]["pattern"] == r"\S"
    assert main["properties"]["detail"]["anyOf"] == [{"pattern": r"\S", "type": "string"}, {"type": "null"}]
    assert list(side["properties"]) == ["action", "target"]
    assert side["properties"]["action"]["enum"] == ["purchase", "interact"]
    assert side["properties"]["target"]["type"] == "array"
    assert side["properties"]["target"]["items"] == {"pattern": r"\S", "type": "string"}
    assert TOOL_DESCRIPTIONS["start_main_activity"] == "开始一项占槽位 1 的活动：出行、在当前地点做一件事，或睡觉。"
    assert TOOL_DESCRIPTIONS["start_side_activity"] == "开始一项占槽位 2 的活动：购买商品，或使用一个内容 app。"
    assert main["properties"]["action"]["description"] == "做什么：travel 出行，interact 在当前地点做一件事，sleep 睡觉。"
    assert main["properties"]["target"]["description"] == (
        "travel 写目的地地点的完整名称；interact 写当前所在地点的完整名称；sleep 写要睡的分钟数，只写数字，例如 470。")
    assert main["properties"]["detail"]["description"] == (
        "travel 写出行方式的名称，照工具层该目的地下列出的写；interact 写要做的那件事的名称，"
        "照工具层当前地点下列出的写；sleep 写 null。")
    assert side["properties"]["action"]["description"] == "做什么：purchase 购买商品，interact 使用内容 app。"
    assert side["properties"]["target"]["description"] == (
        "名称数组。purchase 逐份写商品的完整名称，同一个名称写两次就是两份；interact 只写一个内容 app 的完整名称。")
    for arguments in _MAIN_VALID:
        assert list(Draft202012Validator(main).iter_errors(arguments)) == []
    for arguments in _SIDE_VALID:
        assert list(Draft202012Validator(side).iter_errors(arguments)) == []


@pytest.mark.parametrize("model,tool,valid", [
    (StartMainActivityRequest, "start_main_activity", _MAIN_VALID[0]),
    (StartSideActivityRequest, "start_side_activity", _SIDE_VALID[0]),
])
def test_every_parameter_is_required_and_extra_keys_are_refused(model, tool, valid):
    """每个参数都必须出现在请求里；多写任何键按多余字段拒绝，旧的 items 形状同样被拒。"""
    for field in valid:
        missing = {key: value for key, value in valid.items() if key != field}
        assert _report(model, tool, missing) == [((field,), None, _allowed(model, field),
                                                  "This required field was omitted.")]
    extra = {**valid, "way": "步行"}
    assert _report(model, tool, extra) == [(("way",), "步行", (), "This field is not declared and must be removed.")]
    retired = {"items": [{"action": "walk", "target": "公司"}]}
    paths = {item[0] for item in _report(model, tool, retired)}
    assert ("items",) in paths and {(field,) for field in valid} <= paths


def _allowed(model, field: str) -> tuple:
    """字段在 Schema 里声明的完整闭集（没有闭集时为空）。"""
    return tuple(model.model_json_schema()["properties"][field].get("enum", ()))


@pytest.mark.parametrize("model,tool,valid,allowed", [
    (StartMainActivityRequest, "start_main_activity", _MAIN_VALID[0], ("travel", "interact", "sleep")),
    (StartSideActivityRequest, "start_side_activity", _SIDE_VALID[0], ("purchase", "interact")),
])
@pytest.mark.parametrize("action", ["walk", "drive", "take_taxi", "take_bus", "Travel", "wait"])
def test_action_outside_the_enum_is_refused_with_all_allowed_values(model, tool, valid, allowed, action):
    """四个旧出行动作作为 action 被拒，并返回这个工具允许的全部动作。"""
    assert _report(model, tool, {**valid, "action": action}) == [
        (("action",), action, allowed, "The value is outside the declared allowed values.")]


@pytest.mark.parametrize("arguments,expected", [
    ({"action": "travel", "target": "公司", "detail": None},
     [(("detail",), None, (), "detail is required when action is travel")]),
    ({"action": "interact", "target": "家", "detail": None},
     [(("detail",), None, (), "detail is required when action is interact")]),
    ({"action": "sleep", "target": "470", "detail": "家"},
     [(("detail",), "家", (), "detail must be null when action is sleep")]),
    ({"action": "sleep", "target": "8h", "detail": None}, [(("target",), "8h", (), SLEEP_MINUTES_REASON)]),
    ({"action": "sleep", "target": "721", "detail": "家"},
     [(("target",), "721", (), SLEEP_MINUTES_REASON), (("detail",), "家", (), "detail must be null when action is sleep")]),
    ({"action": "travel", "target": " ", "detail": "步行"},
     [(("target",), " ", (), "A non-blank string matching the declared format is required.")]),
    ({"action": "travel", "target": "公司", "detail": " "},
     [(("detail",), " ", (), "A non-blank string matching the declared format is required.")]),
    ({"action": "travel", "target": ["公司"], "detail": "步行"}, [(("target",), ["公司"], (), "A JSON string is required.")]),
])
def test_main_cross_constraints_report_one_item_per_violation(arguments, expected):
    """detail 的三条交叉约束与睡眠分钟数各报一条，定位到所在字段；两种情形同时出现时两条都报。"""
    assert _report(StartMainActivityRequest, "start_main_activity", arguments) == expected


@pytest.mark.parametrize("arguments,expected", [
    ({"action": "purchase", "target": []}, [(("target",), [], (), "target requires at least one name")]),
    ({"action": "interact", "target": []},
     [(("target",), [], (), "target requires at least one name"),
      (("target",), [], (), "target takes exactly one app name when action is interact")]),
    ({"action": "interact", "target": ["哔哩哔哩", "抖音"]},
     [(("target",), ["哔哩哔哩", "抖音"], (), "target takes exactly one app name when action is interact")]),
    ({"action": "purchase", "target": "牛肉面"}, [(("target",), "牛肉面", (), "A JSON array is required.")]),
    ({"action": "purchase", "target": ["牛肉面", " "]},
     [(("target", 1), " ", (), "A non-blank string matching the declared format is required.")]),
    ({"action": "purchase", "target": ["牛肉面", 2]}, [(("target", 1), 2, (), "A JSON string is required.")]),
])
def test_side_target_is_always_a_non_empty_array_and_one_app(arguments, expected):
    """名称数组至少一项；用内容 app 时恰好一项；项数由本地校验执行，严格 Schema 不带长度约束。"""
    assert _report(StartSideActivityRequest, "start_side_activity", arguments) == expected
    schema = deepseek_strict_schema(StartSideActivityRequest)
    assert "minItems" not in json.dumps(schema) and "maxItems" not in json.dumps(schema)


def test_numeric_minutes_must_be_written_as_text():
    """睡眠时长是数字文字：JSON 数字按类型错误拒绝。"""
    assert _report(StartMainActivityRequest, "start_main_activity",
                   {"action": "sleep", "target": 470, "detail": None}) == [
        (("target",), 470, (), "A JSON string is required.")]
