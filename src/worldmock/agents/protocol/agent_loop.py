"""@brief 与具体业务解耦的通用异步模型循环：容量预检、证据记录、截断重试、纠错配对与挂起退出。

来源：本模块部分代码改写自 AgentSociety2 2.8.7（上游提交 670c94ff），按 Apache-2.0 使用，许可全文见 LICENSES/AgentSociety2-Apache-2.0.txt。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from worldmock.agents.prompt.prompt_capacity import FittedRequest, ProgressBudget, compare_usage, fit_request
from worldmock.agents.protocol.life_feedback import (
    ProtocolFeedback,
    argument_feedback,
    host_feedback,
    report_content,
    stage_error,
    stage_feedback,
)
from worldmock.agents.protocol.life_truncation import (
    RETRY_TOOL_CHOICE,
    retry_due,
    thinking_enabled,
    truncated,
    truncation_feedback,
    with_feedback,
)
from worldmock.configuration.model.model_client import collect_transport_attempts
from worldmock.configuration.model.model_registry import ResolvedModelBinding
from worldmock.foundation.errors import ProtocolError
from worldmock.state.interaction.errors import ErrorItem, ErrorReport, bind_report, host_report
from worldmock.state.interaction.schema import deepseek_strict_schema
from worldmock.state.model.exchange import (
    ModelCallEvidence,
    assistant_message,
    response_self_note,
    wire_tool_call,
)
from worldmock.state.model.truncation import RETRY_THINKING

#: 回合预算耗尽时的默认协议错误原因。
TURNS_EXHAUSTED = "Model turn limit exhausted"
_FUNC_TYPE = "function"


@dataclass(frozen=True, slots=True)
class LoopToolCall:
    """@brief 单条完成 JSON 解析的原生工具调用。"""

    tool: str
    arguments: dict[str, Any]
    tool_call_id: str


@dataclass(frozen=True, slots=True)
class LoopStepOutcome:
    """@brief 业务回调执行完一回合工具调用后向通用循环返回的推进状态。"""

    status: Literal["continue", "completed", "suspended"]
    tool_messages: tuple[dict[str, Any], ...] = ()
    payload: Any = None


@dataclass(frozen=True, slots=True)
class LoopOutcome:
    """@brief 通用模型循环完成或挂起退出时的完整产出。"""

    status: Literal["completed", "suspended"]
    turns_used: int
    messages: tuple[dict[str, Any], ...]
    payload: Any = None


@dataclass(frozen=True, slots=True)
class LoopConfig:
    """@brief 单次循环运行所需的不可变身份、预算与用途参数。"""

    scope_id: str
    wakeup_id: str
    agent_id: int
    call_kind: str
    binding: ResolvedModelBinding
    start_turn: int = 1
    max_turns: int | None = None
    allow_thinking: bool = True
    purpose: str | None = None
    activity_id: str | None = None
    protected_prefix: int = 2
    exhausted_message: str = TURNS_EXHAUSTED


@dataclass(frozen=True, slots=True)
class LoopCallbacks:
    """@brief 通用循环调用具体业务方的五个无状态回调。"""

    turn_tools: Callable[[int, bool], tuple[list[dict[str, Any]], dict[str, type[BaseModel]]]]
    validate_call: Callable[[LoopToolCall, Mapping[str, type[BaseModel]]], ProtocolFeedback | None]
    validate_batch: Callable[[Sequence[LoopToolCall], Mapping[str, type[BaseModel]]], ProtocolFeedback | None]
    execute_calls: Callable[[list[LoopToolCall], str | None], LoopStepOutcome]
    record_exchange: Callable[[ModelCallEvidence], None]


def model_call_id(scope_id: str, kind: str, turn: int, attempt: int = 1) -> str:
    """@brief 按作用域身份、阶段类型、连续回合号与尝试号生成确定性调用身份。"""
    return f"{scope_id}:{kind}:{turn:04d}:attempt:{attempt}"


def build_tool_schema(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    """@brief 由唯一严格请求模型与用途说明构造原生 function 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": deepseek_strict_schema(model),
            "strict": True,
        },
    }


