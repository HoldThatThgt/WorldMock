"""@brief 人物提示七层的机器可读 Schema（JSON Schema 2020-12）与测试用的校验入口。

依据 docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章与接口合同登记：系统层、工具层、人设层、记忆层、待办层、环境层、进度层各一份
Schema，另有原生 tools 的一项定义。各层构建器的输出由测试逐层校验；发送前不做逐层校验（裁决 #2），运行时只有提示指纹
（life_prompt.life_prompt_sha256）读取本模块。枚举直接读取各领域的冻结闭集，Schema 不维护字面副本。
"""

from __future__ import annotations
from jsonschema import Draft202012Validator
from worldmock.life.tool_groups import expand_tool_layer

from typing import Any

from worldmock.life import felt
from worldmock.life.callable_index import MAX_EXAMPLES
from worldmock.life.control_items import CONTROL_TOOLS
from worldmock.life.vocabulary import LIFE_VERBS, WAYS
from worldmock.life.models import ActionStatus
from worldmock.life.schedule import INTENT_KINDS
from worldmock.life.work_schedule import DUTY_DAYS, DUTY_STATUSES

#: 带时区的 ISO 8601 时刻（秒精度）。
_TIME = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$"}
#: 场景本地日期。
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
#: 非空文本与可空文本。
_TEXT = {"type": "string", "minLength": 1}
_NULLABLE_TEXT = {"type": ["string", "null"], "minLength": 1}
_LINES = {"type": "array", "items": _TEXT}

#: 工具层目标类别闭集（英文值；场景联系人并入 person）。
TARGET_KINDS = ("location", "product", "person", "contact", "self", "task", "activity",
                "message", "app", "lifestyle")
#: 工具层可列出的方式，不含仅在请求中由系统确定方式的「购买」。
WAY_LABELS = tuple(way for way in WAYS if way != "购买")
#: 以目标名称为参数的其他工具。
TARGET_TOOLS = (*CONTROL_TOOLS, "send_message")
#: 动作状态由生产枚举按声明顺序投影。
ACTION_STATUSES = tuple(status.value for status in ActionStatus)
#: 五项体感分级文字由实际分级表派生。
BODY_LEVELS = {
    "appetite": (felt.APETITE_BASE_LEVEL, *(label for _, label in felt.APPETITE_BANDS),
                 felt.APPETITE_STARVING_LEVEL),
    "sleepiness": (felt.SLEEPINESS_BASE_LEVEL, *(label for _, label in felt.SLEEPINESS_BANDS)),
    "entertainment": (felt.ENTERTAINMENT_BASE_LEVEL, *(label for _, label in felt.ENTERTAINMENT_BANDS)),
    "social": (felt.SOCIAL_BASE_LEVEL, *(label for _, label in felt.SOCIAL_BANDS)),
    "mood": (felt.MOOD_BASE_LEVEL, *(label for _, label in felt.MOOD_BANDS)),
}

