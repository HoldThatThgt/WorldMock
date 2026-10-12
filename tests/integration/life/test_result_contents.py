"""做完之后读到什么：进度层对一次请求显示最新一条结果；用内容 app 或在地点做事的第二条结果带 contents，按做的先后列出
放过或做过的每一条内容。经真实请求、引擎与 SQLite，读观察里的进度层与落库的结果事件。"""

from __future__ import annotations

import pytest

from tests.integration.start_interfaces.test_location_activities import dinner_environment  # noqa: F401
from tests.life.conftest import (
    SIDE_TOOL, advance_until_ready, conclude_request, do_at, pause_request, progress_calls, purchase, travel, use_app,
)
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import (
    act, events, every_five_minutes, finish_activity, observe_only, one_hour, reach, run_to_end, until_ended,
)
from worldmock.life.readable import duration_phrase
from worldmock.life.result_projection import public_result


def _row(title: str, duration: str, finished: bool) -> dict:
    """contents 的一行：样例与夹具副本里的简介都是「<标题>的测试用合成摘要。」。"""
    return {"title": title, "summary": f"{title}的测试用合成摘要。", "duration": duration, "finished": finished}


def _dinner_record(observed: dict) -> dict:
    """进度层里点名「工作日晚饭」的那条 start_main_activity 记录。"""
    (record,) = [row for row in progress_calls(observed, "start_main_activity")
                 if row["arguments"]["detail"] == "工作日晚饭"]
    return record


def test_purchase_record_shows_the_latest_result(engine_factory):
    """购买进行中，这条记录的 purchases 各行写 price；做完后同一条记录的同几行写 paid，行数不变。"""
    engine = engine_factory()
    _, bought = act(engine, "basket", purchase("薯片", "柠檬茶"))
    assert bought.status == "in_progress", bought.error_code
    (during,) = progress_calls(observe_only(engine, "during"), SIDE_TOOL)
    quoted = during["result"]["details"]["purchases"]
    assert during["result"]["status"] == "in_progress" and quoted
    assert all("price" in row and "paid" not in row for row in quoted)
    until_ended(engine, bought.activity_id)
    (after,) = progress_calls(observe_only(engine, "after"), SIDE_TOOL)
    paid = after["result"]["details"]["purchases"]
    assert after["result"]["status"] == "completed" and after["occurred_at"] == during["occurred_at"]
    assert [row["product"] for row in paid] == [row["product"] for row in quoted]
    assert all("paid" in row and "price" not in row for row in paid)


