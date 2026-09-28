"""@brief AgentSociety2 原生 LifeAgent，使用闭集生活工具直接调用 LifeEnv。

一次决策只有一个行动阶段（docs/dev/SPEC-persona-prompt.md 第二章「单一行动阶段」）：请求由原生 tools、系统层
system 与六层 user 组成；可以先做记忆查询，世界请求登记即结束本次决策，登记后不追加任何 tool 消息。原生工具
整次运行不变，不按轮绑定名称枚举；名称只按观察私有绑定做本地校验，可用性由环境受理判定。同一秒的后续决策
在准备阶段之后注入环境交来的续接链（第二章「同秒续接」，custom/agents/life_continuation.py）。
"""

from __future__ import annotations

import json
import hashlib
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
from custom.agents.life_memory import memory_update_evidence
from custom.agents.life_persona import known_persons, persona_layer, readonly_layers, write_prompt_snapshot
from custom.agents.life_prompt import (TOOL_DESCRIPTIONS, life_user_layers, readonly_messages, system_message,
                                       user_message)
from worldmock.life.continuation import CONTINUATION_KEY
from worldmock.life.interaction_menu import WAYS, is_named_purchase
from worldmock.life.models import (ActionReceipt, ConcludeActivityRequest, REQUEST_MODELS, deepseek_strict_schema,
                                   purpose_only_schema)
