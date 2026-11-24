"""@brief 真实宿主运行：真实引擎、SQLite、沙盒合并器与无状态 LifeAgent，只替换远程模型传输。"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from tests.integration.runtime.current_contract_fixtures import ToolResponse, resolved_binding
from tests.life.conftest import MAIN_TOOL, advance_next_async, main_arguments, stage_day_plan
from worldmock.agents.life_agent import LifeAgent, PersonInputs, person_inputs
from worldmock.agents.memory.life_memory import MemoryStep, memory_update_evidence, save_step
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore, read_episodes
from worldmock.configuration.model.model_registry import ResolvedModelBinding
from worldmock.foundation.time.readable import _datetime
from worldmock.recording_engine.events.memory_evidence import memory_write_points
from worldmock.recording_engine.events.models import EventDraft
from worldmock.simulation_engine.activities.lifecycle.activity_control import is_pending_conclusion

#: 行动回复的正文（登记成世界请求的自述）与思考。
ACTION_NOTE, ACTION_THINKING = "照眼下的情况来。", "先把眼下的事做完。"


def tool_names(request: dict) -> list[str]:
    """@brief 一次模型请求实际开放的工具名。"""
    return [item["function"]["name"] for item in request.get("tools") or []]


def memory(text: str, importance: float = 0.3) -> dict:
    """@brief 收尾时写下的一条普通记忆。"""
    return {"type": "routine", "text": text, "importance": importance, "keywords": ["日常"]}


class DayModel:
    """@brief 脚本模型：行动请求按排好的世界请求作答，全部请求原样留存；不含 start_main_activity 时当场失败。"""

    model_name = "test-model"

    def __init__(self) -> None:
        self.actions: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, dict]] = []

    def of(self, kind: str) -> list[dict]:
        """@brief 某一种请求（只有 action）按先后的原样内容。"""
        return [request for name, request in self.requests if name == kind]

    async def call(self, request):
        """@brief 只答行动请求并返回脚本响应。"""
        if MAIN_TOOL not in tool_names(request):
            raise AssertionError("LifeAgent sent a non-action model request")
        self.requests.append(("action", copy.deepcopy(request)))
        tool, arguments = self.actions.pop(0)
        return ToolResponse(
            tool,
            arguments,
            call_id=f"call-{len(self.requests)}",
            reasoning=ACTION_THINKING,
            content=ACTION_NOTE,
        )

    async def close(self) -> None:
        """@brief 满足 ModelRegistry 回收接口的空关闭方法。"""


class HostRun:
    """@brief 一个真实运行上的人物：每次唤醒经过 prepare_barrier、open_sandbox、LifeAgent 与 commit_barrier。"""

    def __init__(
        self,
        engine,
        inputs: PersonInputs,
        store: WorldMockMemoryStore,
        model: DayModel,
        binding: ResolvedModelBinding,
    ) -> None:
        self.engine = engine
        self.inputs = inputs
        self.store = store
        self.model = model
        self.binding = binding
        self.rounds = 0

    def events(self, event_type: str | None = None, wakeup=None) -> list:
        """@brief 库里已提交的事件，可按类型与唤醒标识筛选。"""
        store = self.engine.context.store
        wakeup_id = wakeup.wakeup_id if hasattr(wakeup, "wakeup_id") else wakeup
        return [
            event for event in list(store.iter_events(store.last_event_seq))
            if (event_type is None or event.event_type == event_type)
            and (wakeup_id is None or event.wakeup_id == wakeup_id)
        ]

    async def open(self):
        """@brief 在当前时刻准备单人唤醒输入快照并返回 AgentWakeupInput。"""
        self.rounds += 1
        [wakeup] = self.engine.prepare_barrier((self.inputs.agent_id,))
        return wakeup

    def _persist_memory(self, wakeup, outcome) -> None:
        """@brief 屏障提交完成后于宿主侧保存步尾记忆并写入 memory_updated 证据事件。"""
        step = MemoryStep(
            identity=self.inputs.persona_card.strip(),
            facts=outcome.verified_experiences,
            observation_event_id=outcome.observation_event_id,
            memories=outcome.pending_memories,
            now=_datetime(wakeup.observed_at, self.engine.context.config.timezone),
        )
        step_outcome = save_step(self.store, step)
        evidence = memory_update_evidence(outcome.wakeup_id, outcome.agent_id, self.store, step_outcome)
        if evidence is None:
            return
        engine_store = self.engine.context.store
        observation = engine_store.read_event(evidence.observation_event_id)
        requests = engine_store.read_agent_events("action_requested", outcome.agent_id, observation.event_seq)
        trigger = memory_write_points(
            requests, outcome.wakeup_id, bool(evidence.created_episode_ids or evidence.merged_episode_ids),
        )
        draft = EventDraft(
            event_type="memory_updated",
            occurred_at=self.engine.context.state.current_time,
            agent_id=outcome.agent_id,
            wakeup_id=outcome.wakeup_id,
            visibility="system",
            source_kind="environment",
            data={
                "operation": evidence.operation,
                "created_episode_ids": list(evidence.created_episode_ids),
                "merged_episode_ids": list(evidence.merged_episode_ids),
                "episodes": [dict(item) for item in evidence.episodes],
                "memory_before_sha256": evidence.memory_before_sha256,
                "memory_sha256": evidence.memory_sha256,
                "episodes_sha256": evidence.episodes_sha256,
                "observation_event_id": evidence.observation_event_id,
                "verified_event_ids": list(evidence.verified_event_ids),
                "trigger": trigger,
                "wakeup_id": evidence.wakeup_id,
            },
        )
        engine_store.append_evidence([draft])

    async def decide_turns(self, *steps: tuple[str, dict]):
        """@brief 一次完整的唤醒：准备快照、开启沙盒、按多回合脚本运行 LifeAgent、提交屏障并落盘步尾记忆。"""
        from tests.life.environment_runtime import action_result

        wakeup = await self.open()
        self.model.actions.extend(steps)
        sandbox = self.engine.open_sandbox(wakeup)
        outcome = await LifeAgent(self.inputs).decide_wakeup(wakeup, sandbox, self.binding)
        receipt = self.engine.commit_barrier([outcome])
        if self.inputs.agent_id in receipt.committed_agent_ids and outcome.status == "completed":
            self._persist_memory(wakeup, outcome)
        action_id = outcome.action_operations[-1]["action_id"] if outcome.action_operations else (
            self.events("action_result", wakeup)[-1].action_id
        )
        return wakeup, action_result(self.engine, action_id)

    async def decide(self, tool: str, arguments: dict):
        """@brief 单步工具唤醒的便捷入口。"""
        return await self.decide_turns((tool, arguments))

    async def conclude(self, *memories: dict, next_call: tuple[str, dict] | None = None):
        """@brief 给全部待收尾的活动收尾（记忆写在第一项上），并按 AgentLoop 要求以终结开始动作结束本轮唤醒。"""
        names = [
            item.name for item in self.engine.context.state.activities.values()
            if item.agent_id == self.inputs.agent_id and is_pending_conclusion(item)
        ]
        assert names, "no activity is awaiting conclusion"
        items = [{"activity_name": name, "summary": "收尾。", "memories": list(memories) if index == 0 else [],
                  "views": []} for index, name in enumerate(names)]
        follow = next_call or (MAIN_TOOL, main_arguments("interact", "家", "测试休息"))
        wakeup, result = await self.decide_turns(("conclude_activity", {"items": items, "errands": []}), follow)
        assert result.status in {"in_progress", "completed"}, result.error_code
        return wakeup

    async def start(self, action: str, target: str, detail: str | None = None):
        """@brief 用 start_main_activity 开始一件事并推进到下一次决策机会。"""
        wakeup, result = await self.decide(MAIN_TOOL, main_arguments(action, target, detail))
        assert result.status == "in_progress", result.error_code
        await self.until_ready()
        return wakeup, result

    async def sleep(self, minutes: int):
        """@brief 睡给定的分钟数并推进到睡醒那一刻；返回睡眠活动的身份。"""
        _, result = await self.start("sleep", str(minutes))
        assert self.engine.context.state.activities[result.activity_id].status == "completed"
        return result.activity_id

    async def conclude_and_start(
        self, *memories: dict, action: str = "interact", target: str = "家", detail: str | None = "测试休息",
    ):
        """@brief 同轮唤醒先收尾全部待收尾活动并写下记忆，再开始下一项主活动并推进到下一次决策机会。"""
        wakeup = await self.conclude(*memories, next_call=(MAIN_TOOL, main_arguments(action, target, detail)))
        await self.until_ready()
        return wakeup

    async def conclude_and_sleep(self, minutes: int, *memories: dict) -> str:
        """@brief 同轮唤醒先收尾再入睡并推进到睡醒那一刻；返回睡眠活动身份。"""
        wakeup = await self.conclude(*memories, next_call=(MAIN_TOOL, main_arguments("sleep", str(minutes))))
        await self.until_ready()
        slept = max(
            (item for item in self.engine.context.state.activities.values()
             if item.agent_id == self.inputs.agent_id and item.kind == "sleep"),
            key=lambda item: item.started_at,
        )
        assert slept.origin_wakeup_id == wakeup.wakeup_id and slept.status == "completed"
        return slept.activity_id

    async def rest_and_note(self, text: str):
        """@brief 在家做一次「测试休息」，做完收尾并写下一条记忆。"""
        await self.start("interact", "家", "测试休息")
        return await self.conclude(memory(text))

    async def until_ready(self) -> None:
        """@brief 逐边界推进到人物的下一次决策机会，若遇到期搜索则先完成之。"""
        from tests.life.environment_runtime import _settle_due_searches

        while not self.engine.scheduling_state().ready_agent_ids:
            if _settle_due_searches(self.engine):
                continue
            assert self.engine.scheduling_state().next_event_at is not None
            await advance_next_async(self.engine)


async def host_run(engine, workspace: Path, label: str = "host", create: bool = True, agent_id: int = 1) -> HostRun:
    """@brief 在真实引擎上备好冻结输入、模型绑定与记忆存储；create 为假时从已有的人物工作区读回条目。"""
    del label
    frozen = engine.context.inputs
    inputs = person_inputs(frozen.persona_by_id[agent_id])
    records = () if create else read_episodes(workspace, inputs.agent_id)
    store = WorldMockMemoryStore(workspace, inputs.agent_id, records)
    model = DayModel()
    binding = resolved_binding(model, frozen=frozen, stage="action", agent_id=inputs.agent_id)
    return HostRun(engine, inputs, store, model, binding)


def _evening(persona, config) -> None:
    """@brief 运行从周一 20:00 开始：写下一条记忆后睡一夜，在周二凌晨醒来。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T20:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-08T20:00:00+08:00")


async def waking_decision(engine_factory, tmp_path, note: str | None = "这一晚歇得很踏实。") -> SimpleNamespace:
    """@brief 真实引擎上先写下一条记忆、再睡完一夜，准备睡醒那一刻的唤醒快照、沙盒、模型绑定与无状态 LifeAgent。"""
    engine = engine_factory(mutate=_evening)
    stage_day_plan(engine, submitted=True)
    run = await host_run(engine, tmp_path / "people" / "agent_0001")
    if note is not None:
        await run.start("interact", "家", "测试休息")
        slept = await run.conclude_and_sleep(480, memory(note))
    else:
        slept = await run.sleep(480)
    wakeup = await run.open()
    sandbox = engine.open_sandbox(wakeup)
    name = engine.context.state.activities[slept].name
    run.model.actions.append(("conclude_activity", {"items": [
        {"activity_name": name, "summary": "睡醒了。", "memories": [], "views": []}], "errands": []}))
    agent = LifeAgent(run.inputs)
    return SimpleNamespace(
        engine=engine,
        agent=agent,
        wakeup=wakeup,
        sandbox=sandbox,
        binding=run.binding,
        model=run.model,
        slept=slept,
    )
