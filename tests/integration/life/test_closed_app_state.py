"""关着的内容 app 在工具层目标的 state 写开放时间，前提拒绝的分句写之后七天里的下次开放
（docs/design/04-simulation-engine.md「出行、内容与查询」，06-agent.md「提示与工具」）。

都经真实请求、引擎与 SQLite：钉钉例子的夹具副本，用 stage_moment 摆到给定本地时刻与地点；工具层钉钉一行的原因与提交后
拒绝的原因一字不差，拒绝不改世界。目标的 state 不经准入次序，所以原因是槽位或先收尾的说明时它照样在。
2026 年 10 月 12 日是周一，17 日是周六。
"""

from __future__ import annotations

import pytest

from tests.integration.life.test_content_start_evidence import CLOCK_REFUSALS, dingtalk_engine, dingtalk_row
from tests.life.conftest import use_app
from tests.life.current_world import stage_moment
from tests.life.environment_runtime import act, events, observe_only, reach
from tests.life.prerequisite_inputs import CLOCK, CLOCK_HOURS, OFFICE, WORKDAYS, app_content, moment, weekday_hours
from worldmock.agents.prompt.prompt_schemas import validate_layer
from worldmock.agents.prompt.tool_groups import compact_tool_layer, expand_tool_layer

HOURS = "08:00–10:00, 17:00–20:00"
TODAY = f"available {HOURS} today"
NEXT_MONDAY = f"not available today (next available Mon 10-19 {HOURS})"
NEXT_MONDAY_NOON = "not available today (next available Mon 10-19 12:00–13:00)"
NEVER = "not available today or in the next seven days"
GO_TO_OFFICE = f"Go to {OFFICE}."
AT_OFFICE = f"Nothing in 钉钉 can start here now: it requires presence at {OFFICE}"
LUNCH = app_content("dingtalk-lunch", "午间打卡", {"opens": weekday_hours(("12:00", "13:00"))})


def _clock(*closed_days: int) -> dict:
    """打卡行：每周时段与地点同 CLOCK，再把 10 月给定各日整日关掉。"""
    exceptions = {f"2026-10-{day:02d}": [] for day in closed_days}
    return app_content("dingtalk-clock", "考勤打卡", {"location_kinds": ["office"], "opens": {
        "weekly": {day: CLOCK_HOURS for day in WORKDAYS}, "exceptions": exceptions}})


def _staged(engine_factory, tmp_path, when, location, *contents, **window):
    """钉钉例子的真实引擎（运行从 10 月 when[0] 日 08:00 开始），摆到给定本地时刻与停留地点。"""
    engine = dingtalk_engine(engine_factory, tmp_path, *contents, start_day=when[0], **window)
    stage_moment(engine, moment(*when), location)
    return engine


def _dingtalk(observed: dict) -> dict:
    """工具层的钉钉目标。"""
    return next(row for row in observed["tool_layer"]["targets"] if row["name"] == "钉钉")


def _refused_with(engine, reason: str, next_step: str, state: str | None) -> None:
    """此刻打开钉钉：工具层钉钉一行的原因、提交后拒绝的原因与下一步、钉钉目标的 state 如给定；拒绝不改世界。"""
    before = engine.context.state.model_copy(deep=True)
    observed, result = act(engine, "clock", use_app("钉钉"))
    assert dingtalk_row(observed)["reason"] == reason and dingtalk_row(observed)["available"] is False
    assert _dingtalk(observed)["state"] == state
    assert result.status == "rejected" and result.error_code == "CONTENT_PREREQUISITE_UNMET"
    assert (result.message, result.data["reason"], result.data["next_step"]) == (reason, reason, next_step)
    after = engine.context.state
    assert after.activities == before.activities == {} and after.sessions == before.sessions
    assert after.agents[1].content_progress == before.agents[1].content_progress
    assert after.random_state == before.random_state and after.current_time == before.current_time
    assert not events(engine, "action_accepted")


#: 规格算例：CLOCK_REFUSALS 六个时刻各自的钉钉目标 state，再加周一 08:55 在办公室（可用，state 为 null）。
_CLOCK_STATES = [
    *[(when, location, state) for (when, location, *_), state in
      zip(CLOCK_REFUSALS, [TODAY, TODAY, TODAY, TODAY, NEXT_MONDAY, NEXT_MONDAY], strict=True)],
    ((12, 8, 55), "office", None),
]


@pytest.mark.parametrize("when,location,state", _CLOCK_STATES,
                         ids=["home-0810", "office-1000", "office-1230", "home-1230", "saturday-office",
                              "saturday-home", "office-0855"])
def test_clock_in_app_state_names_todays_hours_or_the_next_opening(engine_factory, tmp_path, when, location, state):
    """只有打卡的钉钉：内容此刻不合格时目标 state 写打卡的开放时间（今天有时段写今天的，周六写下周一的），只差地点时
    也写；可以打卡时为 null。别的内容 app 的 state 都是 null；工具层通过层校验，紧凑渲染能还原。"""
    engine = _staged(engine_factory, tmp_path, when, location)
    observed = observe_only(engine, "look")
    assert _dingtalk(observed)["state"] == state
    assert dingtalk_row(observed)["available"] is (state is None)
    others = {row["name"]: row["state"] for row in observed["tool_layer"]["targets"]
              if row["kind"] == "app" and row["name"] != "钉钉"}
    assert others == {"哔哩哔哩": None, "抖音": None, "Keep": None}
    layer = observed["tool_layer"]
    validate_layer(layer)
    compact = compact_tool_layer(layer)
    validate_layer(compact)
    assert expand_tool_layer(compact)["targets"] == layer["targets"]


