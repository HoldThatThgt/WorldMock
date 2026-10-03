"""@brief AgentSociety2 原生 LifeAgent，使用闭集生活工具直接调用 LifeEnv。

一次决策只有一个行动阶段（docs/dev/SPEC-persona-prompt.md 第二章「单一行动阶段」）：请求由原生 tools、系统层
system 与六层 user 组成；可以先做记忆查询，世界请求登记即结束本次决策，登记后不追加任何 tool 消息。原生工具
整次运行不变，不按轮绑定名称枚举；名称只按观察私有绑定做本地校验，可用性由环境受理判定。同一秒的后续决策
在准备阶段之后注入环境交来的续接链（第二章「同秒续接」，custom/agents/life_continuation.py）。本地校验失败写成
宿主与环境共用的错误结构回给模型，多项请求逐项报全部名称类错误（docs/dev/SPEC-dayrun-fidelity.md 第四章，
custom/agents/life_feedback.py）；三个控制工具请求级的个人事务变更与环境复核调用同一纯函数
（worldmock.life.errands.change_issues）、读同一份冻结观察的两类私有事务绑定，问题并入同一次纠错
（docs/dev/SPEC-errands-workday.md 2.3）。宿主不写入事务，事务变更也不是记忆写入点。思考用尽输出上限的截断回合丢弃并
纠错，随后以关闭思考、强制工具调用的请求重试一次（7.2，custom/agents/life_truncation.py）。
"""

from __future__ import annotations

import json
import hashlib
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

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
from custom.agents.life_feedback import (HostFeedback, HostIssue, argument_feedback, feedback_content,
                                          feedback_report, host_feedback,
                                         merged_feedback)
from custom.agents.life_memory import memory_update_evidence
from custom.agents.life_persona import known_persons, persona_layer, readonly_layers, write_prompt_snapshot
from custom.agents.life_prompt import (TOOL_DESCRIPTIONS, life_user_layers, readonly_messages, system_message,
                                       user_message)
from custom.agents.life_truncation import (RETRY_THINKING, RETRY_TOOL_CHOICE, retry_due, thinking_enabled, truncated,
                                           truncation_feedback, with_feedback)
from worldmock.life import errands
from worldmock.life.continuation import CONTINUATION_KEY
from worldmock.life.control_items import ControlItem, control_errands, control_items
from worldmock.life.error_contract import (HOST_NAME_CODES, Candidate, ErrorItem, ErrorReport, activity_candidate,
                                           bind_report, host_report)
from worldmock.life.interaction_menu import VERB_WAY_SEPARATOR
from worldmock.life.models import (ActionReceipt, ConcludeActivityRequest, REQUEST_MODELS, deepseek_strict_schema,
                                   purpose_only_schema)
from worldmock.life.name_resolution import StartOutcome, name_book, near_names, resolve_name, resolve_start
from worldmock.life.prompt_capacity import ProgressBudget, compare_usage, fit_request
from worldmock.life.routing import RECIPIENT_NOT_FOUND, RouteIssue, combination_issues, routing_names
from worldmock.life.schedule import MAX_PLAN_GOALS
from worldmock.life.world_query import QueryWorldRequest, query_world, validate_world_query


#: 行动阶段思考开关与工具选择（思考模式拒收 required，真实探针见规格）。
ACTION_THINKING = {"thinking": {"type": "enabled"}}
ACTION_TOOL_CHOICE = "auto"
#: 行动阶段明确预留的最大输出，用于发送前完整上下文计数。
ACTION_MAX_TOKENS = 8000

#: 结束活动时写入的记忆来源（系统按写入点固定，与 memory_updated.data.trigger 同一组取值）。
_CONCLUSION_MEMORY_SOURCE = "activity_conclusion"


