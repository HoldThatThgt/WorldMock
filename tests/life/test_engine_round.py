"""决策轮、版本、顺序、失败与幂等测试。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agentsociety2.society import DecisionContext, DirectToolCall
from agentsociety2.agent.base import ModelCallEvidence

from tests.life.conftest import advance_next, context, submit_round
from worldmock.errors import ProtocolError
from worldmock.life.models import (
    StartBasicActivityRequest,
    StartTargetedActivityRequest,
    StartTravelRequest,
    TravelMode,
)


def test_real_wait_round_advances_and_records_complete_activity(engine_factory) -> None:
    """最小真实链路从观察到活动完成，并携带完整区间状态。"""
    engine = engine_factory()
    ctx = context(engine, "one")
    engine.begin_decision_round([ctx])
    observed = engine.observe(ctx)
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)
    receipt = engine.submit_action(DirectToolCall(ctx, "wait-one", "start_basic_activity", request))
    result = engine.commit_decision_round("one")[0]
    advance_next(engine)
    activity = engine.state.activities[result.activity_id]
    assert observed["world_state_version"] == 0
    assert receipt.status == "submitted" and result.status == "in_progress"
    assert activity.status == "completed" and activity.ended_at == activity.started_at + 60
    progress = [event for event in engine.store.read_events(engine.store.last_event_seq)
                if event.event_type == "activity_progress"][-1]
    assert progress.data["interval_end"] - progress.data["interval_start"] == 60
    assert progress.data["activity"]["ended_at"] == activity.ended_at


def test_business_rejection_is_evidence_and_not_activity(engine_factory) -> None:
    """业务拒绝仍进入同轮结果，但不建立活动。"""
    engine = engine_factory()
    _, results = submit_round(
        engine, {1: ("start_targeted_activity", StartTargetedActivityRequest(
            activity_type="watch", target_id="missing-content",
            planned_duration_seconds=60, playback_speed=1))}, "missing-content")
    assert results[0].status == "rejected"
    assert results[0].error_code == "CONTENT_NOT_FOUND"
    assert engine.state.activities == {}
    types = [event.event_type for event in engine.store.read_events(engine.store.last_event_seq)]
    assert "action_rejected" in types and "action_result" in types


def test_same_action_reuses_result_and_parameter_conflict_fails(engine_factory) -> None:
    """同参数 action_id 不重复产生副作用，参数冲突作为协议故障。"""
    engine = engine_factory()
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)
    ctx = context(engine, "first")
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(ctx, "stable-action", "start_basic_activity", request))
    engine.commit_decision_round("first")
    advance_next(engine)
    ctx = context(engine, "replay")
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(ctx, "stable-action", "start_basic_activity", request))
    replay = engine.commit_decision_round("replay")[0]
    assert replay.status == "completed"
    assert list(engine.state.activities) == ["activity-stable-action"]
    advance_next(engine)
    ctx = context(engine, "conflict")
    engine.begin_decision_round([ctx])
    changed = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=120)
    with pytest.raises(ProtocolError, match="different parameters"):
        engine.submit_action(DirectToolCall(ctx, "stable-action", "start_basic_activity", changed))
    engine.abort_decision_round("conflict", "expected conflict")


def test_abort_preserves_unexecuted_life_state_and_stale_context_fails(engine_factory) -> None:
    """中止只留下失败身份，过期版本不能继续提交。"""
    engine = engine_factory()
    ctx = context(engine, "abort")
    engine.begin_decision_round([ctx])
    request = StartBasicActivityRequest(activity_type="sleep", planned_duration_seconds=3600)
    engine.submit_action(DirectToolCall(ctx, "never-run", "start_basic_activity", request))
    engine.abort_decision_round("abort", "runner failed")
    assert engine.state.activities == {}
    assert engine.state.processed_actions["never-run"].result["status"] == "failed"
    assert any(event.event_type == "run_failed"
               for event in engine.store.read_events(engine.store.last_event_seq))
    engine.begin_decision_round([context(engine, "fresh")])
    with pytest.raises(ProtocolError, match="stale"):
        engine.submit_action(DirectToolCall(ctx, "old", "start_basic_activity", request))
    engine.abort_decision_round("fresh", "cleanup")


def test_multi_person_arrival_order_does_not_change_settlement_order(engine_factory) -> None:
    """人物调用到达顺序颠倒时，结算仍使用 begin 中的共同顺序。"""
    first = engine_factory("multi-person.json")
    second = engine_factory("multi-person.json")
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)
    expected_order = first.scheduling_state().ready_agent_ids
    for engine, order in ((first, (2, 1)), (second, (1, 2))):
        contexts = {agent_id: context(engine, "multi", agent_id) for agent_id in (1, 2)}
        scheduled = engine.scheduling_state().ready_agent_ids
        assert scheduled == expected_order
        engine.begin_decision_round([contexts[agent_id] for agent_id in scheduled])
        for agent_id in order:
            call = DirectToolCall(
                contexts[agent_id], f"multi-{agent_id}", "start_basic_activity", request)
            engine.submit_action(call)
        engine.commit_decision_round("multi")
    first_events = [event.agent_id for event in first.store.read_events(first.store.last_event_seq)
                    if event.event_type == "action_accepted"]
    second_events = [event.agent_id for event in second.store.read_events(second.store.last_event_seq)
                     if event.event_type == "action_accepted"]
    assert first_events == second_events == list(expected_order)
    assert first.state.agents[1].body == second.state.agents[1].body


def test_manifest_uses_single_window_shape(engine_factory) -> None:
    """运行 manifest 冻结唯一时间窗、完整 beta URL 和 strict 请求身份。"""
    engine = engine_factory()
    manifest = engine.store.manifest
    assert set(manifest["window"]) == {"start", "end", "timezone"}
    assert not {"start_at", "end_at", "timezone"} & manifest.keys()
    assert manifest["window"]["timezone"] == "Asia/Shanghai"
    assert manifest["code_sha256"] == "2" * 64
    identity = manifest["model_identity"]
    assert identity["base_url"] == "https://api.deepseek.com/beta"
    assert identity["tool_choice"] == "required" and identity["max_retries"] == 0
    assert "base_url_host" not in identity


def test_context_with_wrong_time_is_rejected(engine_factory) -> None:
    """即使版本相同，错误观察时刻也不能读取或行动。"""
    engine = engine_factory()
    wrong = datetime.fromtimestamp(engine.state.current_time + 1, ZoneInfo("Asia/Shanghai"))
    context_value = DecisionContext(engine.state.run_id, "wrong", "decision-wrong", 1, wrong, 0)
    with pytest.raises(ProtocolError, match="time is stale"):
        engine.observe(context_value)


def test_first_observation_exposes_every_action_target(engine_factory) -> None:
    """首轮无需猜内部 ID 即可发现内容、游戏、地点、路线和目录目标。"""
    engine = engine_factory()
    ctx = context(engine, "discover")
    engine.begin_decision_round([ctx])
    observed = engine.observe(ctx)
    assert observed["content_catalog"][0]["item_id"] == "sintel-en-subtitles"
    assert observed["game_catalog"][0]["game_id"] == "textworld-1201"
    assert {item["location_id"] for item in observed["locations"]} == {"home", "office", "market"}
    routes = {(item["destination_id"], item["mode"]) for item in observed["routes"]}
    assert routes == {("office", "driving"), ("office", "walking"),
                      ("market", "driving"), ("market", "walking")}
    assert observed["browse_targets"] == ["product_catalog"]
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)
    engine.submit_action(DirectToolCall(ctx, "discover-wait", "start_basic_activity", request))
    engine.commit_decision_round("discover")


@pytest.mark.parametrize(
    ("label", "tool_name", "action_request"),
    (
        ("watch", "start_targeted_activity", StartTargetedActivityRequest(
            activity_type="watch", target_id="sintel-en-subtitles",
            planned_duration_seconds=60, playback_speed=1)),
        ("game", "start_targeted_activity", StartTargetedActivityRequest(
            activity_type="play_game", target_id="textworld-1201",
            planned_duration_seconds=3600, playback_speed=1)),
        ("travel", "start_travel", StartTravelRequest(
            destination_id="office", travel_mode=TravelMode.DRIVING,
            planned_duration_seconds=216)),
    ),
)
def test_first_round_accepts_discovered_content_game_and_route(
    engine_factory, label, tool_name, action_request,
) -> None:
    """首轮目录中的三类身份可以直接进入对应真实领域边界。"""
    engine = engine_factory()
    ctx = context(engine, f"first-{label}")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    call = DirectToolCall(ctx, f"first-{label}", tool_name, action_request)
    engine.submit_action(call)
    result = engine.commit_decision_round(f"first-{label}")[0]
    assert result.status == "in_progress"
    activity = engine.state.activities[result.activity_id]
    assert activity.target_id == getattr(action_request, "target_id", None)
    assert activity.destination_id == getattr(action_request, "destination_id", None)


def test_model_calls_require_one_request_and_one_terminal_evidence(engine_factory) -> None:
    """协议无效响应可带 error 结束调用；重试使用新身份且禁止第二终态。"""
    engine = engine_factory()
    ctx = context(engine, "model-evidence")
    engine.begin_decision_round([ctx])
    request = {"model": "deepseek-flash", "messages": [], "tools": []}
    first = f"{ctx.decision_id}:model:0001:attempt:1"
    second = f"{ctx.decision_id}:model:0002:attempt:1"
    engine.record_model_exchange(ModelCallEvidence(ctx, first, "request", 1, request))
    engine.record_model_exchange(ModelCallEvidence(ctx, first, "response", 1, request,
                                                   response={"choices": []}, error="invalid schema"))
    engine.record_model_exchange(ModelCallEvidence(ctx, second, "request", 1, request))
    engine.record_model_exchange(ModelCallEvidence(ctx, second, "response", 1, request,
                                                   response={"choices": [{"message": {}}]}))
    with pytest.raises(ProtocolError, match="no recorded request"):
        engine.record_model_exchange(ModelCallEvidence(ctx, second, "failure", 1, request,
                                                       error="duplicate terminal"))
    wait = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)
    engine.submit_action(DirectToolCall(
        ctx, "model-evidence-wait", "start_basic_activity", wait))
    engine.commit_decision_round("model-evidence")
    exchanges = [event for event in engine.store.read_events(engine.store.last_event_seq)
                 if event.event_type in {"model_request", "model_response"}]
    assert [event.data["phase"] for event in exchanges] == ["request", "response", "request", "response"]
    assert exchanges[1].data["error"] == "invalid schema"


def test_fail_run_is_same_version_and_idempotent(engine_factory) -> None:
    """致命通知只追加一个失败事件，不伪造世界推进。"""
    engine = engine_factory()
    version = engine.state.version
    engine.fail_run("checkpoint failed")
    engine.fail_run("duplicate notification")
    failures = [event for event in engine.store.read_events(engine.store.last_event_seq)
                if event.event_type == "run_failed"]
    assert len(failures) == 1
    assert engine.state.version == version == engine.store.state["version"]
