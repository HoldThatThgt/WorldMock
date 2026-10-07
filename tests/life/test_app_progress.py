"""@brief 统一动作的跨 app 全局进度、零秒暂停、恢复与确定性证据。"""


import pytest
from pydantic import ValidationError

from tests.life.action_worlds import action_world, advance, begin, pin
from worldmock.life.action_run import resume_action, settle_action
from worldmock.life.activities import change_activity
from worldmock.life.models import ChangeActivityRequest, ContentProgress, WorldState
from worldmock.life.sessions import begin_transit, current_session, end_transit
from worldmock.footprints.models import canonical_json


def _pause(state, activity):
    """@brief 暂停仍由真实控制转换执行，不创造进度记录。"""
    _, paused = change_activity(state.agents[activity.agent_id], activity,
        ChangeActivityRequest(activity_id=activity.activity_id, operation="pause"), state.current_time, 900)
    changed = state.model_copy(deep=True)
    changed.activities[activity.activity_id] = paused
    return changed, paused


def test_zero_second_pause_never_creates_consumption_history():
    semantics, state = action_world()
    pin(state)
    state, activity = begin(semantics, state)
    changed, paused = _pause(state, activity)
    assert paused.status == "paused" and changed.agents[1].content_progress == {}
    unchanged, evidence = settle_action(changed, paused, semantics.catalog, semantics.scenario, 0)
    assert unchanged.agents[1].content_progress == {} and evidence["completed"] is False
    assert current_session(unchanged, 1).app_pushes["douyin"].status == "opened"


def test_actual_consumption_is_keyed_once_by_content_across_apps():
    """@brief 两个实际应用消费同一内容只维护一个身份键，第二次接同一真实位置。"""
    semantics, state = action_world()
    pin(state, "c-long")
    state, first = begin(semantics, state)
    state, _ = advance(semantics, state, first, 30)
    assert set(state.agents[1].content_progress) == {"c-long"}
    state, _ = _pause(state, state.activities[first.activity_id])
    pin(state, "c-long", "bilibili")
    state, second = begin(semantics, state, app="bilibili", suffix="second-app")
    assert second.media_position_seconds == 30
    state, evidence = advance(semantics, state, second, 15)
    assert set(state.agents[1].content_progress) == {"c-long"}
    assert evidence["position_before_seconds"] == 30 and evidence["position_after_seconds"] == 45
    assert state.agents[1].content_progress["c-long"].position_seconds == 45


def test_cross_app_progress_uses_latest_global_position_and_owner_isolation():
    semantics, state = action_world(multiple=True)
    pin(state, "c-long")
    state, activity = begin(semantics, state)
    changed, evidence = advance(semantics, state, activity, 1000)
    assert evidence["position_after_seconds"] == 1000
    assert set(changed.agents[1].content_progress) == {"c-long"}
    assert changed.agents[1].content_progress["c-long"].last_consumed_at == changed.current_time
    changed, paused = _pause(changed, changed.activities[activity.activity_id])
    pin(changed, "c-long", "bilibili")
    changed, other = begin(semantics, changed, app="bilibili", suffix="other")
    assert other.data["content_id"] == "c-long" and other.media_position_seconds == 1000
    assert other.planned_duration_seconds == 1400
    assert changed.agents[2].content_progress == {}
    changed, evidence = advance(semantics, changed, other, 100)
    assert set(changed.agents[1].content_progress) == {"c-long"}
    changed, _ = _pause(changed, changed.activities[other.activity_id])
    _, resumed = change_activity(changed.agents[1], paused,
        ChangeActivityRequest(activity_id=paused.activity_id, operation="resume"), changed.current_time, 900)
    resumed_world = resume_action(changed, resumed, semantics.catalog, semantics.scenario)
    actual = resumed_world.activities[paused.activity_id]
    assert actual.media_position_seconds == 1100
    assert actual.planned_duration_seconds == 1000 + 1300
    assert actual.planned_end_at == changed.current_time + 1300
    assert actual.data["content_id"] == paused.data["content_id"] and actual.data["draw_index"] == 0
    assert resumed_world.agents[1].content_progress == changed.agents[1].content_progress


def test_other_app_completed_round_resume_starts_latest_round_zero_and_does_not_count_twice():
    semantics, state = action_world()
    pin(state, "c-long")
    state, activity = begin(semantics, state)
    state, _ = advance(semantics, state, activity, 100)
    state, paused = _pause(state, state.activities[activity.activity_id])
    state.agents[1].content_progress["c-long"] = ContentProgress(content_id="c-long", position_seconds=0,
        completed_rounds=1, last_consumed_at=state.current_time)
    state.current_time += 20
    _, resumed = change_activity(state.agents[1], paused,
        ChangeActivityRequest(activity_id=paused.activity_id, operation="resume"), state.current_time, 900)
    changed = resume_action(state, resumed, semantics.catalog, semantics.scenario)
    actual = changed.activities[activity.activity_id]
    assert actual.media_position_seconds == 0 and actual.planned_end_at == state.current_time + 2400
    assert changed.agents[1].content_progress["c-long"].completed_rounds == 1
    changed, evidence = advance(semantics, changed, actual, 2400)
    assert evidence["completed_rounds_after"] == 2
    assert changed.agents[1].content_progress["c-long"].completed_rounds == 2


