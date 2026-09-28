"""记忆更新只引用本轮真实观察的完整事实窗口；写入点（trigger）由引擎按登记推导。

记忆写入点只有结束活动（activity_conclusion）与就寝反思（reflection）（docs/dev/SPEC-time-and-energy.md 第七章
「finish 并入 conclude_activity」）：同一决策登记了带非空 memories 的 conclude_activity 时含 activity_conclusion，
登记后结算被拒时记忆照写；绑定观察的唤醒原因含 wake_from_sleep 时含 reflection；两者同在一步时按此顺序列出。
写入了条目却没有任何写入点是宿主越过写入点写记忆，按协议违约拒绝。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentsociety2.agent.base import MemoryUpdateEvidence
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (
    advance_next, conclude_pending, conclude_request, context, pending_names, stage_day_plan, start_activity,
    submit_round, wake_by_mail,
    start_arguments,
)
from worldmock.errors import ProtocolError
from worldmock.life.activity_control import PHASE_CONCLUDED
from worldmock.life.memory_evidence import MEMORY_WRITE_POINTS, memory_write_points
from worldmock.life.models import ActivityStatus, ReportStatusRequest

_MEMORY = {"type": "reflection", "text": "等得有点久，下次先带本书。", "importance": 0.3, "keywords": ["等待"]}


@pytest.mark.parametrize("wrong_observation", [False, True])
def test_memory_update_rejects_omitted_observed_source(engine_factory, wrong_observation) -> None:
    """等待经历已被当前观察交付时，不能在记忆证据中漏报该来源；绑定的观察也必须是本决策的那一条。"""
    engine = engine_factory()
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "observed-wait")
    advance_next(engine)
    ctx = context(engine, "memory-source")
    observation = engine.observe(ctx)
    assert observation["verified_experiences"]
    evidence = MemoryUpdateEvidence(
        context=ctx, operation="refresh_summary", created_episode_ids=(), merged_episode_ids=(),
        episodes=(), memory_before_sha256="a" * 64, memory_sha256="b" * 64,
        episodes_sha256="c" * 64, state_sha256="d" * 64, consolidated=False,
        observation_event_id=("unseen-observation" if wrong_observation else observation["observation_event_id"]),
        verified_event_ids=())
    with pytest.raises(ProtocolError, match="Memory update evidence is invalid") as raised:
        engine.record_memory_update(evidence)
    assert str(raised.value.__cause__) == (
        "Memory update observation is invalid" if wrong_observation else "Memory update source window is invalid")
    assert not engine.store.read_agent_events("memory_updated", 1)


def _requested(decision_id: str, tool_name: str = "conclude_activity", memories: tuple = (_MEMORY,)):
    """@brief 本人已持久化的 action_requested 事件（只含写入点推导读取的字段）。"""
    request = ({"task": ["等待（1月5日 00:00 起）"], "summary": ["等完了。"], "memories": [list(memories)]}
               if tool_name == "conclude_activity" else start_arguments(["自己"], ["interact"], ["等待"]))
    return SimpleNamespace(decision_id=decision_id, data={"tool_name": tool_name, "request": request})


@pytest.mark.parametrize("requests,wake_reasons,expected", [
    ([_requested("decision")], ["planned_end"], ["activity_conclusion"]),
    ([_requested("decision")], ["wake_from_sleep", "planned_end"], ["activity_conclusion", "reflection"]),
    ([_requested("decision", memories=())], ["wake_from_sleep"], ["reflection"]),
    ([_requested("decision", memories=())], ["planned_end"], []),
    ([_requested("earlier")], ["planned_end"], []),
    ([_requested("decision", tool_name="start_activity")], ["no_active_activity"], []),
])
def test_write_points_follow_the_registered_conclusion_and_the_wake_from_sleep_round(
        requests, wake_reasons, expected) -> None:
    """收尾写入点只认同一决策登记的带非空 memories 的 conclude_activity，反思写入点只认醒来那一轮；顺序固定。"""
    observation = SimpleNamespace(data={"wake_reasons": wake_reasons})
    assert memory_write_points(requests, "decision", observation, written=bool(expected)) == expected
    assert MEMORY_WRITE_POINTS == ("activity_conclusion", "reflection")


def test_episodes_written_without_any_write_point_are_a_protocol_violation() -> None:
    """写入了条目却没有写入点（没有带记忆的收尾、也不是醒来那一轮）即协议违约；只刷新摘要时写入点为空。"""
    observation = SimpleNamespace(data={"wake_reasons": ["planned_end"]})
    with pytest.raises(ProtocolError, match="no registered write point"):
        memory_write_points([_requested("decision", memories=())], "decision", observation, written=True)
    assert memory_write_points([], "decision", observation, written=False) == []


def _evidence(ctx, observed: dict, identities: tuple[str, ...] = ("episode-1",)) -> MemoryUpdateEvidence:
    """@brief 宿主落盘后的记忆证据：绑定本决策的观察与完整事实窗口；无条目时是一次摘要刷新。"""
    episodes = tuple({"id": item, "type": "reflection", "text": _MEMORY["text"], "epistemic_status": "subjective"}
                     for item in identities)
    return MemoryUpdateEvidence(
        context=ctx, operation="append_episodes" if identities else "refresh_summary",
        created_episode_ids=identities, merged_episode_ids=(), episodes=episodes,
        memory_before_sha256="a" * 64, memory_sha256="b" * 64, episodes_sha256="c" * 64, state_sha256="d" * 64,
        consolidated=False, observation_event_id=observed["observation_event_id"],
        verified_event_ids=tuple(dict.fromkeys(row["source_event_id"] for row in observed["verified_experiences"])))


def _until_ended(engine, activity_id: str, name: str) -> None:
    """@brief 经真实边界推进到活动结束；途中的决策机会以播报继续该活动。"""
    for step in range(128):
        if engine.state.activities[activity_id].status != ActivityStatus.ACTIVE:
            return
        if engine.scheduling_state().ready_agent_ids:
            report = ReportStatusRequest(task=[engine.state.activities[activity_id].name], summary=["继续。"])
            submit_round(engine, {1: ("report_status", report)}, f"{name}-report-{step}")
        else:
            advance_next(engine)
    pytest.fail("Activity did not end at its declared boundaries")


def _started(engine, action: str, name: str) -> str:
    """@brief 以「自己」开始一项等待或睡眠，返回活动身份。"""
    _, results = submit_round(engine, {1: ("start_activity", start_activity(action, "自己"))}, name)
    assert results[0].status == "in_progress", results[0].error_code
    return results[0].activity_id


def _open_decision(engine, name: str):
    """@brief 为就绪人物开一轮决策并取本轮冻结观察。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    return decision, engine.observe(decision)


