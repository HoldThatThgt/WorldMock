"""共同决策轮 Society 的故障传播与顺序测试。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import agentsociety2.society.society as society_module
from agentsociety2.society import (
    AgentSociety,
    AgentSocietyConfig,
    SchedulingState,
)


class _RemoteFunction:
    """模拟 Ray remote 包装。"""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.requests = []

    def remote(self, request, _proxy):
        self.requests.append(request)

        async def done():
            return self.result

        return done()


class _Router:
    """记录 Society 对直接环境合同的调用。"""

    def __init__(self, start: datetime, *, failed: bool = False) -> None:
        self.current = start
        self.next_at = start + timedelta(seconds=60)
        self.ready = (2, 1)
        self.failed = failed
        self.calls = []
        self.checkpoints = []
        self.failures = []
        self.version = 3

    async def scheduling_state(self):
        return SchedulingState(self.current, self.ready, self.next_at, self.version)

    async def init(self, start):
        self.calls.append(("init", start))
        return False

    async def begin_decision_round(self, contexts):
        self.calls.append(("begin", tuple(item.agent_id for item in contexts)))
        self.round_agents = tuple(item.agent_id for item in contexts)
        return contexts[0].round_id

    async def commit_decision_round(self, round_id):
        self.calls.append(("commit", round_id))
        self.ready = ()
        return [
            {"agent_id": agent_id, "status": "completed"}
            for agent_id in self.round_agents
        ]

    async def abort_decision_round(self, round_id, error):
        self.calls.append(("abort", round_id, error))

    async def step(self, tick, target):
        self.calls.append(("step", tick, target))
        self.current = target
        self.next_at = target + timedelta(seconds=60)

    async def set_current_time(self, target):
        self.calls.append(("clock", target))

    async def checkpoint(self, request):
        self.checkpoints.append(request)

    async def fail_run(self, error):
        self.failures.append(error)

    async def apply_counterfactual(self, intervention):
        self.calls.append(("counterfactual", intervention))
        self.version += 1
        return type("Receipt", (), {"intervention_sha256": "a" * 64})()

    async def close(self):
        return None


def _society(
    tmp_path: Path,
    router: _Router,
    workspace_mode: str = "restored",
) -> AgentSociety:
    for agent_id in (1, 2):
        workspace = tmp_path / "as2" / "agents" / f"agent_{agent_id:04d}"
        workspace.mkdir(parents=True)
        (workspace / "AGENT.json").write_text("{}")
    config = AgentSocietyConfig(
        run_id="run-a",
        agent_specs=(
            {"id": 1, "profile": {}, "config": {}},
            {"id": 2, "profile": {}, "config": {}},
        ),
        agent_class_name="LifeAgent",
        env_router=router,
        service_proxy=object(),
        start_time=router.current,
        end_time=router.current + timedelta(hours=1),
        run_dir=tmp_path,
        custom_root=tmp_path,
        workspace_mode=workspace_mode,
        batch_size=8,
    )
    instance = AgentSociety(config)
    instance._initialized = True
    return instance


@pytest.mark.asyncio
async def test_society_uses_environment_order_then_commits(monkeypatch, tmp_path) -> None:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    router = _Router(start)
    remote = _RemoteFunction(
        {
            "results": [
                {"id": 1, "decision_id": "", "ok": True},
                {"id": 2, "decision_id": "", "ok": True},
            ],
            "token_stats": {},
        }
    )

    def fill_ids(request, proxy):
        result = {
            "results": [
                {
                    "id": item.agent_id,
                    "decision_id": item.decision_id,
                    "ok": True,
                }
                for item in reversed(request.contexts)
            ],
            "token_stats": {},
        }
        remote.result = result
        return remote.remote(request, proxy)

    monkeypatch.setattr(
        society_module,
        "step_agent_batch",
        type("Task", (), {"remote": staticmethod(fill_ids)})(),
    )
    instance = _society(tmp_path, router)

    await instance.step()

    assert router.calls[0] == ("begin", (2, 1))
    assert router.calls[1][0] == "commit"
    assert router.calls[2][0] == "step"
    assert instance.current_time == start + timedelta(seconds=60)
    assert router.checkpoints[0].agent_workspaces[0][0] == 1


@pytest.mark.asyncio
async def test_society_redecides_before_advancing_when_agent_remains_ready(
    monkeypatch,
    tmp_path,
) -> None:
    """瞬时结算后仍就绪的人物先看结果并重新决策，再推进世界时钟。"""
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    router = _Router(start)

    async def commit(round_id):
        router.calls.append(("commit", round_id))
        router.version += 1
        commits = [item for item in router.calls if item[0] == "commit"]
        router.ready = (2,) if len(commits) == 1 else ()
        return [
            {"agent_id": agent_id, "status": "rejected"}
            for agent_id in router.round_agents
        ]

    def decide(request, _proxy):
        async def done():
            return {
                "results": [
                    {"id": item.agent_id, "decision_id": item.decision_id, "ok": True}
                    for item in request.contexts
                ],
                "token_stats": {},
            }

        return done()

    router.commit_decision_round = commit
    monkeypatch.setattr(
        society_module,
        "step_agent_batch",
        type("Task", (), {"remote": staticmethod(decide)})(),
    )
    instance = _society(tmp_path, router)

    await instance.step()

    assert instance.current_time == start
    assert [item[0] for item in router.calls] == ["begin", "commit"]
    assert router.checkpoints[0].current_time == start

    await instance.step()

    begins = [item for item in router.calls if item[0] == "begin"]
    assert begins == [("begin", (2, 1)), ("begin", (2,))]
    assert [item[0] for item in router.calls][-2:] == ["step", "clock"]
    assert instance.current_time == start + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_society_aborts_before_commit_on_agent_failure(monkeypatch, tmp_path) -> None:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    router = _Router(start)

    def failed(request, _proxy):
        async def done():
            item = request.contexts[0]
            return {
                "results": [
                    {
                        "id": item.agent_id,
                        "decision_id": item.decision_id,
                        "ok": False,
                        "error": "boom",
                    }
                ],
                "token_stats": {},
            }

        return done()

    monkeypatch.setattr(
        society_module,
        "step_agent_batch",
        type("Task", (), {"remote": staticmethod(failed)})(),
    )
    instance = _society(tmp_path, router)

    with pytest.raises(RuntimeError, match="do not match|failed"):
        await instance.step()

    assert [item[0] for item in router.calls] == ["begin", "abort"]
    assert router.checkpoints == []


@pytest.mark.asyncio
async def test_society_stops_after_committed_fatal_agent_result(monkeypatch, tmp_path) -> None:
    """人物致命结果已原子提交后停止运行，不能再中止同一轮。"""
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    router = _Router(start)

    def decide(request, _proxy):
        async def done():
            return {
                "results": [
                    {"id": item.agent_id, "decision_id": item.decision_id, "ok": True}
                    for item in request.contexts
                ],
                "token_stats": {},
            }

        return done()

    async def fatal_commit(round_id):
        router.calls.append(("commit", round_id))
        router.ready = ()
        return [
            {"agent_id": 2, "status": "failed"},
            {"agent_id": 1, "status": "completed"},
        ]

    router.commit_decision_round = fatal_commit
    monkeypatch.setattr(
        society_module,
        "step_agent_batch",
        type("Task", (), {"remote": staticmethod(decide)})(),
    )
    instance = _society(tmp_path, router)
    instance._environment_started = True

    with pytest.raises(RuntimeError, match="fatal agent result"):
        await instance.run()

    assert [item[0] for item in router.calls] == ["begin", "commit"]
    assert len(router.failures) == 1
    assert router.checkpoints == []


@pytest.mark.asyncio
async def test_society_records_checkpoint_failure_as_run_failure(tmp_path) -> None:
    """推进完成但共同检查点失败时不能留下看似成功的运行。"""
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    router = _Router(start)
    router.ready = ()

    async def fail_checkpoint(_request):
        raise OSError("checkpoint failed")

    router.checkpoint = fail_checkpoint
    instance = _society(tmp_path, router)
    instance._environment_started = True

    with pytest.raises(OSError, match="checkpoint failed"):
        await instance.run()

    assert len(router.failures) == 1
    assert "checkpoint failed" in router.failures[0]


@pytest.mark.asyncio
async def test_society_checkpoints_counterfactual_before_first_decision(tmp_path) -> None:
    """反事实干预推进一个版本并在任何人物决策前形成共同检查点。"""
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    router = _Router(start)
    instance = _society(tmp_path, router)
    instance._environment_started = True
    intervention = object()

    receipt = await instance.apply_counterfactual(intervention, "a" * 64)

    assert receipt.intervention_sha256 == "a" * 64
    assert router.calls == [("counterfactual", intervention)]
    assert router.checkpoints[0].round_id == f"counterfactual:{'a' * 64}"
    assert router.checkpoints[0].current_time == start
    assert router.failures == []


@pytest.mark.asyncio
async def test_society_stops_when_counterfactual_identity_disagrees(tmp_path) -> None:
    """环境提交不同干预身份时标记致命失败且不创建共同检查点。"""
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    router = _Router(start)
    instance = _society(tmp_path, router)
    instance._environment_started = True

    with pytest.raises(RuntimeError, match="different counterfactual identity"):
        await instance.apply_counterfactual(object(), "b" * 64)

    assert router.checkpoints == []
    assert len(router.failures) == 1


@pytest.mark.asyncio
async def test_society_init_checkpoint_failure_records_run_failure(monkeypatch, tmp_path) -> None:
    """环境启动后的初始共同检查点失败必须写运行故障。"""
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    router = _Router(start)

    async def fail_checkpoint(_request):
        raise OSError("initial checkpoint failed")

    async def no_agents():
        return None

    async def no_dispatchers():
        return None

    from agentsociety2.config import llm_dispatcher

    router.checkpoint = fail_checkpoint
    instance = _society(tmp_path, router, "create")
    instance._initialized = False
    instance._environment_started = False
    instance._create_agent_workspaces = no_agents
    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", no_dispatchers)

    with pytest.raises(OSError, match="initial checkpoint failed"):
        await instance.init()

    assert len(router.failures) == 1
    assert "initial checkpoint failed" in router.failures[0]
    assert not instance._initialized
