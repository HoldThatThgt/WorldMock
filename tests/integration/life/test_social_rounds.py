"""@brief 真实目录社交内容的邀请、共同秒数、文字主题与语音事实纯执行验收。"""

import shutil
from dataclasses import replace

import pytest

from tests.catalog.conftest import read_payload, write_payload
from tests.life.action_worlds import action_world, begin
from tests.life.purchase_worlds import ENVIRONMENT
from worldmock.catalog.loader import load_catalog
from worldmock.errors import BusinessRuleError
from worldmock.life import social, social_actions
from worldmock.life.action_run import settle_action
from worldmock.life.activities import change_activity
from worldmock.life.activity_control import with_note
from worldmock.life.models import ChangeActivityRequest, WorldState


def _advance(semantics, state, activity_id, target):
    """@brief 共同秒数从区间开始世界派生，然后真实身体、游标与内容结算。"""
    activity = state.activities[activity_id]
    shared = social_actions.shared_chat_seconds(state, activity, target)
    actual = social_actions.advance_social(activity, state.agents[activity.agent_id], target, semantics.body, shared)
    changed = state.model_copy(deep=True)
    changed.current_time = target
    changed.agents[activity.agent_id] = actual.agent
    changed.activities[activity_id] = actual.activity
    return settle_action(changed, actual.activity, semantics.catalog, semantics.scenario, activity.media_position_seconds)


def _prepared_chat(semantics, state, agent, counterpart, suffix):
    """@brief 已选各自内容的社交活动在提交前，经共同在场路径产邀请或接受。"""
    proposed, activity = begin(semantics, state, "small-talk", None, f"agent:{counterpart}", agent, suffix)
    started = social_actions.start_chat(state, activity, f"message-{suffix}")
    proposed.activities[activity.activity_id] = started.activity
    return proposed, started


def test_invitation_acceptance_keeps_own_content_and_partner_end_partial_progress():
    semantics, state = action_world(multiple=True)
    state.agents[2].location_id = "home"
    state.agents[1].body.social_need = state.agents[2].body.social_need = 0.8
    state, first = _prepared_chat(semantics, state, 1, 2, "first")
    assert first.invite.message.kind == "chat_invite" and first.invite.message.recipient_id == "agent:2"
    state, _ = _advance(semantics, state, first.activity.activity_id, state.current_time + 100)
    assert state.agents[1].body.social_need == pytest.approx(0.8 - (0.2 - 0.04) * 100 / 3600)
    state, second = _prepared_chat(semantics, state, 2, 1, "second")
    assert second.invite is None and second.accepted.activity_id == first.activity.activity_id
    assert second.activity.media_position_seconds == 0
    assert second.activity.planned_end_at == state.current_time + 600
    before = state.model_copy(deep=True)
    boundary = before.activities[first.activity.activity_id].planned_end_at
    shared = social_actions.shared_chat_seconds(before, before.activities[first.activity.activity_id], boundary)
    assert shared == 500
    state, first_evidence = _advance(semantics, state, first.activity.activity_id, boundary)
    state, second_evidence = _advance(semantics, state, second.activity.activity_id, boundary)
    state, ended_people = social_actions.end_partner_chats(before, state)
    assert ended_people == [2]
    assert first_evidence["completed"] is True and second_evidence["completed"] is False
    assert state.activities[first.activity.activity_id].boundary_type == "planned_end"
    assert state.activities[second.activity.activity_id].boundary_type == "partner_ended"
    assert state.agents[1].content_progress["c-chat"].completed_rounds == 1
    assert state.agents[2].content_progress["c-chat"].position_seconds == 500
    assert state.agents[2].body.social_need == pytest.approx(0.8 - (0.5 - 0.04) * 500 / 3600)


def test_common_seconds_are_independent_of_people_advance_order():
    semantics, state = action_world(multiple=True)
    state.agents[2].location_id = "home"
    state, first = _prepared_chat(semantics, state, 1, 2, "first")
    state, second = _prepared_chat(semantics, state, 2, 1, "second")
    target = state.current_time + 120
    outcomes = []
    for sequence in ((first.activity.activity_id, second.activity.activity_id),
                     (second.activity.activity_id, first.activity.activity_id)):
        changed = WorldState.model_validate_json(state.model_dump_json())
        for identity in sequence:
            changed, _ = _advance(semantics, changed, identity, target)
        outcomes.append(changed.model_dump_json())
    assert outcomes[0] == outcomes[1]


def test_acceptance_with_less_than_one_minute_remaining_is_allowed():
    semantics, state = action_world(multiple=True)
    state.agents[2].location_id = "home"
    state, first = _prepared_chat(semantics, state, 1, 2, "first")
    state, _ = _advance(semantics, state, first.activity.activity_id, state.current_time + 599)
    _, accepted = _prepared_chat(semantics, state, 2, 1, "second")
    assert accepted.invite is None and accepted.activity.planned_duration_seconds == 600


def test_duplicate_or_absent_partner_is_rejected_and_social_cannot_pause():
    semantics, state = action_world(multiple=True)
    state.agents[2].location_id = "home"
    state, started = _prepared_chat(semantics, state, 1, 2, "first")
    with pytest.raises(BusinessRuleError, match="CHAT_ALREADY_ACTIVE"):
        _prepared_chat(semantics, state, 1, 2, "duplicate")
    with pytest.raises(BusinessRuleError, match="SOCIAL_ACTIVITY_CANNOT_PAUSE"):
        change_activity(state.agents[1], started.activity,
            ChangeActivityRequest(activity_id=started.activity.activity_id, operation="pause"), state.current_time, 900)
    state.agents[2].location_id = "office"
    with pytest.raises(BusinessRuleError, match="CHAT_PARTNER_NOT_AVAILABLE"):
        _prepared_chat(semantics, state, 2, 1, "absent")
    with pytest.raises(BusinessRuleError, match="SOCIAL_PARTNER_NOT_FOUND"):
        social_actions.partner_presence(state, "agent:unknown")


