"""未了活动只过滤已结清项，保持世界顺序与原有判定结果。"""

from tests.unit.test_environment_engine_admission import running, world
from worldmock.life import activity_control
from worldmock.life.models import ActivityKind, ActivityStatus


def _world():
    state = world()
    cases = [("pending", "completed", 300, None), ("active", "active", 100, None),
             ("concluded", "completed", 110, 500), ("paused", "paused", 200, None),
             ("truncated", "truncated", 120, None), ("cancelled", "cancelled", 130, None),
             ("cancelled-done", "cancelled", 140, 500), ("failed", "failed", 150, None)]
    for identity, status, start, conclusion in cases:
        activity = running("use_app", ActivityKind.INTERACT, "second").model_copy(update={
            "activity_id": identity, "status": ActivityStatus(status), "started_at": start,
            "last_settled_at": start, "ended_at": 500 if status not in {"active", "paused"} else None,
            "concluded_at": conclusion})
        state.activities[identity] = activity
    state.agents[2] = state.agents[1].model_copy(update={"agent_id": 2})
    state.activities["other"] = running("sleep", ActivityKind.SLEEP, "first").model_copy(
        update={"activity_id": "other", "agent_id": 2})
    return state


def test_open_activities_are_the_concludable_ones_in_world_order():
    state = _world()
    found = activity_control.open_activities(state, 1)
    assert tuple(item.activity_id for item in found) == ("pending", "active", "paused", "cancelled", "failed")
    assert all(item is state.activities[item.activity_id] for item in found)


def test_slot_pending_and_sleep_checks_see_the_same_activities_as_a_full_scan():
    state = _world()
    state.activities["asleep"] = running("sleep", ActivityKind.SLEEP, "first").model_copy(
        update={"activity_id": "asleep"})
    current = activity_control.open_activities(state, 1)
    all_own = [item for item in state.activities.values() if item.agent_id == 1]
    predicates = (activity_control.occupies_slot, activity_control.is_pending_conclusion,
                  lambda item: item.kind == ActivityKind.SLEEP and item.status == ActivityStatus.ACTIVE)
    for predicate in predicates:
        assert [item for item in current if predicate(item)] == [item for item in all_own if predicate(item)]
    for reader, predicate in ((activity_control.slot_activities, predicates[0]),
                              (activity_control.pending_conclusions, predicates[1])):
        assert reader(current) == tuple(sorted((item for item in all_own if predicate(item)),
                                              key=lambda item: (item.started_at, item.activity_id)))
