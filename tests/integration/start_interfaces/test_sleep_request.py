"""睡眠请求经真实请求、引擎与存储：写的分钟数、可睡的地点、关门不能开始睡、槽位规则与睡前提示。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.frozen_inputs import fixture_metadata
from tests.life.conftest import (
    MAIN_TOOL, advance_next, advance_until_ready, conclude_request, context, do_at, sleep, travel, use_app,
)
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, events, finish_activity
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import validate_catalog_files
from worldmock.environment import load_environment
from worldmock.life.felt import sleep_debt_phrase
from worldmock.life.models import StartMainActivityRequest
from worldmock.life.narration import render_layer_json
from worldmock.life.sleep_plan import SLEEP_MINUTES_REASON


def _edit_locations(root, updates: dict[str, dict], removed: dict[str, tuple[str, ...]] | None = None):
    """改写夹具副本里给定地点行的字段并刷新清单。"""
    rows = catalog_rows(root, "location.jsonl")
    for row in rows:
        row.update(updates.get(row["location_id"], {}))
        for key in (removed or {}).get(row["location_id"], ()):
            row.pop(key, None)
    write_catalog_rows(root, "location.jsonl", rows)
    return root


def _sleep_row(observed: dict, name: str) -> dict | None:
    """工具层某个地点下的 sleep 动作行，没有时为 None。"""
    target = next(row for row in observed["tool_layer"]["targets"] if row["name"] == name)
    return next((row for row in target["actions"] if row["action"] == "sleep"), None)


def _local(engine) -> datetime:
    """当前模拟时刻在运行时区的本地时刻。"""
    return datetime.fromtimestamp(engine.context.state.current_time, ZoneInfo(engine.context.config.timezone))


def _go(engine, place: str) -> None:
    """驾车到给定地点并收尾。"""
    _, trip = act(engine, f"go-{engine.context.state.current_time}", travel(place, "驾车"))
    assert trip.status == "in_progress", trip.error_code
    finish_activity(engine, trip, f"arrive-{engine.context.state.current_time}")


def test_persona_home_must_be_marked_sleepable(tmp_path):
    """家没标 sleepable 时环境加载失败并给出规定的码、原因与详情；只做目录登记校验时不查。"""
    root = _edit_locations(copy_environment(tmp_path / "plain-home"), {}, {"home": ("sleepable",)})
    with pytest.raises(RegistrationError) as failure:
        load_environment(root, metadata_loader=fixture_metadata)
    assert failure.value.code == "HOME_NOT_SLEEPABLE" and failure.value.detail == "persona.home_location"
    assert failure.value.message == "The persona home must be marked sleepable."
    assert str(failure.value) == "HOME_NOT_SLEEPABLE: The persona home must be marked sleepable. persona.home_location"
    validate_catalog_files(root / "catalog")
    explicit = _edit_locations(copy_environment(tmp_path / "false-home"), {"home": {"sleepable": False}})
    with pytest.raises(RegistrationError, match="HOME_NOT_SLEEPABLE"):
        load_environment(explicit, metadata_loader=fixture_metadata)


def test_other_home_locations_need_not_be_sleepable(tmp_path):
    """只查人物自己的家：目录里别的 is_home 地点没标 sleepable 不报，sleepable 与 is_home 互不推导。"""
    root = _edit_locations(copy_environment(tmp_path / "second-home"), {"park": {"is_home": True}},
                           {"office": ("sleepable",)})
    inputs = load_environment(root, metadata_loader=fixture_metadata)
    locations = inputs.catalog.locations
    assert locations["park"].is_home and not locations["park"].sleepable
    assert locations["home"].sleepable and not locations["office"].sleepable
    assert inputs.home_location_id == "home"


def test_sleeping_sixty_minutes_at_the_sleepable_office(engine_factory):
    """在标了 sleepable 的办公室睡 60 分钟：活动记在办公室，到点才醒，其间不提供决策。"""
    engine = engine_factory()
    _go(engine, "公司")
    observed, result = act(engine, "nap", sleep(60))
    assert result.status == "in_progress", result.error_code
    row = _sleep_row(observed, "公司")
    assert row["available"] is True and row["detail"] is None and row["duration"] is None
    started = engine.context.state.current_time
    activity = engine.context.state.activities[result.activity_id]
    assert activity.kind == "sleep" and activity.location_id == "office" and activity.resource_claims == {"first"}
    assert activity.planned_duration_seconds == 3600 and activity.planned_end_at == started + 3600
    assert activity.data["action"] == "sleep" and activity.data["declared_duration_seconds"] == 3600
    assert activity.data["oversleep_seconds"] == 0
    assert activity.name == f"睡觉（1月5日 {_local(engine):%H:%M} 起）"
    while engine.context.state.current_time < started + 3600:
        assert engine.scheduling_state().ready_agent_ids == ()
        advance_next(engine)
    assert engine.context.state.current_time == started + 3600
    woke = engine.context.state.activities[result.activity_id]
    assert woke.status == "completed" and woke.ended_at == started + 3600 and woke.elapsed_seconds == 3600
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert "planned_end" in engine.context.state.agents[1].pending_wake_reasons


@pytest.mark.parametrize("minutes,seconds", [("10", 600), ("720", 43200), ("０６０", 3600)])
def test_minutes_from_ten_to_seven_hundred_twenty_are_accepted_as_written(engine_factory, minutes, seconds):
    """时长范围的两端都能取到；全角与前导零归一后受理，并在受理结果里记下所写与解析出的数。"""
    engine = engine_factory()
    _, result = act(engine, "sleep", sleep(minutes))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert activity.planned_duration_seconds == activity.data["declared_duration_seconds"] == seconds
    assert activity.location_id == "home"
    expected = [] if minutes == str(seconds // 60) else [{"written": minutes, "as": str(seconds // 60)}]
    assert result.data.get("resolved", []) == expected


@pytest.mark.parametrize("target", ["9", "721", "0", "7.5", "470分钟", "8h"])
def test_minutes_outside_the_range_are_refused_without_clamping(engine_factory, target):
    """越界或带单位的写法：请求模型拒绝；绕过宿主时环境以 ACTION_REQUEST_INVALID 拒绝，不截到范围内。"""
    with pytest.raises(ValueError, match="whole number of minutes from 10 to 720"):
        sleep(target)
    engine = engine_factory()
    bad = StartMainActivityRequest.model_construct(action="sleep", target=target, detail=None)
    _, result = act(engine, "bad", bad, MAIN_TOOL)
    assert result.status == "rejected" and result.error_code == "ACTION_REQUEST_INVALID"
    assert not engine.context.state.activities
    assert result.data["items"] == [{
        "path": ["tool_calls", 0, "function", "arguments", "target"], "written": target,
        "error_code": "ACTION_REQUEST_INVALID", "allowed_values": [], "why": SLEEP_MINUTES_REASON,
        "expected": result.data["items"][0]["expected"], "candidates": []}]
    assert result.data["reason"] == SLEEP_MINUTES_REASON and result.data["fix"] == "Correct every reported parameter."
    assert result.data["unmet"] == []


def test_invalid_minutes_and_non_null_detail_are_both_reported(engine_factory):
    """detail 非空与 target 非法同时出现时两条都报，原因以分号相连。"""
    engine = engine_factory()
    bad = StartMainActivityRequest.model_construct(action="sleep", target="八小时", detail="家")
    _, result = act(engine, "bad", bad, MAIN_TOOL)
    assert result.error_code == "ACTION_REQUEST_INVALID"
    assert [(item["path"][-1], item["why"]) for item in result.data["items"]] == [
        ("target", SLEEP_MINUTES_REASON), ("detail", "detail must be null when action is sleep")]
    assert result.data["reason"] == f"{SLEEP_MINUTES_REASON}; detail must be null when action is sleep"


def test_sleeping_where_it_is_not_possible_points_home(engine_factory):
    """在没标 sleepable 的地点被拒绝，原因和下一步逐字相符；那里的工具层没有 sleep 行。"""
    engine = engine_factory()
    _go(engine, "五里公园")
    rounds = engine.context.state.agents[1].same_moment_rounds
    observed, result = act(engine, "nap", sleep(60))
    assert _sleep_row(observed, "五里公园") is None and _sleep_row(observed, "家") is None
    assert result.status == "rejected" and result.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert result.message == "Sleeping is not possible at 五里公园."
    assert result.data["next_step"] == "Go to 家." and result.data["fix"] is None
    assert result.data["unmet"] == ["sleepable_location"]
    assert result.data["items"] == [{
        "path": ["tool_calls", 0, "function", "arguments", "target"], "written": "60",
        "error_code": "ACTIVITY_PRECONDITION_UNMET", "allowed_values": [],
        "expected": "Sleeping is not possible at 五里公园.", "why": "Sleeping is not possible at 五里公园.",
        "candidates": []}]
    assert not [item for item in engine.context.state.activities.values() if item.kind == "sleep"]
    assert engine.context.state.agents[1].same_moment_rounds == rounds + 1


def test_sleep_on_the_road_is_refused_by_the_occupied_slot(engine_factory):
    """人在路上时槽位 1 被出行占着：按同槽位占用拒绝，不是名称解析的错误。"""
    def attentive(persona, config):
        config["reconsideration_interval_seconds"] = 300

    engine = engine_factory(mutate=attentive)
    _, trip = act(engine, "go", travel("公司", "步行"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[trip.activity_id].name
    _, result = act(engine, "nap", sleep(30))
    assert result.status == "rejected" and result.error_code == "RESOURCE_CONFLICT"
    assert result.message == f"The first activity slot is occupied by {name}."
    assert result.data["next_step"] == f"Conclude {name} first." and result.data["unmet"] == ["resource_claims"]


def test_closed_location_cannot_start_sleep_but_does_not_wake_a_sleeper(engine_factory, tmp_path):
    """sleepable 但关门时不能开始睡，工具层同样标注且提示照常显示；睡着后地点到了关门时刻照常睡到计划结束。"""
    hours = {"weekly": {"mon": [["08:20", "09:00"]]}}
    root = _edit_locations(copy_environment(tmp_path / "hours"), {"office": {"opens": hours}})
    engine = engine_factory(environment_root=root)
    _go(engine, "公司")
    assert _local(engine).strftime("%H:%M") < "08:20"
    observed, closed = act(engine, "early", sleep(60))
    assert closed.status == "rejected" and closed.error_code == "LOCATION_CLOSED"
    assert closed.message == "The location is closed at the current time."
    assert closed.data["fix"] is None and closed.data["next_step"] is None and closed.data["unmet"] == []
    row = _sleep_row(observed, "公司")
    assert (row["available"], row["reason"]) == (False, "The location is closed at the current time.")
    assert row["note"] == sleep_debt_phrase(engine.context.state.agents[1].body.sleep_deficit_hours)
    counter = 0
    while _local(engine).strftime("%H:%M") < "08:20":
        counter += 1
        _, video = act(engine, f"wait-{counter}", use_app("哔哩哔哩"))
        assert video.status == "in_progress", video.error_code
        finish_activity(engine, video, f"wait-{counter}")
    assert _local(engine).strftime("%H:%M") < "09:00"
    observed, result = act(engine, "open", sleep(120))
    assert _sleep_row(observed, "公司")["available"] is True
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    closing = int(_local(engine).replace(hour=9, minute=0, second=0).timestamp())
    assert activity.planned_end_at > closing
    while engine.context.state.current_time < activity.planned_end_at:
        assert engine.scheduling_state().ready_agent_ids == ()
        advance_next(engine)
    woke = engine.context.state.activities[result.activity_id]
    assert woke.status == "completed" and woke.ended_at == activity.planned_end_at and woke.elapsed_seconds == 7200


def test_pre_sleep_hint_follows_the_body_and_never_shows_raw_numbers(engine_factory):
    """sleep 行的 duration 为空，note 是按半小时取整的睡前提示；醒着越久提示越长；提示里没有被禁止的身体键。"""
    engine = engine_factory()
    first = engine.observe(context(engine, "morning"))
    row = _sleep_row(first, "家")
    assert row == {"action": "sleep", "detail": None, "available": True, "reason": None, "price": None,
                   "duration": None, "quantity": None, "note": "现在不欠觉"}
    _, work = act(engine, "chat", do_at("家", "测试闲聊"))
    finish_activity(engine, work, "chat")
    for index in range(6):
        _, rest = act(engine, f"rest-{index}", do_at("家", "测试休息"))
        assert rest.status == "in_progress", rest.error_code
        finish_activity(engine, rest, f"rest-{index}")
    later = engine.observe(context(engine, "later"))
    deficit = engine.context.state.agents[1].body.sleep_deficit_hours
    note = _sleep_row(later, "家")["note"]
    assert deficit > 0.25 and note == sleep_debt_phrase(deficit) and note != "现在不欠觉"
    rendered = render_layer_json(later["tool_layer"])
    assert "sleep_deficit" not in rendered and str(deficit) not in rendered and "sleep_pressure" not in rendered


def test_sleep_requires_slot_two_and_leaves_the_world_unchanged_when_refused(engine_factory):
    """槽位 2 有活动时睡眠被拒，世界与事件里没有任何睡眠活动；收尾后才能睡。"""
    engine = engine_factory()
    _, video = act(engine, "video", use_app("哔哩哔哩"))
    assert advance_until_ready(engine)
    before = {key: (item.status, item.planned_end_at) for key, item in engine.context.state.activities.items()}
    _, refused = act(engine, "sleepy", sleep(60))
    assert refused.status == "rejected" and refused.message == "Sleep requires the second activity slot to be empty."
    after = {key: (item.status, item.planned_end_at) for key, item in engine.context.state.activities.items()}
    assert after == before and all(item.kind != "sleep" for item in engine.context.state.activities.values())
    accepted = [row for row in events(engine, "action_accepted") if row.action_id == "action-sleepy"]
    assert accepted == []
    current = engine.context.state.activities[video.activity_id]
    if current.status == "active":
        act(engine, "stop", conclude_request(current.name), "conclude_activity")
    else:
        finish_activity(engine, video, "video")
    _, result = act(engine, "sleep", sleep(60))
    assert result.status == "in_progress", result.error_code
