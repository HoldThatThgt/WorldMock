"""@brief 冻结派生时长边界在原计划消费者和真实受理中的行为验收。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from worldmock.errors import BusinessRuleError
from worldmock.life.models import ActivityKind
from worldmock.life.named_catalog import _content_rows
from worldmock.life.readable import duration_phrase
from worldmock.life.tool_layer import _facts_seconds


def _pending(action: str) -> SimpleNamespace:
    """形成只用于真实受理入口的共同动作归因。"""
    return SimpleNamespace(agent_id=1, action_id=f"duration-{action}", decision_id="duration-decision",
                           requested_event_id="duration-request")


@pytest.mark.parametrize("seconds", [1, 59])
def test_short_content_is_rejected_by_listing_and_real_admission(engine_factory, monkeypatch, seconds):
    """正数但不足一分钟的内容不能获得计划时长，也不能创建实际活动。"""
    engine = engine_factory()
    original = engine.scenario.contents[0]
    item = original.model_copy(update={"duration_seconds": seconds})
    monkeypatch.setitem(engine.rules.contents, item.item_id, item)
    context = engine._layer_context(engine.state.agents[1], (), [])
    for operation in (
        lambda: _facts_seconds(context, {"object_id": item.item_id}, {"canonical_action": "watch"}),
        lambda: engine._start_targeted_action(engine.state, _pending("watch"), ActivityKind.WATCH, item.item_id),
    ):
        with pytest.raises(BusinessRuleError, match="ACTIVITY_DURATION_INVALID"):
            operation()
    assert not engine.state.activities


@pytest.mark.parametrize("seconds, expected", [(60, 60), (86_400, 86_400), (86_401, 86_400)])
def test_game_content_watch_duration_matches_plans_and_real_admission(engine_factory, monkeypatch, seconds, expected):
    """只能观看的游戏内容按真实 WATCH 受理，一分钟与一天边界在全部原计划消费者中一致。"""
    engine = engine_factory()
    original = next(item for item in engine.scenario.contents if item.kind == "game_session")
    item = original.model_copy(update={"duration_seconds": seconds})
    monkeypatch.setitem(engine.rules.contents, item.item_id, item)
    context = replace(engine._layer_context(engine.state.agents[1], (), []),
                      scenario=engine.scenario.model_copy(update={"contents": (item,)}))
    assert _facts_seconds(context, {"object_id": item.item_id}, {"canonical_action": "watch"}) == expected
    assert _content_rows(context)[0]["note"] == duration_phrase(expected)
    changed, result = engine._start_targeted_action(engine.state, _pending("game-content"),
                                                    ActivityKind.WATCH, item.item_id)
    activity = changed.activities[result.activity_id]
    assert result.status == "in_progress"
    assert activity.kind == ActivityKind.WATCH
    assert activity.planned_duration_seconds == expected
    assert activity.data["content_kind"] == "game_session"
    assert activity.data["start_position_seconds"] == 0


@pytest.mark.parametrize("hours, expected", [(7.0, 25_200), (7.123456, 25_644), (9.0, 32_400)])
def test_sleep_duration_uses_personal_need_in_listing_and_real_admission(engine_factory, hours, expected):
    """合法人物睡眠需要及非整秒需要，在呈现和实际受理中得到同一整秒计划。"""
    engine = engine_factory(mutate=lambda document: document["agents"][0].update(sleep_need_hours=hours))
    agent = engine.state.agents[1]
    assert agent.body.sleep_need_hours == hours
    context = engine._layer_context(agent, (), [])
    assert _facts_seconds(context, {"object_id": "agent:1"}, {"canonical_action": "sleep"}) == expected
    changed, result = engine._start_self_action(engine.state, _pending("sleep"), "sleep")
    activity = changed.activities[result.activity_id]
    assert result.status == "in_progress"
    assert activity.kind == ActivityKind.SLEEP
    assert activity.planned_duration_seconds == expected
    assert activity.target_id is None


@pytest.mark.parametrize("seconds", [60, 86_400])
def test_feedback_duration_matches_listing_and_real_admission(engine_factory, seconds):
    """反馈间隔的合法上下界同时决定等待、阅读、浏览和可玩游戏的真实计划时长。"""
    engine = engine_factory(mutate=lambda document: document.update(activity_feedback_seconds=seconds))
    message_id = engine.scenario.messages[0].message_id
    game_id = engine.scenario.games[0].game_id
    state = engine.state.model_copy(deep=True)
    state.agents[1].visible_message_ids.add(message_id)
    context = replace(engine._layer_context(state.agents[1], (), []), state=state)
    starts = (
        ("wait", "agent:1", ActivityKind.WAIT,
         lambda: engine._start_self_action(state, _pending("wait"), "wait")),
        ("read", message_id, ActivityKind.READ_MAIL,
         lambda: engine._start_targeted_action(state, _pending("read"), ActivityKind.READ_MAIL, message_id)),
        ("browse", "product_catalog", ActivityKind.BROWSE,
         lambda: engine._start_browse_action(state, _pending("browse"), "product_catalog")),
        ("play", game_id, ActivityKind.PLAY_GAME,
         lambda: engine._start_targeted_action(state, _pending("play"), ActivityKind.PLAY_GAME, game_id)),
    )
    for canonical, target, kind, start in starts:
        assert _facts_seconds(context, {"object_id": target}, {"canonical_action": canonical}) == seconds
        changed, result = start()
        assert result.status == "in_progress"
        activity = changed.activities[result.activity_id]
        assert activity.kind == kind
        assert activity.planned_duration_seconds == seconds
        assert not state.activities
