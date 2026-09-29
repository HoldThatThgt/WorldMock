"""@brief AgentSociety2 原生 LifeAgent，使用闭集生活工具直接调用 LifeEnv。

一次决策只有一个行动阶段（docs/dev/SPEC-persona-prompt.md 第二章「单一行动阶段」）：请求由原生 tools、系统层
system 与六层 user 组成；可以先做记忆查询，世界请求登记即结束本次决策，登记后不追加任何 tool 消息。原生工具
整次运行不变，不按轮绑定名称枚举；名称只按观察私有绑定做本地校验，可用性由环境受理判定。同一秒的后续决策
在准备阶段之后注入环境交来的续接链（第二章「同秒续接」，custom/agents/life_continuation.py）。本地校验失败写成
宿主与环境共用的错误结构回给模型，多项请求逐项报全部名称类错误（docs/dev/SPEC-dayrun-fidelity.md 第四章，
custom/agents/life_feedback.py）。思考用尽输出上限的截断回合丢弃并纠错，随后以关闭思考、强制工具调用的请求重试一次
（7.2，custom/agents/life_truncation.py）。
"""

from __future__ import annotations

import json
import hashlib
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agentsociety2.agent import PersonAgent
from agentsociety2.agent.base import (
    ModelCallEvidence,
    ReactDecision,
    ReactLoopRequest,
    ReactToolResult,
)
from agentsociety2.agent.memory_runtime import MemoryStepOutcome
from agentsociety2.society import DirectToolCall
from custom.agents import life_menu
from custom.agents.life_context import persona_card_text
from custom.agents.life_continuation import continuation_messages, end_continuation, send_check
from custom.agents.life_feedback import (HostFeedback, HostIssue, argument_feedback, feedback_content, host_feedback,
                                         merged_feedback)
from custom.agents.life_memory import memory_update_evidence
from custom.agents.life_persona import known_persons, persona_layer, readonly_layers, write_prompt_snapshot
from custom.agents.life_prompt import (TOOL_DESCRIPTIONS, life_user_layers, readonly_messages, system_message,
                                       user_message)
from custom.agents.life_truncation import (RETRY_THINKING, RETRY_TOOL_CHOICE, retry_due, thinking_enabled, truncated,
                                           truncation_feedback, with_feedback)
from worldmock.life.continuation import CONTINUATION_KEY
from worldmock.life.control_items import control_items
from worldmock.life.error_contract import HOST_NAME_CODES, Candidate, ErrorItem, host_report, numbered
from worldmock.life.interaction_menu import VERB_WAY_SEPARATOR
from worldmock.life.models import (ActionReceipt, ConcludeActivityRequest, REQUEST_MODELS, deepseek_strict_schema,
                                   purpose_only_schema)
from worldmock.life.name_resolution import StartOutcome, name_book, near_names, resolve_name, resolve_start
from worldmock.life.prompt_capacity import ProgressBudget, compare_usage, fit_request
from worldmock.life.routing import RECIPIENT_NOT_FOUND, RouteIssue, combination_issues, routing_names
from worldmock.life.schedule import MAX_PLAN_GOALS


#: 行动阶段思考开关与工具选择（思考模式拒收 required，真实探针见规格）。
ACTION_THINKING = {"thinking": {"type": "enabled"}}
ACTION_TOOL_CHOICE = "auto"
#: 行动阶段明确预留的最大输出，用于发送前完整上下文计数。
ACTION_MAX_TOKENS = 8000

#: 一次请求至多选择两个交互对象（targets[]、actions[]、ways[] 等长且 ≤2；DeepSeek strict 禁 maxItems，
#: 故上限由本地校验兜住）。
_START_ACTIVITY_MAX_TARGETS = 2
#: 结束活动时写入的记忆来源（系统按写入点固定，与 memory_updated.data.trigger 同一组取值）。
_CONCLUSION_MEMORY_SOURCE = "activity_conclusion"


class _ToolRequest(BaseModel):
    """@brief 所有 LifeAgent 专用工具请求都拒绝未知字段；参数 Schema 不带开发说明，用途只在工具 description。"""

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_extra=purpose_only_schema())


class MemoryRecentRequest(_ToolRequest):
    """@brief 读取最近人物记忆。"""

    limit: int = Field(ge=1, le=50)


class MemorySearchRequest(_ToolRequest):
    """@brief 按关键词检索人物记忆。"""

    query: str = Field(pattern=r"\S")
    limit: int = Field(ge=1, le=50)

    @model_validator(mode="after")
    def validate_query_length(self) -> "MemorySearchRequest":
        """@brief 保留服务端不支持表达的查询长度上限。"""
        if len(self.query) > 200:
            raise ValueError("memory search query exceeds 200 characters")
        return self


class MemoryTimeRangeRequest(_ToolRequest):
    """@brief 按 ISO 时间闭区间读取记忆。"""

    start_time: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
    end_time: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
    limit: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def validate_interval(self) -> "MemoryTimeRangeRequest":
        """@brief 要求时区明确且时间闭区间顺序有效。"""
        start = _parse_memory_time(self.start_time)
        end = _parse_memory_time(self.end_time)
        if start > end:
            raise ValueError("memory time range is reversed")
        return self


class ReadonlyFinishRequest(_ToolRequest):
    """@brief 结束明确只读人物查询。"""

    answer: str = Field(pattern=r"\S")

    @model_validator(mode="after")
    def validate_answer_length(self) -> "ReadonlyFinishRequest":
        """@brief 保留服务端不支持表达的只读回答长度上限。"""
        if len(self.answer) > 4000:
            raise ValueError("readonly answer exceeds 4000 characters")
        return self


def _parse_memory_time(value: str) -> datetime:
    """@brief 解析带时区的 ISO 记忆边界时刻。"""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("memory time range requires ISO datetimes") from error
    if parsed.utcoffset() is None:
        raise ValueError("memory time range requires timezone-aware datetimes")
    return parsed


