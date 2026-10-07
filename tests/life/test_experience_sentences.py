"""已核验经历只作私有证据，不写成提示句（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第二章「删除与去重」）。

观察的私有证据 data.verified_experiences 保留结构行与来源身份（记忆写入证据依赖它），七层中没有经历句子；零点计划
输入仍用场景的地点、商品与内容名称把经历写成句子（settled_text 的共享写法，例如「14:17 开车到了张江办公室」
「13:57–14:17 吃了番茄炒蛋」），出行方式取自所属出行活动。
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from tests.life.conftest import (
    advance_until_ready, conclude_pending, pause_request, report_request,
    stage_day_plan, start_activity, wake_by_mail,
)
from tests.life.test_app_progress import _action_for, _advance, _change, _open, _world
from tests.life.test_location_sessions import _depart, _preview, _round, _started_ids, _until_ended, _wait
from worldmock.life import projection
from worldmock.life.activities import change_activity
from worldmock.life.memory_layer import memory_view
from worldmock.life.readable import clock_phrase
from worldmock.life.settled_text import activity_sentence, experience_sentence
from worldmock.life.models import ActivityChange, ActivityStatus, ChangeActivityRequest, TravelMode

_HEX = re.compile(r"[0-9a-f]{8,}")


def _clock(engine, epoch: int) -> str:
    """@brief 场景时区的 HH:MM。"""
    return f"{projection.local_datetime(epoch, engine.context.scenario.timezone):%H:%M}"


def _place(engine, location_id: str) -> str:
    """@brief 场景地点名。"""
    return next(item.name for item in engine.context.scenario.locations if item.location_id == location_id)


def test_observation_keeps_experiences_private_and_out_of_the_layers(engine_factory) -> None:
    """真实观察：私有窗口保留等待与到达两条结构行与来源身份；七层不含经历句子与来源身份；同一窗口写成的零点计划句子
    逐条对应、同序，等待带起止时刻，到达带方式与地点名。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "sentence-wait")
    _until_ended(engine, waited.activity_id, "sentence-wait")
    conclude_pending(engine, "sentence-wait-end")
    travel_id = _started_ids(_depart(engine, "sentence-walk"))[0]
    _until_ended(engine, travel_id, "sentence-walk")
    observed = _preview(engine, "sentences")
    rows = observed["verified_experiences"]
    inputs = SimpleNamespace(scenario=engine.context.scenario, state=engine.context.state, rules=engine.context.rules)
    sentences = [experience_sentence(inputs, row) for row in rows]
    assert [row["kind"] for row in rows] == ["waited", "arrived"]
    assert len(sentences) == len(rows)
    wait_row, arrival_row = rows
    assert sentences[0].startswith(
        f"{_clock(engine, wait_row['interval_start'])}–{_clock(engine, wait_row['interval_end'])} 等待了")
    assert sentences[1] == f"{_clock(engine, arrival_row['occurred_at'])} 走路到了{_place(engine, 'office')}"
    for row, sentence in zip(rows, sentences):
        assert row["activity_id"] not in sentence and row["source_event_id"] not in sentence
        assert not _HEX.search(sentence) and "office" not in sentence
    rendered = json.dumps([observed[key] for key in ("tool_layer", "todo_layer", "environment_layer",
                                                     "memory_layer", "progress_layer")], ensure_ascii=False)
    assert sentences[1] not in rendered and arrival_row["source_event_id"] not in rendered
    assert engine.context.state.activities[travel_id].status == ActivityStatus.COMPLETED
    assert conclude_pending(engine, "sentence-walk-end").status == "completed"


