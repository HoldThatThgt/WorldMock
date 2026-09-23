"""活动事件内嵌人物可读标题。"""

from __future__ import annotations

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context
from worldmock.life.models import StartActivityRequest


def _start_visible(engine, round_id: str, kind: str):
    """@brief 从真实观察选一项当前可执行对象并启动。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    binding = next(row for row in observed["interaction_bindings"]
                   if row["kind"] == kind and row["preconditions_met"] and row["actions"])
    request = StartActivityRequest(targets=[binding["object"]],
                                   actions=[binding["actions"][0]["label"]])
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "start_activity", request))
    return binding, engine.commit_decision_round(round_id)[0]


def test_content_title_is_carried_into_activity_events(engine_factory) -> None:
    """@brief 观看活动的结算与进度使用冻结语料标题。"""
    engine = engine_factory()
    binding, started = _start_visible(engine, "watch-title", "content")
    assert started.status == "in_progress", started.error_code
    expected = engine.rules.content_titles[binding["object_id"]]
    event = next(event for event in engine.store.read_agent_events("action_result", 1)
                 if event.action_id == "action-watch-title")
    assert event.data["data"]["activity"]["title"] == expected
    advance_next(engine)
    progress = [event for event in engine.store.read_agent_events("activity_progress", 1)
                if event.data["activity"]["target_id"] == binding["object_id"]]
    assert progress[-1].data["activity"]["title"] == expected


def test_wait_activity_has_no_media_title(engine_factory) -> None:
    """@brief 自身等待不借用内容或目录标题。"""
    engine = engine_factory()
    _, started = _start_visible(engine, "wait-title", "self")
    assert started.status == "in_progress", started.error_code
    event = next(event for event in engine.store.read_agent_events("action_result", 1)
                 if event.action_id == "action-wait-title")
    assert "title" not in event.data["data"]["activity"]
