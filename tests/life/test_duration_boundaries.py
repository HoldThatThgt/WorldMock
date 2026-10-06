"""@brief 冻结派生时长边界在原计划消费者和真实受理中的行为验收。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from worldmock.errors import BusinessRuleError
from worldmock.life.models import ActivityKind
from worldmock.life.tool_layer import _facts_seconds


def _pending(action: str) -> SimpleNamespace:
    """形成只用于真实受理入口的共同动作归因。"""
    return SimpleNamespace(agent_id=1, action_id=f"duration-{action}", decision_id="duration-decision",
                           requested_event_id="duration-request")


@pytest.mark.parametrize('feedback', [60, 86400])
def test_app_duration_is_fixed_in_listing_and_real_admission(engine_factory, feedback):
    """@brief 应用的三十分钟计划与内容时长、场景反馈间隔无关。"""
    from worldmock.exchange.constants import APP_USE_DEFAULT_SECONDS
    engine = engine_factory(mutate=lambda document: document.update(activity_feedback_seconds=feedback))
    context = engine.observations._layer_context(engine.context.state.agents[1], (), [])
    assert _facts_seconds(context, {'object_id': 'bilibili'}, {'canonical_action': 'use_app'}) == 1800
    changed, result = engine.actions._start_targeted_action(
        engine.context.state, _pending('app'), ActivityKind.USE_APP, 'bilibili')
    activity = changed.activities[result.activity_id]
    assert result.status == 'in_progress'
    assert activity.planned_duration_seconds == APP_USE_DEFAULT_SECONDS == 1800
    assert activity.kind == ActivityKind.USE_APP
    assert 'content_id' in activity.data


@pytest.mark.parametrize("hours, expected", [(7.0, 25_200), (7.123456, 25_644), (9.0, 32_400)])
def test_sleep_duration_uses_personal_need_in_listing_and_real_admission(engine_factory, hours, expected):
    """合法人物睡眠需要及非整秒需要，在呈现和实际受理中得到同一整秒计划。"""
    engine = engine_factory(mutate=lambda document: document["agents"][0].update(sleep_need_hours=hours))
    agent = engine.context.state.agents[1]
    assert agent.body.sleep_need_hours == hours
    context = engine.observations._layer_context(agent, (), [])
    assert _facts_seconds(context, {"object_id": "agent:1"}, {"canonical_action": "sleep"}) == expected
    changed, result = engine.actions._start_self_action(engine.context.state, _pending("sleep"), "sleep")
    activity = changed.activities[result.activity_id]
    assert result.status == "in_progress"
    assert activity.kind == ActivityKind.SLEEP
    assert activity.planned_duration_seconds == expected
    assert activity.target_id is None


@pytest.mark.parametrize("seconds", [60, 86_400])
def test_feedback_duration_matches_listing_and_real_admission(engine_factory, seconds):
    """反馈间隔的合法上下界同时决定等待、阅读、浏览和可玩游戏的真实计划时长。"""
    def with_mail(document):
        document['activity_feedback_seconds'] = seconds
        document['messages'] = [{'message_id': 'duration-mail', 'thread_id': 'duration-thread',
            'recipient_agent_id': 1, 'sent_at': document['start_at'], 'subject': '边界邮件', 'body': '测试正文'}]
    engine = engine_factory(mutate=with_mail)
    message_id = engine.context.scenario.messages[0].message_id
    state = engine.context.state.model_copy(deep=True)
    state.agents[1].visible_message_ids.add(message_id)
    context = replace(engine.observations._layer_context(state.agents[1], (), []), state=state)
    starts = (
        ("wait", "agent:1", ActivityKind.WAIT,
         lambda: engine.actions._start_self_action(state, _pending("wait"), "wait")),
        ("read", message_id, ActivityKind.READ_MAIL,
         lambda: engine.actions._start_targeted_action(state, _pending("read"), ActivityKind.READ_MAIL, message_id)),
    )
    for canonical, target, kind, start in starts:
        assert _facts_seconds(context, {"object_id": target}, {"canonical_action": canonical}) == seconds
        changed, result = start()
        assert result.status == "in_progress"
        activity = changed.activities[result.activity_id]
        assert activity.kind == kind
        assert activity.planned_duration_seconds == seconds
        assert not state.activities
