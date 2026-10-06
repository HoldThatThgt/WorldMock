"""@brief 真实引擎和人物循环的共享夹具，只替换远程模型传输。"""

from __future__ import annotations

import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace

from agentsociety2.agent.base import ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv
from tests.integration.test_life_agent_protocol import _ModelResponse
from tests.life.conftest import context


class TruncatedResponse:
    """思考用尽输出上限的回复（形状取自 4a5b0e6 运行记录）：finish_reason=length，正文为空，没有工具调用，思考写满。"""

    def __init__(self, reasoning: str, tokens: int = 8000) -> None:
        self._reasoning, self._tokens = reasoning, tokens
        message = SimpleNamespace(content="", tool_calls=None, reasoning_content=reasoning)
        self.choices = [SimpleNamespace(finish_reason="length", index=0, message=message)]

    def model_dump(self, *, mode: str) -> dict:
        """提供商形状的回复：usage 带输出 token 与其中的思考 token。"""
        assert mode == "json"
        message = {"role": "assistant", "content": "", "tool_calls": None, "reasoning_content": self._reasoning}
        return {"model": "deepseek-flash", "choices": [{"finish_reason": "length", "index": 0, "message": message}],
                "usage": {"prompt_tokens": 12000, "completion_tokens": self._tokens,
                          "total_tokens": 12000 + self._tokens, "prompt_cache_hit_tokens": 2000,
                          "prompt_cache_miss_tokens": 10000,
                          "completion_tokens_details": {"reasoning_tokens": self._tokens}}}


class ScriptedModel:
    """@brief 返回提供商形状响应，并保存实际发给模型的完整请求。"""

    model_name = "deepseek-flash"

    def __init__(self, responses: list) -> None:
        self.requests = []
        self.responses = list(responses)

    async def call(self, **request):
        """@brief 捕获真实请求并返回下一条预先指定的响应。"""
        self.requests.append(copy.deepcopy(request))
        return self.responses.pop(0)


def reply(call_id: str, tool: str, arguments: dict, reasoning: str):
    """@brief 使用供应商实际响应形状提交一次人物选择。"""
    return _ModelResponse(call_id, tool, json.dumps(arguments, ensure_ascii=False), reasoning_content=reasoning)


def environment_for(engine):
    """@brief 绑定真实生活引擎到生产环境适配器。"""
    environment = LifeEnv()
    environment.engine = engine
    return environment


async def decide(environment, round_id: str, responses):
    """@brief 开启真实轮、观察和人物循环，结算后返回观察、模型请求与实际结果。"""
    engine = environment.engine
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observation = await environment.observe(decision)
    model = ScriptedModel(responses(observation) if callable(responses) else responses)
    person = LifeAgent()
    person._id = decision.agent_id
    person._name = engine.context.state.agents[decision.agent_id].name
    person._profile = {"id": decision.agent_id, "name": person._name}
    person._env, person._dispatcher, person._model_name = environment, model, model.model_name
    person._decision_context, person._max_react_turns, person._enable_memory = decision, 3, False
    person.trace_span = lambda *args, **kwargs: nullcontext(SimpleNamespace(attributes={}))
    await person.run_react_loop(ReactLoopRequest(0, decision.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observation}]))
    [result] = engine.commit_decision_round(round_id)
    return observation, model.requests, result
