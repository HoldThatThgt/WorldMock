"""人物稳定工具面的 DeepSeek strict Schema 与运行模型合同。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from custom.agents.life_agent import LifeAgent, MemorySearchRequest
from custom.agents.life_prompt import TOOL_DESCRIPTIONS
from tests.integration.test_life_agent_protocol import (
    _OBSERVED_AT, _TASK, _WAIT, _add_objects, _memory, _observations, _offer, _visible_object, _with_activities,
)
from worldmock.life.activity_control import PHASE_ACTIVE
from worldmock.life.models import REQUEST_MODELS, deepseek_strict_schema
from tests.life.conftest import start_arguments


_UNSUPPORTED_KEYS = {"oneOf", "minLength", "maxLength", "minItems", "maxItems", "default"}
#: 稳定工具面的八件人物动作工具；当日计划由零点后台任务 plan_day 生成，不是人物工具（时间与能量规格第四章）。
_ACTION_TOOLS = {
    "start_activity", "send_message", "recall_task_summary",
    "report_status", "pause_activity", "conclude_activity", "revise_day_plan",
}


def _prepared_agent(revise: bool = False) -> LifeAgent:
    """用本轮公开事实及私有身份绑定准备实际人物工具（当前停留里有一项进行中的等待）。"""
    observations = deepcopy(_observations())
    data = observations[0]["data"]
    _add_objects(data, _visible_object("同事", "person", "agent:9", _offer("interact", "交谈", "chat")))
    _with_activities(data, (_WAIT, PHASE_ACTIVE))
    if revise:
        data["todo_layer"]["day_plan"] = {"date": "2026-01-05", "goals": []}
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, _OBSERVED_AT, observations))
    return agent


def _function(name: str) -> dict[str, Any]:
    """取得模型本轮真正可见的函数声明。"""
    agent = _prepared_agent(revise=name == "revise_day_plan")
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
    """全部八件动作工具采用同一请求模型，身份与执行参数不进工具面。"""
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


def _host_error(tool: str, arguments: dict) -> str:
    """宿主按本轮私有绑定对名称的本地校验结果（空串即通过）。"""
    return _prepared_agent().validate_react_decisions([ReactDecision("", tool, arguments, "")], False)


def test_start_activity_uses_readable_frozen_objects_and_paired_actions() -> None:
    """逐项四字段闭合请求，方式推导动作，购买偏好为中文精确闭集。"""
    schema = _function("start_activity")["parameters"]
    assert schema == deepseek_strict_schema(REQUEST_MODELS["start_activity"])
    assert set(schema["properties"]) == {"items", "answering", "prefer"}
    assert schema["required"] == ["items", "answering", "prefer"]
    item = schema["properties"]["items"]["items"]
    assert set(item["properties"]) == set(item["required"]) == {"target", "way", "from", "to"}
    assert all("enum" not in item["properties"][field] for field in ("target", "way"))
    assert schema["properties"]["prefer"]["enum"] == ["最快拿到", "到手价最低", None]
    assert "$ref" not in json.dumps(schema) and "$defs" not in json.dumps(schema)
    valid = start_arguments(["自己", _TASK], ["interact", "interact"], ["等待", "工作"])
    assert list(Draft202012Validator(schema).iter_errors(valid)) == []
    REQUEST_MODELS["start_activity"].model_validate(valid)
    assert _host_error("start_activity", valid) == ""
    error = _host_error("start_activity", start_arguments(["internal:task:1"], ["interact"], ["工作"]))
    assert error.report.error_code == "WORK_TOPIC_NOT_FOUND" and error.report.items[0].written == "internal:task:1"
    for way in ("interact", "invented_way"):
        error = _host_error("start_activity", start_arguments(["自己"], ["interact"], [way]))
        assert error.report.error_code == "WAY_NOT_OFFERED" and error.report.items[0].path[-1] == "way"
    for deleted in ({"targets": ["自己"], "actions": ["interact"]}, {**valid, "actions": ["interact"]}):
        assert list(Draft202012Validator(schema).iter_errors(deleted))
        with pytest.raises(ValidationError):
            REQUEST_MODELS["start_activity"].model_validate(deleted)


@pytest.mark.parametrize("name", ["report_status", "pause_activity", "conclude_activity"])
def test_activity_control_schemas_use_paired_readable_activity_and_summary(name: str) -> None:
    """三条活动控制工具一项一个对象：每项点名可读活动并写人物自述，只有结束工具每项另带记忆与看法；外层键是 items
    （不是提示字段黑名单里的 activities）与请求级 errands；活动名不做 Schema 枚举（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要
    第六章）；缺 errands 的请求被拒。"""
    schema = _function(name)["parameters"]
    assert set(schema["properties"]) == set(schema["required"]) == {"items", "errands"}
    item = schema["properties"]["items"]["items"]
    expected = {"activity_name", "summary", "memories", "views"} if name == "conclude_activity" else {
        "activity_name", "summary"}
    assert set(item["properties"]) == set(item["required"]) == expected
    assert "enum" not in item["properties"]["activity_name"]
    assert item["properties"]["summary"]["type"] == "string"
    valid = _control_payload(name, [(_WAIT, "我还想等一会儿。")])
    assert list(Draft202012Validator(schema).iter_errors(valid)) == []
    REQUEST_MODELS[name].model_validate(valid)
    assert _host_error(name, valid) == ""
    assert _host_error(name, _control_payload(name, [("别的活动", "继续。")])).report.error_code == "NAME_NOT_FOUND"
    for invalid in ({"items": [], "errands": []}, _control_payload(name, [(_WAIT, "a"), (_WAIT, "b")]),
                    _control_payload(name, [(_WAIT, " ")]), {"items": [{"activity_name": _WAIT}], "errands": []},
                    {"task": [_WAIT], "summary": ["继续。"]}, {"items": valid["items"]}):
        with pytest.raises(ValidationError):
            REQUEST_MODELS[name].model_validate(invalid)


#: 一条个人事务变更的八个键，Schema 与请求模型都按这个顺序（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 「每条变更」）。
_ERRAND_KEYS = ["change", "name", "outcome", "item", "quantity", "memo", "remind_time", "reason"]


@pytest.mark.parametrize("name", ["report_status", "pause_activity", "conclude_activity"])
def test_control_tool_schemas_inline_the_errand_change_object(name: str) -> None:
    """三个控制工具的请求级 errands 是内联的变更对象数组（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 ）：模型本轮可见的函数
    声明就是 DeepSeek strict 校验通过的请求模型 Schema，没有 $ref 与 $defs；变更对象封闭、八个键按序全部必填，change
    的枚举只有新增、更新、取消三种（没有完成），outcome 是可空的取得、吃掉；item、memo、reason 是非空白字符串或 null，
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
    assert fields["outcome"]["enum"] == ["取得", "吃掉", None] and fields["outcome"]["type"] == ["string", "null"]
    assert fields["name"]["pattern"] == r"\S"
    for key in ("item", "memo", "reason"):
        assert fields[key]["anyOf"] == [{"pattern": r"\S", "type": "string"}, {"type": "null"}]
    goal = _function("revise_day_plan")["parameters"]["properties"]["goals"]["items"]["properties"]
    assert fields["remind_time"]["anyOf"] == goal["remind_time"]["anyOf"]
    assert fields["quantity"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert not {"minimum", "maximum", "maxLength", "minLength", "maxItems"} & set(json.dumps(errands).split('"'))
    add = {"change": "新增", "name": "补一份素炒什锦食材包", "outcome": "取得", "item": "素炒什锦食材包",
           "quantity": 1, "memo": "下班前下单送到家", "remind_time": "17:00", "reason": None}
    payload = {**_control_payload(name, [(_WAIT, "我还想等一会儿。")]), "errands": [add]}
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []
    request = REQUEST_MODELS[name].model_validate(payload)
    assert request.model_dump(mode="json")["errands"] == [add]
    assert list(request.model_dump(mode="json")) == ["items", "errands"]


@pytest.mark.parametrize("name", ["report_status", "pause_activity", "conclude_activity"])
def test_control_summaries_are_limited_to_500_characters_by_the_local_check(name: str) -> None:
    """自述长度上限由本地校验兜住（strict 禁 maxLength，docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第六章）：恰好 500 字的自述经
    请求模型与宿主本地校验受理，501 字的在 Schema 上合法、却被请求模型拒绝，宿主按 ARGUMENTS_INVALID 纠错。"""
    schema = _function(name)["parameters"]
    boundary, over = (_control_payload(name, [(_WAIT, "长" * length)]) for length in (500, 501))
    assert list(Draft202012Validator(schema).iter_errors(over)) == []
    REQUEST_MODELS[name].model_validate(boundary)
    assert _host_error(name, boundary) == ""
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


def test_named_content_tools_bind_visible_names_and_inline_deliverable() -> None:
    """工作与通讯保留正文生成，身份字段是可读名称：不做 Schema 枚举，由宿主按任务、人物与回看绑定校验；会话名不在七层中
    出现，不是回看名称。"""
    assert "submit_work" not in {entry["function"]["name"] for entry in _prepared_agent().build_react_tool_schemas(readonly=False)}
    message = _function("send_message")["parameters"]
    recall = _function("recall_task_summary")["parameters"]
    assert set(message["properties"]) == {"recipient_name", "text"}
    assert "enum" not in message["properties"]["recipient_name"]
    assert set(recall["properties"]) == {"task_name"}
    assert _host_error("send_message", {"recipient_name": "同事", "text": "在吗"}) == ""
    assert _host_error("send_message", {"recipient_name": "自己", "text": "在吗"}).report.error_code == "NAME_NOT_FOUND"
    for name in (_TASK, _WAIT):
        assert _host_error("recall_task_summary", {"task_name": name}) == ""
    for name in ("同事", "自己家（1月4日 18:00 起）"):
        assert _host_error("recall_task_summary", {"task_name": name}) .report.error_code == "NAME_NOT_FOUND"


def test_revise_day_plan_schema_inlines_goal_with_required_nullable_remind_time() -> None:
    """人物工具面没有 plan_day；修订的目标内联意图、文字与两个必填可空的场景本地 HH:MM——提醒 remind_time 与大致时刻
    around_time（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-当日目标带大致时刻只有在那前后进行的活动才算推进 ），不向模型索要时长或内部时刻（时间与能量规格第四章「计划与提醒」）。"""
    agent = _prepared_agent(revise=True)
    tools = {item["function"]["name"] for item in agent.build_react_tool_schemas(readonly=False)}
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
    search = {"query": "q" * 201, "limit": 1}
    assert list(Draft202012Validator(_function("memory_search")["parameters"]).iter_errors(search)) == []
    with pytest.raises(ValidationError):
        MemorySearchRequest.model_validate(search)


def test_conclusion_memory_and_view_nested_schemas_remain_strict() -> None:
    """收尾记忆、看法与工作评审的嵌套数据仍由原生严格 Schema 内联约束，没有引用与未知字段：普通记忆没有对象字段、类型
    只取七类，看法与偏好另写在 views 并用 about 写对象（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第六章）。"""
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
    agent = _prepared_agent()
    agent.build_react_tool_schemas(readonly=False)
    assert agent.validate_react_decisions(
        [ReactDecision("", "conclude_activity", payload, "")], False) == ""



@pytest.mark.parametrize("retired_type", ["event", "observation"])
def test_conclusion_memory_rejects_fact_like_memory_types(retired_type: str) -> None:
    """人物记忆不接受事实型事件记录：Schema 枚举与本地请求模型都拒绝。"""
    payload = {"items": [{"activity_name": _WAIT, "summary": "收尾。", "views": [],
                          "memories": [_memory("我以为今天已经吃过饭", kind=retired_type)]}], "errands": []}
    assert list(Draft202012Validator(_function("conclude_activity")["parameters"]).iter_errors(payload))
    with pytest.raises(ValidationError):
        REQUEST_MODELS["conclude_activity"].model_validate(payload)
