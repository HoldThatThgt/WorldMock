"""人物用一组可读对象和逐项的动词与方式启动兼容的并行活动（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章）。"""

from __future__ import annotations

import copy
import os

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.society import DirectToolCall
from custom.agents.life_agent import LifeAgent
from tests.integration.test_life_agent_protocol import (
    _add_objects, _context, _observations, _offer, _visible_object,
)
from tests.engine_fixture import (engine_factory as _engine_factory)  # noqa: F401
from tests.life.conftest import (context, stage_day_plan, start_request, start_arguments)  # noqa: F401
from worldmock.life import sessions
from worldmock.life.activity_control import object_display_name
from worldmock.life.models import SessionKind, SessionStatus
from worldmock.life.sessions import moment_label

_PLACE = "地点：办公室"
_NEWS = "内容：午间新闻"


def _prepared() -> LifeAgent:
    """在正式观察结构中增加可读地点（步行）与内容对象（从没看过：接着看）。"""
    observed = copy.deepcopy(_observations())
    _add_objects(observed[0]["data"],
                 _visible_object(_PLACE, "location", "office",
                                 _offer("travel", "步行", "travel_to", travel_mode="walking")),
                 _visible_object(_NEWS, "content", "content-visible",
                                 _offer("interact", "接着看", "watch")))
    person = LifeAgent()
    person.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observed))
    return person


def _decision(targets: list[str], actions: list[str], ways: list[str]) -> ReactDecision:
    """构造一次真实 ReAct 工具决定：对象、动词与方式三个数组。"""
    return ReactDecision("", "start_activity", start_arguments(targets, actions, ways), "")


def test_parallel_schema_and_local_pairing_use_readable_items() -> None:
    """单工具接收逐项对象和方式，拒绝缺字段、错配和重复对象。"""
    person = _prepared()
    functions = {row["function"]["name"]: row["function"]
                 for row in person.build_react_tool_schemas(readonly=False)}
    assert "start_parallel_activities" not in functions
    schema = functions["start_activity"]["parameters"]
    assert set(schema["properties"]) == {"items", "answering", "prefer"}
    fields = schema["properties"]["items"]["items"]["properties"]
    assert all("enum" not in fields[name] for name in ("target", "way"))
    assert person.validate_react_decisions([
        _decision([_PLACE, _NEWS], ["travel", "interact"], ["步行", "接着看"]),
    ], False) == ""
    incomplete = start_arguments([_PLACE, _NEWS], ["travel", "interact"], ["步行", "接着看"])
    del incomplete["items"][1]["way"]
    error = person.validate_react_decisions([ReactDecision("", "start_activity", incomplete, "")], False)
    assert error.report.items[0].path == ("tool_calls", 0, "function", "arguments", "items", 1, "way")
    assert error.report.items[0].why == "This required field was omitted."
    for actions, ways in ((["interact", "travel"], ["接着看", "步行"]), (["travel", "travel"], ["步行", "步行"])):
        assert "does not support" in person.validate_react_decisions([
            _decision([_PLACE, _NEWS], actions, ways),
        ], False)
    assert "different recipient" in person.validate_react_decisions([
        _decision([_PLACE, _PLACE], ["travel", "travel"], ["步行", "步行"]),
    ], False)


def test_parallel_start_is_atomic_in_real_engine(_engine_factory) -> None:
    """真实引擎从同一观察原子启动出行与观看：两项活动各占一个名额、按「做什么＋对象＋开始时刻」命名，
    出发结束运行开始的停留会话，两项活动同属新开始的路上会话（时间与能量规格第七章「会话与活动的字段」）。"""
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
    watch = next(action["label"] for action in content["actions"] if action["canonical_action"] == "watch")
    assert travel == "步行" and watch in {"接着看", "接着听"}
    request = start_request(
        targets=[place["object"], content["object"]], actions=["travel", "interact"], ways=[travel, watch])
    before = engine.context.state.model_dump(mode="json")
    engine.submit_action(DirectToolCall(current, "parallel-readable", "start_activity", request))
    assert engine.context.state.model_dump(mode="json") == before
    result = engine.commit_decision_round(current.round_id)[0]
    assert result.status.value == "in_progress"
    assert len(engine.context.state.activities) == 2
    started = moment_label(engine.context.state.current_time, engine.context.scenario.timezone)
    assert {activity.name for activity in engine.context.state.activities.values()} == {
        f"步行前往{object_display_name(place)}（{started} 起）",
        f"{watch}{object_display_name(content)}（{started} 起）",
    }
    road = sessions.current_session(engine.context.state, 1)
    assert road.kind == SessionKind.TRANSIT and set(road.activity_ids) == set(engine.context.state.activities)
    assert [item.status for item in engine.context.state.sessions.values() if item.kind == SessionKind.STAY] == [
        SessionStatus.ENDED]
