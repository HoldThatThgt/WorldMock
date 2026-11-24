"""运行宿主并发屏障与搜索子会话顺序契约的纯单元测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from worldmock.agents.memory.life_memory import MemoryStepOutcome
from worldmock.foundation.errors import ProtocolError
from worldmock.runtime.lifecycle import run_host
from worldmock.state.decisions.barrier import (
    AgentWakeupInput,
    BarrierCommitReceipt,
    SandboxOutcome,
    SearchCommitReceipt,
    SearchSessionOutcome,
    SearchTaskSpec,
)
from worldmock.state.decisions.round import SchedulingState

_NOW = datetime(2026, 1, 5, 8, tzinfo=timezone.utc)
_NEXT = datetime(2026, 1, 5, 8, 5, tzinfo=timezone.utc)
_EPOCH = int(_NOW.timestamp())


class _Registry:
    """记录阶段与人物解析顺序及异步关闭的假模型注册表。"""

    def __init__(self, log: list) -> None:
        self.log = log

    def resolve(self, stage: str, agent_id: int | None = None):
        self.log.append(("resolve", stage, agent_id))
        return SimpleNamespace(stage=stage, agent_id=agent_id, profile_id=f"{stage}:{agent_id}")

    async def close(self) -> None:
        self.log.append(("registry-close",))


class _Engine:
    """记录宿主屏障、搜索与时钟推进调用序列的假引擎。"""

    def __init__(self, log: list, search_once: bool = False) -> None:
        self.log = log
        self._search_pending = search_once
        self._ready_queue: list[tuple[int, ...]] = [] if search_once else [(2, 1)]
        self._fail_evidence_drafts: list[tuple] = []
        self.context = SimpleNamespace(
            state=SimpleNamespace(run_id="run", current_time=_EPOCH, end_time=_EPOCH + 3600, version=5),
            config=SimpleNamespace(timezone="UTC"),
            store=SimpleNamespace(assets="assets"),
        )

    @property
    def midnight_rhythm_pending(self) -> bool:
        return False

    def due_searches(self):
        self.log.append(("due_searches", self._search_pending))
        if not self._search_pending:
            return ()
        self._search_pending = False
        tasks = []
        for agent_id in (2, 1):
            spec = SearchTaskSpec(
                action_id=f"act:{agent_id}", agent_id=agent_id, activity_id=f"search:{agent_id}",
                origin_wakeup_id=f"wake:{agent_id}", query="coffee", prefer=None, scoped_app_id=None,
                current_location_id="loc-1", location_name="Home", opened_at_clock="08:00",
                local_date="2026-01-05", weather_label="晴", open_apps=("AppA",),
                category_overview={"drink": 1}, turn_budget=4,
            )
            tasks.append((spec, SimpleNamespace(kind="catalog", agent_id=agent_id)))
        return tuple(tasks)

    def complete_searches(self, outcomes) -> SearchCommitReceipt:
        self.log.append(("complete_searches", [item.agent_id for item in outcomes]))
        self._ready_queue.append((1,))
        return SearchCommitReceipt(completed_count=len(outcomes),
                                   activity_ids=tuple(item.activity_id for item in outcomes), event_seq_last=10)

    def scheduling_state(self) -> SchedulingState:
        ready = self._ready_queue.pop(0) if self._ready_queue else ()
        next_at = None if ready or self._search_pending else _NEXT
        self.log.append(("scheduling_state", ready, next_at))
        return SchedulingState(_NOW, ready, next_at, 5)

    def prepare_barrier(self, agent_ids: tuple[int, ...]) -> tuple[AgentWakeupInput, ...]:
        self.log.append(("prepare_barrier", agent_ids))
        return tuple(
            AgentWakeupInput(
                wakeup_id=f"run:wake:{_EPOCH}:v5:a{agent_id}", agent_id=agent_id, observed_at=_EPOCH,
                world_state_version=5, mode="regular", observation={"id": agent_id},
                observation_parts={}, pending_presentation={}, action_seq_start=1,
            )
            for agent_id in agent_ids
        )

    def open_sandbox(self, wakeup: AgentWakeupInput):
        self.log.append(("open_sandbox", wakeup.agent_id, wakeup.wakeup_id))
        draft = SimpleNamespace(event_id=f"draft-{wakeup.agent_id}", event_type="observation")
        return SimpleNamespace(agent_id=wakeup.agent_id, drain_evidence=lambda: (draft,))

    def commit_barrier(self, outcomes) -> BarrierCommitReceipt:
        self.log.append(("commit_barrier", [(item.agent_id, item.status) for item in outcomes]))
        return BarrierCommitReceipt(
            committed_agent_ids=tuple(item.agent_id for item in outcomes if item.agent_id == 1),
            conflict_requeued_agent_ids=tuple(item.agent_id for item in outcomes if item.agent_id == 2),
            world_state_version=6, event_seq_last=12,
        )

    def record_failed_evidence(self, agent_id: int, wakeup_id: str, drafts) -> None:
        self.log.append(("record_failed_evidence", agent_id, wakeup_id, len(drafts)))

    def advance(self, tick: int, target: datetime) -> list:
        self.log.append(("advance", tick, target))
        return []

    def checkpoint(self, snapshots) -> None:
        self.log.append(("checkpoint", snapshots))

    def close(self) -> None:
        self.log.append(("engine-close",))


def _fake_inputs():
    return SimpleNamespace(config=SimpleNamespace(timezone="UTC"))


def _fake_agents():
    return {
        agent_id: SimpleNamespace(agent_id=agent_id, name=f"Agent{agent_id}",
                                  persona_card=f"Card {agent_id}", persona_card_struct={})
        for agent_id in (1, 2)
    }


async def test_due_searches_run_concurrently_before_wakeups_and_advance(tmp_path, monkeypatch) -> None:
    """到期搜索先于唤醒和推进并发执行，完成后唤醒待恢复人物且仅在无待办时推进时钟。"""
    log: list = []

    class _SearchRunner:
        async def run(self, task, catalog, resolved) -> SearchSessionOutcome:
            log.append(("search-start", task.agent_id, resolved.profile_id))
            await asyncio.sleep(0)
            log.append(("search-end", task.agent_id))
            return SearchSessionOutcome(
                action_id=task.action_id, agent_id=task.agent_id, activity_id=task.activity_id,
                goods_ids=("g1",), place_ids=("p1",), summary="found", evidence_drafts=(),
            )

    class _Agent:
        def __init__(self, person) -> None:
            self.person = person

        async def decide_wakeup(self, wakeup, sandbox, resolved) -> SandboxOutcome:
            log.append(("wakeup", wakeup.agent_id, resolved.profile_id))
            return SandboxOutcome.model_construct(
                wakeup_id=wakeup.wakeup_id, agent_id=wakeup.agent_id, status="completed", state=object(),
                evidence_drafts=(), observation_parts={}, pending_presentation={}, accepted_action_ids=(),
                pending_memories=(), verified_experiences=(), observation_event_id=f"obs-{wakeup.agent_id}",
                observation_payload={}, memory_evidence=None, action_operations=(),
            )

    async def settled(*_args):
        return []

    monkeypatch.setattr(run_host, "SearchSessionRunner", _SearchRunner)
    monkeypatch.setattr(run_host, "LifeAgent", _Agent)
    monkeypatch.setattr(run_host, "save_step", lambda store, step: MemoryStepOutcome(
        (), (), "0" * 64, step.observation_event_id, ()))
    monkeypatch.setattr(run_host.RunHost, "_record_memory_update", lambda self, outcome, step_out: None)
    monkeypatch.setattr(run_host, "snapshot_workspaces", lambda *args: {})
    monkeypatch.setattr(run_host, "settle_pending_memory_digests", settled)
    monkeypatch.setattr(run_host, "settle_pending_day_plans", settled)

    host = run_host.RunHost(tmp_path, _fake_inputs(), _Registry(log), _fake_agents(), {1: (), 2: ()})
    host.engine = _Engine(log, search_once=True)
    await host.step()

    search_starts = [idx for idx, entry in enumerate(log) if entry[0] == "search-start"]
    search_ends = [idx for idx, entry in enumerate(log) if entry[0] == "search-end"]
    assert max(search_starts) < min(search_ends)
    complete_idx = next(idx for idx, entry in enumerate(log) if entry[0] == "complete_searches")
    prepare_idx = next(idx for idx, entry in enumerate(log) if entry[0] == "prepare_barrier")
    advance_idx = next(idx for idx, entry in enumerate(log) if entry[0] == "advance")
    assert max(search_ends) < complete_idx < prepare_idx < advance_idx
    assert ("resolve", "search", 2) in log and ("resolve", "action", 1) in log
    assert host.step_count == 1


async def test_concurrent_wakeups_commit_barrier_and_save_step_skips_requeued_and_suspended(
    tmp_path, monkeypatch,
) -> None:
    """多人物就绪时并发执行 decide_wakeup，save_step 仅在无冲突且非挂起提交后运行。"""
    log: list = []
    saved: list[int] = []

    class _Agent:
        def __init__(self, person) -> None:
            self.person = person

        async def decide_wakeup(self, wakeup, sandbox, resolved) -> SandboxOutcome:
            log.append(("decide-start", wakeup.agent_id, resolved.profile_id))
            await asyncio.sleep(0)
            log.append(("decide-end", wakeup.agent_id))
            status = "suspended" if wakeup.agent_id == 2 else "completed"
            return SandboxOutcome.model_construct(
                wakeup_id=wakeup.wakeup_id, agent_id=wakeup.agent_id, status=status, state=object(),
                evidence_drafts=(), observation_parts={}, pending_presentation={}, accepted_action_ids=(),
                pending_memories=(), verified_experiences=(), observation_event_id=f"obs-{wakeup.agent_id}",
                observation_payload={}, memory_evidence=None, action_operations=(),
            )

    def _spy_save_step(store, step):
        agent_id = int(step.observation_event_id.split("-")[1])
        saved.append(agent_id)
        return MemoryStepOutcome((), (), "0" * 64, step.observation_event_id, ())

    async def settled(*_args):
        return []

    monkeypatch.setattr(run_host, "LifeAgent", _Agent)
    monkeypatch.setattr(run_host, "save_step", _spy_save_step)
    monkeypatch.setattr(run_host.RunHost, "_record_memory_update", lambda self, outcome, step_out: None)
    monkeypatch.setattr(run_host, "snapshot_workspaces", lambda *args: {})
    monkeypatch.setattr(run_host, "settle_pending_memory_digests", settled)
    monkeypatch.setattr(run_host, "settle_pending_day_plans", settled)

    host = run_host.RunHost(tmp_path, _fake_inputs(), _Registry(log), _fake_agents(), {1: (), 2: ()})
    host.engine = _Engine(log, search_once=False)
    await host.step()

    starts = [idx for idx, entry in enumerate(log) if entry[0] == "decide-start"]
    ends = [idx for idx, entry in enumerate(log) if entry[0] == "decide-end"]
    assert max(starts) < min(ends)
    assert saved == [1]


async def test_wakeup_failure_flushes_record_failed_evidence_and_closes_engine_before_registry(
    tmp_path, monkeypatch,
) -> None:
    """并发唤醒异常中止时先补写未提交证据草稿，close 顺序为先引擎后注册表。"""
    log: list = []

    class _FailingAgent:
        def __init__(self, person) -> None:
            self.person = person

        async def decide_wakeup(self, wakeup, sandbox, resolved):
            if wakeup.agent_id == 2:
                raise ProtocolError("turn budget exhausted")
            return SandboxOutcome.model_construct(
                wakeup_id=wakeup.wakeup_id, agent_id=wakeup.agent_id, status="completed", state=object(),
                evidence_drafts=(), observation_parts={}, pending_presentation={}, accepted_action_ids=(),
                pending_memories=(), verified_experiences=(), observation_event_id="obs-1",
                observation_payload={}, memory_evidence=None, action_operations=(),
            )

    monkeypatch.setattr(run_host, "LifeAgent", _FailingAgent)
    host = run_host.RunHost(tmp_path, _fake_inputs(), _Registry(log), _fake_agents(), {1: (), 2: ()})
    host.engine = _Engine(log, search_once=False)

    with pytest.raises(ProtocolError, match="turn budget exhausted"):
        await host.step()

    assert any(entry[:2] == ("record_failed_evidence", 2) and entry[3] == 1 for entry in log)
    assert not any(entry[0] == "commit_barrier" for entry in log)

    await host.close()
    engine_close_idx = log.index(("engine-close",))
    registry_close_idx = log.index(("registry-close",))
    assert engine_close_idx < registry_close_idx
