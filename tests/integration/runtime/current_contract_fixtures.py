"""@brief 当前完整环境与脚本化模型的真实宿主装配。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from tests.data_paths import ENVIRONMENT_ROOT
from tests.frozen_inputs import fixture_metadata
from worldmock.configuration.inputs.environment import load_environment
from worldmock.configuration.inputs.models import referenced_profile_ids
from worldmock.configuration.model.model_registry import ModelRegistry, ResolvedModelBinding
from worldmock.runtime.framework.setup import creation_inputs

if TYPE_CHECKING:
    from worldmock.agents.life_agent import LifeAgent


ROOT = Path(__file__).resolve().parents[3]


def inputs(environment_root: Path | None = None):
    """@brief 以离线夹具元数据加载当前规范环境输入。"""
    return load_environment(environment_root or ENVIRONMENT_ROOT, metadata_loader=fixture_metadata)


def profile():
    """@brief 返回默认环境第一个人物的卡片配置资料。"""
    frozen = inputs()
    return creation_inputs(frozen)[0][0]["profile"]


def metadata_service(monkeypatch, frozen=None):
    """@brief 只替换只读元数据 HTTP 并注入激活档案凭据，其余仍走完整生产实现。"""
    import requests

    frozen = frozen or inputs()
    calls = []
    active_ids = referenced_profile_ids(frozen.config.model_routing)
    profiles = [frozen.config.models[profile_id] for profile_id in active_ids]
    entries = [
        {
            "id": profile_cfg.requested_model,
            "context_window": frozen.model_metadata[profile_id].context_window,
            "max_output_tokens": frozen.model_metadata[profile_id].max_output_tokens,
        }
        for profile_id, profile_cfg in zip(active_ids, profiles, strict=True)
    ]

    def get(url, **arguments):
        calls.append((url, arguments))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": entries})

    for profile_cfg in profiles:
        monkeypatch.setenv(profile_cfg.api_key_env, "offline-runtime-test-secret")
    monkeypatch.setattr(requests, "get", get)
    return calls


class ToolResponse:
    """@brief 唯一工具调用的提供商形状响应。"""

    def __init__(self, name, arguments, call_id="call-test", reasoning="我先看看眼下的事情。", content=""):
        self.document = {"choices": [{"message": {"content": content, "reasoning_content": reasoning,
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

    async def close(self) -> None:
        """@brief 满足 ModelRegistry 回收接口的空关闭方法。"""


def resolved_binding(
    model,
    frozen=None,
    *,
    stage: str = "action",
    agent_id: int = 1,
    max_turns: int | None = None,
) -> ResolvedModelBinding:
    """@brief 使用脚本客户端通过真实 ModelRegistry 解析阶段与人物绑定，可选覆盖回合上限。"""
    env = frozen or inputs()
    binding = ModelRegistry(env.config, env.model_metadata, client_factory=lambda _profile: model).resolve(
        stage, agent_id,
    )
    if max_turns is None or binding.profile.max_react_turns == max_turns:
        return binding
    return ResolvedModelBinding(
        profile_id=binding.profile_id,
        profile=binding.profile.model_copy(update={"max_react_turns": max_turns}),
        metadata=binding.metadata,
        client=binding.client,
        effective_max_tokens=binding.effective_max_tokens,
    )


def person(agent_id: int = 1, frozen=None) -> LifeAgent:
    """@brief 无状态生活人物适配器：由环境对应人物档案构造 PersonInputs 与 LifeAgent。"""
    from worldmock.agents.life_agent import LifeAgent, person_inputs

    env = frozen or inputs()
    return LifeAgent(person_inputs(env.persona_by_id[agent_id]))


async def turns_with_history(
    agent: LifeAgent,
    wakeup,
    sandbox,
    resolved: ResolvedModelBinding,
) -> tuple[Any, list[dict[str, Any]]]:
    """@brief 运行一次 decide_wakeup 并一并交还通用循环结束时的消息历史副本。"""
    from worldmock.agents.protocol import agent_loop as loop_module

    captured: list[list[dict[str, Any]]] = []
    original_run = loop_module.AgentLoop.run

    async def recording_run(self, initial_messages):
        result = await original_run(self, initial_messages)
        captured.append(copy.deepcopy(list(result.messages)))
        return result

    loop_module.AgentLoop.run = recording_run
    try:
        outcome = await agent.decide_wakeup(wakeup, sandbox, resolved)
    finally:
        loop_module.AgentLoop.run = original_run
    return outcome, (captured[-1] if captured else [])


async def decide(engine, label: str, responses, agent_id: int = 1):
    """@brief 准备屏障、开启沙盒、经 LifeAgent 运行并提交屏障；返回观察、模型请求、已登记工具名与动作结果。"""
    from tests.life.environment_runtime import action_result
    from worldmock.agents.life_agent import LifeAgent, person_inputs

    del label
    seq_before = engine.context.store.last_event_seq
    [wakeup] = engine.prepare_barrier((agent_id,))
    sandbox = engine.open_sandbox(wakeup)
    model = ScriptedModel(responses)
    frozen = engine.context.inputs
    binding = resolved_binding(model, frozen=frozen, stage="action", agent_id=agent_id)
    outcome = await LifeAgent(person_inputs(frozen.persona_by_id[agent_id])).decide_wakeup(wakeup, sandbox, binding)
    engine.commit_barrier([outcome])
    committed = [
        event for event in engine.context.store.iter_events(engine.context.store.last_event_seq)
        if event.event_seq > seq_before and event.wakeup_id == wakeup.wakeup_id
    ]
    answer = [event.data["tool_name"] for event in committed if event.event_type == "action_requested"]
    results = [action_result(engine, event.action_id) for event in committed if event.event_type == "action_result"]
    return wakeup.observation, model.requests, answer, results