_MEMORY_TOOL_MODELS: dict[str, type[BaseModel]] = {
    "memory_recent": MemoryRecentRequest,
    "memory_search": MemorySearchRequest,
    "memory_time_range": MemoryTimeRangeRequest,
}

#: 生活决策的工具面：八个人物工具（REQUEST_MODELS 顺序）与记忆查询；没有 finish。当日计划由零点后台任务生成，
#: 不是人物工具（docs/dev/SPEC-time-and-energy.md 第四章）。
_TOOL_MODELS: dict[str, type[BaseModel]] = {
    **REQUEST_MODELS,
    **_MEMORY_TOOL_MODELS,
}

_MEMORY_RUNTIME_ACTIONS = {
    "memory_recent": "memory_recent",
    "memory_search": "memory_search",
    "memory_time_range": "memory_range",
}

#: 记忆查询返回给模型的字段（不含条目身份，docs/dev/SPEC-time-and-energy.md 第七章「发给模型的内容不含内部身份」）。
_MEMORY_RESULT_FIELDS = ("type", "text", "time", "epistemic_status")

#: 以活动名为参数的控制工具。
_ACTIVITY_CONTROL_TOOLS = ("report_status", "pause_activity", "conclude_activity")


def life_action_tool_names() -> frozenset[str]:
    """@brief 返回 LifeAgent 实际开放的全部生活动作工具名（env 侧核对用）。"""
    return frozenset(_TOOL_MODELS) - frozenset(_MEMORY_TOOL_MODELS)


