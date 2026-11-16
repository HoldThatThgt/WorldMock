"""LifeAgent 在真实引擎与 SQLite 上的一次决策：先观察一次、再跑回合循环、最后步尾保存；回合证据按调用身份依次落库；
不写只读问答的快照。

引擎由 engine_factory 建，冻结输入按引擎清单的 stage_model_identity 建，记忆存储由 memory_stores 建在运行目录；
模型客户端是脚本替身，引擎外面只包一层按次序记录五个方法的转交。
"""

from __future__ import annotations

import json

import pytest

from tests.integration.runtime.current_contract_fixtures import ToolResponse
from tests.life.conftest import (
    MAIN_TOOL, advance_until_ready, conclude_request, context, main_arguments, stage_day_plan)
from tests.life.environment_runtime import events
from worldmock.agents.life_agent import LifeAgent, person_inputs
from worldmock.agents.memory.workspace import agent_workspace, memory_stores
from worldmock.agents.prompt import prompt_capacity

#: 在家开始一段休息的世界请求参数。
_REST = main_arguments("interact", "家", "测试休息")
#: 检索今天记忆的只读查询参数。
_TODAY = {"category": "全部", "time": "今天", "keyword": None}


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """容量预检使用冻结元数据的窗口，不读外部模型资料。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


class _Recording:
    """真实引擎外面一层：按次序记下人物调用的五个方法（记忆证据登记时工作区里有没有 MEMORY.md），再转交真实引擎。"""

    def __init__(self, engine, log: list, workspace) -> None:
        self.engine, self.log, self.workspace = engine, log, workspace

    def observe(self, context):
        """记下一次观察。"""
        self.log.append("observe")
        return self.engine.observe(context)

    def submit_action(self, call):
        """记下一次世界请求的登记。"""
        self.log.append("submit_action")
        return self.engine.submit_action(call)

    def read_query(self, context, tool, request):
        """记下一次只读查询。"""
        self.log.append("read_query")
        return self.engine.read_query(context, tool, request)

    def record_model_exchange(self, exchange) -> None:
        """记下一份模型证据的阶段。"""
        self.log.append(f"evidence:{exchange.phase}")
        self.engine.record_model_exchange(exchange)

    def record_memory_update(self, evidence) -> None:
        """记下记忆证据登记时 MEMORY.md 是否已经写出。"""
        self.log.append(("memory_updated", (self.workspace / "MEMORY.md").is_file()))
        self.engine.record_memory_update(evidence)


class _Model:
    """按脚本应答的模型客户端替身：记下调用时工作区里有没有 MEMORY.md。"""

    def __init__(self, responses: list, log: list, workspace) -> None:
        self.responses, self.log, self.workspace, self.requests = list(responses), log, workspace, []

    async def call(self, request):
        """记下一次模型调用后按脚本作答。"""
        self.log.append(("model", (self.workspace / "MEMORY.md").is_file()))
        self.requests.append(request)
        return self.responses.pop(0)


def _setup(engine_factory) -> tuple:
    """真实引擎、冻结输入与建在运行目录里的记忆存储；返回（引擎, 冻结输入, 记忆存储, 工作区）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    store = engine.context.store
    frozen = person_inputs(engine.context.inputs.persona, store.manifest["stage_model_identity"])
    memory = memory_stores(store.run_dir, {frozen.agent_id: ()})[frozen.agent_id]
    return engine, frozen, memory, agent_workspace(store.run_dir, frozen.agent_id)


