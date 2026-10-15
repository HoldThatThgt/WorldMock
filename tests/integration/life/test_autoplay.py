"""内容 app 自动接续：经真实请求、引擎与 SQLite，一次 app 使用放完一条按接续顺序接着放，随时可以暂停，收尾说「结束使用」，
进度事件只带条数。样例环境从 2026-01-05 周一 08:00 开始，人在家。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.life.conftest import (
    advance_next, conclude_request, context, pause_request, report_request, sleep, stage_day_plan, target_tools, travel,
    use_app,
)
from tests.life.environment_inputs import (
    bilibili_series_rows, copy_environment, office_stretch_environment, second_series_environment, set_content,
)
from tests.life.environment_runtime import act, act_group, events, settle_decision, until_ended
from tests.life.phone_records import label_where
from tests.life.prerequisite_inputs import OFFICE, add_rows, dingtalk_environment, example_window
from worldmock.life.admission import present_location
from worldmock.life.content_select import ContentRequest, select_content
from worldmock.life.randomness import random_stream
from worldmock.life.sessions import current_session

ZONE = ZoneInfo("Asia/Shanghai")


def _at(hour: int, minute: int = 0, second: int = 0) -> int:
    """样例环境运行那一天（2026-01-05）的本地时刻对应的世界秒。"""
    return int(datetime(2026, 1, 5, hour, minute, second, tzinfo=ZONE).timestamp())


def _every(seconds: int):
    """运行配置：只把重估间隔改成给定秒数。"""
    def mutate(persona, config):
        """只改重估间隔。"""
        config["reconsideration_interval_seconds"] = seconds
    return mutate


def _opening(engine, app_id: str, prefix: str, *content_ids: str, suffix: str = "") -> str:
    """按固定种子挑一个开始标签：生产的打开顺序此刻用这个标签的键 activity-action-<标签><suffix>:0 抽到的第一条在
    content_ids 里。单独一条 act 的 suffix 为空；act_group 里排第二的打开 app 一条，动作身份带「-2」。"""
    state = engine.context.state
    stay = present_location(state, 1)

    def accepts(label: str) -> bool:
        """这个标签打开 app 时打开顺序挑中的是不是想要的那一条。"""
        request = ContentRequest(1, app_id, stay, f"activity-action-{label}{suffix}")
        stream = random_stream(state.random_state, f"activity-action-{label}{suffix}:0")
        return select_content(engine.context.catalog, state, request, stream)[0].content_id in content_ids

    return label_where(prefix, accepts)


def _open(engine, app: str, label: str) -> str:
    """打开一个内容 app，断言受理，返回活动身份。"""
    _, opened = act(engine, label, use_app(app))
    assert opened.status == "in_progress", opened.error_code
    return opened.activity_id


def _name(engine, activity_id: str) -> str:
    """活动此刻的可读名称。"""
    return engine.context.state.activities[activity_id].name


def _ready_times(engine, activity_id: str) -> list[int]:
    """推进到这项活动不在进行中：人物每次就绪都只播报（settle_decision），返回就绪的各个时刻，含停下的那一刻。"""
    times = []
    while engine.context.state.activities[activity_id].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            times.append(engine.context.state.current_time)
            settle_decision(engine, f"report-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    if engine.scheduling_state().ready_agent_ids:
        times.append(engine.context.state.current_time)
    return times


def _report_until(engine, moment: int) -> None:
    """推进到给定时刻：其间人物每次就绪都只播报或收尾别的已结束活动；停在那一刻，不替那一刻决策。"""
    while engine.context.state.current_time < moment:
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"report-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    assert engine.context.state.current_time == moment


def _progress(engine, activity_id: str) -> list:
    """这项活动已提交的 activity_progress 事件，按事件先后。"""
    return [row for row in events(engine, "activity_progress") if row.activity_id == activity_id]


def _settled(engine, activity_id: str) -> list[tuple[int, int, str, bool]]:
    """这项活动带 data.content 的进度事件写成（区间开始，区间结束，结算的内容，是否放完）。"""
    return [(row.data["interval_start"], row.data["interval_end"], row.data["content"]["content_id"],
             row.data["content"]["completed"]) for row in _progress(engine, activity_id) if "content" in row.data]


def _feed_to_the_stop(engine) -> str:
    """照规格「算例」：08:00 打开抖音（动作身份 action-douyin），每次就绪都只播报，推进到这次使用停下；返回活动身份。"""
    stage_day_plan(engine, submitted=True)
    activity_id = _open(engine, "抖音", "douyin")
    assert activity_id == "activity-action-douyin"
    assert engine.context.state.activities[activity_id].data["content_id"] == "c-long"
    assert _ready_times(engine, activity_id) == [_at(8, 30), _at(9), _at(9, 7)]
    return activity_id


def test_a_feed_chains_unseen_items_by_keyed_draws_and_stops_when_none_is_left(engine_factory):
    """单条之后随机接一条没看过的：键 activity-action-douyin:1、:2 依次抽到《测试短视频》《测试实况》，条目边界不叫起她；
    09:00 的推送指向的《测试短视频二》被预留，09:07 没有可接的就停下等收尾，收尾结果「完成」。同一秒再打开抖音，
    打开顺序钉住那条推送；放完之后四条都看过，不回放，第二次使用也停下。"""
    engine = engine_factory()
    activity_id = _feed_to_the_stop(engine)
    progress = [row for row in _progress(engine, activity_id) if "content" in row.data and row.data["content"]["completed"]]
    assert [row.data["content"]["content_id"] for row in progress] == ["c-long", "c-short", "c-live"]
    assert [row.data["activity"]["data"]["draw_index"] for row in progress[:2]] == [1, 2]
    assert [row.data["activity"]["data"]["content_id"] for row in progress[:2]] == ["c-short", "c-live"]
    stopped = engine.context.state.activities[activity_id]
    assert (stopped.status, stopped.boundary_type, stopped.ended_at) == ("completed", "planned_end", _at(9, 7))
    assert stopped.data["draw_index"] == 2
    assert [item["content_id"] for item in stopped.data["played"]] == ["c-long", "c-short", "c-live"]
    push = current_session(engine.context.state, 1).app_pushes["douyin"]
    assert (push.content_id, push.status) == ("c-short-2", "pending")
    name = stopped.name
    _, concluded = act(engine, "done", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    assert concluded.data["outcome"] == [{"activity": name, "outcome": "完成"}]

    again = _open(engine, "抖音", "again")
    assert engine.context.state.current_time == _at(9, 7)
    pinned = engine.context.state.activities[again]
    assert pinned.data["content_id"] == "c-short-2" and pinned.data["pinned_by_push_id"] == push.push_id
    until_ended(engine, again)
    ended = engine.context.state.activities[again]
    assert (ended.status, ended.ended_at) == ("completed", _at(9, 8, 30))
    assert [item["content_id"] for item in ended.data["played"]] == ["c-short-2"]


def test_only_pushable_standalone_items_chain_after_a_standalone_item(engine_factory):
    """打开哔哩哔哩先放一条可推送的单条：之后只接没看过、可推送的单条，三条放完停下；不接不可推送的《测试课程》，
    也不接没看过的「测试剧集」第一集。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    activity_id = _open(engine, "哔哩哔哩", _opening(engine, "bilibili", "feed", "c-article", "c-image", "c-long"))
    until_ended(engine, activity_id)
    ended = engine.context.state.activities[activity_id]
    assert (ended.status, ended.ended_at) == ("completed", _at(8, 55))
    assert sorted(item["content_id"] for item in ended.data["played"]) == ["c-article", "c-image", "c-long"]