def test_continuation_does_not_open_a_later_push_and_completion_releases_cross_app_pushes():
    semantics, state = action_world()
    pin(state, "c-short")
    pin(state, "c-short", "bilibili")
    state, activity = begin(semantics, state)
    changed, _ = advance(semantics, state, activity, 120)
    assert {push.status for push in current_session(changed, 1).app_pushes.values()} == {"released"}
    ongoing = changed.activities[activity.activity_id]
    pin(changed, ongoing.data["content_id"], "bilibili")
    next_world, _ = advance(semantics, changed, ongoing, 1)
    assert current_session(next_world, 1).app_pushes["bilibili"].status == "pending"
    assert next_world.activities[activity.activity_id].data["pinned_by_push_id"] is None


def test_same_app_other_action_never_opens_push():
    semantics, state = action_world()
    pin(state, "c-long", "bilibili")
    changed, course = begin(semantics, state, "study-course", "bilibili")
    assert course.data["content_id"] == "c-course" and course.data["pinned_by_push_id"] is None
    assert current_session(changed, 1).app_pushes["bilibili"].status == "pending"
    assert changed.agents[1].content_progress == {}
    assert changed.random_state == state.random_state


def test_stop_then_same_session_start_uses_same_pinned_remainder():
    semantics, state = action_world()
    pin(state, "c-long", "bilibili")
    state, activity = begin(semantics, state, app="bilibili")
    state, _ = advance(semantics, state, activity, 30)
    _, cancelled = change_activity(state.agents[1], state.activities[activity.activity_id],
        ChangeActivityRequest(activity_id=activity.activity_id, operation="cancel"), state.current_time, 900)
    state.activities[activity.activity_id] = cancelled
    changed, resumed = begin(semantics, state, app="bilibili", suffix="restart")
    assert resumed.data["content_id"] == "c-long" and resumed.media_position_seconds == 30
    assert resumed.data["pinned_by_push_id"] == current_session(changed, 1).app_pushes["bilibili"].push_id
    assert resumed.planned_end_at == state.current_time + 2370


def test_session_change_drops_pinning_but_keeps_global_progress():
    semantics, state = action_world()
    pin(state, "c-long")
    state, activity = begin(semantics, state)
    state, _ = advance(semantics, state, activity, 20)
    _, cancelled = change_activity(state.agents[1], state.activities[activity.activity_id],
        ChangeActivityRequest(activity_id=activity.activity_id, operation="cancel"), state.current_time, 900)
    state.activities[activity.activity_id] = cancelled
    names = {key: item.name for key, item in semantics.catalog.locations.items()}
    state = begin_transit(state, 1, "cafe", names, opened_by_action_id="departure")
    assert current_session(state, 1).app_pushes == {}
    state, _ = end_transit(state, 1, names)
    changed, next_activity = begin(semantics, state, suffix="fresh")
    assert next_activity.data["content_id"] == "c-long"
    assert next_activity.data["pinned_by_push_id"] is None and next_activity.media_position_seconds == 20
    assert changed.agents[1].content_progress == state.agents[1].content_progress


def test_checkpoint_restore_keeps_current_content_and_future_sequence(tmp_path):
    from worldmock.footprints.store import FootprintStore
    semantics, state = action_world()
    pin(state)
    state, activity = begin(semantics, state)
    state, _ = advance(semantics, state, activity, 120)
    store = FootprintStore.create(tmp_path / "run", {"run_id": state.run_id, "agent_ids": [1]}, state.model_dump(mode="json"))
    try:
        snapshots = {identity: store.assets.put_json({"participant": identity}, {"kind": "environment"})
                     for identity in ("agent:1", "life")}
        saved = store.checkpoint(state.model_dump(mode="json"), snapshots)
        restored = WorldState.model_validate(store.get_checkpoint(saved.checkpoint_id).state)
        assert canonical_json(restored.model_dump(mode="json")) == canonical_json(state.model_dump(mode="json"))
        for _ in range(6):
            original_activity = state.activities[activity.activity_id]
            restored_activity = restored.activities[activity.activity_id]
            seconds = original_activity.planned_end_at - state.current_time
            state, actual = advance(semantics, state, original_activity, seconds)
            restored, repeated = advance(semantics, restored, restored_activity, seconds)
            assert actual == repeated and canonical_json(restored.model_dump(mode="json")) == canonical_json(state.model_dump(mode="json"))
    finally:
        store.close()


def test_progress_rejects_retired_missing_history_shape():
    with pytest.raises(ValidationError):
        ContentProgress(content_id="c-long", position_seconds=20, completed_rounds=0)


def test_silent_continuation_never_opens_same_app_push_that_arrived_during_activity():
    semantics, state = action_world()
    pin(state, "c-short")
    state, activity = begin(semantics, state)
    state, _ = advance(semantics, state, activity, 120)
    ongoing = state.activities[activity.activity_id]
    pin(state, "c-short")
    state, _ = advance(semantics, state, ongoing, 2400)
    assert current_session(state, 1).app_pushes["douyin"].status == "pending"
    assert state.activities[activity.activity_id].data["pinned_by_push_id"] is None
