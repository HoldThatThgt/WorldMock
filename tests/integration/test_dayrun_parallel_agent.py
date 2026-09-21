"""@brief 人物动态组合工具与独立活动控制的正式观察边界验证。"""

from __future__ import annotations

import os

import pytest
from jsonschema import Draft202012Validator

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://openrouter.ai/api/v1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision
from tests.integration.activity_fixtures import occupied_resources, paused_activity
from tests.integration.test_grounded_targets import (
    _Environment, _active, _catalogs, _context, _functions, _prepare,
)
from worldmock.life.models import StartParallelActivitiesRequest


def _decision(action: str, args: dict) -> ReactDecision:
    """构造一次实际解析后的模型工具决定。"""
    return ReactDecision("", action, args, "")


def _watch(target_id: str = "content-visible") -> dict:
    """从本轮真实内容目录选择一个可播放目标。"""
    return {"activity_type": "watch", "target_id": target_id,
            "planned_duration_seconds": 120, "playback_speed": 1}


def _wait() -> dict:
    """构造占用身体位置的基础等待请求。"""
    return {"activity_type": "wait", "planned_duration_seconds": 120}


def _parallel(*children: dict) -> dict:
    """将两项或更多原有启动请求装入一个原子生活动作。"""
    return {"activities": list(children)}


def test_parallel_schema_binds_each_visible_child_and_local_pairs() -> None:
    """组合子项复用本轮真实单项 Schema，类型与身份再由本地准确配对。"""
    agent = _prepare(_catalogs())
    functions = _functions(agent)
    parallel = functions["start_parallel_activities"]["parameters"]
    children = parallel["properties"]["activities"]["items"]["anyOf"]
    by_fields = {frozenset(item["properties"]): item for item in children}
    targeted = by_fields[frozenset(functions["start_targeted_activity"]["parameters"]["properties"])]
    basic = by_fields[frozenset(functions["start_basic_activity"]["parameters"]["properties"])]
    assert targeted == functions["start_targeted_activity"]["parameters"]
    assert basic == functions["start_basic_activity"]["parameters"]
    Draft202012Validator.check_schema(parallel)
    valid = _parallel(_watch(), _wait())
    assert list(Draft202012Validator(parallel).iter_errors(valid)) == []
    assert agent.validate_react_decisions([_decision("start_parallel_activities", valid)], False) == ""
    unseen = _parallel(_watch("invented-content"), _wait())
    assert list(Draft202012Validator(parallel).iter_errors(unseen))
    assert "not visible" in agent.validate_react_decisions(
        [_decision("start_parallel_activities", unseen)], False)
    mismatched = _parallel(_watch("task-a"), _wait())
    assert "not visible" in agent.validate_react_decisions(
        [_decision("start_parallel_activities", mismatched)], False)


@pytest.mark.parametrize("children", [
    (_watch(), _watch()),
    (_wait(), {"destination_id": "office", "travel_mode": "driving",
               "planned_duration_seconds": 216}),
])
def test_parallel_rejects_shared_resources_before_dispatch(children: tuple[dict, dict]) -> None:
    """两项可见且单项合法时，资源相交仍原子拒绝。"""
    agent = _prepare(_catalogs())
    error = agent.validate_react_decisions(
        [_decision("start_parallel_activities", _parallel(*children))], False)
    assert "conflicting activity resources" in error


@pytest.mark.asyncio
async def test_parallel_dispatch_submits_one_typed_action() -> None:
    """人物只向宿主提交一个组合动作，子项请求保持原类型。"""
    agent = _prepare(_catalogs())
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    payload = _parallel(_watch(), _wait())
    assert agent.validate_react_decisions([_decision("start_parallel_activities", payload)], False) == ""
    await agent.dispatch_react_tool("start_parallel_activities", payload)
    assert len(environment.calls) == 1
    request = environment.calls[0].request
    assert isinstance(request, StartParallelActivitiesRequest)
    assert [child.activity_type for child in request.activities] == ["watch", "wait"]


def test_active_watch_keeps_compatible_start_and_its_own_controls() -> None:
    """已播放活动不自动暂停，人物仍可启动占位等待并单独控制播放。"""
    agent = _prepare(_catalogs(), active_activity=_active("watch"))
    functions = _functions(agent)
    assert "start_basic_activity" in functions and "set_activity_state" in functions
    assert "start_targeted_activity" not in functions
    assert functions["set_activity_state"]["parameters"]["properties"]["activity_id"]["enum"] == [
        "activity-watch"]
    assert agent.validate_react_decisions([_decision("start_basic_activity", _wait())], False) == ""
    assert agent.validate_react_decisions([
        _decision("set_activity_state", {"activity_id": "activity-watch", "operation": "pause"})], False) == ""


def test_two_active_activities_bind_independent_control_identities() -> None:
    """两个相容活动各有控制身份，变速只属于播放。"""
    rows = [_active("watch"), _active("travel")]
    agent = _prepare(_catalogs(), overrides={
        "active_activities": rows, "occupied_resources": occupied_resources(rows)})
    functions = _functions(agent)
    assert functions["set_activity_state"]["parameters"]["properties"]["activity_id"]["enum"] == [
        "activity-travel", "activity-watch"]
    assert functions["change_playback_speed"]["parameters"]["properties"]["activity_id"]["enum"] == [
        "activity-watch"]
    invalid = {"activity_id": "activity-travel", "playback_speed": 2}
    assert "not available for this operation" in agent.validate_react_decisions(
        [_decision("change_playback_speed", invalid)], False)
    for identity in ("activity-travel", "activity-watch"):
        assert agent.validate_react_decisions([
            _decision("set_activity_state", {"activity_id": identity, "operation": "cancel"})], False) == ""


@pytest.mark.parametrize("kind", ["chat", "call"])
def test_social_active_activity_only_offers_cancel(kind: str) -> None:
    """社交活动跨决策边界保留自身取消权，但不能暂停、恢复或普通继续。"""
    agent = _prepare(_catalogs(), active_activity=_active(kind))
    names = _functions(agent)
    assert names["set_activity_state"]["parameters"]["properties"]["operation"]["enum"] == ["cancel"]
    assert "continue_activity" not in names
    assert agent.validate_react_decisions([
        _decision("set_activity_state", {"activity_id": f"activity-{kind}",
                                         "operation": "cancel"})], False) == ""
    assert "not valid" in agent.validate_react_decisions([
        _decision("set_activity_state", {"activity_id": f"activity-{kind}",
                                         "operation": "pause"})], False)


@pytest.mark.parametrize("overrides,match", [
    ({"active_activities": "broken"}, "active_activities must be a list"),
    ({"occupied_resources": ["alien"]}, "occupied resources are invalid"),
    ({"occupied_resources": ["position", "position"]}, "occupied resources are invalid"),
    ({"paused_activities": [paused_activity("paused-watch", kind="watch",
                                            resource_claims=["hands"])]},
     "paused activity resource declaration is inconsistent"),
])
def test_malformed_activity_observations_fail_closed(overrides: dict, match: str) -> None:
    """损坏的活动集合与资源投影不能静默退化为可执行工具。"""
    with pytest.raises(RuntimeError, match=match):
        _prepare(_catalogs(), overrides=overrides)