def validate_model_request(model: type[BaseModel], values: Mapping[str, Any]) -> BaseModel:
    """@brief 用严格 JSON 模式校验工具参数字典。"""
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return model.model_validate_json(encoded, strict=True)


def validate_single_tool_call(call: LoopToolCall, models: Mapping[str, type[BaseModel]]) -> ProtocolFeedback | None:
    """@brief 用给定开放工具表校验单次工具调用的名称存在性与 Pydantic 严格参数合同。"""
    model = models.get(call.tool)
    if model is None:
        item = ErrorItem(("tool_calls", 0, "function", "name"), call.tool, "TOOL_CALL_REQUIRED",
                         tuple(models), "A tool offered in this round is required.",
                         "The tool is not offered in this round.")
        return host_feedback(host_report("TOOL_CALL_REQUIRED", items=(item,)), item.why)
    try:
        validate_model_request(model, call.arguments)
    except ValidationError as error:
        return argument_feedback(call.tool, error, model, call.arguments)
    return None


def tool_message(call_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 把工具结果或查询拒绝压缩序列化成原生 tool 对话消息。"""
    return {"role": "tool", "tool_call_id": call_id,
            "content": json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))}


def dispatch_tool_safely(label: str, index: int, fn: Callable[[], Any]) -> Any:
    """@brief 调用外部沙盒或目录服务；异常时写错误日志并保留真实调用位置抛 TOOL_SERVICE_FAILED。"""
    try:
        return fn()
    except Exception as error:
        logging.exception("Tool service failed during %s", label)
        raise stage_error("TOOL_SERVICE_FAILED", ("tool_calls", index)) from error


def combine_reports(reports: Sequence[ErrorReport]) -> ProtocolFeedback:
    """@brief 按调用位置合并多条错误报告，保留全部独立明细并去重拼装原因与改法。"""
    items = tuple(item for report in reports for item in report.items)
    reason = "; ".join(dict.fromkeys(report.reason for report in reports))
    fix = "; ".join(dict.fromkeys(report.fix for report in reports if report.fix)) or None
    merged = ErrorReport(
        reports[0].error_code,
        reason,
        fix,
        items,
        tuple(dict.fromkeys(value for report in reports for value in report.unmet)),
    )
    return host_feedback(merged, reason)


def parse_tool_calls(
    calls: Sequence[Any],
    models: Mapping[str, type[BaseModel]],
) -> tuple[list[tuple[int, LoopToolCall]], list[ErrorReport]]:
    """@brief 解析原生 tool_calls：收集工具名未知、重复或缺失调用身份、非法 JSON 参数的明细报告。"""
    if not calls:
        reason = "At least one complete native tool call is required."
        item = ErrorItem(("tool_calls",), list(calls), "TOOL_CALL_REQUIRED", tuple(models), reason, reason)
        return [], [host_report("TOOL_CALL_REQUIRED", reason, items=(item,))]
    parsed, reports, seen = [], [], set()
    for index, call in enumerate(calls):
        parsed_item, item_reports = _parse_single_call(call, index, models, seen)
        reports.extend(item_reports)
        if parsed_item is not None:
            parsed.append((index, parsed_item))
    return parsed, reports


def _parse_single_call(
    call: Any,
    index: int,
    models: Mapping[str, type[BaseModel]],
    seen: set[Any],
) -> tuple[LoopToolCall | None, list[ErrorReport]]:
    """@brief 校验单条工具调用的名字、身份和参数 JSON，合法则产出 LoopToolCall。"""
    reports: list[ErrorReport] = []
    fn = getattr(call, "function", None)
    name = getattr(fn, "name", None)
    identity = getattr(call, "id", None)
    if name not in models:
        item = ErrorItem(
            ("tool_calls", index, "function", "name"), name, "TOOL_CALL_REQUIRED",
            tuple(models), "A tool offered in this round is required.", "The tool is not offered in this round.",
        )
        reports.append(host_report("TOOL_CALL_REQUIRED", items=(item,)))
    if not identity or identity in seen:
        item = ErrorItem(
            ("tool_calls", index, "id"), identity, "TOOL_CALL_REQUIRED",
            expected="A non-empty unique tool call identity is required.",
            why="The tool call identity is missing or duplicated.",
        )
        reports.append(host_report("TOOL_CALL_REQUIRED", items=(item,)))
    seen.add(identity)
    arguments, arg_error = _parse_arguments(getattr(fn, "arguments", None), index)
    if arg_error is not None:
        reports.append(arg_error)
    elif name in models and arguments is not None:
        return LoopToolCall(name, arguments, identity), reports
    return None, reports


def _parse_arguments(raw: Any, index: int) -> tuple[dict[str, Any] | None, ErrorReport | None]:
    """@brief 解析单条调用的 JSON 对象参数串。"""
    path = ("tool_calls", index, "function", "arguments")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else None
    except json.JSONDecodeError as error:
        expected = f"A complete JSON object is required; parsing failed at line {error.lineno}, column {error.colno}."
        item = ErrorItem(path, raw, "ARGUMENTS_NOT_JSON", expected=expected,
                         why="The arguments JSON is malformed or incomplete.")
        return None, host_report("ARGUMENTS_NOT_JSON", items=(item,))
    if not isinstance(parsed, dict):
        item = ErrorItem(path, parsed, "ARGUMENTS_NOT_JSON", expected="A complete JSON object is required.",
                         why="The tool arguments are not a JSON object.")
        return None, host_report("ARGUMENTS_NOT_JSON", items=(item,))
    return parsed, None


class AgentLoop:
    """@brief 通用多回合模型决策循环器。"""

    def __init__(self, config: LoopConfig, callbacks: LoopCallbacks, budget: ProgressBudget | None = None) -> None:
        """@brief 绑定本次循环的配置、业务回调与可浓缩上下文预算。"""
        self.config = config
        self.callbacks = callbacks
        self.budget = budget

    async def run(self, initial_messages: Sequence[dict[str, Any]]) -> LoopOutcome:
        """@brief 按回合预算持续对话直至完成、挂起或预算耗尽抛 ProtocolError。"""
        history = [copy.deepcopy(item) for item in initial_messages]
        previous: dict[str, Any] = {}
        start = self.config.start_turn
        base_limit = (self.config.max_turns if self.config.max_turns is not None
                      else self.config.binding.profile.max_react_turns)
        limit = max(base_limit, start)
        for turn in range(start, limit + 1):
            remaining = limit - turn + 1
            retry = retry_due(history, previous)
            fitted, raw, attempts = await self._invoke_turn(history, turn, remaining, retry)
            previous = fitted.request
            decisions, feedback, payload = self._validate_reply(raw, remaining, retry)
            self._write_response_evidence(turn, fitted, payload, feedback, attempts)
            if feedback is not None:
                history = _append_correction(history, fitted.request, raw, feedback, self.config.protected_prefix)
                continue
            history.append(_response_assistant_message(raw))
            step = self.callbacks.execute_calls(decisions, response_self_note(payload))
            if step.status in ("completed", "suspended"):
                return LoopOutcome(step.status, turn, tuple(history), step.payload)
            history.extend(step.tool_messages)
        logging.error("%s", self.config.exhausted_message)
        raise ProtocolError(self.config.exhausted_message)

    async def _invoke_turn(
        self,
        history: list[dict[str, Any]],
        turn: int,
        remaining: int,
        retry: bool,
    ) -> tuple[FittedRequest, Any, list[dict[str, Any]]]:
        """@brief 执行单回合容量预检、请求留证与模型异步发送。"""
        tools, _ = self.callbacks.turn_tools(remaining, retry)
        request = self._turn_request(history, tools, retry)
        fitted = fit_request(request, self.config.binding.metadata, self.budget)
        call_id = model_call_id(self.config.scope_id, self.config.call_kind, turn)
        self._emit_exchange(ModelCallEvidence(
            self.config.wakeup_id, self.config.agent_id, call_id, "request", 1, copy.deepcopy(fitted.request),
            purpose=self.config.purpose, activity_id=self.config.activity_id,
        ))
        with collect_transport_attempts() as attempts:
            try:
                raw = await self.config.binding.client.call(fitted.request)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.error("Model service failed during %s call %s: %s", self.config.call_kind, call_id, exc)
                self._emit_failure_evidence(call_id, fitted.request, attempts)
                raise
        return fitted, raw, attempts

    def _turn_request(self, history: list[dict[str, Any]], tools: list[dict[str, Any]], retry: bool) -> dict[str, Any]:
        """@brief 构造满足六键次序的模型请求。"""
        thinking_on = self.config.allow_thinking and self.config.binding.profile.thinking and not retry
        extra_body = dict(RETRY_THINKING) if not thinking_on else {"thinking": {"type": "enabled"}}
        return {
            "model": self.config.binding.profile.requested_model,
            "messages": copy.deepcopy(history),
            "tools": tools,
            "tool_choice": RETRY_TOOL_CHOICE if retry else "auto",
            "max_tokens": self.config.binding.effective_max_tokens,
            "extra_body": extra_body,
        }

    def _validate_reply(
        self,
        raw: Any,
        remaining: int,
        retry: bool,
    ) -> tuple[list[LoopToolCall], ProtocolFeedback | None, dict[str, Any]]:
        """@brief 核验截断、原生调用解析与业务级整批规则；返回合法调用列表与可能纠错。"""
        payload = _raw_response_dict(raw)
        if truncated(raw):
            return [], truncation_feedback(), payload
        _, models = self.callbacks.turn_tools(remaining, retry)
        calls = _raw_tool_calls(raw)
        indexed, parse_reports = parse_tool_calls(calls, models)
        reports: list[ErrorReport] = []
        if calls:
            batch_fb = self.callbacks.validate_batch(_raw_batch_stub(calls), models)
            if batch_fb is not None:
                reports.append(batch_fb.report)
        reports.extend(parse_reports)
        for index, item in indexed:
            call_fb = self.callbacks.validate_call(item, models)
            if call_fb is not None:
                reports.append(bind_report(call_fb.report, index))
        if reports:
            return [], combine_reports(reports), payload
        return [item for _, item in indexed], None, payload

    def _write_response_evidence(
        self,
        turn: int,
        fitted: FittedRequest,
        payload: dict[str, Any],
        feedback: ProtocolFeedback | None,
        attempts: list[dict[str, Any]],
    ) -> None:
        """@brief 复用发送前计量结果做用量核对并写入响应证据，带协议纠错时附加 protocol_feedback 与 error。"""
        compare_usage(fitted.prompt_tokens, payload)
        recorded = {**payload, "protocol_feedback": feedback.report.payload()} if feedback else payload
        call_id = model_call_id(self.config.scope_id, self.config.call_kind, turn)
        self._emit_exchange(ModelCallEvidence(
            self.config.wakeup_id, self.config.agent_id, call_id, "response", 1, copy.deepcopy(fitted.request),
            response=recorded, error=feedback.text if feedback else None,
            transport_attempts=tuple(attempts), purpose=self.config.purpose, activity_id=self.config.activity_id,
        ))

    def _emit_failure_evidence(self, call_id: str, fitted: dict[str, Any], attempts: list[dict[str, Any]]) -> None:
        """@brief 网络或服务调用失败时写入 failure 阶段证据。"""
        fb = stage_feedback("MODEL_SERVICE_FAILED", ("tool_calls",))
        self._emit_exchange(ModelCallEvidence(
            self.config.wakeup_id, self.config.agent_id, call_id, "failure", 1, copy.deepcopy(fitted),
            response={"protocol_feedback": fb.report.payload()}, error=fb.text,
            transport_attempts=tuple(attempts), purpose=self.config.purpose, activity_id=self.config.activity_id,
        ))

    def _emit_exchange(self, exchange: ModelCallEvidence) -> None:
        """@brief 委托回调写入模型证据，写入异常时转为 EVIDENCE_WRITE_FAILED。"""
        try:
            self.callbacks.record_exchange(exchange)
        except Exception as error:
            logging.exception("Failed to record model exchange %s (%s)", exchange.call_id, exchange.phase)
            raise stage_error("EVIDENCE_WRITE_FAILED", ("tool_calls",)) from error


def _raw_tool_calls(raw: Any) -> list[Any]:
    """@brief 从模型回复对象安全提取第一条候选消息中的 tool_calls 列表。"""
    choices = getattr(raw, "choices", None)
    message = getattr(choices[0], "message", None) if choices else None
    return list(getattr(message, "tool_calls", None) or [])


def _raw_batch_stub(calls: Sequence[Any]) -> list[LoopToolCall]:
    """@brief 由原始 tool_calls 构造仅带工具名与调用身份的轻量列表，用于部分 JSON 损坏时仍执行批次形状核验。"""
    return [
        LoopToolCall(
            str(getattr(getattr(call, "function", None), "name", "") or ""),
            {},
            str(getattr(call, "id", "") or ""),
        )
        for call in calls
    ]


def _raw_response_dict(raw: Any) -> dict[str, Any]:
    """@brief 将模型客户端返回值统一转为可 JSON 化的纯字典用于证据落盘。"""
    if isinstance(raw, Mapping):
        return copy.deepcopy(dict(raw))
    if hasattr(raw, "model_dump"):
        return raw.model_dump(mode="json")
    choices = []
    for choice in getattr(raw, "choices", None) or ():
        msg = getattr(choice, "message", None)
        calls = [wire_tool_call(getattr(c, "id", None), getattr(getattr(c, "function", None), "name", None),
                                getattr(getattr(c, "function", None), "arguments", None))
                 for c in (getattr(msg, "tool_calls", None) or ())]
        msg_dict: dict[str, Any] = {"role": getattr(msg, "role", "assistant"), "content": getattr(msg, "content", None)}
        if calls:
            msg_dict["tool_calls"] = calls
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            msg_dict["reasoning_content"] = reasoning
        choices.append({"finish_reason": getattr(choice, "finish_reason", None), "message": msg_dict})
    usage = getattr(raw, "usage", None)
    return {"model": getattr(raw, "model", None), "choices": choices,
            "usage": dict(usage) if isinstance(usage, Mapping) else getattr(usage, "__dict__", None)}


def _response_assistant_message(raw: Any) -> dict[str, Any]:
    """@brief 构造回传对话历史的 assistant 消息，严格保留推理内容与调用定义。"""
    choices = getattr(raw, "choices", None)
    message = getattr(choices[0], "message", None) if choices else None
    content = getattr(message, "content", None) if message is not None else None
    reasoning = getattr(message, "reasoning_content", None) if message is not None else None
    calls = [
        wire_tool_call(
            getattr(call, "id", None),
            getattr(getattr(call, "function", None), "name", None),
            getattr(getattr(call, "function", None), "arguments", None),
        )
        for call in _raw_tool_calls(raw)
    ]
    return assistant_message(content, calls, reasoning)


def _pairable_calls(calls: Sequence[Any]) -> bool:
    """@brief 仅当回复带非空合法结构原生 tool_calls 且调用编号互异时，才能用 tool 消息配对纠错。"""
    if not calls:
        return False
    ids: list[str] = []
    for call in calls:
        fn = getattr(call, "function", None)
        cid = getattr(call, "id", None)
        name = getattr(fn, "name", None)
        if (getattr(call, "type", None) != _FUNC_TYPE or not isinstance(cid, str) or not cid.strip()
                or not isinstance(name, str) or not name.strip()
                or not isinstance(getattr(fn, "arguments", None), str)):
            return False
        ids.append(cid)
    return len(set(ids)) == len(ids)


def _append_correction(
    history: list[dict[str, Any]],
    fitted: Mapping[str, Any],
    raw: Any,
    feedback: ProtocolFeedback,
    prefix: int,
) -> list[dict[str, Any]]:
    """@brief 按配对规则将纠错反馈接回对话历史：思考开启且结构完整时用 tool 配对，否则替换末尾 user 纠错。"""
    text = report_content(feedback.report)
    calls = _raw_tool_calls(raw)
    if thinking_enabled(fitted) and not truncated(raw) and _pairable_calls(calls):
        updated = [*history, _response_assistant_message(raw)]
        updated.extend({"role": "tool", "tool_call_id": call.id, "content": text} for call in calls)
        return updated
    return with_feedback(history, text, prefix)
