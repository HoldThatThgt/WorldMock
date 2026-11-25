"""@brief 数字人单次唤醒的提示适配与沙盒工具调度器：无状态构建七层输入并驱动通用 AgentLoop。"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from worldmock.agents.prompt.life_persona import known_persons, persona_layer
from worldmock.agents.prompt.life_prompt import (
    TOOL_DESCRIPTIONS,
    last_progress_layer,
    life_user_layers,
    system_message,
    user_message,
)
from worldmock.agents.prompt.prompt_capacity import ProgressBudget
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
from worldmock.agents.protocol.engine_handle import SandboxHandle
from worldmock.agents.protocol.life_feedback import HostIssue, ProtocolFeedback, host_feedback, merged_feedback
from worldmock.configuration.inputs.models import PersonaConfig
from worldmock.configuration.model.model_registry import ResolvedModelBinding
from worldmock.configuration.persona.persona_card import persona_card, persona_card_struct
from worldmock.configuration.persona.persona_text import render_card
from worldmock.recording_engine.projections.activities.experiences import validate_verified_experiences
from worldmock.state.decisions.barrier import AgentWakeupInput, SandboxOutcome, ToolDispatchResult
from worldmock.state.decisions.suspended import SuspendedLoopState
from worldmock.state.interaction.actions import SEARCH_TOOL, START_TOOLS
from worldmock.state.interaction.errors import ErrorItem, host_report
from worldmock.state.interaction.requests import QUERY_MODELS, REQUEST_MODELS

#: 每次唤醒（含挂起恢复）最多执行的记忆检索次数；第 4 次起当场以 MEMORY_SEARCH_LIMIT 回答。
MEMORY_SEARCHES_PER_DECISION = 3

_CORE_WORLD_MODELS: dict[str, type[BaseModel]] = {
    name: REQUEST_MODELS[name]
    for name in ("start_main_activity", "start_side_activity", "report_status",
                 "pause_activity", "conclude_activity", "revise_day_plan")
}
_EXTRA_MODELS: dict[str, type[BaseModel]] = {
    SEARCH_TOOL: REQUEST_MODELS[SEARCH_TOOL],
    "recall_task_summary": REQUEST_MODELS["recall_task_summary"],
    **QUERY_MODELS,
}
_FULL_MODELS: dict[str, type[BaseModel]] = {**_CORE_WORLD_MODELS, **_EXTRA_MODELS}
_QUERY_TOOLS = frozenset(_EXTRA_MODELS) - {SEARCH_TOOL}
_WORLD_TOOLS = {name: model for name, model in REQUEST_MODELS.items() if name != "recall_task_summary"}

_BATCH_REASON = ("Submit one world request, start_main_activity together with start_side_activity, "
                 "or a batch containing only read queries.")
_MEMORY_LIMIT_PAYLOAD = {
    "error_code": "MEMORY_SEARCH_LIMIT",
    "reason": "This decision has already searched memory three times.",
    "fix": "本次决策已查过三次记忆，请依据现有信息决定",
    "items": [],
    "unmet": [],
}


def life_action_tool_names() -> frozenset[str]:
    """@brief 返回生活唤醒暴露的全部非纯记忆检索请求工具名。"""
    return frozenset(_FULL_MODELS) - frozenset(QUERY_MODELS)


def life_query_tool_names() -> frozenset[str]:
    """@brief 返回生活唤醒暴露的全部只读查询工具名。"""
    return _QUERY_TOOLS


def life_tool_schema_sha256() -> str:
    """@brief 返回生活唤醒全量九个原生工具 Schema 的确定性 SHA-256 指纹。"""
    schemas = [build_tool_schema(name, TOOL_DESCRIPTIONS[name], model) for name, model in _FULL_MODELS.items()]
    encoded = json.dumps({"tool_schemas": schemas}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PersonInputs:
    """@brief 一位数字人的冻结人设提示输入：运行开始时由人设表一次构建，整次运行不变。"""

    agent_id: int
    name: str
    persona_card: str
    persona_card_struct: dict[str, Any]


def person_inputs(persona: PersonaConfig) -> PersonInputs:
    """@brief 从人物档案构造冻结的提示卡片与结构化人设信息（不含模型参数或凭据）。"""
    card = persona_card(persona)
    return PersonInputs(persona.agent_id, persona.name, render_card(card), persona_card_struct(card))


@dataclass(frozen=True, slots=True)
class _SuspendedMeta:
    """@brief 挂起退出的搜索动作与对应挂起工具调用定位元数据。"""

    pending_tool_call_id: str
    action_id: str
    activity_id: str


class _WakeupRunner:
    """@brief 单次唤醒生命周期内的私有工作状态，隔离并行唤醒与重入。"""

    def __init__(self, inputs: PersonInputs, wakeup: AgentWakeupInput, sandbox: SandboxHandle) -> None:
        """@brief 从 AgentWakeupInput 恢复或初始化沙盒续传计数与上下文列表。"""
        self.inputs = inputs
        self.wakeup = wakeup
        self.sandbox = sandbox
        self._now = _observation_now(wakeup.observation)
        self._verified = validate_verified_experiences(
            wakeup.observation.get("verified_experiences"), wakeup.agent_id, self._now)
        self._obs_event_id = _require_observation_id(wakeup.observation)
        _visible_wake_reasons(wakeup.observation)
        self._budget, self._messages, self._base_layers, self._action_seq = self._init_context()
        self._memory_searches = (wakeup.suspended_loop.memory_search_count
                                 if wakeup.mode == "resume_search" and wakeup.suspended_loop else 0)
        self._accepted_ids = (list(wakeup.suspended_loop.accepted_action_ids)
                              if wakeup.mode == "resume_search" and wakeup.suspended_loop else [])
        self._pending_memories = (list(wakeup.suspended_loop.pending_memories)
                                  if wakeup.mode == "resume_search" and wakeup.suspended_loop else [])
        self._rejections = (dict(wakeup.suspended_loop.local_rejection_counts)
                            if wakeup.mode == "resume_search" and wakeup.suspended_loop else {})

    async def execute(self, resolved: ResolvedModelBinding) -> SandboxOutcome:
        """@brief 运行通用 AgentLoop 直至完成开始动作或因 search_world 挂起并封口沙盒。"""
        suspended = self.wakeup.suspended_loop
        start_turn = suspended.turn_used + 1 if self.wakeup.mode == "resume_search" and suspended else 1
        base_limit = resolved.profile.max_react_turns
        max_turns = max(base_limit, start_turn) if self.wakeup.mode == "resume_search" else base_limit
        prefix = len(self._messages)
        config = LoopConfig(
            scope_id=self.wakeup.wakeup_id, wakeup_id=self.wakeup.wakeup_id, agent_id=self.inputs.agent_id,
            call_kind="model", binding=resolved, start_turn=start_turn, max_turns=max_turns,
            allow_thinking=True, protected_prefix=prefix,
        )
        callbacks = LoopCallbacks(
            turn_tools=_life_turn_tools, validate_call=self._validate_call,
            validate_batch=self._validate_batch, execute_calls=self._execute_calls,
            record_exchange=self.sandbox.record_model_exchange,
            can_finish_without_calls=self.sandbox.can_finish_idle,
        )
        outcome = await AgentLoop(config, callbacks, self._budget).run(self._messages)
        if outcome.status == "completed":
            return self.sandbox.finish_wakeup(None)
        meta: _SuspendedMeta = outcome.payload
        return self.sandbox.finish_wakeup(self._build_suspended(meta, outcome))

    def _init_context(self) -> tuple[ProgressBudget | None, list[dict[str, Any]], tuple[dict[str, Any], ...], int]:
        """@brief 构造常规唤醒或挂起恢复的初始消息序列、容量预算与起始动作序号。"""
        persona = persona_layer(self.inputs.persona_card_struct, self.inputs.name)
        if self.wakeup.mode == "regular":
            layers = tuple(life_user_layers(self.wakeup.observation, persona))
            meta = tuple(_progress_meta(self.wakeup.observation, last_progress_layer(layers)))
            msgs = [system_message(), user_message(layers)]
            return ProgressBudget(layers, meta), msgs, layers, self.wakeup.action_seq_start
        suspended = self.wakeup.suspended_loop
        assert suspended is not None
        layers = suspended.base_layers or tuple(life_user_layers(self.wakeup.observation, persona))
        meta = self.wakeup.observation.get("progress_meta")
        budget = ProgressBudget(layers, tuple(meta)) if _meta_matches(meta, layers) else None
        injected = tool_message(suspended.pending_tool_call_id, self.wakeup.search_result_payload or {})
        history = [*copy.deepcopy(list(suspended.messages)), injected]
        seq = max(self.wakeup.action_seq_start, len(suspended.accepted_action_ids) + 1,
                  _trailing_seq(suspended.action_id) + 1)
        return budget, history, layers, seq

    def _validate_call(self, call: LoopToolCall, models: Mapping[str, type[BaseModel]]) -> ProtocolFeedback | None:
        """@brief 校验单次调用参数及收尾关系看法主体，并统计本地拒信计数。"""
        feedback = validate_single_tool_call(call, models)
        if feedback is None and call.tool == "conclude_activity":
            feedback = self._subject_feedback(call.arguments)
        if feedback is not None:
            self._count_rejection(feedback.report.error_code)
        return feedback

    def _subject_feedback(self, arguments: Mapping[str, Any]) -> ProtocolFeedback | None:
        """@brief 校验 conclude_activity 的 relationship 看法主体是否出自卡片关系或观察已有看法。"""
        items = arguments.get("items") or ()
        if not any(view.get("type") == "relationship" for item in items for view in item.get("views") or ()):
            return None
        known = known_persons(self.inputs.persona_card_struct, _existing_relationship_names(self.wakeup.observation))
        allowed = tuple(sorted(known))
        issues: list[HostIssue] = []
        for item_idx, item in enumerate(items):
            for view_idx, view in enumerate(item.get("views") or ()):
                if view.get("type") == "relationship" and view.get("about") not in known:
                    subject = view["about"]
                    err = ErrorItem(
                        ("items", item_idx, "views", view_idx, "about"), subject, "SUBJECT_NOT_KNOWN",
                        allowed, "The relationship subject must name a known person.",
                        f"{subject!r} is not in persona relationships or prior relationship views.",
                    )
                    issues.append(HostIssue(err, f"Unknown relationship subject {subject!r}.",
                                            f"unknown relationship subject {subject!r}"))
        return merged_feedback(issues, len(issues)) if issues else None

    def _validate_batch(
        self,
        calls: Sequence[LoopToolCall],
        models: Mapping[str, type[BaseModel]],
    ) -> ProtocolFeedback | None:
        """@brief 核验批次形状：全为只读查询、单独一条世界动作（含 search_world）或两个开始工具成组。"""
        feedback = validate_life_batch(calls, models)
        if feedback is not None:
            self._count_rejection(feedback.report.error_code)
        return feedback

    def _execute_calls(self, calls: list[LoopToolCall], note: str | None) -> LoopStepOutcome:
        """@brief 按顺序执行只读查询或通过沙盒调度世界工具并原子提交本批结果。"""
        if all(call.tool in _QUERY_TOOLS for call in calls):
            return self._execute_queries(calls)
        ids = [f"{self.wakeup.wakeup_id}:action:{self._action_seq + i:04d}" for i in range(len(calls))]
        self._action_seq += len(calls)
        requests = [validate_model_request(REQUEST_MODELS[call.tool], call.arguments) for call in calls]
        results: list[tuple[LoopToolCall, str, ToolDispatchResult]] = []
        for index, (call, req, aid) in enumerate(zip(calls, requests, ids, strict=True)):
            res = dispatch_tool_safely(
                call.tool, index,
                lambda c=call, r=req, a=aid, n=note if index == 0 else None: (
                    self.sandbox.dispatch_world_tool(c.tool, r, a, n)
                ),
            )
            results.append((call, aid, res))
            if not res.accepted:
                return self._finish_world_results(
                    results + [(c2, id2, res) for c2, id2 in zip(calls[index + 1:], ids[index + 1:], strict=True)],
                )
        return self._finish_world_results(results)

    def _finish_world_results(self, results: list[tuple[LoopToolCall, str, ToolDispatchResult]]) -> LoopStepOutcome:
        """@brief 对世界调用批次作原子吸收；开始工具成组任一被拒时整组拒收，不计已受理动作。"""
        rejected = [res for _, _, res in results if not res.accepted]
        if rejected:
            chosen = results[-1][2] if not results[-1][2].accepted else rejected[0]
            self._absorb_result(results[-1][1], chosen)
            return LoopStepOutcome("continue", tuple(
                tool_message(call.tool_call_id, chosen.tool_message_payload) for call, _, _ in results))
        for _, action_id, res in results:
            self._absorb_result(action_id, res)
        if len(results) == 1 and results[0][2].suspended:
            call, action_id, res = results[0]
            activity_id = _extract_activity_id(res.tool_message_payload)
            return LoopStepOutcome("suspended", payload=_SuspendedMeta(call.tool_call_id, action_id, activity_id))
        if results and all(res.terminal_exit for _, _, res in results):
            return LoopStepOutcome("completed")
        return LoopStepOutcome("continue", tuple(
            tool_message(call.tool_call_id, res.tool_message_payload) for call, _, res in results))

    def _execute_queries(self, calls: list[LoopToolCall]) -> LoopStepOutcome:
        """@brief 执行只读查询批次，拦截第 4 次及之后的 search_memory 调用并重写查询出错位置。"""
        messages: list[dict[str, Any]] = []
        for index, call in enumerate(calls):
            if call.tool == "search_memory" and self._memory_searches >= MEMORY_SEARCHES_PER_DECISION:
                logging.error("Memory search limit reached for wakeup %s", self.wakeup.wakeup_id)
                self._count_rejection("MEMORY_SEARCH_LIMIT")
                messages.append(tool_message(call.tool_call_id, _MEMORY_LIMIT_PAYLOAD))
                continue
            request = validate_model_request(_FULL_MODELS[call.tool], call.arguments)
            if call.tool == "search_memory":
                self._memory_searches += 1
                payload = dispatch_tool_safely(call.tool, index, lambda req=request: self.sandbox.search_memory(req))
            else:
                payload = dispatch_tool_safely(
                    call.tool, index, lambda req=request: self.sandbox.recall_task_summary(req))
            messages.append(tool_message(call.tool_call_id, _bind_query_error_paths(payload, index)))
        return LoopStepOutcome("continue", tuple(messages))

    def _absorb_result(self, action_id: str, res: ToolDispatchResult) -> None:
        """@brief 累积已受理动作标识、待提交记忆或沙盒拒绝统计。"""
        if res.accepted:
            self._accepted_ids.append(action_id)
            self._pending_memories.extend(res.pending_memories)
        else:
            code = res.tool_message_payload["result"].get("error_code")
            if isinstance(code, str) and code:
                self._count_rejection(code)

    def _count_rejection(self, code: str) -> None:
        """@brief 累计本轮唤醒内发生的协议或规则拒绝次数。"""
        self._rejections[code] = self._rejections.get(code, 0) + 1

    def _build_suspended(self, meta: _SuspendedMeta, outcome: Any) -> SuspendedLoopState:
        """@brief 构造供异步搜索期间持久化至 AgentState 的无栈挂起快照。"""
        return SuspendedLoopState(
            wakeup_id=self.wakeup.wakeup_id, action_id=meta.action_id, activity_id=meta.activity_id,
            pending_tool_call_id=meta.pending_tool_call_id, messages=outcome.messages, turn_used=outcome.turns_used,
            memory_search_count=self._memory_searches, accepted_action_ids=tuple(self._accepted_ids),
            pending_memories=tuple(self._pending_memories), verified_experiences=tuple(self._verified),
            local_rejection_counts=dict(self._rejections), observation_event_id=self._obs_event_id,
            observation_parts=dict(self.wakeup.observation_parts), suspended_at=self._now,
            base_layers=self._base_layers,
        )


class LifeAgent:
    """@brief 数字人单次唤醒的适配器：暴露无状态工具面，并将唤醒上下文与沙盒句柄接入通用循环。"""

    def __init__(self, inputs: PersonInputs) -> None:
        """@brief 保存数字人的冻结人设输入；构造期不做任何 I/O 或外部服务访问。"""
        self.inputs = inputs

    def turn_tools(self, remaining: int, retry: bool) -> list[dict[str, Any]]:
        """@brief 返回本回合开放的原生工具 Schema：末回合或截断重试仅含核心世界请求，其余开放全量九个工具。"""
        return _life_turn_tools(remaining, retry)[0]

    async def decide_wakeup(
        self,
        wakeup: AgentWakeupInput,
        sandbox: SandboxHandle,
        resolved: ResolvedModelBinding,
    ) -> SandboxOutcome:
        """@brief 执行单次人物唤醒（常规进入或 search_world 挂起恢复），失败记日志后原样上抛。"""
        try:
            return await _WakeupRunner(self.inputs, wakeup, sandbox).execute(resolved)
        except Exception:
            logging.exception("LifeAgent wakeup failed for agent %s (%s)", self.inputs.agent_id, wakeup.wakeup_id)
            raise


def validate_life_batch(
    decisions: Sequence[LoopToolCall],
    models: Mapping[str, type[BaseModel]],
) -> ProtocolFeedback | None:
    """@brief 校验生活回复批次：全为只读查询、单个世界动作，或 start_main_activity 与 start_side_activity 成组。"""
    queries = bool(decisions) and all(item.tool in _QUERY_TOOLS for item in decisions)
    business = [item.tool for item in decisions if item.tool in _WORLD_TOOLS]
    single = len(decisions) == 1 and (len(business) == 1 or decisions[0].tool not in models)
    group = len(decisions) == 2 and sorted(business) == sorted(START_TOOLS)
    if queries or single or group:
        return None
    item = ErrorItem(("tool_calls",), [item.tool for item in decisions], "TOOL_CALL_REQUIRED",
                     tuple(models), _BATCH_REASON, "The call combination violates atomic batch rules.")
    return host_feedback(host_report("TOOL_CALL_REQUIRED", _BATCH_REASON, items=(item,)), _BATCH_REASON)


def _life_turn_tools(remaining: int, retry: bool) -> tuple[list[dict[str, Any]], dict[str, type[BaseModel]]]:
    """@brief 根据剩余回合数与截断重试标志计算本回合开放工具表及其 Schema 列表。"""
    models = dict(_CORE_WORLD_MODELS) if remaining <= 1 or retry else dict(_FULL_MODELS)
    schemas = [build_tool_schema(name, TOOL_DESCRIPTIONS[name], model) for name, model in models.items()]
    return schemas, models


def _observation_now(data: Mapping[str, Any]) -> int:
    """@brief 校验观察顶层的模拟时钟（UTC epoch 秒）。"""
    now = data.get("current_time")
    if isinstance(now, bool) or not isinstance(now, int) or now < 0:
        logging.error("LifeAgent observation current time is invalid")
        raise RuntimeError("LifeAgent observation current time is invalid")
    return now


def _require_observation_id(data: Mapping[str, Any]) -> str:
    """@brief 校验观察顶层的冻结事件身份。"""
    obs_id = data.get("observation_event_id")
    if not isinstance(obs_id, str) or not obs_id:
        logging.error("LifeAgent observation event identity is invalid")
        raise RuntimeError("LifeAgent observation event identity is invalid")
    return obs_id


def _visible_wake_reasons(data: Mapping[str, Any]) -> tuple[str, ...]:
    """@brief 校验本轮观察携带的唤醒原因列表。"""
    reasons = data.get("wake_reasons")
    if not isinstance(reasons, list) or any(not isinstance(item, str) or not item.strip() for item in reasons):
        logging.error("LifeAgent wake reasons are invalid")
        raise RuntimeError("LifeAgent wake reasons are invalid")
    return tuple(reasons)


def _progress_meta(data: Mapping[str, Any], progress: Mapping[str, Any]) -> list[dict[str, Any]]:
    """@brief 读取与进度层记录逐条对齐的私有元数据（受保护标记与 settled 句）。"""
    meta = data.get("progress_meta")
    events = progress.get("events")
    if not _meta_matches(meta, (progress,)) or not isinstance(events, list):
        logging.error("LifeAgent progress metadata is invalid")
        raise RuntimeError("LifeAgent progress metadata is invalid")
    return meta


def _meta_matches(meta: Any, layers: Sequence[Mapping[str, Any]]) -> bool:
    """@brief 判断 progress_meta 是否合法并与末层进度层事件数严格一致。"""
    if not layers or not isinstance(meta, list):
        return False
    events = layers[-1].get("events")
    if not isinstance(events, list) or len(meta) != len(events):
        return False
    return all(isinstance(item, dict) and isinstance(item.get("protected"), bool)
               and isinstance(item.get("lines"), list) for item in meta)


def _bind_query_error_paths(payload: dict[str, Any], index: int) -> dict[str, Any]:
    """@brief 只读查询返回错误时把明细路径改写为真实 tool_calls[index] 位置。"""
    if "error_code" not in payload or not isinstance(payload.get("items"), list):
        return payload
    return {
        **payload,
        "items": [{**item, "path": ["tool_calls", index, *item["path"][2:]]}
                  if isinstance(item.get("path"), list) else item for item in payload["items"]],
    }


def _extract_activity_id(payload: Mapping[str, Any]) -> str:
    """@brief 从挂起的 search_world 沙盒载荷中提取已建立搜索活动的 activity_id。"""
    direct = payload.get("activity_id")
    if isinstance(direct, str) and direct:
        return direct
    result = payload.get("result")
    if isinstance(result, Mapping) and isinstance(result.get("activity_id"), str) and result["activity_id"]:
        return result["activity_id"]
    events = payload.get("events")
    if isinstance(events, list):
        for event in events:
            if isinstance(event, Mapping) and isinstance(event.get("activity_id"), str) and event["activity_id"]:
                return event["activity_id"]
    logging.error("Suspended search_world result lacks activity_id")
    raise RuntimeError("Suspended search_world result lacks activity_id")


def _trailing_seq(action_id: str) -> int:
    """@brief 从已知 action_id 解析末尾动作序号以防挂起恢复后编号撞车。"""
    tail = action_id.rsplit(":", 1)[-1]
    return int(tail) if tail.isascii() and tail.isdigit() else 0


def _existing_relationship_names(data: Mapping[str, Any]) -> list[str]:
    """@brief 读取观察私有证据 persona_views 里已形成关系看法的人物名称列表。"""
    views = data.get("persona_views")
    if views is None:
        return []
    if not isinstance(views, Mapping) or not isinstance(views.get("relationships"), list):
        logging.error("Observation persona_views has invalid relationships shape")
        raise RuntimeError("Observation persona_views has invalid relationships shape")
    return [row["person"] for row in views["relationships"]
            if isinstance(row, Mapping) and isinstance(row.get("person"), str) and row["person"]]
