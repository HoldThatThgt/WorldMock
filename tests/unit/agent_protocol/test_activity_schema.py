"""人物稳定工具面的 DeepSeek strict Schema 与运行模型合同。"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from tests.integration.runtime.current_contract_fixtures import inputs
from tests.life.conftest import (
    MAIN_TOOL,
    SEARCH_TOOL,
    SIDE_TOOL,
    advance_until_ready,
    main_arguments,
    side_arguments,
    stage_day_plan,
    wakeup_for,
)
from tests.life.environment_runtime import act
from worldmock.agents.life_agent import LifeAgent, person_inputs, validate_life_batch
from worldmock.agents.prompt.life_prompt import TOOL_DESCRIPTIONS
from worldmock.agents.protocol.agent_loop import LoopToolCall, validate_single_tool_call
from worldmock.agents.protocol.life_feedback import ProtocolFeedback
from worldmock.state.interaction.requests import QUERY_MODELS, REQUEST_MODELS
from worldmock.state.interaction.schema import deepseek_strict_schema


_UNSUPPORTED_KEYS = {"oneOf", "minLength", "maxLength", "minItems", "maxItems", "default"}
#: 稳定工具面的八件请求工具（两个开始工具与搜索在前）；当日计划由零点后台任务 plan_day 生成，不是人物工具（时间与能量规格第四章）。
_ACTION_TOOLS = {
    "start_main_activity",
    "start_side_activity",
    "search_world",
    "recall_task_summary",
    "report_status",
    "pause_activity",
    "conclude_activity",
    "revise_day_plan",
}
_FULL_MODELS = {**REQUEST_MODELS, **QUERY_MODELS}
_REST = main_arguments("interact", "家", "测试休息")
_APP = side_arguments("interact", "抖音")
_WAIT = "测试休息（1月5日 08:00 起）"


def _memory(text: str = "记住这一刻。", *, kind: str = "commitment") -> dict[str, Any]:
    """一条最小合法的收尾记忆。"""
    return {"type": kind, "text": text, "importance": 0.5, "keywords": ["测试"]}


def _agent() -> LifeAgent:
    """用冻结测试夹具构造无状态人物适配器。"""
    return LifeAgent(person_inputs(inputs().persona_by_id[1]))


def _function(name: str) -> dict[str, Any]:
    """取得模型本轮真正可见的函数声明。"""
    return next(item["function"] for item in _agent().turn_tools(16, False)
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


def _control_payload(name: str, pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """活动控制参数：一项一个对象（活动名，自述）；结束工具每项另带空的 memories 与 views，播报与暂停不带；请求级
    errands（个人事务变更）为空列表。"""
    extra = {"memories": [], "views": []} if name == "conclude_activity" else {}
    return {"items": [{"activity_name": activity, "summary": summary, **extra} for activity, summary in pairs],
            "errands": []}


def _properties(schema: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Schema 中全部对象属性（路径，属性 Schema），含数组内联的嵌套对象。"""
    found: list[tuple[str, dict[str, Any]]] = []
    for name, value in schema.get("properties", {}).items():
        found.append((name, value))
        nested = value.get("items", {}) if value.get("type") == "array" else value
        found.extend((f"{name}.{child}", item) for child, item in _properties(nested))
    return found


def test_all_action_tools_use_closed_strict_schemas_from_one_model() -> None:
    """全部八件请求工具采用同一请求模型，身份与执行参数不进工具面。"""
    assert set(REQUEST_MODELS) == _ACTION_TOOLS
    assert list(REQUEST_MODELS)[:3] == [MAIN_TOOL, SIDE_TOOL, SEARCH_TOOL]
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


def _host_error(tool: str, arguments: dict) -> ProtocolFeedback | None:
    """按开放工具表对单条工具调用的参数合同本地校验（None 即通过）。"""
    return validate_single_tool_call(LoopToolCall(tool, arguments, "call-0"), _FULL_MODELS)


def _with_active_rest(engine_factory):
    """提交一个当前在家的休息活动并推进到首个可决策点，返回（唤醒输入，活动可读名，沙盒句柄）。"""
    engine = engine_factory(mutate=lambda _, config: config.update(progress_report_interval_seconds=60))
    stage_day_plan(engine)
    act(engine, "wait", _REST, MAIN_TOOL)
    assert advance_until_ready(engine)
    wakeup = wakeup_for(engine)
    name = wakeup.observation["activity_bindings"][0]["name"]
    return wakeup, name, engine.open_sandbox(wakeup)


