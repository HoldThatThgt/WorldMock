"""世界状态的 processed_actions 只留规则还会读取的条目：本秒结算的结果与进行中或暂停活动的开始动作；时钟每推进一次
删去其余条目，活动终结时核对开始动作仍在（docs/design/04-simulation-engine.md「原子性与恢复」）。全部经真实请求、
引擎与 SQLite。"""

from __future__ import annotations

import logging

import pytest

from tests.integration.runtime_fixtures import branch_of
from tests.life.conftest import (
    advance_next, advance_until_ready, conclude_request, do_at, pause_request, purchase, travel, use_app,
)
from tests.life.day_baseline_runtime import create_complete_day_engine, play_complete_day
from tests.life.environment_runtime import (
    act, check_every_step, events, every_five_minutes, finish_activity, ledger_holds_only_readable_entries, one_hour,
    run_to_end, settle_decision,
)
from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.same_moment import latest_rejection_code, refusal_pulse_applied, rejection_counts

#: 活动终结时开始动作缺失的协议故障原文。
_MISSING = "^Activity origin action is missing from processed actions$"
#: 动作的终态结果状态（截断记为 cancelled）。
_TERMINAL = {"completed", "cancelled", "failed"}


def _check_ledger(engine, origins) -> None:
    """每一步之后核对 processed_actions 只含规则还会读取的条目。"""
    ledger_holds_only_readable_entries(engine.context.state, origins)


def _remove_origin(engine, activity_id: str) -> str:
    """像 stage_day_plan 那样提交一份删去这项活动开始动作的世界（版本加一，带一条系统可见的测试事件），返回开始动作的
    身份。"""
    staged = engine.context.state.model_copy(deep=True)
    origin = staged.activities[activity_id].origin_action_id
    del staged.processed_actions[origin]
    staged.version += 1
    engine.context.store.commit(engine.context.state.model_dump(mode="json"), staged.model_dump(mode="json"), [
        EventDraft(event_type="environment_event", occurred_at=staged.current_time, visibility="system",
                   data={"kind": "test_origin_removed"})])
    engine.context.state = staged
    return origin


def _stored_activity(engine, activity_id: str) -> dict:
    """已提交世界里这项活动的 JSON（活动数组里按身份找）。"""
    return next(row for row in engine.context.store.state["activities"] if row["activity_id"] == activity_id)


def test_processed_actions_keep_only_this_second_and_unfinished_origins(tmp_path, monkeypatch):
    """活动完整日的每一步之后：每个条目要么是本秒结算的结果，要么是最近一次推进前未终结活动的开始动作；那些开始动作
    都在，此刻进行中或暂停的活动的开始动作也都在。最后一次推进之后只剩那次推进前未终结活动的开始动作，那些活动都在
    实验终点结束。"""
    engine = create_complete_day_engine(tmp_path / "case", "activity")
    seen = []

    def check(engine, origins) -> None:
        """核对并记下这一步的 origins。"""
        _check_ledger(engine, origins)
        seen.append(origins)

    check_every_step(monkeypatch, engine, check)
    try:
        play_complete_day(engine, "activity")
        state = engine.context.state
        assert state.current_time == state.end_time and len(seen) > 100
        assert seen[-1] and set(state.processed_actions) == seen[-1]
        ended = [activity for activity in state.activities.values() if activity.origin_action_id in seen[-1]]
        assert len(ended) == len(seen[-1]) and all(activity.ended_at == state.end_time for activity in ended)
    finally:
        engine.close()


def test_a_paused_activity_keeps_its_origin_until_it_ends(engine_factory, monkeypatch):
    """暂停的活动不在进行中，开始它的动作仍留着：之后每次推进都不删去它；运行到终点时这项暂停的活动被截断，截断时核对的
    开始动作仍在。"""
    engine = engine_factory(mutate=every_five_minutes)
    check_every_step(monkeypatch, engine, _check_ledger)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed", paused.error_code
    _, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    assert chat.status == "in_progress", chat.error_code
    origin = engine.context.state.activities[rest.activity_id].origin_action_id
    advances = 0
    while advances < 2:
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"settle-{engine.context.state.current_time}")
            continue
        advance_next(engine)
        advances += 1
        assert engine.context.state.activities[rest.activity_id].status == "paused"
        assert origin in engine.context.state.processed_actions
    run_to_end(engine)
    ended = engine.context.state.activities[rest.activity_id]
    assert ended.status == "truncated" and ended.ended_at == engine.context.state.end_time
    assert origin in engine.context.state.processed_actions