def test_finished_app_use_lists_what_was_watched(engine_factory):
    """打开 Keep（样例环境里它只登记了《测试慢跑》）：做完后这条记录的 details 恰为一行 contents，四个键与目录一致、
    finished 为真；收尾之后这条记录仍在。"""
    engine = engine_factory()
    _, used = act(engine, "keep", use_app("Keep"))
    assert used.status == "in_progress", used.error_code
    until_ended(engine, used.activity_id)
    (record,) = progress_calls(observe_only(engine, "done"), SIDE_TOOL)
    content = engine.context.catalog.contents["c-run"]
    expected = {"contents": [{"title": content.title, "summary": content.summary,
                              "duration": duration_phrase(content.duration_seconds), "finished": True}]}
    assert record["result"]["status"] == "completed" and record["result"]["details"] == expected
    assert expected["contents"][0] == _row("测试慢跑", "约 20 分钟", True)
    name = engine.context.state.activities[used.activity_id].name
    _, concluded = act(engine, "conclude", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    (kept,) = progress_calls(observe_only(engine, "concluded"), SIDE_TOOL)
    assert kept == record


@pytest.fixture
def drama_only_bilibili(tmp_path):
    """夹具副本：哔哩哔哩只留系列「测试剧集」三集（各 1800 秒），其余只属于它的内容删去，测试长视频只留在抖音。"""
    root = copy_environment(tmp_path / "drama")
    rows = [row for row in catalog_rows(root, "content-catalog.jsonl")
            if row["content_id"] not in {"c-article", "c-course", "c-image"}]
    for row in rows:
        if row["content_id"] == "c-long":
            row["app_ids"] = ["douyin"]
    write_catalog_rows(root, "content-catalog.jsonl", rows)
    return root


def test_finished_app_series_lists_every_episode_in_order(engine_factory, drama_only_bilibili):
    """哔哩哔哩连播「测试剧集」三集做完：contents 三行，次序是集号，duration 是各集全长，都做到了头。"""
    engine = engine_factory(drama_only_bilibili)
    _, used = act(engine, "drama", use_app("哔哩哔哩"))
    assert used.status == "in_progress", used.error_code
    until_ended(engine, used.activity_id)
    done = engine.context.state.activities[used.activity_id]
    assert done.status == "completed" and len(done.data["played"]) == 3
    (record,) = progress_calls(observe_only(engine, "done"), SIDE_TOOL)
    assert record["result"]["details"]["contents"] == [
        _row(f"测试剧集第{number}集", "约 30 分钟", True) for number in ("一", "二", "三")]


def test_ended_activity_lists_finished_items_then_the_unfinished_one(engine_factory, dinner_environment):
    """「工作日晚饭」做到第二集一半时结束：两行，做到头的《备菜》在前、没做到头的《吃饭》在后；第二行的 duration
    是这一集的全长「约 25 分钟」，不是已做的时长。"""
    engine = engine_factory(environment_root=dinner_environment, mutate=every_five_minutes)
    _, dinner = act(engine, "dinner", do_at("家", "工作日晚饭"))
    start = engine.context.state.current_time
    reach(engine, start + 1800)
    name = engine.context.state.activities[dinner.activity_id].name
    _, ended = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    result = _dinner_record(observe_only(engine, "ended"))["result"]
    assert (result["status"], result["message"]) == ("cancelled", "Activity cancelled")
    assert result["details"] == {"contents": [_row("备菜", "约 20 分钟", True), _row("吃饭", "约 25 分钟", False)]}


def _first_episode_long(persona, config):
    """重估间隔恰是第一集《备菜》的时长：人物在第一集做完、第二集刚接上的那一秒得到决策机会。"""
    config["reconsideration_interval_seconds"] = 1200


def test_series_ended_at_an_episode_boundary_lists_only_finished_episodes(engine_factory, dinner_environment):
    """人物恰在第一集做完的那一秒结束「工作日晚饭」：contents 只有《备菜》一行、finished 为真；这件事照样记为做完，
    三集的轮数都是 1、位置都是 0。"""
    engine = engine_factory(environment_root=dinner_environment, mutate=_first_episode_long)
    _, dinner = act(engine, "dinner", do_at("家", "工作日晚饭"))
    start = engine.context.state.current_time
    assert advance_until_ready(engine) and engine.context.state.current_time == start + 1200
    activity = engine.context.state.activities[dinner.activity_id]
    assert activity.data["content_id"] == "d-2-eat" and activity.media_position_seconds == 0
    _, ended = act(engine, "stop", conclude_request(activity.name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    assert _dinner_record(observe_only(engine, "ended"))["result"]["details"] == {
        "contents": [_row("备菜", "约 20 分钟", True)]}
    progress = engine.context.state.agents[1].content_progress
    assert {key: (row.position_seconds, row.completed_rounds) for key, row in progress.items()} == {
        key: (0.0, 1) for key in ("d-1-prep", "d-2-eat", "d-3-wash")}


def test_paused_then_ended_activity_lists_what_was_done_before_the_pause(engine_factory):
    """「测试休息」做 5 分钟后暂停，做完另一件事后再结束它：一行《测试休息》，finished 为假。"""
    engine = engine_factory(mutate=every_five_minutes)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed", paused.error_code
    _, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    finish_activity(engine, chat, "chat")
    _, ended = act(engine, "stop", conclude_request(name), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    (record,) = [row for row in progress_calls(observe_only(engine, "ended"), "start_main_activity")
                 if row["arguments"]["detail"] == "测试休息"]
    assert record["result"]["status"] == "cancelled"
    assert record["result"]["details"] == {"contents": [_row("测试休息", "约 15 分钟", False)]}


def _second_results(engine, action_id: str) -> list:
    """这条动作在活动终结时写下的第二条 action_result 事件。"""
    return [row for row in events(engine, "action_result")
            if row.action_id == action_id and row.data["status"] != "in_progress"]


def test_activity_cut_off_at_run_end_lists_what_was_done(engine_factory):
    """在公司做九小时的「季度销售数据整理」，一小时的运行跑到截止：对落库的第二条 action_result 投影，状态 cancelled、
    Activity truncated at experiment end，contents 列出做到一半的这一条。"""
    engine = engine_factory(mutate=one_hour)
    _, trip = act(engine, "commute", travel("公司", "驾车"))
    finish_activity(engine, trip, "commute")
    _, work = act(engine, "work", do_at("公司", "季度销售数据整理"))
    assert work.status == "in_progress", work.error_code
    run_to_end(engine)
    (terminal,) = _second_results(engine, work.action_id)
    result = public_result(terminal.data, engine.context.inputs)
    assert (result["status"], result["message"]) == ("cancelled", "Activity truncated at experiment end")
    assert result["details"] == {"contents": [_row("季度销售数据整理", "约 9 小时", False)]}


def test_terminal_result_event_carries_no_contents_of_its_own(engine_factory):
    """明细在投影时算：落库的第二条 action_result 里结果的 data 键集仍是 activity 与 orders，没有 contents；对同一事件
    做投影得到 contents。"""
    engine = engine_factory()
    _, used = act(engine, "keep", use_app("Keep"))
    until_ended(engine, used.activity_id)
    (terminal,) = _second_results(engine, used.action_id)
    assert set(terminal.data["data"]) == {"activity", "orders"} and "contents" not in terminal.data
    details = public_result(terminal.data, engine.context.inputs)["details"]
    assert details == {"contents": [_row("测试慢跑", "约 20 分钟", True)]}