def test_remote_voice_executes_real_contact_path_and_text_never_calls_it(tmp_path, monkeypatch):
    semantics, state = action_world()
    contact = semantics.scenario.agents[0].contacts[0]
    state, voice = begin(semantics, state, "voice-call", "wechat", contact.contact_id)
    actual = social_actions.start_remote_social(voice, contact, "voice")
    assert actual.activity_kind == "call" and actual.contact_id == contact.contact_id
    assert actual.duration_seconds == 600
    target = state.current_time + 60
    state, evidence = _advance(semantics, state, voice.activity_id, target)
    assert evidence["position_after_seconds"] == 60
    directory = tmp_path / "catalog"
    shutil.copytree(ENVIRONMENT / "catalog", directory)
    actions = read_payload(directory, "actions.json")
    row = next(item for item in actions["actions"] if item["action_id"] == "voice-call")
    row.update(name="文字聊天", communication_mode="text")
    write_payload(directory, "actions.json", actions)
    text_catalog = load_catalog(directory)
    semantics = replace(semantics, catalog=text_catalog)
    state.activities = {}
    state, text = begin(semantics, state, "voice-call", "wechat", contact.contact_id, suffix="text")
    calls, start_call = [], social.start_call

    def called(*args):
        calls.append(args)
        return start_call(*args)

    monkeypatch.setattr(social, "start_call", called)
    result = social_actions.start_remote_social(text, contact, text_catalog.actions["voice-call"].communication_mode)
    assert calls == []
    assert result is None
    agent_before = state.agents[1].model_dump_json()
    noted = with_note(text, "讨论了项目范围，接下来确认负责人", state.current_time, anchor=True)
    assert noted.progress_note == "讨论了项目范围，接下来确认负责人"
    assert noted.media_position_seconds == text.media_position_seconds
    assert state.agents[1].model_dump_json() == agent_before and state.agents[1].inbox == {}


def test_remote_text_and_voice_use_same_actual_seconds_rate():
    semantics, state = action_world()
    state.agents[1].body.social_need = 1
    contact = semantics.scenario.agents[0].contacts[0]
    state, activity = begin(semantics, state, "voice-call", "wechat", contact.contact_id)
    changed, _ = _advance(semantics, state, activity.activity_id, state.current_time + 300)
    assert changed.agents[1].body.social_need == pytest.approx(1 - (0.4 - 0.04) * 300 / 3600)
    assert changed.agents[1].content_progress["c-call"].position_seconds == 300


def test_chat_start_after_initialization_ignores_its_own_new_activity():
    """@brief 引擎先准入并初始化再社交接线，新活动自己不被误判为重复交谈。"""
    semantics, state = action_world(multiple=True)
    state.agents[2].location_id = "home"
    initialized, activity = begin(semantics, state, "small-talk", None, "agent:2")
    started = social_actions.start_chat(initialized, activity, "actual-invite")
    assert started.invite.message.kind == "chat_invite"


def test_template_reply_is_scheduled_once_with_actual_recipient_and_fixed_delay():
    """@brief 既有独立消息模板回复仍走真实预排，不是社交主题推送。"""
    from worldmock.scheduling.effects import process_scheduled_events
    semantics, state = action_world()
    contact = semantics.scenario.agents[0].contacts[0]
    message = social.Message(message_id="actual-direct", sender_id="agent:1", recipient_id=contact.contact_id,
                             kind="direct", text="周末见", created_at=state.current_time)
    changed = social_actions.schedule_template_reply(state, message, contact)
    event = changed.pending_events[0]
    assert event.due_at == state.current_time + social.CONTACT_REPLY_DELAY_SECONDS
    changed.current_time = event.due_at
    delivered, drafts, wakes = process_scheduled_events(changed, changed.current_time, semantics)
    assert delivered.agents[1].inbox[drafts[0].data["message_id"]].text == social.TEMPLATE_REPLY_TEXT
    assert wakes == {1: {"message_arrival"}}
    repeated, redrafts, _ = process_scheduled_events(delivered, delivered.current_time, semantics)
    assert repeated.pending_events == [] and redrafts == []


def test_shared_social_advance_uses_only_real_body_settlement_once():
    """@brief 身体共同结算后，社交只推进内容游标与真实缓解，不重复扣能量。"""
    from worldmock.life.activities import activity_factors
    from worldmock.life.body import settle_shared_body
    semantics, state = action_world()
    state.agents[1].body.social_need = 0.8
    contact = semantics.scenario.agents[0].contacts[0]
    state, activity = begin(semantics, state, "voice-call", "wechat", contact.contact_id)
    agent = state.agents[1]
    settlement = settle_shared_body(agent.body,
        ((activity.kind, activity_factors(activity, agent, semantics.body)),), 100, semantics.body)
    agent = agent.model_copy(update={"body": settlement.after}, deep=True)
    result = social_actions.advance_social_shared(activity, agent, state.current_time + 100, settlement, 0)
    assert result.agent.body.energy_kcal == settlement.after.energy_kcal
    assert result.activity.media_position_seconds == 100
    assert result.agent.body.social_need == pytest.approx(0.8 - (0.4 - 0.04) * 100 / 3600)


def test_remote_invalid_mode_cannot_guess_a_voice_path():
    semantics, state = action_world()
    contact = semantics.scenario.agents[0].contacts[0]
    _, activity = begin(semantics, state, "voice-call", "wechat", contact.contact_id)
    with pytest.raises(ValueError, match="communication mode"):
        social_actions.start_remote_social(activity, contact, "guess")
