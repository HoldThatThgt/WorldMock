"""@brief AgentSociety2 原生 LifeAgent，使用闭集生活工具直接调用 LifeEnv。"""

from __future__ import annotations

import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentsociety2.agent import PersonAgent
from agentsociety2.agent.base import (
    ModelCallEvidence,
    ReactDecision,
    ReactLoopRequest,
    ReactToolResult,
)
from agentsociety2.society import DirectToolCall
from custom.agents import life_menu
from custom.agents.life_context import decision_statement_content, persona_card_text
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_CONCLUDED, PHASE_ENDED, PHASE_PAUSED, PHASE_PENDING
from worldmock.life.interaction_menu import canonical_action
from worldmock.life.models import (ActionReceipt, ConcludeActivityRequest, REQUEST_MODELS, SameMomentStage,
                                   deepseek_strict_schema)
from worldmock.life.prompt_capacity import compare_usage, fit_request
from worldmock.life.same_moment import RESTRICTED_EXCLUDED_ACTIONS, STAGE_TOOLS
from worldmock.life.narration import (
    DecisionStatementValidationError,
    compose_user_message,
    render_facts,
    render_scene,
    validate_decision_statement,
)
from worldmock.life.schedule import INTENT_KINDS, MAX_PLAN_GOALS


#: 决策自述阶段采样参数（模型更换后 temp 1.3 偶发采样脱轨：reasoning 螺旋烧穿、
#: 多语言乱码、超长输出，均发于一日模拟 07:30 最富上下文轮次；同请求 temp 1.0
#: 实测 4/4 正常、0.8 4/4 正常、1.3 累计 12 发 5 发病态，合同「生成参数」定 1.0）。
DECISION_STATEMENT_TEMPERATURE = 1.0
#: 决策自述阶段输出上限。
DECISION_STATEMENT_MAX_TOKENS = 8000
#: 行动阶段思考开关与工具选择（思考模式拒收 required，真实探针见规格）。
ACTION_THINKING = {"thinking": {"type": "enabled"}}
ACTION_TOOL_CHOICE = "auto"
#: 行动阶段明确预留的最大输出，用于发送前完整上下文计数。
ACTION_MAX_TOKENS = 8000

#: 一次会话至多服务两个交互对象（docs/dev/SPEC-agent-interaction.md「动作参数：能力如何收进 action」：
#: targets[] ≤2；DeepSeek strict 禁 maxItems，故上限只能由业务规则兜住）。
_START_ACTIVITY_MAX_TARGETS = 2
#: 日首唤醒原因闭集（docs/dev/SPEC-day-plan-goals.md「提交与修订」）：run_start/wake_from_sleep。当天首次
#: 醒来且当日尚无计划时，动作工具面只保留 plan_day（"必须先提交"的强制）；
#: 拒绝重试边界的唤醒原因是 rejection_feedback，不属日首，不触发强制。
_DAY_START_WAKE_REASONS = frozenset({"run_start", "wake_from_sleep"})
#: 结束活动时写入的记忆来源（系统按写入点固定，与 memory_updated.data.trigger 同一组取值）。
_CONCLUSION_MEMORY_SOURCE = "activity_conclusion"


