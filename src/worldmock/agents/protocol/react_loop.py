"""@brief 一次决策的回合循环：至多 max_react_turns 个模型回合，每个回合恰好一次模型调用。

第 t 回合的调用身份是「决策身份:model:t(四位):attempt:1」；请求证据在发送之前写入，响应证据在解析与校验之后写入，
发送失败写失败证据后原样上抛。回复登记了世界请求即结束循环；协议纠错与截断重试各占一个回合；回合用完而没有世界请求
是运行故障。交给引擎或模型客户端的请求与消息之后不再原地改动：每个回合用新的消息列表，纠错与接受都构造新的历史。
请求内容（工具面、请求字典、解析与校验、工具执行）由 LifeAgent 决定，本模块只管回合机制
（docs/design/06-agent.md「宿主模型协议」）。

来源：本模块部分代码改写自 AgentSociety2 2.8.7（上游提交 670c94ff），按 Apache-2.0 使用，许可全文见 LICENSES/AgentSociety2-Apache-2.0.txt。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from worldmock.agents.prompt.prompt_capacity import FittedRequest, ProgressBudget, compare_usage, fit_request
from worldmock.agents.protocol.life_continuation import end_continuation, send_check
from worldmock.agents.protocol.life_feedback import ProtocolFeedback, report_content, stage_error, stage_feedback
from worldmock.agents.protocol.life_truncation import retry_due, thinking_enabled, with_feedback
from worldmock.configuration.model.model_client import collect_transport_attempts
from worldmock.state.model.exchange import ModelCallEvidence, assistant_message, wire_tool_call

if TYPE_CHECKING:
    from worldmock.agents.life_agent import LifeAgent
    from worldmock.state.decisions.round import DecisionContext

#: 回合用完而没有登记世界请求时的故障文字。
TURNS_EXHAUSTED = "ReAct decision budget exhausted without a world request"


@dataclass(frozen=True, slots=True)
class ReactDecision:
    """@brief 一个已解析的原生工具调用：工具名、参数对象与供应商给出的调用身份。"""

    action: str
    args: dict[str, Any]
    tool_call_id: str


@dataclass(frozen=True, slots=True)
class ReactToolResult:
    """@brief 一个只读查询或世界请求的结构化结果；检索带 error_code 时 ok 为假。"""

    ok: bool
    data: dict[str, Any]


def model_call_id(decision_id: str, turn: int) -> str:
    """@brief 第 turn 回合（从 1 起）的模型调用身份；attempt 恒为 1。"""
    return f"{decision_id}:model:{turn:04d}:attempt:1"


def serialized_response(response: Any) -> dict[str, Any]:
    """@brief 把模型响应转成可落库的字典：有 model_dump 取它的 JSON 形式，映射取浅拷贝，其余拒绝。"""
    if hasattr(response, "model_dump"):
        payload = response.model_dump(mode="json")
    elif isinstance(response, Mapping):
        payload = dict(response)
    else:
        logging.error("Model response is not serializable")
        raise TypeError("Model response is not serializable")
    if not isinstance(payload, dict):
        logging.error("Model response payload must be an object")
        raise TypeError("Model response payload must be an object")
    return payload


def response_tool_calls(response: Any) -> list[dict[str, Any]] | None:
    """@brief 可安全配对到下一请求的完整原生工具调用；调用表为空或任一调用结构不完整、身份重复时为 None。"""
    choices = getattr(response, "choices", None)
    message = getattr(choices[0], "message", None) if choices else None
    raw_calls = getattr(message, "tool_calls", None) or []
    if not isinstance(raw_calls, (list, tuple)) or not raw_calls:
        return None
    call_ids: list[str] = []
    for item in raw_calls:
        function = getattr(item, "function", None)
        call_id = getattr(item, "id", None)
        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)
        if getattr(item, "type", None) != "function":
            return None
        if not isinstance(call_id, str) or not call_id.strip():
            return None
        if not isinstance(name, str) or not name.strip() or not isinstance(arguments, str):
            return None
        call_ids.append(call_id.strip())
    if len(set(call_ids)) != len(call_ids):
        return None
    return [_wire_call(item) for item in raw_calls]


def accepted_assistant(response: Any) -> dict[str, Any]:
    """@brief 由模型响应构造回传给后续请求的 assistant 消息（原样带思考与全部工具调用）。"""
    choices = getattr(response, "choices", None)
    message = getattr(choices[0], "message", None) if choices else None
    calls = getattr(message, "tool_calls", None) or []
    return assistant_message(getattr(message, "content", None), [_wire_call(item) for item in calls],
                             getattr(message, "reasoning_content", None))


def _wire_call(tool_call: Any) -> dict[str, Any]:
    """@brief 从响应里的一个原生工具调用对象取出身份、名称与原始参数文本，按发送形状构造。"""
    function = getattr(tool_call, "function", None)
    return wire_tool_call(getattr(tool_call, "id", ""), getattr(function, "name", ""),
                          getattr(function, "arguments", ""))


class ReactLoop:
    """@brief 一次决策的回合机制：回合计数、调用身份、证据次序、协议纠错与续接前缀。"""

    def __init__(self, person: LifeAgent, context: DecisionContext, budget: ProgressBudget,
                 history: list[dict[str, Any]]) -> None:
        """@brief 持有本次决策的人物、上下文、容量预算与起始消息历史；还没有发出请求。"""
        self._person = person
        self._context = context
        self._budget = budget
        self._history = history
        self._last_request: dict[str, Any] = {}

    async def run(self) -> list[ReactDecision]:
        """@brief 逐回合调用模型，返回首个登记了世界请求的回合登记的请求；回合用完即运行故障。"""
        limit = self._person.inputs.model_identity["max_react_turns"]
        for turn in range(1, limit + 1):
            registered = await self._turn(turn, limit - turn + 1)
            if registered:
                return registered
        logging.error("LifeAgent %s used all %d model turns without a world request",
                      self._person.inputs.agent_id, limit)
        raise RuntimeError(TURNS_EXHAUSTED)

    async def _turn(self, turn: int, remaining: int) -> list[ReactDecision]:
        """@brief 一个模型回合：构造并发出请求、解析校验、留证，再纠错或接受并执行调用；返回本回合登记的世界请求。"""
        person = self._person
        retry = retry_due(self._history, self._last_request)
        tools = person.turn_tools(remaining, retry)
        messages, fitted = self._request(list(self._history), tools, retry)
        call_id = model_call_id(self._context.decision_id, turn)
        response, attempts = await self._send(call_id, fitted.request)
        decisions, feedback = person.parse_reply(response)
        self._record_response(call_id, fitted, response, feedback, attempts)
        if feedback is not None:
            logging.error("Agent %s ReAct protocol validation failed: %s", person.inputs.agent_id, feedback.text)
            self._history = self._corrected(messages, response, feedback)
            return []
        self._history = [*messages, accepted_assistant(response)]
        registered, tool_messages = person.execute_decisions(decisions)
        self._history.extend(tool_messages)
        return registered

    def _request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                 retry: bool) -> tuple[list[dict[str, Any]], FittedRequest]:
        """@brief 发送前自检与容量预检：返回本回合的消息列表（续接可能已换成新快照）与预检结果。"""
        messages, self._budget = send_check(messages, self._budget)
        request = self._person.turn_request(messages, tools, retry)
        fitted = fit_request(request, self._person.inputs.model_identity["model_metadata"], self._budget)
        if len(fitted.request["messages"]) < len(messages):
            messages, self._budget = end_continuation(messages, self._budget)
        self._last_request = fitted.request
        return messages, fitted

    async def _send(self, call_id: str, request: dict[str, Any]) -> tuple[Any, tuple[dict[str, Any], ...]]:
        """@brief 先写请求证据再发送；发送失败写模型服务阶段的失败证据后原样上抛，取消不写失败证据。"""
        self._record(ModelCallEvidence(self._context, call_id, "request", 1, request))
        with collect_transport_attempts() as attempts:
            try:
                response = await self._person.model_client.call(request)
            except Exception:
                feedback = stage_feedback("MODEL_SERVICE_FAILED", ("tool_calls",))
                self._record(ModelCallEvidence(self._context, call_id, "failure", 1, request,
                                               response={"protocol_feedback": feedback.report.payload()},
                                               error=feedback.text, transport_attempts=tuple(attempts)))
                raise
        return response, tuple(attempts)

    def _record_response(self, call_id: str, fitted: FittedRequest, response: Any,
                         feedback: ProtocolFeedback | None, attempts: tuple[dict[str, Any], ...]) -> None:
        """@brief 写响应证据（有纠错时末尾追加 protocol_feedback），再用发送前的计数校核实际用量。"""
        payload = serialized_response(response)
        error = None
        if feedback is not None:
            payload["protocol_feedback"] = feedback.report.payload()
            error = feedback.text
        self._record(ModelCallEvidence(self._context, call_id, "response", 1, fitted.request, response=payload,
                                       error=error, transport_attempts=attempts))
        compare_usage(fitted.prompt_tokens, payload)

    def _record(self, evidence: ModelCallEvidence) -> None:
        """@brief 经引擎写入一份模型证据；写入失败以证据写入阶段终止，之后不再发模型请求。"""
        try:
            self._person.engine.record_model_exchange(evidence)
        except Exception as error:
            logging.error("Model evidence persistence failed: %s", error)
            raise stage_error("EVIDENCE_WRITE_FAILED", ("tool_calls",)) from error

    def _corrected(self, messages: list[dict[str, Any]], response: Any,
                   feedback: ProtocolFeedback) -> list[dict[str, Any]]:
        """@brief 带纠错的新历史：思考请求里调用完整时配对 assistant 与逐调用的 tool 纠错；其余情况接一条 user 纠错，
        去掉本决策已有的相同纠错，续接链前缀不动。"""
        content = report_content(feedback.report)
        calls = response_tool_calls(response)
        if thinking_enabled(self._last_request) and calls is not None:
            results = [{"role": "tool", "tool_call_id": item["id"], "content": content} for item in calls]
            return [*messages, accepted_assistant(response), *results]
        return with_feedback(messages, content, self._budget.chain_length)