def _object(properties: dict[str, Any], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    """@brief 封闭对象：properties 中除 optional 以外的键都必填，不接受其他键。"""
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": [key for key in properties if key not in optional]}


def _availability(extra: dict[str, Any]) -> dict[str, Any]:
    """@brief 带 available 与 reason 的对象：可用时 reason 为 null，不可用时 reason 是非空原因。"""
    schema = _object({**extra, "available": {"type": "boolean"}, "reason": _NULLABLE_TEXT})
    schema["if"] = {"properties": {"available": {"const": True}}}
    schema["then"] = {"properties": {"reason": {"type": "null"}}}
    schema["else"] = {"properties": {"reason": _TEXT}}
    return schema


def _tool_action(tools: tuple[str, ...]) -> dict[str, Any]:
    """@brief 以目标名称为参数的一项工具及其可用性。"""
    return _availability({"tool": {"enum": list(tools)}})


_VERB_ACTION = _availability({
    "action": {"enum": list(LIFE_VERBS)}, "way": {"enum": list(WAY_LABELS)},
    "price": _NULLABLE_TEXT, "duration": _NULLABLE_TEXT, "quantity": _NULLABLE_TEXT, "note": _NULLABLE_TEXT})
_CONTRACT = _object({"topic": _TEXT, "target": _TEXT, "done": _TEXT, "remaining": _TEXT})


def _tools_for(kind: str, tools: tuple[str, ...]) -> dict[str, Any]:
    """@brief 某类目标的 tools 形状：按固定顺序列出该类的工具。"""
    return {"if": {"properties": {"kind": {"const": kind}}},
            "then": {"properties": {"tools": {"prefixItems": [
                {"properties": {"tool": {"const": tool}}} for tool in tools],
                "minItems": len(tools), "maxItems": len(tools)}}}}


_TARGET = _object({
    "name": _TEXT, "kind": {"enum": list(TARGET_KINDS)}, "phase": {"enum": ["进行中", "刚结束", None]},
    "state": _NULLABLE_TEXT, "contract": _CONTRACT, "last_report": _TEXT,
    "actions": {"type": "array", "items": _VERB_ACTION},
    "tools": {"type": "array", "items": _tool_action(TARGET_TOOLS)},
}, optional=("contract", "last_report"))
_TARGET["allOf"] = [
    {"if": {"properties": {"kind": {"const": "task"}}}, "then": {"required": ["contract"]},
     "else": {"not": {"required": ["contract"]}}},
    {"if": {"properties": {"kind": {"const": "activity"}}},
     "then": {"properties": {"phase": {"enum": ["进行中", "刚结束"]}, "actions": {"maxItems": 0}}},
     "else": {"properties": {"phase": {"type": "null"}}}},
    _tools_for("activity", CONTROL_TOOLS),
    _tools_for("task", ()),
    _tools_for("person", ("send_message",)),
    _tools_for("contact", ("send_message",)),
    {"if": {"properties": {"kind": {"enum": ["activity", "task", "person", "contact"]}}}, "else": {
        "properties": {"tools": {"maxItems": 0}}}},
]

SYSTEM_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "系统层",
    **_object({"layer": {"const": "系统层"}, "role": _TEXT,
               "rules": {"type": "array", "minItems": 1, "uniqueItems": True, "items": _TEXT}}),
}

_INDEX_CATEGORY = _object({"category": _TEXT, "count": {"type": "string", "pattern": r"^[1-9][0-9]* 个$"},
                           "examples": {"type": "array", "maxItems": MAX_EXAMPLES, "uniqueItems": True,
                                        "items": _TEXT}})
#: 工具层的可调用索引（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-只列名称的可调用索引 ）：只有名称、品类与计数，不带动作与条件。
_CALLABLE_INDEX = _object({
    "lifestyle": {"type": "array", "uniqueItems": True, "items": _TEXT},
    "goods": {"type": "array", "items": _INDEX_CATEGORY},
    "query_categories": {"type": "array", "items": _object({"category": _TEXT,
                          "count": {"type": "integer", "minimum": 0}})},
})

TOOL_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "工具层",
    **_object({"layer": {"const": "工具层"}, "targets": {"type": "array", "items": _TARGET},
               "callable_index": _CALLABLE_INDEX}),
}


_GROUP = _object({"kind": {"enum": list(TARGET_KINDS)}, "action": {"enum": [*LIFE_VERBS, None]},
                  "way": {"enum": [*WAY_LABELS, None]}, "shared": {"type": "object"},
                  "targets": {"type": "array", "minItems": 1, "items": {"type": "object"}}})
COMPACT_TOOL_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "工具层",
    **_object({"layer": {"const": "工具层"}, "groups": {"type": "array", "items": _GROUP},
               "callable_index": _CALLABLE_INDEX}),
}

TOOL_LAYER_SCHEMA["$defs"] = {"compact": COMPACT_TOOL_LAYER_SCHEMA}

#: 人设层作息九键（键序即渲染顺序）：下班与到家没有固定时刻时为 null，其余七键必为非空短语。
_RHYTHM = _object({key: _NULLABLE_TEXT if key in ("leave_work", "arrive_home") else _TEXT
                   for key in ("wake", "leave_home", "lunch", "leave_work", "arrive_home", "dinner", "bedtime",
                               "sleep_midpoint", "sleep_need")})