from worldmock.life.prompt_capacity import ProgressBudget, compare_usage, fit_request
from worldmock.life.routing import RECIPIENT_NOT_FOUND, combination_error, routing_names
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
        """@brief 保存模型调用证据并对照实际输入 token 量。"""
        await super()._record_model_evidence(evidence)
        if evidence.phase == "response":
            compare_usage(evidence.request, evidence.response)

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
        """@brief 整次运行不变的原生工具；只有本次决策只剩最后一个回合时去掉记忆查询工具。

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
        兜底（"native tool_calls are required"）。system 不附加任何预算文字。带续接链的请求先做发送前协议自检；
        预算第 0 步结束续接后，本决策此后的回合都从新快照续写。
        """
        request = super()._model_request(messages, tools)
        request["tool_choice"] = ACTION_TOOL_CHOICE
        request["max_tokens"] = ACTION_MAX_TOKENS
        request["extra_body"] = dict(ACTION_THINKING)
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
        """@brief 当前请求的工具模型：为世界请求（只读为 finish）保留 1 个回合，剩余不少于 2 个时才开放记忆查询。"""
        memory = _MEMORY_TOOL_MODELS if self._remaining_turns() > 1 else {}
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
        """@brief 在执行任何工具前验证闭集参数、原子动作数量与名称（名称只按私有绑定核对，不查可用性）。"""
        if readonly != self._life_readonly:
            raise RuntimeError("LifeAgent readonly decision state is inconsistent")
        models = self._life_request_models
        business = [item for item in decisions if item.action in REQUEST_MODELS]
        if len(business) > 1:
            return "one model response cannot contain multiple life actions"
        if business and len(decisions) != 1:
            return "a life action must be the only tool call in its model response"
        if business and self._life_action_count:
            return "this decision already submitted its life action"
        for decision in decisions:
            model = models.get(decision.action)
            if model is None:
                return f"unknown LifeAgent tool: {decision.action}"
            try:
                validated = _validate_request(model, decision.args)
                error = self._prepared_action_error(decision.action, validated)
            except Exception as exc:
                return f"invalid {decision.action} arguments: {exc}"
            if error:
                return error
        return ""

    def _prepared_action_error(self, action: str, request: BaseModel) -> str:
        """@brief 用同一冻结观察核对名称与菜单；非生活动作（记忆查询、finish）不核对名称。"""
        if action not in REQUEST_MODELS:
            return ""
        error = _validate_prepared_action(action, request, self._life_names)
        if error:
            return error
        return life_menu.menu_action_error(
            self._life_interaction_menu, action, _selected_actions(action, request, self._life_names["objects"]))

    async def _run_react_turn(self, request: ReactLoopRequest, turn: int) -> str | None:
        """@brief 生活决策的一个模型回合：世界请求登记即结束本轮（取消 finish，AS2 锁定提交不改）。

        记忆查询是观察，不结束本轮；世界请求登记后，conclude_activity 的 memories 按 task 展平交给本步
        after_step 写入（其余请求写入空列表）。只读问答沿用 AS2 的 finish 回合。
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
    """@brief 把 conclude_activity.memories 按 task 展平为本步写入的记忆；其余世界请求不写记忆。

    来源由系统按写入点固定为 activity_conclusion，认知状态固定为 subjective，模型不填、不选任何身份。
    """
    if action != "conclude_activity":
        return []
    request = _validate_request(ConcludeActivityRequest, args)
    return [{**memory.model_dump(mode="json", exclude={"subject"}), "source": _CONCLUSION_MEMORY_SOURCE,
             "epistemic_status": "subjective"}
            for group in request.memories for memory in group]


def _visible_names(data: dict[str, Any], todo: dict[str, Any]) -> dict[str, Any]:
    """@brief 冻结本轮本地校验所用的名称：交互对象的（动词，方式，内部标准动作）、任务、人物、活动与回看名称，以及来源
    与去向的判定名称（人物、可见直接消息）。

    只读观察私有绑定与待办层；缺失或形状不符的绑定是损坏的宿主观察。people 是同世界其他人物的姓名（不论是否同处，
    私有绑定 person_bindings），供 relationship 记忆的对象校验。
    """
    objects = _binding_objects(data.get("interaction_bindings"))
    tasks = _binding_names(data, "task_bindings", "task_name")
    goals = _binding_names(data, "goal_bindings", "task_name")
    activities = _binding_names(data, "activity_bindings", "name")
    sessions = _binding_names(data, "session_bindings", "name")
    kinds = {row["object"]: row["kind"] for row in data["interaction_bindings"]}
    return {
        "objects": objects,
        "task_names": tasks,
        "recipient_names": tuple(name for name in objects if kinds[name] in {"person", "contact"}),
        "people": tuple(name.split("：", 1)[-1] for name in _binding_names(data, "person_bindings", "name")),
        "activity_names": activities,
        "recall_names": (*tasks, *goals, *activities, *sessions),
        "progressed_intents": _progressed_intents(todo),
        "routing": routing_names(data),
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


def _progressed_intents(todo: dict[str, Any]) -> tuple[str, ...]:
    """@brief 今日计划中已推进目标的意图（没有计划时为空，修订不受约束）。"""
    plan = todo.get("day_plan")
    if plan is None:
        return ()
    return tuple(goal["intent_kind"] for goal in plan["goals"] if goal["progressed"])


def _validate_prepared_action(action: str, request: BaseModel, names: dict[str, Any]) -> str:
    """@brief 用同一冻结观察的私有绑定核对工具参数中的名称（docs/dev/SPEC-persona-prompt.md 第一章「工具层」）。"""
    if action == "start_activity":
        return _validate_start_activity(request, names)
    if action in _ACTIVITY_CONTROL_TOOLS:
        if any(name not in names["activity_names"] for name in request.task):
            return f"{action} task names an activity that is not visible"
        return _memory_subject_error(request, names["known_persons"]) if action == "conclude_activity" else ""
    if action == "recall_task_summary":
        return "" if request.task_name in names["recall_names"] else "task_name is not visible"
    if action == "submit_work":
        return "" if request.task_name in names["task_names"] else "task_name is not visible"
    if action == "send_message":
        return "" if request.recipient_name in names["recipient_names"] else "recipient_name is not visible"
    return _validate_plan_goals(request.goals, names["progressed_intents"])


def _memory_subject_error(request: BaseModel, known: frozenset[str]) -> str:
    """@brief relationship 记忆的对象须是已知人物：卡片关系名、场景联系人称谓或同世界人物姓名（规格第一章「人设层」）。"""
    unknown = [memory.subject for group in request.memories for memory in group
               if memory.type == "relationship" and memory.subject not in known]
    return f"conclude_activity relationship subject is not a known person: {unknown[0]}" if unknown else ""


def _validate_start_activity(request: BaseModel, names: dict[str, Any]) -> str:
    """@brief 对象、动词与方式逐项对应，并取自本轮交互对象私有绑定列出的动作；来源、去向与 answering 的组合合法
    （worldmock.life.routing，与环境受理同一判定）。

    obtain 以到店或远程下单写本轮未列出的名称时放行（指名购买，由环境按名称解析）；其余动词的目标仍须是本轮列出的
    名称，否则按协议错误在本轮纠正（docs/dev/SPEC-persona-prompt.md 第五章「指名购买」）。去向不是任何人物
    （RECIPIENT_NOT_FOUND）是拒绝码，宿主放行，由环境以业务拒绝回给模型（第五章「来源与去向」）。
    """
    if not 1 <= len(request.targets) <= _START_ACTIVITY_MAX_TARGETS:
        return "start_activity accepts one or two targets"
    if len(request.targets) != len(request.actions) or len(request.actions) != len(request.ways):
        return "start_activity targets, actions and ways must have equal lengths"
    for target, verb, way in zip(request.targets, request.actions, request.ways):
        actions = names["objects"].get(target)
        if actions is None and is_named_purchase(verb, way):
            continue
        if actions is None:
            return "start_activity target is not a visible interactive object"
        if not any(item[:2] == (verb, way) for item in actions):
            return "start_activity verb and way are not offered by the chosen target"
    error = combination_error(request, names["routing"])
    return error[1] if error is not None and error[0] != RECIPIENT_NOT_FOUND else ""


def _selected_actions(action: str, request: BaseModel, objects: dict[str, tuple[tuple[str, str, str], ...]]
                      ) -> list[str]:
    """@brief start_activity 各项经本轮私有绑定映射后的内部标准动作（其他工具为空；未列出的项不计入，指名购买计为购买）。"""
    if action != "start_activity":
        return []
    selected = []
    for target, verb, way in zip(request.targets, request.actions, request.ways):
        if target not in objects and is_named_purchase(verb, way):
            selected.append(WAYS[way][1])
        selected.extend(item[2] for item in objects.get(target, ()) if item[:2] == (verb, way))
    return selected


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
    """@brief 修订仅包含未推进目标，合并后保持容量和意图唯一性。"""
    if len(goals) + len(progressed_intents) > MAX_PLAN_GOALS:
        logging.error("Day plan exceeds eight goals after preserving progressed goals")
        return "day plan requires at most eight goals including progressed goals"
    if set(progressed_intents).intersection(goal.intent_kind for goal in goals):
        logging.error("Day plan revision resubmits a progressed intent")
        return "revise_day_plan cannot resubmit progressed goals"
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
