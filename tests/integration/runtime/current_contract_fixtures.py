"""@brief 当前完整环境与脚本化模型的真实宿主装配。"""

from __future__ import annotations

import copy
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from tests.data_paths import ENVIRONMENT_ROOT
from tests.frozen_inputs import fixture_metadata
from worldmock.configuration.inputs.environment import load_environment
from worldmock.runtime.framework.setup import creation_inputs, stage_model_identity


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


#: 进程配置写入的十二个环境变量，加上它前置项目根的 PYTHONPATH。
PROCESS_VARIABLES = ("WORKSPACE_PATH", "AGENTSOCIETY_LLM_API_KEY", "AGENTSOCIETY_LLM_API_BASE", "AGENTSOCIETY_LLM_MODEL",
                     "AGENTSOCIETY_CODER_LLM_API_KEY", "AGENTSOCIETY_CODER_LLM_API_BASE",
                     "AGENTSOCIETY_CODER_LLM_MODEL", "AGENTSOCIETY_EMBEDDING_MODEL", "LITELLM_LOCAL_MODEL_COST_MAP",
                     "AGENTSOCIETY_LLM_REQUEST_TIMEOUT", "MEM0_TELEMETRY", "ANONYMIZED_TELEMETRY", "PYTHONPATH")


def aligned_process(monkeypatch, frozen):
    """@brief 让直接调用准备函数的进程内用例走真实的进程配置。

    pytest 进程里 AgentSociety2 早已按 conftest 的值加载：把已加载的配置对上这份环境的端点与模型，
    并用 monkeypatch 隔离进程配置会写的环境变量与 sys.path（假项目根会被插到最前，不隔离会污染之后的导入）。
    """
    from agentsociety2.config.config import Config

    for name in ("LLM_API_BASE", "CODER_LLM_API_BASE"):
        monkeypatch.setattr(Config, name, frozen.config.model.base_url)
    for name in ("LLM_MODEL", "CODER_LLM_MODEL"):
        monkeypatch.setattr(Config, name, frozen.config.model.requested_model)
    monkeypatch.setattr(Config, "EMBEDDING_MODEL", "")
    for name in PROCESS_VARIABLES:
        monkeypatch.setenv(name, os.environ.get(name, ""))
    monkeypatch.setattr(sys, "path", list(sys.path))


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

    async def call(self, **request):
        self.requests.append(copy.deepcopy(request))
        if not self.responses:
            raise AssertionError("Scripted model responses exhausted")
        return self.responses.pop(0)


def person(environment, context, model):
    from custom.agents.life_agent import LifeAgent

    agent = LifeAgent()
    agent._id, agent._name, agent._profile = context.agent_id, "林舟", profile()
    agent._env, agent._dispatcher, agent._model_name = environment, model, model.model_name
    agent._decision_context, agent._max_react_turns, agent._enable_memory = context, 16, False
    agent.trace_span = lambda *args, **kwargs: nullcontext(SimpleNamespace(attributes={}))
    return agent


def environment(engine):
    from custom.envs.life_env import LifeEnv

    result = LifeEnv()
    result.engine = engine
    return result


async def decide(engine, round_id, responses):
    from agentsociety2.agent.base import ReactLoopRequest
    from tests.life.conftest import context

    env, frozen = environment(engine), context(engine, round_id)
    engine.begin_decision_round([frozen])
    observed = await env.observe(frozen)
    model = ScriptedModel(responses)
    agent = person(env, frozen, model)
    result = await agent.run_react_loop(ReactLoopRequest(0, frozen.observed_at,
        [{"turn": 0, "action": "observe", "ok": True, "data": observed}]))
    committed = engine.commit_decision_round(round_id)
    return observed, model.requests, result, committed
