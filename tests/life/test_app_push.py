"""@brief 冻结推送网格、当前会话生命周期及实际持久事件的纯生产所有者测试。"""

from __future__ import annotations

import hashlib
import random
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.life.purchase_worlds import at_time, registered_world
from worldmock.footprints.store import FootprintStore
from worldmock.life.app_push import open_app_push, release_app_pushes, validate_app_pushes
from worldmock.errors import IntegrityError
from worldmock.life.models import ActivityState, AppPush, ContentProgress, WorldState
from worldmock.life.schedule import SLEEP_ALLOWED_REASONS, WakeReason, WakeSnapshot, evaluate_wake_reasons
from worldmock.life.sessions import begin_transit, current_session, end_transit, open_stay
from worldmock.scheduling import compile as compiler
from worldmock.scheduling import effects
from worldmock.scheduling.compile import _push_due_at, _push_rows, datetime_midnight
from worldmock.scheduling.effects import apply_scheduled_effect, process_scheduled_events
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind


def push_world():
    """@brief 生产登记加载器和会话构造器提供真正的世界状态。"""
    semantics, state = registered_world()
    names = {key: item.name for key, item in semantics.catalog.locations.items()}
    state = open_stay(state, 1, names)
    return semantics, at_time(state, state.current_time + 8 * 3600)


def push_event(state, hour=8, sequence=1):
    """@brief 构造冻结标识的真实预排推送机会。"""
    moment = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone))
    return ScheduledEvent(scheduled_id=f"push-1-{moment:%Y-%m-%d}T{hour:02d}", kind="app_push",
                          due_at=state.current_time, sequence=sequence, agent_id=1, data={})


def activity(state, kind="sleep"):
    """@brief 真实活动快照用于验证睡眠与使用中的推送不改变活动。"""
    return ActivityState(activity_id="ongoing", agent_id=1, location_id="home", kind=kind, status="active",
                         started_at=state.current_time, planned_end_at=state.current_time + 1800,
                         last_settled_at=state.current_time, elapsed_seconds=0, planned_duration_seconds=1800,
                         origin_action_id="action", origin_decision_id="decision", origin_request_event_id="request")


@pytest.mark.parametrize("timezone", ["Asia/Shanghai", "UTC", "America/New_York"])
def test_push_grid_uses_local_hours_and_strict_window(timezone):
    semantics, _ = push_world()
    scenario = semantics.scenario.model_copy(update={"timezone": timezone})
    start = int(datetime_midnight("2026-01-05", ZoneInfo(timezone)).timestamp())
    pushes = _push_rows(scenario, start, start + 86400)
    assert len(pushes) == 14 * len(scenario.agents)
    assert {datetime.fromtimestamp(row[0], ZoneInfo(timezone)).hour for row in pushes} == set(range(8, 22))
    assert all(row[4].endswith(f"T{datetime.fromtimestamp(row[0], ZoneInfo(timezone)):%H}")
               and row[2] == ScheduledKind.APP_PUSH and row[3] == {} for row in pushes)
    first = next(row for row in pushes if row[4].endswith("T08"))
    last = next(row for row in pushes if row[4].endswith("T21"))
    clipped = _push_rows(scenario, first[0], last[0])
    assert {row[4][-2:] for row in clipped} == {f"{hour:02d}" for hour in range(9, 21)}


def test_push_grid_preserves_fold_zero_and_gap_pretransition_offset():
    semantics, _ = push_world()
    zone = ZoneInfo("Pacific/Easter")
    scenario = semantics.scenario.model_copy(update={"timezone": zone.key})
    midnight = datetime_midnight("2026-04-04", zone)
    start = int(midnight.timestamp())
    end = int((midnight + timedelta(days=1)).timestamp())
    pushes = _push_rows(scenario, start, end)
    repeated = [row for row in pushes if row[4].endswith("T21")]
    assert len(repeated) == len(scenario.agents)
    assert repeated[0][0] == int(midnight.replace(hour=21, fold=0).timestamp())
    assert repeated[0][0] != int(midnight.replace(hour=21, fold=1).timestamp())
    gap_midnight = datetime_midnight("2026-03-08", ZoneInfo("America/New_York"))
    assert _push_due_at(gap_midnight, 2) == int(datetime.fromisoformat("2026-03-08T02:00:00-05:00").timestamp())
    assert _push_due_at(gap_midnight, 2) == _push_due_at(gap_midnight, 3)