class _ToolRequest(BaseModel):
    """@brief 所有 LifeAgent 专用工具请求都拒绝未知字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


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


class MemoryStepRangeRequest(_ToolRequest):
    """@brief 按人物决策步骤闭区间读取记忆。"""

    start_step: int = Field(ge=0)
    end_step: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def validate_interval(self) -> "MemoryStepRangeRequest":
        """@brief 拒绝反向人物决策步骤闭区间。"""
        if self.start_step > self.end_step:
            raise ValueError("memory step range is reversed")
        return self


class MemoryTickRangeRequest(_ToolRequest):
    """@brief 按决策持续秒数闭区间读取记忆。"""

    start_tick: int = Field(ge=0)
    end_tick: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def validate_interval(self) -> "MemoryTickRangeRequest":
        """@brief 拒绝反向决策持续秒数闭区间。"""
        if self.start_tick > self.end_tick:
            raise ValueError("memory tick range is reversed")
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
    "memory_step_range": MemoryStepRangeRequest,
    "memory_tick_range": MemoryTickRangeRequest,
    "memory_time_range": MemoryTimeRangeRequest,
}

#: 生活决策的工具面：八个人物工具（plan_day 暂未移出，见阶段 5）与预算内记忆查询；没有 finish。
_TOOL_MODELS: dict[str, type[BaseModel]] = {
    **REQUEST_MODELS,
    **_MEMORY_TOOL_MODELS,
}

_MEMORY_RUNTIME_ACTIONS = {
    "memory_recent": "memory_recent",
    "memory_search": "memory_search",
    "memory_step_range": "memory_range",
    "memory_tick_range": "memory_range",
    "memory_time_range": "memory_range",
}

#: 记忆查询返回给模型的字段（不含条目身份，docs/dev/SPEC-time-and-energy.md 第七章「发给模型的内容不含内部身份」）。
_MEMORY_RESULT_FIELDS = ("type", "text", "time", "epistemic_status")

#: 需要内联嵌套子模型 Schema 的工具字段已在 life.models 的字段定义
#: 以 WithJsonSchema 内联（strict 合同禁嵌套引用键，docs/CONTRACTS.md「人物工具与管理接口」）；
#: 本侧直接使用 REQUEST_MODELS 的原始模型，无第二套适配层。


def life_action_tool_names() -> frozenset[str]:
    """@brief 返回 LifeAgent 实际开放的全部生活动作工具名（env 侧核对用）。"""
    return frozenset(_TOOL_MODELS) - frozenset(_MEMORY_TOOL_MODELS)


#: 阶段规则前缀：一次决策恰好一个世界请求，必须是响应中唯一调用，登记即结束本次决策。
_ACTION_PROTOCOL = (
    "During one decision submit exactly one life action; it must be the only "
    "tool call in its model response, and registering it ends the decision.")

#: 稳定工具面的静态工具文案（docs/dev/SPEC-agent-interaction.md「稳定工具面」工具表）。
#: 动作枚举与对象身份每轮由交互对象层绑定进 Schema，不写进这里的文字。
#: 工具协议、引用规则与阶段规则按提示合同（docs/dev/SPEC-life-runtime.md「系统提示与身份前言」）移入各工具 description。
_TOOL_DESCRIPTIONS = {
    "start_activity": (
        _ACTION_PROTOCOL
        + " Choose one or two named objects from facts.interactive_objects and one offered action "
        "phrase for each object in the same order. Choose only objects whose preconditions_met "
        "is true. Send exactly targets and actions. "
        "The world uses the observed quote and derives duration, quantity, travel mode and "
        "fulfillment. Prices and internal state are information from the world, never inputs."),
    "submit_work": (
        _ACTION_PROTOCOL
        + " Submit authored work for one visible task. Use its task_name and write the deliverable."),
    "send_message": (
        _ACTION_PROTOCOL
        + " Send an authored text message to one visible person or contact by recipient_name."),
    "recall_task_summary": (
        _ACTION_PROTOCOL
        + " Read one visible task, day goal, activity or session by task_name without changing the world; "
        "the result appears in the next observation."),
    "report_status": (
        _ACTION_PROTOCOL
        + " Report one or two visible activities by their names and keep them running. "
        "task and summary are equally sized lists paired in order. "
        "Reporting a paused activity resumes it, only at the place where it started. "
        "Each summary is at most 500 characters."),
    "pause_activity": (
        _ACTION_PROTOCOL
        + " Pause one or two running activities by their names; a paused activity still holds one of "
        "your two activity slots, and activities on the road cannot be paused. "
        "task and summary are equally sized lists paired in order. "
        "Invested time is kept. Each summary is at most 500 characters."),
    "conclude_activity": (
        _ACTION_PROTOCOL
        + " End one or two activities by their names: running, paused, or just ended and waiting to be "
        "wrapped up; this frees their slots. task, summary and memories are equally sized lists paired "
        "in order. Each summary is at most 500 characters. memories[i] holds zero or more memories "
        "about task[i], at most eight in total, written in the person's own words as subjective "
        "self-report; never put identifiers or hexadecimal strings in memory text, and use at most "
        "twelve keywords per memory."),
    "plan_day": (
        _ACTION_PROTOCOL
        + " At the first decision after waking, submit today's ordered goals, "
        "from zero to eight entries. An empty list means no deliberate goals today. "
        "Each intent_kind must be unique and belong to the closed enum; each goal "
        "must contain non-whitespace text of at most 300 characters. List order "
        "expresses priority. Do not provide times or durations. Activity evidence "
        "can show progress, but does not establish that the written goal is complete."),
    "revise_day_plan": (
        _ACTION_PROTOCOL
        + " Replace all pending goals by sending the complete new pending list, "
        "which may be empty. Progressed goals stay unchanged at the front; do not "
        "resubmit their intent_kind. The combined list accepts at most eight unique "
        "intent kinds. Each goal must contain non-whitespace text of at most 300 "
        "characters. Keep an unchanged pending goal verbatim to preserve its "
        "registration. Do not provide times or durations."),
    "memory_recent": "Read the person's most recent subjective durable memory episodes.",
    "memory_search": "Search the person's subjective durable memory episodes by keyword.",
    "memory_step_range": "Read subjective durable memory episodes within an inclusive decision-step range.",
    "memory_tick_range": "Read subjective durable memory episodes within an inclusive duration range.",
    "memory_time_range": "Read subjective durable memory episodes within an inclusive ISO time range.",
    "finish": (
        "Answer the external read-only question with answer; finish must be the only tool call "
        "in its response."),
}


#: 活动控制工具（docs/dev/SPEC-agent-interaction.md「保护项修订」）：按活动名逐项播报、暂停与结束。
_ACTIVITY_CONTROL_TOOLS = ("report_status", "pause_activity", "conclude_activity")

#: 进度层活动阶段的人读说法 → 阶段标识（进行中、已暂停、刚结束待收尾、已收尾、截止结束）。
_ACTIVITY_PHASES = {PHASE_ACTIVE: "active", PHASE_PAUSED: "paused", PHASE_PENDING: "pending",
                    PHASE_CONCLUDED: "concluded", PHASE_ENDED: "ended"}

#: 身份前言（docs/dev/SPEC-life-runtime.md「系统提示与身份前言」）：以人物与情境为主，工具协议、引用规则
#: 与阶段规则已移入各工具 description；不含"评审/评分/引擎期望"类措辞；
#: 地点礼仪由场景段落表达，不在此出现禁令句；回合预算只在剩余回合 ≤2 时
#: 由 _model_request 附加一句话。
_LIFE_INSTRUCTIONS = """
你不是AI助手，也不是在等待指令的程序。你是{name}，一个有自己性格、需要和处境的人，
正过着自己的日子：会饿、会困、会心烦，也会在意身边的人和没做完的事。
人物卡片写的就是你这个人：你是谁、日子过得怎么样、身边有谁、
平时喜欢什么、压力大了靠什么缓一缓。
场景段落是你此刻的真实处境：什么时间、在哪儿、身边有谁、身体感觉如何、
手头正有什么事。
事实清单是此刻对你有用的信息。
供给行若有 based_on_action_id，就继承该动作结果中相同 supply_id 的完整供给行，
再用 changes 按键替换；未列字段保持不变，null 是该字段的当前值，嵌套对象整项替换。
凭你自己的性子和眼下的处境拿主意，像一个真实的人那样过日子。
""".strip()

_READONLY_INSTRUCTIONS = """
你是{name}，正在以本人身份回答一个外部只读问题。凭你的卡片与持久记忆作答：
先按需要使用提供的记忆查询工具，再单独调用一次 finish 给出答案；
不要把答案写在正文里。
""".strip()

#: 决策自述的显式生成要求；确定性校验仅负责非空与汉字上限。
_DECISION_STATEMENT_INSTRUCTIONS = (
    "\n\n请用一至三句第一人称中文，说出你此刻的感受和打算，最多二百汉字。")

#: 世界结果尚未结算，模型只收到不带内部身份的固定登记确认。
_ACTION_REGISTRATION_CONFIRMATION = (
    "Your action has been registered for later settlement. Its outcome will appear in your next observation.")

_REACT_BUDGET_MARKER = "\n\nAuthoritative ReAct model-turn budget:\n"

#: 低预算提示（docs/CONTRACTS.md「人物决策提示与生成参数」）：剩余回合 ≤2 时以一句话出现（先于预算 JSON，
#: 保证预算 JSON 始终是消息尾部可解析块）。
_LOW_BUDGET_SENTENCE = (
    "\n\n剩余回合已经不多：当前阶段是{phase}，请尽快提交本阶段需要的动作或结束。")


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
        """@brief 按只读与生活决策阶段生成当前闭集工具 Schema；生活决策没有 finish。"""
        budget = self._react_budget(readonly)
        models = self._budgeted_tool_models(readonly, budget["memory_tools_available"])
        self._life_request_models = models
        schemas = [
            _tool_schema(
                name,
                model,
                _TOOL_DESCRIPTIONS,
            )
            for name, model in models.items()
        ]
        if not readonly and not getattr(self, "_life_action_count", 0):
            if not hasattr(self, "_life_visible_objects"):
                raise RuntimeError("LifeAgent visible interactive objects are not prepared")
            _bind_start_activity_schema(schemas, self._life_visible_objects, self._life_allowed_actions)
            _bind_control_schemas(schemas, self._life_action_contracts)
            _bind_plan_goal_schemas(schemas, self._life_action_contracts)
            _bind_named_action_schemas(schemas, self._life_action_contracts)
        return schemas

    def _model_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """@brief 注入思考开启的 ReAct 请求并写入当前实际模型回合预算。

        每次请求在实际消息中明确当前剩余回合与决策阶段（docs/CONTRACTS.md「人物决策提示与生成参数」）；
        剩余回合 ≤2 时按 docs/CONTRACTS.md「人物决策提示与生成参数」先附一句话低预算提示。
        DeepSeek thinking 模式拒绝 tool_choice=required（真实探针 400：
        "Thinking mode does not support this tool_choice"），因此与思考
        开启同时把 tool_choice 放宽为 auto；模型不产出工具调用时由
        ReAct 协议纠错回合兜底（"native tool_calls are required"）。
        """
        budget = self._react_budget(self._life_readonly)
        first = dict(messages[0])
        if budget["remaining_model_turns_including_current"] <= 2:
            first["content"] += _LOW_BUDGET_SENTENCE.format(phase=budget["phase"])
        first["content"] += _REACT_BUDGET_MARKER + json.dumps(
            budget, ensure_ascii=False, sort_keys=True)
        request = super()._model_request([first, *messages[1:]], tools)
        request["tool_choice"] = ACTION_TOOL_CHOICE
        request["max_tokens"] = ACTION_MAX_TOKENS
        request["extra_body"] = dict(ACTION_THINKING)
        facts = getattr(self, "_life_facts_for_capacity", None)
        return fit_request(request, facts, getattr(self, "_life_prompt_layers", None))

    def _life_require_turns(self, phase: str, required: int) -> None:
        """@brief 剩余回合不足时明确终止本次决策，不发注定失败的请求。"""
        remaining = self._max_react_turns - self._model_call_sequence
        if remaining < required:
            message = (
                f"ReAct phase {phase} requires {required} model turns but only "
                f"{remaining} remain"
            )
            self._logger.error("LifeAgent %s %s", getattr(self, "_id", None), message)
            raise RuntimeError(message)

    def _react_budget(self, readonly: bool) -> dict[str, Any]:
        """@brief 从本决策实际模型调用序号取得当前请求预算。

        生活决策的世界请求登记即结束本轮，行动前只需为它保留 1 个回合；只读问答为 finish 保留 1 个回合。
        """
        phase = "readonly" if readonly else "before_action"
        required = 1
        self._life_require_turns(phase, required)
        remaining = self._max_react_turns - self._model_call_sequence
        return {
            "phase": phase,
            "max_model_turns": self._max_react_turns,
            "used_model_turns": self._model_call_sequence,
            "remaining_model_turns_including_current": remaining,
            "required_model_turns_including_current": required,
            "memory_tools_available": remaining > required,
        }

    def _budgeted_tool_models(
        self,
        readonly: bool,
        memory_tools_available: bool,
    ) -> dict[str, type[BaseModel]]:
        """@brief 按阶段与预留边界形成当前请求唯一工具模型。"""
        memory = _MEMORY_TOOL_MODELS if memory_tools_available else {}
        if readonly:
            return {**memory, "finish": ReadonlyFinishRequest}
        if not hasattr(self, "_life_action_models"):
            raise RuntimeError("LifeAgent activity controls are not prepared")
        return {**self._life_action_models, **memory}

    def _persona_card_text(self) -> str:
        """@brief 从公开人物档案字段确定性渲染提示用人物卡片。"""
        return persona_card_text(self.get_profile(), self.name)

    def _new_memory_runtime(self, config):
        """@brief 复用 AS2 构造接点装配 WorldMock 的事实与主观记忆视图。"""
        from custom.agents.life_memory import WorldMockMemoryRuntime

        return WorldMockMemoryRuntime(self, config)

    def _memory_prompt_section(self) -> str:
        """@brief 渲染持久记忆摘要段：只写主观归纳与最近自述的正文，不带条目身份。"""
        if not self._enable_memory:
            return ""
        memory = self._build_memory_context()
        parts = []
        summary = memory.get("subjective_summary")
        if isinstance(summary, str) and summary.strip():
            parts.append("主观归纳：\n" + summary.strip())
        episodes = memory.get("recent_episodes") or []
        lines = [f"- [subjective] {item.get('text', '')}".rstrip()
                 for item in episodes if isinstance(item, dict)]
        if lines:
            parts.append("最近的人物自述：\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def _life_user_sections(self, request: ReactLoopRequest, *, with_facts: bool) -> str:
        """@brief 组装三段 user 消息文本（persona + scene + [已核验经历句子] + 记忆 [+ facts + 决策自述]）。

        决策自述阶段只取 persona + scene + 已核验经历句子 + 记忆摘要；行动阶段的经历句子随 facts.experiences 出现，
        再追加 facts 与"你刚才心里想的是"（docs/dev/SPEC-life-realism.md「两阶段决策」）。scene/facts 由
        narration 渲染器执行黑名单校验；观察不再序列化 JSON 副本，已核验经历只以句子出现，不带内部身份。
        """
        data = _environment_observation_data(request.observations)
        persona = self._persona_card_text()
        scene = render_scene(_scene_projection(data))
        memory = self._memory_prompt_section()
        experiences = "" if with_facts else _experience_text(data)
        core = compose_user_message(persona, scene, "\n\n".join(part for part in (experiences, memory) if part))
        self._life_prompt_layers = {"persona": persona, "scene": scene,
                                    "verified_experiences": experiences, "memory": memory}
        if not with_facts:
            return core
        self._life_facts_for_capacity = _facts_projection(data)
        facts = render_facts(self._life_facts_for_capacity)
        self._life_prompt_layers["progress"] = render_facts(self._life_facts_for_capacity["progress"])
        self._life_prompt_layers["other_facts"] = render_facts({
            key: value for key, value in self._life_facts_for_capacity.items() if key != "progress"})
        parts = [core, facts]
        decision_statement = getattr(self, "_life_decision_statement", None)
        if decision_statement:
            parts.append(f"你刚才心里想的是：{decision_statement}")
        user = "\n\n".join(parts)
        return user

    def build_react_messages(self, request: ReactLoopRequest) -> list[dict[str, Any]]:
        """@brief 构造只描述实际闭集工具的生活人物提示（三段消息）。"""
        template = _READONLY_INSTRUCTIONS if request.readonly else _LIFE_INSTRUCTIONS
        system = template.format(name=self.name or "this person")
        if request.readonly:
            sections = [compose_user_message(
                self._persona_card_text(), "", self._memory_prompt_section())]
            if request.question is not None:
                sections.append(f"外部只读问题：{request.question}")
            user = "\n\n".join(section for section in sections if section)
            self._life_prompt_layers = {"persona_and_memory": sections[0],
                                        "question": request.question or ""}
        else:
            user = self._life_user_sections(request, with_facts=True)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def prelude_decision_statement_request(self, request: ReactLoopRequest) -> dict[str, Any] | None:
        """@brief 两阶段决策的决策自述请求参数；只读决策不启用决策自述。

        决策自述 = system 身份前言 + persona_card + scene + 记忆摘要，不带
        tools；thinking=disabled、temperature=1.0、max_tokens=8000。
        """
        if request.readonly:
            return None
        if self._model_name is None:
            self._logger.error("Decision statement model is not initialized")
            raise RuntimeError("LLM is not initialized")
        system = (_LIFE_INSTRUCTIONS.format(name=self.name or "this person")
                  + _DECISION_STATEMENT_INSTRUCTIONS)
        user = self._life_user_sections(request, with_facts=False)
        params = {
            "model": self._model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "temperature": DECISION_STATEMENT_TEMPERATURE,
            "max_tokens": DECISION_STATEMENT_MAX_TOKENS,
            "max_retries": 0,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        return fit_request(params, layers=getattr(self, "_life_prompt_layers", None))

    def receive_prelude_decision_statement(self, request: ReactLoopRequest, decision_statement: str) -> None:
        """@brief 接收已通过协议校验的决策自述文本，供行动阶段消息引用。"""
        del request
        self._life_decision_statement = decision_statement

    async def _run_prelude_decision_statement(self, request: ReactLoopRequest) -> None:
        """@brief 执行单次无工具决策自述；超长或空消耗一个回合后重试一次。

        两次失败即本次决策失败（docs/dev/SPEC-life-realism.md「两阶段决策」）。决策自述经完整
        model_request/model_response 证据路径保存；传输异常立即传播。
        """
        params = self.prelude_decision_statement_request(request)
        if not params:
            return
        if "tools" in params:
            self._logger.error("Decision statement request must not expose tools")
            raise RuntimeError("Prelude decision_statement request must not expose tools")
        last_error: DecisionStatementValidationError | None = None
        for _attempt in (1, 2):
            self._life_require_turns("decision_statement", 2)
            decision_statement, last_error = await self._decision_statement_once(params)
            if decision_statement is not None:
                self.receive_prelude_decision_statement(request, decision_statement)
                return
        message = f"Prelude decision_statement failed the protocol twice: {last_error}"
        self._logger.error("%s", message)
        raise RuntimeError(message)

    async def _decision_statement_once(
        self, params: dict[str, Any]) -> tuple[str | None, DecisionStatementValidationError | None]:
        """@brief 调用一次决策自述模型并保存带校验结果的完整证据。

        :returns: (通过校验的决策自述文本或空值, 协议错误或空值)。
        """
        if self._dispatcher is None:
            self._logger.error("Decision statement dispatcher is not initialized")
            raise RuntimeError("LLM is not initialized")
        context = getattr(self, "_decision_context", None)
        call_id = self._next_model_call_id(1)
        await self._record_model_evidence(
            ModelCallEvidence(context, call_id, "request", 1, params))
        try:
            response = await self._dispatcher.call(**params)
        except Exception as exc:
            self._logger.error("Decision statement transport failed: %s", exc)
            await self._record_model_evidence(
                ModelCallEvidence(context, call_id, "failure", 1, params, error=repr(exc)))
            raise
        text = decision_statement_content(response)
        try:
            decision_statement = validate_decision_statement(text)
        except DecisionStatementValidationError as error:
            self._logger.error("Decision statement protocol validation failed: %s", error)
            decision_statement, validated_error = None, error
        else:
            validated_error = None
        await self._record_model_evidence(
            ModelCallEvidence(
                context, call_id, "response", 1, params,
                response=self._serialize_model_response(response),
                error=None if validated_error is None else str(validated_error),
            )
        )
        return decision_statement, validated_error

    def memory_sleep_boundary(self) -> bool:
        """@brief 报告本步骤是否为人物进入睡眠后的第一个边界。

        引擎在观察 data.wake_reasons 提供唤醒原因（B1）；就寝后第一个
        边界以 wake_from_sleep 标记，驱动就寝后的记忆巩固与反思。
        """
        return "wake_from_sleep" in getattr(self, "_life_wake_reasons", frozenset())

    def prepare_react_loop(self, request: ReactLoopRequest) -> None:
        """@brief 重置当前决策动作计数并冻结本轮观察给出的对象、活动与工具面。"""
        super().prepare_react_loop(request)
        self._life_action_count = 0
        self._life_readonly = request.readonly
        self._life_request_models = None
        self._life_decision_statement = None
        self._life_facts_for_capacity = None
        self._life_prompt_layers = {}
        if request.readonly:
            self._life_interaction_menu = None
            self._life_visible_objects = {}
            self._life_allowed_actions = frozenset()
            self._life_action_contracts = {}
            self._life_action_models = {}
            self._life_wake_reasons = frozenset()
            budget = self._react_budget(True)
            self._life_request_models = self._budgeted_tool_models(
                True, budget["memory_tools_available"])
            return
        data = _environment_observation_data(request.observations)
        self._prepare_life_action_state(data)
        if _plan_required_before_other_actions(data, self._life_action_models):
            # 日首唤醒且当日无计划：撤下其他生活动作工具，只留 plan_day。
            self._life_action_models = {
                name: model for name, model in self._life_action_models.items()
                if name == "plan_day"}
        budget = self._react_budget(False)
        self._life_request_models = self._budgeted_tool_models(
            False, budget["memory_tools_available"])

    def _prepare_life_action_state(self, data: dict[str, Any]) -> None:
        """@brief 从一次冻结观察组装菜单、交互对象层、活动名称和稳定工具面。

        同刻上限的受限轮按观察私有证据 same_moment_stage 收窄世界请求工具、交互对象与活动名称，
        Schema 枚举、本地校验与引擎复核因此按同一阶段判定（docs/dev/SPEC-time-and-energy.md 第一章「实现约定」）。
        """
        self._prepare_verified_experiences(data)
        self._life_interaction_menu = life_menu.parse_interaction_menu(data)
        self._life_wake_reasons = frozenset(_visible_wake_reasons(data))
        stage = _visible_same_moment_stage(data)
        self._life_visible_objects = _same_moment_objects(
            stage, _visible_interactive_objects(data), data["interaction_bindings"])
        self._life_allowed_actions = frozenset(self._life_interaction_menu.allowed_actions)
        self._life_action_contracts = _same_moment_contracts(stage, _visible_action_contracts(data))
        self._life_action_models = _available_action_models(self._life_action_contracts, STAGE_TOOLS[stage])

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

    def validate_react_decisions(
        self,
        decisions: list[ReactDecision],
        readonly: bool,
    ) -> str:
        """@brief 在执行任何工具前验证闭集参数和原子动作数量。"""
        if readonly != self._life_readonly:
            raise RuntimeError("LifeAgent readonly decision state is inconsistent")
        models = self._life_request_models
        if models is None:
            raise RuntimeError("LifeAgent request tool schemas are not prepared")
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
                if decision.action in REQUEST_MODELS:
                    return f"{decision.action} is not available in the current activity state"
                return f"unknown LifeAgent tool: {decision.action}"
            try:
                validated = _validate_request(model, decision.args)
                error = _validate_prepared_action(
                    decision.action, validated, self._life_visible_objects, self._life_action_contracts)
                if error:
                    return error
                if decision.action in REQUEST_MODELS:
                    error = life_menu.menu_action_error(
                        self._life_interaction_menu, decision.action, validated)
                    if error:
                        return error
            except Exception as exc:
                return f"invalid {decision.action} arguments: {exc}"
        return ""

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
        if action.startswith("memory_"):
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
        if action not in self._life_action_models:
            self._logger.error("Life action is unavailable in the prepared activity state")
            raise RuntimeError("Life action is unavailable in the prepared activity state")
        request = _validate_request(REQUEST_MODELS[action], args)
        context = self.require_decision_context()
        error = _validate_prepared_action(action, request, self._life_visible_objects, self._life_action_contracts)
        if error:
            raise RuntimeError(error)
        error = life_menu.menu_action_error(self._life_interaction_menu, action, request)
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
        """@brief 模型只见生活动作登记语义，宿主仍保留完整回执与记忆查询结果。"""
        if decision.action in REQUEST_MODELS:
            result = ReactToolResult(True, _ACTION_REGISTRATION_CONFIRMATION, {})
        super()._append_react_tool_result(decision, result)

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


def _scene_projection(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 严格读取两层观察投影的 scene 段（提示渲染输入）。"""
    scene = data.get("scene")
    if not isinstance(scene, dict):
        raise RuntimeError("LifeAgent environment observation scene projection is unavailable")
    return scene


