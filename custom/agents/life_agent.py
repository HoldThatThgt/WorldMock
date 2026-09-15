"""@brief AgentSociety2 原生 LifeAgent，使用闭集生活工具直接调用 LifeEnv。"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, model_validator

from agentsociety2.agent import PersonAgent
from agentsociety2.agent.base import ReactDecision, ReactLoopRequest, ReactToolResult
from agentsociety2.society import DirectToolCall
from worldmock.life.models import ActionReceipt, REQUEST_MODELS, deepseek_strict_schema


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
        """@brief 拒绝反向人物决策持续秒数闭区间。"""
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
    ]
    importance: float = Field(ge=0, le=1)
    keywords: list[Annotated[str, Field(pattern=r"\S")]]
    text: str = Field(pattern=r"\S")
    source: str = Field(pattern=r"^decision_observation$")
    refs: list[Annotated[str, Field(pattern=r"\S")]]

    @model_validator(mode="after")
    def validate_local_limits(self) -> "LifeMemoryEpisode":
        """@brief 保留服务端不支持表达的记忆文本和数组数量约束。"""
        if len(self.keywords) > 12:
            raise ValueError("memory episode has more than twelve keywords")
        if len(self.text) > 1000:
            raise ValueError("memory episode text exceeds 1000 characters")
        if not 1 <= len(self.refs) <= 8:
            raise ValueError("memory episode requires one to eight refs")
        return self


class FinishRequest(_ToolRequest):
    """@brief 在一个生活动作后结束当前人物决策并保存记忆。"""

    memories: Annotated[
        list[LifeMemoryEpisode],
        WithJsonSchema({
            "type": "array",
            "items": LifeMemoryEpisode.model_json_schema(mode="validation"),
        }),
    ]

    @model_validator(mode="after")
    def validate_memory_count(self) -> "FinishRequest":
        """@brief 保留服务端不支持表达的记忆条目数量约束。"""
        if not 1 <= len(self.memories) <= 8:
            raise ValueError("finish requires one to eight memories")
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

_READONLY_TOOL_MODELS: dict[str, type[BaseModel]] = {
    **_MEMORY_TOOL_MODELS,
    "finish": ReadonlyFinishRequest,
}

_MEMORY_RUNTIME_ACTIONS = {
    "memory_recent": "memory_recent",
    "memory_search": "memory_search",
    "memory_step_range": "memory_range",
    "memory_tick_range": "memory_range",
    "memory_time_range": "memory_range",
    "memory_read": "memory_read",
}

_TOOL_DESCRIPTIONS = {
    "start_basic_activity": "Start wait or sleep for an explicit duration.",
    "start_targeted_activity": "Start an activity using an exact target from the current observation catalogs.",
    "start_travel": "Start travel to an observed destination using an observed mode and duration.",
    "set_activity_state": "Pause, resume, or cancel an activity exposed by the current observation.",
    "change_playback_speed": "Change playback speed for the owned active watching activity.",
    "continue_activity": "Continue the current activity when its visible feedback boundary is due.",
    "purchase": "Purchase a visible catalog product with an explicit fulfillment method.",
    "consume": "Consume or use a product already present in personal inventory.",
    "submit_work": "Submit one actual work deliverable for an available task.",
    "game_action": "Send one admissible command to an active TextWorld game session.",
    "memory_recent": "Read the person's most recent durable memory episodes.",
    "memory_search": "Search the person's durable memory episodes by keyword.",
    "memory_step_range": "Read durable memory episodes within an inclusive decision-step range.",
    "memory_tick_range": "Read durable memory episodes within an inclusive duration range.",
    "memory_time_range": "Read durable memory episodes within an inclusive ISO time range.",
    "memory_read": "Read durable memory episodes by exact stable identifiers.",
    "finish": (
        "Finish after exactly one submitted life action; every memory ref must use "
        "an allowed source ID from this schema."
    ),
}

_TARGET_CATALOG_FIELDS: dict[str, tuple[str, str | None]] = {
    "work": ("tasks", "task_id"),
    "watch": ("content_catalog", "item_id"),
    "play_game": ("game_catalog", "game_id"),
    "read_mail": ("messages", "message_id"),
    "browse": ("browse_targets", None),
}

_TARGET_KIND_DESCRIPTION = (
    "Choose only a kind with a currently visible target: work uses observations[0].data.tasks[].task_id; "
    "watch uses content_catalog[].item_id; play_game uses game_catalog[].game_id; read_mail uses "
    "messages[].message_id; browse uses browse_targets[]."
)

_TARGET_ID_DESCRIPTION = (
    "Use the exact visible ID paired with activity_type: work=tasks[].task_id; "
    "watch=content_catalog[].item_id; play_game=game_catalog[].game_id; "
    "read_mail=messages[].message_id; browse=browse_targets[]."
)

_ACTIVITY_CONTROL_TOOLS = frozenset({
    "set_activity_state",
    "change_playback_speed",
    "continue_activity",
    "game_action",
})

_CONTINUABLE_ACTIVITY_KINDS = frozenset({
    "wait",
    "work",
    "watch",
    "travel",
    "read_mail",
    "browse",
    "consume",
})

_LIFE_INSTRUCTIONS = """
You are {name}, living as the person described in the profile inside a simulated Shanghai life.
Use only the current observation and durable memory. Never invent products, tasks, messages,
locations, inventory, game commands, or completed results. Use only the native tools supplied with
this request; no skill, workspace, task-list, environment-query, or script tools exist. Memory tools
may retrieve relevant past episodes. During this decision, submit exactly one life action. A
submitted receipt only proves submission. After that receipt, call finish as the only tool call in a
later response and record at least one truthful memory. Every ref in every memory must be one of the
allowed source IDs in the current finish tool schema: current observation_event_id, current
recent_action_results[].event_id, or a durable episode ID actually shown in initial durable memory or
returned by a memory tool. Never use action_id, decision_id, round_id, request hashes, tool_call_id,
product IDs, activity IDs, or any other identifier as a ref. The current submitted action has not
been settled yet, so describe it only as a choice or submission. Never emit free text instead of a
native tool call. At a visible activity feedback boundary, use
continue_activity to keep going, use set_activity_state to pause or cancel it, or use
change_playback_speed for active watching. Do not start another activity over an active one.
""".strip()

_READONLY_INSTRUCTIONS = """
You are {name}, answering one explicit read-only question as the person described in the profile.
Use only the profile and durable memory. Use only the supplied memory tools, then call finish with a
non-empty answer. No life action, skill, workspace, task-list, environment-query, or script tools
exist. Never emit the answer as free text instead of a native finish tool call.
""".strip()


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
        if readonly:
            models = _READONLY_TOOL_MODELS
        elif getattr(self, "_life_action_count", 0):
            models = {**_MEMORY_TOOL_MODELS, "finish": FinishRequest}
        else:
            if not hasattr(self, "_life_action_models"):
                raise RuntimeError("LifeAgent activity controls are not prepared")
            models = {**self._life_action_models, **_MEMORY_TOOL_MODELS}
        schemas = [_tool_schema(name, model) for name, model in models.items()]
        if not readonly and not getattr(self, "_life_action_count", 0):
            if not hasattr(self, "_life_visible_targets"):
                raise RuntimeError("LifeAgent visible target catalogs are not prepared")
            _bind_targeted_activity_catalogs(schemas, self._life_visible_targets)
            _bind_activity_control_schemas(schemas, self._life_activity_controls)
            _bind_visible_action_schemas(schemas, self._life_action_contracts)
        if not readonly and "finish" in models:
            _bind_finish_reference_ids(
                schemas,
                getattr(self, "_life_visible_refs", set()),
            )
        return schemas

    def build_react_messages(self, request: ReactLoopRequest) -> list[dict[str, Any]]:
        """@brief 构造只描述实际闭集工具的生活人物提示。"""
        template = _READONLY_INSTRUCTIONS if request.readonly else _LIFE_INSTRUCTIONS
        system = template.format(name=self.name or "this person")
        context = {
            "profile": self.get_profile(),
            "current_time": request.t.isoformat(),
            "observations": request.observations,
        }
        if self._enable_memory:
            memory = self._build_memory_context()
            self._life_visible_refs.update(
                _episode_ids(memory.get("recent_episodes", []))
            )
            context["durable_memory"] = memory
        if request.question is not None:
            context["question"] = request.question
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)},
        ]

    def prepare_react_loop(self, request: ReactLoopRequest) -> None:
        """@brief 重置当前决策动作计数并冻结可见来源事件。"""
        super().prepare_react_loop(request)
        self._life_action_count = 0
        self._life_readonly = request.readonly
        if request.readonly:
            self._life_visible_refs = set()
            self._life_visible_targets = {}
            self._life_activity_controls = {}
            self._life_action_contracts = {}
            self._life_action_models = {}
            return
        data = _environment_observation_data(request.observations)
        self._life_visible_refs = _environment_source_ids(data)
        self._life_visible_targets = _visible_target_ids(data)
        self._life_activity_controls = _visible_activity_controls(data)
        self._life_action_contracts = _visible_action_contracts(data)
        self._life_action_models = _available_action_models(
            self._life_visible_targets, self._life_activity_controls, self._life_action_contracts)

    def validate_react_decisions(
        self,
        decisions: list[ReactDecision],
        readonly: bool,
    ) -> str:
        """@brief 在执行任何工具前验证闭集参数和原子动作数量。"""
        if readonly != self._life_readonly:
            raise RuntimeError("LifeAgent readonly decision state is inconsistent")
        if readonly:
            models = _READONLY_TOOL_MODELS
        elif self._life_action_count:
            models = {**_MEMORY_TOOL_MODELS, "finish": FinishRequest}
        else:
            models = {**self._life_action_models, **_MEMORY_TOOL_MODELS}
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
                )
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
            raise RuntimeError("Life action is unavailable in the prepared activity state")
        context = self.require_decision_context()
        request = _validate_request(REQUEST_MODELS[action], args)
        error = _validate_prepared_action(
            action,
            request,
            self._life_visible_targets,
            self._life_activity_controls,
            self._life_action_contracts,
        )
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


def _visible_target_ids(data: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """@brief 从五个公开目录冻结本轮可用活动目标身份。"""
    result = {}
    for kind, (field, identity_field) in _TARGET_CATALOG_FIELDS.items():
        catalog = data.get(field)
        if not isinstance(catalog, list):
            raise RuntimeError(f"LifeAgent {field} catalog must be a list")
        result[kind] = _catalog_identity_values(catalog, field, identity_field)
    result["work"] = _visible_work_target_ids(data, result["work"])
    result["watch"] = _visible_watch_target_ids(data, result["watch"])
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


def _visible_watch_target_ids(data: dict[str, Any], visible_ids: tuple[str, ...]) -> tuple[str, ...]:
    """@brief 只保留可按最低倍速播放至少六十秒的内容目标。"""
    durations = _visible_content_durations(data["content_catalog"], visible_ids)
    available = _visible_content_available(data.get("content_progress"), durations)
    return tuple(sorted(item_id for item_id, seconds in available.items() if seconds >= 30))


def _visible_content_durations(
    catalog: list[Any],
    visible_ids: tuple[str, ...],
) -> dict[str, int]:
    """@brief 校验公开内容目录中的稳定身份与总媒体时长。"""
    result = {}
    for item in catalog:
        item_id = item.get("item_id") if isinstance(item, dict) else None
        duration = item.get("duration_seconds") if isinstance(item, dict) else None
        if (item_id not in visible_ids or isinstance(duration, bool)
                or not isinstance(duration, int) or duration <= 0):
            raise RuntimeError("LifeAgent content duration is invalid")
        if item_id in result and result[item_id] != duration:
            raise RuntimeError("LifeAgent content duration is ambiguous")
        result[item_id] = duration
    return result


def _visible_content_available(progress: Any, durations: dict[str, int]) -> dict[str, float]:
    """@brief 校验人物观看进度并返回下次播放可用媒体秒数。"""
    if not isinstance(progress, dict):
        raise RuntimeError("LifeAgent content_progress must be an object")
    available = {item_id: float(duration) for item_id, duration in durations.items()}
    for item_id, item in progress.items():
        if item_id not in durations or not isinstance(item, dict) or item.get("item_id") != item_id:
            raise RuntimeError("LifeAgent content progress identity is invalid")
        position = item.get("position_seconds")
        remaining = item.get("remaining_media_seconds")
        exposure = item.get("exposure_count")
        values = (position, remaining)
        if (any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values)
                or not isinstance(exposure, int) or isinstance(exposure, bool) or exposure < 0):
            raise RuntimeError("LifeAgent content progress value is invalid")
        expected = durations[item_id] - float(position)
        if position < 0 or expected < 0 or abs(float(remaining) - expected) > 1e-6:
            raise RuntimeError("LifeAgent content remaining time is invalid")
        available[item_id] = float(durations[item_id]) if expected == 0 else expected
    return available


def _visible_activity_controls(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 从当前活动、暂停活动与游戏局面冻结本轮控制合同。"""
    active = _visible_active_activity(data)
    paused_ids = _visible_paused_activity_ids(data)
    if active is not None and active["activity_id"] in paused_ids:
        raise RuntimeError("LifeAgent active activity is also listed as paused")
    controls = {"active": active, "paused_ids": paused_ids,
                "game_session_id": None, "game_commands": ()}
    if active is not None and active["kind"] == "play_game":
        session_id, commands = _visible_game_control(data)
        controls.update(game_session_id=session_id, game_commands=commands)
    return controls


