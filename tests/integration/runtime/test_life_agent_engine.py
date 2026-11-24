"""LifeAgent 在真实引擎、AgentSandbox、RunHost 与 SQLite 上的一次唤醒：沙盒隔离、证据按调用身份落库、步尾保存与无快照。"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory  # noqa: F401
from tests.integration.runtime.current_contract_fixtures import ScriptedModel, ToolResponse, resolved_binding
from tests.life.conftest import MAIN_TOOL, advance_until_ready, conclude_request, main_arguments, stage_day_plan
from tests.life.environment_runtime import events
from worldmock.agents.life_agent import LifeAgent, person_inputs
from worldmock.agents.memory.workspace import agent_workspace
from worldmock.agents.prompt import prompt_capacity

#: 在家开始一段休息的世界请求参数。
_REST = main_arguments("interact", "家", "测试休息")
#: 检索今天记忆的只读查询参数。
_TODAY = {"category": "全部", "time": "今天", "keyword": None}


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """容量预检使用冻结元数据的窗口，不读外部模型资料。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


class _RecordingModel:
    """按脚本应答的模型客户端替身，记录每次调用时工作区是否已有 MEMORY.md。"""

    def __init__(self, responses: list, log: list, workspace) -> None:
        self.responses, self.log, self.workspace, self.requests = list(responses), log, workspace, []

    async def call(self, request):
        self.log.append(("model", (self.workspace / "MEMORY.md").is_file()))
        self.requests.append(request)
        return self.responses.pop(0)


def _wrap_sandbox(engine, log: list):
    """包装 open_sandbox，按顺序记录模型证据、工具调度与只读查询。"""
    real_open = engine.open_sandbox

    def wrapped_open(wakeup):
        sandbox = real_open(wakeup)
        real_rec = sandbox.record_model_exchange
        real_dispatch = sandbox.dispatch_world_tool
        real_search = sandbox.search_memory

        def record_exchange(exchange):
            log.append(f"evidence:{exchange.phase}")
            return real_rec(exchange)

        def dispatch(tool_name, args, call_id, note=None):
            log.append("dispatch_world_tool")
            return real_dispatch(tool_name, args, call_id, note)

        def search_memory(req):
            log.append("search_memory")
            return real_search(req)

        sandbox.record_model_exchange = record_exchange
        sandbox.dispatch_world_tool = dispatch
        sandbox.search_memory = search_memory
        return sandbox

    engine.open_sandbox = wrapped_open


@pytest.mark.asyncio
async def test_wakeup_observes_before_model_and_persists_memory_at_host_step_tail(engine_factory, host_factory) -> None:
    """通过真实 RunHost 步执行两次唤醒：先生成观察与模型证据，再经沙盒调度，提交后再由步尾保存写出 MEMORY.md；
    每次唤醒建立独立沙盒，action_id 随新 wakeup_id 从 0001 起计数。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    log: list = []
    _wrap_sandbox(engine, log)
    workspace = agent_workspace(engine.context.store.run_dir, 1)
    model = _RecordingModel([ToolResponse(MAIN_TOOL, _REST, "call-rest")], log, workspace)
    host = host_factory(engine, model)

    await host.step()
    assert log == ["evidence:request", ("model", False), "evidence:response", "dispatch_world_tool"]
    assert (workspace / "MEMORY.md").is_file()
    [first_req] = events(engine, "action_requested")
    [first_mem] = events(engine, "memory_updated")
    assert first_mem.wakeup_id == first_req.wakeup_id

    started = [e for e in events(engine, "action_result") if e.action_id == first_req.action_id][0]
    assert started.data["status"] == "in_progress" and advance_until_ready(engine)
    name = engine.context.state.activities[started.activity_id].name
    log.clear()
    model.responses.append(ToolResponse("conclude_activity", conclude_request(name).model_dump(mode="json"),
                                        "call-conclude"))
    model.responses.append(ToolResponse(MAIN_TOOL, _REST, "call-rest-2"))
    await host.step()

    assert log[0] == "evidence:request" and ("model", True) in log
    actions = [e for e in events(engine, "action_requested") if e.wakeup_id != first_req.wakeup_id]
    assert [e.action_id for e in actions] == [f"{actions[0].wakeup_id}:action:0001",
                                              f"{actions[0].wakeup_id}:action:0002"]


@pytest.mark.asyncio
async def test_decide_wakeup_runs_without_host_memory_step_leaving_workspace_untouched(engine_factory) -> None:
    """只经 prepare_barrier + open_sandbox + LifeAgent.decide_wakeup + commit_barrier：没有 RunHost 步尾 save_step，
    工作区不写出 MEMORY.md 且无 memory_updated。"""
    engine = engine_factory()
    stage_day_plan(engine)
    workspace = agent_workspace(engine.context.store.run_dir, 1)
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    model = ScriptedModel([ToolResponse(MAIN_TOOL, _REST, "call-rest")])
    agent = LifeAgent(person_inputs(engine.context.inputs.persona_by_id[1]))
    outcome = await agent.decide_wakeup(wakeup, sandbox, resolved_binding(model, frozen=engine.context.inputs))
    assert outcome.status == "completed" and outcome.accepted_action_ids == (f"{wakeup.wakeup_id}:action:0001",)
    assert not (workspace / "MEMORY.md").exists()
    engine.commit_barrier([outcome])
    assert events(engine, "memory_updated") == []


@pytest.mark.asyncio
async def test_turn_evidence_reaches_sqlite_in_order_with_its_call_identities(engine_factory) -> None:
    """检索一回合、登记一回合：库里本唤醒的模型证据依次是两对请求与响应，调用身份按回合号递增，attempt 恒为 1。"""
    engine = engine_factory()
    stage_day_plan(engine)
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)
    model = ScriptedModel([ToolResponse("search_memory", _TODAY, "call-search"), ToolResponse(MAIN_TOOL, _REST)])
    agent = LifeAgent(person_inputs(engine.context.inputs.persona_by_id[1]))
    outcome = await agent.decide_wakeup(wakeup, sandbox, resolved_binding(model, frozen=engine.context.inputs))
    engine.commit_barrier([outcome])

    exchanged = [event for event in events(engine) if event.wakeup_id == wakeup.wakeup_id
                 and event.event_type in {"model_request", "model_response"}]
    identities = [f"{wakeup.wakeup_id}:model:{turn:04d}:attempt:1" for turn in (1, 2)]
    assert [(event.event_type, event.data["call_id"], event.data["attempt"]) for event in exchanged] == [
        (kind, identity, 1) for identity in identities for kind in ("model_request", "model_response")]
    assert [json.loads(json.dumps(event.data["request"], ensure_ascii=False)) for event in exchanged[::2]] == [
        json.loads(json.dumps(request, ensure_ascii=False)) for request in model.requests]
    assert all(response.caused_by == [request.event_id]
               for request, response in zip(exchanged[::2], exchanged[1::2], strict=True))


@pytest.mark.asyncio
async def test_a_wakeup_writes_no_prompt_snapshot(engine_factory, host_factory, tmp_path) -> None:
    """一次真实唤醒之后，运行目录与临时目录下都没有只读问答的快照文件。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    host = host_factory(engine, ScriptedModel([ToolResponse(MAIN_TOOL, _REST)]))
    await host.step()
    assert (agent_workspace(engine.context.store.run_dir, 1) / "MEMORY.md").is_file()
    snapshot = "life_prompt" + "_snapshot.json"
    assert list(engine.context.store.run_dir.rglob(snapshot)) == [] and list(tmp_path.rglob(snapshot)) == []
