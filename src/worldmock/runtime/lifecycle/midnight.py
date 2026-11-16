"""@brief 零点的两个后台任务：日、月、年回顾与零点计划；运行宿主在每个共同检查点之后、零点推进之后与普通恢复启动时调用（docs/design/10-runtime.md「决策循环」）。"""

import logging
import time
from collections.abc import Mapping
from typing import Any

from worldmock.configuration.model.model_client import ModelClient, collect_transport_attempts
from worldmock.configuration.model.model_metadata import IndependentModelClient
from worldmock.foundation.errors import ProtocolError
from worldmock.recording_engine.storage.opslog import transport_wait_record
from worldmock.simulation_engine.evidence import memory_digest
from worldmock.simulation_engine.scheduling.plans import day_planning
from worldmock.simulation_engine.scheduling.plans.day_plan_inputs import plan_inputs
from worldmock.state.model.memory import MEMORY_DIGEST_TIERS

logger = logging.getLogger(__name__)


class PlanDayClient(IndependentModelClient):
    """@brief Plan day 业务客户端，协议重试仍由业务调用方计数。"""

    def __init__(self, model_client: ModelClient, model_identity: Mapping[str, Any]):
        """@brief 包装整次运行唯一的模型客户端，用途与冻结身份取自运行清单。"""
        super().__init__(model_client, model_identity, "Plan day")


async def settle_pending_day_plans(engine: Any, client: ModelClient) -> list[dict]:
    """@brief 零点计划后台任务（docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）：为缺当日计划的人物生成当日计划。

    运行宿主在每个共同检查点之后、零点的回顾任务之后与普通恢复启动时 await 它。不经 LifeAgent，
    不读写人物工作区与记忆运行时，不产生 observation、decision_started、memory_updated。按
    plan_submitted_dates 找出缺当日计划的人物（按编号），运行已到终点或无人缺计划时在构造业务客户端之前返回。
    输出不合法时附回英文错误重试，至多 3 次；重试耗尽或传输失败时先提交已有尝试的证据再抛出（运行故障）。
    """
    missing = day_planning.agents_missing_plan(engine.context.state, engine.context.config.timezone)
    if not missing:
        return []
    caller = PlanDayClient(client, engine.context.store.manifest["stage_model_identity"])
    registered = []
    for agent_id in missing:
        registered.append(await _generate_day_plan(engine, caller, agent_id))
    return registered


async def _generate_day_plan(engine: Any, caller: Any, agent_id: int) -> dict:
    """@brief 为一个人物生成并登记当日计划：请求与响应逐次留证，合法输出经引擎系统登记入口与证据一次提交。"""
    now, timezone_name = engine.context.state.current_time, engine.context.config.timezone
    inputs = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, agent_id)
    sentences = [sentence for sentence, _ in inputs.experiences]
    original, kept = day_planning.fit_plan_day(
        inputs.payload, sentences, caller.model_name, engine.context.inputs.model_metadata)
    sources = [*(source for _, source in inputs.experiences[len(sentences) - kept:]), *inputs.sources]
    date = day_planning.plan_date(now, timezone_name)
    request, evidence, previous, attempt = original, [], None, 1
    while True:
        call_id = f"plan-day:{agent_id}:{date}:attempt:{attempt}"
        evidence.append(day_planning.request_draft(now, agent_id, (call_id, attempt, previous), request))
        link = (call_id, attempt, evidence[-1].event_id)
        raw, attempts = await _call_plan_day(engine, caller, request, (evidence, link, agent_id))
        try:
            parsed = day_planning.parse_plan_day(raw, now, timezone_name)
        except day_planning.PlanDayOutputError as error:
            evidence.append(day_planning.response_draft(now, agent_id, (*link, attempts), raw, error))
            if attempt > engine.context.config.model.day_plan_validation_max_retries:
                engine.context.store.append_evidence(evidence)
                raise ProtocolError("plan_day output validation retries exhausted") from error
            attempt, previous = attempt + 1, evidence[-1].event_id
            request = day_planning.prepare_plan_day_correction(original, raw, error)
            continue
        evidence.append(day_planning.response_draft(now, agent_id, (*link, attempts), raw, None))
        break
    plan = engine.register_day_plan(agent_id, parsed, sources, evidence)
    return {"agent_id": agent_id, "date": date, "goals": len(plan.goals)}