def test_every_experience_kind_is_written_as_a_sentence_without_identities(engine_factory) -> None:
    """四种经历的句式使用当前食物、应用与内容名称；保留跨日钟点、开车及到达被拒的事实。"""
    engine = engine_factory()
    stage_day_plan(engine)
    travel_id = _started_ids(_depart(engine, "kinds-walk"))[0]
    driving = engine.context.state.activities[travel_id].model_copy(update={"travel_mode": TravelMode.DRIVING}, deep=True)
    state = engine.context.state.model_copy(update={"activities": {**engine.context.state.activities, "drive": driving}})
    now = engine.context.state.current_time
    food = engine.context.scenario.foods[0]
    article = engine.context.rules.contents["c-article"]
    video = engine.context.rules.contents["c-long"]
    common = {"agent_id": 1, "source_event_id": "event-0123456789abcdef", "event_seq": 1, "occurred_at": now}
    facts = [
        {**common, "kind": "waited", "activity_id": "wait", "interval_start": now - 600, "interval_end": now,
         "duration_seconds": 600, "reason": "wait"},
        {**common, "kind": "consumed", "activity_id": "eat", "food_id": food.food_id, "category": "meal",
         "location_id": "home", "interval_portion": 1.0, "cumulative_portion": 1.0,
         "interval_start": now - 1200, "interval_end": now},
        {**common, "kind": "played", "activity_id": "article", "content_id": article.content_id, "app_id": "bilibili",
         "position_before_seconds": 0, "position_after_seconds": 60, "interval_start": now - 60, "interval_end": now},
        {**common, "kind": "played", "activity_id": "video", "content_id": video.content_id, "app_id": "douyin",
         "position_before_seconds": 0, "position_after_seconds": 60, "interval_start": now - 60, "interval_end": now},
        {**common, "kind": "arrived", "activity_id": "drive", "location_id": "office", "access_status": "allowed"},
        {**common, "kind": "arrived", "activity_id": travel_id, "location_id": "office", "access_status": "denied"},
    ]
    inputs = SimpleNamespace(scenario=engine.context.scenario, state=state, rules=engine.context.rules)
    def clock(epoch: int) -> str:
        """@brief 共享句式里的钟点：与此刻不在同一本地日期时带「M月D日」。"""
        return clock_phrase(epoch, now, engine.context.scenario.timezone)

    span = f"{clock(now - 600)}–{clock(now)}"
    office = _place(engine, "office")
    assert [experience_sentence(inputs, fact) for fact in facts] == [
        f"{span} 等待了约 10 分钟",
        f"{clock(now - 1200)}–{clock(now)} 吃了{food.name}",
        f"{clock(now - 60)}–{clock(now)} 用哔哩哔哩看了《{article.title}》",
        f"{clock(now - 60)}–{clock(now)} 用抖音看了《{video.title}》",
        f"{clock(now)} 开车到了{office}",
        f"{clock(now)} 走路到了{office}，但没能进去",
    ]


@pytest.mark.parametrize("seconds", [0, 60, 120, 150, 1800])
def test_settled_app_sentence_uses_only_actual_completed_and_advanced_content(seconds):
    """真实 app 初始化与逐内容结算：已完成写全文标题，新抽内容零推进不冒充部分观看。"""
    semantics, state = _world()
    key = _action_for(semantics.catalog, "douyin", state.random_state, "c-short", "settled")
    state, identity = _open(semantics, state, "douyin", key)
    state, intervals = _advance(semantics, state, identity, seconds)
    activity = state.activities[identity]
    inputs = SimpleNamespace(state=state, scenario=semantics.scenario, rules=semantics.catalog,
                             names={"activities": {identity: "应用：抖音"}})
    advanced = {}
    for interval in intervals:
        if interval["completed"]:
            advanced.pop(identity, None)
        elif interval["position_after_seconds"] > interval["position_before_seconds"]:
            advanced[identity] = interval["content_id"]
    sentence = activity_sentence(inputs, activity, ((state.current_time - 30, state.current_time - 10),), advanced)
    titles = "".join(f"《{semantics.catalog.contents[row['content_id']].title}》" for row in activity.data["played"])
    current = semantics.catalog.contents[activity.data["content_id"]]
    assert "暂停 " in sentence
    if seconds == 0:
        assert "用抖音看了" not in sentence
    elif seconds == 60:
        assert sentence.endswith("用抖音看了《测试短视频》的一部分")
    elif seconds == 120:
        assert sentence.endswith("用抖音看了《测试短视频》") and "看了一部分" not in sentence
    elif seconds == 150:
        assert sentence.endswith(f"用抖音看了{titles}（《{current.title}》看了一部分）")
    else:
        assert sentence.endswith(f"用抖音看了{titles}（《{current.title}》看了一部分）")
    assert identity not in sentence and activity.data["content_id"] not in sentence


