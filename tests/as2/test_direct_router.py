"""直接环境路由不经过 CodeGen 的委托测试。"""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from agentsociety2.env.router_direct import DirectRouter
from agentsociety2.society import DecisionContext, DirectToolCall


class _Request(BaseModel):
    value: int


class _Environment:
    def __init__(self):
        self.memory_evidence = None

    async def begin_decision_round(self, contexts):
        return contexts[0].round_id

    async def observe(self, context):
        return {"agent_id": context.agent_id}

    async def submit_action(self, call):
        return {"action_id": call.action_id}

    async def commit_decision_round(self, round_id):
        return [round_id]

    async def abort_decision_round(self, round_id, error):
        return None

    async def scheduling_state(self):
        return None

    async def record_model_exchange(self, exchange):
        return None

    async def record_memory_update(self, evidence):
        self.memory_evidence = evidence

    async def apply_counterfactual(self, intervention):
        self.intervention = intervention
        return {"intervention": intervention}

    async def checkpoint(self, request):
        return "checkpoint"

    async def fail_run(self, error):
        self.error = error


def _router(*modules) -> DirectRouter:
    router = object.__new__(DirectRouter)
    router.env_modules = list(modules)
    return router


@pytest.mark.asyncio
async def test_direct_router_delegates_typed_call() -> None:
    from datetime import datetime, timezone

    context = DecisionContext(
        "run",
        "round",
        "decision",
        1,
        datetime(2026, 1, 5, tzinfo=timezone.utc),
        0,
    )
    call = DirectToolCall(context, "action", "purchase", _Request(value=3))
    router = _router(_Environment())

    assert await router.begin_decision_round((context,)) == "round"
    assert await router.observe(context) == {"agent_id": 1}
    assert await router.submit_action(call) == {"action_id": "action"}
    evidence = SimpleNamespace(operation="append_episodes")
    await router.record_memory_update(evidence)
    assert router.env_modules[0].memory_evidence is evidence
    intervention = SimpleNamespace(kind="set_balance_fen")
    assert await router.apply_counterfactual(intervention) == {"intervention": intervention}
    await router.fail_run("fatal")
    assert router.env_modules[0].error == "fatal"


@pytest.mark.asyncio
async def test_direct_router_rejects_text_and_ambiguous_environment() -> None:
    router = _router(_Environment(), _Environment())

    with pytest.raises(RuntimeError, match="exactly one"):
        await router.scheduling_state()
    with pytest.raises(RuntimeError, match="does not accept text"):
        await router.ask(SimpleNamespace())