def test_distinct_declared_grid_points_at_same_epoch_are_preserved(monkeypatch):
    semantics, _ = push_world()
    scenario = semantics.scenario.model_copy(update={"timezone": "America/New_York"})
    midnight = datetime_midnight("2026-03-08", ZoneInfo(scenario.timezone))
    monkeypatch.setattr(compiler, "PUSH_HOURS_LOCAL", (2, 3))
    pushes = _push_rows(scenario, int(midnight.timestamp()), int((midnight + timedelta(days=1)).timestamp()))
    assert len(pushes) == 2 * len(scenario.agents)
    assert len({row[0] for row in pushes}) == 1
    assert len({row[4] for row in pushes}) == len(pushes)
    due = sorted(pushes, key=lambda row: (row[0], row[4]))
    assert [row[4] for row in due] == sorted(row[4] for row in pushes)
    _, state = push_world()
    moment = due[0][0]
    state.end_time = moment + 86400
    state = at_time(state, moment)
    state.pending_events = [ScheduledEvent(due_at=row[0], agent_id=row[1], kind=row[2], data=row[3],
                                           scheduled_id=row[4], sequence=index)
                            for index, row in enumerate(due, 1)]
    changed, drafts, flags = process_scheduled_events(state, moment, semantics)
    assert [draft.data["scheduled_id"] for draft in drafts] == [row[4] for row in due]
    assert len(current_session(changed, 1).app_pushes) == 2 and flags == {1: {"app_push"}}


def test_push_draw_uses_one_b30_stream_sorted_candidates_and_stable_identity():
    semantics, state = push_world()
    catalog = semantics.catalog
    contents = {key: tuple(reversed(value)) for key, value in reversed(tuple(catalog.contents_by_app.items()))}
    semantics = replace(semantics, catalog=replace(catalog, contents_by_app=contents))
    state.random_state = "中文-seed-17"
    event = push_event(state)
    stream = random.Random(int.from_bytes(hashlib.sha256(
        f"{state.random_state}:{event.scheduled_id}".encode("utf-8")).digest()[:8], "big", signed=False))
    apps = sorted(key for key, values in contents.items() if values)
    selected_app = apps[stream.randrange(len(apps))]
    selected_contents = sorted(contents[selected_app])
    selected_content = selected_contents[stream.randrange(len(selected_contents))]
    first = apply_scheduled_effect(state, event, semantics)
    replay = apply_scheduled_effect(WorldState.model_validate_json(state.model_dump_json()), event, semantics)
    assert first.data["app_id"] == selected_app and first.data["content_id"] == selected_content
    assert first.data["push_id"] == f"{event.scheduled_id}:{selected_app}:{selected_content}"
    assert first.data == replay.data and first.state.model_dump() == replay.state.model_dump()
    assert first.state.random_state == state.random_state == "中文-seed-17"
    assert first.data["kind"] == "app_push" and "content_kind" not in first.data
    assert current_session(state, 1).app_pushes == {}


def test_each_app_is_issued_once_per_session_even_after_release():
    semantics, state = push_world()
    first = apply_scheduled_effect(state, push_event(state), semantics)
    state = release_app_pushes(first.state, 1, first.data["content_id"])
    second = apply_scheduled_effect(state, push_event(state, 9), semantics)
    assert second.data["app_id"] != first.data["app_id"]
    state = release_app_pushes(second.state, 1, second.data["content_id"])
    third = apply_scheduled_effect(state, push_event(state, 10), semantics)
    assert third.data == {"kind": "app_push", "pushed": False, "session_id": current_session(state, 1).session_id}
    assert third.wake is None and third.state is state
    assert len(current_session(third.state, 1).app_pushes) == 2


