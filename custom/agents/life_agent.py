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
    "set_activity_state": "Pause, resume, or cancel the owned active activity.",
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
            models = {**REQUEST_MODELS, **_MEMORY_TOOL_MODELS}
        schemas = [_tool_schema(name, model) for name, model in models.items()]
        if not readonly and not getattr(self, "_life_action_count", 0):
            if not hasattr(self, "_life_visible_targets"):
                raise RuntimeError("LifeAgent visible target catalogs are not prepared")
            _bind_targeted_activity_catalogs(schemas, self._life_visible_targets)
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
            return
        data = _environment_observation_data(request.observations)
        self._life_visible_refs = _environment_source_ids(data)
        self._life_visible_targets = _visible_target_ids(data)

    def validate_react_decisions(
        self,
        decisions: list[ReactDecision],
        readonly: bool,
    ) -> str:
        """@brief 在执行任何工具前验证闭集参数和原子动作数量。"""
        if readonly != self._life_readonly:
            raise RuntimeError("LifeAgent readonly decision state is inconsistent")
        models = _READONLY_TOOL_MODELS if readonly else _TOOL_MODELS
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
                if decision.action == "start_targeted_activity":
                    error = _validate_visible_target(validated, self._life_visible_targets)
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
        context = self.require_decision_context()
        request = _validate_request(REQUEST_MODELS[action], args)
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
    return result


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
    kinds = [kind for kind in _TARGET_CATALOG_FIELDS if targets[kind]]
    if not kinds:
        schemas[:] = [item for item in schemas if item["function"]["name"] != "start_targeted_activity"]
        return
    targeted = next(item for item in schemas if item["function"]["name"] == "start_targeted_activity")
    properties = targeted["function"]["parameters"]["properties"]
    properties["activity_type"]["enum"] = kinds
    properties["activity_type"]["description"] = _TARGET_KIND_DESCRIPTION
    properties["target_id"]["enum"] = sorted({value for kind in kinds for value in targets[kind]})
    properties["target_id"]["description"] = _TARGET_ID_DESCRIPTION


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