@pytest.mark.parametrize("how", ["natural", "control", "truncated"])
def test_an_activity_whose_origin_action_is_missing_cannot_end(engine_factory, caplog, how):
    """开始动作不在 processed_actions 里的活动不能终结：自然结束、被 conclude_activity 结束和在实验终点截断都以协议故障
    停止并写英文错误日志，这一步不提交——已提交世界里活动仍在进行，开始动作没有终态结果事件。"""
    if how == "truncated":
        engine = engine_factory(mutate=one_hour)
        _, trip = act(engine, "commute", travel("公司", "驾车"))
        assert trip.status == "in_progress", trip.error_code
        finish_activity(engine, trip, "commute")
        _, started = act(engine, "work", do_at("公司", "季度销售数据整理"))
    else:
        engine = engine_factory(mutate=every_five_minutes if how == "control" else None)
        _, started = act(engine, "rest", do_at("家", "测试休息"))
    assert started.status == "in_progress", started.error_code
    if how == "control":
        assert advance_until_ready(engine)
    origin = _remove_origin(engine, started.activity_id)
    caplog.set_level(logging.ERROR)
    with pytest.raises(ProtocolError, match=_MISSING):
        if how == "natural":
            finish_activity(engine, started, "rest")
        elif how == "control":
            name = engine.context.state.activities[started.activity_id].name
            act(engine, "stop", conclude_request(name), "conclude_activity")
        else:
            run_to_end(engine)
    assert "origin action" in caplog.text
    assert _stored_activity(engine, started.activity_id)["status"] == "active"
    assert not [row for row in events(engine, "action_result")
                if row.action_id == origin and row.data["status"] in _TERMINAL]


def test_an_activity_that_spans_a_branch_ends_in_the_branch(engine_factory, tmp_path):
    """进行中的活动跨过共同检查点：分支里它照常结束，开始它的动作在分支点之后恰有一条终态结果事件；分支的下一次推进
    删去这条开始动作。"""
    parent = engine_factory()
    _, rest = act(parent, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    origin = parent.context.state.activities[rest.activity_id].origin_action_id
    child = branch_of(parent, tmp_path, "resume", "-")
    try:
        branched_at = child.context.store.last_event_seq
        assert origin in child.context.state.processed_actions
        finish_activity(child, rest, "rest")
        terminal = [row for row in events(child, "action_result") if row.event_seq > branched_at
                    and row.action_id == origin and row.data["status"] in _TERMINAL]
        assert len(terminal) == 1
        while child.scheduling_state().ready_agent_ids:
            settle_decision(child, f"after-{child.context.state.current_time}")
        advance_next(child)
        assert origin not in child.context.state.processed_actions
    finally:
        child.close()


def _same_moment_view(engine) -> tuple:
    """本秒的同刻轮数、最近一次计入的拒绝码、两种拒绝码是否计入与 service_refused 脉冲是否已施加。"""
    state = engine.context.state
    return (state.agents[1].same_moment_rounds, latest_rejection_code(state, 1),
            rejection_counts(state, 1, "SUPPLY_NOT_FOUND"), rejection_counts(state, 1, "ACTION_REQUEST_INVALID"),
            refusal_pulse_applied(state, 1))


def test_a_branch_at_a_second_with_rejections_keeps_the_same_moment_budget_and_pulse(engine_factory, tmp_path):
    """同一秒两次免计的名称类拒绝之后又有一次计入的拒绝，在这一秒开出分支：分支与父运行本秒的同刻轮数、最近一次计入的
    拒绝码、下一次拒绝是否计入与脉冲是否已施加都相同；两边再被拒一次，结果、同刻轮数与放松需要也相同。"""
    parent = engine_factory()
    for label in ("reject-1", "reject-2"):
        _, refused = act(parent, label, purchase("不存在的商品"))
        assert (refused.status, refused.error_code) == ("rejected", "SUPPLY_NOT_FOUND")
    _, counted = act(parent, "reject-3", use_app("不存在"))
    assert (counted.status, counted.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    child = branch_of(parent, tmp_path, "resume", "-")
    try:
        assert child.context.state.current_time == parent.context.state.current_time
        assert _same_moment_view(child) == _same_moment_view(parent)
        outcomes = []
        for engine in (parent, child):
            _, again = act(engine, "reject-4", use_app("不存在"))
            outcomes.append((again.status, again.error_code, again.data["same_moment_round"],
                             engine.context.state.agents[1].body.relaxation_need))
        assert outcomes[0] == outcomes[1] and outcomes[0][0] == "rejected"
    finally:
        child.close()