async def _call_plan_day(engine: Any, caller: Any, request: dict,
                         context: tuple[list, tuple, int]) -> tuple[dict, tuple[dict, ...]]:
    """@brief 发出一次零点计划请求，返回原始响应与这次调用的失败发送，并写调用耗时运维日志；失败时先提交已有尝试的证据与带失败发送的
    失败证据再抛出。"""
    evidence, link, agent_id = context
    now, started = engine.context.state.current_time, time.perf_counter()
    with collect_transport_attempts() as collected:
        try:
            raw = await caller.call(request)
        except BaseException as error:
            logger.error("Plan day model request failed: %s", type(error).__name__)
            _log_plan_day_call(engine, (link[0], agent_id), started, tuple(collected), "failure")
            evidence.append(day_planning.failure_draft(now, agent_id, (*link, tuple(collected)), error))
            engine.context.store.append_evidence(evidence)
            raise
    _log_plan_day_call(engine, (link[0], agent_id), started, tuple(collected), "response")
    return raw, tuple(collected)


def _log_plan_day_call(engine: Any, ids: tuple[str, int], started: float, attempts: tuple, outcome: str) -> None:
    """@brief 零点计划调用的运维日志：调用耗时毫秒数与结果；有失败发送时另写一条传输等待记录。"""
    call_id, agent_id = ids
    ops, now = engine.context.ops, engine.context.state.current_time
    ops.log(now, "agent_runtime", "error" if outcome == "failure" else "info",
            {"code": "PLAN_DAY_CALL", "detail": f"Plan day call {call_id} ended with {outcome}",
             "agent_id": agent_id, "call_id": call_id, "outcome": outcome,
             "call_ms": round((time.perf_counter() - started) * 1000, 1)})
    if attempts:
        ops.log(now, "agent_runtime", "warn", {**transport_wait_record(call_id, attempts), "agent_id": agent_id})


class MemoryDigestClient(IndependentModelClient):
    """@brief 日、月、年回顾的业务客户端，校验重试仍由业务调用方计数。"""

    def __init__(self, model_client: ModelClient, model_identity: Mapping[str, Any]):
        """@brief 包装整次运行唯一的模型客户端，用途与冻结身份取自运行清单。"""
        super().__init__(model_client, model_identity, "Memory digest")


async def settle_pending_memory_digests(engine: Any, client: ModelClient) -> list[dict]:
    """@brief 回顾后台任务（docs/design/06-agent.md「日、月、年回顾」）：为每个人物生成待生成的各期回顾。

    运行宿主在零点推进之后、零点计划之前，每个共同检查点之后与普通恢复启动时 await 它。不经 LifeAgent，不读写人物工作区与
    记忆运行时，不产生 observation、decision_started、memory_updated。运行已到终点时在构造业务客户端之前返回；按人物编号、
    再按级取待生成的期次，第一次需要调用时才构造业务客户端。校验重试用尽时没通过的类记为 null 并列入 fallback，运行继续；
    传输失败时先提交已有尝试的证据再抛出（运行故障）。
    @return 每期一项 {"agent_id", "tier", "period", "fallback"}
    """
    state = engine.context.state
    if state.current_time >= state.end_time:
        return []
    caller, registered = None, []
    for agent_id in sorted(state.agents):
        for tier in MEMORY_DIGEST_TIERS:
            for inputs in memory_digest.pending_inputs(engine.context, engine.observations, agent_id, tier):
                if caller is None:
                    caller = MemoryDigestClient(client, engine.context.store.manifest["stage_model_identity"])
                registered.append(await _generate_memory_digest(engine, caller, agent_id, inputs))
    return registered


