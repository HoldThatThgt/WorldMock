"""@brief 人物提示七层的机器可读 Schema（JSON Schema 2020-12）与测试用的校验入口。

依据 docs/design/06-agent.md与接口合同登记：系统层、工具层、人设层、记忆层、待办层、环境层、进度层各一份
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
from worldmock.life.action_contract import ACTIVITY_ACTIONS
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
#: 内容简介：可以为空，至多 200 字；推送记录与结果明细里的内容共用这一种写法。
_SUMMARY = {"type": "string", "maxLength": 200}

#: 工具层目标类别闭集，只含当前真实目标。
TARGET_KINDS = ("location", "product", "app", "activity")
#: 以目标名称为参数的其他工具。
TARGET_TOOLS = CONTROL_TOOLS
#: 动作状态由生产枚举按声明顺序投影。
ACTION_STATUSES = tuple(status.value for status in ActionStatus)
#: 五项体感分级文字由实际分级表派生。
BODY_LEVELS = {
    "appetite": (felt.APETITE_BASE_LEVEL, *(label for _, label in felt.APPETITE_BANDS),
                 felt.APPETITE_STARVING_LEVEL),
    "sleepiness": (felt.SLEEPINESS_BASE_LEVEL, *(label for _, label in felt.SLEEPINESS_BANDS)),
    "relaxation": ("不闷", "有点想放松", "很想放松"),
    "social": (felt.SOCIAL_BASE_LEVEL, *(label for _, label in felt.SOCIAL_BANDS)),
}

def _object(properties: dict[str, Any], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    """@brief 封闭对象：properties 中除 optional 以外的键都必填，不接受其他键。"""
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": [key for key in properties if key not in optional]}


def _reason_unless(schema: dict[str, Any], condition: dict[str, Any]) -> dict[str, Any]:
    """@brief 给带 reason 键的封闭对象加条件：满足 condition 时 reason 为 null，否则 reason 是非空原因。"""
    schema["if"] = {"properties": condition}
    schema["then"] = {"properties": {"reason": {"type": "null"}}}
    schema["else"] = {"properties": {"reason": _TEXT}}
    return schema


def _availability(extra: dict[str, Any]) -> dict[str, Any]:
    """@brief 带 available 与 reason 的对象：可用时 reason 为 null，不可用时 reason 是非空原因。"""
    return _reason_unless(_object({**extra, "available": {"type": "boolean"}, "reason": _NULLABLE_TEXT}),
                          {"available": {"const": True}})


def _tool_action(tools: tuple[str, ...]) -> dict[str, Any]:
    """@brief 以目标名称为参数的一项工具及其可用性。"""
    return _availability({"tool": {"enum": list(tools)}})


_VERB_ACTION = _availability({
    "action": {"enum": list(ACTIVITY_ACTIONS)}, "detail": _NULLABLE_TEXT, "price": _NULLABLE_TEXT,
    "duration": _NULLABLE_TEXT, "quantity": _NULLABLE_TEXT, "note": _NULLABLE_TEXT})




_TARGET = _object({
    "name": _TEXT, "kind": {"enum": list(TARGET_KINDS)}, "phase": {"enum": ["进行中", "刚结束", None]},
    "state": _NULLABLE_TEXT, "last_report": _TEXT,
    "actions": {"type": "array", "items": _VERB_ACTION},
    "tools": {"type": "array", "items": _tool_action(TARGET_TOOLS)},
}, optional=("last_report",))
_TARGET["allOf"] = [
    {"if": {"properties": {"kind": {"const": "activity"}}},
     "then": {"properties": {"phase": {"enum": ["进行中", "刚结束"]}, "actions": {"maxItems": 0}}},
     "else": {"properties": {"phase": {"type": "null"}}}},
    {"if": {"properties": {"kind": {"const": "activity"}}}, "else": {
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
#: 工具层的可调用索引（docs/design/04-simulation-engine.md）：只有名称、品类与计数，不带动作与条件。
_CALLABLE_INDEX = _object({
    "goods": {"type": "array", "items": _INDEX_CATEGORY},
    "query_categories": {"type": "array", "items": _object({"category": _TEXT,
                          "count": {"type": "integer", "minimum": 0},
                          "examples": {"type": "array", "maxItems": MAX_EXAMPLES, "items": _TEXT}})},
})

TOOL_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "工具层",
    **_object({"layer": {"const": "工具层"}, "targets": {"type": "array", "items": _TARGET},
               "callable_index": _CALLABLE_INDEX}),
}


_GROUP = _object({"kind": {"enum": list(TARGET_KINDS)}, "action": {"enum": [*ACTIVITY_ACTIONS, None]},
                  "shared": {"type": "object"},
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

_GOAL = _object({"intent_kind": {"enum": list(INTENT_KINDS)}, "goal": _TEXT,
                 "remind_time": {"type": ["string", "null"], "pattern": r"^([01][0-9]|2[0-3]):[0-5][0-9]$"},
                 "around_time": {"type": ["string", "null"], "pattern": r"^([01][0-9]|2[0-3]):[0-5][0-9]$"}})
#: 待办层的一条个人事务（docs/design/04-simulation-engine.md）：六键，不含内部身份、状态枚举与可用性。
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
        "location": _object({"session_kind": {"enum": ["stay", "transit"]}, "name": _TEXT,
                             "travel_method": _TEXT}, optional=("travel_method",)),
        "weather": {"enum": ["晴", "多云", "小雨", "大雨", "雪"]},
        "duty": {"oneOf": [{"type": "null"}, _DUTY]},
    }),
}

#: 购买明细的一行：channel 是下单 app 的全名或「到店」；没有付款的配 price，付了款的配 paid。
_PURCHASE = _object({"product": _TEXT, "quantity": _TEXT, "channel": _TEXT, "destination": _TEXT,
                     "expected_at": _TIME, "paid": _TEXT, "price": _TEXT}, optional=("paid", "price"))
_PURCHASE["oneOf"] = [{"required": ["paid"], "not": {"required": ["price"]}},
                       {"required": ["price"], "not": {"required": ["paid"]}}]
#: 第一条结果里实际开始的那条内容；在地点做的事 app 为 null。
_CONTENT = _object({"app": _NULLABLE_TEXT, "title": _TEXT, "summary": _SUMMARY, "duration": _TEXT,
                    "pushed": {"type": "boolean"}})
#: 第二条结果里做过的各条内容，至少一行；finished 表示这条做到了头。
_CONTENTS = {"type": "array", "minItems": 1, "items": _object({
    "title": _TEXT, "summary": _SUMMARY, "duration": _TEXT, "finished": {"type": "boolean"}})}
#: 查询答案的一行：九个键都出现；可以下单时 reason 为 null，不可下单时是非空原因。
_QUERY_ROW = _reason_unless(_object({
    "product": _TEXT, "route": {"enum": ["到店", "外卖", "快递", None]}, "store": _NULLABLE_TEXT,
    "apps": {"type": "array", "uniqueItems": True, "items": _TEXT}, "price": _TEXT,
    "delivery_fee": _NULLABLE_TEXT, "expected_at": {**_TIME, "type": ["string", "null"]},
    "now": {"enum": ["可以下单", "不可下单"]}, "reason": _NULLABLE_TEXT}), {"now": {"const": "可以下单"}})
#: 查完的答案：app 为 null 时一定列出全部途径；没列出的件数为 0 时不写 omitted。
_QUERY_ANSWERED = _object({
    "app": _NULLABLE_TEXT, "scope": {"enum": ["只列经这个 app 能买的", "列出全部途径"]},
    "rows": {"type": "array", "items": _QUERY_ROW},
    "omitted": {"type": "string", "pattern": r"^还有 [1-9][0-9]* 件没列$"}}, optional=("omitted",))
_QUERY_ANSWERED["if"] = {"properties": {"app": {"type": "null"}}}
_QUERY_ANSWERED["then"] = {"properties": {"scope": {"const": "列出全部途径"}}}
#: 查询的第二条结果：查完的答案，或提前结束、被截断时的「没有查完」。
_QUERY = {"oneOf": [_QUERY_ANSWERED, _object({"app": _NULLABLE_TEXT, "state": {"const": "没有查完"}})]}
_RESULT_DETAILS = _object({
    "purchases": {"type": "array", "items": _PURCHASE},
    "planned": {"type": "array", "items": _object({"name": _TEXT, "duration": _TEXT})},
    "content": _CONTENT, "contents": _CONTENTS, "query": _QUERY,
    "arrival": _object({"place": _TEXT, "access": {"enum": ["进入", "没能进去"]}}),
    "recall": {"type": "object"}, "reason": _TEXT, "next_step": _TEXT, "fix": _TEXT,
    "items": {"type": "array", "items": {"type": "object"}},
    "outcome": {"type": "array", "items": _object({"activity": _TEXT, "outcome": _TEXT})},
    "resolved": {"type": "array", "items": {"type": "object"}},
    "notes": {"type": "array", "items": _TEXT},
    "errands": {"type": "array", "items": _object({"errand": _TEXT, "change": _TEXT})},
}, optional=("purchases", "planned", "content", "contents", "query", "arrival", "recall", "reason", "next_step",
             "fix", "items", "outcome", "resolved", "notes", "errands"))
_RESULT = _object({"occurred_at": _TIME, "status": {"enum": list(ACTION_STATUSES)},
                   "error_code": _NULLABLE_TEXT, "message": _TEXT, "details": _RESULT_DETAILS})


def _record(kind: str, fields: dict[str, Any], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    """@brief 进度层的一种记录：occurred_at、type 与该类型的内容字段。"""
    return _object({"occurred_at": _TIME, "type": {"const": kind}, **fields}, optional=optional)


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
            _record("delivery", {"order": _TEXT, "status": {"enum": ["pending", "ready_for_pickup", "delivered"]},
                                 "message": _TEXT}),
            _record("app_push", {"app": _TEXT, "title": _TEXT, "summary": _SUMMARY}),
            _record("callback_reminder", {"app": _TEXT, "text": _TEXT}),
            _record("balance_decreased", {"message": _TEXT}),
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
