"""@brief 每日反思测试共用的真实宿主运行：真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent，只替换远程模型传输。

人物的每一步都走生产的 LifeAgent.step：取观察之前询问环境该不该先做就寝反思，随后是决策循环与步尾的记忆收尾。
脚本模型按请求实际开放的工具分流：只带 submit_reflection 的是就寝反思，不带工具的是记忆巩固，其余是行动请求。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv
from tests.integration.runtime.current_contract_fixtures import profile as current_profile
from tests.integration.runtime.test_life_agent_protocol import _ModelResponse, _TextResponse
from tests.life.conftest import MAIN_TOOL, advance_next, context, main_arguments
from worldmock.life.activity_control import is_pending_conclusion

REFLECTION_TOOL = "submit_reflection"
#: 记忆巩固的脚本回答。
CONSOLIDATED = "我想把日子过得规律一些。"
#: 就寝反思缺省写下的感想，按第几次反思依次取用；互不相似，存储不会把它们合并成一条。
INSIGHTS = ("想把日子过得踏实一些。", "和家里人多说几句话才安心。", "手头的活儿要早点动手做完。",
            "周末出门走走能换个心情。", "花钱之前先想清楚要不要。")
#: 行动回复的正文（登记成世界请求的自述）与思考；就寝反思回复的正文不该被读成自述。
ACTION_NOTE, ACTION_THINKING, REFLECTION_NOTE = "照眼下的情况来。", "先把眼下的事做完。", "这段正文属于反思。"


def tool_names(request: dict) -> list[str]:
    """一次模型请求实际开放的工具名。"""
    return [item["function"]["name"] for item in request.get("tools") or []]


def reflection_input(request: dict) -> list[dict]:
    """一次就寝反思请求交给模型的记忆条目（取自原始 user 消息）。"""
    user = request["messages"][1]["content"]
    body = user.split("<memory_reflection_input>", 1)[1].split("</memory_reflection_input>", 1)[0]
    return json.loads(body)["episodes"]


def submit_reflection(insights: list[dict]) -> _ModelResponse:
    """只含一次 submit_reflection 调用的原生响应，另带一段不该被读取的正文。"""
    return _ModelResponse("call-reflection", REFLECTION_TOOL, json.dumps({"insights": insights}, ensure_ascii=False),
                          content=REFLECTION_NOTE)


def memory(text: str, importance: float = 0.3) -> dict:
    """收尾时写下的一条普通记忆。"""
    return {"type": "routine", "text": text, "importance": importance, "keywords": ["日常"]}


class DayModel:
    """脚本模型：行动请求按排好的世界请求作答；记忆巩固答一句话；就寝反思缺省写一条感想、引用输入里的第一条记忆，
    排了回答（由输入记忆构造响应的函数，或要抛出的异常）时照排好的来。全部请求按种类原样留存。"""

    model_name = "test-model"

    def __init__(self) -> None:
        self.actions: list[tuple[str, dict]] = []
        self.reflection_replies: list = []
        self.requests: list[tuple[str, dict]] = []

    def of(self, kind: str) -> list[dict]:
        """某一种请求（action、reflection、consolidation）按先后的原样内容。"""
        return [request for name, request in self.requests if name == kind]

    async def call(self, **request):
        """按请求开放的工具分流并返回脚本响应。"""
        names = tool_names(request)
        if names == [REFLECTION_TOOL]:
            self.requests.append(("reflection", copy.deepcopy(request)))
            episodes = reflection_input(request)
            if self.reflection_replies:
                reply = self.reflection_replies.pop(0)
                if isinstance(reply, BaseException):
                    raise reply
                return reply(episodes)
            text = INSIGHTS[len(self.of("reflection")) - 1]
            return submit_reflection([{"insight": text, "refs": [episodes[0]["id"]]}])
        if not names:
            self.requests.append(("consolidation", copy.deepcopy(request)))
            return _TextResponse(CONSOLIDATED)
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
        return [event for event in store.read_events(store.last_event_seq)
                if (event_type is None or event.event_type == event_type)
                and (decision is None or event.decision_id == decision.decision_id)]

    def reflections(self) -> list:
        """库里登记了归属日期的记忆更新（就寝反思登记）。"""
        return [event for event in self.events("memory_updated") if event.data["reflection"] is not None]

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


def memory_layer_of(request: dict) -> dict:
    """行动请求的 user 消息里的记忆层（六层数组的第三层）。"""
    return json.loads(request["messages"][1]["content"])[2]
