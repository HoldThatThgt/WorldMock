"""@brief 统一动作快照嵌入登记动作名、可空应用名和实际所选内容标题。"""

import pytest

from tests.life.conftest import advance_next, context, stage_day_plan, start_activity, submit_round
from tests.life.current_world import stage_moment


@pytest.mark.parametrize("action,app,counterpart,location", [
    ("刷推荐", "抖音", None, "home"), ("办公", None, None, "office"),
    ("安静休息", None, None, "home"), ("语音通话", "微信", "联系人：王磊", "home"),
])
def test_action_and_content_titles_are_carried_into_actual_activity_events(
    engine_factory, action, app, counterpart, location,
):
    """@brief 受理、实际推进和公开控制行同源，不用对方姓名代替目录动作名。"""
    engine = engine_factory()
    stage_moment(engine, location=location)
    stage_day_plan(engine)
    request = start_activity("进行", action, app=[app], to=[counterpart])
    _, results = submit_round(engine, {1: ("start_activity", request)}, "embedded-title")
    started = results[0]
    assert started.error_code is None, started.data
    accepted = next(event for event in engine.context.store.read_agent_events("action_result", 1)
                    if event.action_id == "action-embedded-title-1")
    snapshot = accepted.data["data"]["activity"]
    expected = action + (f"（{app}）" if app is not None else "")
    assert snapshot["title"] == expected
    selected = snapshot["data"]["content_id"]
    assert snapshot["content_title"] == engine.context.rules.contents[selected].title
    advance_next(engine)
    progress = [event for event in engine.context.store.read_agent_events("activity_progress", 1)
                if event.activity_id == started.activity_id]
    assert progress[-1].data["activity"]["title"] == expected
    observed = engine.observe(context(engine, "title-peek"))
    rows = observed["active_activities"] + observed["pending_conclusions"]
    assert next(row["title"] for row in rows if row["activity_id"] == started.activity_id) == expected
    assert progress[-1].data["activity"]["data"]["counterpart"] == snapshot["data"]["counterpart"]


def test_wait_activity_has_no_media_title(engine_factory):
    """@brief 原生等待没有内容标题或环境动作标题。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "wait-title")
    assert results[0].error_code is None
    event = next(event for event in engine.context.store.read_agent_events("action_result", 1)
                 if event.action_id == "action-wait-title-1")
    assert "title" not in event.data["data"]["activity"]
    assert "content_title" not in event.data["data"]["activity"]
