"""内容 app 自动接续的选取细则、随时可暂停的判定、「结束使用」的唯一判定与进度快照的形状，直接调用生产函数。

接续顺序在真实目录行上算：样例环境副本加「测试番剧」与「测试纪录片」两部两集的哔哩哔哩系列，世界取
test_environment_content.world_at（人物停留在家，当前会话是 stay），进度、推送与暂停的活动手写。
"""

from __future__ import annotations

from random import Random

import pytest

from tests.life.environment_inputs import bilibili_series_rows, second_series_environment, set_content
from tests.life.prerequisite_inputs import add_rows
from tests.unit.life.test_environment_content import world_at
from tests.unit.test_environment_engine_admission import occupied, running
from worldmock.life import activity_control
from worldmock.life.activity_evidence import OUTCOME_APP_USE_ENDED, app_use_ended, conclusion_outcome
from worldmock.life.activity_titles import TitleIndex, activity_snapshot, progress_snapshot
from worldmock.life.content_select import ContentRequest, autoplay_content
from worldmock.life.models import ActivityKind, ActivityStatus, AppPush, ContentProgress

#: 每种情形都用这几个种子各抽一次：候选集合错了，总有一个种子抽到别的。
_SEEDS = range(6)


@pytest.fixture(scope="module")
def catalogs(tmp_path_factory):
    """两份副本的根目录：plain 是三部系列都可推送的副本；unpushable 另把「测试纪录片第二集」改成不可推送。"""
    roots = {}
    for name in ("plain", "unpushable"):
        root = second_series_environment(tmp_path_factory.mktemp(name) / "environment")
        add_rows(root, "content-catalog.jsonl", "content_id", *bilibili_series_rows("c-doc", "测试纪录片", "low"))
        if name == "unpushable":
            set_content(root, "c-doc-2", pushable=False)
        roots[name] = root
    return roots


#: 情形：（副本，刚放完的那一条，进度 {内容: (位置, 轮数, 晚于开始的秒数)}，推送 (app, 内容, 状态) 或 None，
#: 暂停活动停在的内容或 None，期望）。期望是一条内容或 None 时不取随机数；是元组时它是第 3 行按身份升序的全部候选，
#: 随机流恰取一次 randrange。
_SEEN = {"c-image": (0, 1, 0), "c-long": (0, 1, 0)}
_ANIME_DONE = {"c-anime-1": (0, 1, 0), "c-anime-2": (0, 1, 0)}
_CASES = {
    "only-the-unpushable-item-is-unseen": ("plain", "c-image", {**_SEEN, "c-article": (0, 1, 0)}, None, None, None),
    "the-unseen-item-is-reserved-by-a-bilibili-push": (
        "plain", "c-image", _SEEN, ("bilibili", "c-article", "pending"), None, None),
    "the-unseen-item-is-reserved-by-a-douyin-push": (
        "plain", "c-image", _SEEN, ("douyin", "c-article", "opened"), None, None),
    "a-released-push-reserves-nothing": (
        "plain", "c-image", _SEEN, ("bilibili", "c-article", "released"), None, ("c-article",)),
    "unseen-means-no-progress-record-and-never-a-series": (
        "plain", "c-image", {**_SEEN, "c-long": (120, 0, 0)}, None, None, ("c-article",)),
    "every-unseen-pushable-item-is-a-candidate": (
        "plain", "c-image", {"c-image": (0, 1, 0)}, None, None, ("c-article", "c-long")),
    "equal-recency-goes-to-the-smaller-representative": (
        "plain", "c-anime-2", {**_ANIME_DONE, "c-drama-1": (300, 0, 60), "c-doc-1": (0, 1, 60)}, None, None,
        "c-doc-2"),
    "the-most-recently-watched-series-wins": (
        "plain", "c-anime-2", {**_ANIME_DONE, "c-drama-1": (300, 0, 120), "c-doc-1": (0, 1, 60)}, None, None,
        "c-drama-1"),
    "a-series-watched-to-equal-rounds-is-not-started": (
        "plain", "c-anime-2", {**_ANIME_DONE, "c-doc-1": (0, 1, 60), "c-doc-2": (0, 1, 60)}, None, None, None),
    "a-paused-use-reserves-its-episode": (
        "plain", "c-anime-2", {**_ANIME_DONE, "c-drama-1": (300, 0, 60)}, None, "c-drama-1", None),
    "the-finished-series-is-never-chosen-again": (
        "plain", "c-anime-2", {"c-anime-1": (0, 2, 0), "c-anime-2": (0, 1, 0)}, None, None, None),
    "an-unpushable-representative-skips-the-series": (
        "unpushable", "c-anime-2", {**_ANIME_DONE, "c-doc-1": (0, 1, 60)}, None, None, None),
    "a-pushable-representative-is-chosen-despite-an-unpushable-episode": (
        "unpushable", "c-anime-2", {**_ANIME_DONE, "c-doc-1": (120, 0, 60)}, None, None, "c-doc-1"),
}