def _visible_active_activity(data: dict[str, Any]) -> dict[str, str] | None:
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
    if kind not in _CONTINUABLE_ACTIVITY_KINDS | {"play_game"}:
        raise RuntimeError("LifeAgent active activity kind has no decision boundary controls")
    return {"activity_id": activity_id, "kind": kind}


def _visible_paused_activity_ids(data: dict[str, Any]) -> tuple[str, ...]:
    """@brief 校验并排序人物本人暂停活动的可控制身份。"""
    activities = data.get("paused_activities")
    if not isinstance(activities, list):
        raise RuntimeError("LifeAgent paused_activities must be a list")
    identities = []
    for activity in activities:
        if not isinstance(activity, dict) or activity.get("status") != "paused":
            raise RuntimeError("LifeAgent paused activity state is invalid")
        activity_id = activity.get("activity_id")
        if not isinstance(activity_id, str) or not activity_id.strip():
            raise RuntimeError("LifeAgent paused activity identity is invalid")
        identities.append(activity_id)
    if len(identities) != len(set(identities)):
        raise RuntimeError("LifeAgent paused activity identities are duplicated")
    return tuple(sorted(identities))


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
    """@brief 冻结购买、使用、提交与出行工具所需的公开身份配对。"""
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
    }


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
    """@brief 仅保留人物当前位置数量为正且地点要求满足的商品。"""
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
                              "duration_seconds": product["duration_seconds"]}
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
        if (not isinstance(destination, str) or not destination.strip()
                or mode not in {"driving", "walking"}
                or isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0):
            raise RuntimeError("LifeAgent route contract is invalid")
        if duration >= 60:
            values.append((destination, mode, duration))
    if len(values) != len(set(values)):
        raise RuntimeError("LifeAgent route contracts are duplicated")
    return tuple(sorted(values))