def _written(engine) -> list[tuple]:
    """@brief 已写出的 memory_updated：（所属决策, trigger, data.decision_id）。"""
    return [(event.decision_id, event.data["trigger"], event.data["decision_id"])
            for event in engine.store.read_agent_events("memory_updated", 1)]


def test_conclusion_memories_record_the_activity_conclusion_write_point(engine_factory) -> None:
    """等待一结束就当秒收尾并写记忆：同一决策登记了带记忆的 conclude_activity，引擎记 trigger=[activity_conclusion]。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _started(engine, "等待", "wait")
    _until_ended(engine, waited, "wait")
    decision, observed = _open_decision(engine, "conclude")
    assert "wake_from_sleep" not in observed["wake_reasons"]
    engine.submit_action(DirectToolCall(decision, "action-conclude", "conclude_activity",
                                        conclude_request(*pending_names(observed), memories=[[_MEMORY]])))
    engine.record_memory_update(_evidence(decision, observed))
    assert engine.commit_decision_round("conclude")[0].status == "completed"
    assert _written(engine) == [(decision.decision_id, ["activity_conclusion"], decision.decision_id)]


def test_rejected_conclusion_keeps_its_memories_as_self_report(engine_factory) -> None:
    """登记后结算被拒的 conclude_activity，其记忆仍作为人物自述写入，写入点照样是 activity_conclusion。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _started(engine, "等待", "first-wait")
    _until_ended(engine, waited, "first-wait")
    assert conclude_pending(engine, "first-conclusion").status == "completed"
    _started(engine, "等待", "second-wait")
    wake = wake_by_mail(engine, offset=60)
    while engine.state.current_time < wake:
        advance_next(engine)
    decision, observed = _open_decision(engine, "late-conclusion")
    concluded = [row["name"] for row in observed["activity_bindings"] if row["phase"] == PHASE_CONCLUDED]
    assert concluded == [engine.state.activities[waited].name]
    engine.submit_action(DirectToolCall(decision, "action-late", "conclude_activity",
                                        conclude_request(*concluded, memories=[[_MEMORY]])))
    engine.record_memory_update(_evidence(decision, observed))
    rejected = engine.commit_decision_round("late-conclusion")[0]
    assert (rejected.status, rejected.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert _written(engine) == [(decision.decision_id, ["activity_conclusion"], decision.decision_id)]


def test_waking_round_lists_conclusion_then_reflection(engine_factory) -> None:
    """醒来那一轮的收尾记忆与就寝反思同在一步：trigger 按 activity_conclusion、reflection 的顺序列两项。"""
    engine = engine_factory()
    stage_day_plan(engine)
    slept = _started(engine, "睡觉", "sleep")
    _until_ended(engine, slept, "sleep")
    decision, observed = _open_decision(engine, "woke")
    assert "wake_from_sleep" in observed["wake_reasons"]
    assert pending_names(observed) == [engine.state.activities[slept].name]
    engine.submit_action(DirectToolCall(decision, "action-woke", "conclude_activity",
                                        conclude_request(*pending_names(observed), memories=[[_MEMORY]])))
    engine.record_memory_update(_evidence(decision, observed, ("episode-1", "episode-2")))
    assert engine.commit_decision_round("woke")[0].status == "completed"
    assert _written(engine) == [(decision.decision_id, ["activity_conclusion", "reflection"], decision.decision_id)]


def test_other_decisions_have_no_write_point_and_cannot_write_episodes(engine_factory) -> None:
    """其他决策（开始活动、不带记忆的收尾）没有写入点：只刷新摘要时 trigger 为空，写入条目即协议违约且不留事件。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _started(engine, "等待", "wait")
    _until_ended(engine, waited, "wait")
    decision, observed = _open_decision(engine, "plain-conclusion")
    engine.submit_action(DirectToolCall(decision, "action-plain", "conclude_activity",
                                        conclude_request(*pending_names(observed))))
    with pytest.raises(ProtocolError, match="Memory update evidence is invalid") as raised:
        engine.record_memory_update(_evidence(decision, observed))
    assert str(raised.value.__cause__) == "Memory update has no registered write point"
    assert not engine.store.read_agent_events("memory_updated", 1)
    engine.record_memory_update(_evidence(decision, observed, ()))
    assert engine.commit_decision_round("plain-conclusion")[0].status == "completed"
    assert _written(engine) == [(decision.decision_id, [], decision.decision_id)]