def test_real_pause_resume_immediate_cancellation_keeps_prior_partial_app_viewing(engine_factory):
    """真实宿主暂停与恢复后，在同刻用生产取消转换复核句子；不虚构宿主的再次决策资格。"""
    engine = engine_factory()
    stage_day_plan(engine)
    action = _action_for(engine.context.semantics.catalog, "bilibili", engine.context.state.random_state,
                         "c-long", "action-paused-view")
    _, started = _round(engine, action.removeprefix("action-"), "start_activity",
                        start_activity("打开", "应用：哔哩哔哩"))
    assert engine.context.state.activities[started.activity_id].data["content_id"] == "c-long"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    current = engine.context.state.activities[started.activity_id]
    assert current.media_position_seconds == 60
    _, paused = _round(engine, "paused-view-pause", "pause_activity", pause_request(current.name))
    assert paused.status.value == "completed"
    _, resumed = _round(engine, "paused-view-resume", "report_status", report_request(current.name))
    assert resumed.status.value == "completed"
    resumed_activity = engine.context.state.activities[started.activity_id]
    assert resumed_activity.data["start_position_seconds"] == resumed_activity.media_position_seconds == 60
    _, ended = change_activity(engine.context.state.agents[1], resumed_activity,
                               ChangeActivityRequest(activity_id=started.activity_id, operation=ActivityChange.CANCEL),
                               engine.context.state.current_time, engine.context.scenario.activity_feedback_seconds)
    inputs = engine.observations._layer_context(engine.context.state.agents[1], (), [])
    facts = memory_view(inputs, engine.context.store).facts
    sentence = activity_sentence(inputs, ended, facts.pauses[started.activity_id], facts.app_progress)
    assert sentence.endswith("用哔哩哔哩看了《测试长视频》的一部分") and "暂停 " in sentence
    assert ended.elapsed_seconds == 60 and ended.status == ActivityStatus.CANCELLED
    observed = _preview(engine, "paused-view-done")
    played = [row for row in observed["verified_experiences"] if row["kind"] == "played"]
    assert len(played) == 1 and played[0]["app_id"] == "bilibili"
    assert played[0]["position_after_seconds"] - played[0]["position_before_seconds"] == 60


def test_shared_cursor_advance_from_another_app_is_not_this_activitys_viewing():
    """另一个 app 推进共享游标后，本活动恢复但没有实际消费；句子不能把别处的观看算到本活动。"""
    semantics, state = _world()
    key = _action_for(semantics.catalog, "bilibili", state.random_state, "c-long", "unwatched")
    state, original = _open(semantics, state, "bilibili", key)
    state = _change(semantics, state, original, ActivityChange.PAUSE)
    key = _action_for(semantics.catalog, "douyin", state.random_state, "c-long", "watched")
    state, other = _open(semantics, state, "douyin", key)
    state, actual = _advance(semantics, state, other, 60)
    assert actual[-1]["position_after_seconds"] == 60
    state = _change(semantics, state, other, ActivityChange.PAUSE)
    state = _change(semantics, state, original, ActivityChange.RESUME)
    assert state.activities[original].media_position_seconds == 60
    state = _change(semantics, state, original, ActivityChange.CANCEL)
    inputs = SimpleNamespace(state=state, scenario=semantics.scenario, rules=semantics.catalog,
                             names={"activities": {original: "应用：哔哩哔哩"}})
    assert "用哔哩哔哩看了" not in activity_sentence(inputs, state.activities[original], (), {})
