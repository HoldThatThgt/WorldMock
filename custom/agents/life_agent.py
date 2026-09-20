"""@brief AgentSociety2 原生 LifeAgent，使用闭集生活工具直接调用 LifeEnv。"""

from __future__ import annotations

import json
import hashlib
import logging
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, model_validator

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
from worldmock.life.models import ActionReceipt, REQUEST_MODELS, deepseek_strict_schema
from worldmock.life.narration import (
    DecisionStatementValidationError,
    compose_user_message,
    enforce_budgets,
    render_facts,
    render_scene,
    validate_decision_statement,
)
from worldmock.life.schedule import INTENT_KINDS


#: 决策自述阶段采样参数（DeepSeek 一般对话推荐温度 1.3，两阶段决策合同）。
DECISION_STATEMENT_TEMPERATURE = 1.3
#: 决策自述阶段输出上限（两阶段决策合同：一到三句、≤200 汉字）。
DECISION_STATEMENT_MAX_TOKENS = 400
#: 行动阶段思考开关与工具选择（思考模式拒收 required，真实探针见规格）。
ACTION_THINKING = {"thinking": {"type": "enabled"}}
ACTION_TOOL_CHOICE = "auto"

#: 一笔订单的件数闭区间（docs/dev/SPEC-life-realism.md「商务与进食」：1-5 件，服务端不表达 minItems）。
_ORDER_ITEM_BOUNDS = (1, 5)
#: 一次进食的件数闭区间（docs/dev/SPEC-life-realism.md「商务与进食」：1-3 件）。
_MEAL_ITEM_BOUNDS = (1, 3)
#: 瞬时消息文本上限（docs/dev/SPEC-life-runtime.md「社交最小可行版」：不超过 300 汉字，域内同口径）。
_MESSAGE_TEXT_MAX_CHARS = 300
#: 记忆文本中不允许出现的十六进制身份最小长度（事件/请求哈希防御）。
_HEX_IDENTITY_MIN_CHARS = 8
#: 日首唤醒原因闭集（docs/dev/SPEC-life-realism.md「日程骨架」）：run_start/wake_from_sleep。当天首次
#: 醒来且当日尚无计划时，动作工具面只保留 plan_day（"必须先提交"的强制）；
#: 拒绝重试边界的唤醒原因是 rejection_feedback，不属日首，不触发强制。
_DAY_START_WAKE_REASONS = frozenset({"run_start", "wake_from_sleep"})

_HEX_IDENTITY_PATTERN = re.compile(r"[0-9a-fA-F]{%d,}" % _HEX_IDENTITY_MIN_CHARS)


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


class MemoryReadRequest(_ToolRequest):
    """@brief 按稳定身份读取明确记忆。"""

    ids: list[Annotated[str, Field(pattern=r"\S")]]

    @model_validator(mode="after")
    def validate_ids(self) -> "MemoryReadRequest":
        """@brief 保留服务端不支持表达的身份数量和非空约束。"""
        if not 1 <= len(self.ids) <= 20 or any(not item.strip() for item in self.ids):
            raise ValueError("memory read requires one to twenty nonblank ids")
        return self


class LifeMemoryEpisode(_ToolRequest):
    """@brief finish 工具保存的一条严格人物记忆。"""

    type: Literal[
        "commitment",
        "relationship",
        "preference",
        "goal",
        "conflict",
        "resolution",
        "identity",
        "observation",
        "routine",
        "event",
    ]
    importance: float = Field(ge=0, le=1)
    keywords: list[Annotated[str, Field(pattern=r"\S")]]
    text: str = Field(pattern=r"\S")
    source: str = Field(pattern=r"^decision_observation$")
    refs: list[Annotated[str, Field(pattern=r"\S")]]

    @model_validator(mode="after")
    def validate_local_limits(self) -> "LifeMemoryEpisode":
        """@brief 保留服务端不支持表达的文本、身份与数组数量约束。"""
        if len(self.keywords) > 12:
            raise ValueError("memory episode has more than twelve keywords")
        if len(self.text) > 1000:
            raise ValueError("memory episode text exceeds 1000 characters")
        if _HEX_IDENTITY_PATTERN.search(self.text):
            raise ValueError("memory episode text contains a hexadecimal identifier")
        if not 1 <= len(self.refs) <= 8:
            raise ValueError("memory episode requires one to eight refs")
        return self


class FinishRequest(_ToolRequest):
    """@brief 在一个生活动作后结束当前人物决策并保存记忆。

    记忆合同（docs/dev/SPEC-life-realism.md「记忆合同」）：memories 为 0-8 条，没有值得记录的
    新事件时允许空列表；每条 text 使用人物语言，不得含事件标识或十六进制
    身份（LifeMemoryEpisode 本地校验拒绝）。
    """

    memories: Annotated[
        list[LifeMemoryEpisode],
        WithJsonSchema({
            "type": "array",
            "items": LifeMemoryEpisode.model_json_schema(mode="validation"),
        }),
    ]

    @model_validator(mode="after")
    def validate_memory_count(self) -> "FinishRequest":
        """@brief 保留服务端不支持表达的记忆条目数量约束（允许空列表）。"""
        if not 0 <= len(self.memories) <= 8:
            raise ValueError("finish requires zero to eight memories")
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
    "memory_read": MemoryReadRequest,
}

_TOOL_MODELS: dict[str, type[BaseModel]] = {
    **REQUEST_MODELS,
    **_MEMORY_TOOL_MODELS,
    "finish": FinishRequest,
}

_MEMORY_RUNTIME_ACTIONS = {
    "memory_recent": "memory_recent",
    "memory_search": "memory_search",
    "memory_step_range": "memory_range",
    "memory_tick_range": "memory_range",
    "memory_time_range": "memory_range",
    "memory_read": "memory_read",
}

#: 需要内联嵌套子模型 Schema 的四个工具字段已在 life.models 的字段定义
#: 以 WithJsonSchema 内联（strict 合同禁嵌套引用键，docs/CONTRACTS.md「人物工具与管理接口」）；
#: 本侧直接使用 REQUEST_MODELS 的原始模型，无第二套适配层。


def life_action_tool_names() -> frozenset[str]:
    """@brief 返回 LifeAgent 实际开放的全部生活动作工具名（env 侧核对用）。"""
    return frozenset(_TOOL_MODELS) - frozenset(_MEMORY_TOOL_MODELS) - {"finish"}


#: 阶段规则前缀：一次决策恰好一个生活动作，且必须是响应中唯一调用。
_ACTION_PROTOCOL = (
    "During one decision submit exactly one life action, and it must be the only "
    "tool call in its model response.")