def test_open_reopen_release_and_after_22_follow_current_session_only():
    semantics, state = push_world()
    session = current_session(state, 1)
    shared = AppPush(push_id="shared-push", content_id="c-long", issued_at=state.current_time, status="pending")
    session.app_pushes = {"douyin": shared, "bilibili": shared.model_copy(update={"push_id": "second-push"})}
    opened, push = open_app_push(state, 1, "douyin")
    assert push is not None
    assert push.status == "opened" and push.content_id == "c-long" and push.push_id == "shared-push"
    later = at_time(opened, state.current_time + 15 * 3600)
    reopened, again = open_app_push(later, 1, "douyin")
    assert again == push and reopened is later
    unrelated = release_app_pushes(reopened, 1, "c-short")
    assert current_session(unrelated, 1).app_pushes["douyin"].status == "opened"
    released = release_app_pushes(unrelated, 1, "c-long")
    assert {push.status for push in current_session(released, 1).app_pushes.values()} == {"released"}
    assert open_app_push(released, 1, "douyin")[1] is None
    assert current_session(state, 1).app_pushes["douyin"].status == "pending"


def test_departure_and_arrival_invalidate_old_pushes_without_deleting_history():
    semantics, state = push_world()
    first = apply_scheduled_effect(state, push_event(state), semantics)
    state = first.state
    progress = ContentProgress(content_id=first.data["content_id"], position_seconds=17, completed_rounds=2)
    state.agents[1].content_progress = {progress.content_id: progress}
    old_id = first.data["session_id"]
    names = {key: item.name for key, item in semantics.catalog.locations.items()}
    travelling = begin_transit(state, 1, "cafe", names, opened_by_action_id="depart")
    assert current_session(travelling, 1).app_pushes == {}
    assert open_app_push(travelling, 1, first.data["app_id"])[1] is None
    road = apply_scheduled_effect(travelling, push_event(travelling, 9), semantics)
    transit_id = road.data["session_id"]
    arrived, _ = end_transit(road.state, 1, names)
    assert current_session(arrived, 1).app_pushes == {}
    assert open_app_push(arrived, 1, road.data["app_id"])[1] is None
    fresh = apply_scheduled_effect(arrived, push_event(arrived, 10), semantics)
    assert fresh.data["session_id"] == current_session(arrived, 1).session_id
    assert fresh.state.sessions[old_id].app_pushes == first.state.sessions[old_id].app_pushes
    assert fresh.state.sessions[transit_id].app_pushes == road.state.sessions[transit_id].app_pushes
    assert fresh.state.agents[1].content_progress == {progress.content_id: progress}
    released = release_app_pushes(fresh.state, 1, fresh.data["content_id"])
    assert released.sessions[old_id].app_pushes == first.state.sessions[old_id].app_pushes


def test_release_only_changes_current_session_and_keeps_historical_matching_push():
    semantics, state = push_world()
    session = current_session(state, 1)
    push = AppPush(push_id="historical", content_id="c-long", issued_at=state.current_time, status="opened")
    session.app_pushes = {"douyin": push}
    names = {key: item.name for key, item in semantics.catalog.locations.items()}
    travelling = begin_transit(state, 1, "cafe", names, opened_by_action_id="depart")
    current_session(travelling, 1).app_pushes = {"bilibili": push.model_copy(update={"push_id": "current"})}
    changed = release_app_pushes(travelling, 1, "c-long")
    assert changed.sessions[session.session_id].app_pushes["douyin"].status == "opened"
    assert current_session(changed, 1).app_pushes["bilibili"].status == "released"


def test_sleep_stores_push_without_wake_and_existing_activity_is_unchanged():
    semantics, state = push_world()
    sleeping = activity(state)
    state.activities[sleeping.activity_id] = sleeping
    event = push_event(state)
    state.pending_events = [event]
    changed, drafts, flags = process_scheduled_events(state, state.current_time, semantics)
    assert flags == {} and changed.activities[sleeping.activity_id] == sleeping
    assert current_session(changed, 1).app_pushes[drafts[0].data["app_id"]].status == "pending"
    assert drafts[0].visibility == "agent" and drafts[0].source_kind == "environment"
    assert WakeReason.APP_PUSH not in SLEEP_ALLOWED_REASONS and len(SLEEP_ALLOWED_REASONS) == 4
    assert not evaluate_wake_reasons(WakeSnapshot(now=state.current_time, app_push_arrived=True,
                                                is_asleep=True)).decision_offered
    changed.activities = {}
    assert open_app_push(changed, 1, drafts[0].data["app_id"])[1].status == "opened"
    state.activities = {"ongoing": activity(state, "use_app")}
    awake = apply_scheduled_effect(state, event, semantics)
    assert awake.wake == "app_push" and awake.state.activities == state.activities
    verdict = evaluate_wake_reasons(WakeSnapshot(now=state.current_time, app_push_arrived=True))
    assert verdict.decision_offered and verdict.wake_reasons == [WakeReason.APP_PUSH]


