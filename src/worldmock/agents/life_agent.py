"""@brief 一次决策的生活人物：运行宿主为每个决策新建一个 LifeAgent，决定完就丢弃，决策之间不带内存状态。

一次决策分三段（docs/design/06-agent.md「宿主模型协议」）：先向引擎取恰好一份冻结观察，取观察之前不做任何模型调用；
再跑回合循环，请求由原生 tools、系统层 system 与六层 user 组成；最后做步尾保存，把本决策的已核验经历与收尾记忆写进
人物工作区，并向引擎登记「记忆已更新」。冻结输入（卡片、名字、模型身份与回合上限）在运行开始时由人设表与运行清单
一次建好（PersonInputs）。

工具面整次运行不变：世界请求、回看任务与检索记忆；只剩最后一个回合或截断重试时只开放世界请求。一次回复可以先做只读
查询；世界请求（一个控制请求，或同一次回复里两个开始工具各至多一次的开始组）登记即结束本次决策，登记后不追加任何 tool
消息。名称只按观察私有绑定做本地校验，与引擎受理共用同一组纯函数与同一份冻结观察；本地校验失败写成宿主与引擎共用的
错误结构回给模型，多项请求逐项报全部名称类错误（docs/design/04-simulation-engine.md，
src/worldmock/agents/protocol/life_feedback.py）；三个控制工具请求级的个人事务变更与引擎复核调用同一纯函数
（errands.change_issues）、读同一份冻结观察的两类私有事务绑定，问题并入同一次纠错。宿主不写入事务，事务变更也不是
记忆写入点。思考用尽输出上限的截断回合丢弃并纠错，随后以关闭思考、强制工具调用的请求重试一次
（src/worldmock/agents/protocol/life_truncation.py）。同一秒的后续决策在准备阶段注入引擎交来的续接链
（src/worldmock/agents/protocol/life_continuation.py）。回合机制（回合计数、调用身份、证据次序、纠错消息）在
src/worldmock/agents/protocol/react_loop.py。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from worldmock.agents.memory.life_memory import MemoryStep, memory_update_evidence, save_step
from worldmock.agents.prompt.life_persona import known_persons, persona_layer
from worldmock.agents.prompt.life_prompt import (
    TOOL_DESCRIPTIONS,
    last_progress_layer,
    life_user_layers,
    system_message,
    user_message,
)
from worldmock.agents.prompt.prompt_capacity import ProgressBudget
from worldmock.agents.protocol.life_continuation import continuation_messages
from worldmock.agents.protocol.life_feedback import (
    HostIssue,
    ProtocolFeedback,
    argument_feedback,
    host_feedback,
    merged_feedback,
    stage_error,
)
from worldmock.agents.protocol.life_truncation import RETRY_TOOL_CHOICE, truncated, truncation_feedback
from worldmock.agents.protocol.react_loop import ReactDecision, ReactLoop, ReactToolResult
from worldmock.configuration.inputs.models import PersonaConfig
from worldmock.configuration.persona.persona_card import persona_card, persona_card_struct
from worldmock.configuration.persona.persona_text import render_card
from worldmock.recording_engine.projections.activities.experiences import validate_verified_experiences
from worldmock.simulation_engine.actions.name_resolution import name_book, resolve_start
from worldmock.simulation_engine.decisions.continuation import CONTINUATION_KEY
from worldmock.simulation_engine.observation.targets.object_labels import resolve_name
from worldmock.simulation_engine.observation.wording.guidance import query_rejection, refusal_for_code
from worldmock.simulation_engine.scheduling.plans import errands
from worldmock.state.decisions.results import ActionReceipt
from worldmock.state.decisions.round import DecisionContext, DirectToolCall
from worldmock.state.interaction.actions import START_TOOLS
from worldmock.state.interaction.control_items import CONTROL_TOOLS, ControlItem, control_errands, control_items
from worldmock.state.interaction.errors import ErrorItem, ErrorReport, bind_report, host_report
from worldmock.state.interaction.requests import ConcludeActivityRequest, QUERY_MODELS, REQUEST_MODELS
from worldmock.state.interaction.schema import deepseek_strict_schema
from worldmock.state.model.truncation import RETRY_THINKING

if TYPE_CHECKING:
    from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore
    from worldmock.agents.protocol.engine_handle import EngineHandle
    from worldmock.configuration.model.model_client import ModelClient


#: 行动阶段思考开关与工具选择（思考模式拒收 required，真实探针见规格）。
ACTION_THINKING = {"thinking": {"type": "enabled"}}
ACTION_TOOL_CHOICE = "auto"
#: 每次决策的输出上限，思考与回答共用；实际发出的是它与服务输出上限中较小的一个，截断后的重试
#: 用同一个数，也是发送前完整上下文计数预留的输出（docs/design/06-agent.md「宿主模型协议」）。
ACTION_MAX_TOKENS = 16000


#: 生活决策的工具面：七个请求工具（REQUEST_MODELS 顺序，两个开始工具在前）与检索记忆，共八个；
#: 当日计划由零点后台任务生成，不是人物工具（docs/design/04-simulation-engine.md）。
_TOOL_MODELS: dict[str, type[BaseModel]] = {**REQUEST_MODELS, **QUERY_MODELS}

_QUERY_TOOL_MODELS = {"recall_task_summary": REQUEST_MODELS["recall_task_summary"], **QUERY_MODELS}
_WORLD_TOOL_MODELS = {name: model for name, model in REQUEST_MODELS.items() if name != "recall_task_summary"}

#: 每次决策至多派发的检索次数；第 4 次起以 MEMORY_SEARCH_LIMIT 回答、不送引擎（协议护栏，不是回合上界）。
MEMORY_SEARCHES_PER_DECISION = 3


def life_action_tool_names() -> frozenset[str]:
    """@brief 返回 LifeAgent 实际开放的全部生活动作工具名（工具面单元测试核对用）。"""
    return frozenset(_TOOL_MODELS) - frozenset(QUERY_MODELS)


def life_query_tool_names() -> frozenset[str]:
    """@brief 返回 LifeAgent 开放的全部只读查询工具名（工具面单元测试与只读查询白名单核对用）。"""
    return frozenset(_QUERY_TOOL_MODELS)


@dataclass(frozen=True, slots=True)
class PersonInputs:
    """@brief 一个人物的冻结输入：运行开始时由人设表与运行清单一次建好，整次运行不变。"""

    agent_id: int
    name: str
    persona_card: str
    persona_card_struct: dict[str, Any]
    model_identity: dict[str, Any]


def person_inputs(persona: PersonaConfig, stage_identity: Mapping[str, Any]) -> PersonInputs:
    """@brief 由人设表与运行清单冻结的行动阶段模型身份建人物的冻结输入；模型身份深拷贝，与清单互不别名。"""
    card = persona_card(persona)
    return PersonInputs(persona.agent_id, persona.name, render_card(card), persona_card_struct(card),
                        copy.deepcopy(stage_identity))


class LifeAgent:
    """@brief 一次决策的生活人物：观察、回合循环与步尾。"""

    def __init__(self, inputs: PersonInputs, engine: EngineHandle, model_client: ModelClient,
                 memory: WorldMockMemoryStore) -> None:
        """@brief 只存下四个运行级对象并把本次决策的内部状态置为初值；不读写文件，不调用任何服务。"""
        self.inputs = inputs
        self.engine = engine
        self.model_client = model_client
        self.memory = memory
        self._context: DecisionContext | None = None
        self._names: dict[str, Any] = {}
        self._verified_experiences: list[dict[str, Any]] = []
        self._observation_event_id = ""
        self._turn_models: dict[str, type[BaseModel]] = {}
        self._action_count = 0
        self._world_calls = 0
        self._memory_searches = 0
        self._call_positions: dict[str, int] = {}
        self._memories: list[dict[str, Any]] = []

    async def decide(self, context: DecisionContext) -> None:
        """@brief 一次决策：取一份观察、跑回合循环直到登记世界请求、做步尾保存；任何失败记日志后原样上抛。"""
        try:
            observation = self.engine.observe(context)
            await self.run_turns(context, observation)
            self._record_memory(context)
        except Exception:
            logging.exception("LifeAgent decision failed for agent %s", self.inputs.agent_id)
            raise

    async def run_turns(self, context: DecisionContext, observation: dict[str, Any]) -> list[ReactDecision]:
        """@brief 在给定观察上准备六层与私有名称并跑回合循环，返回登记的世界请求；不做步尾保存。"""
        self._context = context
        budget, history = self._prepare(observation)
        return await ReactLoop(self, context, budget, history).run()

    def turn_tools(self, remaining: int, retry: bool) -> list[dict[str, Any]]:
        """@brief 本回合的原生工具：只剩最后一个回合或截断重试时只有世界请求，其余回合另加回看任务与检索记忆。

        生活决策为七个请求工具与 search_memory，共八个；参数不带逐轮名称枚举。
        """
        queries = _QUERY_TOOL_MODELS if remaining > 1 and not retry else {}
        self._turn_models = {**_WORLD_TOOL_MODELS, **queries}
        return [_tool_schema(name, model) for name, model in self._turn_models.items()]

    def turn_request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                     retry: bool) -> dict[str, Any]:
        """@brief 本回合交给模型客户端的请求字典，按 REQUEST_KEYS 的次序直接构造。

        DeepSeek thinking 模式拒绝 tool_choice=required（真实探针 400："Thinking mode does not support this
        tool_choice"），因此与思考开启同时把 tool_choice 放宽为 auto；模型不产出工具调用时由协议纠错回合兜底。截断之后
        的那一次重试关闭思考并 tool_choice=required，max_tokens 不变，其余请求不变（docs/design/06-agent.md）。
        system 不附加任何预算文字。
        """
        identity = self.inputs.model_identity
        return {"model": identity["requested_model"], "messages": messages, "tools": tools,
                "tool_choice": RETRY_TOOL_CHOICE if retry else ACTION_TOOL_CHOICE,
                "max_tokens": identity["stages"]["action"]["max_tokens"],
                "extra_body": dict(RETRY_THINKING if retry else ACTION_THINKING)}

    def parse_reply(self, response: Any) -> tuple[list[ReactDecision], ProtocolFeedback | None]:
        """@brief 解析回复并在执行前原子校验整批调用；没有错误时纠错为 None。"""
        decisions, feedback = self._parse(response)
        if feedback is None:
            feedback = self._validate(decisions)
        return decisions, feedback

    def execute_decisions(self, decisions: list[ReactDecision]) -> tuple[list[ReactDecision], list[dict[str, Any]]]:
        """@brief 按回复中的先后执行每个调用：返回登记的世界请求与只读查询的 tool 消息。

        回看任务与检索记忆是只读查询，不结束本轮；世界请求按回复中的先后依次登记（开始组两条），登记后 conclude_activity
        逐项的记忆与看法展平留给本步步尾保存（其余请求为空列表）。
        """
        registered: list[ReactDecision] = []
        messages: list[dict[str, Any]] = []
        for decision in decisions:
            result = self._dispatch_decision(decision)
            message = self._tool_message(decision, result)
            if message is not None:
                messages.append(message)
            if decision.action in _WORLD_TOOL_MODELS:
                registered.append(decision)
        if registered:
            self._memories = _conclusion_memories(registered[0].action, registered[0].args)
        return registered, messages

    def _record_memory(self, context: DecisionContext) -> None:
        """@brief 步尾保存：把本决策的已核验经历与收尾记忆写进人物工作区，写了才向引擎登记「记忆已更新」。"""
        step = MemoryStep(self.inputs.persona_card.strip(), tuple(self._verified_experiences),
                          self._observation_event_id, tuple(self._memories), context.observed_at)
        outcome = save_step(self.memory, step)
        evidence = memory_update_evidence(context, self.memory, outcome)
        if evidence is not None:
            self.engine.record_memory_update(evidence)

    def _prepare(self, observation: dict[str, Any]) -> tuple[ProgressBudget, list[dict[str, Any]]]:
        """@brief 从冻结观察组装六层和本地校验所用的私有绑定名称；有续接链时返回本次决策的续接起始消息。

        人设层只由人物卡片构建；relationship 看法可写的对象另取观察私有证据 persona_views 的关系人物。
        """
        self._prepare_verified_experiences(observation)
        _visible_wake_reasons(observation)
        persona = persona_layer(self.inputs.persona_card_struct, self.inputs.name)
        layers = life_user_layers(observation, persona)
        budget = ProgressBudget(tuple(layers), tuple(_progress_meta(observation, last_progress_layer(layers))))
        self._names = {**_visible_names(observation),
                       "known_persons": known_persons(self.inputs.persona_card_struct,
                                                      _relationship_persons(observation))}
        fresh = [system_message(), user_message(layers)]
        continued = continuation_messages(observation.get(CONTINUATION_KEY), layers)
        if continued is not None:
            return replace(budget, fresh_messages=tuple(fresh), chain_length=len(continued)), continued
        return budget, fresh

    def _prepare_verified_experiences(self, data: dict[str, Any]) -> None:
        """@brief 冻结本轮本人事件事实窗口与观察身份（本步记忆更新的来源截止），模型和记忆不能写入该窗口。

        观察身份缺失是损坏的宿主观察，在任何模型调用之前失败，不等到记忆写入时才暴露。
        """

        now = _observation_now(data)
        self._verified_experiences = validate_verified_experiences(
            data.get("verified_experiences"), data.get("agent_id"), now)
        observation_event_id = data.get("observation_event_id")
        if not isinstance(observation_event_id, str) or not observation_event_id:
            logging.error("LifeAgent observation event identity is invalid")
            raise RuntimeError("LifeAgent observation event identity is invalid")
        self._observation_event_id = observation_event_id

    def _parse(self, response: Any) -> tuple[list[ReactDecision], ProtocolFeedback | None]:
        """@brief 参数 JSON、未知工具和真实调用位置在执行前原子校验。"""
        if truncated(response):
            return [], truncation_feedback()
        choices = getattr(response, "choices", None)
        message = getattr(choices[0], "message", None) if choices else None
        calls = getattr(message, "tool_calls", None) or []
        decisions, reports = _parse_model_calls(calls, self._turn_models)
        if reports:
            for index, decision in decisions:
                error = self._decision_error(decision)
                if error is not None:
                    reports.append(bind_report(error.report, index))
            return [], _combine_reports(reports)
        return [decision for _, decision in decisions], None

    def _validate(self, decisions: list[ReactDecision]) -> ProtocolFeedback | None:
        """@brief 整批先校验全部独立错误，再允许任何查询或世界请求执行。"""
        reports = []
        batch = _batch_error(decisions, self._action_count, tuple(self._turn_models))
        if batch is not None:
            reports.append(batch.report)
        for index, decision in enumerate(decisions):
            error = self._decision_error(decision)
            if error is not None:
                reports.append(bind_report(error.report, index))
        if reports:
            return _combine_reports(reports)
        self._call_positions = {decision.tool_call_id: index for index, decision in enumerate(decisions)}
        self._world_calls = sum(decision.action in _WORLD_TOOL_MODELS for decision in decisions)
        return None

    def _decision_error(self, decision: ReactDecision) -> ProtocolFeedback | None:
        """@brief 结构严格类型校验后按冻结观察验证工具的完整语义；本回合没有开放的工具名当场按 TOOL_CALL_REQUIRED 纠错。"""
        model = self._turn_models.get(decision.action)
        if model is None:
            item = ErrorItem(("tool_calls", 0, "function", "name"), decision.action, "TOOL_CALL_REQUIRED",
                             tuple(self._turn_models), "A tool offered in this round is required.",
                             "The tool is not offered in this round.")
            return host_feedback(host_report("TOOL_CALL_REQUIRED", items=(item,)), item.why)
        try:
            validated = _validate_request(model, decision.args)
        except ValidationError as error:
            return argument_feedback(decision.action, error, model, decision.args)
        return self._prepared_action_error(decision.action, validated)

    def _prepared_action_error(self, action: str, request: BaseModel) -> ProtocolFeedback | None:
        """@brief 用同一冻结观察核对名称；非生活动作（检索记忆）不核对名称，两个开始工具的对象名与 detail
        经统一名称解析（未列出的指名购买交给引擎在全量商品里解析）。"""
        if action not in REQUEST_MODELS:
            return None
        return _validate_prepared_action(action, request, self._names)

    def _dispatch_decision(self, decision: ReactDecision) -> ReactToolResult:
        """@brief 工具服务异常保留调用位置；不声称失败的调用已执行。"""
        try:
            return self._dispatch(decision.action, decision.args)
        except Exception as error:
            logging.exception("LifeAgent tool dispatch failed: %s", decision.action)
            index = self._call_positions[decision.tool_call_id]
            raise stage_error("TOOL_SERVICE_FAILED", ("tool_calls", index)) from error

    def _dispatch(self, action: str, args: dict[str, Any]) -> ReactToolResult:
        """@brief 执行生活动作；回看任务与检索记忆送引擎的只读查询当场回答。"""
        self._require_open_decision()
        if action in _WORLD_TOOL_MODELS:
            return self._submit_life_action(action, args)
        if action == "recall_task_summary":
            data = self.engine.read_query(self._context, action, _validate_request(REQUEST_MODELS[action], args))
            return ReactToolResult(True, data)
        if action == "search_memory":
            return self._search_memory(args)
        raise RuntimeError(f"Unsupported LifeAgent tool dispatch: {action}")

    def _search_memory(self, args: dict[str, Any]) -> ReactToolResult:
        """@brief 检索记忆：本次决策前三次送引擎的只读查询，第四次起当场以 MEMORY_SEARCH_LIMIT 回答、不送引擎；
        回答带 error_code 时 ok 为假，宿主据此把明细路径改写到实际调用位置。"""
        if self._memory_searches >= MEMORY_SEARCHES_PER_DECISION:
            logging.error("Memory search limit reached for this decision")
            data = query_rejection(refusal_for_code("MEMORY_SEARCH_LIMIT").error())
            return ReactToolResult(False, data)
        self._memory_searches += 1
        request = _validate_request(QUERY_MODELS["search_memory"], args)
        data = self.engine.read_query(self._context, "search_memory", request)
        return ReactToolResult("error_code" not in data, data)

    def _submit_life_action(self, action: str, args: dict[str, Any]) -> ReactToolResult:
        """@brief 注入宿主身份并向引擎提交一个有类型请求；动作身份的序号是它在本回复世界请求中的位置，从 1 起。"""
        self._require_open_decision()
        request = _validate_request(REQUEST_MODELS[action], args)
        context = self._context
        error = self._prepared_action_error(action, request)
        if error is not None:
            raise RuntimeError(error.text)
        self._action_count += 1
        action_id = f"{context.decision_id}:action:{self._action_count:04d}"
        receipt = self.engine.submit_action(DirectToolCall(context, action_id, action, request))
        validated = ActionReceipt.model_validate(receipt)
        return ReactToolResult(True, validated.model_dump(mode="json"))

    def _require_open_decision(self) -> None:
        """@brief 本回复的世界请求全部登记后本次决策已经结束，之后不再派发任何工具。"""
        if self._action_count and self._action_count >= self._world_calls:
            logging.error("Tool dispatch attempted after the world request ended this decision")
            raise RuntimeError("Life action was already submitted")

    def _tool_message(self, decision: ReactDecision, result: ReactToolResult) -> dict[str, Any] | None:
        """@brief 世界请求登记后不追加任何 tool 消息（返回 None）；只读查询的结果只序列化一次写进 tool 消息。

        宿主完整保留回执；查询失败保留五字段结构和实际调用位置。
        """
        if decision.action in _WORLD_TOOL_MODELS:
            return None
        payload = result.data
        if not result.ok:
            index = self._call_positions[decision.tool_call_id]
            payload = {**payload, "items": [{**item, "path": ["tool_calls", index, *item["path"][2:]]}
                                           for item in payload["items"]]}
        return {"role": "tool", "tool_call_id": decision.tool_call_id,
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}


def _validate_request(model: type[BaseModel], values: dict[str, Any]) -> BaseModel:
    """@brief 用严格 JSON 模式验证模型产生的工具参数。"""
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return model.model_validate_json(encoded, strict=True)


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


def _relationship_persons(data: dict[str, Any]) -> list[str]:
    """@brief 观察私有证据 persona_views 里已有关系看法的人物（只用于已知人物，不进任何提示层）。"""
    views = data.get("persona_views")
    rows = views.get("relationships") if isinstance(views, dict) else None
    if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("person"), str) for row in rows):
        logging.error("LifeAgent persona views are invalid")
        raise RuntimeError("LifeAgent persona views are invalid")
    return [row["person"] for row in rows]


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
    """@brief 把 conclude_activity 逐项的记忆与看法展平为本步写入的记忆，原样交出 control_items 读出的存储形状
    （每项先 memories、后 views，看法带 subject）；其余世界请求不写记忆。

    来源、认知状态与去掉 subject 都由记忆存储按写入点写定，模型不填、不选任何身份。
    """
    if action != "conclude_activity":
        return []
    request = _validate_request(ConcludeActivityRequest, args)
    return [memory for item in control_items(action, request) for memory in item.memories]


def _visible_names(data: dict[str, Any]) -> dict[str, Any]:
    """@brief 冻结本轮本地校验所用的具体动作、对象、活动、回看与个人事务名称。

    两个开始工具的对象全集由 name_book 从本轮私有绑定与冻结地点表构建，引擎受理使用同一观察。
    缺失或形状不符的绑定是损坏的宿主观察。回看名称是待办层写出的目标文字、活动名与
    记忆层「今天」自述前缀所写的活动标注（活动绑定的 note_labels）；事务名不进回看名称。个人事务变更的本地校验读两类私有事务
    绑定原样的行：errand_bindings（本人未完成事务的名称、事务身份、结果种类与商品身份）与 errand_item_bindings（全部
    商品名到商品身份），与引擎复核读同一份冻结观察（docs/design/04-simulation-engine.md）。
    """
    objects = _binding_objects(data.get("interaction_bindings"))
    goals = _binding_names(data, "goal_bindings", "task_name")
    activities = _binding_names(data, "activity_bindings", "name")
    labels = _note_labels(data["activity_bindings"])
    return {
        "objects": objects,
        "activity_names": activities,
        "recall_names": (*goals, *activities, *labels),
        "book": name_book(data),
        "errand_bindings": _binding_rows(data, "errand_bindings", ("name", "errand_id", "product_id")),
        "errand_item_bindings": _binding_rows(data, "errand_item_bindings", ("name", "product_id")),
    }


def _binding_objects(rows: Any) -> dict[str, tuple[str, ...]]:
    """@brief 交互对象私有绑定：可读名称到该对象提供的具体动作。"""
    if not isinstance(rows, list):
        raise RuntimeError("LifeAgent interaction bindings must be a list")
    objects: dict[str, tuple[str, ...]] = {}
    for row in rows:
        name = row.get("object") if isinstance(row, dict) else None
        actions = row.get("actions") if isinstance(row, dict) else None
        if (not isinstance(name, str) or not name.strip() or name in objects or not isinstance(actions, list)
                or not isinstance(row.get("kind"), str)):
            raise RuntimeError("LifeAgent interaction binding identity is invalid")
        objects[name] = tuple(_binding_action(item) for item in actions)
    return objects


def _binding_action(item: Any) -> str:
    """@brief 私有绑定的一项具体动作。"""
    value = item.get("action") if isinstance(item, dict) else None
    if not isinstance(value, str) or not value:
        logging.error("LifeAgent interaction binding action is invalid")
        raise RuntimeError("LifeAgent interaction binding action is invalid")
    return value


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
    """@brief 活动绑定的 note_labels：记忆层「今天」里「标注：」「进展·标注：」前缀所写的活动标注（只作回看名称）。"""
    if any(not isinstance(row.get("note_labels"), list)
           or any(not isinstance(label, str) for label in row["note_labels"]) for row in rows):
        raise RuntimeError("LifeAgent activity_bindings are invalid")
    return tuple(dict.fromkeys(label for row in rows for label in row["note_labels"]))


def _validate_prepared_action(action: str, request: BaseModel, names: dict[str, Any]) -> ProtocolFeedback | None:
    """@brief 用同一冻结观察的私有绑定核对工具参数中的名称（docs/design/06-agent.md「提示与工具」）：精确或
    无损归一后唯一即受理（docs/design/04-simulation-engine.md，worldmock.life.name_resolution），找不到
    的逐项写成
    NAME_NOT_FOUND、归一后不止一个的写成 NAME_AMBIGUOUS（第四章）；控制工具的活动名、结束工具关系看法的对象与请求级
    个人事务变更的问题一并逐项报全部。"""
    if action in START_TOOLS:
        return _validate_start(action, request, names)
    if action in CONTROL_TOOLS:
        return _control_feedback(action, request, names)
    if action == "recall_task_summary":
        return _unknown_names(("task_name", "任务、目标或活动"), [request.task_name], names["recall_names"],
                              "task_name is not visible")
    return None


def _control_feedback(action: str, request: BaseModel, names: dict[str, Any]) -> ProtocolFeedback | None:
    """@brief 控制工具（播报、暂停、结束）逐项核对名称与请求级个人事务变更，全部问题合并成一次纠错
    （docs/design/04-simulation-engine.md；
    docs/design/04-simulation-engine.md）：各项的活动名
    （NAME_NOT_FOUND、NAME_AMBIGUOUS）、各项关系看法的对象（SUBJECT_NOT_KNOWN，只有结束工具带看法），再是事务变更在冻结
    观察私有事务绑定上的问题——与引擎复核同一函数 errands.change_issues（更新或取消的名称找不到或有歧义、同一事务被点名
    两次、商品名解析不到唯一商品、新增重名、同结果同商品、超过上限），路径到 errands[j].<键>、不带候选。明细按此先后
    排列，码取第一项的码；都成立时为 None。请求经唯一读取入口 control_items 与 control_errands 读出。"""
    items = control_items(action, request)
    total = len(items)
    errand_issues = errands.change_issues(control_errands(action, request), names)
    issues = [*_name_issues(("activity_name", "活动"), [item.activity_name for item in items], names["activity_names"],
                            f"{action} activity_name names an activity that is not visible"),
              *_subject_issues(items, names["known_persons"]),
              *(HostIssue(item, sentence, sentence) for item, sentence in errand_issues)]
    return merged_feedback(issues, total, _name_fixes(total)) if issues else None


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


def _batch_error(decisions: list[ReactDecision], submitted: int, available: tuple = ()) -> ProtocolFeedback | None:
    """@brief 合法的批次只有三种：全部是只读查询；恰好一个世界请求；两个开始工具各一次。其余整批原子拒绝。"""
    business = [item.action for item in decisions if item.action in _WORLD_TOOL_MODELS]
    queries = bool(decisions) and not business
    single = len(business) == 1 and len(decisions) == 1
    group = len(decisions) == 2 and sorted(business) == sorted(START_TOOLS)
    if (queries or single or group) and not submitted:
        return None
    reason = ("Submit one world request, start_main_activity together with start_side_activity, "
              "or a batch containing only read queries.")
    item = ErrorItem(("tool_calls",), [item.action for item in decisions], "TOOL_CALL_REQUIRED", available,
                     reason, "The call combination violates atomic batch rules.")
    return host_feedback(host_report("TOOL_CALL_REQUIRED", reason, items=(item,)), reason)


def _unknown_names(field: tuple[str, str], written: list[str], visible, detail: str) -> ProtocolFeedback | None:
    """@brief 名称类参数逐项解析后的纠错（_name_issues），逐项全部报出、合并成一次；都成立时为 None。"""
    issues = _name_issues(field, written, visible, detail)
    return merged_feedback(issues, len(written), _name_fixes(len(written))) if issues else None


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


def _validate_start(tool: str, request: BaseModel, names: dict[str, Any]) -> ProtocolFeedback | None:
    """@brief 一条开始调用的对象名与 detail 在冻结观察上解析，所有独立的名称错误原子合并。"""
    outcome = resolve_start(names["book"], tool, request)
    issues = [HostIssue(failure.item, failure.sentence, failure.detail) for failure in outcome.failures]
    return merged_feedback(issues, len(issues)) if issues else None


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


def life_tool_schema_sha256() -> str:
    """@brief 返回正式生活决策闭集工具 Schema 的稳定 SHA-256。"""
    schemas = [_tool_schema(name, _TOOL_MODELS[name]) for name in _TOOL_MODELS]
    encoded = json.dumps(
        {"tool_schemas": schemas},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _combine_reports(reports: list[ErrorReport]) -> ProtocolFeedback:
    """@brief 保留批内全部独立明细，一次纠正后整体重交；改法取各份报告各自的改法，互不相同的依次相连。"""
    items = tuple(item for report in reports for item in report.items)
    reason = "; ".join(dict.fromkeys(report.reason for report in reports))
    fix = "; ".join(dict.fromkeys(report.fix for report in reports if report.fix)) or None
    report = ErrorReport(reports[0].error_code, reason, fix,
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
            decisions.append((index, ReactDecision(name, parsed, identity)))
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