def _available_action_models(
    targets: dict[str, tuple[str, ...]],
    controls: dict[str, Any],
    contracts: dict[str, Any],
) -> dict[str, type[BaseModel]]:
    """@brief 依据当前活动状态过滤预注册生活动作工具。"""
    active = controls["active"]
    if active is None:
        names = {"start_basic_activity"}
        if any(targets.values()):
            names.add("start_targeted_activity")
        if controls["paused_ids"]:
            names.add("set_activity_state")
        names.update(name for name, contract in contracts.items() if contract)
    elif active["kind"] == "play_game":
        names = {"set_activity_state"}
        if controls["game_commands"]:
            names.add("game_action")
    else:
        names = {"continue_activity", "set_activity_state"}
        if active["kind"] == "watch":
            names.add("change_playback_speed")
    return {name: model for name, model in REQUEST_MODELS.items() if name in names}


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


def _validate_visible_target(request: BaseModel, targets: dict[str, tuple[str, ...]]) -> str:
    """@brief 要求活动类型与本轮首观察中的目标身份准确配对。"""
    kind = str(request.activity_type)
    allowed = targets.get(kind, ())
    if request.target_id not in allowed:
        return "start_targeted_activity target_id is not visible for activity_type"
    return ""


def _validate_prepared_action(
    action: str,
    request: BaseModel,
    targets: dict[str, tuple[str, ...]],
    controls: dict[str, Any],
    contracts: dict[str, Any],
) -> str:
    """@brief 用同一冻结观察核对模型校验与实际分派边界。"""
    if action == "start_targeted_activity":
        return _validate_visible_target(request, targets)
    if action in _ACTIVITY_CONTROL_TOOLS:
        return _validate_activity_control(action, request, controls)
    if action in contracts:
        return _validate_visible_action(action, request, contracts)
    return ""