def test_the_next_episode_comes_first_even_when_it_is_not_pushable(engine_factory, tmp_path):
    """第二集不可推送：第一集放完的那一秒同一项活动照旧接上第二集，draw_index 1，仍在进行。"""
    root = copy_environment(tmp_path / "unpushable-episode")
    set_content(root, "c-drama-2", pushable=False)
    engine = engine_factory(root)
    stage_day_plan(engine, submitted=True)
    activity_id = _open(engine, "哔哩哔哩", _opening(engine, "bilibili", "drama", "c-drama-1"))
    _report_until(engine, _at(8, 30))
    activity = engine.context.state.activities[activity_id]
    assert [item["content_id"] for item in activity.data["played"]] == ["c-drama-1"]
    assert (activity.status, activity.data["content_id"], activity.data["draw_index"]) == ("active", "c-drama-2", 1)


def _open_and_pause(engine, prefix: str, content_id: str) -> str:
    """此刻打开哔哩哔哩（打开顺序挑中 content_id），五分钟后的决策里暂停它；返回活动身份。"""
    activity_id = _open(engine, "哔哩哔哩", _opening(engine, "bilibili", prefix, content_id))
    assert engine.context.state.activities[activity_id].data["content_id"] == content_id
    _report_until(engine, engine.context.state.current_time + 300)
    _, paused = act(engine, f"pause-{prefix}", pause_request(_name(engine, activity_id)), "pause_activity")
    assert paused.status == "completed", paused.error_code
    return activity_id


