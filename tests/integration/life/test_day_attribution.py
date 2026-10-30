"""归日经真实请求、引擎与存储：恰在零点结束的睡眠归新的一天、跨夜睡眠的结束归醒来那天、一次推进的提交不跨本地零点，
「近三天」与「可查」在同一天里逐字节不变。"""

from __future__ import annotations

from datetime import datetime

from tests.life.conftest import (
    StubMemoryDigestClient,
    StubPlanDayClient,
    advance_next,
    advance_until_ready,
    conclude_request,
    do_at,
    drive,
    sleep,
    stage_day_plan,
)
from tests.life.environment_runtime import act, events, observe_only
from worldmock.agents.prompt.narration import render_layer_json
from worldmock.foundation.time.readable import local_date
from worldmock.runtime.lifecycle.runner import settle_pending_day_plans, settle_pending_memory_digests
from worldmock.simulation_engine.observation.memory.days import day_bound, day_window
from worldmock.simulation_engine.scheduling.plans import day_plan_inputs
from worldmock.simulation_engine.scheduling.plans.day_plan_inputs import plan_inputs

ZONE = "Asia/Shanghai"


def _evening(persona, config):
    """运行从 2026-01-05（星期一）20:00 开始，两天后截止。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T20:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-07T20:00:00+08:00")


def _sleep_through(engine, activity_id: str) -> None:
    """逐边界推进到睡眠的计划结束；零点边界照 LifeEnv.step 先生成当日计划。"""
    planned_end = engine.context.state.activities[activity_id].planned_end_at
    while engine.context.state.current_time < planned_end:
        advance_next(engine)


def _overnight(engine_factory):
    """20:00 先睡到 23:30 并收尾，23:30 再睡 480 分钟到次日 07:30；返回引擎与那一夜睡眠的活动身份。"""
    engine = engine_factory(mutate=_evening)
    stage_day_plan(engine, submitted=True)
    _, nap = act(engine, "evening", sleep(210))
    _sleep_through(engine, nap.activity_id)
    _, concluded = act(engine, "conclude", conclude_request(engine.context.state.activities[nap.activity_id].name),
                       "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    _, night = act(engine, "night", sleep(480))
    assert night.status == "in_progress", night.error_code
    assert engine.context.state.current_time == int(datetime.fromisoformat("2026-01-05T23:30:00+08:00").timestamp())
    _sleep_through(engine, night.activity_id)
    return engine, night.activity_id


def test_a_sleep_ending_exactly_at_midnight_is_left_out_of_that_midnights_plan(engine_factory):
    """20:00 睡 240 分钟到 01-06 00:00：这一刻属于新的一天，01-06 零点的计划里「动作结果」与经历都没有这段睡眠的结束句，
    结束句归 01-06 那一天的窗口。"""
    engine = engine_factory(mutate=_evening)
    stage_day_plan(engine, submitted=True)
    _, result = act(engine, "evening", sleep(240))
    assert result.status == "in_progress", result.error_code
    _sleep_through(engine, result.activity_id)
    ended = engine.context.state.activities[result.activity_id].ended_at
    assert day_window("2026-01-06", ZONE).contains(ended) and not day_window("2026-01-05", ZONE).contains(ended)
    planned = plan_inputs(engine.context, engine.observations, True, 1)
    assert planned.payload["回顾时段"] == "1月5日 00:00 至 1月6日 00:00"
    assert not [text for text in planned.payload["动作结果"] if "睡觉" in text]
    assert not [text for text, _ in planned.experiences if "睡觉" in text]
    history = day_plan_inputs._agent_history(engine.context.store, 1)
    wake_day, _ = day_plan_inputs._result_sentences(engine.context, engine.observations, 1, history,
                                                    day_window("2026-01-06", ZONE))
    assert [text for text in wake_day if "睡觉" in text] == ["睡觉（1月5日 20:00 起）：1月5日 20:00–00:00，按计划结束"]


def test_an_overnight_sleep_ends_on_the_wake_day(engine_factory):
    """23:30 开始、07:30 结束的睡眠：开始记在前一天的序号区间里，结束事件归醒来那天，不在前一天的序号区间里。"""
    engine, night = _overnight(engine_factory)
    store, now = engine.context.store, engine.context.state.current_time
    first_day = (day_bound(store, "2026-01-05", ZONE, now), day_bound(store, "2026-01-06", ZONE, now))
    own = [event for event in events(engine) if event.activity_id == night]
    start = next(event for event in own if event.event_type == "action_result")
    assert first_day[0] < start.event_seq <= first_day[1] and local_date(start.occurred_at, ZONE) == "2026-01-05"
    ended_at = engine.context.state.activities[night].ended_at
    endings = [event for event in own if event.occurred_at == ended_at]
    assert {event.event_type for event in endings} == {"activity_progress", "action_result"}
    assert all(event.event_seq > first_day[1] and local_date(event.occurred_at, ZONE) == "2026-01-06"
               for event in endings)


def test_no_commit_spans_a_local_midnight(engine_factory):
    """跑过一个本地零点的运行里，每次推进版本的提交，其事件都落在同一个本地日。"""
    engine, _ = _overnight(engine_factory)
    groups: dict[tuple[int, int], set[str]] = {}
    for event in events(engine):
        if event.state_version_after > event.state_version_before:
            key = (event.state_version_before, event.state_version_after)
            groups.setdefault(key, set()).add(local_date(event.occurred_at, ZONE))
    assert set().union(*groups.values()) == {"2026-01-05", "2026-01-06"}
    assert all(len(days) == 1 for days in groups.values())



def test_recent_days_and_searchable_stay_byte_identical_through_a_day(engine_factory):
    """跨过零点后用回顾任务（确定性桩）生成前一天的日回顾：新的一天里三次观察的「近三天」渲染与「可查」逐字节相同，
    且「近三天」带前一天的格子。"""
    engine = engine_factory(mutate=_evening)
    stage_day_plan(engine, submitted=True)
    _, night = act(engine, "night", sleep(600))
    while not engine.midnight_rhythm_pending:
        state = engine.scheduling_state()
        engine.advance(int(state.next_event_at.timestamp()) - engine.context.state.current_time, state.next_event_at)
    model = {"requested_model": engine.context.config.model.requested_model}
    assert [item["period"] for item in drive(settle_pending_memory_digests(engine, StubMemoryDigestClient(model)))] == [
        "2026-01-05"]
    drive(settle_pending_day_plans(engine, StubPlanDayClient(model)))
    engine.decide_midnight_rhythm()
    layers = [observe_only(engine, "midnight")["memory_layer"]]
    assert advance_until_ready(engine)
    act(engine, "woke", conclude_request(engine.context.state.activities[night.activity_id].name), "conclude_activity")
    layers.append(observe_only(engine, "woke")["memory_layer"])
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    layers.append(observe_only(engine, "rested")["memory_layer"])
    assert len({render_layer_json(layer["近三天"]) for layer in layers}) == 1
    assert len({render_layer_json(layer["可查"]) for layer in layers}) == 1
    assert layers[0]["可查"] == {"最早日期": "2026-01-05"}
    assert layers[0]["近三天"]["生活"] == [{"日期": "2026-01-05", "星期": "星期一", "回顾": "这一天这一类的事照常。"}]
    assert len({len(layer["今天"]) for layer in layers}) == 3