def _tool_schema(name: str, model: type[BaseModel]) -> dict[str, Any]:
    """@brief 从唯一请求模型形成 OpenAI 原生 function Schema。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": _TOOL_DESCRIPTIONS[name],
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
    targets: dict[str, tuple[str, ...]],
) -> None:
    """@brief 将本轮可见目录绑定到原生目标活动工具 Schema。"""
    targeted = next(
        (item for item in schemas if item["function"]["name"] == "start_targeted_activity"),
        None,
    )
    if targeted is None:
        return
    kinds = [kind for kind in _TARGET_CATALOG_FIELDS if targets[kind]]
    if not kinds:
        schemas[:] = [item for item in schemas if item["function"]["name"] != "start_targeted_activity"]
        return
    properties = targeted["function"]["parameters"]["properties"]
    properties["activity_type"]["enum"] = kinds
    properties["activity_type"]["description"] = _TARGET_KIND_DESCRIPTION
    properties["target_id"]["enum"] = sorted({value for kind in kinds for value in targets[kind]})
    properties["target_id"]["description"] = _TARGET_ID_DESCRIPTION


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
        properties["operation"]["enum"] = ["pause", "cancel"] if active is not None else ["resume", "cancel"]
    for name in ("continue_activity", "change_playback_speed"):
        if name in functions:
            properties = functions[name]["parameters"]["properties"]
            properties["activity_id"]["enum"] = [active["activity_id"]]
    if "game_action" in functions:
        properties = functions["game_action"]["parameters"]["properties"]
        properties["game_session_id"]["enum"] = [controls["game_session_id"]]
        properties["command"]["enum"] = list(controls["game_commands"])


def _bind_visible_action_schemas(schemas: list[dict[str, Any]], contracts: dict[str, Any]) -> None:
    """@brief 将公开商品、持有物、任务和路线身份写入实际工具 Schema。"""
    functions = {item["function"]["name"]: item["function"] for item in schemas}
    if "purchase" in functions:
        products = contracts["purchase"]
        properties = functions["purchase"]["parameters"]["properties"]
        properties["product_id"]["enum"] = sorted(products)
        properties["price_version"]["enum"] = sorted({item["price_version"] for item in products.values()})
        properties["destination_id"]["enum"] = sorted({identity for item in products.values()
                                                          for identity in item["destination_ids"]})
        if not any(item["pickup_allowed"] for item in products.values()):
            properties["fulfillment"]["enum"] = ["delivery"]
    if "consume" in functions:
        products = contracts["consume"]
        properties = functions["consume"]["parameters"]["properties"]
        properties["product_id"]["enum"] = sorted(products)
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
    elif action in {"continue_activity", "change_playback_speed"}:
        if active is None or request.activity_id != active["activity_id"]:
            return f"{action} activity_id is not the current visible activity"
    elif action == "game_action":
        if (request.game_session_id != controls["game_session_id"]
                or request.command not in controls["game_commands"]):
            return "game_action is not valid for the current visible game state"
    return ""


def _validate_visible_action(action: str, request: BaseModel, contracts: dict[str, Any]) -> str:
    """@brief 本地核对商品报价、使用方式、任务身份和路线三元组。"""
    if action == "purchase":
        product = contracts[action].get(request.product_id)
        if product is None or request.price_version != product["price_version"]:
            return "purchase product_id and price_version are not a visible pair"
        if request.destination_id not in product["destination_ids"]:
            return "purchase destination_id is not visible"
        if request.fulfillment == "pickup" and (
                not product["pickup_allowed"] or request.destination_id != product["merchant_location_id"]):
            return "purchase pickup is not valid for the visible product and location"
    elif action == "consume":
        product = contracts[action].get(request.product_id)
        if (product is None or request.use_mode not in product["use_modes"]
                or request.duration_seconds != product["duration_seconds"]):
            return "consume request is not valid for current local inventory"
    elif action == "submit_work" and request.task_id not in contracts[action]:
        return "submit_work task_id has no fully revealed visible material"
    elif action == "start_travel":
        route = (request.destination_id, request.travel_mode, request.planned_duration_seconds)
        if route not in contracts[action]:
            return "start_travel destination, mode, and duration are not a visible route"
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
    """@brief 返回正式 LifeAgent 指令模板的稳定 SHA-256。"""
    return hashlib.sha256(_LIFE_INSTRUCTIONS.encode()).hexdigest()


def life_tool_schema_sha256() -> str:
    """@brief 返回正式生活决策闭集工具 Schema 的稳定 SHA-256。"""
    schemas = [_tool_schema(name, model) for name, model in _TOOL_MODELS.items()]
    encoded = json.dumps(schemas, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
