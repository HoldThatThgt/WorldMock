"""@brief 登记 app 目录经真实抽取、身体推进、暂停恢复与全局结算形成连续内容足迹。"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.life.purchase_worlds import ENVIRONMENT, registered_world
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.errors import BusinessRuleError
from worldmock.exchange.constants import APP_USE_DEFAULT_SECONDS, ENTERTAINMENT_RELIEF_PER_HOUR
from worldmock.life import admission, sessions
from worldmock.life.activities import (
    ActivityOrigin, advance_activity, change_activity, content_entertainment_relief_rate,
    entertainment_relief_rate, resource_claims, start_activity,
)
from worldmock.life.app_push import issue_app_push
from worldmock.life.activity_titles import TitleIndex, activity_snapshot, activity_title
from worldmock.life.content import (
    app_content_result, choose_app_content, initialize_app_usage, resume_app_usage, settle_app_usage,
)
from worldmock.life.randomness import random_stream
from worldmock.life.models import (
    ActivityChange, ActivityKind, ActivityStartParams, ActivityStatus, ChangeActivityRequest, StartActivityRequest,
    WorldState,
)
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind


def _strict(state):
    """@brief 每个生产转换后复核严格世界状态，不使用 construct。"""
    return WorldState.model_validate(state.model_dump(mode="json"))


def _world(scenario_path=None):
    """@brief 用真实登记加载器读人工环境，再开始真正的停留会话。"""
    semantics, state = registered_world(scenario_path=scenario_path)
    names = {identity: place.name for identity, place in semantics.catalog.locations.items()}
    return semantics, _strict(sessions.open_stay(state, 1, names))


def _reference_content(catalog, app_id, random_state, key):
    """@brief 冻结公式的独立手算期望，不调用生产抽取辅助函数。"""
    digest = hashlib.sha256(f"{random_state}:{key}".encode("utf-8")).digest()
    stream = random.Random(int.from_bytes(digest[:8], "big", signed=False))
    candidates = sorted(catalog.contents_by_app[app_id])
    return candidates[stream.randrange(len(candidates))]


def test_app_activity_keeps_app_title_and_records_current_content_title():
    """@brief 真实应用使用快照保留应用身份，接续内容只改变独立内容标题。"""
    semantics, state = _world()
    state, activity_id = _open(semantics, state, "douyin", "app-title")
    catalog = semantics.catalog
    titles = TitleIndex(lifestyles={}, apps={key: app.name for key, app in catalog.apps.items()},
                        contents={key: item.title for key, item in catalog.contents.items()},
                        work_topics={}, contacts={}, people={})
    first = state.activities[activity_id]
    snapshot = activity_snapshot(first, titles, ())
    assert activity_title(first, titles) == snapshot["title"] == "抖音"
    assert snapshot["content_title"] == catalog.contents[first.data["content_id"]].title
    state, _ = _advance(semantics, state, activity_id, 600)
    current = state.activities[activity_id]
    snapshot = activity_snapshot(current, titles, ())
    assert snapshot["title"] == "抖音"
    assert snapshot["content_title"] == catalog.contents[current.data["content_id"]].title


def _action_for(catalog, app_id, random_state, content_id, prefix):
    """@brief 用独立冻结公式选择确定会抽到指定登记内容的动作。"""
    return next(f"{prefix}-{index}" for index in range(200)
                if _reference_content(catalog, app_id, random_state, f"{prefix}-{index}:{app_id}") == content_id)


def _open(semantics, state, app_id, action_id):
    """@brief 经真实活动构造、资源声明、会话归属与 app 初始化开始使用。"""
    params = ActivityStartParams(activity_type="use_app", planned_duration_seconds=APP_USE_DEFAULT_SECONDS,
                                 target_id=app_id)
    person, activity = start_activity(state.agents[1], params, state.current_time,
                                     ActivityOrigin(action_id, f"decision-{action_id}", f"request-{action_id}"),
                                     semantics.body)
    changed = state.model_copy(deep=True)
    changed.agents[1] = person
    changed.activities[activity.activity_id] = activity
    changed = initialize_app_usage(changed, activity, semantics.catalog, action_id)
    current = sessions.current_session(changed, 1)
    changed = sessions.assign(changed, current.session_id, [activity.activity_id])
    return _strict(changed), activity.activity_id


def _advance(semantics, state, activity_id, seconds):
    """@brief 按生产内容边界逐区间推进身体和媒体，再调用唯一 app 结算所有者。"""
    until = state.current_time + seconds
    evidence = []
    while state.current_time < until:
        activity = state.activities[activity_id]
        target = min(until, activity.next_interaction_at, activity.planned_end_at)
        advanced = advance_activity(activity, state.agents[1], target, semantics.body)
        changed = state.model_copy(deep=True)
        changed.current_time = target
        changed.agents[1] = advanced.agent
        changed.activities[activity_id] = advanced.activity
        state, interval = settle_app_usage(changed, advanced.activity, semantics.catalog,
                                          activity.media_position_seconds)
        evidence.append(interval)
        state = _strict(state)
        if advanced.completed:
            break
    return state, evidence


def _change(semantics, state, activity_id, operation):
    """@brief 真实暂停和恢复调用领域转换；恢复再按唯一全局位置重建内容快照。"""
    activity = state.activities[activity_id]
    person, changed_activity = change_activity(
        state.agents[1], activity, ChangeActivityRequest(activity_id=activity_id, operation=operation),
        state.current_time, semantics.scenario.activity_feedback_seconds)
    changed = state.model_copy(deep=True)
    changed.agents[1], changed.activities[activity_id] = person, changed_activity
    if operation == ActivityChange.RESUME:
        changed = resume_app_usage(changed, changed_activity, semantics.catalog)
    return _strict(changed)


def _shared_short_world(tmp_path):
    """@brief 登记一个两个 app 共同绑定的短视频，用真实跨 app 完成覆盖交错恢复。"""
    root = tmp_path / "shared-content-environment"
    shutil.copytree(ENVIRONMENT, root)
    path = root / "catalog/contents.json"
    payload = json.loads(path.read_text())
    row = next(item for item in payload["contents"] if item["content_id"] == "c-short")
    row["app_ids"] = ["bilibili", "douyin"]
    path.write_text(json.dumps({"contents": [row]}, ensure_ascii=False) + "\n", encoding="utf-8")
    refresh_manifest_identity(root / "catalog")
    return _world(root / "scenarios/single-person.json")


def test_open_only_selects_selected_app_and_denies_empty_app():
    """@brief 两百种种子始终只消费所选 app，空内容 app 明确拒绝。"""
    semantics, state = _world()
    for seed in range(200):
        varied = state.model_copy(update={"random_state": f"seed-{seed}"}, deep=True)
        opened, identity = _open(semantics, varied, "douyin", f"open-{seed}")
        activity = opened.activities[identity]
        assert activity.data["content_id"] in semantics.catalog.contents_by_app["douyin"]
        assert activity.data["draw_index"] == 0
        assert activity.planned_duration_seconds == 1800
        assert activity.resource_claims == {"use_app"}
        assert opened.random_state == varied.random_state
    with pytest.raises(BusinessRuleError, match="APP_CONTENT_EMPTY"):
        choose_app_content(semantics.catalog, "meituan", state.random_state, "open:meituan")
    with pytest.raises(ValidationError):
        StartActivityRequest.model_validate({"items": [{"target": "应用：抖音", "way": "打开", "from": None,
                                                        "to": None, "content_id": "c-long"}], "prefer": None})


def test_cross_app_pause_resumes_latest_global_position():
    """@brief app A 暂停后 app B 继续推进；恢复 A 从最新位置继续而非回写旧位置。"""
    semantics, state = _world()
    a_key = _action_for(semantics.catalog, "douyin", state.random_state, "c-long", "a")
    state, first = _open(semantics, state, "douyin", a_key)
    state, intervals = _advance(semantics, state, first, 1000)
    assert intervals[-1]["position_after_seconds"] == 1000
    state = _change(semantics, state, first, ActivityChange.PAUSE)
    b_key = _action_for(semantics.catalog, "bilibili", state.random_state, "c-long", "b")
    state, second = _open(semantics, state, "bilibili", b_key)
    assert state.activities[second].media_position_seconds == 1000
    state, _ = _advance(semantics, state, second, 500)
    state = _change(semantics, state, second, ActivityChange.PAUSE)
    state = _change(semantics, state, first, ActivityChange.RESUME)
    resumed = state.activities[first]
    assert resumed.data["content_id"] == "c-long"
    assert resumed.data["draw_index"] == 0
    assert resumed.media_position_seconds == 1500
    assert state.agents[1].content_progress["c-long"].position_seconds == 1500
    assert resumed.next_interaction_at == min(state.current_time + 900, resumed.planned_end_at)
    state, intervals = _advance(semantics, state, first, 100)
    assert intervals[-1]["position_before_seconds"] == 1500
    assert state.agents[1].content_progress["c-long"].position_seconds == 1600


def test_other_app_completed_round_resume_consumes_nothing_then_real_end_adds_once(tmp_path):
    """@brief 跨 app 已完成一轮时恢复从零开始，恢复不增轮数；实际末尾才再次增一轮。"""
    semantics, state = _shared_short_world(tmp_path)
    for sequence in (1, 2):
        event = ScheduledEvent(scheduled_id=f"push-{sequence}", kind=ScheduledKind.APP_PUSH,
                               due_at=state.current_time, sequence=sequence, agent_id=1, data={})
        state, _ = issue_app_push(state, event, semantics.catalog)
    state, first = _open(semantics, state, "douyin", "short-a")
    state, _ = _advance(semantics, state, first, 60)
    state = _change(semantics, state, first, ActivityChange.PAUSE)
    state, second = _open(semantics, state, "bilibili", "short-b")
    assert state.activities[second].media_position_seconds == 60
    state, intervals = _advance(semantics, state, second, 60)
    assert intervals == [{"content_id": "c-short", "app_id": "bilibili",
                          "position_before_seconds": 60, "position_after_seconds": 120,
                          "completed": True, "completed_rounds_after": 1}]
    assert state.agents[1].content_progress["c-short"].position_seconds == 0
    state = _change(semantics, state, second, ActivityChange.PAUSE)
    released = sessions.current_session(state, 1).app_pushes
    assert all(push.status == "released" for push in released.values())
    state = _change(semantics, state, first, ActivityChange.RESUME)
    resumed = state.activities[first]
    assert resumed.media_position_seconds == 0
    assert resumed.data["content_id"] == "c-short" and resumed.data["repeat"] is True
    assert resumed.data["draw_index"] == 0
    assert state.agents[1].content_progress["c-short"].completed_rounds == 1
    assert sessions.current_session(state, 1).app_pushes == released
    state, intervals = _advance(semantics, state, first, 120)
    assert intervals[-1]["completed_rounds_after"] == 2
    assert len(state.activities[first].data["played"]) == 1
    assert state.activities[first].data["played"][0] == {
        "content_id": "c-short", "from_position": 0, "to_position": 120}


def test_short_content_continues_silently_until_plan_and_repeats_allowed(tmp_path):
    """@brief 内容结束即静默接续，同一条允许反复消费，计划结束落全局且不多抽一条。"""
    semantics, state = _shared_short_world(tmp_path)
    state, identity = _open(semantics, state, "douyin", "whole-plan")
    assert state.activities[identity].next_interaction_at == state.current_time + 120
    state, first = _advance(semantics, state, identity, 120)
    activity = state.activities[identity]
    assert first[0]["completed"] is True
    assert activity.status == ActivityStatus.ACTIVE and activity.boundary_type is None
    assert activity.data["draw_index"] == 1 and activity.data["content_id"] == "c-short"
    assert activity.media_position_seconds == 0 and activity.data["repeat"] is True
    state, remaining = _advance(semantics, state, identity, 1680)
    ended = state.activities[identity]
    assert ended.elapsed_seconds == 1800 and ended.status == ActivityStatus.COMPLETED
    assert ended.boundary_type == "planned_end" and ended.next_interaction_at is None
    assert len(ended.data["played"]) == 15
    assert ended.data["draw_index"] == 14
    assert state.agents[1].content_progress["c-short"].completed_rounds == 15
    assert state.agents[1].content_progress["c-short"].position_seconds == 0
    assert len(remaining) == 14


def test_plan_end_and_active_conclude_preserve_partial_content_progress():
    """@brief 计划结束与主动结束都保留实际消费位置，重新打开同一内容从全局位置继续。"""
    semantics, state = _world()
    action = _action_for(semantics.catalog, "douyin", state.random_state, "c-long", "partial")
    state, identity = _open(semantics, state, "douyin", action)
    state, intervals = _advance(semantics, state, identity, 1800)
    assert intervals[-1]["completed"] is False
    assert state.agents[1].content_progress["c-long"].position_seconds == 1800
    assert state.agents[1].content_progress["c-long"].completed_rounds == 0
    assert state.activities[identity].status == ActivityStatus.COMPLETED
    action = _action_for(semantics.catalog, "bilibili", state.random_state, "c-long", "reopen")
    state, second = _open(semantics, state, "bilibili", action)
    assert state.activities[second].media_position_seconds == 1800
    state, _ = _advance(semantics, state, second, 100)
    state = _change(semantics, state, second, ActivityChange.CANCEL)
    assert state.agents[1].content_progress["c-long"].position_seconds == 1900
    assert state.activities[second].status == ActivityStatus.CANCELLED


def test_b30_exact_seed_bytes_open_and_continuation_keys_survive_roundtrip():
    """@brief 字节、大端解释、排序和两类键均与独立期望一致；严格往返不会重抽。"""
    semantics, state = _world()
    for key in ("打开:抖音", "plain:app", "activity-app:1"):
        expected = random.Random(int.from_bytes(hashlib.sha256(f"{state.random_state}:{key}".encode()).digest()[:8],
                                              "big", signed=False)).getrandbits(64)
        assert random_stream(state.random_state, key).getrandbits(64) == expected
    action = _action_for(semantics.catalog, "douyin", state.random_state, "c-short", "stream")
    state, identity = _open(semantics, state, "douyin", action)
    activity = state.activities[identity]
    assert activity.data["content_id"] == _reference_content(semantics.catalog, "douyin", state.random_state,
                                                             f"{action}:douyin")
    state, _ = _advance(semantics, state, identity, 60)
    state = _strict(state)
    assert state.activities[identity].data["draw_index"] == 0
    state, _ = _advance(semantics, state, identity, 60)
    activity = state.activities[identity]
    assert activity.data["content_id"] == _reference_content(semantics.catalog, "douyin", state.random_state,
                                                             f"{identity}:1")
    assert activity.data["draw_index"] == 1


def test_paused_resume_retains_content_pin_and_random_state():
    """@brief 普通暂停恢复保留内容、推送 pin 与 draw_index，动作本身没有播放。"""
    semantics, state = _world()
    event = ScheduledEvent(scheduled_id="pin-open", kind=ScheduledKind.APP_PUSH, due_at=state.current_time,
                           sequence=1, agent_id=1, data={})
    state, pushed = issue_app_push(state, event, semantics.catalog)
    different = next(identity for identity in semantics.catalog.contents_by_app[pushed["app_id"]]
                     if identity != pushed["content_id"])
    action = _action_for(semantics.catalog, pushed["app_id"], state.random_state, different, "pin-miss")
    state, identity = _open(semantics, state, pushed["app_id"], action)
    state, _ = _advance(semantics, state, identity, 30)
    state = _change(semantics, state, identity, ActivityChange.PAUSE)
    before = state.activities[identity]
    pushes_before = sessions.current_session(state, 1).app_pushes
    assert pushes_before[pushed["app_id"]].status == "opened"
    rounds = state.agents[1].content_progress[before.data["content_id"]].completed_rounds
    state = _change(semantics, state, identity, ActivityChange.RESUME)
    resumed = state.activities[identity]
    assert resumed.data["content_id"] == pushed["content_id"]
    assert resumed.data["pinned_by_push_id"] == pushed["push_id"]
    assert resumed.data["draw_index"] == 0 and resumed.media_position_seconds == 30
    assert state.agents[1].content_progress[before.data["content_id"]].completed_rounds == rounds
    assert sessions.current_session(state, 1).app_pushes == pushes_before
    assert state.random_state == "pure-fixed"
    assert app_content_result(resumed, semantics.catalog) == {
        "app": pushed["app"], "title": pushed["title"],
        "summary": pushed["summary"], "pushed": True}


def test_content_relief_uses_one_fixed_rate_and_is_applied_to_real_body():
    """@brief 娱乐直接使用同一系统常数，实际身体结算不依赖内容分类或任何偏好字段。"""
    semantics, state = _world()
    assert content_entertainment_relief_rate() == ENTERTAINMENT_RELIEF_PER_HOUR == 0.3
    action = _action_for(semantics.catalog, "douyin", state.random_state, "c-long", "relief")
    state.agents[1].body.entertainment_need = 0.8
    state, identity = _open(semantics, state, "douyin", action)
    activity = state.activities[identity]
    assert entertainment_relief_rate(activity, semantics.body) == 0.3
    state, _ = _advance(semantics, state, identity, 900)
    assert state.agents[1].body.entertainment_need == pytest.approx(0.725)
    assert "taste_weights" not in state.agents[1].model_dump()
    driving = ActivityStartParams(activity_type="travel", planned_duration_seconds=100,
                                  destination_id="office", travel_mode="driving")
    consuming = ActivityStartParams(activity_type="consume", planned_duration_seconds=60)
    using = ActivityStartParams(activity_type="use_app", planned_duration_seconds=1800, target_id="douyin")
    assert "use_app" in resource_claims(driving)
    assert not resource_claims(consuming) & resource_claims(using)
    assert resource_claims(using) & resource_claims(using) == {"use_app"}


def test_real_admission_blocks_driving_and_same_app_kind_accepts_eating_parallel():
    """@brief 使用真实资源声明与严格世界，通过生产整组准入验证 app 并行规则。"""
    semantics, state = _world()
    names = {identity: place.name for identity, place in semantics.catalog.locations.items()}
    params = ActivityStartParams(activity_type="use_app", planned_duration_seconds=1800, target_id="douyin")
    item = admission.Item(canonical="use_app", way="打开", label="打开抖音",
                          claims=frozenset(resource_claims(params)))
    driving = ActivityStartParams(activity_type="travel", planned_duration_seconds=600,
                                  destination_id="office", travel_mode="driving")
    person, activity = start_activity(state.agents[1], driving, state.current_time,
                                     ActivityOrigin("drive", "decision-drive", "request-drive"), semantics.body)
    blocked = state.model_copy(deep=True)
    blocked.agents[1], blocked.activities[activity.activity_id] = person, activity
    blocked = _strict(blocked)
    context = admission.Context(blocked, 1, names, semantics.catalog.graph)
    with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
        admission.require_group(context, [item])
    eating = ActivityStartParams(activity_type="consume", planned_duration_seconds=300)
    person, activity = start_activity(state.agents[1], eating, state.current_time,
                                     ActivityOrigin("eat", "decision-eat", "request-eat"), semantics.body)
    allowed = state.model_copy(deep=True)
    allowed.agents[1], allowed.activities[activity.activity_id] = person, activity
    allowed = _strict(allowed)
    context = admission.Context(allowed, 1, names, semantics.catalog.graph)
    admission.require_group(context, [item])
    state, _ = _open(semantics, state, "douyin", "already-open")
    with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
        admission.require_group(admission.Context(state, 1, names, semantics.catalog.graph), [item])


def test_reopen_after_conclusion_keeps_opened_push_and_latest_global_position():
    """@brief 本会话提前结束后重新打开仍命中同一推送，从全局剩余位置继续。"""
    semantics, state = _world()
    event = ScheduledEvent(scheduled_id="reopen-push", kind=ScheduledKind.APP_PUSH, due_at=state.current_time,
                           sequence=1, agent_id=1, data={})
    state, pushed = issue_app_push(state, event, semantics.catalog)
    state, first = _open(semantics, state, pushed["app_id"], "first-pushed")
    state, _ = _advance(semantics, state, first, 30)
    state = _change(semantics, state, first, ActivityChange.CANCEL)
    different = next(identity for identity in semantics.catalog.contents_by_app[pushed["app_id"]]
                     if identity != pushed["content_id"])
    action = _action_for(semantics.catalog, pushed["app_id"], state.random_state, different, "reopen-miss")
    state, second = _open(semantics, state, pushed["app_id"], action)
    assert first != second
    assert state.activities[second].data["content_id"] == pushed["content_id"]
    assert state.activities[second].data["pinned_by_push_id"] == pushed["push_id"]
    assert state.activities[second].media_position_seconds == 30
    assert sessions.current_session(state, 1).app_pushes[pushed["app_id"]].status == "opened"


def test_content_completion_occurs_at_actual_end_only(tmp_path):
    """@brief 内容还剩一秒时不增轮数，真实末尾恰好完成一次。"""
    semantics, state = _shared_short_world(tmp_path)
    state, identity = _open(semantics, state, "douyin", "exact-end")
    state, intervals = _advance(semantics, state, identity, 119)
    assert intervals[-1]["completed"] is False
    assert state.agents[1].content_progress["c-short"].completed_rounds == 0
    assert state.agents[1].content_progress["c-short"].position_seconds == 119
    assert state.activities[identity].data["draw_index"] == 0
    state, intervals = _advance(semantics, state, identity, 1)
    assert intervals[-1]["completed"] is True
    assert state.agents[1].content_progress["c-short"].completed_rounds == 1
    assert state.activities[identity].data["draw_index"] == 1


def test_initialize_app_usage_does_not_mutate_previous_strict_world():
    """@brief 无推送打开也返回独立状态，旧世界和活动快照保持原字节语义。"""
    semantics, state = _world()
    params = ActivityStartParams(activity_type="use_app", planned_duration_seconds=1800, target_id="douyin")
    person, activity = start_activity(state.agents[1], params, state.current_time,
                                     ActivityOrigin("pure-open", "decision-pure", "request-pure"), semantics.body)
    staged = state.model_copy(update={"agents": {1: person}, "activities": {activity.activity_id: activity}}, deep=True)
    staged = _strict(staged)
    previous = staged.model_dump(mode="json")
    initialized = initialize_app_usage(staged, activity, semantics.catalog, "pure-open")
    assert staged.model_dump(mode="json") == previous
    assert initialized is not staged
    assert initialized.activities[activity.activity_id].data["app_id"] == "douyin"
    _strict(initialized)


def test_real_app_interval_advances_entertainment_goal_and_retired_kinds_do_not():
    """@brief 当日娱乐目标只由真实 app 或生活娱乐活动推进，旧种类没有计划匹配资格。"""
    from worldmock.life.schedule import (
        ACTIVITY_KIND_FAMILIES, INTENT_ACTIVITY_FAMILIES, DayPlan, PlanGoal,
        activity_family, progress_day_plan,
    )

    plan = DayPlan(day_end=1000, goals=[PlanGoal(intent_kind="娱乐", goal="休息时刷手机", registered_at=100)])
    actual = {"activity_id": "app-a", "kind": "use_app", "status": "active"}
    progressed = progress_day_plan(plan, actual, 100, 200)
    assert progressed.goals[0].progressed_at == 200
    assert progressed.goals[0].progress_activity_id == "app-a"
    assert ACTIVITY_KIND_FAMILIES["use_app"] == "entertainment"
    assert INTENT_ACTIVITY_FAMILIES["娱乐"] == {"use_app", "lifestyle:hobby"}
    for retired in ("watch", "play_game", "browse"):
        invalid = {**actual, "kind": retired}
        assert activity_family(invalid) is None
        assert progress_day_plan(plan, invalid, 100, 200) == plan