class LifeAgent(PersonAgent):
    """@brief 保留 PersonAgent 记忆与 ReAct 循环的生活人物实现。"""

    @classmethod
    def description(cls) -> str:
        """@brief 返回注册中心使用的准确职责。"""
        return "LifeAgent: autonomous person using typed life tools through DirectRouter."

    async def _record_model_evidence(self, evidence: ModelCallEvidence) -> None:
        """@brief 保存模型调用证据并对照实际输入 token 量；协议错误串以普通字符串落库（HostFeedback 的结构只回给模型）。"""
        if isinstance(evidence.error, HostFeedback):
            evidence = replace(evidence, error=str(evidence.error))
        await super()._record_model_evidence(evidence)
        if evidence.phase == "response":
            compare_usage(evidence.request, evidence.response)

    @staticmethod
    def _protocol_feedback_content(error: str) -> str:
        """@brief 协议纠错正文（覆写 AS2 同名静态方法，锁定提交不改）：共用错误结构 {error_code, reason, fix, items,
        unmet} 加固定 instruction，不带英文校验转储（docs/dev/SPEC-dayrun-fidelity.md 第四章）。"""
        return feedback_content(error)

    def _parse_react_responses(self, response: Any, *, readonly: bool = False) -> tuple[list[ReactDecision], str]:
        """@brief 解析原生工具调用（覆写 AS2 同名方法，锁定提交不改）：思考用尽输出上限的截断回合（finish_reason=length、
        正文为空、没有工具调用）不按「没有工具调用」处理，改为 OUTPUT_TRUNCATED 纠错（docs/dev/SPEC-dayrun-fidelity.md
        7.2）；其余照 AS2 解析。"""
        if truncated(response):
            return [], truncation_feedback()
        return super()._parse_react_responses(response, readonly=readonly)

    def _append_react_protocol_feedback(self, messages: list[dict[str, Any]], response: Any, error: str) -> None:
        """@brief 把协议纠错加入下一回合的消息历史（覆写 AS2 同名方法，锁定提交不改，docs/dev/SPEC-dayrun-fidelity.md 7.2）。

        没有工具调用的回复（含截断回合）与截断重试（关闭思考的请求）的回合都不留在历史中：纠错作为一条 user 消息，本次
        决策中已有的相同纠错替换、不叠加，续接链前缀不动；这样本次决策此后带思考的请求里没有缺思考的 assistant。带工具
        调用的其余回复照 AS2 配对 assistant（原样带思考）与 tool 消息。
        """
        if thinking_enabled(self._last_model_request) and self._feedback_tool_calls(response) is not None:
            super()._append_react_protocol_feedback(messages, response, error)
            return
        self._react_messages = with_feedback(messages, self._protocol_feedback_content(error),
                                             self._life_budget.chain_length)

    @classmethod
    def init_description(cls) -> str:
        """@brief 返回场景人物配置说明。"""
        return (
            "LifeAgent requires a stable id and profile. Memory must be enabled; "
            "todo and external skills are disabled for formal life decisions."
        )

    async def _load_world_description(self) -> str:
        """@brief 返回固定职责描述，不调用 CodeGen 世界摘要。"""
        return "A typed life simulation whose visible facts arrive through LifeEnv.observe."

    async def _observe_environment(self) -> dict[str, Any]:
        """@brief 使用 runner 绑定上下文直接读取 LifeEnv。"""
        if self._env is None:
            raise RuntimeError("Environment is not initialized")
        return await self._env.observe(self.require_decision_context())

    def build_react_tool_schemas(self, *, readonly: bool) -> list[dict[str, Any]]:
        """@brief 整次运行不变的原生工具；只有本次决策只剩最后一个回合或截断重试时去掉记忆查询工具。

        生活决策为八个人物工具与记忆查询（没有 finish），只读问答为记忆查询与 finish；参数不带逐轮名称枚举。
        """
        models = self._request_tool_models(readonly)
        self._life_request_models = models
        return [_tool_schema(name, model) for name, model in models.items()]

    def _model_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """@brief 注入行动阶段生成参数，并在发送前按完整上下文计量与浓缩进度层。

        DeepSeek thinking 模式拒绝 tool_choice=required（真实探针 400："Thinking mode does not support this
        tool_choice"），因此与思考开启同时把 tool_choice 放宽为 auto；模型不产出工具调用时由 ReAct 协议纠错回合
        兜底（"native tool_calls are required"）。截断之后的那一次重试按状态判定（上一回合的思考请求被截断，行动与
        只读问答相同）：关闭思考并 tool_choice=required，max_tokens 不变，其余请求不变（docs/dev/SPEC-dayrun-fidelity.md
        7.2）。system 不附加任何预算文字。带续接链的请求先做发送前协议自检；预算第 0 步结束续接后，本决策此后的回合
        都从新快照续写。
        """
        request = super()._model_request(messages, tools)
        retry = retry_due(messages, self._last_model_request)
        request["tool_choice"] = RETRY_TOOL_CHOICE if retry else ACTION_TOOL_CHOICE
        request["max_tokens"] = ACTION_MAX_TOKENS
        request["extra_body"] = dict(RETRY_THINKING if retry else ACTION_THINKING)
        self._life_budget = send_check(messages, self._life_budget)
        fitted = fit_request(request, self._life_budget)
        if len(fitted["messages"]) < len(messages):
            self._life_budget = end_continuation(messages, self._life_budget)
        return fitted

    def _remaining_turns(self) -> int:
        """@brief 本决策含当前请求在内的剩余模型回合；不足一个时明确终止，不发注定失败的请求。"""
        remaining = self._max_react_turns - self._model_call_sequence
        if remaining < 1:
            message = f"ReAct decision requires 1 model turn but only {remaining} remain"
            self._logger.error("LifeAgent %s %s", getattr(self, "_id", None), message)
            raise RuntimeError(message)
        return remaining

    def _request_tool_models(self, readonly: bool) -> dict[str, type[BaseModel]]:
        """@brief 当前请求的工具模型：为世界请求（只读为 finish）保留 1 个回合，剩余不少于 2 个时才开放记忆查询；截断重试
        同样不开放记忆查询，只能给出世界请求（只读为 finish，docs/dev/SPEC-dayrun-fidelity.md 7.2）。"""
        retry = retry_due(self._react_messages, self._last_model_request)
        memory = _MEMORY_TOOL_MODELS if self._remaining_turns() > 1 and not retry else {}
        if readonly:
            return {**memory, "finish": ReadonlyFinishRequest}
        return {**REQUEST_MODELS, **memory}

    def _persona_card_text(self) -> str:
        """@brief 从公开人物档案字段确定性渲染人物卡片文本（记忆巩固与反思使用）。"""
        return persona_card_text(self.get_profile(), self.name)

    def _new_memory_runtime(self, config):
        """@brief 复用 AS2 构造接点装配 WorldMock 的事实与主观记忆视图。"""
        from custom.agents.life_memory import WorldMockMemoryRuntime

        return WorldMockMemoryRuntime(self, config)

    def build_react_messages(self, request: ReactLoopRequest) -> list[dict[str, Any]]:
        """@brief 生活决策为系统层与六层 user；只读问答为只读系统层、人设层与记忆层以及外部问题。"""
        if request.readonly:
            persona, memory = self._life_budget.layers
            return readonly_messages(persona, memory, request.question)
        return [system_message(False), user_message(self._life_budget.layers)]

    def memory_sleep_boundary(self) -> bool:
        """@brief 报告本步骤是否为人物进入睡眠后的第一个边界。

        引擎在观察 data.wake_reasons 提供唤醒原因；就寝后第一个边界以 wake_from_sleep 标记，驱动就寝后的记忆巩固与反思。
        """
        return "wake_from_sleep" in getattr(self, "_life_wake_reasons", frozenset())

    def prepare_react_loop(self, request: ReactLoopRequest) -> None:
        """@brief 重置当前决策动作计数，并冻结本轮观察给出的层、私有绑定与工具面。"""
        super().prepare_react_loop(request)
        self._life_action_count = 0
        self._life_readonly = request.readonly
        if request.readonly:
            self._life_budget = ProgressBudget(readonly_layers(self._workspace_root, self.get_profile(), self.name), ())
            self._life_names = {}
            self._life_wake_reasons = frozenset()
        else:
            data = _environment_observation_data(request.observations)
            self._prepare_life_action_state(data)
        self._life_request_models = self._request_tool_models(request.readonly)

    def _prepare_life_action_state(self, data: dict[str, Any]) -> None:
        """@brief 从一次冻结观察组装六层、菜单与本地校验所用的私有绑定名称；有续接链时注入本次决策的起始消息。"""
        self._prepare_verified_experiences(data)
        self._life_interaction_menu = life_menu.parse_interaction_menu(data)
        self._life_wake_reasons = frozenset(_visible_wake_reasons(data))
        persona = persona_layer(self.get_profile(), self.name, _persona_views(data))
        layers = life_user_layers(data, persona)
        self._life_budget = ProgressBudget(tuple(layers), tuple(_progress_meta(data, layers[-1])))
        names = _visible_names(data, layers[3])
        self._life_names = {**names, "known_persons": known_persons(self.get_profile(), list(names["people"]))}
        if self._workspace_root is not None:
            write_prompt_snapshot(self._workspace_root, persona, layers[2])
        continued = continuation_messages(data.get(CONTINUATION_KEY), layers)
        if continued is not None:
            self._react_messages = continued
            self._life_budget = replace(self._life_budget, fresh_messages=(system_message(False), user_message(layers)),
                                        chain_length=len(continued))

    def _prepare_verified_experiences(self, data: dict[str, Any]) -> None:
        """@brief 冻结本轮本人事件事实窗口与观察身份（本步记忆更新的来源截止），模型和记忆不能写入该窗口。

        观察身份缺失是损坏的宿主观察，在任何模型调用之前失败，不等到记忆写入时才暴露。
        """
        from worldmock.footprints.experiences import validate_verified_experiences

        now = _observation_now(data)
        self._life_verified_experiences = validate_verified_experiences(
            data.get("verified_experiences"), data.get("agent_id"), now)
        observation_event_id = data.get("observation_event_id")
        if not isinstance(observation_event_id, str) or not observation_event_id:
            logging.error("LifeAgent observation event identity is invalid")
            raise RuntimeError("LifeAgent observation event identity is invalid")
        self._life_observation_event_id = observation_event_id

    async def _record_memory_update(self, outcome: MemoryStepOutcome) -> None:
        """@brief 登记本步已落盘的记忆（覆写 AS2 同名方法，锁定提交不改）：就寝反思的快照条目另带来源 reflection，
        环境据此给反思找归属日期（docs/dev/SPEC-persona-prompt.md 第三章「零点、反思与未了结」）。"""
        context = getattr(self, "_decision_context", None)
        store = self._memory_runtime.store
        if context is None or store is None:
            return
        if self._env is None:
            raise RuntimeError("Environment is not initialized")
        evidence = memory_update_evidence(context, store, outcome)
        if evidence is not None:
            await self._env.record_memory_update(evidence)

    def validate_react_decisions(
        self,
        decisions: list[ReactDecision],
        readonly: bool,
    ) -> str:
        """@brief 在执行任何工具前验证闭集参数、原子动作数量与名称（名称只按私有绑定核对，不查可用性）。

        不成立时返回 HostFeedback：证据文字为「<错误码>: <英文说明>」，AS2 把它原样交给 _protocol_feedback_content 写成
        共用错误结构（docs/dev/SPEC-dayrun-fidelity.md 第四章）；成立时为空串。
        """
        if readonly != self._life_readonly:
            raise RuntimeError("LifeAgent readonly decision state is inconsistent")
        error = _batch_error(decisions, self._life_action_count)
        for decision in decisions:
            error = error or self._decision_error(decision)
        return error

    def _decision_error(self, decision: ReactDecision) -> str:
        """@brief 一个工具调用：工具不在本次提供之列为 TOOL_CALL_REQUIRED，参数形状不符为 ARGUMENTS_INVALID（按字段写
        原因），再按冻结观察核对名称与菜单。"""
        model = self._life_request_models.get(decision.action)
        if model is None:
            return host_feedback(host_report("TOOL_CALL_REQUIRED", f"「{decision.action}」不是这次提供的工具",
                                             "只调用这次请求里提供的工具"), f"unknown LifeAgent tool: {decision.action}")
        try:
            validated = _validate_request(model, decision.args)
        except ValidationError as exc:
            return argument_feedback(decision.action, exc)
        try:
            return self._prepared_action_error(decision.action, validated)
        except Exception as exc:
            logging.error("LifeAgent could not check %s arguments: %s", decision.action, exc)
            return host_feedback(host_report("ARGUMENTS_INVALID"), f"invalid {decision.action} arguments: {exc}")

    def _prepared_action_error(self, action: str, request: BaseModel) -> str:
        """@brief 用同一冻结观察核对名称与菜单；非生活动作（记忆查询、finish）不核对名称，菜单只剪裁 start_activity 各项经
        统一名称解析选中的内部标准动作（未列出的指名购买计为购买）。"""
        if action not in REQUEST_MODELS:
            return ""
        error = _validate_prepared_action(action, request, self._life_names)
        if error or action != "start_activity":
            return error
        outcome = resolve_start(self._life_names["book"], request)
        denied = life_menu.menu_action_error(self._life_interaction_menu, action,
                                             [row["selected_action"]["canonical_action"] for row in outcome.rows])
        return _menu_feedback(denied, request, outcome, self._life_interaction_menu.allowed_actions) if denied else ""

    async def _run_react_turn(self, request: ReactLoopRequest, turn: int) -> str | None:
        """@brief 生活决策的一个模型回合：世界请求登记即结束本轮（取消 finish，AS2 锁定提交不改）。

        记忆查询是观察，不结束本轮；世界请求登记后，conclude_activity 逐项的记忆与看法展平交给本步 after_step 写入
        （其余请求写入空列表）。只读问答沿用 AS2 的 finish 回合。
        """
        if request.readonly:
            return await super()._run_react_turn(request, turn)
        decisions = await self._call_react_llm(request)
        for decision in decisions:
            result = await self._execute_react_tool(decision, readonly=False)
            self._append_react_tool_result(decision, result)
            request.observations.append({"turn": turn, "action": decision.action, "ok": result.ok,
                                         "observation": result.observation, "data": result.data})
            if decision.action in REQUEST_MODELS:
                self._last_finish_memories = _conclusion_memories(decision.action, decision.args)
                return f"{decision.action} registered"
        return None

    async def dispatch_react_tool(
        self,
        action: str,
        args: dict[str, Any],
        *,
        readonly: bool = False,
    ) -> ReactToolResult:
        """@brief 直接执行生活动作或严格只读记忆工具。"""
        if action in REQUEST_MODELS:
            if readonly:
                raise RuntimeError("Life actions are disabled in readonly mode")
            return await self._submit_life_action(action, args)
        if action in _MEMORY_TOOL_MODELS:
            return self._dispatch_strict_memory(action, args)
        raise RuntimeError(f"Unsupported LifeAgent tool dispatch: {action}")

    async def _submit_life_action(
        self,
        action: str,
        args: dict[str, Any],
    ) -> ReactToolResult:
        """@brief 注入宿主身份并向 LifeEnv 提交一个有类型请求。"""
        if self._life_action_count:
            raise RuntimeError("Life action was already submitted")
        request = _validate_request(REQUEST_MODELS[action], args)
        context = self.require_decision_context()
        error = self._prepared_action_error(action, request)
        if error:
            raise RuntimeError(error)
        action_id = f"{context.decision_id}:action:0001"
        self._life_action_count = 1
        if self._env is None:
            raise RuntimeError("Environment is not initialized")
        receipt = await self._env.submit_action(
            DirectToolCall(context, action_id, action, request)
        )
        validated = ActionReceipt.model_validate(receipt)
        data = validated.model_dump(mode="json")
        return ReactToolResult(True, json.dumps(data, ensure_ascii=False), data)

    def _append_react_tool_result(self, decision: ReactDecision, result: ReactToolResult) -> None:
        """@brief 世界请求登记后不追加任何 tool 消息；记忆查询的结果只序列化一次写进 tool 消息。

        宿主仍完整保留回执（审计与幂等）；失败的记忆查询以 {"error": 原因} 反馈给模型重试。
        """
        if decision.action in REQUEST_MODELS:
            return
        if not decision.tool_call_id:
            logging.error("Native tool call id is unavailable")
            raise RuntimeError("Native tool call id is unavailable")
        payload = result.data if result.ok else {"error": result.observation}
        self._react_messages.append({"role": "tool", "tool_call_id": decision.tool_call_id,
                                     "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))})

    def _dispatch_strict_memory(
        self,
        action: str,
        args: dict[str, Any],
    ) -> ReactToolResult:
        """@brief 验证记忆查询并把结果投影为 {type, text, time, epistemic_status}，不含条目身份。"""
        model = _TOOL_MODELS[action]
        request = _validate_request(model, args)
        payload = request.model_dump(mode="json", exclude_none=True)
        runtime_action = _MEMORY_RUNTIME_ACTIONS[action]
        ok, observation, data = self._memory_runtime.dispatch_tool(runtime_action, payload)
        # 工具失败必须以 ReactToolResult(False) 反馈给模型重试；raise 会穿透无 try/except 的
        # dispatch_react_tool 覆写杀 run。
        if not ok:
            return ReactToolResult(False, observation, {"error": observation})
        episodes = [{key: item.get(key) for key in _MEMORY_RESULT_FIELDS} for item in data.get("episodes", [])]
        projected = {"epistemic_status": "subjective", "episodes": episodes}
        return ReactToolResult(True, json.dumps(projected, ensure_ascii=False), projected)


def _validate_request(model: type[BaseModel], values: dict[str, Any]) -> BaseModel:
    """@brief 用严格 JSON 模式验证模型产生的工具参数。"""
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return model.model_validate_json(encoded, strict=True)


def _environment_observation_data(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """@brief 取得本轮唯一结构化环境观察，拒绝缺失或额外观察。"""
    if len(observations) != 1 or not isinstance(observations[0], dict):
        raise RuntimeError("LifeAgent requires one structured environment observation")
    data = observations[0].get("data")
    if not isinstance(data, dict):
        raise RuntimeError("LifeAgent environment observation data is unavailable")
    return data


def _observation_now(data: dict[str, Any]) -> int:
    """@brief 校验观察顶层的模拟时钟（UTC epoch 秒）。"""
    now = data.get("current_time")
    if isinstance(now, bool) or not isinstance(now, int) or now < 0:
        logging.error("LifeAgent observation current time is invalid")
        raise RuntimeError("LifeAgent observation current time is invalid")
    return now


def _visible_wake_reasons(data: dict[str, Any]) -> tuple[str, ...]:
    """@brief 冻结本轮观察携带的唤醒原因闭集（引擎 schedule 信号）。"""
    reasons = data.get("wake_reasons")
    if not isinstance(reasons, list) or any(
            not isinstance(item, str) or not item.strip() for item in reasons):
        raise RuntimeError("LifeAgent wake reasons are invalid")
    return tuple(reasons)


def _persona_views(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 读取环境下发的看法与偏好（观察私有证据 persona_views）。"""
    views = data.get("persona_views")
    if (not isinstance(views, dict) or not isinstance(views.get("relationships"), list)
            or not isinstance(views.get("preferences"), list)):
        logging.error("LifeAgent persona views are invalid")
        raise RuntimeError("LifeAgent persona views are invalid")
    return views


def _progress_meta(data: dict[str, Any], progress: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 读取与进度层记录逐条对齐的私有元数据（受保护标记与 settled 句）。"""
    meta = data.get("progress_meta")
    events = progress.get("events")
    if (not isinstance(meta, list) or not isinstance(events, list) or len(meta) != len(events)
            or any(not isinstance(item, dict) or not isinstance(item.get("protected"), bool)
                   or not isinstance(item.get("lines"), list) for item in meta)):
        logging.error("LifeAgent progress metadata is invalid")
        raise RuntimeError("LifeAgent progress metadata is invalid")
    return meta


def _conclusion_memories(action: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 把 conclude_activity 逐项的记忆与看法展平为本步写入的记忆（经 control_items 读出：每项先 memories、后
    views，views 的 about 转为 subject）；其余世界请求不写记忆。

    subject 只在 WorldMock 的请求与事件中，写入 AS2 前剥掉，于是与旧形状写下的同一条记忆逐字节相同；来源由系统按写入点
    固定为 activity_conclusion，认知状态固定为 subjective，模型不填、不选任何身份。
    """
    if action != "conclude_activity":
        return []
    request = _validate_request(ConcludeActivityRequest, args)
    return [{**{key: value for key, value in memory.items() if key != "subject"}, "source": _CONCLUSION_MEMORY_SOURCE,
             "epistemic_status": "subjective"}
            for item in control_items(action, request) for memory in item.memories]


def _visible_names(data: dict[str, Any], todo: dict[str, Any]) -> dict[str, Any]:
    """@brief 冻结本轮本地校验所用的名称：交互对象的（动词，方式，内部标准动作）、任务、人物、活动与回看名称，来源
    与去向的判定名称（人物、可见直接消息），以及 start_activity 统一名称解析的对象全集（book：列出的对象、目录绑定与
    冻结地点表，worldmock.life.name_resolution.name_book，环境受理读同一冻结观察）。

    只读观察私有绑定与待办层；缺失或形状不符的绑定是损坏的宿主观察。people 是同世界其他人物的姓名（不论是否同处，
    私有绑定 person_bindings），供 relationship 记忆的对象校验。回看名称是任务名、待办层写出的目标文字、活动名与
    记忆层 self_notes 所写的活动名（活动绑定的 note_labels）。
    """
    objects = _binding_objects(data.get("interaction_bindings"))
    tasks = _binding_names(data, "task_bindings", "task_name")
    goals = _binding_names(data, "goal_bindings", "task_name")
    activities = _binding_names(data, "activity_bindings", "name")
    labels = _note_labels(data["activity_bindings"])
    kinds = {row["object"]: row["kind"] for row in data["interaction_bindings"]}
    return {
        "objects": objects,
        "task_names": tasks,
        "recipient_names": tuple(name for name in objects if kinds[name] in {"person", "contact"}),
        "people": tuple(name.split("：", 1)[-1] for name in _binding_names(data, "person_bindings", "name")),
        "activity_names": activities,
        "recall_names": (*tasks, *goals, *activities, *labels),
        "progressed_intents": _progressed_intents(todo),
        "routing": routing_names(data),
        "book": name_book(data),
    }


def _binding_objects(rows: Any) -> dict[str, tuple[tuple[str, str, str], ...]]:
    """@brief 交互对象私有绑定：可读名称 -> 该对象列出的（动词，方式，内部标准动作）。"""
    if not isinstance(rows, list):
        raise RuntimeError("LifeAgent interaction bindings must be a list")
    objects: dict[str, tuple[tuple[str, str, str], ...]] = {}
    for row in rows:
        name = row.get("object") if isinstance(row, dict) else None
        actions = row.get("actions") if isinstance(row, dict) else None
        if (not isinstance(name, str) or not name.strip() or name in objects or not isinstance(actions, list)
                or not isinstance(row.get("kind"), str)):
            raise RuntimeError("LifeAgent interaction binding identity is invalid")
        objects[name] = tuple(_binding_action(item) for item in actions)
    return objects


def _binding_action(item: Any) -> tuple[str, str, str]:
    """@brief 私有绑定的一项动作：（动词，方式，内部标准动作）。"""
    fields = tuple(item.get(key) if isinstance(item, dict) else None
                   for key in ("verb", "label", "canonical_action"))
    if not all(isinstance(value, str) and value for value in fields):
        raise RuntimeError("LifeAgent interaction binding action is invalid")
    return fields


def _binding_names(data: dict[str, Any], key: str, field: str) -> tuple[str, ...]:
    """@brief 一类私有绑定的可读名称（任务、目标、活动或会话）。"""
    rows = data.get(key)
    if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get(field), str) for row in rows):
        raise RuntimeError(f"LifeAgent {key} are invalid")
    return tuple(row[field] for row in rows)


def _note_labels(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    """@brief 活动绑定的 note_labels：记忆层 self_notes 中去掉开始时刻后缀的活动名（只作回看名称）。"""
    if any(not isinstance(row.get("note_labels"), list)
           or any(not isinstance(label, str) for label in row["note_labels"]) for row in rows):
        raise RuntimeError("LifeAgent activity_bindings are invalid")
    return tuple(dict.fromkeys(label for row in rows for label in row["note_labels"]))


def _progressed_intents(todo: dict[str, Any]) -> tuple[str, ...]:
    """@brief 今日计划中已推进目标的意图（没有计划时为空，修订不受约束）。"""
    plan = todo.get("day_plan")
    if plan is None:
        return ()
    return tuple(goal["intent_kind"] for goal in plan["goals"] if goal["progressed"])


def _validate_prepared_action(action: str, request: BaseModel, names: dict[str, Any]) -> str:
    """@brief 用同一冻结观察的私有绑定核对工具参数中的名称（docs/dev/SPEC-persona-prompt.md 第一章「工具层」）：精确或
    无损归一后唯一即受理（docs/dev/SPEC-dayrun-fidelity.md 3.1，worldmock.life.name_resolution），找不到的逐项写成
    NAME_NOT_FOUND、归一后不止一个的写成 NAME_AMBIGUOUS（第四章）。"""
    if action == "start_activity":
        return _validate_start_activity(request, names)
    if action in _ACTIVITY_CONTROL_TOOLS:
        written = [item.activity_name for item in control_items(action, request)]
        unknown = _unknown_names(("activity_name", "活动"), written, names["activity_names"],
                                 f"{action} activity_name names an activity that is not visible")
        if unknown or action != "conclude_activity":
            return unknown
        return _subject_feedback(request, _memory_subject_error(request, names["known_persons"]))
    if action == "recall_task_summary":
        return _unknown_names(("task_name", "任务、目标或活动"), [request.task_name], names["recall_names"],
                              "task_name is not visible")
    if action == "submit_work":
        return _unknown_names(("task_name", "任务"), [request.task_name], names["task_names"],
                              "task_name is not visible")
    if action == "send_message":
        return _unknown_names(("recipient_name", "收件人"), [request.recipient_name], names["recipient_names"],
                              "recipient_name is not visible")
    return _validate_plan_goals(request.goals, names["progressed_intents"])


def _memory_subject_error(request: BaseModel, known: frozenset[str]) -> str:
    """@brief relationship 看法的对象（views 的 about，存储为 subject）须是已知人物：卡片关系名、场景联系人称谓或同世界
    人物姓名（规格第一章「人设层」）。"""
    unknown = [memory["subject"] for item in control_items("conclude_activity", request) for memory in item.views
               if memory["type"] == "relationship" and memory["subject"] not in known]
    return f"conclude_activity relationship subject is not a known person: {unknown[0]}" if unknown else ""


def _batch_error(decisions: list[ReactDecision], submitted: int) -> str:
    """@brief 一次回复的工具调用组合：至多一个世界请求，世界请求单独调用，且本次决策还没有登记过世界请求；不成立时为
    TOOL_CALL_REQUIRED。"""
    business = [item for item in decisions if item.action in REQUEST_MODELS]
    if len(business) > 1:
        return host_feedback(host_report("TOOL_CALL_REQUIRED", f"一次回复只能提交一个世界请求，这次有 {len(business)} 个",
                                         "只保留一个世界请求重新提交"),
                             "one model response cannot contain multiple life actions")
    if business and len(decisions) != 1:
        return host_feedback(host_report("TOOL_CALL_REQUIRED", "世界请求要单独调用，这次还同时调用了别的工具",
                                         "先单独查记忆，或者只提交世界请求"),
                             "a life action must be the only tool call in its model response")
    if business and submitted:
        return host_feedback(host_report("TOOL_CALL_REQUIRED", "这次决策已经提交过世界请求", "不再调用世界请求工具"),
                             "this decision already submitted its life action")
    return ""


def _unknown_names(field: tuple[str, str], written: list[str], visible, detail: str) -> str:
    """@brief 名称类参数逐项解析（精确或无损归一后唯一即受理，worldmock.life.name_resolution.resolve_name，环境受理用同一
    函数）：找不到的写成 NAME_NOT_FOUND（原因列出最接近的几个名称），归一后不止一个的写成 NAME_AMBIGUOUS（原因列出它们），
    逐项全部报出；都成立时为空串。

    :param field: （参数名，名称所指的东西），如 ("activity_name", "活动")。
    :param detail: 找不到时的英文说明（证据用）。
    """
    total, issues = len(written), []
    for index, name in enumerate(written):
        match = resolve_name(name, visible)
        if match.name is not None:
            continue
        lead = f"{numbered(index, total)}「{name}」"
        if match.matches:
            issues.append(HostIssue(ErrorItem(index, field[0], name, "NAME_AMBIGUOUS"),
                                    f"{lead}对应不止一个{field[1]}：{_quoted(match.matches)}",
                                    f"{field[0]} matches several visible names"))
            continue
        near = near_names(name, visible)
        issues.append(HostIssue(ErrorItem(index, field[0], name, "NAME_NOT_FOUND"),
                                f"{lead}不是本轮列出的{field[1]}" + (f"，最接近的是{_quoted(near)}" if near else ""),
                                detail))
    fix = "照工具层、待办层或记忆层中的写法填写名称" + ("，或者去掉这一项" if total > 1 else "")
    fixes = {"NAME_NOT_FOUND": fix, "NAME_AMBIGUOUS": "改写成原因里列出的某一个完整名称"}
    return merged_feedback(issues, total, fixes) if issues else ""


def _quoted(names) -> str:
    """@brief 几个名称的说法「A」、「B」。"""
    return "、".join(f"「{name}」" for name in names)


def _subject_feedback(request: BaseModel, error: str) -> str:
    """@brief 把 _memory_subject_error 的失败包成 SUBJECT_NOT_KNOWN：对象（英文说明末尾「: 」之后的名字）写在原因里，
    所在一项按请求中写着该名字的看法（views 的 about）定位到 items 的那一项，明细的参数名为 about；没有失败时为空串。"""
    if not error:
        return ""
    name = error.rsplit(": ", 1)[1] if ": " in error else ""
    located = _subject_location(request.model_dump(mode="json", by_alias=True), name) if name else None
    items = [ErrorItem(located[1], located[0], name, "SUBJECT_NOT_KNOWN")] if located else []
    reason = f"关系看法写的对象「{name}」不是认识的人" if name else None
    return host_feedback(host_report("SUBJECT_NOT_KNOWN", reason, items=items), error)


def _subject_location(arguments: dict[str, Any], name: str) -> tuple[str, int] | None:
    """@brief 写着该对象名的（参数名，项序号）：在各顶层数组的每一项里找 about（旧形状为 subject）等于该名字的对象，
    参数名取写着它的那个键。"""
    def key_of(value: Any) -> str | None:
        """@brief 值里 about 或 subject 等于该名字的那个键名；没有时为 None。"""
        if isinstance(value, dict):
            direct = next((key for key in ("about", "subject") if value.get(key) == name), None)
            return direct or next(filter(None, map(key_of, value.values())), None)
        return next(filter(None, map(key_of, value)), None) if isinstance(value, list) else None

    return next(((found, index) for rows in arguments.values() if isinstance(rows, list)
                 for index, row in enumerate(rows) if (found := key_of(row))), None)


def _validate_start_activity(request: BaseModel, names: dict[str, Any]) -> str:
    """@brief 对象、动词与方式逐项经统一名称解析（worldmock.life.name_resolution.resolve_start，与环境受理同一冻结观察、
    同一函数）；来源、去向与 answering 按归一后的请求判定组合（worldmock.life.routing，与环境受理同一判定）。

    本轮列出的对象、目录绑定（没有列出的生活活动、内容与游戏）与冻结地点表中的名称精确或无损归一后唯一即受理，存在
    但没有列出的由环境受理时判定资格与准入；obtain 以到店或远程下单（含同义写法）写出的其余名称放行，由环境按指名购买
    解析商品（docs/dev/SPEC-dayrun-fidelity.md 3.1 第 4 条）。名称类错误（找不到、有歧义、对象没有列出所写的动词与方式、
    目标已写明门店却写了另一家的 from）逐项报全部，候选可以照填，其余条件报第一条（3.1 第 5 条、第四章）。去向不是任何
    人物（RECIPIENT_NOT_FOUND）是拒绝码，宿主放行，由环境以业务拒绝回给模型（人物 Prompt 规格第五章「来源与去向」）。
    """
    count = len(request.targets)
    if not 1 <= count <= _START_ACTIVITY_MAX_TARGETS:
        report = (host_report("TOO_MANY_TARGETS", f"一次最多选两个目标，这次写了 {count} 个") if count
                  else host_report("ARGUMENTS_INVALID", "targets 至少要写一个目标", "targets、actions、ways 各写一项"))
        return host_feedback(report, "start_activity accepts one or two targets")
    if count != len(request.actions) or len(request.actions) != len(request.ways):
        return host_feedback(host_report(
            "ARGUMENTS_INVALID", f"targets、actions、ways 要逐项对应、一样长，这次分别有 {count}、"
            f"{len(request.actions)}、{len(request.ways)} 项", "每个目标各写一个动词和一个方式"),
            "start_activity targets, actions and ways must have equal lengths")
    outcome = resolve_start(names["book"], request)
    route = combination_issues(outcome.request, names["routing"])
    issues = [*(HostIssue(failure.item, failure.sentence, failure.detail) for failure in outcome.failures),
              *_source_issues(outcome.request, route)]
    if issues:
        return merged_feedback(issues, count, _START_NAME_FIXES)
    first = next((issue for issue in route if issue.code != RECIPIENT_NOT_FOUND), None)
    return _route_feedback(first, count) if first is not None else ""


#: start_activity 名称类纠错的改法：找不到与有歧义时都有可以照填的候选（docs/dev/SPEC-dayrun-fidelity.md 3.1 第 3 条）。
_START_NAME_FIXES = {"NAME_NOT_FOUND": "改用候选里的请求，或者照工具层、待办层中的写法重写这一项，或者去掉这一项",
                     "NAME_AMBIGUOUS": "改用候选里写完整名称的请求"}


def _source_issues(request, route: list[RouteIssue]) -> list[HostIssue]:
    """@brief 来源与去向中的名称类问题（目标已写明门店、from 却是另一家，SUPPLY_SOURCE_MISMATCH；写的正是这家店时已在
    名称解析中按冗余受理）逐项写成明细，候选为同一项改 from 为 null；其余问题（ROUTE_FIELDS_INVALID）不在此列，只报
    第一条。

    :param request: 归一后的 start_activity 请求（name_resolution.StartRequest）。
    """
    total, issues = len(request.targets), []
    for issue in route:
        if issue.host_code not in HOST_NAME_CODES:
            continue
        index = issue.index
        candidate = Candidate(request.targets[index], request.actions[index], request.ways[index], None,
                              request.to[index])
        issues.append(HostIssue(ErrorItem(index, issue.field, issue.written, issue.host_code, (candidate,)),
                                f"{numbered(index, total)}「{request.targets[index]}」已经写明了门店，from 要写 null",
                                f"{issue.detail} (targets[{index}])"))
    return issues


def _route_feedback(issue: RouteIssue, total: int) -> HostFeedback:
    """@brief 来源、去向或 answering 不相配（ROUTE_FIELDS_INVALID）：报第一条，逐项的问题带该项明细。"""
    items = [] if issue.index is None else [ErrorItem(issue.index, issue.field, issue.written, issue.host_code)]
    lead = "" if issue.index is None else numbered(issue.index, total)
    reason = f"{lead}：{issue.reason}" if lead else issue.reason
    return host_feedback(host_report(issue.host_code, reason, issue.fix, items), issue.detail)


def _menu_feedback(denied: str, request: BaseModel, outcome: StartOutcome, allowed: tuple[str, ...]) -> HostFeedback:
    """@brief 本地交互菜单不允许的项包成 WAY_NOT_OFFERED：逐项写出这里不能那样做的项（经名称解析选中的内部动作不在菜单里，
    未列出的指名购买计为购买）。"""
    total, issues = len(request.targets), []
    for index, row in enumerate(outcome.rows):
        action = row["selected_action"]
        if action["canonical_action"] not in allowed:
            doing = f"{action['verb']}{VERB_WAY_SEPARATOR}{action['label']}"
            issues.append(HostIssue(ErrorItem(index, "ways", request.ways[index], "WAY_NOT_OFFERED"),
                                    f"{numbered(index, total)}「{row['object']}」在这里不能「{doing}」",
                                    f"{denied} (targets[{index}])"))
    return merged_feedback(issues, total, {"WAY_NOT_OFFERED": "改选这里能做的事，或者先去能这样做的地方"})


def _tool_schema(name: str, model: type[BaseModel]) -> dict[str, Any]:
    """@brief 从唯一请求模型与用途说明形成 OpenAI 原生 function Schema。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": TOOL_DESCRIPTIONS[name],
            "parameters": deepseek_strict_schema(model),
            "strict": True,
        },
    }


def _validate_plan_goals(goals: Any, progressed_intents: tuple[str, ...]) -> str:
    """@brief 修订仅包含未推进目标，合并后保持容量和意图唯一性；不成立时为 ARGUMENTS_INVALID（重交已推进的目标逐项写出）。"""
    if len(goals) + len(progressed_intents) > MAX_PLAN_GOALS:
        logging.error("Day plan exceeds eight goals after preserving progressed goals")
        return host_feedback(host_report(
            "ARGUMENTS_INVALID", f"今日计划连同已推进的目标最多八条，这次合计 {len(goals) + len(progressed_intents)} 条",
            "删掉几条目标，使连同已推进的不超过八条"), "day plan requires at most eight goals including progressed goals")
    total = len(goals)
    resubmitted = [ErrorItem(index, "goals", goal.intent_kind, "ARGUMENTS_INVALID")
                   for index, goal in enumerate(goals) if goal.intent_kind in progressed_intents]
    if resubmitted:
        logging.error("Day plan revision resubmits a progressed intent")
        reason = "；".join(f"{numbered(item.index, total)}意图「{item.written}」的目标今天已经推进过，不能再提交"
                          for item in resubmitted)
        return host_feedback(host_report("ARGUMENTS_INVALID", reason, "修订时只写还没推进的目标", resubmitted),
                             "revise_day_plan cannot resubmit progressed goals")
    return ""


def affordance_registry_sha256() -> str:
    """@brief 返回仓库默认可供性注册表文件的 SHA-256（工具面审计指纹输入）。"""
    from worldmock.catalog.affordances import AFFORDANCES_FILE

    root = Path(__file__).resolve().parents[2]
    return hashlib.sha256(
        (root / "catalog" / AFFORDANCES_FILE).read_bytes()).hexdigest()


def life_tool_schema_sha256() -> str:
    """@brief 返回正式生活决策闭集工具 Schema + 注册表摘要的稳定 SHA-256。

    原生工具整次运行不变（不绑定逐轮枚举），指纹另绑定 affordances.toml 字节摘要，确保内容目录与工具面一起留证。
    """
    schemas = [_tool_schema(name, _TOOL_MODELS[name]) for name in _TOOL_MODELS]
    encoded = json.dumps(
        {"tool_schemas": schemas,
         "affordance_registry_sha256": affordance_registry_sha256()},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