class _ToolRequest(BaseModel):
    """@brief 所有 LifeAgent 专用工具请求都拒绝未知字段；参数 Schema 不带开发说明，用途只在工具 description。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, json_schema_extra=purpose_only_schema())


class MemoryRecentRequest(_ToolRequest):
    """@brief 读取最近人物记忆。"""

    limit: int = Field(ge=1, le=50)


class MemorySearchRequest(_ToolRequest):
    """@brief 按关键词检索人物记忆。"""

    query: str = Field(pattern=r"\S")
    limit: int = Field(ge=1, le=50)

    @field_validator("query")
    @classmethod
    def validate_query_length(cls, query: str) -> str:
        """@brief 查询长度错误精确指向查询字段。"""
        if len(query) > 200:
            logging.error("Memory search query exceeds its character limit")
            raise ValueError("memory search query exceeds 200 characters")
        return query


class MemoryTimeRangeRequest(_ToolRequest):
    """@brief 按 ISO 时间闭区间读取记忆。"""

    start_time: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
    end_time: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
    limit: int = Field(ge=1, le=100)

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        """@brief 时区与 ISO 格式错误定位具体时间边界。"""
        _parse_memory_time(value)
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> "MemoryTimeRangeRequest":
        """@brief 要求时区明确且时间闭区间顺序有效。"""
        if _parse_memory_time(self.start_time) > _parse_memory_time(self.end_time):
            logging.error("Memory time range is reversed")
            errors = [{"type": "value_error", "loc": (field,), "input": getattr(self, field),
                       "ctx": {"error": ValueError("memory time range is reversed")}}
                      for field in ("start_time", "end_time")]
            raise ValidationError.from_exception_data(type(self).__name__, errors)
        return self


class ReadonlyFinishRequest(_ToolRequest):
    """@brief 结束明确只读人物查询。"""

    answer: str = Field(pattern=r"\S")

    @field_validator("answer")
    @classmethod
    def validate_answer_length(cls, answer: str) -> str:
        """@brief 回答长度错误精确指向回答字段。"""
        if len(answer) > 4000:
            logging.error("Readonly answer exceeds its character limit")
            raise ValueError("readonly answer exceeds 4000 characters")
        return answer


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
    "query_world": QueryWorldRequest,
}

_QUERY_TOOL_MODELS = {"recall_task_summary": REQUEST_MODELS["recall_task_summary"],
                      "query_world": QueryWorldRequest, **_MEMORY_TOOL_MODELS}
_WORLD_TOOL_MODELS = {name: model for name, model in REQUEST_MODELS.items() if name != "recall_task_summary"}

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
        """@brief 保存完整协议结构和失败阶段，证据写入失败立即终止。"""
        if evidence.phase == "failure":
            evidence = replace(evidence, error=_stage_feedback("MODEL_SERVICE_FAILED", ("tool_calls",)))
        if isinstance(evidence.error, HostFeedback):
            response = dict(evidence.response or {})
            response["protocol_feedback"] = evidence.error.report.payload()
            evidence = replace(evidence, error=str(evidence.error), response=response)
        try:
            await super()._record_model_evidence(evidence)
        except Exception as error:
            feedback = _stage_feedback("EVIDENCE_WRITE_FAILED", ("tool_calls",))
            raise RuntimeError(feedback) from error
        if evidence.phase == "response":
            compare_usage(evidence.request, evidence.response)

    @staticmethod
    def _validate_react_tool_names(decisions: list[ReactDecision], tools: list[dict[str, Any]]) -> str:
        """@brief 闭集工具名核对同样保留真实调用位置与全部合法工具。"""
        allowed = tuple(item["function"]["name"] for item in tools if item["type"] == "function")
        items = tuple(ErrorItem(("tool_calls", index, "function", "name"), decision.action,
                                "TOOL_CALL_REQUIRED", allowed, "A tool offered in this round is required.",
                                "The tool is not offered in this round.")
                      for index, decision in enumerate(decisions) if decision.action not in allowed)
        return host_feedback(host_report("TOOL_CALL_REQUIRED", items=items), items[0].why) if items else ""

    @staticmethod
    def _protocol_feedback_content(error: str) -> str:
        """@brief 协议纠错正文（覆写 AS2 同名静态方法，锁定提交不改）：共用错误结构 {error_code, reason, fix, items,
        unmet} 加固定 instruction，不带英文校验转储（docs/dev/SPEC-dayrun-fidelity.md 第四章）。"""
        return feedback_content(error)

    def _parse_react_responses(self, response: Any, *, readonly: bool = False) -> tuple[list[ReactDecision], str]:
        """@brief 参数 JSON、未知工具和真实调用位置在执行前原子校验。"""
        if truncated(response):
            return [], truncation_feedback()
        choices = getattr(response, "choices", None)
        message = getattr(choices[0], "message", None) if choices else None
        calls = getattr(message, "tool_calls", None) or []
        decisions, reports = _parse_model_calls(calls, self._life_request_models)
        if reports:
            for index, decision in decisions:
                error = self._decision_error(decision)
                if error:
                    reports.append(bind_report(feedback_report(error), index))
            return [], _combine_reports(reports)
        parsed = [decision for _, decision in decisions]
        finish = next((decision for decision in parsed if decision.action == "finish"), None)
        if finish is not None and len(parsed) == 1:
            parsed = [replace(finish, final=str(finish.args.get("answer", "")))]
        return parsed, ""


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

    async def step(self, tick: int, t: datetime) -> str:
        """@brief 保留人物决策失败的完整异常链，便于定位真实服务故障。"""
        try:
            return await super().step(tick, t)
        except Exception:
            logging.exception("LifeAgent decision failed for agent %s", self.id)
            raise

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
        """@brief 最终模型回合和截断重试只允许世界请求；只读问答只允许 finish。"""
        retry = retry_due(self._react_messages, self._last_model_request)
        queries = _QUERY_TOOL_MODELS if self._remaining_turns() > 1 and not retry else {}
        if readonly:
            memory = {name: model for name, model in queries.items() if name in _MEMORY_TOOL_MODELS}
            return {**memory, "finish": ReadonlyFinishRequest}
        return {**_WORLD_TOOL_MODELS, **queries}


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
        self._life_observation = data
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

    def validate_react_decisions(self, decisions: list[ReactDecision], readonly: bool) -> str:
        """@brief 整批先校验全部独立错误，再允许任何查询或世界请求执行。"""
        if readonly != self._life_readonly:
            raise RuntimeError("LifeAgent readonly decision state is inconsistent")
        reports = []
        batch = _batch_error(decisions, self._life_action_count, tuple(self._life_request_models))
        if batch:
            reports.append(feedback_report(batch))
        for index, decision in enumerate(decisions):
            error = self._decision_error(decision)
            if error:
                reports.append(bind_report(feedback_report(error), index))
        if reports:
            return _combine_reports(reports)
        self._life_call_positions = {decision.tool_call_id: index for index, decision in enumerate(decisions)}
        return ""


    def _decision_error(self, decision: ReactDecision) -> str:
        """@brief 结构严格类型校验后按冻结观察验证工具的完整语义。"""
        model = self._life_request_models.get(decision.action)
        if model is None:
            item = ErrorItem(("tool_calls", 0, "function", "name"), decision.action, "TOOL_CALL_REQUIRED",
                             tuple(self._life_request_models), "A tool offered in this round is required.",
                             "The requested tool is not offered in this round.")
            return host_feedback(host_report("TOOL_CALL_REQUIRED", items=(item,)), item.why)
        try:
            validated = _validate_request(model, decision.args)
        except ValidationError as error:
            return argument_feedback(decision.action, error, model, decision.args)
        if decision.action == "query_world":
            report = validate_world_query(self._life_observation, validated)
            return host_feedback(report, report.reason) if report else ""
        return self._prepared_action_error(decision.action, validated)


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
            if decision.action in _WORLD_TOOL_MODELS:
                self._last_finish_memories = _conclusion_memories(decision.action, decision.args)
                return f"{decision.action} registered"
        return None

    async def _execute_react_tool(self, decision: ReactDecision, *, readonly: bool = False) -> ReactToolResult:
        """@brief 工具服务异常保留调用位置；不声称失败的调用已执行。"""
        try:
            return await super()._execute_react_tool(decision, readonly=readonly)
        except Exception as error:
            logging.exception("LifeAgent tool dispatch failed: %s", decision.action)
            index = self._life_call_positions[decision.tool_call_id]
            feedback = _stage_feedback("TOOL_SERVICE_FAILED", ("tool_calls", index))
            raise RuntimeError(feedback) from error

    async def dispatch_react_tool(
        self,
        action: str,
        args: dict[str, Any],
        *,
        readonly: bool = False,
    ) -> ReactToolResult:
        """@brief 直接执行生活动作或严格只读记忆工具。"""
        if self._life_action_count:
            logging.error("Tool dispatch attempted after the world request ended this decision")
            raise RuntimeError("Life action was already submitted")
        if action in _WORLD_TOOL_MODELS:
            if readonly:
                raise RuntimeError("Life actions are disabled in readonly mode")
            return await self._submit_life_action(action, args)
        if action == "query_world":
            data = query_world(self._life_observation, _validate_request(QueryWorldRequest, args))
            return ReactToolResult(True, json.dumps(data, ensure_ascii=False), data)
        if action == "recall_task_summary":
            if self._env is None:
                raise RuntimeError("Environment is not initialized")
            data = await self._env.read_query(self.require_decision_context(), action,
                                              _validate_request(REQUEST_MODELS[action], args))
            return ReactToolResult(True, json.dumps(data, ensure_ascii=False), data)
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

        宿主完整保留回执；查询失败保留五字段结构和实际调用位置。
        """
        if decision.action in _WORLD_TOOL_MODELS:
            return
        if not decision.tool_call_id:
            logging.error("Native tool call id is unavailable")
            raise RuntimeError("Native tool call id is unavailable")
        payload = result.data
        if not result.ok:
            index = self._life_call_positions[decision.tool_call_id]
            payload = {**payload, "items": [{**item, "path": ["tool_calls", index, *item["path"][2:]]}
                                           for item in payload["items"]]}
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
            feedback = _stage_feedback("MEMORY_QUERY_FAILED", ("tool_calls", 0), observation)
            return ReactToolResult(False, str(feedback), feedback.report.payload())
        episodes = [{key: item.get(key) for key in _MEMORY_RESULT_FIELDS} for item in data.get("episodes", [])]
        projected = {"epistemic_status": "subjective", "episodes": episodes}
        return ReactToolResult(True, json.dumps(projected, ensure_ascii=False), projected)


