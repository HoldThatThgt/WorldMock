"""人物用一组可读对象和逐项动作启动兼容的并行活动。"""

from __future__ import annotations

import copy
import os

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://openrouter.ai/api/v1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.society import DirectToolCall
from custom.agents.life_agent import LifeAgent
from tests.integration.test_life_agent_protocol import _context, _observations
from tests.life.conftest import context, engine_factory as _engine_factory, stage_day_plan
from worldmock.life.models import StartActivityRequest


def _prepared() -> LifeAgent:
    """在正式观察结构中增加可读地点与内容对象。"""
    observed = copy.deepcopy(_observations())
    data = observed[0]["data"]
    data["facts"]["interactive_objects"].extend([
        {"object": "地点：办公室", "actions": ["步行前往"], "preconditions_met": True},
        {"object": "内容：午间新闻", "actions": ["观看"], "preconditions_met": True},
    ])
    data["interaction_bindings"].extend([
        {"object": "地点：办公室", "kind": "location", "object_id": "office",
         "actions": [{"label": "步行前往", "canonical_action": "travel_to", "travel_mode": "walking"}]},
        {"object": "内容：午间新闻", "kind": "content", "object_id": "content-visible",
         "actions": [{"label": "观看", "canonical_action": "watch"}]},
    ])
    person = LifeAgent()
    person.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observed))
    return person


def _decision(targets: list[str], actions: list[str]) -> ReactDecision:
    """构造一次真实 ReAct 工具决定。"""
    return ReactDecision("", "start_activity", {"targets": targets, "actions": actions}, "")


def test_parallel_schema_and_local_pairing_use_readable_arrays() -> None:
    """单工具接收等长逐项对象动作，拒绝错配和重复对象。"""
    person = _prepared()
    functions = {row["function"]["name"]: row["function"]
                 for row in person.build_react_tool_schemas(readonly=False)}
    assert "start_parallel_activities" not in functions
    schema = functions["start_activity"]["parameters"]
    assert set(schema["properties"]) == {"targets", "actions"}
    assert {"地点：办公室", "内容：午间新闻"} <= set(schema["properties"]["targets"]["items"]["enum"])
    assert {"步行前往", "观看"} <= set(schema["properties"]["actions"]["items"]["enum"])
    assert person.validate_react_decisions([
        _decision(["地点：办公室", "内容：午间新闻"], ["步行前往", "观看"]),
    ], False) == ""
    assert "equal lengths" in person.validate_react_decisions([
        _decision(["地点：办公室", "内容：午间新闻"], ["步行前往"]),
    ], False)
    assert "not offered" in person.validate_react_decisions([
        _decision(["地点：办公室", "内容：午间新闻"], ["观看", "步行前往"]),
    ], False)
    assert "unique" in person.validate_react_decisions([
        _decision(["地点：办公室", "地点：办公室"], ["步行前往", "步行前往"]),
    ], False)


def test_parallel_start_is_atomic_in_real_engine(_engine_factory) -> None:
    """真实引擎从同一观察启动出行与观看，留下两条独立会话。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    current = context(engine, "parallel-walk-watch")
    engine.begin_decision_round([current])
    observed = engine.observe(current)
    place = next(row for row in observed["interaction_bindings"]
                 if row["kind"] == "location" and any(
                     action.get("travel_mode") == "walking" for action in row["actions"]))
    content = next(row for row in observed["interaction_bindings"]
                   if row["kind"] == "content" and any(
                       action["canonical_action"] == "watch" for action in row["actions"]))
    travel = next(action["label"] for action in place["actions"]
                  if action.get("travel_mode") == "walking")
    request = StartActivityRequest(
        targets=[place["object"], content["object"]], actions=[travel, "观看"])
    before = engine.state.model_dump(mode="json")
    engine.submit_action(DirectToolCall(current, "parallel-readable", "start_activity", request))
    assert engine.state.model_dump(mode="json") == before
    result = engine.commit_decision_round(current.round_id)[0]
    assert result.status.value == "in_progress"
    assert len(engine.state.sessions) == 2
    assert len(engine.state.activities) == 2
    assert {session.session_name.split(" ", 1)[0] for session in engine.state.sessions.values()} == {
        travel, "观看",
    }