def test_start_tools_declare_the_model_schema_and_take_readable_frozen_names(engine_factory) -> None:
    """模型本轮可见的开始工具与 search_world 声明就是各自请求模型的 DeepSeek strict Schema，对象名与 detail 不做
    逐轮枚举；协议层校验批次与闭集，沙盒分派核对可读完整名称。"""
    for tool, valid in ((MAIN_TOOL, _REST), (SIDE_TOOL, _APP)):
        function = _function(tool)
        schema = function["parameters"]
        assert schema == deepseek_strict_schema(REQUEST_MODELS[tool])
        assert function["description"] == TOOL_DESCRIPTIONS[tool] and "description" not in schema
        assert list(schema["properties"]) == schema["required"] == list(valid)
        assert all("enum" not in json.dumps(schema["properties"][field]) for field in valid if field != "action")
        assert "$ref" not in json.dumps(schema) and "$defs" not in json.dumps(schema)
        assert list(Draft202012Validator(schema).iter_errors(valid)) == []
        REQUEST_MODELS[tool].model_validate(valid)
        assert _host_error(tool, valid) is None
        retired = {"items": [{"action": "interact", "target": "家"}]}
        assert list(Draft202012Validator(schema).iter_errors(retired))
        assert _host_error(tool, retired).report.error_code == "ARGUMENTS_INVALID"
    search_fn = _function(SEARCH_TOOL)
    assert search_fn["parameters"] == deepseek_strict_schema(REQUEST_MODELS[SEARCH_TOOL])
    assert search_fn["description"] == TOOL_DESCRIPTIONS[SEARCH_TOOL]
    group = [LoopToolCall(MAIN_TOOL, _REST, "c1"), LoopToolCall(SIDE_TOOL, _APP, "c2")]
    assert validate_life_batch(group, REQUEST_MODELS) is None
    engine = engine_factory()
    stage_day_plan(engine)
    wakeup = wakeup_for(engine)
    sandbox = engine.open_sandbox(wakeup)
    private = [(MAIN_TOOL, main_arguments("interact", "internal:task:1", "测试休息"), ["target"], "internal:task:1"),
               (MAIN_TOOL, main_arguments("interact", "家", "rest-visible"), ["detail"], "rest-visible"),
               (SIDE_TOOL, side_arguments("interact", "work-visible"), ["target", 0], "work-visible")]
    for index, (tool, arguments, path, written) in enumerate(private, start=1):
        res = sandbox.dispatch_world_tool(tool, arguments, f"{wakeup.wakeup_id}:action:{index:04d}")
        assert not res.accepted
        [item] = res.tool_message_payload["result"]["details"]["items"]
        assert (item["error_code"], item["path"][4:], item["written"]) == ("NAME_NOT_FOUND", path, written)
    unsupported = {MAIN_TOOL: (main_arguments("travel", "家", "步行"), ["interact", "sleep"]),
                   SIDE_TOOL: (side_arguments("purchase", "抖音"), ["interact"])}
    for index, (tool, (arguments, offered)) in enumerate(unsupported.items(), start=10):
        res = sandbox.dispatch_world_tool(tool, arguments, f"{wakeup.wakeup_id}:action:{index:04d}")
        assert not res.accepted
        [item] = res.tool_message_payload["result"]["details"]["items"]
        assert (item["error_code"], item["path"][4:], item["written"]) == (
            "ACTION_NOT_OFFERED", ["action"], arguments["action"])
        assert item["allowed_values"] == offered
    for tool, allowed in ((MAIN_TOOL, ("travel", "interact", "sleep")), (SIDE_TOOL, ("purchase", "interact"))):
        valid = _REST if tool == MAIN_TOOL else _APP
        for action in ("wait", "walk", "invented_action"):
            [item] = _host_error(tool, {**valid, "action": action}).report.items
            assert (item.error_code, item.path[4:], item.written) == ("ARGUMENTS_INVALID", ("action",), action)
            assert item.allowed_values == allowed