_TASTES = _object({key: {"type": "array", "items": _TEXT} for key in ("content_genres", "food_preferences", "media")})
PERSONA_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "人设层",
    **_object({
        "layer": {"const": "人设层"},
        "identity": _object({"name": _TEXT, "age": {"type": ["integer", "null"]}, "occupation": _NULLABLE_TEXT,
                             "living_arrangement": _NULLABLE_TEXT}),
        "rhythm": {"oneOf": [{"type": "null"}, _RHYTHM]},
        "habits": {"type": "array", "maxItems": 5,
                   "items": _object({"trigger": _TEXT, "response": _TEXT, "time_window": _TEXT})},
        "tastes": {"oneOf": [{"type": "null"}, _TASTES]},
        "coping": _NULLABLE_TEXT,
        "relationships": {"type": "array", "items": _object({
            "person": _TEXT, "role": _NULLABLE_TEXT, "closeness": {"enum": ["很亲近", "较亲近", "一般", "疏远", None]},
            "channels": {"type": "array", "items": _TEXT}, "view": _NULLABLE_TEXT})},
        "preferences": {"type": "array", "items": _object({"subject": _TEXT, "preference": _TEXT,
                                                           "noted_on": _DATE})},
    }),
}

_NOTE = _object({"activity": _NULLABLE_TEXT, "text": _TEXT})
MEMORY_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "记忆层",
    **_object({
        "layer": {"const": "记忆层"},
        "earlier_days": {"type": "array", "items": _object({
            "date": _DATE, "settled": _LINES, "self_notes": {"type": "array", "items": _NOTE}})},
        "earlier_today": {"type": "array", "items": _object({
            "recorded_at": _TIME, "settled": _LINES, "self_notes": {"type": "array", "items": _NOTE}})},
    }),
}

_GOAL = _object({"intent_kind": {"enum": list(INTENT_KINDS)}, "goal": _TEXT, "progressed": {"type": "boolean"},
                 "remind_time": {"type": ["string", "null"], "pattern": r"^([01][0-9]|2[0-3]):[0-5][0-9]$"},
                 "around_time": {"type": ["string", "null"], "pattern": r"^([01][0-9]|2[0-3]):[0-5][0-9]$"}})
#: 待办层的一条个人事务（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-待办层与进度层呈现 ）：六键，不含内部身份、状态枚举与可用性。
_ERRAND = _object({"name": _TEXT, "done_when": _TEXT, "progress": _TEXT, "memo": _NULLABLE_TEXT,
                   "remind_time": {"type": ["string", "null"], "pattern": r"^([01][0-9]|2[0-3]):[0-5][0-9]$"},
                   "noted_on": _DATE})
TODO_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "待办层",
    **_object({
        "layer": {"const": "待办层"},
        "paused_activities": {"type": "array", "items": _object({
            "name": _TEXT, "paused_at": _TIME, "place": _TEXT, "progress": _TEXT,
            "tools": {"type": "array", "minItems": 2, "maxItems": 2, "prefixItems": [
                _tool_action(("report_status",)), _tool_action(("conclude_activity",))]}})},
        "errands": {"type": "array", "maxItems": 8, "items": _ERRAND},
        "day_plan": {"oneOf": [{"type": "null"}, _object({
            "date": _DATE, "goals": {"type": "array", "maxItems": 8, "items": _GOAL}})]},
    }),
}



#: 工作日写班次、工作地点名与四种状态之一。
_DUTY = _object({"day": {"enum": list(DUTY_DAYS)}, "hours": _NULLABLE_TEXT, "place": _NULLABLE_TEXT,
                 "breaks": {"type": "array", "items": _TEXT}, "status": {"enum": [*DUTY_STATUSES, None]}})
_DUTY["if"] = {"properties": {"day": {"const": "休息日"}}}
_DUTY["then"] = {"properties": {"hours": {"type": "null"}, "place": {"type": "null"},
                                "status": {"type": "null"}, "breaks": {"maxItems": 0}}}
_DUTY["else"] = {"properties": {"hours": _TEXT, "place": _TEXT, "status": {"enum": list(DUTY_STATUSES)}}}
ENVIRONMENT_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "环境层",
    **_object({
        "layer": {"const": "环境层"}, "observed_at": _TIME,
        "body": _object({key: {"enum": list(levels)} for key, levels in BODY_LEVELS.items()}),
        "location": _object({"session_kind": {"enum": ["stay", "transit"]}, "name": _TEXT, "place_type": _TEXT,
                             "fixed_rules": {"type": "array", "uniqueItems": True, "items": _TEXT}}),
        "duty": {"oneOf": [{"type": "null"}, _DUTY]},
    }),
}

_RESULT = _object({"occurred_at": _TIME, "status": {"enum": list(ACTION_STATUSES)},
                   "error_code": _NULLABLE_TEXT, "message": _TEXT, "details": {"type": "object", "properties": {
                       "app_content": _object({"app": _TEXT, "title": _TEXT,
                                               "summary": {"type": "string", "maxLength": 200},
                                               "pushed": {"type": "boolean"}}),
                       "work": {"type": "array", "items": _object(
                           {"name": _TEXT, "done": _TEXT, "remaining": _TEXT})}}},
                   })


