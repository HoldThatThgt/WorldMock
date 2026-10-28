"""自动接续的显示与记录：经真实请求、引擎与 SQLite，内容 app 行按条写时长，一次 app 使用的活动行写「已用…，放到第 K 条」，
时间线与结束句的标题只列三条，结果明细只列三行，导出里一次使用一行 app_use、每条各有消费与经历。

「五集副本」是 tests/life/environment_inputs.py 的 five_episode_environment：哔哩哔哩只有「测试剧集」五集、各 400 秒。
08:00 在家打开它，一集接一集，第一次决策机会是 08:30 的半小时播报，那时放完四集、第五集放到第 200 秒；只播报不收尾时
08:33:20 第五集放完，没有能接的内容，使用停下待收尾。样例环境从 2026-01-05 周一 08:00 开始。
"""

from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scripts import inspect_run, render_day
from tests.footprints.recording import artifact_rows, export_completed
from tests.life.conftest import (
    SIDE_TOOL,
    action_identity,
    activity_name,
    advance_until_ready,
    conclude_request,
    do_at,
    pause_request,
    progress_calls,
    report_request,
    target_actions,
    tool_targets,
    travel,
    use_app,
)
from tests.life.environment_inputs import catalog_rows, five_episode_environment, meal_environment, write_catalog_rows
from tests.life.environment_runtime import (
    act,
    conclude_app_use,
    events,
    every_five_minutes,
    observe_only,
    one_hour,
    one_hour_every_five_minutes,
    until_ended,
)
from tests.life.prerequisite_inputs import app_content, dingtalk_environment
from worldmock.recording_engine.projections.presentation.timeline_labels import label_findings
from worldmock.state.interaction.requests import RecallTaskSummaryRequest
from worldmock.agents.prompt.prompt_schemas import validate_layer

ZONE = ZoneInfo("Asia/Shanghai")
#: 五集副本里那次使用的活动名。
WATCHING = "进行哔哩哔哩（1月5日 08:00 起）"
#: 五集副本 08:30 时活动行与回看写的进度。
AT_HALF_PAST = "已用约 30 分钟，放到第 5 条"


def _at(hour: int, minute: int = 0, second: int = 0) -> int:
    """样例环境运行那一天（2026-01-05）的本地时刻对应的世界秒。"""
    return int(datetime(2026, 1, 5, hour, minute, second, tzinfo=ZONE).timestamp())


def _episode(number: int) -> str:
    """五集副本里第 number 集的标题。"""
    return f"测试剧集第{'一二三四五'[number - 1]}集"


def _watch(engine_factory, tmp_path, mutate=None):
    """五集副本建引擎（可带运行配置），08:00 打开哔哩哔哩；返回（引擎，打开时的观察，活动身份）。"""
    engine = engine_factory(five_episode_environment(tmp_path / "five"), mutate=mutate)
    observed, opened = act(engine, "watch", use_app("哔哩哔哩"))
    assert opened.status == "in_progress", opened.error_code
    assert engine.context.state.activities[opened.activity_id].data["content_id"] == "c-drama-1"
    return engine, observed, opened.activity_id


def _half_past(engine, activity_id: str) -> None:
    """推进到 08:30 的半小时播报，停在这次决策之前：四集放完，第五集放到第 200 秒。"""
    assert advance_until_ready(engine) and engine.context.state.current_time == _at(8, 30)
    activity = engine.context.state.activities[activity_id]
    assert [item["content_id"] for item in activity.data["played"]] == [f"c-drama-{number}" for number in range(1, 5)]
    assert activity.data["content_id"] == "c-drama-5"
    assert engine.context.state.agents[1].content_progress["c-drama-5"].position_seconds == 200