@pytest.mark.parametrize("name", ["report_status", "pause_activity", "conclude_activity"])
def test_activity_control_schemas_use_paired_readable_activity_and_summary(engine_factory, name: str) -> None:
    """三条活动控制工具一项一个对象：每项点名可读活动并写人物自述，只有结束工具每项另带记忆与看法；外层键是 items
    （不是提示字段黑名单里的 activities）与请求级 errands；活动名不做 Schema 枚举；缺 errands 的请求被拒。"""
    schema = _function(name)["parameters"]
    assert set(schema["properties"]) == set(schema["required"]) == {"items", "errands"}
    item = schema["properties"]["items"]["items"]
    expected = {"activity_name", "summary", "memories", "views"} if name == "conclude_activity" else {
        "activity_name", "summary"}
    assert set(item["properties"]) == set(item["required"]) == expected
    assert "enum" not in item["properties"]["activity_name"]
    assert item["properties"]["summary"]["type"] == "string"
    wakeup, active_name, sandbox = _with_active_rest(engine_factory)
    valid = _control_payload(name, [(active_name, "我还想等一会儿。")])
    assert list(Draft202012Validator(schema).iter_errors(valid)) == []
    REQUEST_MODELS[name].model_validate(valid)
    assert _host_error(name, valid) is None
    bad = sandbox.dispatch_world_tool(name, _control_payload(name, [("别的活动", "继续。")]),
                                    f"{wakeup.wakeup_id}:action:0001")
    result = bad.tool_message_payload["result"]
    assert not bad.accepted and result["error_code"] == "ACTION_REQUEST_INVALID"
    assert result["details"]["items"][0]["error_code"] == "NAME_NOT_FOUND"
    ok = sandbox.dispatch_world_tool(name, valid, f"{wakeup.wakeup_id}:action:0002")
    assert ok.accepted
    for invalid in ({"items": [], "errands": []}, _control_payload(name, [(active_name, "a"), (active_name, "b")]),
                    _control_payload(name, [(active_name, " ")]), {"items": [{"activity_name": active_name}],
                                                                  "errands": []},
                    {"task": [active_name], "summary": ["继续。"]}, {"items": valid["items"]}):
        with pytest.raises(ValidationError):
            REQUEST_MODELS[name].model_validate(invalid)


#: 一条个人事务变更的七个键，Schema 与请求模型都按这个顺序（docs/design/04-simulation-engine.md 「每条变更」）。
_ERRAND_KEYS = ["change", "name", "item", "quantity", "memo", "remind_time", "reason"]