@pytest.mark.parametrize("held", ["concluded", "still_paused"])
def test_the_last_episode_continues_with_another_started_series(engine_factory, tmp_path, held):
    """「测试番剧」最后一集放完：接这个 app 里开始了没看完的另一部「测试剧集」，从保存的第 300 秒接着放，缓解档位换成
    它的；那部剧还停在一项暂停的使用里时被预留，这次使用停下。"""
    engine = engine_factory(second_series_environment(tmp_path / "two-series"), mutate=_every(300))
    stage_day_plan(engine, submitted=True)
    drama = _open_and_pause(engine, "drama", "c-drama-1")
    assert engine.context.state.current_time == _at(8, 5)
    anime = _open(engine, "哔哩哔哩", _opening(engine, "bilibili", "anime", "c-anime-1"))
    assert engine.context.state.activities[anime].data["relaxation_relief_per_hour"] == 0.5
    if held == "concluded":
        _report_until(engine, _at(8, 10))
        _, concluded = act(engine, "conclude-drama", conclude_request(_name(engine, drama)), "conclude_activity")
        assert concluded.status == "completed", concluded.error_code
    _report_until(engine, _at(8, 25))
    activity = engine.context.state.activities[anime]
    assert [item["content_id"] for item in activity.data["played"]] == ["c-anime-1", "c-anime-2"]
    if held == "concluded":
        assert (activity.status, activity.data["content_id"], activity.data["draw_index"]) == ("active", "c-drama-1", 2)
        assert activity.data["start_position_seconds"] == 300
        assert activity.data["relaxation_relief_per_hour"] == 0.3
    else:
        assert (activity.status, activity.ended_at, activity.data["content_id"]) == ("completed", _at(8, 25), "c-anime-2")


