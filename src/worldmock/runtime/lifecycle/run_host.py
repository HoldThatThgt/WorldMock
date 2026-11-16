"""@brief 运行宿主 RunHost：一个进程、一个事件循环里驱动决策轮、推进、共同检查点与零点任务（docs/design/10-runtime.md「决策循环」）。"""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from worldmock.agents.life_agent import LifeAgent, PersonInputs
from worldmock.agents.memory.workspace import create_workspaces, memory_stores, snapshot_workspaces
from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.configuration.model.model_client import ModelClient
from worldmock.foundation.errors import ProtocolError
from worldmock.foundation.time.readable import _datetime
from worldmock.runtime.lifecycle.midnight import settle_pending_day_plans, settle_pending_memory_digests
from worldmock.runtime.monitoring.status import write_status
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.state.decisions.results import CounterfactualIntervention, CounterfactualReceipt
from worldmock.state.decisions.round import DecisionContext, SchedulingState
from worldmock.state.identity import RunIdentity

logger = logging.getLogger(__name__)


def _stop(message: str) -> NoReturn:
    """@brief 宿主核对失败：先写同文错误日志，再抛 RuntimeError（文本沿用今天的 Society 核对）。"""
    logger.error("%s", message)
    raise RuntimeError(message)


class RunHost:
    """@brief 运行宿主：在一个事件循环里驱动决策轮、推进、共同检查点与零点任务；引擎是唯一写入者，宿主不另存世界或时钟。"""

    def __init__(self, run_dir: Path, inputs: EnvironmentInputs, client: ModelClient,
                 agents: Mapping[int, PersonInputs],
                 memory_records: Mapping[int, Sequence[Mapping[str, Any]]]) -> None:
        """@brief 绑定运行目录、已加载的冻结输入、整次运行唯一的模型客户端、每个人物的冻结输入与记忆存储；引擎由启动方法建立。"""
        self.run_dir = Path(run_dir)
        self.engine: LifeEngine | None = None
        self.step_count = 0
        self._inputs = inputs
        self._client = client
        self._agents = dict(agents)
        self._memories = memory_stores(self.run_dir, memory_records)

    @property
    def current_time(self) -> datetime:
        """@brief 引擎当前时刻（场景时区）；宿主不保存第二份时钟。"""
        return _datetime(self.engine.context.state.current_time, self.engine.context.config.timezone)

    async def start_new(self, identity: RunIdentity, stage_identity: dict[str, Any],
                        create_options: dict[str, Any]) -> None:
        """@brief 新建：建人物工作区，建引擎，核对起始时刻，写初始检查点并补零点任务。"""
        create_workspaces(self.run_dir, identity.agent_ids)
        self.engine = LifeEngine.create(self._inputs, self.run_dir, identity, stage_identity, create_options)
        self._require_start_time()
        try:
            await self._checkpoint()
        except Exception as error:
            self._record_failure(error)
            raise

    async def start_resume(self) -> None:
        """@brief 普通恢复：打开引擎，在首次决策之前补回顾与零点计划，不写检查点。"""
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
        """@brief 反复 step 直到引擎时钟到达结束时刻；循环里的故障登记后原样传播。"""
        try:
            while self.engine.context.state.current_time < self.engine.context.state.end_time:
                await self.step()
        except Exception as error:
            self._record_failure(error)
            raise

    async def step(self) -> None:
        """@brief 一步：有人就绪就开一轮，提交后没人就绪才推进，步数加一，写一个共同检查点。"""
        before = self.engine.scheduling_state()
        if before.ready_agent_ids:
            await self._decision_round(before)
        after = self.engine.scheduling_state()
        if not after.ready_agent_ids and after.next_event_at is not None:
            await self._advance(after.next_event_at)
        self.step_count += 1
        await self._checkpoint()

    async def close(self) -> None:
        """@brief 关闭引擎（失败时登记），再关闭唯一的模型客户端；两步的故障都保留。"""
        errors: list[Exception] = []
        if self.engine is not None:
            try:
                self._close_engine()
            except Exception as error:
                errors.append(error)
        try:
            await self._client.close()
        except Exception as error:
            logger.exception("Model client close failed")
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

    async def _decision_round(self, state: SchedulingState) -> None:
        """@brief 一个决策轮：开轮，按就绪次序为每个人物新建 LifeAgent 逐个决定，再原子提交；轮内故障中止后原样传播。"""
        contexts = self._decision_contexts(state)
        round_id = contexts[0].round_id
        try:
            write_status(self.run_dir, "running", "deciding")
            self.engine.begin_decision_round(list(contexts))
            for context in contexts:
                agent_id = context.agent_id
                agent = LifeAgent(self._agents[agent_id], self.engine, self._client, self._memories[agent_id])
                await agent.decide(context)
            write_status(self.run_dir, "running", "resolving")
            self.engine.commit_decision_round(round_id)
        except Exception as error:
            self._abort_round(round_id, error)
            raise

    def _decision_contexts(self, state: SchedulingState) -> tuple[DecisionContext, ...]:
        """@brief 由调度状态形成本轮的决策上下文：轮次与决策身份格式逐字保留，未知的就绪人物在开轮之前停止。"""
        unknown = set(state.ready_agent_ids) - set(self._agents)
        if unknown:
            _stop(f"Environment returned unknown ready agents: {sorted(unknown)}")
        run_id = self.engine.context.state.run_id
        round_id = f"{run_id}:round:{int(state.current_time.timestamp())}:{state.world_state_version}"
        return tuple(DecisionContext(run_id, round_id, f"{round_id}:agent:{agent_id}:order:{order}", agent_id,
                                     state.current_time, state.world_state_version)
                     for order, agent_id in enumerate(state.ready_agent_ids))

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
        """@brief 依次跑回顾与零点计划两个后台任务，都经整次运行唯一的模型客户端。"""
        await settle_pending_memory_digests(self.engine, self._client)
        await settle_pending_day_plans(self.engine, self._client)

    def _abort_round(self, round_id: str, error: Exception) -> None:
        """@brief 决策轮在开轮之后、提交返回之前出错：只在引擎的轮次仍开着时中止这一轮，中止原因是原异常的 repr；
        提交已经关闭本轮（同秒决策次数用完）或开轮之前就失败时不中止。中止本身失败时两项异常都保留。

        @param round_id 出错那一轮的身份
        @param error 宿主捕获的原异常
        @exception ExceptionGroup 中止也失败（Decision round and abort both failed）
        """
        if self.engine.decisions.round is None:
            return
        try:
            self.engine.abort_decision_round(round_id, repr(error))
        except Exception as abort_error:
            logger.exception("Decision round abort failed")
            raise ExceptionGroup("Decision round and abort both failed", [error, abort_error]) from error

    def _record_failure(self, error: Exception) -> None:
        """@brief 登记运行故障：把原异常的 repr 交给引擎，引擎已有 run_failed 时不写；登记本身失败时两项异常都保留。

        @param error 宿主捕获的原异常
        @exception ExceptionGroup 登记也失败（Run and failure recording both failed）
        """
        try:
            self.engine.fail_run(repr(error))
        except Exception as record_error:
            logger.exception("Run failure recording failed")
            raise ExceptionGroup("Run and failure recording both failed", [error, record_error]) from error

    def _close_engine(self) -> None:
        """@brief 关闭引擎：失败时先写日志并登记运行故障再原样抛出，引擎属性保持不变；成功后置空。"""
        try:
            self.engine.close()
        except Exception as error:
            logger.exception("Engine close failed")
            self._record_failure(error)
            raise
        self.engine = None
