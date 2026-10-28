"""引擎世界等于已提交世界：暂存时原地改动共享条目、或引擎世界在调用之外被改过时，观察与每一步推进都在写入之前被拒绝。

规则见 docs/design/04-simulation-engine.md「原子性与恢复」；守卫夹具 committed_world_guard 包住引擎的每个公开方法。
"""

import pytest

import worldmock.simulation_engine.decisions.same_moment as same_moment
from agentsociety2.society import DirectToolCall
from tests.committed_world_guard import public_methods
from tests.life.conftest import advance_next, context, tool_of, travel
from tests.life.environment_runtime import act, events, settle_decision
from worldmock.foundation.errors import IntegrityError
from worldmock.simulation_engine.engine import LifeEngine

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
    engine.context.state.weather_by_date["2000-01-01"] = "晴"


def _submitted(engine_factory):
    """像 act 那样开轮、观察并登记一条去公司的出行（标签 probe），尚未结算。"""
    engine = engine_factory()
    decision = context(engine, "probe")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    request = travel("公司")
    engine.submit_action(DirectToolCall(decision, f"{decision.decision_id}:action:0001", tool_of(request), request))
    return engine


def test_in_place_edit_of_a_shared_entry_is_refused_before_the_step_commits(
        engine_factory, monkeypatch, committed_world_guard):
    """推进时原地改动候选世界里与当前世界共享的活动条目：这一步在写入之前被拒绝，库不变，守卫记下这次泄漏。"""
    engine, started = _walking(engine_factory)
    original = same_moment.reset_same_moment

    def leaking(state):
        state.activities[started.activity_id].data["leaked"] = True
        return original(state)

    monkeypatch.setattr(same_moment, "reset_same_moment", leaking)
    before = _committed(engine)
    with pytest.raises(IntegrityError, match=DIFFERS):
        advance_next(engine)
    assert _committed(engine) == before
    assert committed_world_guard.leaks == ["LifeEngine.advance changed the world it started from"]
    committed_world_guard.leaks.clear()


def test_observation_refuses_an_engine_world_that_differs_from_the_committed_world(
        engine_factory, committed_world_guard):
    """引擎世界与已提交世界不同时观察被拒：不写观察事件，也就不会有据此渲染的决策提示。"""
    engine = engine_factory()
    decision = context(engine, "probe")
    engine.begin_decision_round([decision])
    _diverge(engine)
    before = engine.context.store.last_event_seq
    with pytest.raises(IntegrityError, match=DIFFERS):
        engine.observe(decision)
    assert engine.context.store.last_event_seq == before
    assert committed_world_guard.leaks == []


def test_run_failure_is_recorded_even_when_the_engine_world_differs(engine_factory):
    """运行失败事件不比较世界：引擎世界不同时照样写下唯一一条 run_failed。"""
    engine = engine_factory()
    _diverge(engine)
    before = engine.context.store.last_event_seq
    engine.fail_run("probe failure")
    assert engine.context.store.last_event_seq == before + 1
    assert len(events(engine, "run_failed")) == 1


@pytest.mark.parametrize("entry", ["advance", "commit", "abort"])
def test_every_staging_entry_refuses_a_diverged_engine_world(engine_factory, committed_world_guard, entry):
    """时钟推进、决策轮结算与中止都在提交这一步之前比较引擎世界：不同即拒绝，库保持调用之前的样子。

    零点计划登记、零点节律判定与反事实干预经过同一个 commit_step。
    """
    if entry == "advance":
        engine, _ = _walking(engine_factory)
        call = lambda: advance_next(engine)
    else:
        engine = _submitted(engine_factory)
        call = (lambda: engine.commit_decision_round("probe")) if entry == "commit" else (
            lambda: engine.abort_decision_round("probe", "probe abort"))
    _diverge(engine)
    before = _committed(engine)
    with pytest.raises(IntegrityError, match=DIFFERS):
        call()
    assert _committed(engine) == before
    assert committed_world_guard.leaks == []


def test_guard_wraps_every_public_engine_method(committed_world_guard):
    """守卫包住引擎当前的全部公开方法，以后新增的公开方法也自动包住。"""
    expected = {"observe", "advance", "commit_decision_round", "abort_decision_round", "register_day_plan",
                "decide_midnight_rhythm", "apply_counterfactual", "checkpoint", "record_model_exchange",
                "record_memory_update", "submit_action", "begin_decision_round", "scheduling_state",
                "due_reflection", "read_task_summary", "fail_run", "meal_windows_by_agent", "close"}
    assert expected <= set(public_methods(LifeEngine))
    assert all(hasattr(getattr(LifeEngine, name), "__wrapped__") for name in expected)