def _end(engine, activity_id: str, how: str) -> None:
    """按 how 结束这次使用：concluded 是 08:30 收尾；finished 是 08:30 只播报，08:33:20 第五集放完、使用停下之后收尾。"""
    if how == "finished":
        _half_past(engine, activity_id)
        act(engine, "report", report_request(WATCHING), "report_status")
        until_ended(engine, activity_id)
        assert engine.context.state.current_time == _at(8, 33, 20)
        assert engine.context.state.activities[activity_id].status == "completed"
    ended = conclude_app_use(engine, activity_id, "watch", items=4)
    assert ended.status == "completed", ended.error_code
    assert engine.context.state.current_time == (_at(8, 30) if how == "concluded" else _at(8, 33, 20))


def _activity_row(observed: dict, name: str) -> tuple[str, str]:
    """工具层里这项活动一行的（阶段，客观进度）。"""
    row = tool_targets(observed)[name]
    assert row["kind"] == "activity"
    return row["phase"], row["state"]


def test_content_app_rows_label_the_length_per_item(engine_factory):
    """样例环境在家 08:00：哔哩哔哩、抖音、Keep 三行的时长依次是「每条约 5 分钟–约 40 分钟」「每条约 2 分钟–约 40 分钟」
    「每条约 20 分钟」，都没有推送说明。"""
    engine = engine_factory()
    observed = observe_only(engine, "home")
    rows = {name: target_actions(observed, name)["interact"] for name in ("哔哩哔哩", "抖音", "Keep")}
    assert {name: (row["duration"], row["note"]) for name, row in rows.items()} == {
        "哔哩哔哩": ("每条约 5 分钟–约 40 分钟", None), "抖音": ("每条约 2 分钟–约 40 分钟", None),
        "Keep": ("每条约 20 分钟", None)}


def test_content_app_row_names_the_first_item_when_the_opening_knows_it(engine_factory, tmp_path):
    """五集副本 08:00 哔哩哔哩一行「每条约 7 分钟」；打开，08:30 收尾之后同一秒的观察里这一行写「先放的一条约 3 分钟」，
    再打开开始的正是那一条：《测试剧集第五集》从第 200 秒放起，计划时长 200 秒。"""
    engine, observed, activity_id = _watch(engine_factory, tmp_path)
    assert target_actions(observed, "哔哩哔哩")["interact"]["duration"] == "每条约 7 分钟"
    _end(engine, activity_id, "concluded")
    observed, again = act(engine, "again", use_app("哔哩哔哩"))
    row = target_actions(observed, "哔哩哔哩")["interact"]
    assert (row["available"], row["duration"], row["note"]) == (True, "先放的一条约 3 分钟", None)
    assert again.status == "in_progress", again.error_code
    reopened = engine.context.state.activities[again.activity_id]
    assert reopened.data["content_id"] == "c-drama-5" and reopened.data["start_position_seconds"] == 200
    assert reopened.planned_duration_seconds == 200


def test_content_app_row_skips_the_item_held_by_a_paused_use(engine_factory):
    """样例环境每五分钟重估，打开抖音、08:05 暂停：停在半截的那一条被暂停的使用预留着，此刻打开不会先放它，
    抖音一行仍写「每条约 2 分钟–约 40 分钟」，不写「先放的一条」。"""
    engine = engine_factory(mutate=one_hour_every_five_minutes)
    _, opened = act(engine, "clips", use_app("抖音"))
    assert opened.status == "in_progress", opened.error_code
    assert advance_until_ready(engine) and engine.context.state.current_time == _at(8, 5)
    _, paused = act(engine, "pause", pause_request(activity_name(engine, opened.activity_id)), "pause_activity")
    assert paused.status == "completed", paused.error_code
    held = engine.context.state.activities[opened.activity_id].data["content_id"]
    assert engine.context.state.agents[1].content_progress[held].position_seconds > 0
    observed = observe_only(engine, "paused")
    assert target_actions(observed, "抖音")["interact"]["duration"] == "每条约 2 分钟–约 40 分钟"