@pytest.mark.parametrize("contents,when,location,reason,next_step,state", [
    ((_clock(12, 13, 14, 15, 16),), (12, 12, 30), "office", f"Nothing in 钉钉 can start now: it is {NEXT_MONDAY}.",
     "Choose another action.", NEXT_MONDAY),
    ((_clock(19, 20, 21, 22, 23),), (17, 9, 0), "office", f"Nothing in 钉钉 can start now: it is {NEVER}.",
     "Choose another action.", NEVER),
    ((_clock(19, 20, 21, 22, 23),), (17, 9, 0), "home", f"{AT_OFFICE} and is {NEVER}.", GO_TO_OFFICE, NEVER),
], ids=["seventh-day", "none-office", "none-home"])
def test_row_and_refusal_name_an_opening_on_the_seventh_day_or_none_within_seven_days(
        engine_factory, tmp_path, contents, when, location, reason, next_step, state):
    """12 日至 16 日关掉的打卡在 12 日写下次开放在 19 日（恰是第七天）；19 日至 23 日关掉的打卡在 17 日（周六）写七天里
    都不开放，在家时分句另写要去的地点。行、拒绝与目标 state 都如算例。"""
    engine = _staged(engine_factory, tmp_path, when, location, *contents)
    _refused_with(engine, reason, next_step, state)


@pytest.mark.parametrize("when,location,reason,next_step,state", [
    ((12, 8, 10), "home", f"{AT_OFFICE}; or it is available 12:00–13:00 today.", GO_TO_OFFICE,
     f"{TODAY}; or available 12:00–13:00 today"),
    ((17, 9, 0), "office", f"Nothing in 钉钉 can start now: it is {NEXT_MONDAY}; or it is {NEXT_MONDAY_NOON}.",
     "Choose another action.", f"{NEXT_MONDAY}; or {NEXT_MONDAY_NOON}"),
    ((17, 9, 0), "home", f"{AT_OFFICE} and is {NEXT_MONDAY}; or it is {NEXT_MONDAY_NOON}.", GO_TO_OFFICE,
     f"{NEXT_MONDAY}; or {NEXT_MONDAY_NOON}"),
], ids=["monday-home-0810", "saturday-office", "saturday-home"])
def test_clock_and_lunch_rows_join_their_openings_with_or(
        engine_factory, tmp_path, when, location, reason, next_step, state):
    """打卡加午间打卡：两条内容各写一个分句、各写一个时间短语，以「; or 」相连；周六两句写的下次开放不同，去重后都留下；
    下一步照旧指向办公室。"""
    engine = _staged(engine_factory, tmp_path, when, location, CLOCK, LUNCH)
    _refused_with(engine, reason, next_step, state)


def test_state_stays_empty_when_no_content_declares_opening_hours(engine_factory, tmp_path):
    """钉钉只有一条只限办公室、不限时段的内容：周一 12:30 人在家，原因叫她去办公室，目标 state 为 null。"""
    badge = app_content("dingtalk-badge", "门禁登记", {"location_kinds": ["office"]})
    engine = _staged(engine_factory, tmp_path, (12, 12, 30), "home", badge)
    _refused_with(engine, f"{AT_OFFICE}.", GO_TO_OFFICE, None)


def test_state_names_only_contents_that_declare_opening_hours(engine_factory, tmp_path):
    """打卡加只限办公室、不限时段的门禁登记，周一 12:30 人在家：原因两句（门禁登记按内容身份在前），目标 state 只写
    打卡今天的时段。"""
    badge = app_content("dingtalk-badge", "门禁登记", {"location_kinds": ["office"]})
    engine = _staged(engine_factory, tmp_path, (12, 12, 30), "home", CLOCK, badge)
    _refused_with(engine, f"{AT_OFFICE}; or it requires presence at {OFFICE} during {HOURS} today.",
                  GO_TO_OFFICE, TODAY)


def test_opening_state_stays_visible_behind_slot_and_conclusion_messages(engine_factory, tmp_path):
    """周六 09:00 在办公室，重估间隔 300 秒，09:00 打开 Keep（「测试慢跑」1200 秒）：钉钉一行的原因依次是前提原因、槽位 2
    被占、先收尾，准入次序不变；目标 state 三次都是下周一的开放时间。"""
    engine = _staged(engine_factory, tmp_path, (17, 9, 0), "office", reconsideration_interval_seconds=300)
    keep = "进行Keep（10月17日 09:00 起）"
    observed, opened = act(engine, "keep", use_app("Keep"))
    assert opened.status == "in_progress", opened.error_code
    assert engine.context.state.activities[opened.activity_id].name == keep
    rows = [observed]
    for offset in (300, 1200):
        reach(engine, moment(17, 9) + offset)
        assert engine.context.state.current_time == moment(17, 9) + offset
        rows.append(observe_only(engine, "behind"))
    assert engine.context.state.activities[opened.activity_id].status == "completed"
    assert [dingtalk_row(row)["reason"] for row in rows] == [
        f"Nothing in 钉钉 can start now: it is {NEXT_MONDAY}.",
        f"The second activity slot is occupied by {keep}.",
        f"Conclude the finished activities {keep} first."]
    assert [_dingtalk(row)["state"] for row in rows] == [NEXT_MONDAY] * 3