def _world(root, progress, push, paused):
    """按情形手写进度、当前会话的推送与一项暂停的 app 使用；返回（目录，世界）。"""
    context, state = world_at(root)
    start = state.current_time
    state.agents[1].content_progress = {
        key: ContentProgress(content_id=key, position_seconds=position, completed_rounds=rounds,
                             last_consumed_at=start + later)
        for key, (position, rounds, later) in progress.items()}
    if push is not None:
        app_id, content_id, status = push
        state.sessions["stay"].app_pushes[app_id] = AppPush(push_id="push", content_id=content_id,
                                                            issued_at=start, status=status)
    if paused is not None:
        held = running("interact", ActivityKind.INTERACT, "second")
        state.activities[held.activity_id] = held.model_copy(update={
            "status": ActivityStatus.PAUSED, "data": {**held.data, "app_id": "bilibili", "content_id": paused}})
    return context.catalog, state


@pytest.mark.parametrize("case", list(_CASES))
def test_autoplay_content_picks_by_the_rows_on_real_catalog_rows(catalogs, case):
    """接续顺序的选取细则：可推送与预留按将要放的那一条判，开始了没看完有两种情形、看完的不算，同一时刻按代表集身份，
    刚放完的单元整体排除，没看过只认没有进度记录；第 3 行恰取一次随机数，第 2 行与没有候选时随机流不动。"""
    copy, finished, progress, push, paused, expected = _CASES[case]
    catalog, state = _world(catalogs[copy], progress, push, paused)
    request = ContentRequest(1, "bilibili", "home", "activity-feed", finished)
    for seed in _SEEDS:
        stream, twin = Random(seed), Random(seed)
        chosen = autoplay_content(catalog, state, request, stream)
        wanted = expected[twin.randrange(len(expected))] if isinstance(expected, tuple) else expected
        assert (chosen.content_id if chosen is not None else None) == wanted, seed
        assert stream.getstate() == twin.getstate(), seed
    assert state.agents[1].content_progress.keys() == progress.keys()


def _app_use(seconds: int, *, app: bool):
    """一项进行中的 interact，离计划结束还有给定秒数；app 为真是一次 app 使用，否则是在地点做的事。"""
    activity = running("interact", ActivityKind.INTERACT, "second" if app else "first", presence=not app)
    return activity.model_copy(update={"planned_end_at": 100 + seconds, "planned_duration_seconds": seconds})


@pytest.mark.parametrize("seconds", [1, 60, 179, 180])
def test_an_app_use_is_pausable_however_little_is_left(seconds):
    """一次 app 使用离这一条结束剩 1、60、179、180 秒都可以暂停；在地点做的事只有剩 180 秒那一档可以。"""
    app_use, location = _app_use(seconds, app=True), _app_use(seconds, app=False)
    assert activity_control.pausable(occupied(app_use), app_use) is True
    assert activity_control.pausable(occupied(location), location) is (seconds >= 180)


#: 「收尾的说法」一表：（状态，是不是 app 使用）→（settled 句的终局，收尾结果的 outcome；截断不收尾为 None）。
_ENDINGS = {
    (ActivityStatus.CANCELLED, True): ("结束使用", "结束使用"),
    (ActivityStatus.COMPLETED, True): ("按计划结束", "完成"),
    (ActivityStatus.TRUNCATED, True): ("提前结束", None),
    (ActivityStatus.FAILED, True): ("失败", "失败"),
    (ActivityStatus.CANCELLED, False): ("提前结束", "提前结束"),
    (ActivityStatus.COMPLETED, False): ("按计划结束", "完成"),
    (ActivityStatus.TRUNCATED, False): ("提前结束", None),
    (ActivityStatus.FAILED, False): ("失败", "失败"),
}


@pytest.mark.parametrize("status,app", list(_ENDINGS))
def test_one_predicate_words_the_ending_and_the_outcome(status, app):
    """settled 句的终局与收尾结果的 outcome 逐格照「收尾的说法」一表；app_use_ended 只对被收尾的 app 使用为真。"""
    boundary = {ActivityStatus.COMPLETED: "planned_end", ActivityStatus.CANCELLED: "cancelled"}.get(status, status.value)
    activity = _app_use(900, app=app).model_copy(update={"status": status, "ended_at": 400, "boundary_type": boundary})
    phrase, outcome = _ENDINGS[(status, app)]
    assert activity_control.ending_phrase(activity) == phrase
    if outcome is not None:
        assert conclusion_outcome(activity, "活动") == {"activity": "活动", "outcome": outcome}
    assert app_use_ended(activity) is (status == ActivityStatus.CANCELLED and app)
    assert OUTCOME_APP_USE_ENDED == "结束使用"


def test_progress_snapshot_counts_played_items_only_for_interactions():
    """进度快照：interact 活动不带 played、带条数 played_count，其余与 activity_snapshot 相同；出行活动与它完全相同。"""
    index = TitleIndex(contents={"c-run": "测试慢跑"})
    played = [{"content_id": "c-run", "from_position": 0.0, "to_position": 1200},
              {"content_id": "c-run", "from_position": 0.0, "to_position": 1200}]
    activity = _app_use(900, app=True)
    activity = activity.model_copy(update={"data": {**activity.data, "content_id": "c-run", "played": played}})
    full, counted = activity_snapshot(activity, index), progress_snapshot(activity, index)
    assert "played" not in counted["data"] and counted["data"]["played_count"] == 2
    assert {key: value for key, value in counted["data"].items() if key != "played_count"} == {
        key: value for key, value in full["data"].items() if key != "played"}
    assert {key: value for key, value in counted.items() if key != "data"} == {
        key: value for key, value in full.items() if key != "data"}
    trip = running("travel", ActivityKind.TRAVEL, "first")
    assert progress_snapshot(trip, index) == activity_snapshot(trip, index)
