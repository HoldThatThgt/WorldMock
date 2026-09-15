"""动态工具请求的持久化、身份冻结和跨目录去重回归。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ModelCallEvidence, ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from tests.life.conftest import context, engine_factory  # noqa: F401
from worldmock.footprints.models import canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.runtime import runtime_code_sha256


def _prepared_tools(data, observed_at):
    """用明确首观察生成实际原生工具声明。"""
    agent = LifeAgent()
    observation = {"turn": 0, "action": "observe", "ok": True, "data": data}
    agent.prepare_react_loop(ReactLoopRequest(0, observed_at, [observation]))
    return agent, agent.build_react_tool_schemas(readonly=False)


def test_model_request_persists_exact_grounded_tools(engine_factory):
    """真实环境观察生成的完整工具请求经关闭重开数据库后保持原值。"""
    engine = engine_factory()
    ctx = context(engine, "grounded-wire")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    agent, tools = _prepared_tools(observation, ctx.observed_at)
    agent._model_name = "deepseek-flash"
    wire = agent._model_request([{"role": "user", "content": "Choose a visible activity."}], tools)
    expected = canonical_json(wire)
    targeted = next(item for item in tools if item["function"]["name"] == "start_targeted_activity")
    assert "enum" in targeted["function"]["parameters"]["properties"]["target_id"]
    engine.record_model_exchange(ModelCallEvidence(ctx, "grounded-call", "request", 1, wire))
    cutoff = engine.store.last_event_seq
    directory = engine.store.run_dir
    engine.close()

    reopened = FootprintStore(directory)
    try:
        requests = [event for event in reopened.read_events(cutoff) if event.event_type == "model_request"]
        assert len(requests) == 1
        assert canonical_json(requests[0].data["request"]) == expected
        assert requests[0].data["request"]["tools"] == tools
    finally:
        reopened.close()


def test_runtime_code_identity_includes_life_agent_content(tmp_path: Path):
    """只改变人物实现字节就必须改变整个运行身份。"""
    for name in ("pyproject.toml", "uv.lock"):
        (tmp_path / name).write_text("frozen dependency fixture\n")
    source = tmp_path / "custom" / "agents" / "life_agent.py"
    source.parent.mkdir(parents=True)
    source.write_text("TARGET_SOURCE = 'current_observation'\n")
    before = runtime_code_sha256(tmp_path)

    source.write_text("TARGET_SOURCE = 'unbound'\n")

    assert runtime_code_sha256(tmp_path) != before


def test_grounded_tool_deduplicates_target_ids_across_catalogs(engine_factory):
    """不同目录中的相同身份在最终目标枚举中只出现一次。"""
    engine = engine_factory()
    ctx = context(engine, "cross-catalog-id")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    observation["tasks"] = [{"task_id": "shared-id"}]
    observation["content_catalog"] = [{"item_id": "shared-id"}]
    _, tools = _prepared_tools(observation, ctx.observed_at)
    targeted = next(item for item in tools if item["function"]["name"] == "start_targeted_activity")
    ids = targeted["function"]["parameters"]["properties"]["target_id"]["enum"]

    assert ids.count("shared-id") == 1
    assert ids == sorted(set(ids))
