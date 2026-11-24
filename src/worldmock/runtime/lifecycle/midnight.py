"""@brief 零点的两个后台任务：日、月、年回顾与零点计划；经模型注册表按阶段与人物绑定并发执行。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from worldmock.agents.prompt.prompt_capacity import CONTEXT_THRESHOLD, model_context_length, request_token_count
from worldmock.configuration.model.model_client import ModelClient, collect_transport_attempts
from worldmock.configuration.model.model_metadata import IndependentModelClient
from worldmock.configuration.model.model_registry import ModelRegistry, ResolvedModelBinding
from worldmock.foundation.errors import ProtocolError
from worldmock.recording_engine.storage.opslog import transport_wait_record
from worldmock.simulation_engine.evidence import memory_digest
from worldmock.simulation_engine.scheduling.plans import day_planning
from worldmock.simulation_engine.scheduling.plans.day_plan_inputs import plan_inputs
from worldmock.state.model.memory import MEMORY_DIGEST_TIERS

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _CallSpec:
    """@brief 单次后台模型调用的日志与失败存证上下文。"""

    agent_id: int
    call_id: str
    attempt: int
    request_event_id: str
    code: str
    label: str
    failure_factory: Callable[[int, int, tuple[Any, ...], BaseException], Any]


@dataclass(frozen=True, slots=True)
class _PreparedDayPlan:
    """@brief 单个人物并发生成的当日计划与待提交证据。"""

    agent_id: int
    date: str
    parsed: Any
    sources: list[str]
    evidence: list[Any]


@dataclass(frozen=True, slots=True)
class _PreparedMemoryDigest:
    """@brief 单个人物一期并发生成的回顾结果与待提交证据。"""

    agent_id: int
    tier: str
    period: str
    outcome: Any
    evidence: list[Any]
    exhausted_prefix: str | None


class PlanDayClient(IndependentModelClient):
    """@brief Plan day 业务客户端，协议重试仍由业务调用方计数。"""

    def __init__(self, model_client: ModelClient, model_identity: Mapping[str, Any]):
        """@brief 包装模型客户端，用途与请求模型名由阶段绑定提供。"""
        super().__init__(model_client, model_identity, "Plan day")


class MemoryDigestClient(IndependentModelClient):
    """@brief 日、月、年回顾的业务客户端，校验重试仍由业务调用方计数。"""

    def __init__(self, model_client: ModelClient, model_identity: Mapping[str, Any]):
        """@brief 包装模型客户端，用途与请求模型名由阶段绑定提供。"""
        super().__init__(model_client, model_identity, "Memory digest")


async def settle_pending_day_plans(engine: Any, registry: ModelRegistry) -> list[dict[str, Any]]:
    """@brief 按人物编号升序并发生成缺当日计划的请求，完成后按编号升序顺序登记。"""
    missing = day_planning.agents_missing_plan(engine.context.state, engine.context.config.timezone)
    if not missing:
        return []
    prepared = await asyncio.gather(
        *(_prepare_day_plan(engine, agent_id, registry.resolve("plan_day", agent_id)) for agent_id in missing)
    )
    registered: list[dict[str, Any]] = []
    for item in prepared:
        plan = engine.register_day_plan(item.agent_id, item.parsed, item.sources, item.evidence)
        registered.append({"agent_id": item.agent_id, "date": item.date, "goals": len(plan.goals)})
    return registered


async def settle_pending_memory_digests(engine: Any, registry: ModelRegistry) -> list[dict[str, Any]]:
    """@brief 按日、月、年逐级并发生成待登记回顾，并在每级内按人物与期次升序顺序提交。"""
    state = engine.context.state
    if state.current_time >= state.end_time:
        return []
    registered: list[dict[str, Any]] = []
    for tier in MEMORY_DIGEST_TIERS:
        tasks = [
            (agent_id, inputs)
            for agent_id in sorted(state.agents)
            for inputs in memory_digest.pending_inputs(engine.context, engine.observations, agent_id, tier)
        ]
        if not tasks:
            continue
        prepared = await asyncio.gather(
            *(_prepare_memory_digest(engine, agent_id, inputs, registry.resolve("digest", agent_id))
              for agent_id, inputs in tasks)
        )
        registered.extend(_commit_memory_digest(engine, item) for item in prepared)
    return registered


async def _prepare_day_plan(engine: Any, agent_id: int, binding: ResolvedModelBinding) -> _PreparedDayPlan:
    """@brief 为单个人物生成并本地校验当日计划；重试耗尽先写证据再抛协议异常。"""
    caller = PlanDayClient(binding.client, {"requested_model": binding.profile.requested_model})
    now, timezone_name = engine.context.state.current_time, engine.context.config.timezone
    inputs = plan_inputs(engine.context, engine.observations, bool(engine.midnight_rhythm_pending), agent_id)
    original, sources = _fit_plan_day_with_binding(inputs, binding)
    date = day_planning.plan_date(now, timezone_name)
    request, evidence, previous, attempt = original, [], None, 1
    while True:
        call_id = f"plan-day:{agent_id}:{date}:attempt:{attempt}"
        evidence.append(day_planning.request_draft(now, agent_id, (call_id, attempt, previous), request))
        spec = _CallSpec(agent_id, call_id, attempt, evidence[-1].event_id,
                         "PLAN_DAY_CALL", "Plan day", day_planning.failure_draft)
        raw, attempts = await _call_background_model(engine, caller, request, evidence, spec)
        try:
            parsed = day_planning.parse_plan_day(raw, now, timezone_name)
        except day_planning.PlanDayOutputError as error:
            evidence.append(day_planning.response_draft(now, agent_id,
                                                        (call_id, attempt, spec.request_event_id, attempts),
                                                        raw, error))
            if attempt > binding.profile.day_plan_validation_max_retries:
                logger.error("Plan day validation retries exhausted for agent %s", agent_id)
                engine.context.store.append_evidence(evidence)
                raise ProtocolError("plan_day output validation retries exhausted") from error
            attempt, previous = attempt + 1, evidence[-1].event_id
            request = day_planning.prepare_plan_day_correction(original, raw, error)
            continue
        evidence.append(day_planning.response_draft(now, agent_id, (call_id, attempt, spec.request_event_id, attempts),
                                                    raw, None))
        return _PreparedDayPlan(agent_id, date, parsed, sources, evidence)


def _fit_plan_day_with_binding(inputs: Any, binding: ResolvedModelBinding) -> tuple[dict[str, Any], list[str]]:
    """@brief 按人物绑定的输出上限与思考开关构造并检查日计划请求。"""
    sentences = [sentence for sentence, _ in inputs.experiences]
    original, kept = day_planning.fit_plan_day(
        inputs.payload, sentences, binding.profile.requested_model, binding.metadata,
    )
    _apply_binding_options(original, binding)
    _ensure_binding_capacity(original, binding, "plan_day request capacity exceeded")
    sources = [*(source for _, source in inputs.experiences[len(sentences) - kept:]), *inputs.sources]
    return original, sources


async def _prepare_memory_digest(
    engine: Any, agent_id: int, inputs: Any, binding: ResolvedModelBinding,
) -> _PreparedMemoryDigest:
    """@brief 为单个人物生成并校验一期回顾；超限耗尽仍生成 fallback 结果交由主线程提交。"""
    caller = MemoryDigestClient(binding.client, {"requested_model": binding.profile.requested_model})
    now, period = engine.context.state.current_time, inputs.period
    original = memory_digest.prepare_digest_request(inputs, caller.model_name, binding.metadata)
    _apply_binding_options(original, binding)
    _ensure_binding_capacity(original, binding, "memory digest request capacity exceeded")
    request, evidence, previous, accepted, error, call_id = original, [], None, {}, None, ""
    for attempt in range(1, binding.profile.reflection_validation_max_retries + 2):
        call_id = period.call_id(agent_id, attempt)
        evidence.append(memory_digest.request_draft(now, agent_id, (call_id, attempt, previous), request))
        spec = _CallSpec(agent_id, call_id, attempt, evidence[-1].event_id,
                         "MEMORY_DIGEST_CALL", "Memory digest", memory_digest.failure_draft)
        raw, attempts = await _call_background_model(engine, caller, request, evidence, spec)
        accepted, error = memory_digest.check_attempt(inputs, raw, accepted)
        evidence.append(memory_digest.response_draft(now, agent_id, (call_id, attempt, spec.request_event_id, attempts),
                                                     raw, error))
        if error is None:
            break
        logger.error("Memory digest call %s failed validation", call_id)
        previous = evidence[-1].event_id
        request = memory_digest.prepare_digest_correction(original, raw, error)
    outcome = memory_digest.digest_outcome(inputs, accepted)
    exhausted = call_id.rpartition(":attempt:")[0] if error is not None else None
    return _PreparedMemoryDigest(agent_id, period.tier, period.period, outcome, evidence, exhausted)


def _commit_memory_digest(engine: Any, item: _PreparedMemoryDigest) -> dict[str, Any]:
    """@brief 主线程按序提交单个回顾与证据并记录校验耗尽告警。"""
    if item.exhausted_prefix is not None:
        logger.error("Memory digest validation retries exhausted for %s; fallback categories: %s",
                     item.exhausted_prefix, ",".join(item.outcome.fallback))
    engine.register_memory_digest(item.agent_id, item.outcome, item.evidence)
    return {"agent_id": item.agent_id, "tier": item.tier,
            "period": item.period, "fallback": list(item.outcome.fallback)}


async def _call_background_model(
    engine: Any, caller: Any, request: dict[str, Any], evidence: list[Any], spec: _CallSpec,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """@brief 执行一次后台模型调用并记录运维耗时；传输或协议失败时先提交已有证据。"""
    now, started = engine.context.state.current_time, time.perf_counter()
    with collect_transport_attempts() as collected:
        try:
            raw = await caller.call(request)
        except BaseException as error:
            logger.error("%s model request failed: %s", spec.label, type(error).__name__)
            _log_background_call(engine, spec, started, tuple(collected), "failure")
            evidence.append(spec.failure_factory(
                now, spec.agent_id, (spec.call_id, spec.attempt, spec.request_event_id, tuple(collected)), error,
            ))
            engine.context.store.append_evidence(evidence)
            raise
    _log_background_call(engine, spec, started, tuple(collected), "response")
    return raw, tuple(collected)


def _log_background_call(
    engine: Any, spec: _CallSpec, started: float, attempts: tuple[dict[str, Any], ...], outcome: str,
) -> None:
    """@brief 写入后台模型调用耗时与传输重试运维日志。"""
    ops, now = engine.context.ops, engine.context.state.current_time
    ops.log(now, "agent_runtime", "error" if outcome == "failure" else "info", {
        "code": spec.code, "detail": f"{spec.label} call {spec.call_id} ended with {outcome}",
        "agent_id": spec.agent_id, "call_id": spec.call_id, "outcome": outcome,
        "call_ms": round((time.perf_counter() - started) * 1000, 1),
    })
    if attempts:
        ops.log(now, "agent_runtime", "warn", {**transport_wait_record(spec.call_id, attempts),
                                             "agent_id": spec.agent_id})


def _apply_binding_options(request: dict[str, Any], binding: ResolvedModelBinding) -> None:
    """@brief 按绑定模型档覆写最大输出令牌数与思考模式开关。"""
    request["max_tokens"] = binding.effective_max_tokens
    request["extra_body"] = {"thinking": {"type": "enabled" if binding.profile.thinking else "disabled"}}


def _ensure_binding_capacity(request: dict[str, Any], binding: ResolvedModelBinding, message: str) -> None:
    """@brief 按绑定输出上限复核请求总容量是否低于 85% 上下文阈值。"""
    limit = CONTEXT_THRESHOLD * model_context_length(binding.profile.requested_model, binding.metadata)
    if request_token_count(request) > limit:
        logger.error("Background model request exceeded context capacity: %s", message)
        raise RuntimeError(message)
