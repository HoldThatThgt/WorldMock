"""@brief 原工作断言在统一动作中的真实暂停、自述与有限内容回归。"""

from copy import deepcopy

import pytest

from tests.life.action_worlds import action_world, advance, begin
from tests.integration.life.test_app_progress import _pause
from worldmock.errors import BusinessRuleError
from worldmock.life.action_run import resume_action, settle_action
from worldmock.life.activities import advance_activity, change_activity
from worldmock.life.activity_control import with_note
from worldmock.life.models import ChangeActivityRequest


def test_thirty_paused_minutes_never_increase_actual_assigned_content_seconds():
    semantics, state = action_world("office")
    state, before = begin(semantics, state, "office-work", None)
    state, _ = advance(semantics, state, before, 700)
    state, paused = _pause(state, state.activities[before.activity_id])
    original = deepcopy(state.agents[1].content_progress)
    state.current_time += 1800
    result = advance_activity(paused, state.agents[1], state.current_time, semantics.body)
    assert result.activity == paused and result.settlement is None
    assert result.activity.elapsed_seconds == 700 and result.activity.media_position_seconds == 700
    changed, evidence = settle_action(state, result.activity, semantics.catalog, semantics.scenario, 700)
    assert changed.agents[1].content_progress == original
    assert evidence["completed"] is False and evidence["position_after_seconds"] == 700
    _, resumed = change_activity(changed.agents[1], paused,
        ChangeActivityRequest(activity_id=paused.activity_id, operation="resume"), state.current_time, 900)
    changed = resume_action(changed, resumed, semantics.catalog, semantics.scenario)
    assert changed.activities[before.activity_id].planned_end_at == state.current_time + 31700
    assert changed.activities[before.activity_id].elapsed_seconds == 700


def test_work_reports_never_change_real_content_seconds_or_claim_completion():
    semantics, state = action_world("office")
    state, before = begin(semantics, state, "office-work", None)
    state, _ = advance(semantics, state, before, 700)
    progress = state.agents[1].content_progress["topic-sales"].model_copy(deep=True)
    activity = state.activities[before.activity_id]
    reported = with_note(activity, "我已完成全部工作", state.current_time)
    assert reported.progress_note == "我已完成全部工作"
    assert reported.elapsed_seconds == activity.elapsed_seconds == 700
    assert reported.media_position_seconds == activity.media_position_seconds == 700
    assert reported.status == "active" and reported.ended_at is None
    assert state.agents[1].content_progress["topic-sales"] == progress
    assert progress.completed_rounds == 0 and progress.position_seconds == 700


def test_completed_assigned_content_cannot_resume_old_paused_work_or_reset_progress():
    semantics, state = action_world("office")
    state, first = begin(semantics, state, "office-work", None)
    state, _ = advance(semantics, state, first, 700)
    state, paused = _pause(state, state.activities[first.activity_id])
    state, second = begin(semantics, state, "office-work", None, suffix="second")
    state, evidence = advance(semantics, state, second, 31700)
    assert evidence["completed"] is True and evidence["completed_rounds_after"] == 1
    assert state.agents[1].content_progress["topic-sales"].completed_rounds == 1
    assert state.agents[1].content_progress["topic-sales"].position_seconds == 0
    _, resumed = change_activity(state.agents[1], paused,
        ChangeActivityRequest(activity_id=paused.activity_id, operation="resume"), state.current_time, 900)
    with pytest.raises(BusinessRuleError, match="CONTENT_EXHAUSTED"):
        resume_action(state, resumed, semantics.catalog, semantics.scenario)
    with pytest.raises(BusinessRuleError, match="CONTENT_EXHAUSTED"):
        begin(semantics, state, "office-work", None, suffix="new")
    assert state.agents[1].content_progress["topic-sales"].completed_rounds == 1