def test_queue_is_popped_before_effect_checkpoint_and_restore_never_reissues(tmp_path, monkeypatch):
    semantics, state = push_world()
    event = push_event(state)
    state.pending_events = [event]
    original = effects.apply_scheduled_effect
    observed = []

    def observe_pending(ready, due, semantic_input):
        observed.append(WorldState.model_validate_json(ready.model_dump_json()).pending_events)
        return original(ready, due, semantic_input)

    monkeypatch.setattr(effects, "apply_scheduled_effect", observe_pending)
    store = FootprintStore.create(tmp_path / "run", {"run_id": state.run_id, "agent_ids": [1]}, state.model_dump(mode="json"))
    try:
        changed, drafts, flags = process_scheduled_events(state, state.current_time, semantics)
        changed.version += 1
        store.commit(changed.model_dump(mode="json"), drafts)
        snapshots = {key: store.assets.put_json({"participant": key}, {"kind": "environment"})
                     for key in ("agent:1", "life")}
        checkpoint = store.checkpoint(changed.model_dump(mode="json"), snapshots)
        restored = WorldState.model_validate(store.get_checkpoint(checkpoint.checkpoint_id).state)
        repeated, redrafts, reflags = process_scheduled_events(restored, restored.current_time, semantics)
        assert redrafts == [] and reflags == {} and repeated.pending_events == []
        assert observed == [[]]
        assert flags == {1: {"app_push"}} and repeated.random_state == state.random_state
        assert len(store.read_agent_events("environment_event", 1)) == 1
    finally:
        store.close()


def test_full_push_summary_and_source_discriminator_project_from_committed_event(tmp_path):
    from worldmock.life.progress_layer import _world_records
    from worldmock.life.tool_layer import LayerContext

    semantics, state = push_world()
    summary = "完整摘要包含超过二十字的内容，保留每个字符。" * 7
    contents = {key: item.model_copy(update={"summary": summary}) for key, item in semantics.catalog.contents.items()}
    semantics = replace(semantics, catalog=replace(semantics.catalog, contents=contents))
    state.pending_events = [push_event(state)]
    store = FootprintStore.create(tmp_path / "run", {"run_id": state.run_id}, state.model_dump(mode="json"))
    try:
        changed, drafts, _ = process_scheduled_events(state, state.current_time, semantics)
        changed.version += 1
        store.commit(changed.model_dump(mode="json"), drafts)
        context = LayerContext(changed, semantics.scenario, None, semantics, 1, {}, [], None)
        records = _world_records(context, store, 0)
        assert len(records) == 1 and records[0].record == {
            "occurred_at": datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone)).isoformat(),
            "type": "app_push", "app": drafts[0].data["app"], "title": drafts[0].data["title"],
            "summary": summary}
        assert drafts[0].data["kind"] == "app_push" and "content_kind" not in drafts[0].data
        assert drafts[0].source_kind == "environment" and 20 < len(summary) <= 200
        assert records[0].sources[0].event_id == store.read_agent_events("environment_event", 1)[0].event_id
        from worldmock.life.progress_layer import _protected
        from worldmock.life.prompt_capacity import _condensation_steps
        from worldmock.life.settled_text import record_lines

        record = records[0]
        protected = _protected(context, record, (record.sources[0].event_seq, state.current_time))
        assert protected is False
        meta = [{"protected": protected, "lines": record_lines(context, record.record, list(record.sources))}]
        folded, _ = next(_condensation_steps([record.record], meta))
        assert folded[0]["type"] == "condensed"
        assert folded[0]["occurred_at"] == record.record["occurred_at"]
        assert store.read_agent_events("environment_event", 1)[0].data["summary"] == summary
    finally:
        store.close()