async def _generate_memory_digest(engine: Any, caller: Any, agent_id: int, inputs: Any) -> dict:
    """@brief 生成并登记一期回顾：请求与响应逐次留证，每次尝试只对仍未接受的类报错；尝试用尽时没通过的类列入 fallback，
    最后经引擎系统登记入口与全部证据一次提交。"""
    now, period = engine.context.state.current_time, inputs.period
    original = memory_digest.prepare_digest_request(inputs, caller.model_name, engine.context.inputs.model_metadata)
    request, evidence, previous, accepted = original, [], None, {}
    for attempt in range(1, engine.context.config.model.reflection_validation_max_retries + 2):
        call_id = period.call_id(agent_id, attempt)
        evidence.append(memory_digest.request_draft(now, agent_id, (call_id, attempt, previous), request))
        link = (call_id, attempt, evidence[-1].event_id)
        raw, attempts = await _call_memory_digest(engine, caller, request, (evidence, link, agent_id))
        accepted, error = memory_digest.check_attempt(inputs, raw, accepted)
        evidence.append(memory_digest.response_draft(now, agent_id, (*link, attempts), raw, error))
        if error is None:
            break
        logger.error("Memory digest call %s failed validation", call_id)
        previous = evidence[-1].event_id
        request = memory_digest.prepare_digest_correction(original, raw, error)
    outcome = memory_digest.digest_outcome(inputs, accepted)
    if error is not None:
        logger.error("Memory digest validation retries exhausted for %s; fallback categories: %s",
                     call_id.rpartition(":attempt:")[0], ",".join(outcome.fallback))
    engine.register_memory_digest(agent_id, outcome, evidence)
    return {"agent_id": agent_id, "tier": period.tier, "period": period.period, "fallback": list(outcome.fallback)}


async def _call_memory_digest(engine: Any, caller: Any, request: dict,
                              context: tuple[list, tuple, int]) -> tuple[dict, tuple[dict, ...]]:
    """@brief 发出一次回顾请求，返回原始响应与这次调用的失败发送，并写调用耗时运维日志；失败时先提交已有尝试的证据与带失败
    发送的失败证据再抛出。"""
    evidence, link, agent_id = context
    now, started = engine.context.state.current_time, time.perf_counter()
    with collect_transport_attempts() as collected:
        try:
            raw = await caller.call(request)
        except BaseException as error:
            logger.error("Memory digest model request failed: %s", type(error).__name__)
            _log_memory_digest_call(engine, (link[0], agent_id), started, tuple(collected), "failure")
            evidence.append(memory_digest.failure_draft(now, agent_id, (*link, tuple(collected)), error))
            engine.context.store.append_evidence(evidence)
            raise
    _log_memory_digest_call(engine, (link[0], agent_id), started, tuple(collected), "response")
    return raw, tuple(collected)


def _log_memory_digest_call(engine: Any, ids: tuple[str, int], started: float, attempts: tuple, outcome: str) -> None:
    """@brief 回顾调用的运维日志：调用耗时毫秒数与结果；有失败发送时另写一条传输等待记录。"""
    call_id, agent_id = ids
    ops, now = engine.context.ops, engine.context.state.current_time
    ops.log(now, "agent_runtime", "error" if outcome == "failure" else "info",
            {"code": "MEMORY_DIGEST_CALL", "detail": f"Memory digest call {call_id} ended with {outcome}",
             "agent_id": agent_id, "call_id": call_id, "outcome": outcome,
             "call_ms": round((time.perf_counter() - started) * 1000, 1)})
    if attempts:
        ops.log(now, "agent_runtime", "warn", {**transport_wait_record(call_id, attempts), "agent_id": agent_id})