@pytest.mark.asyncio
async def test_a_decision_observes_once_before_any_model_call_and_ends_with_the_memory_step(engine_factory) -> None:
    """一次决策恰好观察一次、在任何模型调用之前；随后是证据与模型调用，世界请求登记之后才由步尾保存写出 MEMORY.md、
    再登记「记忆已更新」。同一人物的第二个决策用新实例：动作序号重新从 1 起，第一实例的状态不带进来。"""
    engine, frozen, memory, workspace = _setup(engine_factory)
    log: list = []
    model = _Model([ToolResponse(MAIN_TOOL, _REST, "call-rest")], log, workspace)
    handle = _Recording(engine, log, workspace)
    first = context(engine, "first")
    engine.begin_decision_round([first])
    agent = LifeAgent(frozen, handle, model, memory)
    await agent.decide(first)
    assert log == ["observe", "evidence:request", ("model", False), "evidence:response", "submit_action",
                   ("memory_updated", True)]
    [started] = engine.commit_decision_round(first.round_id)
    assert started.status.value == "in_progress"
    assert [event.decision_id for event in events(engine, "memory_updated")] == [first.decision_id]

    assert advance_until_ready(engine)
    name = engine.context.state.activities[started.activity_id].name
    model.responses.append(ToolResponse("conclude_activity", conclude_request(name).model_dump(mode="json"),
                                        "call-conclude"))
    second = context(engine, "second")
    engine.begin_decision_round([second])
    log.clear()
    successor = LifeAgent(frozen, handle, model, memory)
    await successor.decide(second)
    assert successor is not agent and log[0] == "observe" and log.count("observe") == 1
    assert (agent._context, agent._action_count) == (first, 1)
    assert (successor._context, successor._action_count) == (second, 1)
    engine.commit_decision_round(second.round_id)
    actions = [event for event in events(engine, "action_requested") if event.decision_id == second.decision_id]
    assert [event.action_id for event in actions] == [f"{second.decision_id}:action:0001"]


@pytest.mark.asyncio
async def test_run_turns_answers_on_the_given_observation_without_the_memory_step(engine_factory) -> None:
    """run_turns 在给定的观察上跑回合循环、返回登记的世界请求：引擎没有第二次观察，工作区没有写出 MEMORY.md。"""
    engine, frozen, memory, workspace = _setup(engine_factory)
    log: list = []
    decision = context(engine, "turns")
    engine.begin_decision_round([decision])
    observation = engine.observe(decision)
    agent = LifeAgent(frozen, _Recording(engine, log, workspace),
                      _Model([ToolResponse(MAIN_TOOL, _REST)], log, workspace), memory)
    registered = await agent.run_turns(decision, observation)
    assert [(item.action, item.args) for item in registered] == [(MAIN_TOOL, _REST)]
    assert "observe" not in log and not (workspace / "MEMORY.md").exists()
    engine.commit_decision_round(decision.round_id)
    assert events(engine, "memory_updated") == []


@pytest.mark.asyncio
async def test_turn_evidence_reaches_sqlite_in_order_with_its_call_identities(engine_factory) -> None:
    """检索一回合、登记一回合：库里本决策的模型证据依次是两对请求与响应，调用身份按回合号递增，attempt 恒为 1，
    响应带回的请求就是登记时的那一份。"""
    engine, frozen, memory, workspace = _setup(engine_factory)
    log: list = []
    model = _Model([ToolResponse("search_memory", _TODAY, "call-search"), ToolResponse(MAIN_TOOL, _REST)],
                   log, workspace)
    decision = context(engine, "evidence")
    engine.begin_decision_round([decision])
    await LifeAgent(frozen, engine, model, memory).decide(decision)
    engine.commit_decision_round(decision.round_id)
    exchanged = [event for event in events(engine) if event.decision_id == decision.decision_id
                 and event.event_type in {"model_request", "model_response"}]
    identities = [f"{decision.decision_id}:model:{turn:04d}:attempt:1" for turn in (1, 2)]
    assert [(event.event_type, event.data["call_id"], event.data["attempt"]) for event in exchanged] == [
        (kind, identity, 1) for identity in identities for kind in ("model_request", "model_response")]
    assert [json.loads(json.dumps(event.data["request"], ensure_ascii=False)) for event in exchanged[::2]] == [
        json.loads(json.dumps(request, ensure_ascii=False)) for request in model.requests]
    assert all(response.caused_by == [request.event_id]
               for request, response in zip(exchanged[::2], exchanged[1::2], strict=True))


@pytest.mark.asyncio
async def test_a_decision_writes_no_prompt_snapshot(engine_factory, tmp_path) -> None:
    """一次真实决策之后，运行目录与临时目录下都没有只读问答的快照文件。"""
    engine, frozen, memory, workspace = _setup(engine_factory)
    decision = context(engine, "snapshot")
    engine.begin_decision_round([decision])
    await LifeAgent(frozen, engine, _Model([ToolResponse(MAIN_TOOL, _REST)], [], workspace), memory).decide(decision)
    engine.commit_decision_round(decision.round_id)
    assert (workspace / "MEMORY.md").is_file()
    snapshot = "life_prompt" + "_snapshot.json"
    assert list(engine.context.store.run_dir.rglob(snapshot)) == [] and list(tmp_path.rglob(snapshot)) == []