def test_content_app_row_compares_lengths_in_seconds(engine_factory, tmp_path):
    """钉钉副本只有 100 秒与 110 秒两条（短语都是「约 2 分钟」）：按秒比较不等，仍写成范围「每条约 2 分钟–约 2 分钟」。"""
    root = dingtalk_environment(tmp_path / "pair", app_content("dingtalk-a", "甲", duration_seconds=100),
                                app_content("dingtalk-b", "乙", duration_seconds=110))
    engine = engine_factory(root)
    assert target_actions(observe_only(engine, "home"), "钉钉")["interact"]["duration"] == "每条约 2 分钟–约 2 分钟"


def test_running_app_use_row_says_time_used_and_the_item_playing(engine_factory, tmp_path):
    """五集副本 08:30：工具层活动行进行中、「已用约 30 分钟，放到第 5 条」；在同一份冻结观察上回看这项活动，客观进度
    是同一句；进度层里开始请求的记录仍是第一条结果，计划「约 7 分钟」，内容是《测试剧集第一集》。"""
    engine, _, activity_id = _watch(engine_factory, tmp_path)
    _half_past(engine, activity_id)
    observed = observe_only(engine, "half-past")
    assert activity_name(engine, activity_id) == WATCHING
    assert _activity_row(observed, WATCHING) == ("进行中", AT_HALF_PAST)
    recalled = engine.read_task_summary(1, RecallTaskSummaryRequest(task_name=WATCHING), observed)["recall"]
    assert (recalled["kind"], recalled["phase"], recalled["objective_progress"]) == ("activity", "进行中", AT_HALF_PAST)
    [record] = progress_calls(observed, SIDE_TOOL)
    result = record["result"]
    assert result["status"] == "in_progress"
    assert result["details"]["planned"] == [{"name": WATCHING, "duration": "约 7 分钟"}]
    assert result["details"]["content"]["title"] == "测试剧集第一集"


def test_paused_app_use_in_the_todo_layer_says_time_used_and_the_item_playing(engine_factory, tmp_path):
    """五集副本 08:30 暂停这次使用：同一秒的待办层里这一行的进度是「已用约 30 分钟，放到第 5 条」。"""
    engine, _, activity_id = _watch(engine_factory, tmp_path)
    _half_past(engine, activity_id)
    _, paused = act(engine, "pause", pause_request(WATCHING), "pause_activity")
    assert paused.status == "completed", paused.error_code
    observed = observe_only(engine, "paused")
    [row] = [item for item in observed["todo_layer"]["paused_activities"] if item["name"] == WATCHING]
    assert row["progress"] == AT_HALF_PAST


def test_finished_app_use_row_counts_only_the_items_played(engine_factory, tmp_path):
    """五集副本 08:30 只播报；08:33:20 第五集放完、没有能接的内容：活动行刚结束，「已用约 33 分钟，放到第 5 条」。"""
    engine, _, activity_id = _watch(engine_factory, tmp_path)
    _half_past(engine, activity_id)
    act(engine, "report", report_request(WATCHING), "report_status")
    until_ended(engine, activity_id)
    assert engine.context.state.current_time == _at(8, 33, 20)
    stopped = engine.context.state.activities[activity_id]
    assert stopped.status == "completed" and len(stopped.data["played"]) == 5
    assert _activity_row(observe_only(engine, "stopped"), WATCHING) == ("刚结束", "已用约 33 分钟，放到第 5 条")


def test_location_unit_row_keeps_time_done_and_time_left(engine_factory):
    """样例环境每五分钟重估，在家做「测试休息」到 08:05：活动行仍写「已做约 5 分钟，还剩约 10 分钟」。"""
    engine = engine_factory(mutate=every_five_minutes)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    assert advance_until_ready(engine) and engine.context.state.current_time == _at(8, 5)
    observed = observe_only(engine, "resting")
    assert _activity_row(observed, activity_name(engine, rest.activity_id)) == ("进行中", "已做约 5 分钟，还剩约 10 分钟")


