"""@brief 人物提示七层的机器可读 Schema（JSON Schema 2020-12）与测试用的校验入口。

依据 docs/dev/SPEC-persona-prompt.md 第一章与接口合同登记：系统层、工具层、人设层、记忆层、待办层、环境层、进度层各一份
Schema，另有原生 tools 的一项定义。各层构建器的输出由测试逐层校验；发送前不做逐层校验（裁决 #2），运行时只有提示指纹
（life_prompt.life_prompt_sha256）读取本模块。枚举是冻结闭集的字面副本（体感分级、方式、动作状态），由测试锁定与源头一致。
"""

from __future__ import annotations

from typing import Any

#: 带时区的 ISO 8601 时刻（秒精度）。
_TIME = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$"}
#: 场景本地日期。
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
#: 非空文本与可空文本。
_TEXT = {"type": "string", "minLength": 1}
_NULLABLE_TEXT = {"type": ["string", "null"], "minLength": 1}
_LINES = {"type": "array", "items": _TEXT}

#: 工具层目标类别闭集（英文值；场景联系人并入 person）。
TARGET_KINDS = ("location", "product", "person", "content", "game", "catalog", "lifestyle", "task", "activity",
                "message", "self")
#: 五个生活动词（interaction_menu.LIFE_VERBS 的字面副本）。
VERBS = ("travel", "interact", "obtain", "consume", "sleep")
#: 工具层可列出的方式短语（interaction_menu.WAYS 键的字面副本，不含只写在请求里、由系统定方式的「购买」）。
WAY_LABELS = ("步行", "驾车", "工作", "观看", "游玩", "交谈", "通话", "阅读", "浏览", "进行", "等待", "到店", "远程下单",
              "制作", "食用", "使用", "给予", "接着看", "从头再看", "接着听", "从头再听", "睡觉")
#: 以目标名称为参数的其他工具。
TARGET_TOOLS = ("report_status", "pause_activity", "conclude_activity", "submit_work", "send_message")
#: 动作状态闭集（models.ActionStatus 值的字面副本）。
ACTION_STATUSES = ("submitted", "accepted", "in_progress", "completed", "rejected", "cancelled", "failed", "unknown")
#: 十一种当日意图（schedule.IntentKind 的字面副本）。
INTENT_KINDS = ("睡眠", "晨间例程", "通勤", "工作", "用餐", "休息", "娱乐", "社交", "家务", "运动", "外出")
#: 五项体感分级文字（felt 分级表的字面副本）。
BODY_LEVELS = {
    "appetite": ("不饿", "有点想吃", "饿", "很饿", "饿得难受", "饿得发慌"),
    "sleepiness": ("清醒", "有点乏", "困", "很困", "撑不住"),
    "entertainment": ("不闷", "有点想放松", "很想放松"),
    "social": ("不想找人", "想和人说说话", "有点孤单"),
    "mood": ("很烦", "有点烦", "平静", "挺好", "很开心"),
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
    "action": {"enum": list(VERBS)}, "way": {"enum": list(WAY_LABELS)},
    "price": _NULLABLE_TEXT, "duration": _NULLABLE_TEXT, "quantity": _NULLABLE_TEXT, "note": _NULLABLE_TEXT})
_CONTRACT = _object({"opens": _TIME, "deadline": _TIME, "place": _TEXT,
                     "remaining": _TEXT, "outlook": _NULLABLE_TEXT})


def _tools_for(kind: str, tools: tuple[str, ...]) -> dict[str, Any]:
    """@brief 某类目标的 tools 形状：按固定顺序列出该类的工具。"""
    return {"if": {"properties": {"kind": {"const": kind}}},
            "then": {"properties": {"tools": {"prefixItems": [
                {"properties": {"tool": {"const": tool}}} for tool in tools],
                "minItems": len(tools), "maxItems": len(tools)}}}}


_TARGET = _object({
    "name": _TEXT, "kind": {"enum": list(TARGET_KINDS)}, "phase": {"enum": ["进行中", "刚结束", None]},
    "state": _NULLABLE_TEXT, "contract": _CONTRACT,
    "actions": {"type": "array", "items": _VERB_ACTION},
    "tools": {"type": "array", "items": _tool_action(TARGET_TOOLS)},
}, optional=("contract",))
_TARGET["allOf"] = [
    {"if": {"properties": {"kind": {"const": "task"}}}, "then": {"required": ["contract"]},
     "else": {"not": {"required": ["contract"]}}},
    {"if": {"properties": {"kind": {"const": "activity"}}},
     "then": {"properties": {"phase": {"enum": ["进行中", "刚结束"]}, "actions": {"maxItems": 0}}},
     "else": {"properties": {"phase": {"type": "null"}}}},
    _tools_for("activity", ("report_status", "pause_activity", "conclude_activity")),
    _tools_for("task", ("submit_work",)),
    _tools_for("person", ("send_message",)),
    {"if": {"properties": {"kind": {"enum": ["activity", "task", "person"]}}}, "else": {
        "properties": {"tools": {"maxItems": 0}}}},
]