@pytest.mark.parametrize("name", ["report_status", "pause_activity", "conclude_activity"])
def test_control_tool_schemas_inline_the_errand_change_object(name: str) -> None:
    """三个控制工具的请求级 errands 是内联的变更对象数组（docs/design/04-simulation-engine.md ）：模型本轮可见的函数
    声明就是 DeepSeek strict 校验通过的请求模型 Schema，没有 $ref 与 $defs；变更对象封闭、七个键按序全部必填，change
    的枚举只有新增、更新、取消三种（没有完成），item、memo、reason 是非空白字符串或 null，
    remind_time 与当日目标同形，quantity 是整数或 null（上限由本地校验执行，Schema 不带长度与范围）。"""
    schema = _function(name)["parameters"]
    assert schema == deepseek_strict_schema(REQUEST_MODELS[name])
    assert "$ref" not in json.dumps(schema) and "$defs" not in json.dumps(schema)
    errands = schema["properties"]["errands"]
    change = errands["items"]
    assert errands["type"] == "array" and change["type"] == "object"
    _assert_closed(errands)
    assert list(change["properties"]) == change["required"] == _ERRAND_KEYS
    fields = change["properties"]
    assert fields["change"]["enum"] == ["新增", "更新", "取消"] and fields["change"]["type"] == "string"
    assert fields["name"]["pattern"] == r"\S"
    for key in ("item", "memo", "reason"):
        assert fields[key]["anyOf"] == [{"pattern": r"\S", "type": "string"}, {"type": "null"}]
    goal = _function("revise_day_plan")["parameters"]["properties"]["goals"]["items"]["properties"]
    assert fields["remind_time"]["anyOf"] == goal["remind_time"]["anyOf"]
    assert fields["quantity"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert not {"minimum", "maximum", "maxLength", "minLength", "maxItems"} & set(json.dumps(errands).split('"'))
    add = {"change": "新增", "name": "补一份素炒什锦食材包", "item": "素炒什锦食材包",
           "quantity": 1, "memo": "下班前下单送到家", "remind_time": "17:00", "reason": None}
    payload = {**_control_payload(name, [(_WAIT, "我还想等一会儿。")]), "errands": [add]}
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []
    request = REQUEST_MODELS[name].model_validate(payload)
    assert request.model_dump(mode="json")["errands"] == [add]
    assert list(request.model_dump(mode="json")) == ["items", "errands"]


@pytest.mark.parametrize("name", ["report_status", "pause_activity", "conclude_activity"])
def test_control_summaries_are_limited_to_500_characters_by_the_local_check(name: str) -> None:
    """自述长度上限由本地校验兜住（strict 禁 maxLength，docs/design/04-simulation-engine.md 第六章）：恰好 500 字的自述经
    请求模型与宿主本地校验受理，501 字的在 Schema 上合法、却被请求模型拒绝，宿主按 ARGUMENTS_INVALID 纠错。"""
    schema = _function(name)["parameters"]
    boundary, over = (_control_payload(name, [(_WAIT, "长" * length)]) for length in (500, 501))
    assert list(Draft202012Validator(schema).iter_errors(over)) == []
    REQUEST_MODELS[name].model_validate(boundary)
    assert _host_error(name, boundary) is None
    with pytest.raises(ValidationError, match="within 500 characters"):
        REQUEST_MODELS[name].model_validate(over)
    error = _host_error(name, over)
    assert error.report.error_code == "ARGUMENTS_INVALID" and error.report.items[0].path[-1] == "summary"


@pytest.mark.parametrize("name", ["report_status", "pause_activity", "conclude_activity"])
def test_control_tool_fields_carry_format_and_source_descriptions(name: str) -> None:
    """控制工具参数的每个字段都带一句「格式与来源」说明（字段级说明是参数 Schema 的一部分，不是工具 description）：活动名
    照工具层、待办层或进度层原样抄写并带开始时刻，看法写对象；参数对象本身不写说明，工具 description 仍只写用途。"""
    schema = _function(name)["parameters"]
    fields = _properties(schema)
    assert fields and all(isinstance(value.get("description"), str) and value["description"] for _, value in fields)
    described = dict(fields)
    assert "原样抄写" in described["items.activity_name"]["description"]
    assert "（9月28日 12:05 起）" in described["items.activity_name"]["description"]
    assert "description" not in schema and _function(name)["description"] == TOOL_DESCRIPTIONS[name]
    if name == "conclude_activity":
        assert {"items.memories.type", "items.memories.text", "items.views.about"} <= set(described)
        assert "对象" in described["items.views.about"]["description"]


def test_recall_uses_readable_activity_name_and_retired_world_tools_are_absent(engine_factory) -> None:
    """回看绑定真实活动名称，旧消息、工作提交与闹钟工具不再提供。"""
    names = {entry["function"]["name"] for entry in _agent().turn_tools(16, False)}
    assert {"start_activity", "submit_work", "send_message", "set_alarm", "finish"}.isdisjoint(names)
    assert len(names) == 9 and {MAIN_TOOL, SIDE_TOOL, SEARCH_TOOL} <= names
    recall = _function("recall_task_summary")["parameters"]
    assert set(recall["properties"]) == {"task_name"}
    _, active_name, sandbox = _with_active_rest(engine_factory)
    assert "error_code" not in sandbox.recall_task_summary({"task_name": active_name})
    for bad in ("同事", "家（1月4日 18:00 起）"):
        assert sandbox.recall_task_summary({"task_name": bad})["error_code"] == "TASK_NOT_FOUND"


def test_revise_day_plan_schema_inlines_goal_with_required_nullable_remind_time() -> None:
    """人物工具面没有 plan_day；修订的目标内联意图、文字与两个必填可空的场景本地 HH:MM——提醒 remind_time 与大致时刻
    around_time（docs/design/04-simulation-engine.md ），不向模型索要时长或内部时刻（时间与能量规格第四章「计划与提醒」）。"""
    tools = {item["function"]["name"] for item in _agent().turn_tools(16, False)}
    assert "revise_day_plan" in tools and "plan_day" not in tools
    schema = _function("revise_day_plan")["parameters"]
    assert set(schema["properties"]) == {"goals"}
    item = schema["properties"]["goals"]["items"]
    assert set(item["properties"]) == set(item["required"]) == {"intent_kind", "goal", "remind_time", "around_time"}
    assert item["properties"]["goal"]["pattern"] == r"\S"
    assert item["properties"]["around_time"]["anyOf"] == item["properties"]["remind_time"]["anyOf"]
    assert {"remind_at", "reminded_at", "around_at", "registered_at",
            "planned_duration_seconds"}.isdisjoint(item["properties"])
    model = REQUEST_MODELS["revise_day_plan"]
    base = {"intent_kind": "休息", "goal": "歇一会儿", "remind_time": None, "around_time": None}
    for field in ("remind_time", "around_time"):
        for clock in (None, "00:00", "08:15", "23:59"):
            valid = {"goals": [{**base, field: clock}]}
            assert list(Draft202012Validator(schema).iter_errors(valid)) == []
            model.model_validate(valid)
        missing = {key: value for key, value in base.items() if key != field}
        for goal in (missing, *({**base, field: text} for text in ("8:15", "24:00", "12:60", "08:15:00"))):
            assert list(Draft202012Validator(schema).iter_errors({"goals": [goal]}))
            with pytest.raises(ValidationError, match=field):
                model.model_validate({"goals": [goal]})


def test_provider_array_cardinality_gap_is_locally_rejected() -> None:
    """供应商不支持数组长度约束时，本地模型仍拒绝越界请求（含一至两项与记忆、看法合计八条的上限）。"""
    schema = _function("report_status")["parameters"]
    invalid = {"items": [], "errands": []}
    assert list(Draft202012Validator(schema).iter_errors(invalid)) == []
    with pytest.raises(ValidationError):
        REQUEST_MODELS["report_status"].model_validate(invalid)
    conclude = _function("conclude_activity")["parameters"]
    view = {"type": "preference", "about": "面馆", "text": "面馆的面好吃。", "importance": 0.5, "keywords": ["午饭"]}
    nine = [{"activity_name": _WAIT, "summary": "收尾。", "views": [view],
             "memories": [_memory(f"第{index}件想记住的事。") for index in range(8)]}]
    for items in ([], nine):
        payload = {"items": items, "errands": []}
        assert list(Draft202012Validator(conclude).iter_errors(payload)) == []
        with pytest.raises(ValidationError):
            REQUEST_MODELS["conclude_activity"].model_validate(payload)
    search = {"category": "饮食", "time": "今天", "keyword": "早饭 午饭 晚饭 夜宵 零食"}
    assert list(Draft202012Validator(_function("search_memory")["parameters"]).iter_errors(search)) == []
    with pytest.raises(ValidationError):
        QUERY_MODELS["search_memory"].model_validate(search)


def test_conclusion_memory_and_view_nested_schemas_remain_strict() -> None:
    """收尾记忆、看法与工作评审的嵌套数据仍由原生严格 Schema 内联约束，没有引用与未知字段：普通记忆没有对象字段、类型
    只取七类，看法与偏好另写在 views 并用 about 写对象（docs/design/04-simulation-engine.md 第六章）。"""
    conclude = _function("conclude_activity")["parameters"]
    item = conclude["properties"]["items"]["items"]["properties"]
    memory_item, view_item = item["memories"]["items"], item["views"]["items"]
    assert set(memory_item["properties"]) == {"type", "text", "importance", "keywords"}
    assert memory_item["properties"]["type"]["enum"] == [
        "commitment", "goal", "conflict", "resolution", "identity", "routine", "reflection"]
    assert set(view_item["properties"]) == {"type", "about", "text", "importance", "keywords"}
    assert view_item["properties"]["type"]["enum"] == ["relationship", "preference"]
    assert view_item["properties"]["about"]["pattern"] == r"\S"
    _assert_closed(memory_item)
    _assert_closed(view_item)
    assert "$ref" not in json.dumps(conclude) and "$defs" not in json.dumps(conclude)
    payload = {"items": [{"activity_name": _WAIT, "summary": "收尾。", "memories": [_memory()], "views": []}],
               "errands": []}
    assert list(Draft202012Validator(conclude).iter_errors(payload)) == []
    REQUEST_MODELS["conclude_activity"].model_validate(payload)
    assert _host_error("conclude_activity", payload) is None


@pytest.mark.parametrize("retired_type", ["event", "observation"])
def test_conclusion_memory_rejects_fact_like_memory_types(retired_type: str) -> None:
    """人物记忆不接受事实型事件记录：Schema 枚举与本地请求模型都拒绝。"""
    payload = {"items": [{"activity_name": _WAIT, "summary": "收尾。", "views": [],
                          "memories": [_memory("我以为今天已经吃过饭", kind=retired_type)]}], "errands": []}
    assert list(Draft202012Validator(_function("conclude_activity")["parameters"]).iter_errors(payload))
    with pytest.raises(ValidationError):
        REQUEST_MODELS["conclude_activity"].model_validate(payload)
