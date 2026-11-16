"""@brief 当前完整环境与脚本化模型的真实宿主装配。"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from tests.data_paths import ENVIRONMENT_ROOT
from tests.frozen_inputs import fixture_metadata
from worldmock.configuration.inputs.environment import load_environment
from worldmock.runtime.framework.setup import creation_inputs, stage_model_identity

if TYPE_CHECKING:
    from worldmock.agents.life_agent import LifeAgent
    from worldmock.agents.protocol.react_loop import ReactDecision


ROOT = Path(__file__).resolve().parents[3]


def inputs():
    return load_environment(ENVIRONMENT_ROOT, metadata_loader=fixture_metadata)


def profile():
    frozen = inputs()
    return creation_inputs(frozen)[0][0]["profile"]


def metadata_service(monkeypatch, frozen=None):
    """@brief 只替换只读元数据 HTTP，严格输入与完整宿主代码仍走生产实现。"""
    import requests

    frozen = frozen or inputs()
    calls = []

    def get(url, **arguments):
        calls.append((url, arguments))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [
            {"id": frozen.config.model.requested_model, "context_window": frozen.model_metadata.context_window,
             "max_output_tokens": frozen.model_metadata.max_output_tokens}]})

    monkeypatch.setenv(frozen.config.model.api_key_env, "offline-runtime-test-secret")
    monkeypatch.setattr(requests, "get", get)
    return calls


class ToolResponse:
    """@brief 唯一工具调用的提供商形状响应。"""

    def __init__(self, name, arguments, call_id="call-test", reasoning="我先看看眼下的事情。"):
        self.document = {"choices": [{"message": {"content": "", "reasoning_content": reasoning,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}}]}
        item = self.document["choices"][0]["message"]
        self.choices = [SimpleNamespace(message=SimpleNamespace(**{**item, "tool_calls": [
            SimpleNamespace(**{**call, "function": SimpleNamespace(**call["function"])})
            for call in item["tool_calls"]]}))]

    def model_dump(self, *, mode):
        assert mode == "json"
        return copy.deepcopy(self.document)


class ScriptedModel:
    """@brief 只替换外部传输，保留真实请求构造与严格工具循环。"""

    model_name = "test-model"

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    async def call(self, request):
        """@brief 记下实际交给模型客户端的请求副本，返回下一条脚本响应。"""
        self.requests.append(copy.deepcopy(request))
        if not self.responses:
            raise AssertionError("Scripted model responses exhausted")
        return self.responses.pop(0)


def person(engine, context, model, turns: int = 16) -> LifeAgent:
    """@brief 真实 LifeAgent：冻结输入取自夹具人设表与清单的模型身份，名字「林舟」，不做步尾保存（记忆存储为 None）。"""
    from worldmock.agents.life_agent import LifeAgent, person_inputs

    frozen = inputs()
    identity = stage_model_identity(frozen.config.model, frozen.model_metadata)
    person_input = replace(person_inputs(frozen.persona, identity), name="林舟",
                           model_identity={**identity, "max_react_turns": turns})
    return LifeAgent(person_input, engine, model, None)


async def turns_with_history(agent, context, observation) -> tuple[list[ReactDecision], list[dict[str, Any]]]:
    """@brief 与 LifeAgent.run_turns 相同的三步，另交还回合循环结束时的消息历史（人物不保存历史）。"""
    from worldmock.agents.protocol.react_loop import ReactLoop

    agent._context = context
    budget, history = agent._prepare(observation)
    loop = ReactLoop(agent, context, budget, history)
    registered = await loop.run()
    return registered, loop._history


async def decide(engine, round_id, responses):
    """@brief 开轮、观察、只跑回合循环、提交；返回观察、模型请求、登记的工具名与提交结果。"""
    from tests.life.conftest import context

    frozen = context(engine, round_id)
    engine.begin_decision_round([frozen])
    observed = engine.observe(frozen)
    model = ScriptedModel(responses)
    registered = await person(engine, frozen, model).run_turns(frozen, observed)
    committed = engine.commit_decision_round(round_id)
    return observed, model.requests, [decision.action for decision in registered], committed