def test_the_most_recently_watched_started_series_comes_first(engine_factory, tmp_path):
    """三部系列：「测试剧集」08:05、「测试纪录片」08:10 各看了五分钟后暂停，再打开看「测试番剧」；08:15 一次收尾两项暂停
    的使用。08:30「测试番剧」放完，接最近看过的「测试纪录片」第一集，从第 300 秒接着放。"""
    root = second_series_environment(tmp_path / "three-series")
    add_rows(root, "content-catalog.jsonl", "content_id", *bilibili_series_rows("c-doc", "测试纪录片", "low"))
    engine = engine_factory(root, mutate=_every(300))
    stage_day_plan(engine, submitted=True)
    drama = _open_and_pause(engine, "drama", "c-drama-1")
    documentary = _open_and_pause(engine, "doc", "c-doc-1")
    assert engine.context.state.current_time == _at(8, 10)
    anime = _open(engine, "哔哩哔哩", _opening(engine, "bilibili", "anime", "c-anime-1"))
    _report_until(engine, _at(8, 15))
    names = (_name(engine, drama), _name(engine, documentary))
    _, concluded = act(engine, "conclude-held", conclude_request(*names), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    _report_until(engine, _at(8, 30))
    activity = engine.context.state.activities[anime]
    assert activity.status == "active" and activity.data["content_id"] == "c-doc-1"
    assert activity.data["start_position_seconds"] == 300 and activity.data["draw_index"] == 2


def _keep_trip(engine, method: str | None) -> tuple[str, str | None]:
    """打开 Keep；method 不为空时同一组按这个出行方式去公司。返回（Keep 的活动身份，出行的活动身份或 None）。"""
    stage_day_plan(engine, submitted=True)
    if method is None:
        return _open(engine, "Keep", "keep"), None
    _, (trip, keep) = act_group(engine, "commute", travel("公司", method), use_app("Keep"))
    assert [trip.status, keep.status] == ["in_progress", "in_progress"], (trip.error_code, keep.error_code)
    return keep.activity_id, trip.activity_id


@pytest.mark.parametrize("case", ["home", "drive", "walk", "arrival_second"])
def test_a_draw_sees_the_stay_of_its_own_second(engine_factory, tmp_path, case):
    """《测试慢跑》放完那一秒的停留决定接不接只在公司合格的《办公室拉伸》：在家、在路上都不接；驾车 08:05:29 已到公司，
    08:20 接上、08:25 停下；恰在到达那一秒放完时，接续看到的仍是路上，不接，那一秒之后人在公司。"""
    root = office_stretch_environment(tmp_path / "stretch")
    if case == "arrival_second":
        probe = engine_factory(office_stretch_environment(tmp_path / "probe"))
        _, trip = _keep_trip(probe, "驾车")
        seconds = probe.context.state.activities[trip].planned_duration_seconds
        set_content(root, "c-run", duration_seconds=seconds)
    engine = engine_factory(root)
    keep, trip = _keep_trip(engine, {"home": None, "walk": "步行"}.get(case, "驾车"))
    until_ended(engine, keep)
    ended = engine.context.state.activities[keep]
    played = [item["content_id"] for item in ended.data["played"]]
    if case == "drive":
        assert engine.context.state.activities[trip].ended_at == _at(8, 5, 29)
        assert (played, ended.ended_at) == (["c-run", "c-office-stretch"], _at(8, 25))
    else:
        assert (played, ended.status) == (["c-run"], "completed")
    if case == "walk":
        assert engine.context.state.activities[trip].planned_end_at == _at(8, 31, 59)
    if case in {"home", "walk"}:
        assert ended.ended_at == _at(8, 20)
        assert present_location(engine.context.state, 1) == ("home" if case == "home" else None)
    if case == "arrival_second":
        assert ended.ended_at == engine.context.state.activities[trip].ended_at == engine.context.state.current_time
        assert present_location(engine.context.state, 1) == "office"


#: 只在家合格、可推送的 Keep 内容《居家拉伸》：路上的停留为空，它不合格；开始地点家不替路上作停留。
_HOME_STRETCH = {
    "app_ids": ["keep"], "content_id": "c-home-stretch", "duration_seconds": 300, "exertion": "light",
    "prerequisite": {"location_kinds": ["home"]}, "pushable": True, "relaxation_relief": "medium",
    "social_relief": "none", "summary": "居家拉伸的测试用合成摘要。",
    "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": "居家拉伸"}


@pytest.mark.parametrize("case", ["home", "walk"])
def test_on_the_road_content_tied_to_the_start_location_is_not_drawn(engine_factory, tmp_path, case):
    """《办公室拉伸》副本再加只在家合格的《居家拉伸》，打开 Keep 时打开顺序放《测试慢跑》。在家：08:20 放完接上《居家拉伸》，
    08:25 停下。步行去公司时带上 Keep：08:20 放完那一秒人在路上，停留为空，开始地点家不算，《居家拉伸》不合格，不接，
    这次使用停下，人仍在路上。"""
    root = office_stretch_environment(tmp_path / "stretch")
    add_rows(root, "content-catalog.jsonl", "content_id", _HOME_STRETCH)
    engine = engine_factory(root)
    stage_day_plan(engine, submitted=True)
    if case == "home":
        keep = _open(engine, "Keep", _opening(engine, "keep", "keep", "c-run"))
    else:
        label = _opening(engine, "keep", "commute", "c-run", suffix="-2")
        _, (trip, opened) = act_group(engine, label, travel("公司", "步行"), use_app("Keep"))
        assert [trip.status, opened.status] == ["in_progress", "in_progress"], (trip.error_code, opened.error_code)
        keep = opened.activity_id
    assert engine.context.state.activities[keep].data["content_id"] == "c-run"
    until_ended(engine, keep)
    ended = engine.context.state.activities[keep]
    played = [item["content_id"] for item in ended.data["played"]]
    if case == "home":
        assert (played, ended.status, ended.ended_at) == (["c-run", "c-home-stretch"], "completed", _at(8, 25))
        assert present_location(engine.context.state, 1) == "home"
    else:
        assert (played, ended.status, ended.ended_at) == (["c-run"], "completed", _at(8, 20))
        assert engine.context.state.activities[trip.activity_id].status == "active"
        assert present_location(engine.context.state, 1) is None


def test_a_chained_item_continues_the_same_activity(engine_factory):
    """08:40《测试长视频》放完、接上《测试短视频》：同一项活动、同一槽位，计划结束是这一秒加这一条的余量，计划时长是已做
    时长加余量，推送字段为空，受理事件带过去，08:30 的播报锚点不动；两条各有自己结算的进度事件。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    activity_id = _open(engine, "抖音", "douyin")
    accepted = engine.context.state.activities[activity_id].data["accepted_event_id"]
    _report_until(engine, _at(8, 40))
    activity = engine.context.state.activities[activity_id]
    assert activity.resource_claims == {"second"} and activity.data["content_id"] == "c-short"
    assert (activity.status, activity.ended_at, activity.boundary_type) == ("active", None, None)
    assert (activity.planned_end_at, activity.planned_duration_seconds) == (_at(8, 42), 2520)
    assert activity.media_position_seconds == 0 and activity.data["pinned_by_push_id"] is None
    assert activity.data["accepted_event_id"] == accepted and activity.report_anchor_at == _at(8, 30)
    _report_until(engine, _at(8, 42))
    assert _settled(engine, activity_id)[-2:] == [(_at(8, 30), _at(8, 40), "c-long", True),
                                                  (_at(8, 40), _at(8, 42), "c-short", True)]


@pytest.mark.parametrize("case", ["home", "road"])
def test_an_app_use_can_be_paused_at_any_time(engine_factory, tmp_path, case):
    """一次 app 使用随时可以暂停：60 秒的《测试慢跑》放到第 20 秒，工具层可以暂停，去睡觉、再开一个 app 的下一步都写
    结束或暂停，暂停成立，同一秒恢复从第 20 秒接着放；在路上也能暂停，只是恢复要回开始地点家。"""
    if case == "road":
        engine = engine_factory(mutate=_every(300))
        keep, _ = _keep_trip(engine, "步行")
    else:
        root = copy_environment(tmp_path / "minute-run")
        set_content(root, "c-run", duration_seconds=60)
        engine = engine_factory(root, mutate=_every(20))
        keep, _ = _keep_trip(engine, None)
    start = engine.context.state.current_time
    _report_until(engine, start + (300 if case == "road" else 20))
    name = _name(engine, keep)
    if case == "home":
        _sleep_and_feed_ask_to_conclude_or_pause(engine, name)
    observed, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert target_tools(observed, name)["pause_activity"] == {"tool": "pause_activity", "available": True,
                                                              "reason": None}
    assert paused.status == "completed", paused.error_code
    assert engine.context.state.activities[keep].status == "paused"
    if case == "road":
        assert not engine.scheduling_state().ready_agent_ids
        _report_until(engine, start + 600)
        _, refused = act(engine, "resume", report_request(name), "report_status")
        assert refused.status == "rejected" and refused.error_code == "ACTIVITY_PRECONDITION_UNMET"
        assert refused.message == f"Resume {name} at its start location 家; it can be concluded elsewhere."
        return
    seen, resumed = act(engine, "resume", report_request(name), "report_status")
    assert [row["name"] for row in seen["todo_layer"]["paused_activities"]] == [name]
    assert resumed.status == "completed", resumed.error_code
    assert engine.context.state.activities[keep].data["start_position_seconds"] == 20
    until_ended(engine, keep)
    assert engine.context.state.activities[keep].ended_at == start + 60


def _sleep_and_feed_ask_to_conclude_or_pause(engine, name: str) -> None:
    """槽位 2 被 app 使用占着：去睡觉与再打开抖音都被拒，下一步都写结束或暂停；拒绝不改变这项使用。"""
    _, slept = act(engine, "sleepy", sleep(30))
    assert slept.status == "rejected" and slept.data["next_step"] == "Conclude or pause the second activity first."
    _, feed = act(engine, "feed", use_app("抖音"))
    assert feed.status == "rejected" and feed.error_code == "RESOURCE_CONFLICT"
    assert feed.data["next_step"] == f"Conclude or pause {name} first."


def _concluded_in(engine, case: str) -> tuple[str, object]:
    """running：打开 Keep，五分钟后的决策里收尾；paused：五分钟后先暂停，没有进行中的活动、同一秒再收尾；clock：人在
    办公室的打卡时段打开钉钉，第 20 秒收尾《考勤打卡》。返回（活动身份，收尾结果）。"""
    stage_day_plan(engine, submitted=True)
    activity_id = _open(engine, "钉钉" if case == "clock" else "Keep", "used")
    _report_until(engine, engine.context.state.current_time + (20 if case == "clock" else 300))
    name = _name(engine, activity_id)
    if case == "paused":
        _, paused = act(engine, "pause", pause_request(name), "pause_activity")
        assert paused.status == "completed" and engine.scheduling_state().ready_agent_ids == (1,)
    _, concluded = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    return activity_id, concluded


@pytest.mark.parametrize("case", ["running", "paused", "clock"])
def test_a_concluded_app_use_reads_end_of_use(engine_factory, tmp_path, case):
    """被收尾的一次 app 使用（进行中或暂停中）：收尾结果的 outcome 与记忆层的结束句都是「结束使用」，机器状态仍是
    cancelled、agent_cancelled，开始请求的第二条结果仍是 cancelled；只有一条、不会接续的打卡也一样。"""
    if case == "clock":
        engine = engine_factory(dingtalk_environment(tmp_path / "clock"),
                                mutate=example_window(days=1, at_office=True, reconsideration_interval_seconds=20))
    else:
        engine = engine_factory(mutate=_every(300))
    activity_id, concluded = _concluded_in(engine, case)
    ended = engine.context.state.activities[activity_id]
    assert concluded.data["outcome"] == [{"activity": ended.name, "outcome": "结束使用"}]
    assert (ended.status, ended.boundary_type, ended.interruption_reason) == (
        "cancelled", "cancelled", "agent_cancelled")
    results = [row for row in events(engine, "action_result") if row.action_id == "action-used"]
    assert [(row.data["status"], row.data["message"]) for row in results][1] == ("cancelled", "Activity cancelled")
    if case == "clock":
        assert ended.data["content_id"] == "dingtalk-clock" and ended.location_id == "office"
        return
    _, trip = act(engine, "leave", travel("公司", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    memory = engine.observe(context(engine, "road"))["memory_layer"]
    (sentence,) = [line for entry in memory["earlier_today"] for line in entry["settled"]
                   if line.startswith(ended.name)]
    assert sentence.endswith("，结束使用，（《测试慢跑》未完）")


def test_progress_events_carry_the_played_count_and_other_snapshots_the_list(engine_factory):
    """照「算例」跑到 09:07 并收尾：这项活动的每条进度事件的快照不带 played、只带条数；受理结果、09:07 的终态结果与
    收尾的 activity_concluded 带完整的 played。"""
    engine = engine_factory()
    activity_id = _feed_to_the_stop(engine)
    _, concluded = act(engine, "done", conclude_request(_name(engine, activity_id)), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    snapshots = [row.data["activity"]["data"] for row in _progress(engine, activity_id)]
    assert not [data for data in snapshots if "played" in data]
    assert [data["played_count"] for data in snapshots] == [0, 0, 1, 2, 2, 2, 3]
    played = engine.context.state.activities[activity_id].data["played"]
    assert len(played) == 3
    (accepted,) = [row for row in events(engine, "action_accepted") if row.action_id == "action-douyin"]
    assert accepted.data["activity"]["data"]["played"] == []
    results = [row for row in events(engine, "action_result") if row.action_id == "action-douyin"]
    assert [row.data["status"] for row in results] == ["in_progress", "completed"]
    assert results[1].occurred_at == _at(9, 7) and results[1].data["activity"]["data"]["played"] == played
    (lifecycle,) = [row for row in events(engine, "environment_event")
                    if row.data.get("kind") == "activity_concluded" and row.activity_id == activity_id]
    assert lifecycle.data["activity"]["data"]["played"] == played