#: 与可供性家族无关的工具文案（静态）；家族相关的三个工具描述
#: （start_basic_activity / start_targeted_activity / browse_activities）的
#: kind 清单自观察合同拼装，见 _life_tool_descriptions。工具协议、引用规则
#: 与阶段规则按提示合同（docs/dev/SPEC-life-runtime.md「系统提示与身份前言」）移入各工具 description。
_TOOL_DESCRIPTIONS = {
    "start_travel": (
        _ACTION_PROTOCOL
        + " Start travel to an observed destination using an observed mode and duration. "
        "At a feedback boundary this switches directly: the current activity pauses "
        "automatically with its progress preserved."),
    "set_activity_state": (
        _ACTION_PROTOCOL
        + " Pause, resume, or cancel an activity exposed by the current observation."),
    "change_playback_speed": (
        _ACTION_PROTOCOL
        + " Change playback speed for the owned active watching activity."),
    "continue_activity": (
        _ACTION_PROTOCOL
        + " Continue the current activity when its visible feedback boundary is due."),
    "purchase": (
        _ACTION_PROTOCOL
        + " Buy one to five visible products from one merchant in a single paid order "
        "with one shared fulfillment; every product_id must pair with its visible "
        "price_version and each quantity must not exceed the visible current stock. "
        "Delivery is not constrained by your current location, but pickup requires you "
        "to be at the merchant's location."),
    "consume": (
        _ACTION_PROTOCOL
        + " Eat one to three held foods in one sitting (the duration equals the longest "
        "item duration) or use exactly one held product with its catalog duration."),
    "submit_work": (
        _ACTION_PROTOCOL
        + " Submit one actual work deliverable for an available task."),
    "game_action": (
        _ACTION_PROTOCOL
        + " Send one admissible command to an active TextWorld game session."),
    "plan_day": (
        _ACTION_PROTOCOL
        + " At the first decision after waking, submit today's skeleton of five to "
        "eight non-overlapping segments covering up to bedtime; every intent_kind "
        "must come from the closed enum in this schema and no segment may start "
        "earlier than the current time."),
    "revise_day_plan": (
        _ACTION_PROTOCOL
        + " Rewrite only the not-yet-started segments of today's submitted plan; a "
        "segment that already started cannot be rewritten."),
    "send_message": (
        _ACTION_PROTOCOL
        + " Send one short message (at most 300 characters) to a recipient identity "
        "visible in this decision's inbox projection; never invent a recipient."),
    "start_social_activity": (
        _ACTION_PROTOCOL
        + " Start a chat with a visible conversation partner as a foreground social "
        "activity; the partner identity must come from this decision's observation."),
    "memory_recent": "Read the person's most recent durable memory episodes.",
    "memory_search": "Search the person's durable memory episodes by keyword.",
    "memory_step_range": "Read durable memory episodes within an inclusive decision-step range.",
    "memory_tick_range": "Read durable memory episodes within an inclusive duration range.",
    "memory_time_range": "Read durable memory episodes within an inclusive ISO time range.",
    "memory_read": "Read durable memory episodes by exact stable identifiers.",
    "finish": (
        "Finish after exactly one submitted life action; finish must be the only tool "
        "call in its response. memories holds zero to eight episodes written in the "
        "person's own words; never put event identifiers, request hashes, or any "
        "hexadecimal identifier inside memory text. Record observed facts and your "
        "intentions only; never claim that the pending action has succeeded. "
        "Every ref must use an allowed "
        "source ID from this schema: the current observation_event_id, current "
        "recent_action_results[].event_id, or a durable episode ID actually shown in "
        "this decision. Never use action_id, decision_id, round_id, request hashes, "
        "tool_call_id, product IDs, activity IDs, or any other identifier as a ref."),
}


def _join_kinds(kinds: list[str]) -> str:
    """@brief 以自然英文枚举拼出 kind 清单（两个用 or，三个以上用牛津逗号）。"""
    if len(kinds) <= 1:
        return ", ".join(kinds)
    if len(kinds) == 2:
        return f"{kinds[0]} or {kinds[1]}"
    return ", ".join(kinds[:-1]) + f", or {kinds[-1]}"


