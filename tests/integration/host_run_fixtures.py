"""@brief 真实宿主运行：真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent，只替换远程模型传输。

人物的每一步都走生产的 LifeAgent.step：取本次决策唯一的一份观察，随后是决策循环与步尾的记忆收尾；人物一侧不调用别的模型。
脚本模型只答行动请求（工具含 start_main_activity）；收到别的请求就是宿主发出了不该发的模型调用，当场失败。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv
from tests.integration.runtime.current_contract_fixtures import profile as current_profile
from tests.integration.runtime.test_life_agent_protocol import _ModelResponse
from tests.life.conftest import MAIN_TOOL, advance_next, context, main_arguments, stage_day_plan
from worldmock.simulation_engine.activities.lifecycle.activity_control import is_pending_conclusion

#: 行动回复的正文（登记成世界请求的自述）与思考。
ACTION_NOTE, ACTION_THINKING = "照眼下的情况来。", "先把眼下的事做完。"


def tool_names(request: dict) -> list[str]:
    """一次模型请求实际开放的工具名。"""
    return [item["function"]["name"] for item in request.get("tools") or []]


def memory(text: str, importance: float = 0.3) -> dict:
    """收尾时写下的一条普通记忆。"""
    return {"type": "routine", "text": text, "importance": importance, "keywords": ["日常"]}


class DayModel:
    """脚本模型：行动请求按排好的世界请求作答，全部请求原样留存；请求的工具不含 start_main_activity 时当场失败。"""

    model_name = "test-model"

    def __init__(self) -> None:
        self.actions: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, dict]] = []

    def of(self, kind: str) -> list[dict]:
        """某一种请求（只有 action）按先后的原样内容。"""
        return [request for name, request in self.requests if name == kind]

    async def call(self, **request):
        """只答行动请求并返回脚本响应。"""
        if MAIN_TOOL not in tool_names(request):
            raise AssertionError("LifeAgent sent a non-action model request")
        self.requests.append(("action", copy.deepcopy(request)))
        tool, arguments = self.actions.pop(0)
        return _ModelResponse(f"call-{len(self.requests)}", tool, json.dumps(arguments, ensure_ascii=False),
                              reasoning_content=ACTION_THINKING, content=ACTION_NOTE)


class HostRun:
    """一个真实运行上的人物：每次决策开一轮、走 LifeAgent.step、再结算；label 是决策身份的前缀。"""

    def __init__(self, engine, agent: LifeAgent, model: DayModel, label: str) -> None:
        self.engine, self.agent, self.model, self.label = engine, agent, model, label
        self.environment = agent._env
        self.rounds = 0

    def events(self, event_type: str | None = None, decision=None) -> list:
        """库里已提交的事件，可按类型与决策筛选。"""
        store = self.engine.context.store
        return [event for event in list(store.iter_events(store.last_event_seq))
                if (event_type is None or event.event_type == event_type)
                and (decision is None or event.decision_id == decision.decision_id)]

    async def open(self):
        """在当前时刻开一轮并把决策上下文绑到人物上。"""
        self.rounds += 1
        decision = context(self.engine, f"{self.label}-{self.rounds}")
        await self.environment.begin_decision_round((decision,))
        self.agent.bind_decision_context(decision)
        return decision

    async def decide(self, tool: str, arguments: dict):
        """一次完整的决策：开轮、人物走一步（脚本模型以给定的世界请求作答）、结算；返回决策上下文与结果。"""
        decision = await self.open()
        self.model.actions.append((tool, arguments))
        await self.agent.step(0, decision.observed_at)
        (result,) = await self.environment.commit_decision_round(decision.round_id)
        return decision, result

    async def conclude(self, *memories: dict):
        """给全部待收尾的活动收尾，记忆写在第一项上。"""
        names = [item.name for item in self.engine.context.state.activities.values() if is_pending_conclusion(item)]
        assert names, "no activity is awaiting conclusion"
        items = [{"activity_name": name, "summary": "收尾。", "memories": list(memories) if index == 0 else [],
                  "views": []} for index, name in enumerate(names)]
        decision, result = await self.decide("conclude_activity", {"items": items, "errands": []})
        assert result.status == "completed", result.error_code
        return decision

    async def start(self, action: str, target: str, detail: str | None = None):
        """用 start_main_activity 开始一件事并推进到下一次决策机会。"""
        decision, result = await self.decide(MAIN_TOOL, main_arguments(action, target, detail))
        assert result.status == "in_progress", result.error_code
        self.until_ready()
        return decision, result

    async def sleep(self, minutes: int):
        """睡给定的分钟数并推进到睡醒那一刻；返回睡眠活动的身份。"""
        _, result = await self.start("sleep", str(minutes))
        assert self.engine.context.state.activities[result.activity_id].status == "completed"
        return result.activity_id

    async def rest_and_note(self, text: str):
        """在家做一次「测试休息」，做完收尾并写下一条记忆。"""
        await self.start("interact", "家", "测试休息")
        return await self.conclude(memory(text))

    def until_ready(self) -> None:
        """逐边界推进到人物的下一次决策机会。"""
        while not self.engine.scheduling_state().ready_agent_ids:
            assert self.engine.scheduling_state().next_event_at is not None
            advance_next(self.engine)


async def host_run(engine, workspace: Path, label: str = "host", create: bool = True) -> HostRun:
    """在真实引擎上备好 LifeEnv 与直接导入的 LifeAgent；create 为假时从已有的人物工作区恢复。"""
    environment = LifeEnv()
    environment.engine = engine
    if create:
        LifeAgent.create(workspace, current_profile(),
                         {"enable_memory": True, "enable_todo_list": False, "max_react_turns": 16})
    model = DayModel()
    service = SimpleNamespace(env=environment, llm=SimpleNamespace(default=model), trace=None)
    agent = await LifeAgent.from_workspace(workspace, service)
    assert type(agent) is LifeAgent
    return HostRun(engine, agent, model, label)


def _evening(persona, config) -> None:
    """运行从周一 20:00 开始：写下一条记忆后睡一夜，在周二凌晨醒来。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T20:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-08T20:00:00+08:00")


async def waking_decision(engine_factory, tmp_path, note: str | None = "这一晚歇得很踏实。") -> SimpleNamespace:
    """真实引擎上先写下一条记忆、再睡完一夜，开出当天第一次睡醒的那一轮决策，并备好真实 LifeEnv 与直接导入的
    LifeAgent；脚本模型排好给刚醒的这段睡眠收尾。note 为 None 时人物睡前什么都没写下。"""
    engine = engine_factory(mutate=_evening)
    stage_day_plan(engine, submitted=True)
    run = await host_run(engine, tmp_path / "people" / "agent_0001")
    if note is not None:
        await run.rest_and_note(note)
    slept = await run.sleep(480)
    decision = await run.open()
    name = engine.context.state.activities[slept].name
    run.model.actions.append(("conclude_activity", {"items": [
        {"activity_name": name, "summary": "睡醒了。", "memories": [], "views": []}], "errands": []}))
    return SimpleNamespace(engine=engine, environment=run.environment, agent=run.agent, decision=decision,
                           model=run.model, slept=slept)