def test_no_eligible_app_is_system_visible_without_person_record_or_wake(tmp_path):
    from worldmock.life.progress_layer import _world_records
    from worldmock.life.tool_layer import LayerContext

    semantics, state = push_world()
    session = current_session(state, 1)
    session.app_pushes = {key: AppPush(push_id=f"old-{key}", content_id=value[0], issued_at=state.current_time,
                                     status="released")
                          for key, value in semantics.catalog.contents_by_app.items() if value}
    state.pending_events = [push_event(state)]
    store = FootprintStore.create(tmp_path / "run", {"run_id": state.run_id}, state.model_dump(mode="json"))
    try:
        changed, drafts, flags = process_scheduled_events(state, state.current_time, semantics)
        changed.version += 1
        store.commit(changed.model_dump(mode="json"), drafts)
        context = LayerContext(changed, semantics.scenario, None, semantics, 1, {}, [], None)
        assert len(drafts) == 1 and drafts[0].visibility == "system" and flags == {}
        assert drafts[0].data["pushed"] is False and _world_records(context, store, 0) == []
        assert current_session(changed, 1).app_pushes == session.app_pushes
    finally:
        store.close()


@pytest.mark.parametrize("kind,wake,data", [
    ("calendar_flag", "message_arrival", {"date": "2026-01-05", "flags": ["makeup_workday"]}),
    ("bill_due", "message_arrival", {"bill_id": "bill", "account_id": "agent:1",
                                    "payee_account_id": "transport:service", "amount_fen": 1}),
    ("payroll", "message_arrival", {"payroll_id": "payroll", "employer_account_id": "employer:test",
                                   "agent_id": 1, "amount_fen": 1}),
])
def test_retained_scheduled_outcomes_preserve_source_and_wake(kind, wake, data):
    semantics, state = push_world()
    event = ScheduledEvent(scheduled_id=f"test-{kind}", kind=kind, due_at=state.current_time, sequence=1,
                           agent_id=1, data=data)
    state.pending_events = [event]
    outcome = apply_scheduled_effect(state, event, semantics)
    changed, drafts, flags = process_scheduled_events(state, state.current_time, semantics)
    assert outcome.wake == wake and outcome.source_kind == "scenario"
    assert flags == {1: {wake}} and drafts[0].source_kind == "scenario" and drafts[0].visibility == "agent"
    assert changed.pending_events == [] and drafts[0].data["kind"] == kind


def test_message_and_delivery_keep_their_existing_wake_source():
    from worldmock.life.basket import purchase_order
    from worldmock.life.models import PurchaseItem, PurchaseOrderRequest
    from worldmock.life.social import Message

    semantics, state = push_world()
    reply = Message(message_id="reply", kind="direct", sender_id="agent:2", recipient_id="agent:1",
                    text="测试消息", created_at=state.current_time)
    arrived = ScheduledEvent(scheduled_id="reply", kind="message_arrival", due_at=state.current_time, sequence=1,
                             agent_id=1, data={"message": reply.model_dump()})
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="cafe", food_id="sandwich", quantity=1,
                                                       recipient_id="agent:1"),), fulfillment="delivery",
                                   destination_id="home", app_id=None)
    bought = purchase_order(state, semantics, 1, request)
    delivery = bought.scheduled[0]
    for original, event, wake in ((state, arrived, "message_arrival"),
                                   (at_time(bought.state, delivery.due_at), delivery, "delivery_arrival")):
        outcome = apply_scheduled_effect(original, event, semantics)
        assert outcome.wake == wake and outcome.source_kind == "scenario"


@pytest.mark.parametrize("app_id,content_id", [("unknown-app", "c-long"), ("bilibili", "unknown-content")])
def test_push_identity_validation_rejects_unregistered_references_in_historical_session(app_id, content_id):
    semantics, state = push_world()
    session = current_session(state, 1)
    session.app_pushes = {"bilibili": AppPush(push_id="valid", content_id="c-long",
                                           issued_at=state.current_time, status="opened")}
    validate_app_pushes(state, semantics.catalog)
    names = {key: item.name for key, item in semantics.catalog.locations.items()}
    changed = begin_transit(state, 1, "cafe", names, opened_by_action_id="depart")
    changed.sessions[session.session_id].app_pushes = {app_id: AppPush(push_id="invalid", content_id=content_id,
                                                                   issued_at=state.current_time, status="released")}
    with pytest.raises(IntegrityError, match="Runtime app push refers to an unregistered app or content"):
        validate_app_pushes(changed, semantics.catalog)
