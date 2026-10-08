"""直接人物 runner 的故障保真测试。"""

from datetime import datetime, timezone

import pytest

import agentsociety2.agent.runner as runner
from agentsociety2.agent.runner import _step_agent_batch_async
from agentsociety2.society import AgentStepBatchRequest, DecisionContext


@pytest.mark.asyncio
async def test_runner_preserves_step_and_close_failures(monkeypatch, tmp_path) -> None:
    """人物步骤和关闭同时失败时，批次结果保留两个根因。"""
    class Agent:
        def bind_decision_context(self, context):
            self.context = context

        async def step(self, tick, observed_at):
            raise ValueError("step failed")

        async def to_workspace(self, workspace):
            raise AssertionError("workspace write must not run")

        async def close(self):
            raise OSError("close failed")

    class AgentClass:
        @classmethod
        async def from_workspace(cls, workspace, service):
            return Agent()

    monkeypatch.setattr(runner, "_resolve_custom_agent", lambda root, name: AgentClass)
    observed = datetime(2026, 1, 5, tzinfo=timezone.utc)
    context = DecisionContext("run", "round", "decision", 1, observed, 0)
    request = AgentStepBatchRequest(
        (context,),
        str(tmp_path),
        str(tmp_path),
        "LifeAgent",
        0,
    )

    results = await _step_agent_batch_async(request, object())

    assert not results[0]["ok"]
    assert "step failed" in results[0]["error"]
    assert "close failed" in results[0]["error"]
