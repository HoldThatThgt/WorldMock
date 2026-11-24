"""引擎世界等于已提交世界：暂存时原地改动共享条目、或引擎世界在调用之外被改过时，每次提交都在写入之前被拒绝。

规则见 docs/design/04-simulation-engine.md「原子性与恢复」；守卫夹具 committed_world_guard 包住引擎的每个公开方法。
"""

from __future__ import annotations

import pytest

from tests.committed_world_guard import public_methods
from tests.life.conftest import action_identity, advance_next, search_world, travel
from tests.life.environment_runtime import act, events, settle_decision
from worldmock.foundation.errors import IntegrityError
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.simulation_engine.scheduling.clock import time_advance
from worldmock.state.decisions.barrier import SearchSessionOutcome

DIFFERS = "^Engine world differs from the committed world$"


def _walking(engine_factory):
    """走向公司，再像 finish_activity 那样处理掉到期的决策，直到下一步只能推进时钟；返回引擎与出行的受理结果。"""
    engine = engine_factory()
    _, started = act(engine, "walk", travel("公司"))
    settled = 0
    while engine.scheduling_state().ready_agent_ids:
        settled += 1
        assert settled < 20
        settle_decision(engine, f"settle-{settled}")
    return engine, started


def _committed(engine) -> tuple:
    """库里的末事件序号与已提交世界。"""
    return engine.context.store.last_event_seq, engine.context.store.state


def _diverge(engine) -> None:
    """在任何引擎调用之外改动引擎世界的一个非条目字段。"""
    engine.context.state.weather_by_date["2000-01-01"] = {"test-city": "sunny"}


def test_in_place_edit_of_a_shared_entry_is_refused_before_the_step_commits(
        engine_factory, monkeypatch, committed_world_guard):
    """推进时原地改动候选世界里与当前世界共享的活动条目：这一步在写入之前被拒绝，库不变，守卫记下这次泄漏。"""
    engine, started = _walking(engine_factory)
    original = time_advance.apply_engine_rhythm

    def leaking(context, staged, boundary):
        context.state.activities[started.activity_id].data["leaked"] = True
        return original(context, staged, boundary)

    monkeypatch.setattr(time_advance, "apply_engine_rhythm", leaking)
    before = _committed(engine)
    with pytest.raises(IntegrityError, match=DIFFERS):
        advance_next(engine)
    assert _committed(engine) == before
    assert committed_world_guard.leaks == ["LifeEngine.advance changed the world it started from"]
    committed_world_guard.leaks.clear()


def test_run_failure_is_recorded_even_when_the_engine_world_differs(engine_factory):
    """运行失败事件不比较世界：引擎世界不同时照样写下唯一一条 run_failed。"""
    engine = engine_factory()
    _diverge(engine)
    before = engine.context.store.last_event_seq
    engine.fail_run("probe failure")
    assert engine.context.store.last_event_seq == before + 1
    assert len(events(engine, "run_failed")) == 1


@pytest.mark.parametrize("entry", ["advance", "commit_barrier", "complete_searches"])
def test_every_staging_entry_refuses_a_diverged_engine_world(engine_factory, committed_world_guard, entry):
    """时钟推进、屏障提交与搜索交卷都在 commit_step 之前比较引擎世界：不同即拒绝，库保持调用之前的样子。"""
    if entry == "advance":
        engine, _ = _walking(engine_factory)
        call = lambda: advance_next(engine)
    elif entry == "commit_barrier":
        engine = engine_factory()
        [wakeup] = engine.prepare_barrier((1,))
        sandbox = engine.open_sandbox(wakeup)
        sandbox.dispatch_world_tool("start_main_activity", travel("公司"), action_identity("probe"))
        outcome = sandbox.finish_wakeup(None)
        call = lambda: engine.commit_barrier([outcome])
    else:
        engine = engine_factory()
        _, started = act(engine, "probe-s", search_world("测试搜索"))
        advance_next(engine)
        [(task, _)] = engine.due_searches()
        res = SearchSessionOutcome(
            action_id=task.action_id, agent_id=1, activity_id=started.activity_id,
            goods_ids=(), place_ids=(), summary="完成",
        )
        call = lambda: engine.complete_searches([res])
    _diverge(engine)
    before = _committed(engine)
    with pytest.raises(IntegrityError, match=DIFFERS):
        call()
    assert _committed(engine) == before
    assert committed_world_guard.leaks == []


def test_guard_wraps_every_public_engine_method(committed_world_guard):
    """守卫包住引擎当前的全部公开方法，以后新增的公开方法也自动包住。"""
    expected = {
        "scheduling_state", "advance", "prepare_barrier", "open_sandbox", "commit_barrier",
        "due_searches", "complete_searches", "record_failed_evidence", "decide_midnight_rhythm",
        "register_day_plan", "register_memory_digest", "read_task_summary", "search_memory",
        "apply_counterfactual", "meal_windows_by_agent", "fail_run", "checkpoint", "close",
    }
    assert expected <= set(public_methods(LifeEngine))
    assert all(hasattr(getattr(LifeEngine, name), "__wrapped__") for name in expected)
