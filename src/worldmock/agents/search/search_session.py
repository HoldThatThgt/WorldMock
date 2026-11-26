"""@brief 异步搜索子会话运行器：以隔离上下文和 Fact Guard 白名单驱动通用 AgentLoop 完成搜索交卷。"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from worldmock.agents.protocol.agent_loop import (
    AgentLoop,
    LoopCallbacks,
    LoopConfig,
    LoopStepOutcome,
    LoopToolCall,
    build_tool_schema,
    dispatch_tool_safely,
    tool_message,
    validate_model_request,
    validate_single_tool_call,
)
from worldmock.agents.protocol.engine_handle import SearchCatalogHandle
from worldmock.agents.protocol.life_feedback import ProtocolFeedback, host_feedback
from worldmock.agents.search.search_prompt import (
    SEARCH_TOOL_DESCRIPTIONS,
    search_system_message,
    search_user_message,
)
from worldmock.configuration.model.model_registry import ResolvedModelBinding
from worldmock.recording_engine.events.models import EventDraft
from worldmock.state.decisions.barrier import SearchSessionOutcome, SearchTaskSpec
from worldmock.state.interaction.errors import ErrorItem, host_report
from worldmock.state.interaction.search_requests import SEARCH_SESSION_MODELS, SubmitSearchResultsRequest
from worldmock.state.model.exchange import SEARCH_SESSION_PURPOSE, ModelCallEvidence

_SUBMIT_TOOL = "submit_search_results"
_READ_TOOLS = frozenset({"search_goods", "inspect_goods_offers", "search_places"})
_BUDGET_EXHAUSTED = "Search session turn budget exhausted"


class _SearchEvidenceCollector:
    """@brief 在内存中收集搜索子会话产生的模型请求与响应事件草稿。"""

    def __init__(self, task: SearchTaskSpec) -> None:
        """@brief 绑定当前搜索子任务归属身份并初始化请求事件索引。"""
        self.task = task
        self.drafts: list[EventDraft] = []
        self._requests: dict[str, str] = {}

    def record(self, exchange: ModelCallEvidence) -> None:
        """@brief 把模型交换证据转为带 activity_id 与 search_session 用途标记的事件草稿。"""
        caused_by: list[str] = []
        event_id = uuid4().hex
        if exchange.phase == "request":
            self._requests[exchange.call_id] = event_id
        elif exchange.call_id in self._requests:
            caused_by = [self._requests.pop(exchange.call_id)]
        data: dict[str, Any] = {
            "phase": exchange.phase,
            "call_id": exchange.call_id,
            "attempt": exchange.attempt,
            "request": exchange.request if exchange.phase == "request" else None,
            "response": exchange.response,
            "error": exchange.error,
            "purpose": SEARCH_SESSION_PURPOSE,
        }
        if isinstance(exchange.response, dict) and "protocol_feedback" in exchange.response:
            data["protocol_feedback"] = exchange.response["protocol_feedback"]
        if exchange.phase != "request" and exchange.transport_attempts:
            data["transport_attempts"] = [dict(item) for item in exchange.transport_attempts]
        self.drafts.append(EventDraft(
            event_id=event_id,
            event_type="model_request" if exchange.phase == "request" else "model_response",
            occurred_at=0,
            agent_id=exchange.agent_id,
            wakeup_id=exchange.wakeup_id,
            action_id=self.task.action_id,
            activity_id=self.task.activity_id,
            caused_by=caused_by,
            visibility="system",
            source_kind="model",
            data=data,
        ))


class SearchSessionRunner:
    """@brief 运行隔离的 LLM Search Session，产出经 Fact Guard 验证的商品与地点编号清单。"""

    async def run(
        self,
        task: SearchTaskSpec,
        catalog: SearchCatalogHandle,
        resolved: ResolvedModelBinding,
    ) -> SearchSessionOutcome:
        """@brief 在禁用思考模式下执行搜索子会话直至合法交卷或预算耗尽抛 ProtocolError。"""
        collector = _SearchEvidenceCollector(task)
        config = LoopConfig(
            scope_id=task.action_id,
            wakeup_id=task.origin_wakeup_id,
            agent_id=task.agent_id,
            call_kind="search",
            binding=resolved,
            start_turn=1,
            max_turns=task.turn_budget,
            allow_thinking=False,
            purpose=SEARCH_SESSION_PURPOSE,
            activity_id=task.activity_id,
            exhausted_message=_BUDGET_EXHAUSTED,
        )
        callbacks = LoopCallbacks(
            turn_tools=_search_turn_tools,
            validate_call=validate_single_tool_call,
            validate_batch=_validate_search_batch,
            execute_calls=lambda calls, note: _execute_search_calls(calls, catalog),
            record_exchange=collector.record,
        )
        try:
            outcome = await AgentLoop(config, callbacks).run([search_system_message(), search_user_message(task)])
        except Exception as error:
            logging.error("Search session %s failed: %s", task.activity_id, error)
            setattr(error, "evidence_drafts", tuple(collector.drafts))
            raise
        submission: SubmitSearchResultsRequest = outcome.payload
        return SearchSessionOutcome(
            action_id=task.action_id,
            agent_id=task.agent_id,
            activity_id=task.activity_id,
            goods_ids=tuple(submission.goods_ids),
            place_ids=tuple(submission.place_ids),
            summary=submission.summary.strip(),
            evidence_drafts=tuple(collector.drafts),
        )


def _search_turn_tools(remaining: int, retry: bool) -> tuple[list[dict[str, Any]], dict[str, type[BaseModel]]]:
    """@brief 全程下发恒定四个搜索工具，不在末轮或重试时删减工具列表。"""
    del remaining, retry
    models = dict(SEARCH_SESSION_MODELS)
    schemas = [build_tool_schema(name, SEARCH_TOOL_DESCRIPTIONS[name], model) for name, model in models.items()]
    return schemas, models


def _validate_search_batch(
    calls: Sequence[LoopToolCall],
    models: Mapping[str, type[BaseModel]],
) -> ProtocolFeedback | None:
    """@brief 核验搜索批次形状：一组只读查询，或单独一条 submit_search_results 交卷调用。"""
    reads = all(call.tool in _READ_TOOLS for call in calls)
    submit = len(calls) == 1 and calls[0].tool == _SUBMIT_TOOL
    if calls and (reads or submit):
        return None
    reason = "Submit read search queries together or call submit_search_results alone."
    item = ErrorItem(("tool_calls",), [call.tool for call in calls], "TOOL_CALL_REQUIRED",
                     tuple(models), reason, "The call combination violates search batch rules.")
    return host_feedback(host_report("TOOL_CALL_REQUIRED", reason, items=(item,)), reason)


def _execute_search_calls(calls: Sequence[LoopToolCall], catalog: SearchCatalogHandle) -> LoopStepOutcome:
    """@brief 调度只读目录工具或校验 submit_search_results 白名单；非法 ID 返回循环内纠错消息。"""
    messages: list[dict[str, Any]] = []
    for index, call in enumerate(calls):
        request = validate_model_request(SEARCH_SESSION_MODELS[call.tool], call.arguments)
        if call.tool == _SUBMIT_TOOL:
            verdict = dispatch_tool_safely(call.tool, index, lambda req=request: catalog.validate_submission(req))
            if _submission_rejected(verdict):
                logging.error("Search submission rejected by Fact Guard: %s", verdict)
                messages.append(tool_message(call.tool_call_id, verdict))
                return LoopStepOutcome("continue", tuple(messages))
            return LoopStepOutcome("completed", payload=request)
        handler = getattr(catalog, call.tool)
        payload = dispatch_tool_safely(call.tool, index, lambda h=handler, req=request: h(req))
        messages.append(tool_message(call.tool_call_id, payload))
    return LoopStepOutcome("continue", tuple(messages))


def _submission_rejected(verdict: Mapping[str, Any]) -> bool:
    """@brief 判断 Fact Guard 交卷核验返回值是否携带错误或越界拒信。"""
    return ("error_code" in verdict or verdict.get("valid") is False or verdict.get("ok") is False
            or bool(verdict.get("invalid_goods_ids")) or bool(verdict.get("invalid_place_ids")))