def _life_tool_descriptions(
        contract: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """@brief 模板 + 合同品类拼装家族相关工具描述（kind 清单不硬编码）。

    直名基础活动 = surface basic 且目标不由 browse 揭示；下钻揭示目标按
    工具面拆分（targeted/game surface 的进 start_targeted_activity，
    basic surface 的进 start_basic_activity）。
    """
    descriptions = {name: text for name, text in _TOOL_DESCRIPTIONS.items()}
    basic_direct = [kind for kind, entry in contract.items()
                    if entry["surface"] == "basic" and entry["target_discovery"] == "none"]
    browse_targeted = [kind for kind, entry in contract.items()
                       if entry["target_discovery"] == "browse_result"
                       and entry["surface"] in ("targeted", "game")]
    browse_basic = [kind for kind, entry in contract.items()
                    if entry["target_discovery"] == "browse_result"
                    and entry["surface"] == "basic"]
    descriptions["start_basic_activity"] = (
        f"{_ACTION_PROTOCOL} Start {_join_kinds(basic_direct)} for an explicit duration. "
        "At a feedback boundary this switches directly: the current activity pauses "
        "automatically with its progress preserved.")
    descriptions["start_targeted_activity"] = (
        f"{_ACTION_PROTOCOL} Start one of "
        f"{_join_kinds(list(_targeted_activity_kinds(contract)))} using an exact "
        "target from the current observation catalogs. At a feedback boundary this "
        "switches directly: the current activity pauses automatically with its "
        "progress preserved.")
    descriptions["browse_activities"] = (
        "Drill into one activity category from activity_summary and read its item list. "
        "Pure query: it starts nothing and consumes nothing; the returned items arrive "
        f"as action results and become usable targets ({_join_kinds(browse_targeted)} items "
        f"for start_targeted_activity, {_join_kinds(browse_basic)} items for "
        "start_basic_activity).")
    return descriptions


#: 观察合同（affordance_contract）条目的七键闭集：每个 kind 的
#: {surface, target_discovery, target_field, energy_projection,
#: feedback_eligible, continuable, game_boundary} 冻结形状（引擎随场景组装
#: 一次性生成；本侧严格校验同一形状）。
_CONTRACT_KEYS = frozenset({
    "surface", "target_discovery", "target_field", "energy_projection",
    "feedback_eligible", "continuable", "game_boundary"})


def _affordance_contract(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """@brief 严格读取环境观察中的冻结可供性合同（L4 工具面单一事实源）。"""
    contract = data.get("affordance_contract")
    if not isinstance(contract, dict) or not contract:
        raise RuntimeError("LifeAgent affordance contract is unavailable")
    for kind, entry in contract.items():
        if not isinstance(kind, str) or not kind.strip() or not isinstance(entry, dict):
            raise RuntimeError("LifeAgent affordance contract entry is invalid")
        if set(entry) != _CONTRACT_KEYS:
            raise RuntimeError("LifeAgent affordance contract keys are invalid")
        if not isinstance(entry["surface"], str) or not entry["surface"].strip():
            raise RuntimeError("LifeAgent affordance contract surface is invalid")
        if not isinstance(entry["target_discovery"], str):
            raise RuntimeError("LifeAgent affordance contract target discovery is invalid")
        target_field = entry["target_field"]
        if target_field is not None and (
                not isinstance(target_field, list) or len(target_field) != 2
                or not isinstance(target_field[0], str) or not target_field[0].strip()
                or not (target_field[1] is None or (
                    isinstance(target_field[1], str) and target_field[1].strip()))):
            raise RuntimeError("LifeAgent affordance contract target field is invalid")
        for key in ("energy_projection", "feedback_eligible", "continuable", "game_boundary"):
            if not isinstance(entry[key], bool):
                raise RuntimeError(f"LifeAgent affordance contract {key} is invalid")
    return contract


def _targeted_activity_kinds(
        contract: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
    """@brief start_targeted_activity 的 activity_type 枚举序（合同声明序）。"""
    return tuple(kind for kind, entry in contract.items()
                 if entry["surface"] in ("targeted", "game"))


def _target_catalog_fields(
        contract: Mapping[str, Mapping[str, Any]]) -> dict[str, tuple[str, str | None]]:
    """@brief 观察目录直读字段映射（合同 target_discovery = catalog_field）。"""
    return {kind: (entry["target_field"][0], entry["target_field"][1])
            for kind, entry in contract.items()
            if entry["target_discovery"] == "catalog_field"}


def _energy_limit_kinds(
        contract: Mapping[str, Mapping[str, Any]]) -> frozenset[str]:
    """@brief 能量可行性投影闭集（合同 energy_projection = true）。"""
    return frozenset(kind for kind, entry in contract.items()
                     if entry["energy_projection"])


def _continuable_activity_kinds(
        contract: Mapping[str, Mapping[str, Any]]) -> frozenset[str]:
    """@brief continue_activity 资格闭集（合同 continuable 键）。"""
    return frozenset(kind for kind, entry in contract.items() if entry["continuable"])


def _game_boundary_kinds(
        contract: Mapping[str, Mapping[str, Any]]) -> frozenset[str]:
    """@brief 游戏反馈边界闭集（合同 game_boundary；走 game_action 继续交互）。"""
    return frozenset(kind for kind, entry in contract.items() if entry["game_boundary"])


_TARGET_KIND_DESCRIPTION = (
    "Choose only a kind with a currently visible target: work uses tasks[].task_id; "
    "watch uses item_id values revealed by a recent browse_activities result or listed in "
    "content_progress; play_game uses the game IDs of the currently available games; "
    "read_mail uses the id of a facts.inbox entry whose kind is mail; browse uses browse_targets[]."
)

_TARGET_ID_DESCRIPTION = (
    "Use the exact visible ID paired with activity_type: work = tasks[].task_id; "
    "watch = item_id from recent browse_activities results or content_progress; "
    "play_game = a game ID of the currently available games; "
    "read_mail = the id of a facts.inbox entry whose kind is mail; "
    "browse = browse_targets[]."
)

_ACTIVITY_CONTROL_TOOLS = frozenset({
    "set_activity_state",
    "change_playback_speed",
    "continue_activity",
    "game_action",
})

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
        """@brief 按只读、动作前和动作后阶段生成当前闭集工具 Schema。"""
        budget = self._react_budget(readonly)
        models = self._budgeted_tool_models(readonly, budget["memory_tools_available"])
        self._life_request_models = models
        descriptions = _life_tool_descriptions(getattr(self, "_life_contract", None) or {})
        schemas = [
            _tool_schema(
                name,
                model,
                descriptions,
            )
            for name, model in models.items()
        ]
        if not readonly and not getattr(self, "_life_action_count", 0):
            if not hasattr(self, "_life_visible_targets"):
                raise RuntimeError("LifeAgent visible target catalogs are not prepared")
            life_menu.bind_basic_activity_schema(schemas, self._life_basic_activity_types)
            _bind_targeted_activity_catalogs(
                schemas,
                self._life_targeted_kinds,
                self._life_visible_targets,
                self._life_activity_controls["maximum_duration_seconds_by_kind"],
            )
            _bind_browse_categories(schemas, self._life_browse_categories)
            _bind_activity_control_schemas(schemas, self._life_activity_controls)
            _bind_visible_action_schemas(schemas, self._life_action_contracts)
        if not readonly and "finish" in models:
            _bind_finish_reference_ids(
                schemas,
                getattr(self, "_life_visible_refs", set()),
            )
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
        enforce_budgets(0, len(first["content"]))
        request = super()._model_request([first, *messages[1:]], tools)
        request["tool_choice"] = ACTION_TOOL_CHOICE
        request["extra_body"] = dict(ACTION_THINKING)
        return request

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
        """@brief 从本决策实际模型调用序号取得当前请求预算。"""
        phase = "readonly" if readonly else (
            "after_action" if getattr(self, "_life_action_count", 0) else "before_action")
        required = 1 if phase in {"readonly", "after_action"} else 2
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
        if getattr(self, "_life_action_count", 0):
            return {**memory, "finish": FinishRequest}
        if not hasattr(self, "_life_action_models"):
            raise RuntimeError("LifeAgent activity controls are not prepared")
        return {**self._life_action_models, **memory}

    def _persona_card_text(self) -> str:
        """@brief 从公开人物档案字段确定性渲染提示用人物卡片。"""
        return persona_card_text(self.get_profile(), self.name)

    def _memory_prompt_section(self) -> str:
        """@brief 渲染持久记忆摘要段并冻结其可见记忆身份。"""
        if not self._enable_memory:
            return ""
        memory = self._build_memory_context()
        self._life_visible_refs.update(
            _episode_ids(memory.get("recent_episodes", []))
        )
        parts = []
        summary = memory.get("memory_md")
        if isinstance(summary, str) and summary.strip():
            parts.append(summary.strip())
        episodes = memory.get("recent_episodes") or []
        lines = [f"- {item.get('text', '')}".rstrip()
                 for item in episodes if isinstance(item, dict)]
        if lines:
            parts.append("最近的记忆：\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def _life_user_sections(self, request: ReactLoopRequest, *, with_facts: bool) -> str:
        """@brief 组装三段 user 消息文本（persona + scene + 记忆 [+ facts + 决策自述]）。

        决策自述阶段只取 persona + scene + 记忆摘要；行动阶段再追加 facts 与
        "你刚才心里想的是"（docs/dev/SPEC-life-realism.md「两阶段决策」）。scene/facts 由
        narration 渲染器执行黑名单校验；观察不再序列化 JSON 副本。
        """
        data = _environment_observation_data(request.observations)
        persona = self._persona_card_text()
        scene = render_scene(_scene_projection(data))
        memory = self._memory_prompt_section()
        core = compose_user_message(persona, scene, memory)
        if not with_facts:
            return core
        facts = render_facts(_facts_projection(data))
        parts = [core, facts]
        decision_statement = getattr(self, "_life_decision_statement", None)
        if decision_statement:
            parts.append(f"你刚才心里想的是：{decision_statement}")
        user = "\n\n".join(parts)
        enforce_budgets(len(user), 0)
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
        else:
            user = self._life_user_sections(request, with_facts=True)
        enforce_budgets(len(user), len(system))
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def prelude_decision_statement_request(self, request: ReactLoopRequest) -> dict[str, Any] | None:
        """@brief 两阶段决策的决策自述请求参数；只读决策不启用决策自述。

        决策自述 = system 身份前言 + persona_card + scene + 记忆摘要，不带
        tools；thinking=disabled、temperature=1.3、max_tokens=400。
        """
        if request.readonly:
            return None
        if self._model_name is None:
            self._logger.error("Decision statement model is not initialized")
            raise RuntimeError("LLM is not initialized")
        system = (_LIFE_INSTRUCTIONS.format(name=self.name or "this person")
                  + _DECISION_STATEMENT_INSTRUCTIONS)
        user = self._life_user_sections(request, with_facts=False)
        enforce_budgets(len(user), len(system))
        return {
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
            self._life_require_turns("decision_statement", 3)
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
        """@brief 重置当前决策动作计数并冻结可见来源事件。"""
        super().prepare_react_loop(request)
        self._life_action_count = 0
        self._life_readonly = request.readonly
        self._life_request_models = None
        self._life_decision_statement = None
        if request.readonly:
            self._life_interaction_menu = None
            self._life_basic_activity_types = ()
            self._life_visible_refs = set()
            self._life_visible_targets = {}
            self._life_activity_controls = {}
            self._life_action_contracts = {}
            self._life_action_models = {}
            self._life_browse_categories = frozenset()
            self._life_contract = {}
            self._life_targeted_kinds = ()
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
        """@brief 从一次冻结观察组装菜单、可见身份、控制和生活动作工具。"""
        self._life_interaction_menu = menu = life_menu.parse_interaction_menu(data)
        self._life_contract = contract = _affordance_contract(data)
        self._life_targeted_kinds = life_menu.filter_targeted_kinds(
            menu, _targeted_activity_kinds(contract))
        energy_limits = _visible_energy_limits(data, contract)
        self._life_wake_reasons = frozenset(_visible_wake_reasons(data))
        self._life_visible_refs = _environment_source_ids(data)
        self._life_basic_activity_types = life_menu.basic_activity_types(menu, data)
        self._life_browse_categories = life_menu.filter_browse_categories(
            menu, _visible_browse_categories(data))
        self._life_visible_targets = life_menu.filter_targets(
            menu, _visible_target_ids(data, contract, energy_limits))
        self._life_activity_controls = life_menu.filter_activity_controls(
            menu, _visible_activity_controls(data, contract, energy_limits))
        self._life_action_contracts = life_menu.filter_action_contracts(
            menu, _visible_action_contracts(data))
        self._life_action_models = _available_action_models(
            self._life_visible_targets, self._life_activity_controls, self._life_action_contracts,
            self._life_browse_categories)
        self._life_action_models = life_menu.filter_action_models(
            menu, self._life_action_models)

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
                    decision.action,
                    validated,
                    self._life_visible_targets,
                    self._life_activity_controls,
                    self._life_action_contracts,
                    self._life_browse_categories,
                )
                if error:
                    return error
                if decision.action in REQUEST_MODELS:
                    error = life_menu.menu_action_error(
                        self._life_interaction_menu, decision.action, validated,
                        self._life_activity_controls, self._life_basic_activity_types)
                    if error:
                        return error
                if decision.action == "finish" and not readonly:
                    error = self._validate_finish(validated)
                    if error:
                        return error
            except Exception as exc:
                return f"invalid {decision.action} arguments: {exc}"
        return ""

    def _validate_finish(self, request: BaseModel) -> str:
        """@brief 要求动作已提交且全部记忆引用存在。"""
        if self._life_action_count != 1:
            return "finish requires exactly one submitted life action"
        refs = {ref for memory in request.memories for ref in memory.refs}
        unresolved = refs - self._life_visible_refs
        return "" if not unresolved else "finish contains an unknown memory reference"

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
        error = _validate_prepared_action(
            action,
            request,
            self._life_visible_targets,
            self._life_activity_controls,
            self._life_action_contracts,
            self._life_browse_categories,
        )
        if error:
            raise RuntimeError(error)
        error = life_menu.menu_action_error(
            self._life_interaction_menu, action, request,
            self._life_activity_controls, self._life_basic_activity_types)
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
        """@brief 验证记忆请求并拒绝缺失的明确引用。"""
        model = _TOOL_MODELS[action]
        request = _validate_request(model, args)
        payload = request.model_dump(mode="json", exclude_none=True)
        runtime_action = _MEMORY_RUNTIME_ACTIONS[action]
        ok, observation, data = self._memory_runtime.dispatch_tool(runtime_action, payload)
        if not ok:
            raise RuntimeError(observation)
        if action == "memory_read":
            requested = set(request.ids)
            found = {str(item["id"]) for item in data["episodes"]}
            if found != requested:
                raise RuntimeError("Memory reference does not exist")
        self._life_visible_refs.update(_episode_ids(data.get("episodes", [])))
        return ReactToolResult(True, observation, data)


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
    """@brief 读取观察顶层的模拟时钟（UTC epoch 秒，计划工具的本地基准）。"""
    now = data.get("current_time")
    if isinstance(now, bool) or not isinstance(now, int) or now < 0:
        raise RuntimeError("LifeAgent observation current time is invalid")
    return now


def _visible_wake_reasons(data: dict[str, Any]) -> tuple[str, ...]:
    """@brief 冻结本轮观察携带的唤醒原因闭集（引擎 schedule 信号）。"""
    reasons = data.get("wake_reasons")
    if not isinstance(reasons, list) or any(
            not isinstance(item, str) or not item.strip() for item in reasons):
        raise RuntimeError("LifeAgent wake reasons are invalid")
    return tuple(reasons)


def _environment_source_ids(data: dict[str, Any]) -> set[str]:
    """@brief 从当前结构化观察冻结人物实际看见的环境事件身份。"""
    observation_id = data.get("observation_event_id")
    if not isinstance(observation_id, str) or not observation_id:
        raise RuntimeError("LifeAgent observation event identity is unavailable")
    result_ids = []
    results = data.get("recent_action_results", [])
    if not isinstance(results, list):
        raise RuntimeError("LifeAgent recent action results are invalid")
    for result in results:
        event_id = result.get("event_id") if isinstance(result, dict) else None
        if not isinstance(event_id, str) or not event_id:
            raise RuntimeError("LifeAgent action result event identity is invalid")
        result_ids.append(event_id)
    return {observation_id, *result_ids}


def _visible_energy_limits(
    data: dict[str, Any],
    contract: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    """@brief 严格读取环境以权威成本函数计算的能量投影活动墙钟上限。

    键集对照观察合同的 energy_projection 闭集（与引擎投影器同一份合同，
    生产者与校验者共享单一事实源）。
    """
    projection = data.get("energy_feasibility")
    maximums = projection.get("maximum_duration_seconds_by_kind") if isinstance(projection, dict) else None
    if not isinstance(maximums, dict) or set(maximums) != _energy_limit_kinds(contract):
        raise RuntimeError("LifeAgent energy feasibility kinds are invalid")
    for kind, value in maximums.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 86_400:
            raise RuntimeError(f"LifeAgent {kind} energy duration limit is invalid")
    return dict(maximums)


def _visible_target_ids(
    data: dict[str, Any],
    contract: Mapping[str, Mapping[str, Any]],
    energy_limits: dict[str, int],
) -> dict[str, tuple[str, ...]]:
    """@brief 从公开目录、browse 结果与进度冻结本轮可用活动目标身份。

    观察目录直读的字段映射自合同派生（target_discovery = catalog_field 的
    kinds；watch 目标由 browse_activities 结果与观看进度单独冻结）。
    """
    result = {}
    for kind, (field, identity_field) in _target_catalog_fields(contract).items():
        catalog = data.get(field)
        if not isinstance(catalog, list):
            raise RuntimeError(f"LifeAgent {field} catalog must be a list")
        result[kind] = _catalog_identity_values(catalog, field, identity_field)
    result["work"] = _visible_work_target_ids(data, result["work"])
    result["watch"] = _visible_watch_target_ids(data)
    for kind, maximum in energy_limits.items():
        if maximum < 60:
            result[kind] = ()
    return result


def _visible_work_target_ids(data: dict[str, Any], visible_ids: tuple[str, ...]) -> tuple[str, ...]:
    """@brief 只保留合同剩余时间可容纳最短活动的工作任务。"""
    allowed = []
    for task in data["tasks"]:
        task_id = task.get("task_id") if isinstance(task, dict) else None
        remaining = task.get("remaining_contract_seconds") if isinstance(task, dict) else None
        if (task_id not in visible_ids or isinstance(remaining, bool)
                or not isinstance(remaining, int) or remaining < 0):
            raise RuntimeError("LifeAgent task remaining contract time is invalid")
        if remaining >= 60:
            allowed.append(task_id)
    return tuple(sorted(set(allowed)))


def _visible_watch_target_ids(data: dict[str, Any]) -> tuple[str, ...]:
    """@brief 只保留 browse 结果或观看进度中可播放至少三十秒的内容目标。

    观察不再携带全量 content_catalog：观看目标闭集来自（一）上次观察后的
    browse_activities 内容品类结果（action_result），（二）content_progress
    中已开始的条目（时长 = 位置 + 剩余，保证跨轮续看不需要重新下钻）。
    """
    durations = _browsed_content_durations(data.get("recent_action_results"))
    progress = _visible_content_progress(data.get("content_progress"), durations)
    available = _visible_content_available(progress, durations)
    return tuple(sorted(item_id for item_id, seconds in available.items() if seconds >= 30))


def _browsed_content_durations(results: Any) -> dict[str, int]:
    """@brief 从近期 browse_activities 动作结果提取内容身份与总时长。"""
    if not isinstance(results, list):
        raise RuntimeError("LifeAgent recent action results are invalid")
    result: dict[str, int] = {}
    for entry in results:
        payload = entry.get("result") if isinstance(entry, dict) else None
        if not isinstance(payload, dict) or payload.get("status") != "completed":
            continue
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("tool") != "browse_activities":
            continue
        items = data.get("items")
        if not isinstance(items, list):
            raise RuntimeError("LifeAgent browsed activity items must be a list")
        for item in items:
            if not isinstance(item, dict) or "item_id" not in item:
                continue  # 生活活动/食品条目不是观看目标
            item_id = item.get("item_id")
            duration = item.get("duration_seconds")
            if (not isinstance(item_id, str) or not item_id.strip()
                    or isinstance(duration, bool) or not isinstance(duration, int)
                    or duration <= 0):
                raise RuntimeError("LifeAgent browsed content duration is invalid")
            if item_id in result and result[item_id] != duration:
                raise RuntimeError("LifeAgent browsed content duration is ambiguous")
            result[item_id] = duration
    return result


def _visible_content_progress(progress: Any, durations: dict[str, int]) -> dict[str, dict[str, Any]]:
    """@brief 校验人物观看进度，并为无 browse 结果的条目推导总时长。"""
    if not isinstance(progress, dict):
        raise RuntimeError("LifeAgent content_progress must be an object")
    for item_id, item in progress.items():
        if not isinstance(item, dict) or item.get("item_id") != item_id:
            raise RuntimeError("LifeAgent content progress identity is invalid")
        position = item.get("position_seconds")
        remaining = item.get("remaining_media_seconds")
        exposure = item.get("exposure_count")
        values = (position, remaining)
        if (any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values)
                or not isinstance(exposure, int) or isinstance(exposure, bool) or exposure < 0):
            raise RuntimeError("LifeAgent content progress value is invalid")
        if position < 0 or remaining < 0:
            raise RuntimeError("LifeAgent content progress time is invalid")
        if item_id not in durations:
            durations[item_id] = int(float(position) + float(remaining))
    return progress


def _visible_content_available(progress: dict[str, dict[str, Any]],
                               durations: dict[str, int]) -> dict[str, float]:
    """@brief 返回每个已知内容目标的下次播放可用媒体秒数。"""
    available = {item_id: float(duration) for item_id, duration in durations.items()}
    for item_id, item in progress.items():
        position = float(item["position_seconds"])
        expected = durations[item_id] - position
        if expected < 0 or abs(float(item["remaining_media_seconds"]) - expected) > 1e-6:
            raise RuntimeError("LifeAgent content remaining time is invalid")
        available[item_id] = float(durations[item_id]) if expected == 0 else expected
    return available


def _visible_activity_controls(
    data: dict[str, Any],
    contract: Mapping[str, Mapping[str, Any]],
    energy_limits: dict[str, int],
) -> dict[str, Any]:
    """@brief 从当前活动、暂停活动与游戏局面冻结本轮控制合同。"""
    active = _visible_active_activity(data, contract)
    paused_ids, resumable_ids, paused_kinds = _visible_paused_activity_ids(data)
    if active is not None and active["activity_id"] in paused_ids:
        raise RuntimeError("LifeAgent active activity is also listed as paused")
    controls = {"active": active, "paused_ids": paused_ids, "resumable_paused_ids": resumable_ids,
                "menu_resumable_paused_ids": resumable_ids,
                "paused_activity_kinds": paused_kinds,
                "maximum_duration_seconds_by_kind": energy_limits,
                "game_session_id": None, "game_commands": ()}
    if active is not None and active["kind"] == "play_game":
        session_id, commands = _visible_game_control(data)
        controls.update(game_session_id=session_id, game_commands=commands)
    return controls


def _visible_active_activity(
    data: dict[str, Any],
    contract: Mapping[str, Mapping[str, Any]],
) -> dict[str, str] | None:
    """@brief 校验环境提供的当前活动身份、类型和活动状态。"""
    if "active_activity" not in data:
        raise RuntimeError("LifeAgent active_activity is unavailable")
    activity = data["active_activity"]
    if activity is None:
        return None
    if not isinstance(activity, dict):
        raise RuntimeError("LifeAgent active_activity must be an object or null")
    activity_id = activity.get("activity_id")
    kind = activity.get("kind")
    if not isinstance(activity_id, str) or not activity_id.strip():
        raise RuntimeError("LifeAgent active activity identity is invalid")
    if activity.get("status") != "active" or not isinstance(kind, str):
        raise RuntimeError("LifeAgent active activity state is invalid")
    if kind not in _continuable_activity_kinds(contract) | _game_boundary_kinds(contract):
        raise RuntimeError("LifeAgent active activity kind has no decision boundary controls")
    return {"activity_id": activity_id, "kind": kind}


def _visible_paused_activity_ids(
    data: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, str]]:
    """@brief 校验暂停活动身份，并区分当前能量允许恢复的闭集。"""
    activities = data.get("paused_activities")
    if not isinstance(activities, list):
        raise RuntimeError("LifeAgent paused_activities must be a list")
    identities = []
    resumable = []
    kinds = {}
    for activity in activities:
        if not isinstance(activity, dict) or activity.get("status") != "paused":
            raise RuntimeError("LifeAgent paused activity state is invalid")
        activity_id = activity.get("activity_id")
        if not isinstance(activity_id, str) or not activity_id.strip():
            raise RuntimeError("LifeAgent paused activity identity is invalid")
        kind = activity.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            logging.error("LifeAgent paused activity kind is invalid")
            raise RuntimeError("LifeAgent paused activity kind is invalid")
        remaining = activity.get("remaining_duration_seconds")
        cost = activity.get("minimum_energy_cost_kcal")
        feasible = activity.get("resume_energy_feasible")
        if (isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 0
                or isinstance(cost, bool) or not isinstance(cost, (int, float))
                or not math.isfinite(cost) or cost < 0 or not isinstance(feasible, bool)):
            raise RuntimeError("LifeAgent paused activity energy feasibility is invalid")
        identities.append(activity_id)
        kinds[activity_id] = kind
        if feasible:
            resumable.append(activity_id)
    if len(identities) != len(set(identities)):
        raise RuntimeError("LifeAgent paused activity identities are duplicated")
    return tuple(sorted(identities)), tuple(sorted(resumable)), kinds


def _visible_game_control(data: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """@brief 取得当前游戏活动的真实会话和可接受命令闭集。"""
    activity = data["active_activity"]
    activity_data = activity.get("data") if isinstance(activity, dict) else None
    session_id = activity_data.get("game_session_id") if isinstance(activity_data, dict) else None
    games = data.get("games")
    if not isinstance(session_id, str) or not session_id or not isinstance(games, dict):
        raise RuntimeError("LifeAgent active game session is invalid")
    progress = games.get(session_id)
    if not isinstance(progress, dict) or progress.get("session_id") != session_id:
        raise RuntimeError("LifeAgent active game progress is unavailable")
    if progress.get("won") is not False or progress.get("lost") is not False:
        raise RuntimeError("LifeAgent active game progress is terminal")
    commands = progress.get("admissible_commands")
    if not isinstance(commands, list):
        raise RuntimeError("LifeAgent active game commands must be a list")
    values = _catalog_identity_values(commands, "game commands", None)
    return session_id, values


def _visible_action_contracts(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 冻结瞬时动作工具所需的公开身份配对（含修订合同四件新工具）。"""
    products = _visible_products(data)
    locations = _visible_location_ids(data)
    available = _visible_available_inventory(data, products)
    location_id = data.get("location_id")
    if not isinstance(location_id, str) or not location_id:
        raise RuntimeError("LifeAgent current location is invalid")
    return {
        "purchase": {
            key: {**value, "destination_ids": locations,
                  "pickup_allowed": value["merchant_location_id"] == location_id}
            for key, value in products.items() if value["current_stock"] > 0
        },
        "consume": available,
        "submit_work": _visible_submittable_tasks(data),
        "start_travel": _visible_routes(data),
        "plan_day": _visible_plan_day_contract(data),
        "revise_day_plan": _visible_revise_plan_contract(data),
        "send_message": _visible_message_recipients(data),
        "start_social_activity": _visible_social_partners(data),
    }


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
    """@brief plan_day 在当日尚无计划时开放（docs/dev/SPEC-life-realism.md「日程骨架」；跳过首醒者当天可补交）。

    可见性判据只有"当日尚无计划"（facts.day_plan 为空，观察与跨日归零/就寝
    隐藏同口径）；不限唤醒原因——被引擎拒绝后的一秒重试边界与当天中途的
    补交决策同样开放。日首无计划时的动作面强制见 prepare_react_loop。
    """
    if _facts_projection(data).get("day_plan") is not None:
        return {}
    return {"now": _observation_now(data)}


def _visible_revise_plan_contract(data: dict[str, Any]) -> dict[str, Any]:
    """@brief revise_day_plan 只在存在尚未开始的计划段时开放（docs/dev/SPEC-life-realism.md「日程骨架」）。"""
    plan = _facts_projection(data).get("day_plan")
    if plan is None:
        return {}
    if not isinstance(plan, dict) or not isinstance(plan.get("segments"), list):
        raise RuntimeError("LifeAgent day plan segments are invalid")
    now = _observation_now(data)
    for segment in plan["segments"]:
        start = segment.get("start") if isinstance(segment, dict) else None
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise RuntimeError("LifeAgent day plan segment start is invalid")
    future = [item for item in plan["segments"] if item["start"] > now]
    return {"now": now} if future else {}


def _visible_people_ids(facts: dict[str, Any]) -> tuple[str, ...]:
    """@brief 冻结本轮在场人物的不透明身份闭集（facts.people_here 的 person_id）。"""
    people = facts.get("people_here")
    if not isinstance(people, list):
        raise RuntimeError("LifeAgent people projection is invalid")
    identities = []
    for row in people:
        if not isinstance(row, dict):
            raise RuntimeError("LifeAgent people projection row must be an object")
        person_id = row.get("person_id")
        if not isinstance(person_id, str) or not person_id.strip():
            raise RuntimeError("LifeAgent person identity is invalid")
        identities.append(person_id)
    return tuple(sorted(set(identities)))


def _visible_contact_ids(facts: dict[str, Any]) -> tuple[str, ...]:
    """@brief 冻结本轮场景联系人的不透明身份闭集（facts.contacts 的 contact_id）。"""
    contacts = facts.get("contacts", [])
    if contacts is None:
        contacts = []
    if not isinstance(contacts, list):
        raise RuntimeError("LifeAgent contacts projection is invalid")
    identities = []
    for row in contacts:
        if not isinstance(row, dict):
            raise RuntimeError("LifeAgent contacts projection row must be an object")
        contact_id = row.get("contact_id")
        if not isinstance(contact_id, str) or not contact_id.strip():
            raise RuntimeError("LifeAgent contact identity is invalid")
        identities.append(contact_id)
    return tuple(sorted(set(identities)))


def _visible_inbox_senders(facts: dict[str, Any]) -> tuple[str, ...]:
    """@brief 冻结本轮收件箱投影的发送者身份闭集。"""
    inbox = facts.get("inbox")
    if not isinstance(inbox, list):
        raise RuntimeError("LifeAgent inbox projection is invalid")
    senders = set()
    for row in inbox:
        if not isinstance(row, dict):
            raise RuntimeError("LifeAgent inbox row must be an object")
        sender = row.get("sender_id")
        if sender is None:
            continue
        if not isinstance(sender, str) or not sender.strip():
            raise RuntimeError("LifeAgent inbox sender identity is invalid")
        senders.add(sender)
    return tuple(sorted(senders))


def _visible_message_recipients(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 冻结本轮可见的消息收件人身份闭集（docs/CONTRACTS.md「人物工具与管理接口」身份枚举用本轮观察）。

    收件人 = 在场人物（people_here 的不透明 person_id）∪ 场景联系人
    （contacts 的 contact_id）∪ 已向你发过消息的人（inbox 发送者）；
    三者都是本轮观察实际可见的身份，无可见收件人时撤下工具。
    """
    facts = _facts_projection(data)
    recipients = (set(_visible_people_ids(facts)) | set(_visible_contact_ids(facts))
                  | set(_visible_inbox_senders(facts)))
    return {"recipients": tuple(sorted(recipients))} if recipients else {}


def _visible_social_partners(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 冻结本轮可见的交谈与通话对象身份闭集（docs/dev/SPEC-life-runtime.md「社交最小可行版」）。

    chat 对象 = 在场人物 ∪ 收件箱发送者（共同在场与可交谈由环境结算
    边界校验）；call 对象 = 场景联系人。两个闭集都为空时撤下工具。
    """
    facts = _facts_projection(data)
    chat = tuple(sorted(set(_visible_people_ids(facts))
                        | set(_visible_inbox_senders(facts))))
    call = _visible_contact_ids(facts)
    return {"chat_partners": chat, "call_partners": call} if chat or call else {}


def _visible_products(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """@brief 校验已浏览商品的当前库存、报价和使用合同。"""
    catalog = data.get("products")
    if not isinstance(catalog, list):
        raise RuntimeError("LifeAgent products catalog must be a list")
    result = {}
    for item in catalog:
        if not isinstance(item, dict):
            raise RuntimeError("LifeAgent products catalog item must be an object")
        product_id = item.get("product_id")
        price_version = item.get("price_version")
        stock = item.get("current_stock")
        modes = item.get("use_modes")
        duration = item.get("use_duration_seconds")
        merchant = item.get("merchant_location_id")
        required = item.get("required_location_id")
        if (not isinstance(product_id, str) or not product_id.strip()
                or not isinstance(price_version, str) or not price_version.strip()):
            raise RuntimeError("LifeAgent product identity or price version is invalid")
        if isinstance(stock, bool) or not isinstance(stock, int) or stock < 0:
            raise RuntimeError("LifeAgent product current stock is invalid")
        if not isinstance(modes, list) or not modes or not all(isinstance(value, str) for value in modes):
            raise RuntimeError("LifeAgent product use modes are invalid")
        if not isinstance(duration, int) or duration <= 0 or not isinstance(merchant, str):
            raise RuntimeError("LifeAgent product use contract is invalid")
        if required is not None and not isinstance(required, str):
            raise RuntimeError("LifeAgent product required location is invalid")
        if product_id in result:
            raise RuntimeError("LifeAgent product identities are duplicated")
        result[product_id] = {"price_version": price_version, "current_stock": stock,
                              "use_modes": tuple(sorted(set(modes))), "duration_seconds": duration,
                              "merchant_location_id": merchant, "required_location_id": required}
    return result


def _visible_location_ids(data: dict[str, Any]) -> tuple[str, ...]:
    """@brief 从公开地点目录取得购买目的地闭集。"""
    locations = data.get("locations")
    if not isinstance(locations, list):
        raise RuntimeError("LifeAgent locations catalog must be a list")
    return _catalog_identity_values(locations, "locations", "location_id")


def _visible_available_inventory(
    data: dict[str, Any],
    products: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """@brief 仅保留人物当前位置数量为正且地点要求满足的商品（含持有量）。"""
    inventory = data.get("available_inventory")
    location_id = data.get("location_id")
    if not isinstance(inventory, dict) or not isinstance(location_id, str):
        raise RuntimeError("LifeAgent available inventory or location is invalid")
    result = {}
    for product_id, quantity in inventory.items():
        if not isinstance(product_id, str) or isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            raise RuntimeError("LifeAgent available inventory entry is invalid")
        if quantity <= 0:
            continue
        product = products.get(product_id)
        if product is None:
            raise RuntimeError("LifeAgent held product contract is unavailable")
        if product["required_location_id"] not in {None, location_id}:
            continue
        if product["duration_seconds"] < 60:
            continue
        result[product_id] = {"use_modes": product["use_modes"],
                              "duration_seconds": product["duration_seconds"],
                              "held_quantity": quantity}
    return result


def _visible_submittable_tasks(data: dict[str, Any]) -> tuple[str, ...]:
    """@brief 只保留正文已经按实际工作完整揭示的公开任务。"""
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        raise RuntimeError("LifeAgent tasks catalog must be a list")
    identities = []
    for task in tasks:
        material = task.get("material") if isinstance(task, dict) else None
        task_id = task.get("task_id") if isinstance(task, dict) else None
        if not isinstance(task_id, str) or not isinstance(material, dict):
            raise RuntimeError("LifeAgent task submission contract is invalid")
        end = material.get("end_char")
        total = material.get("total_chars")
        if not isinstance(end, int) or not isinstance(total, int) or end < 0 or total <= 0 or end > total:
            raise RuntimeError("LifeAgent task material progress is invalid")
        if end == total:
            identities.append(task_id)
    return tuple(sorted(set(identities)))


def _visible_routes(data: dict[str, Any]) -> tuple[tuple[str, str, int], ...]:
    """@brief 冻结人物当前位置公开路线的目的地、方式与时长三元组。"""
    routes = data.get("routes")
    if not isinstance(routes, list):
        raise RuntimeError("LifeAgent routes catalog must be a list")
    values = []
    for route in routes:
        destination = route.get("destination_id") if isinstance(route, dict) else None
        mode = route.get("mode") if isinstance(route, dict) else None
        duration = route.get("duration_seconds") if isinstance(route, dict) else None
        cost = route.get("minimum_energy_cost_kcal") if isinstance(route, dict) else None
        feasible = route.get("energy_feasible") if isinstance(route, dict) else None
        if (not isinstance(destination, str) or not destination.strip()
                or mode not in {"driving", "walking"}
                or isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0
                or isinstance(cost, bool) or not isinstance(cost, (int, float))
                or not math.isfinite(cost) or cost < 0 or not isinstance(feasible, bool)):
            raise RuntimeError("LifeAgent route contract is invalid")
        if duration >= 60 and feasible:
            values.append((destination, mode, duration))
    if len(values) != len(set(values)):
        raise RuntimeError("LifeAgent route contracts are duplicated")
    return tuple(sorted(values))


def _available_action_models(
    targets: dict[str, tuple[str, ...]],
    controls: dict[str, Any],
    contracts: dict[str, Any],
    browse_categories: frozenset[str] = frozenset(),
) -> dict[str, type[BaseModel]]:
    """@brief 依据当前活动状态过滤预注册生活动作工具。

    修订合同四件新工具（plan_day/revise_day_plan/send_message/
    start_social_activity）与购买、进食、提交一样属于瞬时或独立动作，
    只在无当前活动的自由决策点开放；活动反馈边界只提供该活动允许的
    控制、调速或直接切换工具（docs/dev/SPEC-life-runtime.md「活动组合政策」）。
    """
    active = controls["active"]
    if active is None:
        names = {"start_basic_activity"}
        if any(targets.values()):
            names.add("start_targeted_activity")
        if browse_categories:
            names.add("browse_activities")
        if controls["paused_ids"]:
            names.add("set_activity_state")
        names.update(name for name, contract in contracts.items() if contract)
    elif active["kind"] == "play_game":
        names = {"set_activity_state"}
        if controls["game_commands"]:
            names.add("game_action")
    else:
        names = {"continue_activity", "set_activity_state", "start_basic_activity"}
        if any(targets.values()):
            names.add("start_targeted_activity")
        if contracts["start_travel"]:
            names.add("start_travel")
        if active["kind"] == "watch":
            names.add("change_playback_speed")
    return {name: _TOOL_MODELS[name] for name in _TOOL_MODELS if name in names}


def _catalog_identity_values(
    catalog: list[Any],
    field: str,
    identity_field: str | None,
) -> tuple[str, ...]:
    """@brief 严格提取一个目录的非空身份并形成排序闭集。"""
    values = []
    for item in catalog:
        if identity_field is not None and not isinstance(item, dict):
            raise RuntimeError(f"LifeAgent {field} catalog item must be an object")
        identity = item.get(identity_field) if identity_field is not None else item
        if not isinstance(identity, str) or not identity.strip():
            raise RuntimeError(f"LifeAgent {field} catalog identity must be a nonblank string")
        values.append(identity)
    return tuple(sorted(set(values)))


def _validate_visible_target(
    request: BaseModel,
    targets: dict[str, tuple[str, ...]],
    maximums: dict[str, int],
) -> str:
    """@brief 要求活动类型与本轮首观察中的目标身份准确配对。"""
    kind = str(request.activity_type)
    allowed = targets.get(kind, ())
    if request.target_id not in allowed:
        return "start_targeted_activity target_id is not visible for activity_type"
    if request.planned_duration_seconds > maximums[kind]:
        return "start_targeted_activity duration exceeds the current energy limit for activity_type"
    return ""


def _validate_prepared_action(
    action: str,
    request: BaseModel,
    targets: dict[str, tuple[str, ...]],
    controls: dict[str, Any],
    contracts: dict[str, Any],
    browse_categories: frozenset[str] = frozenset(),
) -> str:
    """@brief 用同一冻结观察核对模型校验与实际分派边界。"""
    if action == "start_targeted_activity":
        return _validate_visible_target(request, targets, controls["maximum_duration_seconds_by_kind"])
    if action in _ACTIVITY_CONTROL_TOOLS:
        return _validate_activity_control(action, request, controls)
    if action == "browse_activities":
        if request.category not in browse_categories:
            return "browse_activities category is not a key of the visible activity_summary"
        return ""
    if action in contracts:
        return _validate_visible_action(action, request, contracts)
    return ""


def _visible_browse_categories(data: dict[str, Any]) -> frozenset[str]:
    """@brief 从 activity_summary 冻结本轮可下钻的活动品类闭集。"""
    summary = data.get("activity_summary")
    if not isinstance(summary, dict) or not summary:
        raise RuntimeError("LifeAgent activity summary is unavailable")
    categories = []
    for key, count in summary.items():
        if (not isinstance(key, str) or not key.strip()
                or isinstance(count, bool) or not isinstance(count, int) or count < 0):
            raise RuntimeError("LifeAgent activity summary entry is invalid")
        categories.append(key)
    return frozenset(categories)


def _bind_browse_categories(
    schemas: list[dict[str, Any]],
    categories: frozenset[str],
) -> None:
    """@brief 将 activity_summary 键绑定到 browse_activities 原生 Schema。"""
    browse = next(
        (item for item in schemas if item["function"]["name"] == "browse_activities"),
        None,
    )
    if browse is None:
        return
    properties = browse["function"]["parameters"]["properties"]
    properties["category"]["enum"] = sorted(categories)


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


def _bind_finish_reference_ids(
    schemas: list[dict[str, Any]],
    reference_ids: set[str],
) -> None:
    """@brief 把本轮实际可见来源身份写入 finish 原生 Schema。"""
    finish = next(item for item in schemas if item["function"]["name"] == "finish")
    parameters = finish["function"]["parameters"]
    episode = parameters["properties"]["memories"]["items"]
    episode["properties"]["refs"]["items"]["enum"] = sorted(reference_ids)


def _bind_targeted_activity_catalogs(
    schemas: list[dict[str, Any]],
    targeted_kinds: tuple[str, ...],
    targets: dict[str, tuple[str, ...]],
    maximums: dict[str, int],
) -> None:
    """@brief 将本轮可见目录绑定到原生目标活动工具 Schema。

    枚举序取合同声明序（targeted_kinds 自观察合同派生，本侧零硬编码）。
    """
    targeted = next(
        (item for item in schemas if item["function"]["name"] == "start_targeted_activity"),
        None,
    )
    if targeted is None:
        return
    kinds = [kind for kind in targeted_kinds if targets[kind]]
    if not kinds:
        schemas[:] = [item for item in schemas if item["function"]["name"] != "start_targeted_activity"]
        return
    properties = targeted["function"]["parameters"]["properties"]
    properties["activity_type"]["enum"] = kinds
    properties["activity_type"]["description"] = _energy_limit_description(kinds, maximums)
    properties["target_id"]["enum"] = sorted({value for kind in kinds for value in targets[kind]})
    properties["target_id"]["description"] = _TARGET_ID_DESCRIPTION
    properties["planned_duration_seconds"]["maximum"] = max(maximums[kind] for kind in kinds)


def _energy_limit_description(kinds: list[str], maximums: dict[str, int]) -> str:
    """@brief 将每类当前最大墙钟时长写入实际模型工具说明。"""
    values = ", ".join(f"{kind}={maximums[kind]}" for kind in kinds)
    return f"{_TARGET_KIND_DESCRIPTION} Current maximum wall-clock seconds by kind: {values}."


def _bind_activity_control_schemas(
    schemas: list[dict[str, Any]],
    controls: dict[str, Any],
) -> None:
    """@brief 将当前或暂停活动身份与操作写入实际模型工具 Schema。"""
    functions = {item["function"]["name"]: item["function"] for item in schemas}
    active = controls["active"]
    if "set_activity_state" in functions:
        identifiers = [active["activity_id"]] if active is not None else list(controls["paused_ids"])
        properties = functions["set_activity_state"]["parameters"]["properties"]
        properties["activity_id"]["enum"] = identifiers
        if active is not None:
            properties["operation"]["enum"] = ["pause", "cancel"]
        else:
            operations = ["cancel"]
            if controls["menu_resumable_paused_ids"]:
                operations.insert(0, "resume")
            properties["operation"]["enum"] = operations
    for name in ("continue_activity", "change_playback_speed"):
        if name in functions:
            properties = functions[name]["parameters"]["properties"]
            properties["activity_id"]["enum"] = [active["activity_id"]]
    if "game_action" in functions:
        properties = functions["game_action"]["parameters"]["properties"]
        properties["game_session_id"]["enum"] = [controls["game_session_id"]]
        properties["command"]["enum"] = list(controls["game_commands"])


def _bind_visible_action_schemas(schemas: list[dict[str, Any]], contracts: dict[str, Any]) -> None:
    """@brief 将公开身份闭集写入瞬时动作工具的实际 Schema。"""
    functions = {item["function"]["name"]: item["function"] for item in schemas}
    if "purchase" in functions:
        products = contracts["purchase"]
        properties = functions["purchase"]["parameters"]["properties"]
        item_properties = properties["items"]["items"]["properties"]
        item_properties["product_id"]["enum"] = sorted(products)
        item_properties["price_version"]["enum"] = sorted(
            {item["price_version"] for item in products.values()})
        properties["destination_id"]["enum"] = sorted(
            {identity for item in products.values() for identity in item["destination_ids"]})
        if not any(item["pickup_allowed"] for item in products.values()):
            properties["fulfillment"]["enum"] = ["delivery"]
    if "consume" in functions:
        products = contracts["consume"]
        properties = functions["consume"]["parameters"]["properties"]
        properties["items"]["items"]["properties"]["product_id"]["enum"] = sorted(products)
        properties["use_mode"]["enum"] = sorted({mode for item in products.values()
                                                 for mode in item["use_modes"]})
        properties["duration_seconds"]["enum"] = sorted(
            {item["duration_seconds"] for item in products.values()})
    if "submit_work" in functions:
        properties = functions["submit_work"]["parameters"]["properties"]
        properties["task_id"]["enum"] = list(contracts["submit_work"])
    if "start_travel" in functions:
        routes = contracts["start_travel"]
        properties = functions["start_travel"]["parameters"]["properties"]
        properties["destination_id"]["enum"] = sorted({item[0] for item in routes})
        properties["travel_mode"]["enum"] = sorted({item[1] for item in routes})
        properties["planned_duration_seconds"]["enum"] = sorted({item[2] for item in routes})
    for name in ("plan_day", "revise_day_plan"):
        if name in functions:
            properties = functions[name]["parameters"]["properties"]
            properties["segments"]["items"]["properties"]["intent_kind"]["enum"] = list(INTENT_KINDS)
    if "send_message" in functions:
        properties = functions["send_message"]["parameters"]["properties"]
        properties["recipient_id"]["enum"] = list(contracts["send_message"]["recipients"])
    if "start_social_activity" in functions:
        contract = contracts["start_social_activity"]
        properties = functions["start_social_activity"]["parameters"]["properties"]
        properties["kind"]["enum"] = [kind for kind in ("chat", "call")
                                      if contract[f"{kind}_partners"]]
        properties["partner_id"]["enum"] = sorted(
            set(contract["chat_partners"]) | set(contract["call_partners"]))


def _validate_activity_control(
    action: str,
    request: BaseModel,
    controls: dict[str, Any],
) -> str:
    """@brief 本地核对控制工具与本轮活动身份、操作和游戏命令。"""
    active = controls["active"]
    if action == "set_activity_state":
        identifiers = {active["activity_id"]} if active is not None else set(controls["paused_ids"])
        operations = {"pause", "cancel"} if active is not None else {"resume", "cancel"}
        if request.activity_id not in identifiers or request.operation not in operations:
            return "set_activity_state is not valid for the visible activity state"
        if (active is None and request.operation == "resume"
                and request.activity_id not in controls["resumable_paused_ids"]):
            return "set_activity_state resume exceeds current energy for the visible paused activity"
    elif action in {"continue_activity", "change_playback_speed"}:
        if active is None or request.activity_id != active["activity_id"]:
            return f"{action} activity_id is not the current visible activity"
    elif action == "game_action":
        if (request.game_session_id != controls["game_session_id"]
                or request.command not in controls["game_commands"]):
            return "game_action is not valid for the current visible game state"
    return ""


def _validate_visible_action(action: str, request: BaseModel, contracts: dict[str, Any]) -> str:
    """@brief 本地核对商品报价、使用方式、任务身份、路线与计划配对。"""
    if action == "purchase":
        return _validate_purchase_order(request, contracts[action])
    if action == "consume":
        return _validate_meal_request(request, contracts[action])
    if action == "submit_work" and request.task_id not in contracts[action]:
        return "submit_work task_id has no fully revealed visible material"
    if action == "start_travel":
        route = (request.destination_id, request.travel_mode, request.planned_duration_seconds)
        if route not in contracts[action]:
            return "start_travel destination, mode, and duration are not a visible route"
    if action == "plan_day":
        return _validate_plan_segments(request.segments, contracts[action]["now"], 5, 8,
                                       "plan_day")
    if action == "revise_day_plan":
        return _validate_plan_segments(request.segments, contracts[action]["now"], 1, 8,
                                       "revise_day_plan")
    if action == "send_message":
        if request.recipient_id not in contracts[action]["recipients"]:
            return "send_message recipient_id is not visible"
        if len(request.text) > _MESSAGE_TEXT_MAX_CHARS:
            return "send_message text exceeds 300 characters"
    if action == "start_social_activity":
        partners = contracts[action][f"{request.kind}_partners"]
        if request.partner_id not in partners:
            return "start_social_activity partner_id is not visible"
    return ""


def _validate_purchase_order(request: BaseModel, catalog: dict[str, dict[str, Any]]) -> str:
    """@brief 本地核对多件购买：件数、可见配对、库存、同商家与自提边界。"""
    if not _ORDER_ITEM_BOUNDS[0] <= len(request.items) <= _ORDER_ITEM_BOUNDS[1]:
        return "purchase requires one to five items"
    merchants = set()
    for item in request.items:
        product = catalog.get(item.product_id)
        if product is None or item.price_version != product["price_version"]:
            return "purchase product_id and price_version are not a visible pair"
        if item.quantity > product["current_stock"]:
            return "purchase quantity exceeds the visible current stock"
        merchants.add(product["merchant_location_id"])
    if len(merchants) != 1:
        return "purchase items must share one merchant"
    destinations = {identity for product in catalog.values()
                    for identity in product["destination_ids"]}
    if request.destination_id not in destinations:
        return "purchase destination_id is not visible"
    if (request.fulfillment == "pickup"
            and (request.destination_id not in merchants
                 or not any(product["pickup_allowed"] for product in catalog.values()))):
        return "purchase pickup is not valid for the visible products and location"
    return ""


def _validate_meal_request(request: BaseModel, catalog: dict[str, dict[str, Any]]) -> str:
    """@brief 本地核对多件进食：件数、持有量、使用方式与时长口径。"""
    if not _MEAL_ITEM_BOUNDS[0] <= len(request.items) <= _MEAL_ITEM_BOUNDS[1]:
        return "consume requires one to three items"
    if request.use_mode == "use" and len(request.items) != 1:
        return "consume use mode accepts exactly one item"
    durations = []
    for item in request.items:
        product = catalog.get(item.product_id)
        if (product is None or request.use_mode not in product["use_modes"]
                or item.quantity > product["held_quantity"]):
            return "consume request is not valid for current local inventory"
        durations.append(product["duration_seconds"])
    expected = max(durations) if request.use_mode == "eat" else durations[0]
    if request.duration_seconds != expected:
        return "consume duration does not match the selected products"
    return ""


def _validate_plan_segments(
    segments: Any,
    now: int,
    minimum: int,
    maximum: int,
    action: str,
) -> str:
    """@brief 本地核对计划段数量、意图闭集与"尚未开始"边界。"""
    if not minimum <= len(segments) <= maximum:
        return f"{action} requires {minimum} to {maximum} segments"
    for segment in segments:
        if segment.intent_kind not in INTENT_KINDS:
            return f"{action} intent_kind is outside the closed intent set"
        if segment.start < now:
            return f"{action} segment starts earlier than the current time"
    return ""


def _episode_ids(episodes: Any) -> set[str]:
    """@brief 从实际展示的记忆记录提取稳定身份。"""
    if not isinstance(episodes, list):
        raise RuntimeError("Visible memory episodes must be a list")
    ids: set[str] = set()
    for episode in episodes:
        episode_id = episode.get("id") if isinstance(episode, dict) else None
        if not isinstance(episode_id, str) or not episode_id:
            raise RuntimeError("Visible memory episode identity is unavailable")
        ids.add(episode_id)
    return ids


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

    家族相关工具描述自注册表合同拼装，指纹还绑定运行期目标字段描述与
    affordances.toml 字节摘要，确保提示字段指路和工具面一起留证。
    """
    from worldmock.catalog.affordances import default_affordances
    from worldmock.life.engine import affordance_contract

    descriptions = _life_tool_descriptions(
        affordance_contract(default_affordances()))
    schemas = [_tool_schema(name, _TOOL_MODELS[name], descriptions)
               for name in _TOOL_MODELS]
    encoded = json.dumps(
        {"tool_schemas": schemas,
         "target_kind_description": _TARGET_KIND_DESCRIPTION,
         "target_id_description": _TARGET_ID_DESCRIPTION,
         "affordance_registry_sha256": affordance_registry_sha256()},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