SYSTEM_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "系统层",
    **_object({"layer": {"const": "系统层"}, "role": _TEXT,
               "rules": {"type": "array", "minItems": 1, "uniqueItems": True, "items": _TEXT}}),
}

#: 可调用索引每个品类至多给出的示例名数（callable_index.MAX_EXAMPLES 的字面副本）。
INDEX_EXAMPLES_MAX = 3
_INDEX_CATEGORY = _object({"category": _TEXT, "count": {"type": "string", "pattern": r"^[1-9][0-9]* 个$"},
                           "examples": {"type": "array", "maxItems": INDEX_EXAMPLES_MAX, "uniqueItems": True,
                                        "items": _TEXT}})
#: 工具层的可调用索引（docs/dev/SPEC-dayrun-fidelity.md 5.2）：只有名称、品类与计数，不带动作与条件。
_CALLABLE_INDEX = _object({
    "lifestyle": {"type": "array", "uniqueItems": True, "items": _TEXT},
    "contents": {"type": "array", "items": _INDEX_CATEGORY},
    "goods": {"type": "array", "items": _INDEX_CATEGORY},
    "query_categories": {"type": "array", "items": _object({"category": _TEXT,
                          "count": {"type": "integer", "minimum": 0}})},
})

TOOL_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "工具层",
    **_object({"layer": {"const": "工具层"}, "targets": {"type": "array", "items": _TARGET},
               "callable_index": _CALLABLE_INDEX}),
}


_GROUP = _object({"kind": {"enum": list(TARGET_KINDS)}, "action": {"enum": [*VERBS, None]},
                  "way": {"enum": [*WAY_LABELS, None]}, "shared": {"type": "object"},
                  "targets": {"type": "array", "minItems": 1, "items": {"type": "object"}}})
COMPACT_TOOL_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "工具层",
    **_object({"layer": {"const": "工具层"}, "groups": {"type": "array", "items": _GROUP},
               "callable_index": _CALLABLE_INDEX}),
}

TOOL_LAYER_SCHEMA["$defs"] = {"compact": COMPACT_TOOL_LAYER_SCHEMA}

_RHYTHM = _object({key: _TEXT for key in ("wake", "leave_home", "lunch", "dinner", "bedtime", "sleep_midpoint",
                                          "sleep_need")})
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
#: 待办层的一条个人事务（docs/dev/SPEC-errands-workday.md 2.6）：六键，不含内部身份、状态枚举与可用性。
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

ENVIRONMENT_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "环境层",
    **_object({
        "layer": {"const": "环境层"}, "observed_at": _TIME,
        "body": _object({key: {"enum": list(levels)} for key, levels in BODY_LEVELS.items()}),
        "location": _object({"session_kind": {"enum": ["stay", "transit"]}, "name": _TEXT, "place_type": _TEXT,
                             "fixed_rules": {"type": "array", "uniqueItems": True, "items": _TEXT}}),
    }),
}

_RESULT = _object({"occurred_at": _TIME, "status": {"enum": list(ACTION_STATUSES)},
                   "error_code": _NULLABLE_TEXT, "message": _TEXT, "details": {"type": "object"},
                   "work_review": {"type": "object"}}, optional=("work_review",))


def _record(kind: str, fields: dict[str, Any], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    """@brief 进度层的一种记录：occurred_at、type 与该类型的内容字段。"""
    return _object({"occurred_at": _TIME, "type": {"const": kind}, **fields}, optional=optional)


_MESSAGE_RECORD = _record("message_arrival", {
    "sender": _NULLABLE_TEXT, "kind": {"enum": ["direct", "chat_invite", "mail"]},
    "text": _TEXT, "subject": _TEXT, "task": _TEXT,
}, optional=("text", "subject", "task"))
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
            _record("delivery", {"order": _TEXT, "status": {"enum": ["ready", "delivered", "cancelled"]},
                                 "message": _TEXT}),
            _record("task_deadline", {"task": _TEXT, "stage": {"enum": ["due_soon", "expired"]}}),
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
    from jsonschema import Draft202012Validator

    name = layer.get("layer") if isinstance(layer, dict) else None
    if name not in LAYER_SCHEMAS:
        raise ValueError(f"Unknown prompt layer: {name!r}")
    schema = COMPACT_TOOL_LAYER_SCHEMA if name == "工具层" and "groups" in layer else LAYER_SCHEMAS[name]
    errors = sorted(Draft202012Validator(schema).iter_errors(layer), key=lambda item: list(item.path))
    if name == "工具层" and "groups" in layer and not errors:
        from worldmock.life.tool_groups import expand_tool_layer

        validate_layer(expand_tool_layer(layer))
    if errors:
        first = errors[0]
        raise ValueError(f"Prompt layer {name} violates its schema at {list(first.path)}: {first.message}")