def test_timeline_row_of_a_long_app_use_lists_three_titles_and_the_count(engine_factory, tmp_path, capsys):
    """五集副本 08:30 收尾后经命令行入口写 2026-01-05 的时间线：这次使用一行，前三集加「等 5 条」，标签检查零发现；
    inspect_run 对这项活动最后一条进度事件仍只写那一条内容《测试剧集第五集》。"""
    engine, _, activity_id = _watch(engine_factory, tmp_path)
    _end(engine, activity_id, "concluded")
    run_dir = engine.context.store.run_dir
    render_day.main([str(run_dir / "footprints.sqlite"), "--agent", "1", "--date", "2026-01-05",
                     "--out", str(tmp_path / "out")])
    lines = (tmp_path / "out" / "timeline" / "1-2026-01-05.md").read_text(encoding="utf-8").splitlines()
    [line] = [line for line in lines if "使用哔哩哔哩" in line]
    assert line == ("| 08:00–08:30 | 使用哔哩哔哩《测试剧集第一集》《测试剧集第二集》《测试剧集第三集》等 5 条 "
                    "| 30 分钟 | 5 |")
    assert label_findings(line.strip("| ").split(" | ")[1]) == []
    last = [row for row in events(engine, "activity_progress") if row.activity_id == activity_id][-1]
    with inspect_run._connect(run_dir) as conn:
        inspect_run.cmd_events(conn, argparse.Namespace(run_dir=run_dir, type="activity_progress", agent=1, tail=0,
                                                        full=False))
    [brief] = [text for text in capsys.readouterr().out.splitlines() if text.startswith(f"[{last.event_seq:>5}]")]
    assert brief.endswith(" 使用哔哩哔哩《测试剧集第五集》 → active")