def _record(kind: str, fields: dict[str, Any], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    """@brief 进度层的一种记录：occurred_at、type 与该类型的内容字段。"""
    return _object({"occurred_at": _TIME, "type": {"const": kind}, **fields}, optional=optional)


_MESSAGE_RECORD = _record("message_arrival", {
    "sender": _NULLABLE_TEXT, "kind": {"enum": ["direct", "chat_invite", "mail"]},
    "text": _TEXT, "subject": _TEXT,
}, optional=("text", "subject"))
_MESSAGE_RECORD["if"] = {"properties": {"kind": {"const": "mail"}}}
_MESSAGE_RECORD["then"] = {"required": ["subject"], "not": {"required": ["text"]}}
_MESSAGE_RECORD["else"] = {"required": ["text"], "not": {"required": ["subject"]},
                           "properties": {"sender": _TEXT}}
PROGRESS_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "进度层",
    **_object({
        "layer": {"const": "进度层"}, "session_started_at": _TIME,
        "rejected_today": {"type": "array", "maxItems": 8, "items": _object({
            "written": {}, "why": {"type": "string", "maxLength": 60},
            "times": {"type": "integer", "minimum": 1}, "last": _TIME, "accepted_as": {}})},
        "events": {"type": "array", "items": {"oneOf": [
            _record("tool_call", {"tool": {"type": "string", "pattern": r"^[a-z][a-z0-9_]*$"},
                                  "arguments": {"type": "object"}, "result": _RESULT, "self_note": _TEXT},
                    optional=("self_note",)),
            _MESSAGE_RECORD,
            _record("delivery", {"order": _TEXT, "status": {"enum": ["pending", "delivered"]},
                                 "message": _TEXT}),
            _record("app_push", {"app": _TEXT, "title": _TEXT,
                                  "summary": {"type": "string", "maxLength": 200}}),
            _record("work_completed", {"topic": _TEXT}),
            _record("plan_reminder", {"goal": _TEXT}),
            _record("errand_reminder", {"errand": _TEXT}),
            _record("errand_completed", {"errand": _TEXT}),
            _record("condensed", {"until_at": _TIME, "lines": _LINES}),
        ]}},
    }),
}

TOOL_DEFINITION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "原生工具定义",
    **_object({"type": {"const": "function"}, "function": _object({
        "name": {"type": "string", "pattern": r"^[a-z][a-z0-9_]*$"}, "description": _TEXT,
        "parameters": {"type": "object", "minProperties": 1}, "strict": {"const": True}})}),
}

#: user 消息中六层的固定顺序。
USER_LAYER_NAMES: tuple[str, ...] = ("工具层", "人设层", "记忆层", "待办层", "环境层", "进度层")
#: 层名（含系统层）→ Schema。
LAYER_SCHEMAS: dict[str, dict[str, Any]] = {
    "系统层": SYSTEM_LAYER_SCHEMA, "工具层": TOOL_LAYER_SCHEMA, "人设层": PERSONA_LAYER_SCHEMA,
    "记忆层": MEMORY_LAYER_SCHEMA, "待办层": TODO_LAYER_SCHEMA, "环境层": ENVIRONMENT_LAYER_SCHEMA,
    "进度层": PROGRESS_LAYER_SCHEMA,
}


def validate_layer(layer: dict) -> None:
    """@brief 按 layer 字段取对应 Schema 校验一层（jsonschema Draft 2020-12）；不符或层名未知时抛 ValueError。"""

    name = layer.get("layer") if isinstance(layer, dict) else None
    if name not in LAYER_SCHEMAS:
        raise ValueError(f"Unknown prompt layer: {name!r}")
    schema = COMPACT_TOOL_LAYER_SCHEMA if name == "工具层" and "groups" in layer else LAYER_SCHEMAS[name]
    errors = sorted(Draft202012Validator(schema).iter_errors(layer), key=lambda item: list(item.path))
    if name == "工具层" and "groups" in layer and not errors:

        validate_layer(expand_tool_layer(layer))
    if errors:
        first = errors[0]
        raise ValueError(f"Prompt layer {name} violates its schema at {list(first.path)}: {first.message}")
