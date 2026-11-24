"""@brief 运行宿主 RunHost：驱动全局离散时钟屏障、并发唤醒与搜索子会话及共同检查点（docs/design/10-runtime.md）。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from worldmock.agents.life_agent import LifeAgent, PersonInputs
from worldmock.agents.memory.life_memory import MemoryStep, MemoryStepOutcome, save_step
from worldmock.agents.memory.workspace import create_workspaces, memory_stores, snapshot_workspaces
from worldmock.agents.search.search_session import SearchSessionRunner
from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.configuration.model.model_registry import ModelRegistry
from worldmock.foundation.errors import ProtocolError
from worldmock.foundation.time.readable import _datetime
from worldmock.recording_engine.events.memory_evidence import memory_write_points
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.storage.opslog import transport_wait_record
from worldmock.runtime.lifecycle.midnight import settle_pending_day_plans, settle_pending_memory_digests
from worldmock.runtime.monitoring.status import write_status
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.state.decisions.barrier import AgentWakeupInput, BarrierCommitReceipt, SandboxOutcome, SearchTaskSpec
from worldmock.state.decisions.results import CounterfactualIntervention, CounterfactualReceipt
from worldmock.state.decisions.round import SchedulingState
from worldmock.state.identity import RunIdentity
from worldmock.state.model.memory import valid_subjective_snapshots

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _BarrierJob:
    """@brief 单项并发屏障任务的身份、草稿源与协程对象。"""

    agent_id: int
    wakeup_id: str
    source: Any
    coro: Coroutine[Any, Any, Any]


def _stop(message: str) -> NoReturn:
    """@brief 宿主核对失败：先写同文错误日志，再抛 RuntimeError。"""
    logger.error("%s", message)
    raise RuntimeError(message)


def _collect_failed_drafts(source: Any, error: BaseException) -> tuple[Any, ...]:
    """@brief 提取并发任务异常中止时尚未落盘的模型与沙盒草稿。"""
    error_drafts = getattr(error, "evidence_drafts", None)
    if error_drafts:
        return tuple(error_drafts)
    drain = getattr(source, "drain_evidence", None)
    if callable(drain):
        return tuple(drain())
    source_drafts = getattr(source, "evidence_drafts", None)
    return tuple(source_drafts) if source_drafts else ()


class RunHost:
    """@brief 运行宿主：单事件循环内并发驱动搜索子会话与人物唤醒，主线程同步提交引擎并落盘记忆。"""

    def __init__(self, run_dir: Path, inputs: EnvironmentInputs, registry: ModelRegistry,
                 agents: Mapping[int, PersonInputs],
                 memory_records: Mapping[int, Sequence[Mapping[str, Any]]]) -> None:
        """@brief 绑定运行目录、冻结环境、模型注册表、全员冻结输入与记忆存储；引擎由启动方法建立。"""
        self.run_dir = Path(run_dir)
        self.engine: LifeEngine | None = None
        self.step_count = 0
        self._inputs = inputs
        self._registry = registry
        self._agents = dict(agents)
        self._memories = memory_stores(self.run_dir, memory_records)

    @property
    def current_time(self) -> datetime:
        """@brief 引擎当前时刻（场景时区）；宿主不保存第二份时钟。"""
        return _datetime(self.engine.context.state.current_time, self.engine.context.config.timezone)

    async def start_new(self, identity: RunIdentity, create_options: dict[str, Any]) -> None:
        """@brief 新建：建全员工作区，建引擎，核对起始时刻，写初始检查点并补零点任务。"""
        create_workspaces(self.run_dir, identity.agent_ids)
        self.engine = LifeEngine.create(
            self._inputs, self.run_dir, identity, identity.model_identity, create_options,
        )
        self._require_start_time()
        try:
            await self._checkpoint()
        except Exception as error:
            self._record_failure(error)
            raise

    async def start_resume(self) -> None:
        """@brief 普通恢复：打开引擎，在首次唤醒之前补全员回顾与零点计划，不写检查点。"""
        self.engine = LifeEngine.open(self._inputs, self.run_dir)
        try:
            await self._settle_midnight_tasks()
        except Exception as error:
            self._record_failure(error)
            raise

    async def start_counterfactual(self, intervention: CounterfactualIntervention,
                                   intervention_sha256: str) -> CounterfactualReceipt:
        """@brief 反事实：打开引擎，施加并核对干预，写干预检查点并补零点任务。"""
        self.engine = LifeEngine.open(self._inputs, self.run_dir)
        try:
            receipt = self._apply_counterfactual(intervention, intervention_sha256)
            await self._checkpoint()
        except Exception as error:
            self._record_failure(error)
            raise
        return receipt

    async def run(self) -> None:
        """@brief 反复执行屏障步直到引擎时钟到达结束时刻；运行故障登记后原样传播。"""
        try:
            while self.engine.context.state.current_time < self.engine.context.state.end_time:
                await self.step()
        except Exception as error:
            self._record_failure(error)
            raise

    async def step(self) -> None:
        """@brief 一步：先处理到期搜索，再并行唤醒就绪人物，无待搜索与就绪人物才推进时钟，最后写检查点。"""
        searches = self.engine.due_searches()
        if searches:
            await self._search_barrier(searches)
        before = self.engine.scheduling_state()
        if before.ready_agent_ids:
            await self._decision_barrier(before)
        after = self.engine.scheduling_state()
        if not after.ready_agent_ids and after.next_event_at is not None and not self.engine.due_searches():
            await self._advance(after.next_event_at)
        self.step_count += 1
        await self._checkpoint()

    async def close(self) -> None:
        """@brief 先同步关闭引擎，再异步关闭模型注册表；两步故障各自保留。"""
        errors: list[BaseException] = []
        if self.engine is not None:
            try:
                self._close_engine()
            except Exception as error:
                errors.append(error)
        try:
            await self._registry.close()
        except Exception as error:
            logger.exception("Model registry close failed")
            errors.append(error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise ExceptionGroup("Run host close failed", errors)

    def _require_start_time(self) -> None:
        """@brief 新建运行的起始时刻须等于引擎时钟；不等时先关闭引擎、不登记运行失败，再抛协议错误。"""
        if int(self._inputs.config.start_time.timestamp()) != self.engine.context.state.current_time:
            self.engine.close()
            self.engine = None
            logger.error("Run host start time disagrees with the engine")
            raise ProtocolError("Run host start time disagrees with the engine")

    def _apply_counterfactual(self, intervention: CounterfactualIntervention,
                              intervention_sha256: str) -> CounterfactualReceipt:
        """@brief 施加一次封闭干预并核对：收据摘要等于给定身份，模拟时刻不变，世界版本恰好加一。"""
        before = self.engine.scheduling_state()
        receipt = self.engine.apply_counterfactual(intervention)
        if receipt.intervention_sha256 != intervention_sha256:
            _stop("Environment confirmed a different counterfactual identity")
        after = self.engine.scheduling_state()
        if after.current_time != before.current_time:
            _stop("Counterfactual intervention changed simulation time")
        if after.world_state_version != before.world_state_version + 1:
            _stop("Counterfactual intervention did not advance one world version")
        return receipt

    async def _search_barrier(self, searches: Sequence[tuple[SearchTaskSpec, Any]]) -> None:
        """@brief 并发执行全部到期搜索子会话并在主线程同步完成事实核验与副槽位释放。"""
        write_status(self.run_dir, "running", "deciding")
        jobs = [
            _BarrierJob(
                task.agent_id, task.origin_wakeup_id, catalog,
                SearchSessionRunner().run(task, catalog, self._registry.resolve("search", task.agent_id)),
            )
            for task, catalog in searches
        ]
        outcomes = await self._gather_barrier(jobs, "Concurrent search sessions failed")
        self._log_transport_waits(outcomes)
        write_status(self.run_dir, "running", "resolving")
        self.engine.complete_searches(outcomes)

    async def _decision_barrier(self, state: SchedulingState) -> None:
        """@brief 校验就绪人物，分配只读唤醒快照与沙盒并并发推理，主线程提交后落盘无冲突人物记忆。"""
        unknown = set(state.ready_agent_ids) - set(self._agents)
        if unknown:
            _stop(f"Environment returned unknown ready agents: {sorted(unknown)}")
        write_status(self.run_dir, "running", "deciding")
        wakeups = self.engine.prepare_barrier(state.ready_agent_ids)
        sandboxes = [self.engine.open_sandbox(wakeup) for wakeup in wakeups]
        jobs = [
            _BarrierJob(
                wakeup.agent_id, wakeup.wakeup_id, sandbox,
                LifeAgent(self._agents[wakeup.agent_id]).decide_wakeup(
                    wakeup, sandbox, self._registry.resolve("action", wakeup.agent_id),
                ),
            )
            for wakeup, sandbox in zip(wakeups, sandboxes, strict=True)
        ]
        outcomes = await self._gather_barrier(jobs, "Concurrent agent wakeups failed")
        self._log_transport_waits(outcomes)
        write_status(self.run_dir, "running", "resolving")
        receipt = self.engine.commit_barrier(outcomes)
        self._persist_committed_memories(wakeups, outcomes, receipt)

    def _log_transport_waits(self, outcomes: Sequence[Any]) -> None:
        """@brief 从本轮结果草稿提取含失败重试的模型响应或失败记录，写入运维日志告警事件。"""
        drafts = [draft for item in outcomes for draft in getattr(item, "evidence_drafts", ())]
        self._log_draft_waits(drafts)

    def _log_draft_waits(self, drafts: Sequence[EventDraft]) -> None:
        """@brief 把证据草稿里的模型传输重试记录写入 agent_runtime 告警日志。"""
        now = self.engine.context.state.current_time
        for draft in drafts:
            if draft.event_type not in {"model_response", "model_failure"}:
                continue
            attempts = draft.data.get("transport_attempts")
            if attempts:
                record = transport_wait_record(draft.data["call_id"], attempts)
                self.engine.context.ops.log(now, "agent_runtime", "warn", {**record, "agent_id": draft.agent_id})

    async def _gather_barrier(self, jobs: Sequence[_BarrierJob], group_message: str) -> list[Any]:
        """@brief 并发运行同刻屏障任务；任一任务失败先补写现场草稿，再原样传播异常。"""
        results = await asyncio.gather(*(job.coro for job in jobs), return_exceptions=True)
        errors: list[Exception] = []
        for job, result in zip(jobs, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result
            if isinstance(result, Exception):
                drafts = _collect_failed_drafts(job.source, result)
                if drafts:
                    self._flush_failed_evidence(job.agent_id, job.wakeup_id, drafts, result)
                errors.append(result)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise ExceptionGroup(group_message, errors)
        return list(results)

    def _flush_failed_evidence(self, agent_id: int, wakeup_id: str, drafts: Sequence[Any], error: Exception) -> None:
        """@brief 把失败并发协程尚未提交的模型与沙盒草稿写入足迹库。"""
        try:
            self._log_draft_waits(drafts)
            self.engine.record_failed_evidence(agent_id, wakeup_id, drafts)
        except Exception as record_error:
            logger.exception("Failed evidence recording failed for agent %s", agent_id)
            raise ExceptionGroup("Run and failure recording both failed", [error, record_error]) from error

    def _persist_committed_memories(
        self, wakeups: Sequence[AgentWakeupInput], outcomes: Sequence[SandboxOutcome], receipt: BarrierCommitReceipt,
    ) -> None:
        """@brief 仅对屏障提交成功且非挂起人物调用 save_step 更新工作区与登记 memory_updated。"""
        wakeups_by_agent = {item.agent_id: item for item in wakeups}
        outcomes_by_agent = {item.agent_id: item for item in outcomes}
        for agent_id in sorted(receipt.committed_agent_ids):
            outcome = outcomes_by_agent[agent_id]
            if outcome.status != "completed":
                continue
            wakeup = wakeups_by_agent[agent_id]
            step = MemoryStep(
                identity=self._agents[agent_id].persona_card.strip(),
                facts=outcome.verified_experiences,
                observation_event_id=outcome.observation_event_id,
                memories=outcome.pending_memories,
                now=_datetime(wakeup.observed_at, self._inputs.config.timezone),
            )
            step_outcome = save_step(self._memories[agent_id], step)
            self._record_memory_update(outcome, step_outcome)

    def _record_memory_update(self, outcome: SandboxOutcome, step_outcome: MemoryStepOutcome) -> None:
        """@brief 若本次步尾保存改变了条目文件或 MEMORY.md 视图则写入 memory_updated 事件。"""
        store = self._memories[outcome.agent_id]
        memory_sha256 = hashlib.sha256(store.memory_md_path.read_bytes()).hexdigest()
        created = tuple(str(item["id"]) for item in step_outcome.created_episodes)
        merged = tuple(str(item["id"]) for item in step_outcome.merged_episodes)
        if not created and not merged and step_outcome.memory_before_sha256 == memory_sha256:
            return
        episodes = [{"id": str(item["id"]), "type": str(item["type"]), "text": str(item["text"]),
                     "epistemic_status": str(item["epistemic_status"])}
                    for item in (*step_outcome.created_episodes, *step_outcome.merged_episodes)]
        if not valid_subjective_snapshots(episodes, created + merged):
            logger.error("Memory update payload is invalid for agent %s", outcome.agent_id)
            raise ProtocolError("Memory update payload is invalid")
        self._append_memory_updated_event(outcome, step_outcome, (created, merged, episodes, memory_sha256))

    def _append_memory_updated_event(
        self, outcome: SandboxOutcome, step_outcome: MemoryStepOutcome, payload_parts: tuple[Any, ...],
    ) -> None:
        """@brief 核验观察事实窗口与写入点并追加 memory_updated 事件及运维日志。"""
        created, merged, episodes, memory_sha256 = payload_parts
        engine_store = self.engine.context.store
        observation = engine_store.read_event(outcome.observation_event_id)
        visible_ids = {row["source_event_id"] for row in observation.data.get("verified_experiences", [])}
        if set(step_outcome.verified_event_ids) != visible_ids:
            logger.error("Memory update source window differs from observation for agent %s", outcome.agent_id)
            raise ProtocolError("Memory update evidence is invalid")
        requests = engine_store.read_agent_events("action_requested", outcome.agent_id, observation.event_seq)
        trigger = memory_write_points(requests, outcome.wakeup_id, bool(created or merged))
        episodes_sha256 = hashlib.sha256(self._memories[outcome.agent_id].episodes_path.read_bytes()).hexdigest()
        data = {
            "operation": "append_episodes" if (created or merged) else "refresh_summary",
            "created_episode_ids": list(created), "merged_episode_ids": list(merged), "episodes": episodes,
            "memory_before_sha256": step_outcome.memory_before_sha256, "memory_sha256": memory_sha256,
            "episodes_sha256": episodes_sha256, "observation_event_id": outcome.observation_event_id,
            "verified_event_ids": list(step_outcome.verified_event_ids),
            "trigger": trigger, "wakeup_id": outcome.wakeup_id,
        }
        draft = EventDraft(
            event_type="memory_updated", occurred_at=self.engine.context.state.current_time,
            agent_id=outcome.agent_id, wakeup_id=outcome.wakeup_id,
            visibility="system", source_kind="environment", data=data,
        )
        engine_store.append_evidence([draft])
        self.engine.context.ops.log(self.engine.context.state.current_time, "memory", "info", {
            "code": "MEMORY_UPDATED", "detail": f"Memory update recorded {len(episodes)} episodes",
            "agent_id": outcome.agent_id, "wakeup_id": outcome.wakeup_id,
        })

    async def _advance(self, target: datetime) -> None:
        """@brief 推进到引擎给出的下一边界；零点边界先补回顾与零点计划，再判定零点节律。"""
        write_status(self.run_dir, "running", "advancing")
        self.engine.advance(int(target.timestamp()) - self.engine.context.state.current_time, target)
        if self.engine.midnight_rhythm_pending:
            await self._settle_midnight_tasks()
            self.engine.decide_midnight_rhythm()

    async def _checkpoint(self) -> None:
        """@brief 写一个共同检查点：全部人物工作区的快照与引擎状态一并提交，随后补回顾与零点计划。"""
        write_status(self.run_dir, "running", "checkpointing")
        snapshots = snapshot_workspaces(self.run_dir, sorted(self._agents), self.engine.context.store.assets)
        self.engine.checkpoint(snapshots)
        await self._settle_midnight_tasks()

    async def _settle_midnight_tasks(self) -> None:
        """@brief 按人物编号升序并发补全回顾与零点计划两个后台任务。"""
        await settle_pending_memory_digests(self.engine, self._registry)
        await settle_pending_day_plans(self.engine, self._registry)

    def _record_failure(self, error: Exception) -> None:
        """@brief 登记运行故障：把原异常的 repr 交给引擎，登记本身失败时两项异常都保留。"""
        try:
            self.engine.fail_run(repr(error))
        except Exception as record_error:
            logger.exception("Run failure recording failed")
            raise ExceptionGroup("Run and failure recording both failed", [error, record_error]) from error

    def _close_engine(self) -> None:
        """@brief 关闭引擎：失败时先写日志并登记运行故障再原样抛出；成功后置空。"""
        try:
            self.engine.close()
        except Exception as error:
            logger.exception("Engine close failed")
            self._record_failure(error)
            raise
        self.engine = None