@pytest.mark.parametrize("how,sentence", [
    ("concluded", f"{WATCHING}：08:00–08:30，结束使用，《测试剧集第一集》《测试剧集第二集》《测试剧集第三集》等 4 条"
                  "（《测试剧集第五集》未完）"),
    ("finished", f"{WATCHING}：08:00–08:33，按计划结束，《测试剧集第一集》《测试剧集第二集》《测试剧集第三集》等 5 条"),
], ids=["concluded", "finished"])
def test_settled_sentence_lists_three_titles_the_count_and_the_unfinished_item(engine_factory, tmp_path, how,
                                                                                sentence):
    """五集副本收尾之后驾车去五里公园：记忆层 earlier_today 里这次使用的结束句只列前三集，「等 K 条」数的是放完的条数，
    08:30 收尾的那一句后面紧接「（《测试剧集第五集》未完）」，自己停下后收尾的那一句没有这半句。"""
    engine, _, activity_id = _watch(engine_factory, tmp_path)
    _end(engine, activity_id, how)
    _, trip = act(engine, "leave", travel("五里公园", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    memory = observe_only(engine, "road")["memory_layer"]
    found = [line for entry in memory["earlier_today"] for line in entry["settled"] if line.startswith(WATCHING)]
    assert found == [sentence]


def test_settled_sentence_of_a_place_series_over_three_episodes_lists_three_and_the_count(engine_factory, tmp_path):
    """在地点做的系列同样截断：家的「做晚饭」加第四集《倒垃圾》，做完四集后收尾、驾车出门；记忆层里这一段的结束句以
    「按计划结束，《备菜做饭》《吃晚饭》《洗碗收拾厨房》等 4 条」结尾，不列第四集，没有「未完」半句。"""
    root = meal_environment(tmp_path / "meal")
    rows = catalog_rows(root, "content-catalog.jsonl")
    rows.append({"content_id": "dinner-4-trash", "duration_seconds": 600, "exertion": "light",
                 "location_kinds": ["home"], "pushable": False, "relaxation_relief": "none",
                 "series": {"episode": 4, "title": "做晚饭"}, "social_relief": "none",
                 "summary": "倒垃圾的测试用合成摘要。",
                 "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": "倒垃圾"})
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))
    engine = engine_factory(root)
    _, dinner = act(engine, "dinner", do_at("家", "做晚饭"))
    assert dinner.status == "in_progress", dinner.error_code
    until_ended(engine, dinner.activity_id)
    assert len(engine.context.state.activities[dinner.activity_id].data["played"]) == 4
    name = activity_name(engine, dinner.activity_id)
    _, ended = act(engine, "end", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    _, trip = act(engine, "leave", travel("五里公园", "驾车"))
    assert trip.status == "in_progress", trip.error_code
    memory = observe_only(engine, "road")["memory_layer"]
    found = [line for entry in memory["earlier_today"] for line in entry["settled"] if line.startswith(name)]
    assert len(found) == 1 and found[0].endswith("，按计划结束，《备菜做饭》《吃晚饭》《洗碗收拾厨房》等 4 条"), found


@pytest.mark.parametrize("how,played", [("concluded", 4), ("finished", 5)], ids=["concluded", "finished"])
def test_ended_app_use_lists_three_contents_and_says_how_many_are_left_out(engine_factory, tmp_path, how, played):
    """五集副本收尾之后，进度层里开始请求的记录是第二条结果：恰为第一至三集三行（放完、「约 7 分钟」）加「还有 2 条没列」，
    整份进度层通过层校验；落库的终态结果事件里活动快照的 played 照旧是完整的四项或五项。"""
    engine, _, activity_id = _watch(engine_factory, tmp_path)
    _end(engine, activity_id, how)
    observed = observe_only(engine, "ended")
    [record] = progress_calls(observed, SIDE_TOOL)
    rows = [{"title": _episode(number), "summary": f"{_episode(number)}的测试用合成摘要。", "duration": "约 7 分钟",
             "finished": True} for number in (1, 2, 3)]
    assert record["result"]["details"] == {"contents": rows, "contents_omitted": "还有 2 条没列"}
    validate_layer(observed["progress_layer"])
    [terminal] = [row for row in events(engine, "action_result")
                  if row.action_id == action_identity("watch") and row.data["status"] != "in_progress"]
    assert terminal.data["status"] == ("cancelled" if how == "concluded" else "completed")
    assert [item["content_id"] for item in terminal.data["activity"]["data"]["played"]] == [
        f"c-drama-{number}" for number in range(1, played + 1)]


def _use_rows(directory, activity_id: str) -> tuple[list[dict], list[dict], list[dict]]:
    """导出里这项活动的 app_use 行、content_consumed 行与 played 经历行。"""
    usage = [row for row in artifact_rows(directory, "app_usage") if row["activity_id"] == activity_id]
    experiences = [row for row in artifact_rows(directory, "verified_experiences")
                   if row["kind"] == "played" and row["activity_id"] == activity_id]
    return ([row for row in usage if row["fact_kind"] == "app_use"],
            [row for row in usage if row["fact_kind"] == "content_consumed"], experiences)


def _paused_and_resumed(engine, activity_id: str) -> None:
    """08:30 暂停，在家做「测试休息」到 08:45，收尾它、恢复这次使用；08:48:20 第五集放完，收尾。"""
    _half_past(engine, activity_id)
    act(engine, "pause", pause_request(WATCHING), "pause_activity")
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    assert advance_until_ready(engine) and engine.context.state.current_time == _at(8, 45)
    act(engine, "rest-done", conclude_request(activity_name(engine, rest.activity_id)), "conclude_activity")
    _, resumed = act(engine, "resume", report_request(WATCHING), "report_status")
    assert resumed.status == "completed", resumed.error_code
    until_ended(engine, activity_id)
    assert engine.context.state.current_time == _at(8, 48, 20)
    conclude_app_use(engine, activity_id, "watch", items=5)


#: 五集副本各集按次序首尾相接的区间（08:00 起每集 400 秒），第五集只到 08:30。
_EPISODES = [(f"c-drama-{number}", _at(8) + 400 * (number - 1), min(_at(8) + 400 * number, _at(8, 30)))
             for number in range(1, 6)]


def _five_episode_records(engine_factory, tmp_path, how: str) -> None:
    """五集副本运行一小时：08:30 收尾，或暂停再恢复、放完后收尾；导出并认证，核对这次使用的三种记录。"""
    engine, _, activity_id = _watch(engine_factory, tmp_path, one_hour)
    if how == "concluded":
        _end(engine, activity_id, "concluded")
    else:
        _paused_and_resumed(engine, activity_id)
    export_completed(engine, tmp_path / "export")
    uses, consumed, played = _use_rows(tmp_path / "export", activity_id)
    expected = [(key, start, end, end - start == 400) for key, start, end in _EPISODES]
    segments = [{"started_at": _at(8), "ended_at": _at(8, 30)}]
    if how == "paused-and-resumed":
        expected.append(("c-drama-5", _at(8, 45), _at(8, 48, 20), True))
        segments.append({"started_at": _at(8, 45), "ended_at": _at(8, 48, 20)})
    assert [(row["status"], row["segments"]) for row in uses] == [
        ("completed" if how == "paused-and-resumed" else "cancelled", segments)]
    assert [(row["content_id"], row["interval_start"], row["interval_end"], row["completed"])
            for row in consumed] == expected
    assert [(row["content_id"], row["interval_start"], row["interval_end"]) for row in played] == [
        row[:3] for row in expected]


def _drawn_feed_records(engine_factory, tmp_path) -> None:
    """样例环境运行一小时、每五分钟重估，标签 clips 打开抖音（第一条《测试短视频》），放完两条以上后收尾；导出并认证，
    核对这次使用的三种记录与这项活动的进度事件逐一对应。"""
    engine = engine_factory(mutate=one_hour_every_five_minutes)
    _, opened = act(engine, "clips", use_app("抖音"))
    assert opened.status == "in_progress", opened.error_code
    assert engine.context.state.activities[opened.activity_id].data["content_id"] == "c-short"
    ended = conclude_app_use(engine, opened.activity_id, "clips", items=2)
    assert ended.status == "completed", ended.error_code
    stopped = engine.context.state.activities[opened.activity_id]
    assert stopped.status == "cancelled"
    export_completed(engine, tmp_path / "export")
    uses, consumed, played = _use_rows(tmp_path / "export", opened.activity_id)
    progress = [row.data for row in events(engine, "activity_progress")
                if row.activity_id == opened.activity_id and "content" in row.data]
    assert [(row["status"], row["segments"]) for row in uses] == [
        ("cancelled", [{"started_at": stopped.started_at, "ended_at": stopped.ended_at}])]
    assert [(row["content_id"], row["interval_start"], row["interval_end"], row["completed"]) for row in consumed] == [
        (data["content"]["content_id"], data["interval_start"], data["interval_end"], data["content"]["completed"])
        for data in progress]
    assert sum(row["completed"] for row in consumed) >= 2
    assert {row["content_id"] for row in consumed} <= set(engine.context.catalog.contents_by_app["douyin"])
    assert [(row["content_id"], row["interval_start"], row["interval_end"]) for row in played] == [
        (data["content"]["content_id"], data["interval_start"], data["interval_end"]) for data in progress
        if data["content"]["position_after_seconds"] > data["content"]["position_before_seconds"]]


@pytest.mark.parametrize("how", ["paused-and-resumed", "concluded", "drawn-feed"])
def test_one_app_use_row_spans_every_item_and_splits_only_at_a_pause(engine_factory, tmp_path, how):
    """导出里一次 app 使用恰一行 app_use：条与条之间首尾相接并成一段，只有暂停分段；每条带内容的进度事件一行
    content_consumed、一条 played 经历，区间逐行相同。五集副本暂停再恢复、放完后收尾的是 completed、两段；08:30 收尾的
    是 cancelled、一段；样例环境里由接续顺序抽着接上的抖音各条同样并成一行、各有一行消费与经历，收尾后是 cancelled。"""
    if how == "drawn-feed":
        _drawn_feed_records(engine_factory, tmp_path)
    else:
        _five_episode_records(engine_factory, tmp_path, how)