def _stage_feedback(code: str, path: tuple, detail: str | None = None) -> HostFeedback:
    """@brief 服务和证据异常明确指出失败阶段，不虚构参数或执行结果。"""
    report = host_report(code)
    reason = f"{report.reason} {detail}" if detail else report.reason
    item = ErrorItem(path, None, code, expected=report.fix, why=reason)
    return host_feedback(replace(report, reason=reason, items=(item,)), reason)


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
    记忆层 self_notes 所写的活动名（活动绑定的 note_labels）；事务名不进回看名称。个人事务变更的本地校验读两类私有事务
    绑定原样的行：errand_bindings（本人未完成事务的名称、事务身份、结果种类与商品身份）与 errand_item_bindings（全部
    商品名到商品身份），与环境复核读同一份冻结观察（docs/dev/SPEC-errands-workday.md 2.3）。
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
        "errand_bindings": _binding_rows(data, "errand_bindings", ("name", "errand_id", "outcome", "product_id")),
        "errand_item_bindings": _binding_rows(data, "errand_item_bindings", ("name", "product_id")),
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
    return tuple(row[field] for row in _binding_rows(data, key, (field,)))


def _binding_rows(data: dict[str, Any], key: str, fields: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    """@brief 一类私有绑定原样的行：列表里每行都是对象且给定字段都是字符串；缺失或形状不符是损坏的宿主观察。"""
    rows = data.get(key)
    if not isinstance(rows, list) or any(
            not isinstance(row, dict) or any(not isinstance(row.get(field), str) for field in fields) for row in rows):
        logging.error("LifeAgent private bindings %s are invalid", key)
        raise RuntimeError(f"LifeAgent {key} are invalid")
    return tuple(rows)


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
    NAME_NOT_FOUND、归一后不止一个的写成 NAME_AMBIGUOUS（第四章）；控制工具的活动名、结束工具关系看法的对象与请求级
    个人事务变更的问题一并逐项报全部。"""
    if action == "start_activity":
        return _validate_start_activity(request, names)
    if action in _ACTIVITY_CONTROL_TOOLS:
        return _control_feedback(action, request, names)
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


def _control_feedback(action: str, request: BaseModel, names: dict[str, Any]) -> str:
    """@brief 控制工具（播报、暂停、结束）逐项核对名称与请求级个人事务变更，全部问题合并成一次纠错
    （docs/dev/SPEC-dayrun-fidelity.md 3.1 第 5 条、第四章；docs/dev/SPEC-errands-workday.md 2.3）：各项的活动名
    （NAME_NOT_FOUND、NAME_AMBIGUOUS）、各项关系看法的对象（SUBJECT_NOT_KNOWN，只有结束工具带看法），再是事务变更在冻结
    观察私有事务绑定上的问题——与环境复核同一函数 errands.change_issues（更新或取消的名称找不到或有歧义、同一事务被点名
    两次、商品名解析不到唯一商品、新增重名、同结果同商品、超过上限），路径到 errands[j].<键>、不带候选。明细按此先后
    排列，码取第一项的码；都成立时为空串。请求经唯一读取入口 control_items 与 control_errands 读出。"""
    items = control_items(action, request)
    total = len(items)
    errand_issues = errands.change_issues(control_errands(action, request), names)
    issues = [*_name_issues(("activity_name", "活动"), [item.activity_name for item in items], names["activity_names"],
                            f"{action} activity_name names an activity that is not visible"),
              *_subject_issues(items, names["known_persons"]),
              *(HostIssue(item, sentence, sentence) for item, sentence in errand_issues)]
    return merged_feedback(issues, total, _name_fixes(total)) if issues else ""


def _subject_issues(items: tuple[ControlItem, ...], known: frozenset[str]) -> list[HostIssue]:
    """@brief 每条关系看法的真实嵌套位置独立返回。"""
    issues = []
    for index, item in enumerate(items):
        for view_index, view in enumerate(item.views):
            if view["type"] != "relationship" or view["subject"] in known:
                continue
            reason = f"The relationship subject {view['subject']} is not a known person."
            error = ErrorItem(("items", index, "views", view_index, "about"), view["subject"],
                              "SUBJECT_NOT_KNOWN", expected="The exact name of a known person is required.", why=reason)
            issues.append(HostIssue(error, reason, reason))
    return issues


def _batch_error(decisions: list[ReactDecision], submitted: int, available: tuple = ()) -> str:
    """@brief 只读查询可同批，世界请求或 finish 必须独占，整批原子拒绝。"""
    business = [item for item in decisions if item.action in _WORLD_TOOL_MODELS or item.action == "finish"]
    invalid = not decisions or len(business) > 1 or (business and len(decisions) != 1) or bool(submitted)
    if not invalid:
        return ""
    reason = "Submit exactly one world request, or a batch containing only read queries."
    item = ErrorItem(("tool_calls",), [item.action for item in decisions], "TOOL_CALL_REQUIRED", available,
                     reason, "The call combination violates atomic batch rules.")
    return host_feedback(host_report("TOOL_CALL_REQUIRED", reason, items=(item,)), reason)


def _unknown_names(field: tuple[str, str], written: list[str], visible, detail: str) -> str:
    """@brief 名称类参数逐项解析后的纠错（_name_issues），逐项全部报出、合并成一次；都成立时为空串。"""
    issues = _name_issues(field, written, visible, detail)
    return merged_feedback(issues, len(written), _name_fixes(len(written))) if issues else ""


def _name_issues(field: tuple[str, str], written: list[str], visible, detail: str) -> list[HostIssue]:
    """@brief 名称错误逐项返回完整位置，名称不是静态参数枚举。"""
    issues = []
    for index, name in enumerate(written):
        match = resolve_name(name, visible)
        if match.name is not None:
            continue
        code = "NAME_AMBIGUOUS" if match.matches else "NAME_NOT_FOUND"
        reason = f"The name {name} is ambiguous." if match.matches else f"The name {name} is not known."
        path = ("items", index, field[0]) if field[0] == "activity_name" else (field[0],)
        error = ErrorItem(path, name, code, expected="One exact known name is required.", why=reason)
        issues.append(HostIssue(error, reason, detail))
    return issues


def _name_fixes(total: int) -> dict[str, str]:
    """@brief 修正姓名时使用公开的完整写法。"""
    return {"NAME_NOT_FOUND": "Use an exact known name.", "NAME_AMBIGUOUS": "Use an unambiguous complete name."}


def _quoted(names) -> str:
    """@brief 几个名称的说法「A」、「B」。"""
    return "、".join(f"「{name}」" for name in names)


def _validate_start_activity(request: BaseModel, names: dict[str, Any]) -> str:
    """@brief 名称、方式、来源和去向的所有独立错误原子合并。"""
    outcome = resolve_start(names["book"], request)
    issues = [HostIssue(failure.item, failure.sentence, failure.detail) for failure in outcome.failures]
    failed = {failure.item.path[1] for failure in outcome.failures}
    route = combination_issues(outcome.request, names["routing"])
    for issue in route:
        if issue.index in failed:
            continue
        path = ("items", issue.index, issue.field) if issue.index is not None else (issue.field,)
        item = ErrorItem(path, issue.written, issue.host_code or "ROUTE_FIELDS_INVALID", issue.allowed_values,
                         issue.reason, issue.detail)
        issues.append(HostIssue(item, issue.reason, issue.detail))
    return merged_feedback(issues, len(request.items)) if issues else ""






def _menu_feedback(denied: str, request: BaseModel, outcome: StartOutcome, allowed: tuple[str, ...]) -> HostFeedback:
    """@brief 菜单拒绝保留每项方式位置和目标方式闭集。"""
    issues = []
    for index, row in enumerate(outcome.rows):
        action = row["selected_action"]
        if action["canonical_action"] in allowed:
            continue
        reason = f"The way for {row['object']} is not admitted at the current place."
        ways = tuple(item["label"] for item in row["actions"] if item["canonical_action"] in allowed)
        item = ErrorItem(("items", index, "way"), request.items[index].way, "WAY_NOT_OFFERED", ways,
                         "This way requires an admitting location.", reason)
        issues.append(HostIssue(item, reason, denied))
    return merged_feedback(issues, len(request.items))


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
    """@brief 修订保留已推进目标并逐项明确重复意图位置。"""
    issues = []
    if len(goals) + len(progressed_intents) > MAX_PLAN_GOALS:
        reason = "At most eight goals are allowed including preserved progressed goals."
        issues.append(HostIssue(ErrorItem(("goals",), len(goals), "ARGUMENTS_INVALID", expected=reason,
                                          why=reason), reason, reason))
    for index, goal in enumerate(goals):
        if goal.intent_kind not in progressed_intents:
            continue
        reason = "This intent has already progressed and cannot be resubmitted."
        item = ErrorItem(("goals", index, "intent_kind"), goal.intent_kind, "ARGUMENTS_INVALID",
                         expected="Submit only intents that have not progressed.", why=reason)
        issues.append(HostIssue(item, reason, reason))
    return merged_feedback(issues, len(goals)) if issues else ""


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


def _combine_reports(reports: list[ErrorReport]) -> HostFeedback:
    """@brief 保留批内全部独立明细，一次纠正后整体重交。"""
    items = tuple(item for report in reports for item in report.items)
    reason = "; ".join(dict.fromkeys(report.reason for report in reports))
    report = ErrorReport(reports[0].error_code, reason, "Correct all reported errors and resubmit the entire batch.",
                         items, tuple(dict.fromkeys(value for report in reports for value in report.unmet)))
    return host_feedback(report, reason)


def _parse_model_calls(calls: list, models: dict) -> tuple[list, list[ErrorReport]]:
    """@brief 逐调用保留 JSON 损坏位置和本轮工具枚举，合法调用仍参与独立校验。"""
    if not calls:
        reason = "At least one complete native tool call is required."
        item = ErrorItem(("tool_calls",), calls, "TOOL_CALL_REQUIRED", tuple(models), reason, reason)
        return [], [host_report("TOOL_CALL_REQUIRED", reason, items=(item,))]
    decisions, reports, seen = [], [], set()
    for index, call in enumerate(calls):
        function = getattr(call, "function", None)
        name = getattr(function, "name", None)
        raw = getattr(function, "arguments", None)
        identity = getattr(call, "id", None)
        if name not in models:
            item = ErrorItem(("tool_calls", index, "function", "name"), name, "TOOL_CALL_REQUIRED", tuple(models),
                             "A tool offered in this round is required.", "The tool is not offered in this round.")
            reports.append(host_report("TOOL_CALL_REQUIRED", items=(item,)))
        if not identity or identity in seen:
            item = ErrorItem(("tool_calls", index, "id"), identity, "TOOL_CALL_REQUIRED",
                             expected="A non-empty unique tool call identity is required.",
                             why="The tool call identity is missing or duplicated.")
            reports.append(host_report("TOOL_CALL_REQUIRED", items=(item,)))
        seen.add(identity)
        parsed, error = _parse_arguments(raw, index)
        if error:
            reports.append(error)
        elif name in models:
            decisions.append((index, ReactDecision("", name, parsed, "", identity)))
    return decisions, reports


def _parse_arguments(raw: Any, index: int) -> tuple[dict | None, ErrorReport | None]:
    """@brief JSON 损坏时只定位调用参数根，不猜未完成字段。"""
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