def _facts_projection(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 严格读取两层观察投影的 facts 段（提示与工具身份来源）。"""
    facts = data.get("facts")
    if not isinstance(facts, dict):
        raise RuntimeError("LifeAgent environment observation facts projection is unavailable")
    return facts


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


def _experience_text(data: dict[str, Any]) -> str:
    """@brief 已核验经历的句子段（取 facts.experiences，不含任何内部身份）；没有经历时为空串。"""
    sentences = _facts_projection(data).get("experiences")
    if not isinstance(sentences, list) or any(not isinstance(item, str) for item in sentences):
        raise RuntimeError("LifeAgent verified experience sentences are invalid")
    return "已核验经历：\n" + "\n".join(sentences) if sentences else ""


def _conclusion_memories(action: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 把 conclude_activity.memories 按 task 展平为本步写入的记忆；其余世界请求不写记忆。

    来源由系统按写入点固定为 activity_conclusion，认知状态固定为 subjective，模型不填、不选任何身份。
    """
    if action != "conclude_activity":
        return []
    request = _validate_request(ConcludeActivityRequest, args)
    return [{**memory.model_dump(mode="json"), "source": _CONCLUSION_MEMORY_SOURCE,
             "epistemic_status": "subjective"}
            for group in request.memories for memory in group]


def _visible_interactive_objects(data: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """@brief 冻结本轮交互对象层：可读名称 -> 该对象自己的动作短语。

    每个对象只暴露对它自己合法的动作（docs/dev/SPEC-agent-interaction.md
    「对象与动作选择」的绑定粒度裁决），start_activity 的
    action 取值即这些枚举的并集投影，不是一张全局动作表。
    空列表是合法状态（docs/dev/SPEC-time-and-energy.md「仍须修复」第 1 条），
    start_activity 照常可见；缺失或非列表的对象层仍是损坏的宿主观察。
    """
    rows = _facts_projection(data).get("interactive_objects")
    if not isinstance(rows, list):
        raise RuntimeError("LifeAgent interactive objects layer must be a list")
    objects: dict[str, tuple[str, ...]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("LifeAgent interactive object must be an object")
        name, actions = row.get("object"), row.get("actions")
        if not isinstance(name, str) or not name.strip() or name in objects:
            raise RuntimeError("LifeAgent interactive object identity is invalid")
        if (not isinstance(actions, list) or not actions
                or not all(isinstance(item, str) and item.strip() for item in actions)):
            raise RuntimeError("LifeAgent interactive object actions are invalid")
        if not isinstance(row.get("preconditions_met"), bool):
            raise RuntimeError("LifeAgent interactive object preconditions flag is invalid")
        objects[name] = tuple(sorted(set(actions)))
    return objects


def _visible_progress(data: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, str]]:
    """@brief 冻结本轮进度层（facts.progress.sessions）：会话名称与各活动的名称和阶段。

    :returns: (会话名称, 活动名称→阶段标识)；阶段标识取 active、paused、pending、concluded、ended。
    """
    progress = _facts_projection(data).get("progress")
    rows = progress.get("sessions") if isinstance(progress, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("LifeAgent sessions layer must be a list")
    sessions: list[str] = []
    activities: dict[str, str] = {}
    for row in rows:
        name = row.get("name") if isinstance(row, dict) else None
        items = row.get("activities") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name.strip() or name in sessions or not isinstance(items, list):
            raise RuntimeError("LifeAgent session identity is invalid")
        sessions.append(name)
        for item in items:
            label = item.get("name") if isinstance(item, dict) else None
            phase = item.get("phase") if isinstance(item, dict) else None
            if not isinstance(label, str) or not label.strip() or label in activities:
                raise RuntimeError("LifeAgent activity identity is invalid")
            if phase not in _ACTIVITY_PHASES:
                raise RuntimeError("LifeAgent activity phase is invalid")
            activities[label] = _ACTIVITY_PHASES[phase]
    return tuple(sessions), activities


def _visible_resumable(data: dict[str, Any], paused: tuple[str, ...]) -> tuple[str, ...]:
    """@brief 此刻能在开始地点恢复的暂停活动名（私有活动绑定的 resumable 标记；受限轮的恢复枚举只列这些）。"""
    rows = data.get("activity_bindings")
    if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("name"), str)
            or not isinstance(row.get("resumable"), bool) for row in rows):
        raise RuntimeError("LifeAgent activity bindings are invalid")
    here = {row["name"] for row in rows if row["resumable"]}
    return tuple(name for name in paused if name in here)


def _visible_action_contracts(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 冻结当日目标合同、活动控制名称及内容、回看工具的可见名称。"""
    bindings = data["interaction_bindings"]
    tasks = data["task_bindings"]
    goals = data["goal_bindings"]
    sessions, activities = _visible_progress(data)
    by_phase = {phase: tuple(name for name, value in activities.items() if value == phase)
                for phase in ("active", "paused", "pending")}
    return {
        "plan_day": _visible_plan_day_contract(data),
        "revise_day_plan": _visible_revise_plan_contract(data),
        "task_names": tuple(row["task_name"] for row in tasks),
        "recipient_names": tuple(row["object"] for row in bindings
                                 if row["kind"] in {"person", "contact"}),
        "recall_names": tuple([*(row["task_name"] for row in tasks), *(row["task_name"] for row in goals),
                               *activities, *sessions]),
        "reportable_names": by_phase["active"] + by_phase["paused"],
        "resumable_names": _visible_resumable(data, by_phase["paused"]),
        "pausable_names": by_phase["active"],
        "concludable_names": by_phase["active"] + by_phase["paused"] + by_phase["pending"],
        "pending_names": by_phase["pending"],
        "conclude_all_pending": False,
    }


def _visible_same_moment_stage(data: dict[str, Any]) -> SameMomentStage:
    """@brief 读取观察私有证据中的同刻受限轮阶段；闭集之外的取值是损坏的宿主观察。"""
    stage = data.get("same_moment_stage")
    if not isinstance(stage, str) or stage not in {item.value for item in SameMomentStage}:
        logging.error("LifeAgent same-moment stage is invalid: %r", stage)
        raise RuntimeError("LifeAgent same-moment stage is invalid")
    return SameMomentStage(stage)


def _same_moment_objects(stage: SameMomentStage, objects: dict[str, tuple[str, ...]],
                         bindings: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    """@brief 按受限轮阶段收窄交互对象层：start_or_resume 去掉受限轮排除的动作（到店购买与远程下单），
    wait_only 只留等待，不开放 start_activity 的阶段不列对象。

    动作短语到内部标准动作的对应取本轮私有绑定；收窄后没有动作的对象不再列出。
    """
    if stage == SameMomentStage.OPEN:
        return objects
    filters = {SameMomentStage.START_OR_RESUME: lambda action: action not in RESTRICTED_EXCLUDED_ACTIONS,
               SameMomentStage.WAIT_ONLY: lambda action: action == "wait"}
    wanted = filters.get(stage)
    if wanted is None:
        return {}
    canonical = {(row["object"], action["label"]): action["canonical_action"]
                 for row in bindings for action in row["actions"]}
    narrowed = {name: tuple(label for label in labels if wanted(canonical.get((name, label))))
                for name, labels in objects.items()}
    return {name: labels for name, labels in narrowed.items() if labels}


def _same_moment_contracts(stage: SameMomentStage, contracts: dict[str, Any]) -> dict[str, Any]:
    """@brief 受限轮的活动名称枚举（docs/dev/SPEC-time-and-energy.md 第一章规则 3）。

    conclude_pending 只列待收尾活动且须一次点名全部；start_or_resume 只可恢复此刻在开始地点的暂停活动；
    resume_or_conclude 可恢复这些活动或结束占名额的活动；conclude_only 只可结束占名额的活动；wait_only 不开放活动控制。
    名称枚举只列当下可执行的活动（第一章规则 3）。
    """
    if stage == SameMomentStage.OPEN:
        return contracts
    slots = contracts["reportable_names"] + contracts["pending_names"]
    names = {"reportable_names": (), "pausable_names": (), "concludable_names": (), "conclude_all_pending": False}
    if stage == SameMomentStage.CONCLUDE_PENDING:
        names.update(concludable_names=contracts["pending_names"], conclude_all_pending=True)
    elif stage == SameMomentStage.START_OR_RESUME:
        names.update(reportable_names=contracts["resumable_names"])
    elif stage == SameMomentStage.RESUME_OR_CONCLUDE:
        names.update(reportable_names=contracts["resumable_names"], concludable_names=slots)
    elif stage == SameMomentStage.CONCLUDE_ONLY:
        names.update(concludable_names=slots)
    return {**contracts, **names}


def _plan_required_before_other_actions(
    data: dict[str, Any], action_models: dict[str, type[BaseModel]]
) -> bool:
    """@brief 日首唤醒（run_start/wake_from_sleep）且当日无计划时要求先提交计划。

    判据 = plan_day 在本轮动作面开放（即 facts.day_plan 为空）且唤醒原因含
    日首闭集。这是门控协议而非替人物选择：工具集合只表达"此刻先做什么才
    合法"的顺序（与"提交动作后才开放 finish"的阶段门同构，docs/CONTRACTS.md「动作状态与动作结果」工具集合
    不替人物选择指的是不代选内容）；人物仍可提交计划后 finish 跳过当天，
    或被拒绝后在一秒重试边界拿回其他动作工具。
    """
    return ("plan_day" in action_models
            and bool(_DAY_START_WAKE_REASONS.intersection(_visible_wake_reasons(data))))


def _visible_plan_day_contract(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 当日尚未提交时开放目标清单；空清单同样表示已提交。"""
    if _facts_projection(data).get("day_plan") is not None:
        return {}
    return {"progressed_intents": ()}


def _visible_revise_plan_contract(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 任何已提交清单都可修订未推进部分，空清单也允许追加目标。"""
    plan = _facts_projection(data).get("day_plan")
    if plan is None:
        return {}
    return {"progressed_intents": tuple(goal["intent_kind"] for goal in plan["goals"]
                                        if goal["progressed"])}


def _available_action_models(
    contracts: dict[str, Any],
    allowed_tools: frozenset[str] | None,
) -> dict[str, type[BaseModel]]:
    """@brief 稳定工具面的可见性规则（docs/dev/SPEC-agent-interaction.md「稳定工具面」工具表）。

    start_activity 与 recall_task_summary 永恒可见；播报在有进行中或暂停的活动时可见，暂停在有进行中活动时
    可见，结束在有进行中、暂停或待收尾的活动时可见；两件登记动作工具按当日目标合同分别开放。同刻上限的受限轮
    另与该阶段开放的工具求交（allowed_tools 为 None 表示不收窄）。
    """
    names = {"start_activity", "recall_task_summary"}
    if contracts["task_names"]:
        names.add("submit_work")
    if contracts["recipient_names"]:
        names.add("send_message")
    if contracts["reportable_names"]:
        names.add("report_status")
    if contracts["pausable_names"]:
        names.add("pause_activity")
    if contracts["concludable_names"]:
        names.add("conclude_activity")
    names.update(name for name in ("plan_day", "revise_day_plan") if contracts[name])
    if allowed_tools is not None:
        names &= allowed_tools
    return {name: _TOOL_MODELS[name] for name in _TOOL_MODELS if name in names}


#: 活动控制工具 → 其名称枚举所在的合同键。
_CONTROL_NAME_KEYS = {"report_status": "reportable_names", "pause_activity": "pausable_names",
                      "conclude_activity": "concludable_names"}


def _validate_prepared_action(
    action: str,
    request: BaseModel,
    objects: dict[str, tuple[str, ...]],
    contracts: dict[str, Any],
) -> str:
    """@brief 用同一冻结观察核对模型校验与实际分派边界。"""
    if action == "start_activity":
        return _validate_start_activity(request, objects)
    if action in _ACTIVITY_CONTROL_TOOLS:
        if not 1 <= len(request.task) <= 2 or len(set(request.task)) != len(request.task):
            return f"{action} accepts one or two distinct activity names"
        if any(name not in contracts[_CONTROL_NAME_KEYS[action]] for name in request.task):
            return f"{action} task contains an unavailable activity"
        if (action == "conclude_activity" and contracts["conclude_all_pending"]
                and set(request.task) != set(contracts["pending_names"])):
            return "conclude_activity must name every activity that just ended"
        return ""
    if action == "recall_task_summary":
        return "" if request.task_name in contracts["recall_names"] else "task_name is not visible"
    if action == "submit_work":
        return "" if request.task_name in contracts["task_names"] else "task_name is not visible"
    if action == "send_message":
        return "" if request.recipient_name in contracts["recipient_names"] else "recipient_name is not visible"
    if action in {"plan_day", "revise_day_plan"}:
        return _validate_plan_goals(request.goals, contracts[action]["progressed_intents"])
    return ""


def _validate_start_activity(request: BaseModel, objects: dict[str, tuple[str, ...]]) -> str:
    """@brief 对象与动作逐项对应，并取自本轮可见交互对象。"""
    if not 1 <= len(request.targets) <= _START_ACTIVITY_MAX_TARGETS:
        return f"start_activity accepts one or two targets"
    if len(request.targets) != len(request.actions):
        return "start_activity targets and actions must have equal lengths"
    if len(set(request.targets)) != len(request.targets):
        return "start_activity targets must be unique"
    for target, action in zip(request.targets, request.actions):
        actions = objects.get(target)
        if actions is None:
            return "start_activity target is not a visible interactive object"
        if action not in actions:
            return "start_activity action is not offered by the chosen target"
    return ""


def _bind_start_activity_schema(
    schemas: list[dict[str, Any]],
    objects: dict[str, tuple[str, ...]],
    allowed_actions: frozenset[str],
) -> None:
    """@brief 把本轮交互对象身份与菜单允许的动作并集绑定到 start_activity Schema。

    地点菜单只剪裁动作枚举（docs/dev/SPEC-agent-interaction.md「保护项修订」），
    因此绑定值是对象动作并集与菜单允许集合的交集。对象层为空（或交集为空）时
    省略对应的 enum 键，不绑定空枚举；所填名称不在本轮对象中时由本地校验按协议错误
    在本轮重试（docs/dev/SPEC-time-and-energy.md「仍须修复」第 1 条）。
    """
    start = next((item for item in schemas if item["function"]["name"] == "start_activity"), None)
    if start is None:
        return
    union = {value for actions in objects.values() for value in actions}
    properties = start["function"]["parameters"]["properties"]
    targets = sorted(objects)
    actions = sorted(label for label in union if canonical_action(label) in allowed_actions)
    if targets:
        properties["targets"]["items"]["enum"] = targets
    if actions:
        properties["actions"]["items"]["enum"] = actions


def _bind_control_schemas(schemas: list[dict[str, Any]], contracts: dict[str, Any]) -> None:
    """@brief 将本轮可控制的活动名分别绑定到播报、暂停与结束工具的名称枚举。"""
    for item in schemas:
        name = item["function"]["name"]
        if name in _ACTIVITY_CONTROL_TOOLS:
            properties = item["function"]["parameters"]["properties"]
            properties["task"]["items"]["enum"] = sorted(contracts[_CONTROL_NAME_KEYS[name]])


def _bind_named_action_schemas(schemas: list[dict[str, Any]], contracts: dict[str, Any]) -> None:
    """@brief 给工作提交、通讯和回看工具绑定本轮可见的可读名称。"""
    fields = {"submit_work": ("task_name", "task_names"),
              "send_message": ("recipient_name", "recipient_names"),
              "recall_task_summary": ("task_name", "recall_names")}
    for item in schemas:
        name = item["function"]["name"]
        if name in fields and contracts[fields[name][1]]:
            field = fields[name][0]
            item["function"]["parameters"]["properties"][field]["enum"] = sorted(contracts[fields[name][1]])


def _bind_plan_goal_schemas(schemas: list[dict[str, Any]], contracts: dict[str, Any]) -> None:
    """@brief 把尚未推进的意图闭集绑定到两件登记动作工具的目标 Schema。"""
    for item in schemas:
        name = item["function"]["name"]
        if name not in contracts or not contracts[name]:
            continue
        properties = item["function"]["parameters"]["properties"]
        progressed = contracts[name]["progressed_intents"]
        properties["goals"]["items"]["properties"]["intent_kind"]["enum"] = [
            kind for kind in INTENT_KINDS if kind not in progressed]


def _tool_schema(
    name: str,
    model: type[BaseModel],
    descriptions: Mapping[str, str],
) -> dict[str, Any]:
    """@brief 从唯一请求模型与合同拼装描述形成 OpenAI 原生 function Schema。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": descriptions[name],
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


def life_prompt_sha256() -> str:
    """@brief 将身份、自述要求及只读指令全部绑定为稳定模板身份。"""
    templates = {
        "life": _LIFE_INSTRUCTIONS,
        "decision_statement": _DECISION_STATEMENT_INSTRUCTIONS,
        "readonly": _READONLY_INSTRUCTIONS,
    }
    encoded = json.dumps(templates, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def affordance_registry_sha256() -> str:
    """@brief 返回仓库默认可供性注册表文件的 SHA-256（工具面审计指纹输入）。"""
    from worldmock.catalog.affordances import AFFORDANCES_FILE

    root = Path(__file__).resolve().parents[2]
    return hashlib.sha256(
        (root / "catalog" / AFFORDANCES_FILE).read_bytes()).hexdigest()


def life_tool_schema_sha256() -> str:
    """@brief 返回正式生活决策闭集工具 Schema + 注册表摘要的稳定 SHA-256。

    稳定工具面的描述是静态的（每轮绑定的对象身份与动作枚举不进指纹），
    指纹另绑定 affordances.toml 字节摘要，确保内容目录与工具面一起留证。
    """
    schemas = [_tool_schema(name, _TOOL_MODELS[name], _TOOL_DESCRIPTIONS)
               for name in _TOOL_MODELS]
    encoded = json.dumps(
        {"tool_schemas": schemas,
         "affordance_registry_sha256": affordance_registry_sha256()},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
